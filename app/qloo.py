"""Qloo client: tracing, caching, retries and normalisation.

Every call is recorded in a per-run trace (a contextvars list started with
``start_trace()``). That trace becomes the "Qloo receipts" panel.
"""
from __future__ import annotations

import asyncio
import contextvars
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

import httpx

from . import fixtures
from .config import settings

T = TypeVar("T")

CACHE_TTL_SECONDS = 6 * 3600
MAX_ATTEMPTS = 4
MAX_BACKOFF_SECONDS = 10.0

# Hard-coded fallbacks if both tag lookups come back empty.
FALLBACK_TAGS = {
    "restaurant": "urn:tag:category:place:restaurant",
    "bar": "urn:tag:category:place:bar",
}

_TRACE: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar("qloo_trace", default=None)


class QlooError(RuntimeError):
    pass


class QlooAuthError(QlooError):
    pass


def start_trace() -> list[dict]:
    trace: list[dict] = []
    _TRACE.set(trace)
    return trace


def current_trace() -> list[dict]:
    return _TRACE.get() or []


@dataclass
class Entity:
    id: str
    name: str
    type: str
    affinity: float | None = None
    popularity: float | None = None
    tags: list[dict] = field(default_factory=list)
    address: str = ""
    lat: float | None = None
    lon: float | None = None
    image: str = ""
    source: str = "qloo"

    def to_dict(self) -> dict:
        return asdict(self)


def _num(value: Any) -> float | None:
    try:
        return None if value is None else round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _results(payload: dict, key: str) -> list:
    """Pull a list out of ``results`` whether it is a list or a dict of lists."""
    res = (payload or {}).get("results")
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        val = res.get(key)
        if isinstance(val, list):
            return val
        for v in res.values():  # tolerate an unexpected key name
            if isinstance(v, list):
                return v
    return []


