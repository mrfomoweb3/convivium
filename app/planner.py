"""Plan schema, composers (LLM and deterministic), guards and the no-Qloo baseline.

The guards (``validate``, ``check_policy``, ``enforce_policy``) run on every
plan whichever composer wrote it, so grounding and venue rules are enforced in
code rather than by asking the model nicely.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import BaseModel, Field

from .fixtures import SUFFIX


class Item(BaseModel):
    title: str
    detail: str = ""
    evidence: list[str] = Field(default_factory=list, description="Qloo ids from the evidence pools")


class Plan(BaseModel):
    headline: str = Field(description="Short, specific title for the night")
    audience_read: str = Field(description="At most two sentences interpreting demographics and tags")
    # Lists default to empty so a model that skips a section doesn't fail the whole draft;
    # the guards refill grounded sections from the Qloo pools.
    food: list[Item] = Field(default_factory=list)
    drinks: list[Item] = Field(default_factory=list)
    non_alcoholic_share_pct: int = Field(default=25, ge=0, le=100)
    dj_set: list[Item] = Field(default_factory=list)
    brands: list[Item] = Field(default_factory=list)
    after_show: list[Item] = Field(default_factory=list)
    promo_focus: list[str] = Field(default_factory=list)
    run_of_show: list[str] = Field(default_factory=list)


class Policy(BaseModel):
    no_alcohol: bool = False
    dietary: list[str] = Field(default_factory=list)
    max_partners: int = Field(default=4, ge=0, le=12)
    notes: str = ""
    partner_contacts: dict[str, str] = Field(default_factory=dict)


SECTION_POOLS = {"dj_set": ("artists",), "brands": ("brands",), "after_show": ("eats", "bars")}
CREATIVE_SECTIONS = ("food", "drinks")

FOOD_WORDS = (
    "burger", "taco", "ramen", "noodle", "sushi", "pizza", "bbq", "barbecue", "wing", "bakery", "pastry",
    "brunch", "dumpling", "bao", "curry", "jollof", "suya", "tamal", "mole", "elote", "street food", "food",
    "cuisine", "kitchen", "dessert", "ice cream", "chocolate", "cheese", "fried chicken", "korean", "japanese",
    "mexican", "italian", "thai", "indian", "ethiopian", "mediterranean", "middle eastern", "french", "seafood",
    "steak", "sandwich", "bagel", "donut", "doughnut", "kebab", "falafel", "pho", "banh mi", "poke", "vegan",
    "vegetarian", "west african", "nigerian", "caribbean", "jerk", "dim sum", "izakaya", "yakitori", "tapas",
    "empanada", "arepa", "ceviche", "shawarma", "biryani", "kimchi", "bibimbap", "gyoza", "onigiri", "crepe",
    "croissant", "gelato", "churro", "hot dog", "fries", "chili", "european", "american", "british",
    "breakfast", "latin", "asian", "african", "spanish", "greek", "turkish", "lebanese", "vietnamese", "chinese",
    "peruvian", "brazilian", "german", "souffle", "soufflé", "tartare", "oyster", "bread", "meatball", "rice ball",
    "pasta", "neapolitan", "sicilian", "baked goods", "cake", "small plates", "bar fare", "pub fare", "noodles",
    "dessert", "pie", "tapas", "mezze", "bbq", "grill", "kebab", "sandwich", "salad", "vegan", "seafood",
)
# Real but broad cuisines: rank below the fans' specific dishes.
BROAD_CUISINES = {"american", "new american", "european", "international", "contemporary", "fusion", "modern",
                  "continental", "western", "global", "eclectic"}
# Too generic to build a concept on.
GENERIC_TASTES = {
    "classic", "food", "full bar", "spirits", "soft drinks", "snacks", "bar food", "restaurant", "bar", "place",
    "hard liquor", "alcohol", "specialty non alcoholic", "drinks", "beverages", "coffee and tea", "quick bite",
}
DRINK_WORDS = (
    "coffee", "tea", "matcha", "wine", "beer", "ale", "ipa", "cocktail", "mezcal", "tequila", "spritz",
    "kombucha", "soda", "juice", "horchata", "aguas frescas", "agua fresca", "energy drink", "whisk", "gin",
    "rum", "sake", "cider", "boba", "lemonade", "smoothie", "espresso", "highball", "lager", "soju", "vermouth",
    "aperitivo", "shochu", "bubble tea", "cola",
)
ALCOHOL_WORDS = (
    "beer", "lager", "ale", "ipa", "stout", "wine", "cocktail", "spritz", "gin", "vodka", "rum", "tequila",
    "mezcal", "whisky", "whiskey", "bourbon", "sake", "soju", "shochu", "negroni", "margarita",
    "prosecco", "champagne", "cava", "cider", "martini", "highball", "vermouth", "aperitivo", "liqueur",
    "spirits", "liquor", "michelada", "pisco", "cachaça", "caipirinha", "baijiu", "brandy", "cognac",
)
NA_MARKERS = (
    "zero-proof", "zero proof", "non-alcoholic", "non alcoholic", "alcohol-free", "alcohol free", "mocktail",
    "0%", "no-alcohol", "virgin", "sin alcohol", "soft drink", "temperance", "0.0", "ginger beer", "root beer",
    "ginger ale", "birch beer",
)
DIETARY_SYNONYMS = {
    "vegan": ("vegan", "plant-based"),
    "vegetarian": ("vegetarian", "veggie", "vegan", "meat-free"),
    "halal": ("halal",),
    "kosher": ("kosher",),
    "gluten-free": ("gluten-free", "gluten free", "coeliac", "celiac"),
    "dairy-free": ("dairy-free", "dairy free", "vegan"),
    "nut-free": ("nut-free", "nut free"),
}


# ---- small helpers ---------------------------------------------------------

def clean(name: str) -> str:
    return (name or "").removesuffix(SUFFIX).strip()


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean(text).lower()).strip()


def _has_word(text: str, words: tuple[str, ...], whole: bool = False) -> bool:
    """Prefix match by default ("taco" finds "tacos"); ``whole`` also needs a word end, so "gin" skips "ginger"."""
    low = f" {text.lower()} "
    end = r"(?:s|es)?(?![a-z])" if whole else ""
    return any(re.search(r"(?<![a-z])" + re.escape(w) + end, low) for w in words)


def is_alcohol_word(text: str) -> bool:
    return _has_word(text, ALCOHOL_WORDS, whole=True)


def is_food(text: str) -> bool:
    return _has_word(text, FOOD_WORDS)


def is_drink(text: str) -> bool:
    return _has_word(text, DRINK_WORDS)


def is_alcoholic(item: Item) -> bool:
    text = f"{item.title} {item.detail}".lower()
    if any(m in text for m in NA_MARKERS):
        return False
    return is_alcohol_word(text)


def matches(value: str, title: str) -> bool:
    a, b = norm(value), norm(title)
    if not a or not b:
        return False
    return a == b or (len(a) > 3 and a in b) or (len(b) > 3 and b in a)


def dietary_ok(diet: str, food: list[Item]) -> bool:
    words = DIETARY_SYNONYMS.get(diet.lower().strip(), (diet.lower().strip(),))
    return any(any(w in f"{i.title} {i.detail}".lower() for w in words) for i in food)


def pool_index(pools: dict) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for key in ("artists", "brands", "eats", "bars"):
        for e in pools.get(key, []):
            idx[e["id"]] = {**e, "pool": key}
    for t in pools.get("tags", []):
        idx[t["id"]] = {**t, "pool": "tags"}
    return idx


def _aff(x: Any) -> str:
    return f"{x:.2f}" if isinstance(x, (int, float)) else "n/a"


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def empty_lessons() -> dict:
    return {"declined": [], "sold_well": [], "flopped": [], "past_partners": []}


# ---- evidence-derived building blocks ----------------------------------------

def _blocked(label: str, lessons: dict) -> bool:
    return norm(label) in GENERIC_TASTES or any(matches(f, label) for f in lessons.get("flopped", []))


def food_candidates(pools: dict, lessons: dict) -> list[dict]:
    """Food signals ranked by summed affinity: dish and cuisine taste tags, plus restaurants' cuisine tags."""
    found: dict[str, dict] = {}

    def add(label: str, score: float, ev: str, place: str = "") -> None:
        label = clean(label)
        key = norm(label)
        if not key or _blocked(label, lessons):
            return
        c = found.setdefault(key, {"label": label, "score": 0.0, "evidence": [], "place": ""})
        c["score"] += score
        if ev not in c["evidence"]:
            c["evidence"].append(ev)
        c["place"] = c["place"] or place

    for t in pools.get("tags", []):
        kind = t.get("kind")
        if kind == "music":
            continue
        if kind == "dish" or ((kind in (None, "place")) and is_food(t["name"]) and not is_drink(t["name"])):
            add(t["name"], t.get("affinity") or 0.5, t["id"])
    for e in pools.get("eats", []):
        for t in e.get("tags", []):
            tid, ttype = t.get("id", ""), t.get("type", "")
            if any(k in ttype for k in ("cuisine", "menu_highlight", "specialty_dish")) or ":restaurant:" in tid:
                if not is_drink(t.get("name", "")):
                    add(t.get("name", ""), (e.get("affinity") or 0.5) * 0.15, e["id"], e["name"])
    for c in found.values():
        if norm(c["label"]) in BROAD_CUISINES:
            c["score"] *= 0.3
    return sorted(found.values(), key=lambda c: -c["score"])


