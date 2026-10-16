"""Observe loop: poll each venue's iCal feed, parse lineups, detect changes, plan.

A new or changed event (fingerprint = sha1(summary|start|support)) starts a run
with kind="auto" and deliver=True. No human trigger is involved.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from icalendar import Calendar

from . import store
from .config import ROOT, settings

log = logging.getLogger(__name__)

SAMPLES = ROOT / "samples"
MAX_CONCURRENT_RUNS = 2

SPLIT_RE = re.compile(
    r"\s+(?:with|w/|feat\.|ft\.|featuring|supported by)\s+|\s+support:\s*|\s*\bw/\s*",
    re.IGNORECASE,
)
TOUR_RE = re.compile(r"\s+[—–]\s+.*$|\s+-\s+.*\b(tour|live|world|night|show|concert|edition)\b.*$", re.IGNORECASE)
PAREN_RE = re.compile(r"\s*\((?:[^)]*\b(?:live|tour|sold out|matinee|late show|early show|\d{4})\b[^)]*)\)",
                      re.IGNORECASE)
# Split support lists on commas, &, "and", "+", but never when the next word is
# "the" (Florence + the Machine, Tyler, The Creator, Echo & the Bunnymen).
SUPPORT_SPLIT_RE = re.compile(r"\s*(?:,|&|\+|\band\b)(?!\s*the\b)\s*", re.IGNORECASE)


def _strip_suffixes(text: str) -> str:
    text = PAREN_RE.sub("", text)
    text = TOUR_RE.sub("", text)
    return text.strip(" -–—:")


def split_support(text: str) -> list[str]:
    parts = [p.strip(" ,") for p in SUPPORT_SPLIT_RE.split(_strip_suffixes(text))]
    return [p for p in parts if p]


def parse_lineup(summary: str, description: str = "") -> tuple[str, list[str]]:
    """Return (headliner, support) from an event title, with DESCRIPTION overrides."""
    head_override, support_override = None, None
    for line in (description or "").splitlines():
        m = re.match(r"\s*headliner\s*:\s*(.+)$", line, re.IGNORECASE)
        if m:
            head_override = m.group(1).strip()
        m = re.match(r"\s*support\s*:\s*(.+)$", line, re.IGNORECASE)
        if m:
            support_override = split_support(m.group(1))

    title = _strip_suffixes((summary or "").strip())
    parts = SPLIT_RE.split(title, maxsplit=1)
    headliner = _strip_suffixes(parts[0])
    support = split_support(parts[1]) if len(parts) > 1 else []
    return (head_override or headliner), (support_override if support_override is not None else support)


def fingerprint(summary: str, start: str, support: list[str]) -> str:
    return hashlib.sha1(f"{summary}|{start}|{','.join(support)}".encode()).hexdigest()


def _iso(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, 20, tzinfo=timezone.utc).isoformat()
    return str(value)


def parse_events(ics_text: str, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    cal = Calendar.from_ical(ics_text)
    out = []
    for comp in cal.walk("VEVENT"):
        summary = str(comp.get("SUMMARY") or "").strip()
        dt = comp.get("DTSTART")
        if not summary or dt is None:
            continue
        start = _iso(dt.dt)
        if datetime.fromisoformat(start) < now:
            continue
        desc = str(comp.get("DESCRIPTION") or "")
        headliner, support = parse_lineup(summary, desc)
        if not headliner:
            continue
        uid = str(comp.get("UID") or hashlib.sha1(f"{summary}{start}".encode()).hexdigest())
        out.append({"uid": uid, "summary": summary, "start": start, "headliner": headliner, "support": support,
                    "description": desc, "fingerprint": fingerprint(summary, start, support)})
    return sorted(out, key=lambda e: e["start"])


def resolve_feed_path(url: str) -> Path:
    """Map a local feed reference to a file inside the project, refusing anything outside it."""
    ref = url.removeprefix("file://")
    path = Path(ref)
    if not path.is_absolute():
        path = (ROOT / ref) if (ROOT / ref).exists() else (SAMPLES / ref)
    path = path.resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("Local calendar paths must be inside the project's samples folder.")
    if not path.is_file():
        raise ValueError(f"No calendar file at {url}.")
    return path


async def fetch_feed(url: str) -> str:
    if url.startswith("webcal://"):
        url = "https://" + url.removeprefix("webcal://")
    if url.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Convivium calendar watcher"})
            r.raise_for_status()
            return r.text
    return resolve_feed_path(url).read_text()


class Watcher:
    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
        self._running: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None

    def is_running(self, event_id: str) -> bool:
        return event_id in self._running

    async def sync_venue(self, venue_id: str, force: bool = False) -> list[str]:
        """Read one venue's feed; start runs for new or changed events. Returns event ids scheduled."""
        venue = store.get_venue(venue_id)
        if not venue:
            return []
        try:
            events = parse_events(await fetch_feed(venue["ical_url"]))
        except Exception as exc:
            log.warning("feed for %s failed: %s", venue["name"], exc)
            return []
        store.delete_events_except(venue_id, [e["uid"] for e in events])
        scheduled = []
        for ev in events:
            row = store.upsert_event(venue_id, ev)
            if row["id"] in self._running:
                continue
            if force or row["planned_fingerprint"] != row["fingerprint"]:
                self.schedule(venue, row, kind="auto")
                scheduled.append(row["id"])
        return scheduled

    def schedule(self, venue: dict, event: dict, kind: str = "auto") -> str | None:
        if event["id"] in self._running:
            return None
        from .graph import run_agent  # avoid import cycle at module load

        self._running.add(event["id"])
        rid = store.create_run(kind, event_ctx(venue, event), venue["policy"], venue["id"], event["id"])
        store.mark_event_planned(event["id"], event["fingerprint"], rid)

        async def go() -> None:
            try:
                async with self._sem:
                    await run_agent(event_ctx(venue, event), venue["policy"], kind=kind, deliver=True,
                                    venue_id=venue["id"], event_id=event["id"], run_id=rid)
            finally:
                self._running.discard(event["id"])

        task = asyncio.create_task(go())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return rid

    async def sync_all(self) -> None:
        for v in store.list_venues():
            await self.sync_venue(v["id"])

    async def loop(self) -> None:
        while True:
            try:
                await self.sync_all()
            except Exception:
                log.exception("watcher tick failed")
            await asyncio.sleep(settings().watch_interval_seconds)

    def start(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self.loop())

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
        for t in list(self._tasks):
            t.cancel()

    async def idle(self, timeout: float = 60) -> None:
        """Wait until no runs are in flight (used by tests)."""
        end = asyncio.get_event_loop().time() + timeout
        while self._tasks and asyncio.get_event_loop().time() < end:
            await asyncio.sleep(0.05)


def event_ctx(venue: dict, event: dict) -> dict:
    return {
        "headliner": event["headliner"], "support": event.get("support") or [], "city": venue["city"],
        "venue_name": venue["name"], "address": venue.get("address", ""),
        "date": event["start"][:10], "description": event.get("description", ""),
        "is_sample": "not a real tour date" in (event.get("description") or "").lower(),
    }
