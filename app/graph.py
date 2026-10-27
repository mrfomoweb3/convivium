"""The LangGraph agent.

START → resolve ─┬─> gather → recall → plan ⟲ critic → finalize ─┬─> act → remember → END
                 └─> baseline (same task, no Qloo) ──────────────┘
"""
from __future__ import annotations

import asyncio
import logging
import math
import operator
import time
import traceback
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import store
from .actions import run_actions
from .llm import get_llm, llm_label
from .memory import get_memory
from .planner import (Plan, Policy, baseline_compose, check_policy, clean, deterministic_compose, enforce_policy,
                      ground_promo, llm_compose, norm, validate)
from .qloo import (QlooAuthError, QlooError, current_trace, get_client, is_hospitality, near_cluster, safe,
                   start_trace)

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


class State(TypedDict, total=False):
    ctx: dict
    policy: dict
    kind: str
    deliver: bool
    venue_id: str | None
    run_id: str
    resolved: dict
    error: str
    pools: dict
    lessons: dict
    memory_text: list[str]
    plan: dict
    composer: str
    attempts: int
    violations: list[str]
    dropped: list[dict]
    fixes: list[str]
    baseline: dict
    baseline_composer: str
    actions: list[dict]
    notes: Annotated[list[str], operator.add]


def _pick(results: list, name: str):
    if not results:
        return None
    for e in results:
        if norm(e.name) == norm(name):
            return e
    return results[0]


async def resolve(state: State) -> dict:
    ctx = state["ctx"]
    client = get_client()
    try:
        head, *rest = await asyncio.gather(
            client.search_artists(ctx["headliner"]),
            *[safe(client.search_artists(s), []) for s in (ctx.get("support") or [])[:3]],
        )
    except QlooAuthError as exc:
        return {"error": str(exc), "notes": [str(exc)]}
    except QlooError as exc:
        return {"error": f"Qloo search failed: {exc}", "notes": [f"Qloo search failed: {exc}"]}
    hl = _pick(head, ctx["headliner"])
    if hl is None:
        msg = (f"Qloo has no artist matching '{ctx['headliner']}'. Check the spelling or try their "
               f"best-known name.")
        return {"error": msg, "notes": [msg]}
    support, notes = [], [f"Resolved headliner '{ctx['headliner']}' to Qloo entity {hl.name} ({hl.id})."]
    for name, res in zip((ctx.get("support") or [])[:3], rest):
        e = _pick(res, name)
        if e:
            support.append(e.to_dict())
            notes.append(f"Resolved support act '{name}' to {e.name}.")
        else:
            notes.append(f"Qloo has no match for support act '{name}', so it isn't used as a signal.")
    return {"resolved": {"headliner": hl.to_dict(), "support": support}, "notes": notes}


def after_resolve(state: State):
    return END if state.get("error") else ["gather", "baseline"]