def drink_candidates(pools: dict, lessons: dict) -> list[dict]:
    """Drink signals: drink-related taste tags, plus what the fans' favourite bars are known for pouring."""
    found: dict[str, dict] = {}

    def add(label: str, score: float, ev: str) -> None:
        label = re.sub(r"\s+(shop|bar|house|lounge|room)$", "", clean(label), flags=re.IGNORECASE)
        key = norm(label)
        if not key or _blocked(label, lessons):
            return
        c = found.setdefault(key, {"label": label, "score": 0.0, "affinity": score, "evidence": []})
        c["score"] += score
        if ev not in c["evidence"]:
            c["evidence"].append(ev)

    for t in pools.get("tags", []):
        if t.get("kind") != "music" and is_drink(t["name"]):
            add(t["name"], t.get("affinity") or 0.5, t["id"])
    for b in pools.get("bars", []):
        for t in b.get("tags", []):
            if any(k in t.get("type", "") for k in ("beverage_offering", "offerings")) and is_drink(t.get("name", "")):
                add(t["name"], (b.get("affinity") or 0.5) * 0.5, b["id"])
    return sorted(found.values(), key=lambda c: -c["score"])


def music_tags(pools: dict) -> list[str]:
    tags = [t for t in pools.get("tags", []) if t.get("kind") == "music"]
    tags.sort(key=lambda t: 0 if "genre" in t.get("type", "") else 1)  # "dream pop" before "Melodious"
    if not tags:  # older pools or fixtures without kinds
        tags = [t for t in pools.get("tags", []) if not is_food(t["name"]) and not is_drink(t["name"])]
    return [clean(t["name"]).lower() for t in tags]


