"""Offline fixture responses shaped like Qloo's.

Used only when there is no QLOO_API_KEY or QLOO_FIXTURES=1 (offline dev and
CI). Every name ends in "(fixture)" and every id contains "fixture". All names
are invented; none are real brands or venues.

Responses are deterministic: they vary with the signal entities and the city,
so two different artists in the same city produce clearly different nights.
"""
from __future__ import annotations

import hashlib
import random
import re

SUFFIX = " (fixture)"

CITY_CENTRES = {
    "london": (51.5072, -0.1276),
    "new york": (40.7128, -74.0060),
    "mexico city": (19.4326, -99.1332),
    "ciudad de mexico": (19.4326, -99.1332),
    "tokyo": (35.6762, 139.6503),
    "berlin": (52.5200, 13.4050),
    "lagos": (6.5244, 3.3792),
    "paris": (48.8566, 2.3522),
    "sydney": (-33.8688, 151.2093),
    "sao paulo": (-23.5505, -46.6333),
    "toronto": (43.6532, -79.3832),
    "los angeles": (34.0522, -118.2437),
    "seoul": (37.5665, 126.9780),
    "mumbai": (19.0760, 72.8777),
}

# Taste "moods": each signal set lands on one, so contrasting artists get
# contrasting fixture nights. Every word here is generic and invented.
MOODS = [
    {
        "key": "soft",
        "artists": ["Velvet Hour", "Cloudline Trio", "Marigold Lane", "June Harbour", "Paper Lanterns",
                    "Sora Quartet", "Linden & Moss", "The Quiet Orchard", "Ada Winterfield", "Glasshouse Choir"],
        "music": ["jazz pop", "bossa nova", "chamber pop", "dream pop"],
        "dishes": ["Chocolate souffle", "Pastry", "Brunch", "Croissant"],
        "drinks": ["Matcha", "Natural wine", "Tea house"],
        "brands": ["Lumen Tea Co", "Fernleaf Bakery Goods", "Orchard Notebook", "Halcyon Audio"],
        "eats": ["Petal & Crumb", "Kettle House", "Morning Tide Cafe", "Little Ember Bistro", "Sakura Lane Kitchen"],
        "bars": ["The Listening Room", "Cellar Nine", "Moonglass Wine Bar", "Hush Lounge"],
        "cuisines": ["french bakery", "japanese cafe", "brunch"],
    },
    {
        "key": "loud",
        "artists": ["Riot Signal", "Concrete Saints", "Pitbox", "Black Ferry", "Static Youth",
                    "Gravel Mouth", "Hollow Engine", "Scrapyard Hymns", "Knuckle Choir", "Burnout Parade"],
        "music": ["hardcore", "metalcore", "punk"],
        "dishes": ["Smash burgers", "Hot wings", "Tacos", "Loaded fries"],
        "drinks": ["Craft beer", "Energy drinks", "Cola"],
        "brands": ["Voltcan Energy", "Gritline Skate Supply", "Ironjaw Apparel", "Riffhouse Strings"],
        "eats": ["Smash Shack", "Wing Republic", "Night Taco Window", "Grill Pit 24", "Burnt Ends Diner"],
        "bars": ["Amp Room Taproom", "The Mosh Pit Bar", "Loading Dock Brewery", "Fuse Box Tavern"],
        "cuisines": ["burgers", "tacos", "bbq"],
    },
    {
        "key": "club",
        "artists": ["Pulse Theory", "Neon Delta", "Saltwater Drums", "Midnight Office", "Low Sun Collective",
                    "Kino Bloom", "Rhythm Atlas", "Glass Floor", "Dawn Shift", "Monoform"],
        "music": ["house", "afrobeats", "electronic"],
        "dishes": ["Ramen", "Street food", "Jollof rice", "Bao"],
        "drinks": ["Mezcal", "Spritz", "Kombucha"],
        "brands": ["Basslane Headphones", "Kinetic Sneaker Co", "Spritzy Soda Works", "Afterglow Cosmetics"],
        "eats": ["Bao Window", "Ramen Lab", "Suya Corner", "Midnight Noodle Bar", "Jollof Junction"],
        "bars": ["Strobe Social", "Agave Room", "Rooftop 808", "Groove Cellar"],
        "cuisines": ["ramen", "west african", "street food"],
    },
    {
        "key": "roots",
        "artists": ["Rio Lento", "Cantina Dreams", "Sierra Bloom", "Los Faroles", "Ana del Monte",
                    "Brass Plaza", "Copper Valley", "Mariposa Strings", "Tierra Alta", "Banda Nocturna"],
        "music": ["regional mexican", "corridos", "folk"],
        "dishes": ["Street tacos", "Tamales", "Mole", "Elote"],
        "drinks": ["Horchata", "Aguas frescas", "Mezcal"],
        "brands": ["Sol Lindo Beverages", "Botas Rancho Co", "Maizal Snacks", "Plaza Records"],
        "eats": ["Taqueria Faro", "Casa Mole", "Tamal Corner", "Elote Stand 7", "Comal Kitchen"],
        "bars": ["Mezcaleria Luna", "Cantina 22", "Agua Fresca Bar", "Patio Norte"],
        "cuisines": ["mexican", "tacos", "street food"],
    },
]