def _near_places(points: list[dict], places: list, max_km: float = 40.0) -> list[dict]:
    """A city heatmap can span a whole region; keep the cells around the city's fan places."""
    located = [e for e in places if e.lat is not None and e.lon is not None]
    if len(located) < 3:
        return points
    lats, lons = sorted(e.lat for e in located), sorted(e.lon for e in located)
    clat, clon = lats[len(lats) // 2], lons[len(lons) // 2]

    def km(p: dict) -> float:
        dlat = math.radians(p["lat"] - clat)
        dlon = math.radians(p["lon"] - clon) * math.cos(math.radians(clat))
        return 6371 * math.hypot(dlat, dlon)

    kept = [p for p in points if km(p) <= max_km]
    return kept or points


async def gather(state: State) -> dict:
    ctx, res = state["ctx"], state["resolved"]
    client = get_client()
    ids = [res["headliner"]["id"]] + [s["id"] for s in res["support"]]
    city = ctx["city"]
    try:
        rest_tag, bar_tag = await asyncio.gather(client.find_tag("restaurant"), client.find_tag("bar"))
        dishes, cuisines, music, demo, heat, artists, brands, eats, bars = await asyncio.gather(
            safe(client.taste_tags(ids, "urn:tag:specialty_dish:place", "Taste analysis: dishes these fans love",
                                   "dish"), []),
            safe(client.taste_tags(ids, "urn:tag:genre:place", "Taste analysis: cuisines and kinds of places",
                                   "place", take=25), []),
            safe(client.taste_tags(ids, "urn:tag:genre:music,urn:tag:style:qloo",
                                   "Taste analysis: music genres and style", "music"), []),
            safe(client.demographics(ids), {"age": {}, "gender": {}}),
            safe(client.heatmap(ids, city), []),
            safe(client.fan_artists(ids, res["headliner"].get("popularity")), []),
            safe(client.fan_brands(ids), []),
            safe(client.fan_places(ids, city, rest_tag, "Restaurants"), []),
            safe(client.fan_places(ids, city, bar_tag, "Bars"), []),
        )
    except QlooAuthError as exc:
        return {"error": str(exc), "notes": [str(exc)], "pools": {}}
    notes = []
    removed = []
    for label, group in (("restaurants", eats), ("bars", bars)):
        kept = near_cluster([e for e in group if is_hospitality(e)])
        removed += [e.name for e in group if e not in kept]
        if label == "restaurants":
            eats = kept[:12]
        else:
            bars = kept[:12]
    heat = _near_places(heat, eats + bars)
    pools = {
        "artists": [e.to_dict() for e in artists], "brands": [e.to_dict() for e in brands],
        "eats": [e.to_dict() for e in eats], "bars": [e.to_dict() for e in bars],
        "tags": dishes + cuisines + music, "demographics": demo, "heatmap": heat,
    }
    if removed:
        notes.append(f"Set aside {len(removed)} place(s) that are closed, not food or drink venues, or far outside "
                     f"{city}: " + ", ".join(sorted(set(removed))[:6]) + ("…" if len(set(removed)) > 6 else "") + ".")
    counts = {k: len(v) for k, v in pools.items() if isinstance(v, list)}
    notes = ["Gathered Qloo evidence: " + ", ".join(f"{v} {k}" for k, v in counts.items()) + "."] + notes
    gaps = [k for k, v in counts.items() if v == 0]
    if gaps:
        notes.append("Qloo returned nothing for: " + ", ".join(gaps)
                     + ". Those sections stay thin rather than being filled with guesses.")
    return {"pools": pools, "notes": notes}


async def recall(state: State) -> dict:
    mem = get_memory()
    vid = state.get("venue_id")
    if not vid:
        return {"lessons": mem.lessons(None), "memory_text": [],
                "notes": ["One-off plan with no venue on file, so there's no memory to recall."]}
    lessons = mem.lessons(vid)
    texts = await mem.recall(vid, f"What worked and what failed for {state['ctx']['headliner']} style shows?")
    n = sum(len(v) for v in lessons.values())
    return {"lessons": lessons, "memory_text": texts,
            "notes": [f"Recalled {len(texts)} venue memories and {n} structured lessons ({mem.backend})."]}


async def plan_node(state: State) -> dict:
    if state.get("error"):
        return {}
    ctx, pools, lessons = state["ctx"], state["pools"], state["lessons"]
    policy = Policy(**state["policy"])
    attempts = state.get("attempts", 0) + 1
    llm = get_llm()
    notes = []
    composer = "deterministic"
    plan: Plan | None = None
    if llm is not None:
        try:
            feedback = state.get("violations", []) if attempts > 1 else []
            try:
                plan = await llm_compose(llm, ctx, policy, pools, lessons, state.get("memory_text", []), feedback)
            except Exception as exc:  # one retry when the model's output didn't match the schema
                if "tool_use_failed" not in str(exc) and "validation" not in str(exc).lower():
                    raise
                notes.append("The LLM's first answer didn't match the plan schema; asked again.")
                plan = await llm_compose(llm, ctx, policy, pools, lessons, state.get("memory_text", []), feedback)
            composer = f"llm:{llm_label()}"
        except Exception as exc:
            reason = "hit the provider's rate limit" if "rate_limit" in str(exc) or "429" in str(exc) or "413" in str(
                exc) else f"failed ({exc.__class__.__name__})"
            log.warning("LLM composer error: %s", str(exc)[:500])
            notes.append(f"The LLM composer {reason}; using the deterministic composer for this draft.")
            plan = None
    if plan is None:
        plan = deterministic_compose(ctx, policy, pools, lessons)
    plan, dropped = validate(plan, pools)
    violations = check_policy(plan, policy, lessons)
    notes.append(f"Draft {attempts} written by the {composer} composer.")
    if dropped:
        notes.append(f"Grounding guard changed {len(dropped)} item(s) without valid Qloo evidence.")
    notes.append(f"Venue rules critic found {len(violations)} violation(s): " + " ".join(violations[:3])
                 if violations else "Venue rules critic found no violations.")
    return {"plan": plan.model_dump(), "composer": composer, "attempts": attempts,
            "dropped": state.get("dropped", []) + dropped, "violations": violations, "notes": notes}


def critic(state: State) -> str:
    if state.get("error"):
        return "finalize"
    if state.get("violations") and state.get("attempts", 0) < MAX_ATTEMPTS and get_llm() is not None:
        return "plan"
    return "finalize"


async def finalize(state: State) -> dict:
    if state.get("error"):
        return {}
    plan, fixes = enforce_policy(Plan(**state["plan"]), Policy(**state["policy"]), state["lessons"],
                                 state["pools"], state["ctx"])
    plan, dropped = validate(plan, state["pools"])
    plan = ground_promo(plan, state["pools"], state["ctx"]["city"])
    remaining = check_policy(plan, Policy(**state["policy"]), state["lessons"])
    notes = [f"Rule fixer made {len(fixes)} change(s)." if fixes else "No rule fixes needed."]
    if remaining:
        notes.append("Unresolved after fixing: " + "; ".join(remaining))
    return {"plan": plan.model_dump(), "fixes": fixes, "violations": remaining,
            "dropped": state.get("dropped", []) + dropped, "notes": notes}


async def baseline(state: State) -> dict:
    plan, composer = await baseline_compose(get_llm(), state["ctx"], Policy(**state["policy"]))
    return {"baseline": plan.model_dump(), "baseline_composer": composer,
            "notes": [f"Wrote the without-Qloo baseline ({composer}) for comparison."]}


async def act(state: State) -> dict:
    if state.get("error"):
        return {"actions": []}
    venue = store.get_venue(state["venue_id"]) if state.get("venue_id") else None
    actions = await run_actions(Plan(**state["plan"]), state["ctx"], Policy(**state["policy"]), state["pools"],
                                state["run_id"], venue, state.get("deliver", False))
    by_status: dict[str, int] = {}
    for a in actions:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1
    return {"actions": actions,
            "notes": ["Took " + str(len(actions)) + " action(s): "
                      + ", ".join(f"{v} {k}" for k, v in by_status.items()) + "."]}


async def remember(state: State) -> dict:
    vid = state.get("venue_id")
    if state.get("error") or not vid:
        return {"notes": ["Nothing written to venue memory (no venue on file)."] if not state.get("error") else []}
    plan = Plan(**state["plan"])
    mem = get_memory()
    partners = [clean(i.title) for i in plan.after_show]
    for p in partners:
        if p not in mem.lessons(vid)["past_partners"]:
            mem.add_lesson(vid, "past_partners", p, state["run_id"])
    await mem.add_note(vid, f"Planned {state['ctx']['headliner']} ({state['ctx'].get('date', 'date tbc')}): "
                            f"'{plan.headline}'. Partners: {', '.join(partners) or 'none'}. "
                            f"Food: {', '.join(i.title for i in plan.food[:3])}.")
    return {"notes": [f"Saved the plan summary and {len(partners)} partner(s) to venue memory."]}


def build_graph():
    g = StateGraph(State)
    g.add_node("resolve", resolve)
    g.add_node("gather", gather)
    g.add_node("recall", recall)
    g.add_node("plan", plan_node)
    g.add_node("finalize", finalize)
    g.add_node("baseline", baseline)
    g.add_node("act", act)
    g.add_node("remember", remember)
    g.add_edge(START, "resolve")
    g.add_conditional_edges("resolve", after_resolve, ["gather", "baseline", END])
    g.add_edge("gather", "recall")
    g.add_edge("recall", "plan")
    g.add_conditional_edges("plan", critic, ["plan", "finalize"])
    g.add_edge(["finalize", "baseline"], "act")
    g.add_edge("act", "remember")
    g.add_edge("remember", END)
    return g.compile()


GRAPH = build_graph()

NODE_LABELS = {
    "resolve": "Resolve the lineup", "gather": "Gather Qloo evidence", "recall": "Recall venue memory",
    "plan": "Write the plan", "finalize": "Enforce venue rules", "baseline": "Write the no-Qloo baseline",
    "act": "Act", "remember": "Remember",
}


async def run_agent(ctx: dict, policy: dict, kind: str = "try", deliver: bool = False, venue_id: str | None = None,
                    event_id: str | None = None, run_id: str | None = None) -> str:
    rid = run_id or store.create_run(kind, ctx, policy, venue_id, event_id)
    trace = start_trace()
    t0 = time.perf_counter()
    store.update_run(rid, status="running")
    final: dict[str, Any] = {}
    try:
        state: State = {"ctx": ctx, "policy": Policy(**policy).model_dump(), "kind": kind, "deliver": deliver,
                        "venue_id": venue_id, "run_id": rid, "notes": []}
        async for chunk in GRAPH.astream(state, stream_mode="updates"):
            for node, update in chunk.items():
                update = update or {}
                final.update({k: v for k, v in update.items() if k != "notes"})
                store.append_step(rid, {"node": node, "label": NODE_LABELS.get(node, node),
                                        "notes": update.get("notes", []), "t": round(time.perf_counter() - t0, 2)})
                if node == "gather":
                    store.update_run(rid, pools=update.get("pools"), trace=current_trace())
        if final.get("error"):
            store.update_run(rid, status="error", error=final["error"], trace=trace, baseline=final.get("baseline"))
            return rid
        plan = final["plan"]
        plan["_meta"] = {
            "composer": final.get("composer"), "attempts": final.get("attempts"),
            "dropped": final.get("dropped", []), "violations": final.get("violations", []),
            "fixes": final.get("fixes", []), "actions": [{k: a[k] for k in ("kind", "recipient", "status", "detail")}
                                                         for a in final.get("actions", [])],
            "memory_used": final.get("memory_text", []), "lessons": final.get("lessons", {}),
            "resolved": final.get("resolved"), "qloo_mode": get_client().mode,
            "calls": len(trace), "total_ms": round((time.perf_counter() - t0) * 1000),
            "baseline_composer": final.get("baseline_composer"),
        }
        store.update_run(rid, status="done", plan=plan, baseline=final.get("baseline"), pools=final.get("pools"),
                         trace=trace)
    except Exception as exc:
        log.error("run %s failed: %s", rid, traceback.format_exc())
        store.update_run(rid, status="error", error=f"The agent hit an unexpected error: {exc}", trace=trace)
    return rid