def young_skew(demo: dict) -> float:
    vals = []
    for bucket, score in (demo or {}).get("age", {}).items():
        m = re.search(r"\d+", bucket)
        if m and int(m.group()) <= 29:
            vals.append(score)
    return sum(vals) / len(vals) if vals else 0.0


def na_share(policy: Policy, demo: dict) -> int:
    if policy.no_alcohol:
        return 100
    bump = round(max(0.0, young_skew(demo)) * 40)
    return max(15, min(45, 25 + bump))


FOOD_FORMATS = (
    "{c} street-food counter",
    "{c} shareable plates",
    "Late-night {lc} bites",
    "{c} grab-and-go box",
)


def make_food(pools: dict, policy: Policy, lessons: dict, n: int = 4) -> list[Item]:
    items: list[Item] = []
    cands = food_candidates(pools, lessons)
    for i, c in enumerate(cands[:n]):
        fmt = FOOD_FORMATS[i % len(FOOD_FORMATS)]
        title = fmt.format(c=c["label"][:1].upper() + c["label"][1:], lc=c["label"].lower())
        detail = f"Qloo ties this audience to {c['label'].lower()}"
        if c["place"]:
            detail += f"; fans also rate {c['place']} in town"
        items.append(Item(title=title, detail=detail + ".", evidence=c["evidence"][:3]))
    if not items:
        items.append(Item(title="Late-night street-food counter",
                          detail="Qloo returned no food signal for this audience, so this is a neutral default."))
    add_dietary(items, policy, cands)
    return items


def add_dietary(items: list[Item], policy: Policy, cands: list[dict]) -> list[str]:
    fixes = []
    for diet in policy.dietary:
        diet = diet.strip()
        if not diet or dietary_ok(diet, items):
            continue
        base = cands[0] if cands else None
        label = base["label"].lower() if base else "street-food"
        items.append(Item(
            title=f"{diet[:1].upper() + diet[1:]} {label} plate",
            detail=f"The venue requires a {diet} option; built on the strongest food signal in the data.",
            evidence=base["evidence"][:2] if base else [],
        ))
        fixes.append(f"Added a {diet} food option (venue rule).")
    return fixes


def make_drinks(pools: dict, policy: Policy, lessons: dict) -> list[Item]:
    cands = drink_candidates(pools, lessons)
    soft = [c for c in cands if not is_alcohol_word(c["label"])]
    items: list[Item] = []
    if soft:
        items.append(Item(title=f"Zero-proof {soft[0]['label'].lower()} signature",
                          detail="Non-alcoholic signature drink, priced and garnished like the headline cocktail.",
                          evidence=soft[0]["evidence"][:3]))
    else:
        items.append(Item(title="Zero-proof house signature",
                          detail="Non-alcoholic signature drink, priced and garnished like the headline cocktail."
                                 + (f" Built to sit beside the {cands[0]['label'].lower()} these fans' bars pour."
                                    if cands else ""),
                          evidence=cands[0]["evidence"][:2] if cands else []))
    used = {norm(soft[0]["label"])} if soft else set()
    for c in cands:
        if len(items) >= 3:
            break
        if norm(c["label"]) in used:
            continue
        used.add(norm(c["label"]))
        label = c["label"].lower()
        cap = c["label"][:1].upper() + c["label"][1:]
        if policy.no_alcohol:
            if is_alcohol_word(label):
                title = f"Zero-proof take on {label}"
            else:
                title = f"Zero-proof {label} cooler"
        elif is_alcohol_word(label):
            title = f"{cap} list picked for this crowd"
        else:
            title = f"{cap} highball"
        items.append(Item(title=title, detail=f"Qloo ties these fans to {label}.", evidence=c["evidence"][:3]))
    bars = [b for b in pools.get("bars", []) if not any(matches(d, b["name"]) for d in lessons.get("declined", []))]
    if bars:
        b = bars[0]
        kind = "zero-proof guest menu" if policy.no_alcohol else "guest menu"
        items.append(Item(title=f"A {kind} inspired by {b['name']}",
                          detail=f"Fans' top-rated bar nearby (affinity {_aff(b.get('affinity'))}).",
                          evidence=[b["id"]]))
    return [i for i in items if not any(matches(f, i.title) for f in lessons.get("flopped", []))]


def _entity_items(entities: list[dict], lessons: dict, limit: int, detail_fn, skip_declined: bool) -> list[Item]:
    out = []
    for e in entities:
        if any(matches(f, e["name"]) for f in lessons.get("flopped", [])):
            continue
        if skip_declined and any(matches(d, e["name"]) for d in lessons.get("declined", [])):
            continue
        out.append(Item(title=e["name"], detail=detail_fn(e), evidence=[e["id"]]))
        if len(out) >= limit:
            break
    return out


def make_partners(pools: dict, policy: Policy, lessons: dict, exclude: set[str] | None = None) -> list[Item]:
    exclude = exclude or set()
    bars, eats = list(pools.get("bars", [])), list(pools.get("eats", []))
    merged: list[dict] = []
    while bars or eats:  # interleave so the night gets a bar and a late kitchen
        if bars:
            merged.append(bars.pop(0))
        if eats:
            merged.append(eats.pop(0))
    seen: set[str] = set()
    merged = [e for e in merged if e["id"] not in exclude and not (e["id"] in seen or seen.add(e["id"]))]

    def detail(e: dict) -> str:
        kind = "bar" if e in pools.get("bars", []) else "restaurant"
        where = f" at {e['address']}" if e.get("address") else ""
        return f"After-show {kind}{where}; fans' affinity {_aff(e.get('affinity'))}."

    return _entity_items(merged, lessons, policy.max_partners, detail, skip_declined=True)


