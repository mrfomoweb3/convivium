from app import store
from app.graph import run_agent

CTX = {"headliner": "Laufey", "support": ["Sampha"], "city": "New York"}


async def test_run_reaches_done_with_every_section():
    rid = await run_agent(CTX, {})
    run = store.get_run(rid)
    assert run["status"] == "done", run["error"]
    plan = run["plan"]
    for section in ("food", "drinks", "dj_set", "brands", "after_show", "promo_focus", "run_of_show"):
        assert plan[section], f"{section} is empty"
    assert plan["headline"] and plan["audience_read"]
    assert len(run["trace"]) >= 10
    assert run["baseline"] and run["baseline"]["dj_set"]
    nodes = {s["node"] for s in run["steps"]}
    assert nodes == {"resolve", "gather", "recall", "plan", "finalize", "baseline", "act", "remember"}
    assert plan["_meta"]["qloo_mode"] == "fixture"
    assert [s["node"] for s in run["steps"]][-1] == "baseline"  # the plan never waits on the baseline
    assert all(t["data_source"] == "fixture" for t in run["trace"])


async def test_grounded_items_cite_qloo_ids_and_baseline_has_none():
    run = store.get_run(await run_agent(CTX, {}))
    ids = {e["id"] for k in ("artists", "brands", "eats", "bars") for e in run["pools"][k]}
    for section in ("dj_set", "brands", "after_show"):
        for item in run["plan"][section]:
            assert item["evidence"] and set(item["evidence"]) <= ids
    for section in ("food", "drinks", "dj_set", "brands", "after_show"):
        assert all(not i["evidence"] for i in run["baseline"][section])


async def test_unknown_headliner_ends_with_clear_error(monkeypatch):
    from app import qloo

    async def nothing(self, name, take=5):
        return []

    monkeypatch.setattr(qloo.QlooClient, "search_artists", nothing)
    run = store.get_run(await run_agent({"headliner": "Zzqx", "city": "Paris"}, {}))
    assert run["status"] == "error"
    assert "Qloo has no artist matching 'Zzqx'" in run["error"]


async def test_contrasting_artists_get_different_nights():
    a = store.get_run(await run_agent({"headliner": "Laufey", "city": "New York"}, {}))["plan"]
    b = store.get_run(await run_agent({"headliner": "Turnstile", "city": "New York"}, {}))["plan"]
    assert {i["title"] for i in a["dj_set"]}.isdisjoint({i["title"] for i in b["dj_set"]})
    assert a["headline"] != b["headline"]


async def test_try_runs_only_draft():
    rid = await run_agent(CTX, {"partner_contacts": {"x": "x@example.com"}}, deliver=False)
    statuses = {a["status"] for a in store.list_outbox(rid)}
    assert statuses == {"drafted"}
