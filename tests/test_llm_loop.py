"""The LLM path, with a fake chat model (no key needed): critic loop and grounding."""
from app import graph, store
from app.planner import Item, Plan


class FakeStructured:
    def __init__(self, parent):
        self.parent = parent

    async def ainvoke(self, messages):
        self.parent.prompts.append(messages[-1][1])
        return self.parent.next_plan(messages[-1][1])


class FakeLLM:
    """First draft breaks the alcohol-free rule and invents an artist; later drafts comply."""

    def __init__(self):
        self.prompts: list[str] = []
        self.calls = 0

    def with_structured_output(self, schema):
        return FakeStructured(self)

    def next_plan(self, prompt: str) -> Plan:
        if "Evidence pools" in prompt:  # the baseline prompt has no Qloo data
            self.calls += 1
        bad = self.calls == 1 and "Evidence pools" in prompt
        return Plan(
            headline="A matcha night", audience_read="Young and bookish.",
            food=[Item(title="Halal-friendly bakery counter")],
            drinks=[Item(title="Gin spritz" if bad else "Zero-proof matcha tonic")],
            non_alcoholic_share_pct=30 if bad else 100,
            dj_set=[Item(title="Totally Invented DJ", evidence=["not-a-qloo-id"])],
            brands=[], after_show=[], promo_focus=["p"], run_of_show=["s"],
        )


async def test_violations_trigger_replan_and_guards_still_apply(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(graph, "get_llm", lambda: fake)
    monkeypatch.setattr(graph, "llm_label", lambda: "fake")
    rid = await graph.run_agent({"headliner": "Laufey", "city": "New York"}, {"no_alcohol": True})
    run = store.get_run(rid)
    assert run["status"] == "done", run["error"]
    meta = run["plan"]["_meta"]
    assert meta["composer"] == "llm:fake"
    assert meta["attempts"] == 2
    assert "Fix every one" in fake.prompts[-1]  # violations were fed back to the model
    titles = [i["title"] for i in run["plan"]["dj_set"]]
    assert "Totally Invented DJ" not in titles
    assert any(d["title"] == "Totally Invented DJ" for d in meta["dropped"])
    assert titles  # refilled from the Qloo artist pool by the rule fixer
    assert run["plan"]["non_alcoholic_share_pct"] == 100