def promo_bullets(pools: dict, city: str, n: int = 4) -> list[str]:
    places = [e for e in pools.get("eats", []) + pools.get("bars", []) if e.get("lat") is not None]
    points = sorted(pools.get("heatmap", []), key=lambda p: -(p.get("affinity") or 0))
    if len(places) >= 3:  # a city query can return cells across the whole region; keep the city's
        lats, lons = sorted(e["lat"] for e in places), sorted(e["lon"] for e in places)
        centre = (lats[len(lats) // 2], lons[len(lons) // 2])
        points = [p for p in points if _haversine_km((p["lat"], p["lon"]), centre) <= 30]
    out, seen, named = [], set(), set()
    for p in points:
        cell = (round(p["lat"], 2), round(p["lon"], 2))
        if cell in seen:
            continue
        seen.add(cell)
        near = min(places, key=lambda e: _haversine_km((p["lat"], p["lon"]), (e["lat"], e["lon"]))) if places else None
        km = _haversine_km((p["lat"], p["lon"]), (near["lat"], near["lon"])) if near else 99.0
        if near and km <= 3:
            if near["id"] in named:  # one bullet per landmark; the next hotspot must be somewhere else
                continue
            named.add(near["id"])
            out.append(f"Fan hotspot near {near['name']} (affinity {_aff(p.get('affinity'))}, {km:.1f} km away): "
                       f"posters, flyers and geo-targeted ads here.")
        else:
            out.append(f"Fan hotspot around {p['lat']:.3f}, {p['lon']:.3f} (affinity {_aff(p.get('affinity'))}): "
                       f"geo-targeted ads here.")
        if len(out) >= n:
            break
    if not out:
        out.append(f"Qloo returned no heatmap for {city}, so promote through the artist's own channels.")
    return out


def audience_read(pools: dict) -> str:
    demo = pools.get("demographics") or {}
    parts = []
    age = demo.get("age") or {}
    if age:
        bucket, score = max(age.items(), key=lambda kv: kv[1])
        if score > 0.05:
            parts.append("skews " + bucket.replace("_", " ").replace("and younger", "and under"))
    g = demo.get("gender") or {}
    if g:
        f, m = g.get("female", 0), g.get("male", 0)
        if abs(f - m) > 0.2:
            parts.append("leans female" if f > m else "leans male")
    food = food_candidates(pools, empty_lessons())
    tags = music_tags(pools)[:2] + ([food[0]["label"].lower()] if food else [])
    first = ("This crowd " + " and ".join(parts) + ".") if parts else "Qloo shows no strong age or gender skew."
    second = f" Their tastes run to {', '.join(tags[:-1])} and {tags[-1]}." if len(tags) > 1 else ""
    return first + second


def run_of_show(ctx: dict, plan_parts: dict) -> list[str]:
    dj = [clean(i.title) for i in plan_parts["dj_set"]]
    drinks, partners, brands = plan_parts["drinks"], plan_parts["after_show"], plan_parts["brands"]
    support = ctx.get("support") or []
    steps = [
        "Doors: DJ warm-up from " + (", ".join(dj[:2]) if dj else "the house selector")
        + (f"; {drinks[0].title.lower()} is front and centre at the bar" if drinks else "") + ".",
        (f"Support: {', '.join(support)}. Food counters open." if support
         else "Early evening: food counters open while the room fills."),
        "Changeover: DJ plays " + (", ".join(dj[2:4]) if len(dj) > 2 else "more from the warm-up crates")
        + (f"; {clean(brands[0].title)} moment on the screens" if brands else "") + ".",
        f"Headliner: {ctx.get('headliner', 'the headliner')}.",
        "Exit: DJ closes with " + (dj[4] if len(dj) > 4 else "the encore's mood")
        + (f"; staff hand out walk-over cards for {clean(partners[0].title)}" if partners else "") + ".",
    ]
    if partners:
        steps.append("After-show: " + ", ".join(clean(p.title) for p in partners) + ".")
    return steps


# ---- composers ---------------------------------------------------------------

def deterministic_compose(ctx: dict, policy: Policy, pools: dict, lessons: dict) -> Plan:
    food = make_food(pools, policy, lessons)
    drinks = make_drinks(pools, policy, lessons)
    dj = _entity_items(pools.get("artists", []), lessons, 8,
                       lambda e: f"Fans' affinity {_aff(e.get('affinity'))}.", skip_declined=False)
    brands = _entity_items(pools.get("brands", []), lessons, 3,
                           lambda e: f"Brand these fans over-index on (affinity {_aff(e.get('affinity'))}).",
                           skip_declined=True)
    partners = make_partners(pools, policy, lessons)
    music = music_tags(pools)
    fc = food_candidates(pools, lessons)
    if music and fc:
        headline = f"{music[0][:1].upper() + music[0][1:]} and {fc[0]['label'].lower()} for {ctx['headliner']} fans"
    elif music:
        headline = f"A {music[0]} night for {ctx['headliner']} fans"
    else:
        headline = f"{ctx['headliner']} in {ctx['city']}: the night around the show"

    parts = {"dj_set": dj, "drinks": drinks, "after_show": partners, "brands": brands}
    return Plan(
        headline=headline,
        audience_read=audience_read(pools),
        food=food,
        drinks=drinks,
        non_alcoholic_share_pct=na_share(policy, pools.get("demographics") or {}),
        dj_set=dj,
        brands=brands,
        after_show=partners,
        promo_focus=promo_bullets(pools, ctx["city"]),
        run_of_show=run_of_show(ctx, parts),
    )


def compact_pools(pools: dict, per_pool: int = 8, tags_per_kind: int = 8) -> tuple[dict, dict[str, str]]:
    """A small view of the evidence for the LLM, with short refs (A1, B2, …) standing in for Qloo ids.

    Returns (payload, ref_to_id). Short refs keep the prompt inside small token budgets and make
    invented ids easy to catch: any ref that isn't in the map is dropped by ``validate``.
    """
    refs: dict[str, str] = {}

    def ref(prefix: str, n: int, qid: str) -> str:
        r = f"{prefix}{n}"
        refs[r] = qid
        return r

    def ent(prefix: str, e: dict, n: int) -> dict:
        d = {"ref": ref(prefix, n, e["id"]), "name": e["name"]}
        if isinstance(e.get("affinity"), (int, float)):
            d["aff"] = round(e["affinity"], 2)
        if e.get("tags"):
            d["tags"] = [t["name"] for t in e["tags"][:3]]
        return d

    payload: dict[str, Any] = {}
    for key, prefix in (("artists", "A"), ("brands", "B"), ("eats", "R"), ("bars", "S")):
        payload[key] = [ent(prefix, e, i) for i, e in enumerate(pools.get(key, [])[:per_pool], 1)]
    tags, counts, n = [], {}, 0
    for t in pools.get("tags", []):
        kind = t.get("kind", "")
        if counts.get(kind, 0) >= tags_per_kind:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        n += 1
        tags.append({"ref": ref("T", n, t["id"]), "name": t["name"], "kind": kind,
                     "aff": round(t["affinity"], 2) if isinstance(t.get("affinity"), (int, float)) else None})
    payload["tags"] = tags
    demo = pools.get("demographics") or {}
    payload["demographics"] = {"age": demo.get("age", {}), "gender": demo.get("gender", {})}
    return payload, refs


REF_RE = re.compile(r"\s*[\(\[]?\b[ABRST]\d{1,3}\b(?:\s*(?:[+,&/]|and)\s*\b[ABRST]\d{1,3}\b)*[\)\]]?")
SPLIT_RE = re.compile(r"\s+[–—-]\s+|:\s+")


def strip_refs(text: str) -> str:
    """Remove prompt refs like 'T5 + T4' or '(A3)' that a model copied into prose."""
    text = REF_RE.sub("", text or "")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"[,;\s–—-]+$", "", text.strip())
    return re.sub(r"\s{2,}", " ", text).strip()


def _unalias(plan: Plan, refs: dict[str, str]) -> Plan:
    for section in ("food", "drinks", "dj_set", "brands", "after_show"):
        for item in getattr(plan, section):
            item.evidence = [refs.get(str(e).strip(), str(e)) for e in item.evidence]
            item.title, item.detail = plain(strip_refs(item.title)), plain(strip_refs(item.detail))
            if not item.detail and section in CREATIVE_SECTIONS:  # "Name – description" in the title
                parts = SPLIT_RE.split(item.title, maxsplit=1)
                if len(parts) == 2 and len(parts[0]) >= 3:
                    item.title, item.detail = parts[0].strip(), parts[1].strip()[:1].upper() + parts[1].strip()[1:]
    plan.headline, plan.audience_read = plain(strip_refs(plan.headline)), plain(strip_refs(plan.audience_read))
    plan.promo_focus = [plain(strip_refs(x)) for x in plan.promo_focus]
    plan.run_of_show = [plain(strip_times(strip_refs(x))) for x in plan.run_of_show]
    return plan


HYPE_WORDS = [(r"\bvibes\b", "mood"), (r"\bvibe\b", "feel"), (r"\bflair\b", "touch"), (r"\bsoir[ée]e\b", "night"),
              (r"\bcurated\b", "chosen"), (r"\bimmersive\s+", ""), (r"\belevated?\s+", ""), (r"\bwhimsical\b", "playful"),
              (r"\bethereal\b", "airy"), (r"\bkawaii\b", "cute")]


def plain(text: str) -> str:
    """Swap the marketing words a model slips in despite the prompt for plain ones."""
    text = (text or "").replace("\u2011", "-")  # models love non-breaking hyphens; they break search and wrapping
    for pat, rep in HYPE_WORDS:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


TIME_RE = re.compile(r"^\s*(?:\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?|\d{1,2}\s*[ap]\.?m\.?|\d{1,2}h\d{2})\s*(?:[–—:-]\s*)?",
                     re.IGNORECASE)


def strip_times(step: str) -> str:
    """'7:30 pm – Doors open' -> 'Doors open'. The venue sets its own times."""
    out = TIME_RE.sub("", step or "", count=1).strip()
    return (out[:1].upper() + out[1:]) if out else step


SYSTEM_PROMPT = """You are Convivium, an agent that plans the whole night around a concert for a venue team:
food, drinks, the DJ set between acts, brand moments, after-show partners, promotion and the run of show.

Hard rules:
- Use ONLY the evidence pools provided. Every item in dj_set, brands and after_show must use the exact entity
  name from the pool and cite its ref (like "A3") in `evidence` (dj_set from artists A*, brands from brands B*,
  after_show from eats R* or bars S*). Never invent artist, brand or venue names. Items without a valid ref
  are deleted.
- food and drinks are creative concepts. Ground them in tags (T*) and places, and cite the refs that inspired
  them. Tag kinds: dish = dishes the fans love, place = cuisines and kinds of places, music = genres and style.
- Respect every venue rule and apply the past lessons (never re-use declined partners or flopped items).
- Being generic (plain burgers, generic lager, "top 40 hits") counts as failure unless the evidence points there.
- audience_read is at most two sentences of interpretation grounded in the demographics and tags.
- Every item has a short `title` (a name, at most 6 words) and a one-sentence `detail` saying why it fits these
  fans. Refs go ONLY in `evidence`, never in title, detail or any other text.
- Use 6-10 dj_set items, 2-3 brands, 3-5 food items, 3-5 drinks (always include a non-alcoholic signature),
  2 promo_focus ideas (the agent adds Qloo heatmap hotspots itself), and 5-7 run_of_show steps.
- Support human creatives: write as suggestions for the venue's chefs, bartenders and DJs, not replacements.

Voice (this is an ops sheet for venue staff, not marketing copy):
- Plain and specific. Name food and drinks the way a menu would, not as a pitch. Each detail says which fan
  signal it comes from, in plain words.
- Keep place and brand names out of food and drink titles; credit them in the detail instead.
- Every dish and drink must come from THIS audience's signals and THIS city. Don't fall back on drinks or snacks
  you'd serve any crowd; if the evidence is thin, offer fewer items rather than generic ones.
- headline: "<Headliner> in <City>: " followed by the two or three strongest taste signals in plain words.
  No invented event titles.
- Never use these words: soirée, vibe(s), curated, elevate(d), immersive, experience, journey, magical,
  whimsical, ethereal, kawaii, unforgettable, sensory, harmonious, symphony.
- run_of_show: steps in show order using phases (Doors, Support, Changeover, Headliner, Encore, After-show).
  Never give clock times; the venue sets its own times.
"""


def _rules_text(policy: Policy, lessons: dict) -> str:
    rules = [
        "Alcohol-free venue: no alcoholic drinks at all, non_alcoholic_share_pct must be 100."
        if policy.no_alcohol else "Alcohol is allowed; pick a sensible non_alcoholic_share_pct (15-45).",
        f"After-show partners: at most {policy.max_partners}.",
    ]
    if policy.dietary:
        rules.append("Food must include options that are: " + ", ".join(policy.dietary) + " (say so in the title).")
    if policy.notes:
        rules.append("Venue notes: " + policy.notes)
    if lessons.get("declined"):
        rules.append("Partners that declined before (never include): " + "; ".join(lessons["declined"]))
    if lessons.get("flopped"):
        rules.append("Items that flopped before (do not repeat): " + "; ".join(lessons["flopped"]))
    if lessons.get("sold_well"):
        rules.append("Items that sold well before (build on these): " + "; ".join(lessons["sold_well"]))
    if lessons.get("past_partners"):
        rules.append("Past partners (fine to keep if still in the pools): " + "; ".join(lessons["past_partners"][:8]))
    return "\n".join("- " + r for r in rules)


async def llm_compose(llm, ctx: dict, policy: Policy, pools: dict, lessons: dict, memory_text: list[str],
                      feedback: list[str]) -> Plan:
    show = {k: ctx.get(k) for k in ("headliner", "support", "city", "venue_name", "date") if ctx.get(k)}
    compact, refs = compact_pools(pools)
    msg = (
        f"The show:\n{json.dumps(show, ensure_ascii=False)}\n\n"
        f"Venue rules and lessons:\n{_rules_text(policy, lessons)}\n\n"
        + (f"Venue memory:\n- " + "\n- ".join(memory_text[:6]) + "\n\n" if memory_text else "")
        + f"Evidence pools from Qloo (compact; cite refs):\n{json.dumps(compact, ensure_ascii=False)}\n"
    )
    if feedback:
        msg += "\nYour previous draft broke these rules. Fix every one:\n- " + "\n- ".join(feedback)
    structured = llm.with_structured_output(Plan)
    plan = await structured.ainvoke([("system", SYSTEM_PROMPT), ("human", msg)])
    return _unalias(plan, refs)


GENERIC_BASELINE = {
    "food": [("Classic cheeseburgers", ""), ("Loaded fries", ""), ("Hot dogs", ""), ("Nachos", "")],
    "drinks": [("House lager", ""), ("Rum and cola", ""), ("Bottled water", ""), ("Soft drinks", "")],
    "dj": ["Current chart hits", "Crowd-pleasing classics", "Upbeat pop", "Party anthems", "Popular dance tracks"],
    "brands": ["A major soft-drink sponsor", "A national beer brand"],
    "after": ["The nearest late-opening bar", "A 24-hour diner"],
}


async def baseline_compose(llm, ctx: dict, policy: Policy) -> tuple[Plan, str]:
    """Same task and schema, no Qloo data. Everything here is a guess."""
    if llm is not None:
        try:
            show = {k: ctx.get(k) for k in ("headliner", "support", "city", "venue_name") if ctx.get(k)}
            msg = (f"Plan the night for this show. You have NO audience data, no taste data and no list of local "
                   f"places; just do your best.\nShow: {json.dumps(show, ensure_ascii=False)}\n"
                   f"Venue rules:\n{_rules_text(policy, empty_lessons())}\nLeave every evidence list empty.")
            plan = await llm.with_structured_output(Plan).ainvoke([
                ("system", "You plan concert nights for venues: food, drinks, DJ set, brands, after-show partners, "
                           "promo and run of show."), ("human", msg)])
            for section in ("food", "drinks", "dj_set", "brands", "after_show"):
                for item in getattr(plan, section):
                    item.evidence = []
            return plan, "llm-without-qloo"
        except Exception:
            pass
    g = GENERIC_BASELINE
    drinks = [Item(title=t, detail=d) for t, d in g["drinks"]]
    if policy.no_alcohol:
        drinks = [Item(title=t) for t in ("Soft drinks", "Bottled water", "Lemonade", "Iced tea")]
    food = [Item(title=t, detail=d) for t, d in g["food"]]
    add_dietary(food, policy, [])
    plan = Plan(
        headline=f"{ctx['headliner']} live in {ctx['city']}",
        audience_read="Concert-goers who like live music.",
        food=food,
        drinks=drinks,
        non_alcoholic_share_pct=100 if policy.no_alcohol else 20,
        dj_set=[Item(title=t) for t in g["dj"]],
        brands=[Item(title=t) for t in g["brands"]],
        after_show=[Item(title=t) for t in g["after"]][: policy.max_partners],
        promo_focus=["Social media posts", "Posters around the city centre"],
        run_of_show=["Doors open", "Support act", "Changeover music", f"{ctx['headliner']}", "Doors close"],
    )
    return plan, "generic-template"


# ---- guards ------------------------------------------------------------------

def validate(plan: Plan, pools: dict) -> tuple[Plan, list[dict]]:
    """Drop data items without valid evidence; fix names that don't match the cited entity."""
    idx = pool_index(pools)
    dropped: list[dict] = []
    plan = plan.model_copy(deep=True)
    for section, allowed in SECTION_POOLS.items():
        kept = []
        for item in getattr(plan, section):
            valid = [i for i in item.evidence if i in idx and idx[i]["pool"] in allowed]
            if not valid:
                dropped.append({"section": section, "title": item.title,
                                "reason": "no evidence id from the " + " or ".join(allowed) + " pool"})
                continue
            names = [idx[i]["name"] for i in valid]
            if not any(norm(n) == norm(item.title) for n in names):
                if re.match(r"^\s*[–—:-]", item.title):  # the model wrote a ref as the name; it was stripped
                    if not item.detail:
                        item.detail = item.title.strip(" –—:-")
                elif norm(item.title).startswith(norm(names[0])):  # "ANRI – city pop warm-up": keep the note
                    extra = SPLIT_RE.split(item.title, maxsplit=1)
                    if len(extra) == 2 and not item.detail:
                        item.detail = extra[1].strip()
                else:
                    dropped.append({"section": section, "title": item.title,
                                    "reason": f"renamed to the cited Qloo entity '{names[0]}'"})
                item.title = names[0]
            item.evidence = valid
            kept.append(item)
        # de-duplicate by entity
        seen, uniq = set(), []
        for item in kept:
            if item.evidence[0] in seen:
                continue
            seen.add(item.evidence[0])
            uniq.append(item)
        setattr(plan, section, uniq)
    for section in CREATIVE_SECTIONS:
        for item in getattr(plan, section):
            unknown = [i for i in item.evidence if i not in idx]
            if unknown:
                dropped.append({"section": section, "title": item.title,
                                "reason": f"kept, but removed {len(unknown)} unknown evidence id(s)"})
                item.evidence = [i for i in item.evidence if i in idx]
            loose = [i for i in item.evidence if not explains(idx[i], item, section)]
            if loose:
                names = ", ".join(clean(idx[i]["name"]) for i in loose)
                dropped.append({"section": section, "title": item.title,
                                "reason": f"kept, but removed evidence that doesn't explain it ({names})"})
                item.evidence = [i for i in item.evidence if i not in loose]
    return plan, dropped


EVIDENCE_STOPWORDS = {"shop", "bar", "bars", "restaurant", "house", "place", "the", "and", "with", "food", "drink",
                      "drinks", "classic", "style", "room", "lounge", "cafe", "new", "york", "city"}


def _tokens(text: str) -> set[str]:
    import unicodedata

    text = re.sub(r"[\u2010-\u2015/_-]", " ", text or "")  # "Häagen‑Dazs" (non-breaking hyphen) -> two words
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    out = set()
    for w in re.findall(r"[a-z0-9]+", text):
        if len(w) < 3 or w in EVIDENCE_STOPWORDS:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)  # balls -> ball, tacos -> taco
    return out


