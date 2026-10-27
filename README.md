# Convivium

**An autonomous agent that plans the whole night around a concert (food and drinks, the DJ set between acts, brand moments, after-show partners and promotion) from what that artist's fans actually love, anywhere in the world.** Built on the Qloo Taste AI API for the Qloo Agentic Hackathon.

**Live demo:** `<PUBLIC_URL>`, no sign-up.

**Try it in 30 seconds:** open the link, type any artist and city (try *Laufey* and then *Turnstile*, both in *New York*), and press **Plan the night**. You'll watch the agent work step by step, then get a full plan in which every artist, brand and venue links back to the Qloo response it came from.

## The problem and the buyer

Venue and promoter teams decide the food, drinks, DJ set and partners for a show mostly by guesswork: the same burgers and lager whether the headliner is a jazz-pop singer or a hardcore band. Fans spend more when the night fits them. Convivium is built for **venue and promoter ops teams**. It turns "who are this artist's fans?" into a run sheet the bar, kitchen, DJ and partnerships staff can act on, then it does the outreach itself.

## How it works

```
START → resolve ─┬─> gather → recall → plan ⟲ critic → finalize ─┬─> act → remember → END
                 └─> baseline (same task, no Qloo) ──────────────┘
```

A LangGraph `StateGraph` runs as a level-3 agent with a persistent observe → reason → act → learn loop. **There are no confirm buttons between steps.**

1. **Observe.** Every 15 minutes the agent reads each connected venue's iCal feed. A new or changed show (fingerprinted on title, date and lineup) starts a run. The parser splits headliner from support only on explicit words (`with`, `w/`, `feat.`, `supported by`, …), so *Florence + the Machine* and *Tyler, The Creator* survive intact.
2. **Reason.** `resolve` finds the lineup in Qloo. `gather` fans out the evidence calls in parallel. `recall` loads venue memory. `plan` writes the plan (LLM, or a deterministic composer when no LLM key is set). A **critic** checks it against the venue's rules and loops back to the LLM with the violations (up to 3 drafts). `finalize` then fixes anything left in code.
3. **Act.** The agent emails a pitch to each after-show partner and top brand ("your venue's fans love you"), emails the plan to the venue's ops address and posts it to the team webhook. Every action lands in the outbox with its **real** status: sent, queued, failed, skipped or drafted.
4. **Learn.** The venue reports what sold well, what flopped and which partners declined. The agent stores it (Mem0, or SQLite) and **automatically re-plans the venue's next 3 shows**: the declined partner disappears and the flop gets replaced.

The demo seeds 5 fictional venues on 4 continents (London, New York, Mexico City, Tokyo, Berlin) with sample calendars. Each city pairs contrasting artists, so the same city gets two very different nights. All 10 shows are planned on startup with no clicks.

## Qloo calls

Every call is listed in each plan's **Qloo receipts** panel with its purpose, parameters, result count, latency and cache status.

| Endpoint | Key parameters | Powers |
|---|---|---|
| `GET /search` | `query`, `types=urn:entity:artist`, `take=5` | Resolves the headliner and up to 3 support acts to Qloo ids (exact-name match preferred) |
| `GET /v2/tags` | `filter.query=restaurant\|bar`, `filter.tag.types=urn:tag:category:place` (falls back to an untyped search, then a known id) | Place-category tag ids for the venue searches |
| `GET /v2/insights` `filter.type=urn:entity:artist` | `signal.interests.entities`, `filter.exclude.entities`, `filter.popularity.max=<headliner popularity>`, `take=15` | **DJ set**: artists the fans love, no bigger than the headliner |
| `GET /v2/insights` `filter.type=urn:entity:brand` | `signal.interests.entities`, `take=10` | **Brand moments** and brand pitches |
| `GET /v2/insights` `filter.type=urn:entity:place` | `signal.interests.entities`, `filter.location.query=<city>`, `filter.tags=…:restaurant`, `take=12` | **After-show partners**, plus the cuisine signals behind the food concepts |
| same, with `filter.tags=…:bar` | | After-show bars, plus the bar-inspired guest menu |
| `GET /v2/insights` `filter.type=urn:tag` (3 typed calls) | `signal.interests.entities`, `filter.tag.types=urn:tag:specialty_dish:place` / `urn:tag:genre:place` / `urn:tag:genre:music,urn:tag:style:qloo` | **Taste analysis**: dishes, cuisines and music taste behind the food and drink concepts, the headline and the audience read. (Untyped tag calls return mostly media keywords, so every call is typed.) |
| `GET /v2/insights` `filter.type=urn:demographics` | `signal.interests.entities` | Age and gender skew, which tunes the non-alcoholic share and the audience read |
| `GET /v2/insights` `filter.type=urn:heatmap` | `signal.interests.entities`, `filter.location.query=<city>`, `take=50` | **Where the fans are**: the map, plus promo hotspots labelled with the nearest Qloo place |

