import httpx
import pytest

from app.qloo import QlooAuthError, QlooClient, parse_demographics, parse_entities, parse_heatmap, start_trace


def make_client(handler):
    c = QlooClient(transport=httpx.MockTransport(handler), use_fixtures=False)
    c._key = "test-key"

    async def no_sleep(_):
        return None

    c._sleep = no_sleep
    return c


async def test_429_retries_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        assert req.headers["X-Api-Key"]
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"results": [{"entity_id": "x", "name": "X", "types": ["urn:entity:artist"]}]})

    trace = start_trace()
    res = await make_client(handler).search_artists("X")
    assert calls["n"] == 3 and res[0].id == "x"
    assert trace[0]["status"] == 200 and trace[0]["retries"] == 2


async def test_401_raises_clear_error():
    client = make_client(lambda req: httpx.Response(401, json={"error": "nope"}))
    with pytest.raises(QlooAuthError, match="Check your hackathon key"):
        await client.search_artists("X")


async def test_cache_hit_is_traced():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"results": {"entities": []}})

    client = make_client(handler)
    trace = start_trace()
    await client.fan_brands(["a"])
    await client.fan_brands(["a"])
    assert calls["n"] == 1
    assert [t["cache"] for t in trace] == [False, True]
    assert "test-key" not in str(trace)


async def test_failed_fanout_call_falls_back():
    from app.qloo import safe

    client = make_client(lambda req: httpx.Response(500))
    assert await safe(client.fan_brands(["a"]), []) == []


def test_parsers_are_tolerant():
    ents = parse_entities({"results": {"entities": [
        {"entity_id": "p1", "name": "Place", "types": ["urn:entity:place"], "query": {"affinity": 0.8},
         "properties": {"address": "1 Road", "image": {"url": "http://img"}}, "location": {"lat": 1, "lon": 2}},
        {"name": "missing id"},
    ]}}, "qloo")
    assert len(ents) == 1 and ents[0].lat == 1 and ents[0].image == "http://img" and ents[0].affinity == 0.8
    demo = parse_demographics({"results": {"demographics": [
        {"query": {"age": {"24_and_younger": 0.2}, "gender": {"female": 0.1}}},
        {"query": {"age": {"24_and_younger": 0.4}}}]}}, "qloo")
    assert demo["age"]["24_and_younger"] == 0.3
    heat = parse_heatmap({"results": {"heatmap": [{"location": {"latitude": 1, "longitude": 2},
                                                   "query": {"affinity": 0.5}}, {"location": {}}]}}, "qloo")
    assert heat == [{"lat": 1.0, "lon": 2.0, "affinity": 0.5, "popularity": None, "source": "qloo"}]


async def test_fixture_names_and_ids_are_marked():
    client = QlooClient(use_fixtures=True)
    for e in await client.fan_places(["fixture-artist-x"], "Lagos", "urn:tag:category:place:bar", "Bars"):
        assert e.name.endswith("(fixture)") and "fixture" in e.id
    for e in await client.fan_brands(["fixture-artist-x"]):
        assert e.name.endswith("(fixture)") and "fixture" in e.id