def explains(ent: dict, item: Item, section: str) -> bool:
    """True when the cited tag or place plausibly explains a food or drink item.

    A tag must share a word with the item and be the right kind (a drink signal for drinks, a dish or cuisine
    signal for food). A place must be named in the item or share a word through one of its Qloo tags.
    """
    text = _tokens(f"{item.title} {item.detail}")
    if ent.get("pool") == "tags":
        if ent.get("kind") == "music":
            return False
        name = ent.get("name", "")
        right_kind = is_drink(name) if section == "drinks" else not (is_drink(name) and not is_food(name))
        return right_kind and bool(_tokens(name) & text)
    if ent.get("pool") == "brands":
        return bool(_tokens(ent.get("name", "")) & text)
    if ent.get("pool") in ("eats", "bars"):
        if _tokens(ent.get("name", "")) & text:
            return True
        return any(_tokens(t.get("name", "")) & text for t in ent.get("tags", []))
    return False


def strip_place_names(plan: Plan, pools: dict) -> Plan:
    """A menu item isn't a restaurant: "Lucky's Beef Pie" becomes "Beef Pie". The place stays in the evidence."""
    names = set()
    for e in pools.get("eats", []) + pools.get("bars", []):
        n = clean(e["name"])
        names |= {n, re.sub(r"^the\s+", "", n, flags=re.IGNORECASE)}
    for item in plan.food + plan.drinks:
        for n in sorted(names, key=len, reverse=True):
            m = re.match(re.escape(n) + r"(?:'s)?[\s:–—-]+(.{3,})$", item.title, re.IGNORECASE)
            if m:
                rest = m.group(1).strip()
                item.title = rest[0].upper() + rest[1:]
                break
    return plan


