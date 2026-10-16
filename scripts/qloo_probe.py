"""Day-1 Qloo check: make every call Convivium relies on and print the raw JSON.

    QLOO_API_KEY=... python scripts/qloo_probe.py                 # Laufey in New York
    QLOO_API_KEY=... python scripts/qloo_probe.py "Peso Pluma" "Mexico City"
    QLOO_API_KEY=... python scripts/qloo_probe.py --coverage      # every demo artist and city, counts only

Raw responses are saved under scripts/probe_out/ so parsers can be checked
against what Qloo actually returns. The key is never printed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

OUT = ROOT / "scripts" / "probe_out"
DEMO = [("Little Simz", "London"), ("Bring Me The Horizon", "London"), ("Laufey", "New York"),
        ("Turnstile", "New York"), ("Peso Pluma", "Mexico City"), ("Natalia Lafourcade", "Mexico City"),
        ("YOASOBI", "Tokyo"), ("Hiromi", "Tokyo"), ("Fred again..", "Berlin"), ("Nils Frahm", "Berlin")]


def count(payload: dict) -> int:
    res = payload.get("results")
    if isinstance(res, list):
        return len(res)
    if isinstance(res, dict):
        return sum(len(v) for v in res.values() if isinstance(v, list))
    return 0


async def call(client: httpx.AsyncClient, label: str, path: str, params: dict, save: bool) -> dict:
    r = await client.get(path, params=params)
    try:
        body = r.json()
    except ValueError:
        body = {"_raw": r.text[:2000]}
    if save:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{label}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False))
        print(f"\n=== {label}  HTTP {r.status_code}  {path} {params}")
        print(json.dumps(body, indent=2, ensure_ascii=False)[:2500])
    return {"status": r.status_code, "count": count(body) if isinstance(body, dict) else 0, "body": body}


async def probe(client: httpx.AsyncClient, artist: str, city: str, save: bool) -> dict:
    found = await call(client, "search", "/search", {"query": artist, "types": "urn:entity:artist", "take": 5}, save)
    results = found["body"].get("results") or []
    if not results:
        return {"artist": artist, "city": city, "search": 0}
    ent = results[0]
    eid = ent["entity_id"]
    pop = ent.get("popularity")
    sig = {"signal.interests.entities": eid}
    tags = {}
    for term in ("restaurant", "bar"):
        typed = await call(client, f"tags_{term}", "/v2/tags",
                           {"filter.query": term, "filter.tag.types": "urn:tag:category:place"}, save)
        if not typed["count"]:
            await call(client, f"tags_{term}_untyped", "/v2/tags", {"filter.query": term}, save)
        tags[term] = f"urn:tag:category:place:{term}"
    calls = {
        "artists": {"filter.type": "urn:entity:artist", **sig, "filter.exclude.entities": eid, "take": 15,
                    **({"filter.popularity.max": pop} if pop else {})},
        "brands": {"filter.type": "urn:entity:brand", **sig, "take": 10},
        "restaurants": {"filter.type": "urn:entity:place", **sig, "filter.location.query": city,
                        "filter.tags": tags["restaurant"], "take": 12},
        "bars": {"filter.type": "urn:entity:place", **sig, "filter.location.query": city,
                 "filter.tags": tags["bar"], "take": 12},
        "taste_tags": {"filter.type": "urn:tag", **sig, "take": 25},
        "demographics": {"filter.type": "urn:demographics", **sig},
        "heatmap": {"filter.type": "urn:heatmap", "filter.location.query": city, **sig, "take": 60},
    }
    row = {"artist": artist, "city": city, "search": len(results), "resolved": ent.get("name"), "popularity": pop}
    for label, params in calls.items():
        res = await call(client, label, "/v2/insights", params, save)
        row[label] = res["count"] if res["status"] == 200 else f"HTTP {res['status']}"
    return row


async def main() -> None:
    s = settings()
    if not s.qloo_api_key:
        sys.exit("Set QLOO_API_KEY first (the hackathon key).")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    async with httpx.AsyncClient(base_url=s.qloo_base, timeout=30,
                                 headers={"X-Api-Key": s.qloo_api_key}) as client:
        if "--coverage" in sys.argv:
            rows = [await probe(client, a, c, save=False) for a, c in DEMO]
            keys = ["artist", "city", "resolved", "artists", "brands", "restaurants", "bars", "taste_tags",
                    "demographics", "heatmap"]
            print("\t".join(keys))
            for r in rows:
                print("\t".join(str(r.get(k, "-")) for k in keys))
            print("\nSwap any artist or city whose restaurants/bars/heatmap come back thin (under ~4).")
        else:
            artist, city = (args + ["Laufey", "New York"][len(args):])[:2]
            print(json.dumps(await probe(client, artist, city, save=True), indent=2))
            print(f"\nRaw responses saved in {OUT}")


if __name__ == "__main__":
    os.environ.setdefault("QLOO_FIXTURES", "0")
    asyncio.run(main())