That's about a dozen Qloo calls per plan (more with support acts), with a 6-hour cache, retry and backoff on 429, and a clear error on 401/403. One failed call never sinks a run; it shows up as an honest gap instead.

## Why it doesn't work without Qloo

Every plan has a **With Qloo | Without Qloo** toggle. "Without" is the same task, the same schema, the same venue rules and the same LLM, but no Qloo data, and it comes out as cheeseburgers, house lager and "current chart hits". "With" gives a jazz-pop night of matcha, pastry and a bossa-nova warm-up for Laufey fans, and a smash-burger, energy-drink, skate-brand night for Turnstile fans in the same city.

<!-- screenshot: Laufey vs Turnstile, both New York -->

## Guardrails

- **Grounded or removed.** Every artist, brand and venue the plan names must cite an id from the matching Qloo pool. The code drops items that don't, and renames items whose title doesn't match the entity they cite. The removals are shown in the plan.
- **Venue rules critic.** Rules like alcohol-free (100% non-alcoholic, no alcoholic drinks), required dietary options, a partner cap, and lessons from past nights (no declined partners, no flopped items) are checked in code. They go back to the LLM as feedback, and anything left over is fixed deterministically.
- **Honest labels.** Rose = data from Qloo, with its affinity. Amber = agent inference. Every plan shows how many of its items Qloo backs ("21 of 21" with Qloo, "0 of 7" without). Offline fixture data says so in a banner and in every name. Sample calendar events say "Not a real tour date."
- **Honest actions.** Nothing is reported as sent unless the provider accepted it. The public "Plan the night" form only drafts and never contacts anyone. It is rate-limited to 6 plans a minute per IP.
- **Privacy.** No personal data goes to Qloo, only artist ids and a city name. Partner contact emails never reach the browser.

## Run it locally

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add QLOO_API_KEY for live data; leave blank for offline fixtures
uvicorn app.main:app --reload   # http://localhost:8000
```

With no keys at all it runs on offline fixtures (clearly marked) with the deterministic composer, so you can try every feature. Add `GROQ_API_KEY` to switch on the LLM composer and critic loop (default model `openai/gpt-oss-120b` on Groq; `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` also work, and `LLM_MODEL` picks any `provider:model`), and `MEM0_API_KEY`, `RESEND_API_KEY` and `WEBHOOK_URL` for Mem0 memory, real email and a team channel. All variables are in [.env.example](.env.example).

**Day-1 API check:** `python scripts/qloo_probe.py` prints the raw JSON for every call above. `python scripts/qloo_probe.py --coverage` prints result counts for every demo artist and city.

**Tests** run offline (fixtures, a temporary database, no LLM):

```bash
pytest
```

They cover the graph end to end, the grounding and rules guards, the LLM critic loop (with a fake model), memory and re-planning, the lineup parser, calendar change detection, the API and its rate limit, and the Qloo client's retry, auth-error and cache behaviour.

## Deploy

The repo includes a `Dockerfile` and a Render blueprint (`render.yaml`: a paid tier so it never sleeps, plus a persistent disk for SQLite). Set `QLOO_API_KEY`, `GROQ_API_KEY` and `PUBLIC_URL`. After deploying, `/healthz` should report `"qloo_mode": "live"`.

Map tiles: CARTO's basemaps now need an API key, so by default the map uses OpenStreetMap tiles muted in CSS. Set `MAP_TILE_URL` to use another provider.

## Layout

`app/qloo.py` is the Qloo client (trace, cache, retries, normalisation). `app/planner.py` holds the plan schema, composers and guards. `app/graph.py` is the agent. `app/calendar_watch.py` is the observe loop. `app/actions.py` holds the actuators, `app/memory.py` the memory and `app/main.py` the API. `web/` is the dashboard (vanilla JS and Leaflet), `samples/` holds the demo calendars and `tests/` the tests.

## License

[MIT](LICENSE)
