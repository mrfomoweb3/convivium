import time

import pytest
from fastapi.testclient import TestClient

from app import store
from app.graph import run_agent
from app.memory import get_memory


async def test_declined_partner_disappears_from_next_plan():
    v = store.create_venue("Hall", "Berlin", "samples/spree-kantine.ics", {"max_partners": 3})
    ctx = {"headliner": "Nils Frahm", "city": "Berlin", "venue_name": "Hall"}
    first = store.get_run(await run_agent(ctx, v["policy"], kind="auto", deliver=True, venue_id=v["id"]))["plan"]
    declined = first["after_show"][0]["title"]
    flopped = first["food"][0]["title"]
    get_memory().add_lesson(v["id"], "declined", declined)
    get_memory().add_lesson(v["id"], "flopped", flopped)
    second = store.get_run(await run_agent(ctx, v["policy"], kind="replan", deliver=True, venue_id=v["id"]))["plan"]
    assert declined not in [p["title"] for p in second["after_show"]]
    assert flopped not in [f["title"] for f in second["food"]]
    assert len(second["after_show"]) == 3
    assert any(declined in s for s in second["_meta"]["lessons"]["declined"])
    assert store.list_memories(v["id"])  # remember node wrote a summary


async def test_venue_run_outbox_reports_real_statuses():
    v = store.create_venue("Hall", "London", "samples/northside-hall.ics", {"partner_contacts": {}})
    rid = await run_agent({"headliner": "Little Simz", "city": "London"}, v["policy"], kind="auto", deliver=True,
                          venue_id=v["id"])
    out = {a["kind"]: a for a in store.list_outbox(rid)}
    assert out["partner_pitch"]["status"] == "queued"
    assert "no contact on file" in out["partner_pitch"]["detail"]
    assert out["plan_email"]["status"] == "skipped"
    assert out["webhook"]["status"] == "skipped"


@pytest.fixture
def client():
    from app import main

    main._hits.clear()
    with TestClient(main.app) as c:
        yield c


def wait_done(client, rid, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        run = client.get(f"/api/runs/{rid}").json()
        if run["status"] in ("done", "error"):
            return run
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True, "qloo_mode": "fixture"}


def test_try_returns_run_then_finished_run(client):
    res = client.post("/api/try", json={"headliner": "YOASOBI", "city": "Tokyo"})
    assert res.status_code == 200
    run = wait_done(client, res.json()["run_id"])
    assert run["status"] == "done"
    assert run["plan"]["dj_set"] and run["baseline"] and run["trace"]
    assert {a["status"] for a in run["outbox"]} == {"drafted"}


def test_try_is_rate_limited(client):
    codes = [client.post("/api/try", json={"headliner": "Hiromi", "city": "Tokyo"}).status_code for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


def test_try_validates_input(client):
    assert client.post("/api/try", json={"headliner": "  ", "city": "Tokyo"}).status_code == 422


def test_outcome_replans_next_shows(client):
    from app import main

    res = client.post("/api/venues", json={"name": "Kiln", "city": "Tokyo", "ical_url": "samples/kiln-live.ics"})
    assert res.status_code == 200 and res.json()["scheduled"] == 2
    vid = res.json()["venue"]["id"]
    end = time.time() + 30
    while time.time() < end and not all(e["last_run_status"] == "done"
                                        for e in client.get("/api/venues").json()[0]["events"]):
        time.sleep(0.1)
    venue = next(v for v in client.get("/api/venues").json() if v["id"] == vid)
    run = client.get(f"/api/runs/{venue['events'][0]['last_run_id']}").json()
    partner = run["plan"]["after_show"][0]["title"]
    out = client.post(f"/api/venues/{vid}/outcomes", json={"run_id": run["id"], "declined_partners": [partner]}).json()
    assert len(out["replanning"]) == 2
    for rid in out["replanning"]:
        replanned = wait_done(client, rid)
        assert replanned["kind"] == "replan"
        assert partner not in [p["title"] for p in replanned["plan"]["after_show"]]
    mem = client.get(f"/api/venues/{vid}/memory").json()
    assert partner in mem["lessons"]["declined"]


def test_connect_rejects_paths_outside_project(client):
    res = client.post("/api/venues", json={"name": "X", "city": "Y", "ical_url": "/etc/passwd"})
    assert res.status_code == 400


def test_runs_interrupted_by_a_restart_are_failed_and_replanned():
    vid = store.create_venue("V", "New York", "samples/ironworks-room.ics", {})["id"]
    ev = store.upsert_event(vid, {"uid": "u1", "summary": "Laufey", "start": "2026-11-11T20:00", "headliner": "Laufey",
                                  "support": [], "fingerprint": "fp1"})
    rid = store.create_run("auto", {"headliner": "Laufey", "city": "New York"}, {}, vid, ev["id"])
    store.mark_event_planned(ev["id"], "fp1", rid)
    assert store.recover_interrupted_runs() == 1
    assert store.get_run(rid)["status"] == "error"
    assert store.get_event(ev["id"])["planned_fingerprint"] is None
    assert store.recover_interrupted_runs() == 0