def ground_promo(plan: Plan, pools: dict, city: str) -> Plan:
    """Lead promo with Qloo heatmap hotspots; keep at most two of the composer's own ideas after them."""
    hotspots = promo_bullets(pools, city)
    # The composer's own ideas are inference; drop any that claim to be heatmap data.
    extra = [p for p in plan.promo_focus
             if p and p not in hotspots and not re.search(r"heat\s*map|hotspot", p, re.IGNORECASE)]
    plan.promo_focus = hotspots + extra[:2]
    return plan


def check_policy(plan: Plan, policy: Policy, lessons: dict) -> list[str]:
    v: list[str] = []
    if policy.no_alcohol:
        for d in plan.drinks:
            if is_alcoholic(d):
                v.append(f"Alcohol-free venue: '{d.title}' reads as an alcoholic drink.")
        if plan.non_alcoholic_share_pct != 100:
            v.append(f"Alcohol-free venue: non_alcoholic_share_pct must be 100, not {plan.non_alcoholic_share_pct}.")
    for diet in policy.dietary:
        if diet.strip() and not dietary_ok(diet, plan.food):
            v.append(f"Food must include a {diet} option.")
    if len(plan.after_show) > policy.max_partners:
        v.append(f"Too many after-show partners: {len(plan.after_show)} (max {policy.max_partners}).")
    for section in ("after_show", "brands"):
        for item in getattr(plan, section):
            if any(matches(d, item.title) for d in lessons.get("declined", [])):
                v.append(f"'{item.title}' declined to partner before; remove it.")
    for section in ("food", "drinks", "dj_set", "brands", "after_show"):
        for item in getattr(plan, section):
            if any(matches(f, item.title) for f in lessons.get("flopped", [])):
                v.append(f"'{item.title}' flopped before; replace it.")
    return v


