"""FastAPI routes, demo seeding and background tasks."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import store
from .calendar_watch import Watcher, event_ctx
from .config import ROOT, settings
from .graph import run_agent
from .llm import llm_label
from .memory import get_memory
from .planner import Policy
from .qloo import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("convivium")

WEB = ROOT / "web"
SAMPLE_NOTE = "Sample calendar for the Convivium demo. Not a real tour date."

DEMO_VENUES = [
    {"id": "demo-northside-hall", "name": "Northside Hall", "city": "London", "capacity": 2300,
     "address": "Fictional venue, North London", "ical_url": "samples/northside-hall.ics",
     "policy": {"dietary": ["halal"], "max_partners": 3}},
    {"id": "demo-ironworks-room", "name": "Ironworks Room", "city": "New York", "capacity": 1100,
     "address": "Fictional venue, Brooklyn", "ical_url": "samples/ironworks-room.ics",
     "policy": {"no_alcohol": True, "notes": "All-ages room; nothing alcoholic anywhere in the building."}},
    {"id": "demo-casa-sonora", "name": "Casa Sonora", "city": "Mexico City", "capacity": 3000,
     "address": "Fictional venue, Roma Norte", "ical_url": "samples/casa-sonora.ics",
     "policy": {"dietary": ["vegetarian"]}},
    {"id": "demo-kiln-live", "name": "Kiln Live", "city": "Tokyo", "capacity": 1500,
     "address": "Fictional venue, Shibuya", "ical_url": "samples/kiln-live.ics", "policy": {}},
    {"id": "demo-spree-kantine", "name": "Spree Kantine", "city": "Berlin", "capacity": 1600,
     "address": "Fictional venue, Friedrichshain", "ical_url": "samples/spree-kantine.ics",
     "policy": {"dietary": ["vegan"]}},
]

watcher = Watcher()
_bg: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def seed_demo() -> None:
    for v in DEMO_VENUES:
        if store.get_venue(v["id"]):
            continue
        store.create_venue(v["name"], v["city"], v["ical_url"], Policy(**v["policy"]).model_dump(),
                           address=v["address"], capacity=v["capacity"], is_demo=True, venue_id=v["id"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = settings()
    store.init_db()
    if n := store.recover_interrupted_runs():
        log.info("Recovered %d run(s) interrupted by a restart; their shows will be planned again", n)
    if s.seed_demo:
        seed_demo()
    log.info("Convivium starting: qloo=%s planner=%s memory=%s", get_client().mode, llm_label(),
             get_memory().backend)
    if s.watcher_enabled:
        watcher.start()
    yield
    await watcher.stop()
    for t in list(_bg):
        t.cancel()


app = FastAPI(title="Convivium", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB), name="static")


# ---- models ------------------------------------------------------------------

class TryRequest(BaseModel):
    headliner: str = Field(min_length=1, max_length=120)
    support: list[str] = Field(default_factory=list, max_length=3)
    city: str = Field(min_length=1, max_length=120)
    venue_name: str | None = Field(default=None, max_length=120)
    policy: Policy = Field(default_factory=Policy)

    @field_validator("headliner", "city")
    @classmethod
    def strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("support")
    @classmethod
    def strip_list(cls, v: list[str]) -> list[str]:
        return [s.strip()[:120] for s in v if s and s.strip()]


class VenueRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=120)
    address: str = Field(default="", max_length=240)
    capacity: int | None = Field(default=None, ge=0, le=200000)
    ical_url: str = Field(min_length=1, max_length=2000)
    ops_email: str = Field(default="", max_length=240)
    policy: Policy = Field(default_factory=Policy)


class OutcomeRequest(BaseModel):
    run_id: str | None = None
    sold_well: list[str] = Field(default_factory=list)
    flopped: list[str] = Field(default_factory=list)
    declined_partners: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000)


# ---- rate limit --------------------------------------------------------------

_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request) -> None:
    now = time.monotonic()
    q = _hits[_client_ip(request)]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= settings().try_rate_per_minute:
        raise HTTPException(429, "You've planned a lot of nights in the last minute. Wait a moment and try again.")
    q.append(now)


# ---- routes ------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "qloo_mode": get_client().mode}


@app.get("/api/status")
async def status():
    s = settings()
    return {
        "qloo_mode": get_client().mode, "llm": llm_label(), "memory": get_memory().backend,
        "watcher_enabled": s.watcher_enabled, "watch_interval_seconds": s.watch_interval_seconds,
        "email_configured": bool(s.resend_api_key), "webhook_configured": bool(s.webhook_url),
        # CARTO tiles now need an API key; without one, use OSM tiles muted in CSS.
        "map": {"url": s.map_tile_url, "attribution": s.map_tile_attribution} if s.map_tile_url else
               {"url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "muted": True,
                "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'},
    }


def _venue_view(v: dict) -> dict:
    events = []
    for e in store.list_events(v["id"]):
        run = store.get_run(e["last_run_id"]) if e.get("last_run_id") else None
        events.append({
            "id": e["id"], "summary": e["summary"], "start": e["start"], "headliner": e["headliner"],
            "support": e["support"], "is_sample": SAMPLE_NOTE.lower() in (e.get("description") or "").lower(),
            "last_run_id": e.get("last_run_id"), "last_run_status": run["status"] if run else None,
            "planning": watcher.is_running(e["id"]),
        })
    return {k: v[k] for k in ("id", "name", "city", "address", "capacity", "ical_url", "policy", "is_demo")} | {
        "has_ops_email": bool(v.get("ops_email")), "events": events}


@app.get("/api/venues")
async def venues():
    return [_venue_view(v) for v in store.list_venues()]


@app.post("/api/venues")
async def connect_venue(req: VenueRequest):
    url = req.ical_url.strip()
    if not url.startswith(("http://", "https://", "webcal://")):
        from .calendar_watch import resolve_feed_path

        try:
            resolve_feed_path(url)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    v = store.create_venue(req.name.strip(), req.city.strip(), url, req.policy.model_dump(),
                           address=req.address, capacity=req.capacity, ops_email=req.ops_email.strip())
    scheduled = await watcher.sync_venue(v["id"])
    return {"venue": _venue_view(store.get_venue(v["id"])), "scheduled": len(scheduled)}


def _require_venue(vid: str) -> dict:
    v = store.get_venue(vid)
    if not v:
        raise HTTPException(404, "No venue with that id.")
    return v


@app.post("/api/venues/{vid}/sync")
async def sync_venue(vid: str):
    _require_venue(vid)
    scheduled = await watcher.sync_venue(vid, force=True)
    return {"scheduled": len(scheduled)}


@app.post("/api/venues/{vid}/outcomes")
async def outcomes(vid: str, req: OutcomeRequest):
    venue = _require_venue(vid)
    mem = get_memory()
    for kind, values in (("sold_well", req.sold_well), ("flopped", req.flopped),
                         ("declined", req.declined_partners)):
        for value in values:
            mem.add_lesson(vid, kind, value, req.run_id)
    summary = []
    if req.sold_well:
        summary.append("sold well: " + ", ".join(req.sold_well))
    if req.flopped:
        summary.append("flopped: " + ", ".join(req.flopped))
    if req.declined_partners:
        summary.append("declined to partner: " + ", ".join(req.declined_partners))
    if req.notes.strip():
        summary.append("notes: " + req.notes.strip())
    if summary:
        await mem.add_note(vid, "Outcome reported. " + "; ".join(summary) + ".")
    now = time.strftime("%Y-%m-%d")
    upcoming = [e for e in store.list_events(vid) if (e["start"] or "")[:10] >= now][:3]
    run_ids = [rid for e in upcoming if (rid := watcher.schedule(venue, e, kind="replan"))]
    return {"stored": sum(map(len, (req.sold_well, req.flopped, req.declined_partners))), "replanning": run_ids}


@app.get("/api/venues/{vid}/memory")
async def venue_memory(vid: str):
    _require_venue(vid)
    return {"backend": get_memory().backend, "memories": store.list_memories(vid),
            "lessons": get_memory().lessons(vid)}


@app.post("/api/try")
async def try_it(req: TryRequest, request: Request):
    _rate_limit(request)
    ctx = {"headliner": req.headliner, "support": req.support, "city": req.city,
           "venue_name": (req.venue_name or "").strip() or None}
    policy = req.policy.model_dump()
    policy["partner_contacts"] = {}  # the public path never contacts anyone
    rid = store.create_run("try", ctx, policy)
    _spawn(run_agent(ctx, policy, kind="try", deliver=False, run_id=rid))
    return {"run_id": rid}


@app.get("/api/runs")
async def runs(limit: int = 30, venue_id: str | None = None):
    return store.list_runs(min(max(limit, 1), 100), venue_id)


@app.get("/api/runs/{rid}")
async def run(rid: str):
    r = store.get_run(rid)
    if not r:
        raise HTTPException(404, "No run with that id.")
    r["outbox"] = store.list_outbox(rid)
    if r.get("policy"):
        r["policy"].pop("partner_contacts", None)  # contact emails stay server-side
    if r.get("venue_id"):
        v = store.get_venue(r["venue_id"])
        r["venue"] = {"id": v["id"], "name": v["name"], "city": v["city"], "is_demo": bool(v["is_demo"])} if v else None
    for item in r["outbox"]:
        if item["kind"].endswith("_pitch") and "@" in (item.get("recipient") or ""):
            item["recipient"] = "partner contact on file"
    return r


__all__ = ["app", "event_ctx"]