def _seed(*parts: object) -> int:
    raw = "|".join(str(p) for p in parts)
    return int(hashlib.sha1(raw.encode()).hexdigest()[:12], 16)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


KNOWN_MOODS = {
    "soft": ("laufey", "hiromi", "nils-frahm", "natalia-lafourcade", "sampha"),
    "loud": ("turnstile", "bring-me-the-horizon", "militarie-gun"),
    "club": ("fred-again", "little-simz", "yoasobi", "joy-anonymous"),
    "roots": ("peso-pluma",),
}


def _mood(signal: str) -> dict:
    """Pick a mood from the first signal id; known demo artists get a fitting one."""
    first = signal.split(",")[0]
    for key, slugs in KNOWN_MOODS.items():
        if any(first.endswith("-" + s) for s in slugs):
            return next(m for m in MOODS if m["key"] == key)
    return MOODS[_seed("mood", signal) % len(MOODS)]


def city_centre(city: str) -> tuple[float, float]:
    key = re.sub(r"[^a-z ]", "", (city or "").lower()).strip()
    if key in CITY_CENTRES:
        return CITY_CENTRES[key]
    rng = random.Random(_seed("city", key))
    return (round(rng.uniform(-40, 55), 4), round(rng.uniform(-120, 140), 4))


def _entity(name: str, etype: str, rng: random.Random, affinity: float, extra: dict | None = None) -> dict:
    ent = {
        "entity_id": f"fixture-{etype.split(':')[-1]}-{_slug(name)}",
        "name": name + SUFFIX,
        "types": [etype],
        "popularity": round(rng.uniform(0.35, 0.95), 3),
        "query": {"affinity": round(affinity, 3)},
        "tags": [],
        "properties": {},
    }
    if extra:
        ent.update(extra)
    return ent


def _search(params: dict) -> dict:
    query = (params.get("query") or "").strip()
    if not query:
        return {"results": []}
    rng = random.Random(_seed("search", query.lower()))
    ent = _entity(query, "urn:entity:artist", rng, 1.0)
    ent["entity_id"] = f"fixture-artist-{_slug(query)}"
    ent["popularity"] = round(rng.uniform(0.80, 0.99), 3)
    near = _entity(query + " Tribute Band", "urn:entity:artist", rng, 1.0)
    near["popularity"] = 0.2
    return {"results": [ent, near]}


def _tags_lookup(params: dict) -> dict:
    term = (params.get("filter.query") or "").lower().strip()
    if term in {"restaurant", "bar"}:
        return {"results": {"tags": [{"id": f"urn:tag:category:place:{term}", "name": term.title() + SUFFIX,
                                      "type": "urn:tag:category:place"}]}}
    return {"results": {"tags": []}}