def enforce_policy(plan: Plan, policy: Policy, lessons: dict, pools: dict, ctx: dict) -> tuple[Plan, list[str]]:
    """Deterministically fix anything the critic loop didn't resolve."""
    plan = plan.model_copy(deep=True)
    fixes: list[str] = []
    declined, flopped = lessons.get("declined", []), lessons.get("flopped", [])

    for section in ("food", "drinks", "dj_set", "brands", "after_show"):
        keep = []
        for item in getattr(plan, section):
            if any(matches(f, item.title) for f in flopped):
                fixes.append(f"Removed '{item.title}' from {section} (flopped before).")
            elif section in ("after_show", "brands") and any(matches(d, item.title) for d in declined):
                fixes.append(f"Removed '{item.title}' from {section} (declined before).")
            else:
                keep.append(item)
        setattr(plan, section, keep)

    if policy.no_alcohol:
        before = len(plan.drinks)
        plan.drinks = [d for d in plan.drinks if not is_alcoholic(d)]
        if len(plan.drinks) < before:
            fixes.append(f"Removed {before - len(plan.drinks)} alcoholic drink(s) (alcohol-free venue).")
        if plan.non_alcoholic_share_pct != 100:
            fixes.append("Set the non-alcoholic share to 100% (alcohol-free venue).")
            plan.non_alcoholic_share_pct = 100

    if len(plan.drinks) < 2:
        have = {norm(d.title) for d in plan.drinks}
        for d in make_drinks(pools, policy, lessons):
            if norm(d.title) not in have:
                plan.drinks.append(d)
                fixes.append(f"Added drink '{d.title}' to refill the drinks list.")
            if len(plan.drinks) >= 3:
                break

    if len(plan.food) < 2:
        have = {norm(f.title) for f in plan.food}
        for f in make_food(pools, Policy(), lessons):
            if norm(f.title) not in have:
                plan.food.append(f)
                fixes.append(f"Added food concept '{f.title}' to replace removed items.")
            if len(plan.food) >= 3:
                break

    fixes += add_dietary(plan.food, policy, food_candidates(pools, lessons))

    if len(plan.after_show) > policy.max_partners:
        fixes.append(f"Cut after-show partners from {len(plan.after_show)} to {policy.max_partners} (venue cap).")
        plan.after_show = plan.after_show[: policy.max_partners]
    elif len(plan.after_show) < policy.max_partners:
        used = {i.evidence[0] for i in plan.after_show if i.evidence}
        for p in make_partners(pools, policy, lessons, exclude=used):
            if len(plan.after_show) >= policy.max_partners:
                break
            plan.after_show.append(p)
            fixes.append(f"Added after-show partner '{p.title}' as the next best match.")

    if len(plan.dj_set) < 6:
        used = {i.evidence[0] for i in plan.dj_set if i.evidence}
        for e in pools.get("artists", []):
            if len(plan.dj_set) >= 8:
                break
            if e["id"] in used or any(matches(f, e["name"]) for f in flopped):
                continue
            plan.dj_set.append(Item(title=e["name"], detail=f"Fans' affinity {_aff(e.get('affinity'))}.",
                                    evidence=[e["id"]]))
            fixes.append(f"Added '{e['name']}' to the DJ set from the Qloo artist pool.")

    if not plan.brands:
        for b in _entity_items(pools.get("brands", []), lessons, 2,
                               lambda e: f"Brand these fans over-index on (affinity {_aff(e.get('affinity'))}).",
                               skip_declined=True):
            plan.brands.append(b)
            fixes.append(f"Added brand '{b.title}' from the Qloo brand pool.")

    return plan, fixes