def normalise_entity(raw: dict, source: str) -> Entity:
    props = raw.get("properties") or {}
    loc = raw.get("location") or props.get("geocode") or {}
    image = props.get("image") or {}
    types = raw.get("types") or [raw.get("subtype") or raw.get("type") or ""]
    tags = []
    for t in raw.get("tags") or []:
        if isinstance(t, dict):
            tags.append({"id": t.get("id") or t.get("tag_id") or "", "name": t.get("name") or "",
                         "type": t.get("type") or t.get("subtype") or ""})
    address = props.get("address") or ""
    if isinstance(address, dict):
        address = ", ".join(str(v) for v in address.values() if v)
    return Entity(
        id=str(raw.get("entity_id") or raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        type=str(types[0] if types else ""),
        affinity=_num((raw.get("query") or {}).get("affinity")),
        popularity=_num(raw.get("popularity")),
        tags=tags[:12],
        address=str(address),
        lat=_num(loc.get("lat", loc.get("latitude"))),
        lon=_num(loc.get("lon", loc.get("longitude"))),
        image=str(image.get("url") or "") if isinstance(image, dict) else str(image or ""),
        source=source,
    )


def parse_entities(payload: dict, source: str) -> list[Entity]:
    out = [normalise_entity(r, source) for r in _results(payload, "entities") if isinstance(r, dict)]
    return [e for e in out if e.id and e.name]


def parse_tags(payload: dict, source: str) -> list[dict]:
    out = []
    for r in _results(payload, "tags"):
        if not isinstance(r, dict):
            continue
        tid = r.get("id") or r.get("tag_id") or r.get("entity_id")
        if not tid or not r.get("name"):
            continue
        out.append({"id": str(tid), "name": str(r["name"]),
                    "type": str(r.get("type") or r.get("subtype") or ""),
                    "affinity": _num((r.get("query") or {}).get("affinity")), "source": source})
    return out


def parse_demographics(payload: dict, source: str) -> dict:
    """Average age and gender skews across returned rows. Scores are roughly -1..1."""
    age: dict[str, list[float]] = {}
    gender: dict[str, list[float]] = {}
    for row in _results(payload, "demographics"):
        q = (row or {}).get("query") or {}
        for bucket, score in (q.get("age") or {}).items():
            if _num(score) is not None:
                age.setdefault(bucket, []).append(float(score))
        for g, score in (q.get("gender") or {}).items():
            if _num(score) is not None:
                gender.setdefault(g, []).append(float(score))
    avg = lambda d: {k: round(sum(v) / len(v), 3) for k, v in d.items()}  # noqa: E731
    return {"age": avg(age), "gender": avg(gender), "source": source}


def parse_heatmap(payload: dict, source: str) -> list[dict]:
    out = []
    for p in _results(payload, "heatmap"):
        loc = (p or {}).get("location") or {}
        q = (p or {}).get("query") or {}
        lat, lon = _num(loc.get("latitude", loc.get("lat"))), _num(loc.get("longitude", loc.get("lon")))
        if lat is None or lon is None:
            continue
        out.append({"lat": lat, "lon": lon, "affinity": _num(q.get("affinity")) or 0.0,
                    "popularity": _num(q.get("popularity")), "source": source})
    return out


def _result_count(payload: dict) -> int:
    res = (payload or {}).get("results")
    if isinstance(res, list):
        return len(res)
    if isinstance(res, dict):
        return sum(len(v) for v in res.values() if isinstance(v, list))
    return 0


class QlooClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, use_fixtures: bool | None = None):
        s = settings()
        self.base = s.qloo_base
        self._key = s.qloo_api_key
        self.use_fixtures = (s.qloo_mode == "fixture") if use_fixtures is None else use_fixtures
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._sem = asyncio.Semaphore(s.qloo_concurrency)
        self._cache: dict[str, tuple[float, dict]] = {}
        self._sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    @property
    def mode(self) -> str:
        return "fixture" if self.use_fixtures else "live"

    @property
    def data_source(self) -> str:
        return "fixture" if self.use_fixtures else "qloo"

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.base, timeout=20.0, transport=self._transport,
                headers={"X-Api-Key": self._key, "Accept": "application/json"},
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    async def get(self, path: str, params: dict, purpose: str) -> dict:
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        key = path + "?" + "&".join(f"{k}={clean[k]}" for k in sorted(clean))
        entry = {"path": path, "params": clean, "purpose": purpose, "ms": 0, "cache": False,
                 "count": 0, "status": None, "data_source": self.data_source}
        trace = _TRACE.get()
        if trace is not None:
            trace.append(entry)
        t0 = time.perf_counter()
        try:
            hit = self._cache.get(key)
            if hit and hit[0] > time.time():
                entry.update(cache=True, status=200, count=_result_count(hit[1]))
                return hit[1]
            if self.use_fixtures:
                payload = fixtures.respond(path, clean)
                entry["status"] = 200
            else:
                payload = await self._fetch(path, clean, entry)
            entry["count"] = _result_count(payload)
            self._cache[key] = (time.time() + CACHE_TTL_SECONDS, payload)
            return payload
        finally:
            entry["ms"] = round((time.perf_counter() - t0) * 1000)

    async def _fetch(self, path: str, params: dict, entry: dict) -> dict:
        async with self._sem:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    resp = await self._client().get(path, params=params)
                except httpx.HTTPError as exc:
                    entry["status"] = "network-error"
                    if attempt == MAX_ATTEMPTS:
                        raise QlooError(f"Qloo request to {path} failed: {exc.__class__.__name__}") from exc
                    await self._sleep(min(0.5 * 2 ** attempt, MAX_BACKOFF_SECONDS))
                    continue
                entry["status"] = resp.status_code
                if resp.status_code in (401, 403):
                    raise QlooAuthError(
                        f"Qloo rejected the request with HTTP {resp.status_code}. Check your hackathon key "
                        f"(QLOO_API_KEY) and that QLOO_BASE is the hackathon host ({self.base})."
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt == MAX_ATTEMPTS:
                        raise QlooError(f"Qloo returned HTTP {resp.status_code} for {path} after {attempt} attempts")
                    wait = 0.5 * 2 ** attempt
                    try:
                        wait = float(resp.headers.get("Retry-After", wait))
                    except ValueError:
                        pass
                    entry["retries"] = attempt
                    await self._sleep(min(max(wait, 0.0), MAX_BACKOFF_SECONDS))
                    continue
                if resp.status_code >= 400:
                    raise QlooError(f"Qloo returned HTTP {resp.status_code} for {path}: {resp.text[:200]}")
                try:
                    return resp.json()
                except ValueError as exc:
                    raise QlooError(f"Qloo returned non-JSON for {path}") from exc
        raise QlooError(f"Qloo request to {path} failed")  # pragma: no cover

    # ---- typed helpers -------------------------------------------------

    async def search_artists(self, name: str, take: int = 5) -> list[Entity]:
        payload = await self.get("/search", {"query": name, "types": "urn:entity:artist", "take": take},
                                 f"Find the Qloo id for the artist '{name}'")
        return parse_entities(payload, self.data_source)

    async def find_tag(self, term: str, tag_type: str = "urn:tag:category:place") -> str:
        """Resolve a tag id: typed lookup, then untyped, then a hard-coded id."""
        wanted = term.lower()
        for params, purpose in (
            ({"filter.query": term, "filter.tag.types": tag_type}, f"Look up the '{term}' place tag"),
            ({"filter.query": term}, f"Look up the '{term}' tag (untyped fallback)"),
        ):
            try:
                tags = parse_tags(await self.get("/v2/tags", params, purpose), self.data_source)
            except QlooAuthError:
                raise
            except QlooError:
                tags = []
            for t in tags:
                if t["id"].endswith(":" + wanted) and "category:place" in t["id"]:
                    return t["id"]
            for t in tags:
                if t["name"].lower().removesuffix(fixtures.SUFFIX.lower()) == wanted:
                    return t["id"]
        return FALLBACK_TAGS.get(wanted, f"urn:tag:category:place:{wanted}")

    async def insights(self, params: dict, purpose: str) -> dict:
        return await self.get("/v2/insights", params, purpose)

    async def fan_artists(self, ids: list[str], max_popularity: float | None, take: int = 15) -> list[Entity]:
        joined = ",".join(ids)
        params = {"filter.type": "urn:entity:artist", "signal.interests.entities": joined,
                  "filter.exclude.entities": joined, "take": take}
        if max_popularity is not None:
            params["filter.popularity.max"] = max_popularity
        return parse_entities(await self.insights(params, "Artists these fans love, for the DJ set"),
                              self.data_source)

    async def fan_brands(self, ids: list[str], take: int = 10) -> list[Entity]:
        params = {"filter.type": "urn:entity:brand", "signal.interests.entities": ",".join(ids), "take": take}
        return parse_entities(await self.insights(params, "Brands these fans over-index on"), self.data_source)

    async def fan_places(self, ids: list[str], city: str, tag_id: str, label: str, take: int = 12) -> list[Entity]:
        params = {"filter.type": "urn:entity:place", "signal.interests.entities": ",".join(ids),
                  "filter.location.query": city, "filter.tags": tag_id, "take": take}
        return parse_entities(await self.insights(params, f"{label} in {city} these fans love"), self.data_source)

    async def taste_tags(self, ids: list[str], take: int = 25) -> list[dict]:
        params = {"filter.type": "urn:tag", "signal.interests.entities": ",".join(ids), "take": take}
        return parse_tags(await self.insights(params, "Taste analysis: tags that describe this audience"),
                          self.data_source)

    async def demographics(self, ids: list[str]) -> dict:
        params = {"filter.type": "urn:demographics", "signal.interests.entities": ",".join(ids)}
        return parse_demographics(await self.insights(params, "Audience age and gender skew"), self.data_source)

    async def heatmap(self, ids: list[str], city: str, take: int = 60) -> list[dict]:
        params = {"filter.type": "urn:heatmap", "filter.location.query": city,
                  "signal.interests.entities": ",".join(ids), "take": take}
        return parse_heatmap(await self.insights(params, f"Where in {city} these fans are"), self.data_source)


async def safe(coro: Awaitable[T], default: T) -> T:
    """Run one fan-out call; a failure returns ``default`` (auth errors still raise)."""
    try:
        return await coro
    except QlooAuthError:
        raise
    except QlooError:
        return default


_CLIENT: QlooClient | None = None


def get_client() -> QlooClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = QlooClient()
    return _CLIENT


def set_client(client: QlooClient | None) -> None:
    global _CLIENT
    _CLIENT = client