def _insights(params: dict) -> dict:
    ftype = params.get("filter.type", "")
    signal = params.get("signal.interests.entities", "")
    city = params.get("filter.location.query", "")
    take = int(params.get("take") or 10)
    mood = _mood(signal)
    rng = random.Random(_seed(ftype, signal, city, params.get("filter.tags", "")))

    def affinities(n: int) -> list[float]:
        vals = sorted((rng.uniform(0.55, 0.99) for _ in range(n)), reverse=True)
        return vals

    if ftype == "urn:entity:artist":
        names = mood["artists"][:take]
        return {"results": {"entities": [_entity(n, ftype, rng, a) for n, a in zip(names, affinities(len(names)))]}}

    if ftype == "urn:entity:brand":
        names = mood["brands"][:take]
        return {"results": {"entities": [_entity(n, ftype, rng, a) for n, a in zip(names, affinities(len(names)))]}}

    if ftype == "urn:entity:place":
        is_bar = params.get("filter.tags", "").endswith(":bar")
        names = mood["bars"] if is_bar else mood["eats"]
        lat0, lon0 = city_centre(city)
        out = []
        for i, (n, a) in enumerate(zip(names[:take], affinities(len(names)))):
            lat = lat0 + rng.uniform(-0.03, 0.03)
            lon = lon0 + rng.uniform(-0.04, 0.04)
            cuisine = mood["cuisines"][i % len(mood["cuisines"])]
            tags = [{"tag_id": "urn:tag:category:place:" + ("bar" if is_bar else "restaurant"),
                     "name": ("Bar" if is_bar else "Restaurant") + SUFFIX, "type": "urn:tag:category:place"}]
            if is_bar:
                drink = mood["drinks"][i % len(mood["drinks"])]
                tags.append({"tag_id": f"urn:tag:beverage_offering:qloo:fixture-{_slug(drink)}",
                             "name": drink + SUFFIX, "type": "urn:tag:beverage_offering:qloo"})
            else:
                tags.append({"tag_id": f"urn:tag:cuisine:qloo:fixture-{_slug(cuisine)}",
                             "name": cuisine.title() + SUFFIX, "type": "urn:tag:cuisine:qloo"})
            out.append(_entity(n, ftype, rng, a, {
                "entity_id": f"fixture-place-{_slug(city)}-{_slug(n)}",
                "tags": tags,
                "location": {"lat": round(lat, 5), "lon": round(lon, 5)},
                "properties": {"address": f"{10 + i * 7} Fixture Street, {city or 'Anytown'}{SUFFIX}",
                               "primary_genre": {"id": "urn:tag:genre:place:" + ("restaurant:bar" if is_bar
                                                                                  else "restaurant")},
                               "is_closed": False},
            }))
        return {"results": {"entities": out}}

    if ftype == "urn:tag":
        types = params.get("filter.tag.types", "")
        if "genre:music" in types:
            names, subtype = mood["music"], "urn:tag:genre:music"
        elif "specialty_dish" in types:
            names, subtype = mood["dishes"], "urn:tag:specialty_dish:place"
        else:
            names, subtype = mood["drinks"] + mood["cuisines"], "urn:tag:genre:place"
        out = []
        for name, a in zip(names[:take], affinities(len(names))):
            out.append({"tag_id": f"{subtype}:fixture-{_slug(name)}", "name": name + SUFFIX,
                        "subtype": subtype, "query": {"affinity": round(a, 3)}})
        return {"results": {"tags": out}}

    if ftype == "urn:demographics":
        young = {"soft": 0.25, "loud": 0.35, "club": 0.3, "roots": 0.1}[mood["key"]]
        female = {"soft": 0.3, "loud": -0.25, "club": 0.05, "roots": 0.0}[mood["key"]]
        return {"results": {"demographics": [{
            "entity_id": signal.split(",")[0],
            "query": {
                "age": {"24_and_younger": round(young, 2), "25_to_29": round(young / 2, 2), "30_to_34": 0.05,
                        "35_to_44": -0.1, "45_to_54": -0.2, "55_and_older": -0.35},
                "gender": {"female": round(female, 2), "male": round(-female, 2)},
            },
        }]}}

    if ftype == "urn:heatmap":
        lat0, lon0 = city_centre(city)
        hubs = [(lat0 + rng.uniform(-0.04, 0.04), lon0 + rng.uniform(-0.05, 0.05)) for _ in range(3)]
        out = []
        for i in range(min(take, 50)):
            hlat, hlon = hubs[i % 3]
            out.append({
                "location": {"latitude": round(hlat + rng.gauss(0, 0.008), 5),
                             "longitude": round(hlon + rng.gauss(0, 0.01), 5),
                             "geohash": f"fixture{i:03d}"},
                "query": {"affinity": round(rng.uniform(0.3, 1.0), 3),
                          "popularity": round(rng.uniform(0.1, 1.0), 3)},
            })
        return {"results": {"heatmap": out}}

    return {"results": {"entities": []}}


def respond(path: str, params: dict) -> dict:
    """Return a Qloo-shaped fixture response for ``path`` and ``params``."""
    if path == "/search":
        return _search(params)
    if path == "/v2/tags":
        return _tags_lookup(params)
    if path == "/v2/insights":
        return _insights(params)
    return {"results": []}
