/* Convivium dashboard. Vanilla JS, no build step.
   Rose chips = grounded in Qloo data; amber markers = agent inference. */
(() => {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const view = $("#view");
  const tip = $("#tip");
  const state = {
    status: null, venues: [], runId: null, run: null, mode: "with", pollTimer: null, map: null,
    lastKey: "", reported: null, idx: {}, page: null,
  };
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fixtureName = (s) => String(s || "").replace(/ \(fixture\)$/, "");
  const fmtAff = (a) => (typeof a === "number" ? a.toFixed(2) : "n/a");
  // Similar-artist affinities cluster tightly (0.902 to 0.913), so show the third decimal there.
  const fmtAff3 = (a) => (typeof a === "number" ? a.toFixed(3) : "n/a");
  const ARROW = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9.5M8.5 3.5 13 8l-4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  const img = (src, cls = "", alt = "") => (src ? `<img class="${cls}" src="${esc(src)}" alt="${esc(alt)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : "");
  const AGE = [["24_and_younger", "<25"], ["25_to_29", "25–29"], ["30_to_34", "30–34"], ["35_to_44", "35–44"], ["45_to_54", "45–54"], ["55_and_older", "55+"]];
  const AGE_LONG = { "24_and_younger": "under 25", "25_to_29": "25 to 29", "30_to_34": "30 to 34", "35_to_44": "35 to 44", "45_to_54": "45 to 54", "55_and_older": "55 and over" };

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    let body = null;
    try { body = await res.json(); } catch (_) { /* empty */ }
    if (!res.ok) {
      const detail = body && body.detail;
      const msg = Array.isArray(detail) ? detail.map((d) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ") : detail;
      throw new Error(msg || `The server returned ${res.status}.`);
    }
    return body;
  }

  function fmtDate(iso, long = false) {
    if (!iso) return "";
    // Show the venue's local calendar date, not the viewer's: use the date part as written.
    const d = new Date(iso.slice(0, 10) + "T12:00:00");
    if (isNaN(d)) return iso;
    return d.toLocaleDateString(undefined, long ? { weekday: "short", month: "short", day: "numeric", year: "numeric" } : { month: "short", day: "numeric" });
  }

  function destroyMap() { if (state.map) { state.map.remove(); state.map = null; } }

  /* ---------- status ---------- */
  async function loadStatus() {
    try {
      const s = await api("/api/status");
      state.status = s;
      const live = s.qloo_mode === "live";
      const chip = $("#qloo-chip");
      chip.className = "status-chip " + (live ? "live" : "fixture");
      chip.innerHTML = `<span class="dot"></span>${live ? "Live Qloo data" : "Offline fixtures"}`;
      $("#fixture-banner").hidden = live;
      $("#stack").innerHTML = [
        `Qloo: ${live ? "live" : "fixtures"}`, `Planner: ${s.llm}`, `Memory: ${s.memory}`,
        `Calendar check every ${Math.round(s.watch_interval_seconds / 60)} min`,
      ].map((t) => `<span class="pill">${esc(t)}</span>`).join("");
    } catch (_) {
      $("#qloo-chip").innerHTML = `<span class="dot"></span>Status unavailable`;
    }
  }
  window.addEventListener("scroll", () => $(".nav").classList.toggle("scrolled", window.scrollY > 8), { passive: true });

  /* ---------- evidence ---------- */
  function poolIndex(pools) {
    const idx = {};
    for (const key of ["artists", "brands", "eats", "bars"]) {
      for (const e of (pools && pools[key]) || []) idx[e.id] = { ...e, pool: key };
    }
    for (const t of (pools && pools.tags) || []) idx[t.id] = { ...t, pool: "tags" };
    return idx;
  }
  const entsOf = (item, idx) => (item.evidence || []).map((id) => idx[id]).filter(Boolean);

  function chip(ent, label, precise = false) {
    const name = label || ent.name;
    const aff = precise ? fmtAff3(ent.affinity) : fmtAff(ent.affinity);
    return `<button type="button" class="chip" data-eid="${esc(ent.id)}" aria-label="${esc(ent.name)}, Qloo affinity ${aff}">
      <span class="chip-label">${esc(name)}</span>${typeof ent.affinity === "number" ? `<span class="aff">${aff}</span>` : ""}</button>`;
  }
  const infer = (text = "inference") => `<span class="tag-infer">${esc(text)}</span>`;

  function evidenceHtml(item, idx, generic) {
    const ents = generic ? [] : entsOf(item, idx);
    return `<div class="chips">${ents.length ? ents.map((e) => chip(e)).join("") : infer()}</div>`;
  }

  const POOL_LABEL = { artists: "Artist", brands: "Brand", eats: "Restaurant", bars: "Bar", tags: "Taste tag" };

  function showTip(target) {
    const ent = state.idx && state.idx[target.dataset.eid];
    if (!ent) return;
    const tags = (ent.tags || []).map((t) => fixtureName(t.name)).filter(Boolean).slice(0, 5);
    tip.innerHTML = `${ent.image ? `<img class="tip-img" src="${esc(ent.image)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
      <strong>${esc(ent.name)}</strong>
      <div class="muted">${esc(POOL_LABEL[ent.pool] || ent.type)} from ${ent.source === "fixture" ? "offline fixtures" : "Qloo"}</div>
      <div>Affinity ${fmtAff3(ent.affinity)}${typeof ent.popularity === "number" ? `, popularity ${fmtAff(ent.popularity)}` : ""}</div>
      ${ent.address ? `<div class="muted">${esc(ent.address)}</div>` : ""}
      ${tags.length ? `<div class="muted">Tags: ${esc(tags.join(", "))}</div>` : ""}`;
    tip.hidden = false;
    const r = target.getBoundingClientRect();
    const w = tip.offsetWidth, h = tip.offsetHeight;
    const left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    let top = r.bottom + 8;
    if (top + h > window.innerHeight - 8) top = r.top - h - 8;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  const hideTip = () => { tip.hidden = true; };
  document.addEventListener("mouseover", (e) => { const c = e.target.closest(".chip[data-eid]"); if (c) showTip(c); });
  document.addEventListener("mouseout", (e) => { if (e.target.closest(".chip[data-eid]")) hideTip(); });
  document.addEventListener("focusin", (e) => { const c = e.target.closest(".chip[data-eid]"); if (c) showTip(c); else hideTip(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  window.addEventListener("scroll", hideTip, { passive: true });

  /* ---------- small visuals from real run data ---------- */
  function ageBars(demo) {
    const age = (demo && demo.age) || {};
    const rows = AGE.filter(([k]) => typeof age[k] === "number");
    if (!rows.length) return "";
    const top = rows.reduce((a, b) => (age[b[0]] > age[a[0]] ? b : a))[0];
    return `<div class="bars" role="img" aria-label="Audience age skew from Qloo: strongest for ${esc(AGE_LONG[top])}">${rows.map(([k, label]) => {
      const h = 6 + ((Math.max(-1, Math.min(1, age[k])) + 1) / 2) * 64;  // px; score runs -1..1
      return `<div class="bar ${k === top ? "top" : ""}"><span style="height:${h.toFixed(0)}px"></span><em>${label}</em></div>`;
    }).join("")}</div>`;
  }

  function topAge(demo) {
    const age = (demo && demo.age) || {};
    const keys = Object.keys(age);
    if (!keys.length) return null;
    return keys.reduce((a, b) => (age[b] > age[a] ? b : a));
  }

  function spark(values) {
    if (values.length < 2) return "";
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const pts = values.slice().reverse().map((v, i) => [(i / (values.length - 1)) * 220, 56 - ((v - lo) / span) * 46]);
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const last = pts[pts.length - 1];
    return `<svg class="spark" viewBox="0 0 230 64" aria-hidden="true">
      <path d="${d} L220 64 L0 64 Z" fill="rgba(92,196,135,0.12)"/>
      <path d="${d}" fill="none" stroke="#5CC487" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="${last[0]}" cy="${last[1]}" r="5" fill="#13211A" stroke="#5CC487" stroke-width="2.5"/></svg>`;
  }

  function groundedCount(plan, idx, generic) {
    let total = 0, grounded = 0;
    for (const k of ["food", "drinks", "dj_set", "brands", "after_show"]) {
      for (const i of plan[k] || []) {
        total += 1;
        if (!generic && entsOf(i, idx).length) grounded += 1;
      }
    }
    return { total, grounded };
  }

  /* ---------- home ---------- */
  function arcs() {
    const rings = [300, 420, 540, 660, 780].map((r) => `<circle cx="850" cy="620" r="${r}"/>`).join("");
    return `<svg class="arcs" viewBox="0 0 1700 1100" aria-hidden="true">${rings}
      <circle class="orb" cx="548" cy="350" r="6" fill="#7EA6F5"/><circle class="orb" cx="1168" cy="330" r="6" fill="#5CC487"/>
      <circle class="orb" cx="520" cy="520" r="6" fill="#F2B44E"/><circle class="orb" cx="1192" cy="540" r="6" fill="#A58BF0"/></svg>`;
  }

  function homeHtml() {
    return `
    <section class="hero">
      <div class="wrap hero-copy">
        <p class="eyebrow">An autonomous agent for venues and promoters</p>
        <h1 class="display">Every show night.<br>Planned around the fans.</h1>
        <p class="lede">Convivium reads Qloo's taste data for each headliner's fans, then plans the food, drinks, DJ set, partners and promotion, and sends it out on its own.</p>
      </div>
      <div class="stage">
        ${arcs()}
        <div class="cta">
          <a class="btn btn-dark" href="#plan">Plan a night ${ARROW}</a>
          <a class="btn btn-light" id="see-live" href="#recent">See a live plan</a>
        </div>
        <div class="stage-cards" id="stage">${stageHtml(null)}</div>
      </div>
      <div class="powered" aria-label="Built with">
        <span class="brand">Qloo</span><span class="brand">LangGraph</span><span class="brand">Groq</span>
        <span class="brand">Leaflet</span><span class="brand">OpenStreetMap</span>
      </div>
    </section>

    <section class="section wrap" id="plan">
      <div class="section-head">
        <p class="eyebrow">Try it</p>
        <h2 class="h2">Plan any show, anywhere.</h2>
        <p class="sub">Type an artist and a city. The agent asks Qloo who the fans are and writes the whole night in about 30 seconds. Nothing is sent to anyone from here.</p>
      </div>
      <form id="try-form" class="card plan-card" novalidate>
        <div class="fields">
          <label class="field">Artist<input name="headliner" required autocomplete="off" placeholder="Laufey" maxlength="120"></label>
          <label class="field"><span>Support acts <em>optional, comma-separated</em></span><input name="support" autocomplete="off" placeholder="Militarie Gun" maxlength="300"></label>
          <label class="field">City<input name="city" required autocomplete="off" placeholder="Lagos, Seoul, São Paulo" maxlength="120"></label>
        </div>
        <div class="rules" role="group" aria-label="Venue rules">
          <span class="rules-label">Venue rules</span>
          <label class="toggle-chip"><input type="checkbox" name="no_alcohol"><span>Alcohol-free</span></label>
          <label class="toggle-chip"><input type="checkbox" name="dietary" value="vegan"><span>Vegan option</span></label>
          <label class="toggle-chip"><input type="checkbox" name="dietary" value="vegetarian"><span>Vegetarian option</span></label>
          <label class="toggle-chip"><input type="checkbox" name="dietary" value="halal"><span>Halal option</span></label>
          <label class="toggle-chip"><input type="checkbox" name="dietary" value="gluten-free"><span>Gluten-free option</span></label>
        </div>
        <div class="form-foot">
          <p class="form-error" id="try-error" role="alert" hidden></p>
          <span class="muted">Every artist, brand and venue in the plan comes from Qloo.</span>
          <button type="submit" class="btn btn-dark">Plan the night ${ARROW}</button>
        </div>
      </form>
      <div class="ideas">Try
        <button type="button" class="idea" data-try="Laufey|New York">Laufey in New York</button>
        <button type="button" class="idea" data-try="Turnstile|New York">Turnstile in New York</button>
        <button type="button" class="idea" data-try="Burna Boy|Lagos">Burna Boy in Lagos</button>
        <button type="button" class="idea" data-try="YOASOBI|Tokyo">YOASOBI in Tokyo</button>
      </div>
    </section>

    <section class="section wrap" id="how">
      <div class="section-head">
        <p class="eyebrow">How it works</p>
        <h2 class="h2">Set the rules once. The agent does the rest.</h2>
        <p class="sub">No confirm buttons between steps. The venue connects its calendar and its house rules, and the agent runs the loop by itself.</p>
      </div>
      <div class="steps">
        <div class="card step"><span class="n">1</span><h3>Watches your calendar</h3><p>Reads each venue's iCal feed every 15 minutes. A new or changed show starts a plan.</p></div>
        <div class="card step blue"><span class="n">2</span><h3>Asks Qloo who the fans are</h3><p>About a dozen calls per show: similar artists, brands, local bars and restaurants, dishes, music taste, age skew and a city heatmap.</p></div>
        <div class="card step yellow"><span class="n">3</span><h3>Plans and checks the rules</h3><p>Writes the night, removes anything Qloo can't back up, and fixes breaks of the venue's rules before it ships.</p></div>
        <div class="card step green"><span class="n">4</span><h3>Sends it out and learns</h3><p>Pitches partners, emails the team, and re-plans the next shows when you report what sold and what flopped.</p></div>
      </div>
    </section>

    <section class="section wrap" id="venues">
      <div class="section-head">
        <p class="eyebrow">Venues being watched</p>
        <h2 class="h2">Two very different nights in every city.</h2>
        <p class="sub">Fictional demo venues on four continents, each with a sample calendar. The agent planned every show on its own.</p>
      </div>
      <div class="venues" id="venue-list"><p class="muted">Loading venues…</p></div>
    </section>

    <section class="section wrap" id="recent">
      <div class="section-head">
        <p class="eyebrow">Recently planned</p>
        <h2 class="h2">Open any plan to see the Qloo receipts.</h2>
      </div>
      <div class="recent" id="recent-list"><p class="muted">Loading plans…</p></div>
    </section>

    <section class="section wrap" id="connect-section">
      <details class="card connect" id="connect">
        <summary>Connect a venue</summary>
        <form id="venue-form" novalidate>
          <div class="grid-2">
            <label class="field">Venue name<input name="name" required maxlength="120"></label>
            <label class="field">City<input name="city" required maxlength="120"></label>
            <label class="field"><span>Calendar feed <em>iCal or webcal URL</em></span><input name="ical_url" required placeholder="https://… .ics" maxlength="2000"></label>
            <label class="field"><span>Ops email <em>optional, gets every plan</em></span><input name="ops_email" type="email" maxlength="240"></label>
            <label class="field"><span>Must offer <em>comma-separated, e.g. halal, vegan</em></span><input name="dietary"></label>
            <label class="field">Most after-show partners<input name="max_partners" type="number" min="0" max="12" value="4"></label>
            <label class="field"><span>Notes for the planner <em>optional</em></span><textarea name="notes" rows="2" maxlength="600"></textarea></label>
            <label class="field"><span>Partner contacts <em>one per line: Name = email</em></span><textarea name="contacts" rows="2"></textarea></label>
          </div>
          <div class="rules"><label class="toggle-chip"><input type="checkbox" name="no_alcohol"><span>Alcohol-free venue</span></label></div>
          <p class="form-error" id="venue-error" role="alert" hidden></p>
          <div><button type="submit" class="btn btn-dark">Connect venue</button></div>
        </form>
      </details>
    </section>`;
  }

  function stageHtml(run) {
    if (!run || !run.plan) {
      return `<div class="phone" aria-hidden="true"><div class="phone-screen"><div class="phone-notch"></div>
        <div class="phone-ui"><p class="phone-kicker">Convivium agent</p><p class="phone-state"><span class="dot"></span>Waiting</p>
        <p class="phone-title">Ready for the next show</p><p class="phone-empty-msg">Plan a night and it shows up here.</p></div></div></div>`;
    }
    const plan = run.plan, meta = plan._meta || {}, pools = run.pools || {};
    const idx = poolIndex(pools);
    const head = (meta.resolved || {}).headliner || {};
    const dj = (plan.dj_set || []).map((i) => entsOf(i, idx)[0]).filter(Boolean);
    const partner = (plan.after_show || []).map((i) => entsOf(i, idx)[0]).find((e) => e && e.image) || null;
    const demo = pools.demographics || {};
    const top = topAge(demo);
    const affs = dj.map((e) => e.affinity).filter((a) => typeof a === "number");
    const faces = dj.filter((e) => e.image).slice(0, 4);
    const calls = meta.calls || (run.trace || []).length;
    return `
      <a class="phone" href="#run/${esc(run.id)}" aria-label="Open the plan for ${esc(run.ctx.headliner)} in ${esc(run.ctx.city)}">
        <div class="phone-screen">${img(head.image)}<div class="phone-notch"></div>
          <div class="phone-ui">
            <p class="phone-kicker">Convivium agent</p>
            <p class="phone-state"><span class="dot"></span>Planned automatically</p>
            <p class="phone-title">The night for ${esc(fixtureName(run.ctx.headliner))}</p>
            <p class="phone-sub">${esc(run.ctx.venue_name || "Any venue")}, ${esc(run.ctx.city)}</p>
            <div class="phone-bar"><span class="track"><span class="fill" style="display:block;width:100%"></span></span>${calls} Qloo calls</div>
          </div>
          ${partner ? `<div class="phone-inset">${img(partner.image)}<span>After-show: ${esc(fixtureName(partner.name))}</span></div>` : ""}
        </div>
      </a>
      <div class="card blue float float-1">
        <div class="card-title">DJ set from Qloo <span class="muted">${dj.length} artists</span></div>
        <ul class="float-list">${dj.slice(0, 3).map((e) => `<li><span class="name">${esc(fixtureName(e.name))}</span><span class="num">${fmtAff3(e.affinity)}</span></li>`).join("")}</ul>
      </div>
      <div class="card yellow float float-2">
        <div class="big-num">${top ? esc(AGE.find(([k]) => k === top)?.[1] || "") : "n/a"}</div>
        <div class="float-cap">Biggest age skew of these fans</div>
        ${ageBars(demo)}
      </div>
      <div class="card green float float-3">
        <div class="big-num">${affs.length ? fmtAff3(Math.max(...affs)) : "n/a"}</div>
        <div class="float-cap">Top fan affinity in the DJ set</div>
        ${spark(affs)}
        <div class="float-foot">Qloo affinity, artist by artist</div>
      </div>
      <div class="card float float-4">
        <div class="big-num">${calls}</div>
        <div class="float-cap">Qloo calls behind this one plan</div>
        <div class="avatars">${faces.map((e) => img(e.image, "", e.name)).join("")}${dj.length > faces.length ? `<span class="more">+${dj.length - faces.length}</span>` : ""}</div>
        <div class="float-foot">Every name traced to a Qloo response</div>
      </div>`;
  }

  async function loadFeatured() {
    try {
      const runs = (await api("/api/runs?limit=20")).filter((r) => r.status === "done");
      if (!runs.length) return;
      const run = await api(`/api/runs/${runs[0].id}`);
      if (state.page !== "home") return;
      $("#stage").innerHTML = stageHtml(run);
      $("#see-live").setAttribute("href", `#run/${run.id}`);
    } catch (_) { /* keep the empty stage */ }
  }

  function rulesPills(p) {
    const r = [];
    if (p.no_alcohol) r.push("Alcohol-free");
    for (const d of p.dietary || []) r.push(d[0].toUpperCase() + d.slice(1) + " option");
    r.push(`Up to ${p.max_partners} partners`);
    return r.map((t) => `<span class="pill">${esc(t)}</span>`).join("");
  }

  function statusPill(ev) {
    if (ev.planning || ev.last_run_status === "running" || ev.last_run_status === "queued") return `<span class="status running">Planning</span>`;
    if (ev.last_run_status === "done") return `<span class="status done">Planned</span>`;
    if (ev.last_run_status === "error") return `<span class="status error">Failed</span>`;
    return `<span class="status">Waiting</span>`;
  }

  async function loadVenues() {
    const el = $("#venue-list");
    if (!el) return;
    try {
      state.venues = await api("/api/venues");
    } catch (e) {
      el.innerHTML = `<p class="form-error">Couldn't load venues: ${esc(e.message)}</p>`;
      return;
    }
    if (!state.venues.length) {
      el.innerHTML = `<div class="card flat"><p class="muted">No venues yet. Connect one below and the agent starts watching its calendar.</p></div>`;
      return;
    }
    el.innerHTML = state.venues.map((v) => `
      <article class="card venue">
        <div class="venue-top">
          <div><p class="venue-city">${esc(v.city)}</p><h3 class="venue-name">${esc(v.name)}</h3></div>
          <button class="text-btn" data-sync="${esc(v.id)}" type="button">Re-plan</button>
        </div>
        <div class="pills">${rulesPills(v.policy)}</div>
        <ul class="events">${v.events.length ? v.events.map((ev) => {
          const who = `<span class="who">${esc(ev.headliner)}${ev.support && ev.support.length ? ` <small>with ${esc(ev.support.join(", "))}</small>` : ""}</span>`;
          const inner = `<span class="date">${esc(fmtDate(ev.start))}</span>${who}${statusPill(ev)}`;
          return `<li>${ev.last_run_id ? `<a href="#run/${ev.last_run_id}">${inner}</a>` : `<div class="ev">${inner}</div>`}</li>`;
        }).join("") : `<li class="ev note">No upcoming shows in this calendar.</li>`}</ul>
        ${v.is_demo ? `<p class="note">Fictional demo venue. Sample calendar, not real tour dates.</p>` : ""}
      </article>`).join("");
  }

  async function loadRecent() {
    const el = $("#recent-list");
    if (!el) return;
    let runs = [];
    try { runs = (await api("/api/runs?limit=12")).filter((r) => r.status === "done"); } catch (_) { /* ignore */ }
    el.innerHTML = runs.length ? runs.map((r) => `
      <a class="card" href="#run/${r.id}"><span class="r-title">${esc(r.ctx.headliner)}</span>
        <span class="r-meta">${esc(r.ctx.city)}, ${r.kind === "try" ? "one-off plan" : r.kind === "replan" ? "re-planned after feedback" : "planned from a calendar"}</span></a>`).join("")
      : `<div class="card flat"><p class="muted">No plans yet. Plan a night above and it shows up here.</p></div>`;
  }

  function renderHome(anchor) {
    destroyMap();
    if (state.page !== "home") {
      view.innerHTML = homeHtml();
      state.page = "home";
      loadFeatured(); loadVenues(); loadRecent();
    }
    if (anchor) {
      const t = document.getElementById(anchor);
      if (t) {
        t.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
        if (anchor === "plan") setTimeout(() => $("#try-form [name=headliner]").focus({ preventScroll: true }), 350);
      }
    } else {
      window.scrollTo(0, 0);
    }
  }

  /* ---------- run view ---------- */
  function itemsList(items, idx, generic, emptyMsg) {
    if (!items || !items.length) return `<p class="empty">${esc(emptyMsg)}</p>`;
    return `<ul class="items">${items.map((i) => `
      <li><div class="item-title">${esc(i.title)}</div>
        ${i.detail ? `<div class="item-detail">${esc(i.detail)}</div>` : ""}
        ${evidenceHtml(i, idx, generic)}</li>`).join("")}</ul>`;
  }

  function setlistCard(items, idx, generic) {
    const body = items && items.length ? `<ol class="setlist">${items.map((i) => {
      const e = generic ? null : entsOf(i, idx)[0];
      const name = fixtureName(i.title);
      const av = e && e.image ? img(e.image, "av") : `<span class="av ph" aria-hidden="true">${esc(name.charAt(0))}</span>`;
      return `<li>${av}<div><div class="song">${esc(name)}</div>
        ${i.detail && !/^Fans' affinity/.test(i.detail) ? `<div class="song-detail">${esc(i.detail)}</div>` : ""}
        <div class="chips">${e ? chip(e, "Qloo", true) : infer()}</div></div></li>`;
    }).join("")}</ol>` : `<p class="empty">Qloo returned no artists for these fans, so there's no DJ set to suggest.</p>`;
    return `<section class="card blue s-5 tall"><div class="card-title">DJ set between acts <span class="muted">${generic ? "guessed" : "artists these fans love"}</span></div>${body}</section>`;
  }

  function audienceCard(pools, generic) {
    if (generic) {
      return `<section class="card yellow s-5"><div class="card-title">Who the fans are</div><p class="empty">Without Qloo there's no audience data. The plan has to assume everyone is the same.</p></section>`;
    }
    const demo = (pools && pools.demographics) || {};
    const top = topAge(demo);
    const g = demo.gender || {};
    const gl = typeof g.female === "number" && typeof g.male === "number"
      ? (Math.abs(g.female - g.male) < 0.2 ? "No strong gender skew" : g.female > g.male ? "Leans female" : "Leans male") : "";
    if (!top) return `<section class="card yellow s-5"><div class="card-title">Who the fans are</div><p class="empty">Qloo returned no demographics for this audience.</p></section>`;
    return `<section class="card yellow s-5"><div class="card-title">Who the fans are <span class="muted">Qloo demographics</span></div>
      <div class="audience"><div class="big-num">${esc(AGE.find(([k]) => k === top)?.[1] || top)}</div>
      <div class="float-cap">The age group these fans skew toward most</div></div>
      ${ageBars(demo)}
      ${gl ? `<div class="gender"><span class="pill">${esc(gl)}</span></div>` : ""}</section>`;
  }

  function brandsCard(items, idx, generic) {
    const body = items && items.length ? `<ul class="items">${items.map((i) => {
      const e = generic ? null : entsOf(i, idx)[0];
      const logo = e && e.image ? `<span class="logo">${img(e.image)}</span>` : "";
      return `<li class="${logo ? "brand-row" : ""}">${logo}<div>
        <div class="item-title">${esc(i.title)}</div>${i.detail ? `<div class="item-detail">${esc(i.detail)}</div>` : ""}
        ${evidenceHtml(i, idx, generic)}</div></li>`;
    }).join("")}</ul>` : `<p class="empty">Qloo returned no brands these fans over-index on.</p>`;
    return `<section class="card s-7"><div class="card-title">Brand moments</div>${body}</section>`;
  }

  function partnersCard(items, idx, generic) {
    const body = items && items.length ? `<div class="partners">${items.map((i) => {
      const e = generic ? null : entsOf(i, idx)[0];
      return `<article class="partner">${e && e.image ? `<div class="ph">${img(e.image)}</div>` : ""}<div class="body">
        <span class="name">${esc(i.title)}</span>
        ${e && e.address ? `<span class="addr">${esc(e.address)}</span>` : i.detail ? `<span class="addr">${esc(i.detail)}</span>` : ""}
        ${evidenceHtml(i, idx, generic)}</div></article>`;
    }).join("")}</div>` : `<p class="empty">Qloo returned no bars or restaurants in this city for these fans.</p>`;
    return `<section class="card s-12"><div class="card-title">After-show partners <span class="muted">${generic ? "guessed" : "places in town these fans love; the agent pitches each one"}</span></div>${body}</section>`;
  }

  function header(run, back = true) {
    const ctx = run.ctx || {};
    const pills = [];
    if (ctx.date) pills.push(fmtDate(ctx.date, true));
    pills.push(`${ctx.venue_name || "Any venue"}, ${ctx.city}`);
    pills.push(ctx.headliner + (ctx.support && ctx.support.length ? " with " + ctx.support.join(", ") : ""));
    if (run.kind === "auto") pills.push("Planned automatically from the venue calendar");
    if (run.kind === "replan") pills.push("Re-planned after an outcome report");
    if (run.kind === "try") pills.push("One-off plan, drafted only");
    return `${back ? `<a class="back" href="#">Back to Convivium</a>` : ""}
      <div class="meta">${pills.map((p) => `<span class="pill">${esc(p)}</span>`).join("")}
      ${ctx.is_sample ? `<span class="pill sample">Sample calendar for the demo. Not a real tour date.</span>` : ""}</div>`;
  }

  function stepsLog(run, live) {
    const rows = (run.steps || []).map((s) => `<li><span class="dot"></span>
      <div><strong>${esc(s.label)}</strong>${(s.notes || []).map((n) => `<div class="notes">${esc(n)}</div>`).join("")}</div>
      <span class="when">${s.t.toFixed(1)}s</span></li>`);
    if (live) rows.push(`<li><span class="dot live"></span><div><strong>Working…</strong><div class="notes">Calling Qloo and writing the plan.</div></div><span></span></li>`);
    return `<ul class="log">${rows.join("")}</ul>`;
  }

  function receipts(run) {
    const trace = run.trace || [];
    if (!trace.length) return `<p class="empty">No Qloo calls recorded yet.</p>`;
    const total = trace.reduce((a, t) => a + (t.ms || 0), 0);
    return `<p class="muted">${trace.length} calls, ${total.toLocaleString()} ms of Qloo time. Each row is a real request this run made${trace[0].data_source === "fixture" ? " (to offline fixtures)" : ""}.</p>
      <div class="scroll-x"><table><thead><tr><th>What for</th><th>Endpoint</th><th class="num">Results</th><th class="num">ms</th><th>Cache</th><th>Status</th></tr></thead>
      <tbody>${trace.map((t) => {
        const p = t.params || {};
        const ep = t.path + (p["filter.type"] ? ` ${p["filter.type"]}` : "") + (p["filter.tag.types"] ? ` (${p["filter.tag.types"].replace(/urn:tag:/g, "")})` : "") + (p["filter.tags"] ? ` + ${p["filter.tags"].split(":").pop()}` : "");
        return `<tr><td>${esc(t.purpose)}</td><td><code>${esc(ep)}</code></td><td class="num">${t.count}</td>
          <td class="num">${t.ms}</td><td>${t.cache ? "hit" : "miss"}</td><td>${esc(t.status)}</td></tr>`;
      }).join("")}</tbody></table></div>`;
  }

  function guardsHtml(meta) {
    const rows = [];
    for (const d of meta.dropped || []) rows.push(`<li><strong>${esc(d.title)}</strong> (${esc(d.section)}): ${esc(d.reason)}</li>`);
    for (const f of meta.fixes || []) rows.push(`<li>${esc(f)}</li>`);
    for (const v of meta.violations || []) rows.push(`<li class="form-error">Still open: ${esc(v)}</li>`);
    const head = `<p class="muted">Written by the ${esc(meta.composer)} composer in ${meta.attempts} draft${meta.attempts === 1 ? "" : "s"}. Every artist, brand and venue is checked against Qloo's response in code.</p>`;
    return head + (rows.length ? `<ul class="guards">${rows.join("")}</ul>` : `<p class="empty">Nothing removed or fixed: every grounded item cited a Qloo id and the venue rules held.</p>`);
  }

  function outboxHtml(run) {
    const out = run.outbox || [];
    if (!out.length) return `<p class="empty">No actions recorded.</p>`;
    const label = { partner_pitch: "Partner pitch", brand_pitch: "Brand pitch", plan_email: "Plan to ops", webhook: "Team channel" };
    return `<div class="scroll-x"><table><thead><tr><th>Action</th><th>To</th><th>Status</th></tr></thead><tbody>
      ${out.map((a) => `<tr><td><strong>${esc(label[a.kind] || a.kind)}</strong><div class="muted">${esc(a.subject)}</div></td>
        <td>${esc(fixtureName(a.recipient))}</td>
        <td><span class="st st-${esc(a.status)}">${esc(a.status)}</span><div class="muted">${esc(a.detail)}</div></td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function reportCard(run, plan) {
    if (!run.venue_id) return "";
    if (state.reported && state.reported.run === run.id) {
      const links = state.reported.runs.map((r, i) => `<a href="#run/${r}">re-plan ${i + 1}</a>`).join(", ");
      return `<section class="card s-12"><div class="card-title">Report how it went</div><div class="notice" role="status">Saved to venue memory.
        ${state.reported.runs.length ? `The agent is re-planning the next ${state.reported.runs.length} show${state.reported.runs.length === 1 ? "" : "s"}: ${links}.` : "There are no upcoming shows to re-plan."}</div></section>`;
    }
    const box = (name, items) => items.map((i) => `<label class="toggle-chip"><input type="checkbox" name="${name}" value="${esc(i.title)}"><span>${esc(fixtureName(i.title))}</span></label>`).join("");
    const sellable = [...(plan.food || []), ...(plan.drinks || [])];
    return `<section class="card s-12"><div class="card-title">Report how it went</div>
      <form class="report" id="report-form">
        <p class="muted">Tell the agent what happened. It remembers, then re-plans this venue's next 3 shows on its own.</p>
        <fieldset><legend>Sold well</legend><div class="opts">${box("sold_well", sellable)}</div></fieldset>
        <fieldset><legend>Flopped</legend><div class="opts">${box("flopped", sellable)}</div></fieldset>
        <fieldset><legend>Declined to partner</legend><div class="opts">${box("declined_partners", [...(plan.after_show || []), ...(plan.brands || [])])}</div></fieldset>
        <label class="field">Anything else<textarea name="notes" rows="2" maxlength="2000"></textarea></label>
        <div><button type="submit" class="btn btn-dark">Report how it went</button></div>
        <p class="form-error" id="report-error" role="alert" hidden></p>
      </form></section>`;
  }

  function renderMap(plan, pools) {
    destroyMap();
    const el = $("#map");
    if (!el || typeof L === "undefined") return;
    const heat = (pools && pools.heatmap) || [];
    const idx = poolIndex(pools);
    const partners = (plan.after_show || []).map((i) => idx[(i.evidence || [])[0]]).filter((e) => e && e.lat != null && e.lon != null);
    if (!heat.length && !partners.length) {
      el.outerHTML = `<p class="empty">Qloo returned no location data for this city, so there's no map. The promo plan falls back to the artist's own channels.</p>`;
      return;
    }
    const map = L.map(el, { scrollWheelZoom: false, attributionControl: true });
    state.map = map;
    const tiles = (state.status && state.status.map) || { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", muted: true,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' };
    el.classList.toggle("muted", !!tiles.muted);
    L.tileLayer(tiles.url, { maxZoom: 19, subdomains: "abcd", attribution: tiles.attribution }).addTo(map);
    const pts = [];
    for (const p of heat) {
      const a = Math.max(0, Math.min(1, p.affinity || 0));
      L.circleMarker([p.lat, p.lon], { radius: 5 + a * 13, color: "#FF6B9A", weight: 1, fillColor: "#FF6B9A", fillOpacity: 0.12 + a * 0.30 })
        .bindTooltip(`Fan density (affinity ${fmtAff(p.affinity)})`).addTo(map);
      pts.push([p.lat, p.lon]);
    }
    for (const e of partners) {
      const name = fixtureName(e.name);
      L.marker([e.lat, e.lon], { icon: L.divIcon({ className: "partner-pin", html: esc(name.length > 24 ? name.slice(0, 23) + "…" : name) }), keyboard: true, title: e.name })
        .bindTooltip(`${e.name}: after-show partner (affinity ${fmtAff(e.affinity)})`).addTo(map);
      pts.push([e.lat, e.lon]);
    }
    // Frame the partners and the strongest hotspots, not every cell in the region.
    const focus = partners.map((e) => [e.lat, e.lon]).concat(
      heat.slice().sort((a, b) => (b.affinity || 0) - (a.affinity || 0)).slice(0, 8).map((p) => [p.lat, p.lon]));
    const frame = focus.length ? focus : pts;
    if (frame.length === 1) map.setView(frame[0], 13); else map.fitBounds(frame, { padding: [36, 36], maxZoom: 14 });
  }

  function renderPlan(run) {
    const generic = state.mode === "without";
    const fullPlan = run.plan || {};
    const meta = fullPlan._meta || {};
    const plan = generic ? run.baseline : fullPlan;
    const idx = poolIndex(run.pools);
    state.idx = idx;
    const na = plan.non_alcoholic_share_pct;
    const g = groundedCount(plan, idx, generic);
    const pct = g.total ? Math.round((g.grounded / g.total) * 100) : 0;
    const promo = (plan.promo_focus || []).map((p) => `<li>${esc(p)}<div>${!generic && /^Fan hotspot/.test(p) ? `<span class="chip chip-static">Qloo heatmap</span>` : infer()}</div></li>`).join("");
    view.innerHTML = `<div class="wrap ${generic ? "generic" : ""}">
      <div class="run-head">
        ${header(run)}
        <h1 class="h1">${esc(plan.headline)}</h1>
        <p class="read">${esc(plan.audience_read)} ${infer(generic ? "inference" : "inference from Qloo demographics and tags")}</p>
        <div class="controls">
          <div class="seg" role="group" aria-label="Compare with and without Qloo">
            <button type="button" data-mode="with" aria-pressed="${!generic}">With Qloo</button>
            <button type="button" data-mode="without" aria-pressed="${generic}">Without Qloo</button>
          </div>
          <span class="ground"><span class="ground-track" aria-hidden="true"><span class="ground-fill" style="display:block;width:${pct}%"></span></span>
            <span><b>${g.grounded} of ${g.total}</b> items backed by Qloo</span></span>
        </div>
        ${generic ? `<p class="without-note">No Qloo data: every item here is a guess. Same task, same venue rules, ${meta.baseline_composer === "llm-without-qloo" ? "the same LLM" : "a generic template"}, but no idea who the fans are.</p>` : ""}
      </div>
      <div class="bento">
        ${setlistCard(plan.dj_set, idx, generic)}
        <section class="card s-7"><div class="card-title">Food <span class="muted">concepts for the kitchen</span></div>${itemsList(plan.food, idx, generic, "No food signal came back from Qloo.")}</section>
        <section class="card green s-7"><div class="card-title">Drinks</div>
          <div class="na"><div class="na-track" role="img" aria-label="${na}% of the drinks menu is non-alcoholic"><div class="na-fill" style="width:${na}%"></div></div>
          <div class="na-label"><b>${na}%</b>of the bar is non-alcoholic</div></div>
          ${itemsList(plan.drinks, idx, generic, "No drinks suggested.")}</section>
        ${audienceCard(run.pools, generic)}
        ${brandsCard(plan.brands, idx, generic)}
        ${partnersCard(plan.after_show, idx, generic)}
        <section class="card s-12"><div class="card-title">Where the fans are</div>
          <div class="map-wrap">
            ${generic ? `<p class="empty">Without Qloo there's no heatmap, so promotion is spread evenly and hopes for the best.</p>` : `<div id="map" role="region" aria-label="Map of where these fans are in ${esc(run.ctx.city)}"></div>`}
            <div><div class="card-title" style="margin-bottom:12px">Promo focus</div><ul class="promo">${promo}</ul></div>
          </div></section>
        <section class="card s-5"><div class="card-title">Run of show ${infer()}</div>
          <ol class="ros">${(plan.run_of_show || []).map((r) => `<li><span>${esc(r)}</span></li>`).join("")}</ol></section>
        <section class="card s-7"><div class="card-title">What the agent did</div>${stepsLog(run, false)}</section>
        <section class="card s-7"><div class="card-title">Actions <span class="muted">real statuses, nothing faked</span></div>${outboxHtml(run)}</section>
        <section class="card s-5"><div class="card-title">Guards</div>${guardsHtml(meta)}
          ${meta.memory_used && meta.memory_used.length ? `<div class="card-title">Memory it used</div><ul class="guards">${meta.memory_used.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}</section>
        <section class="card s-12"><div class="card-title">Qloo receipts</div>${receipts(run)}</section>
        ${generic ? "" : reportCard(run, fullPlan)}
      </div></div>`;
    if (!generic) renderMap(fullPlan, run.pools);
    else destroyMap();
  }

  const NODE_ORDER = ["resolve", "baseline", "gather", "recall", "plan", "finalize", "act", "remember"];

  function renderProgress(run) {
    destroyMap();
    const steps = run.steps || [];
    const done = new Set(steps.map((s) => s.node)).size;
    const pct = Math.min(96, Math.round((done / NODE_ORDER.length) * 100));
    view.innerHTML = `<div class="wrap"><div class="run-head">${header(run)}</div>
      <div class="progress-grid">
        <div>
          <h1 class="h1">Planning ${esc(run.ctx.headliner)} in ${esc(run.ctx.city)}…</h1>
          <p class="read" role="status" style="margin-top:18px">The agent works on its own from here: find the lineup in Qloo, ask what these fans love, write the plan, check it against the venue rules, then act.</p>
          <section class="card" style="margin-top:28px"><div class="card-title">What the agent is doing</div>${stepsLog(run, true)}</section>
          <section class="card" style="margin-top:20px"><div class="card-title">Qloo receipts</div>${receipts(run)}</section>
        </div>
        <div class="progress-phone"><div class="phone" aria-hidden="true"><div class="phone-screen"><div class="phone-notch"></div>
          <div class="phone-ui"><p class="phone-kicker">Convivium agent</p><p class="phone-state"><span class="dot"></span>Working</p>
            <p class="phone-title">${esc(run.ctx.headliner)}</p><p class="phone-sub">${esc(run.ctx.city)}</p>
            <div class="phone-bar"><span class="track"><span class="fill" style="display:block;width:${pct}%"></span></span>${pct}%</div>
            <ul class="phone-steps">${steps.map((s) => `<li>${esc(s.label)}</li>`).join("")}<li class="live">Working…</li></ul>
          </div></div></div></div>
      </div></div>`;
  }

  function renderError(run) {
    destroyMap();
    view.innerHTML = `<div class="wrap"><div class="run-head">${header(run)}</div>
      <section class="card error-card" role="alert" style="margin-top:26px"><h2>The agent couldn't plan this night</h2>
        <p>${esc(run.error || "Something went wrong.")}</p>
        <p class="muted" style="margin-top:8px">Try again from the form. If the artist is niche, try their best-known name or another act on the bill.</p>
        <p style="margin-top:18px"><a class="btn btn-dark btn-sm" href="#plan">Plan another night</a></p></section>
      <div class="bento"><section class="card s-6"><div class="card-title">What the agent did</div>${stepsLog(run, false)}</section>
      <section class="card s-6"><div class="card-title">Qloo receipts</div>${receipts(run)}</section></div></div>`;
  }

  async function loadRun(id) {
    clearTimeout(state.pollTimer);
    let run;
    try {
      run = await api(`/api/runs/${encodeURIComponent(id)}`);
    } catch (e) {
      view.innerHTML = `<div class="wrap"><section class="card error-card" role="alert" style="margin-top:40px"><h2>No plan here</h2><p>${esc(e.message)} The link may be from an older deploy; plan the show again.</p>
        <p style="margin-top:18px"><a class="btn btn-dark btn-sm" href="#plan">Plan a night</a></p></section></div>`;
      return;
    }
    if (state.runId !== id) return;
    const key = `${run.status}|${(run.steps || []).length}|${(run.trace || []).length}|${run.updated}`;
    const changed = key !== state.lastKey;
    state.lastKey = key;
    state.run = run;
    if (run.status === "done") { if (changed) renderPlan(run); }
    else if (run.status === "error") { if (changed) renderError(run); }
    else {
      if (changed) renderProgress(run);
      state.pollTimer = setTimeout(() => loadRun(id), 1000);
    }
  }

  /* ---------- events ---------- */
  view.addEventListener("click", async (e) => {
    const mode = e.target.closest("[data-mode]");
    if (mode && state.run) {
      state.mode = mode.dataset.mode;
      renderPlan(state.run);
      const again = view.querySelector(`[data-mode="${state.mode}"]`);
      if (again) again.focus();
      return;
    }
    const idea = e.target.closest("[data-try]");
    if (idea) {
      const [artist, city] = idea.dataset.try.split("|");
      const f = $("#try-form");
      f.headliner.value = artist; f.city.value = city; f.support.value = "";
      f.querySelector("button[type=submit]").focus();
      return;
    }
    const sync = e.target.closest("[data-sync]");
    if (sync) {
      sync.disabled = true;
      sync.textContent = "Re-planning…";
      try { await api(`/api/venues/${sync.dataset.sync}/sync`, { method: "POST" }); } catch (err) { alert(err.message); }
      await loadVenues();
    }
  });

  view.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (f.id === "try-form") return submitTry(f);
    if (f.id === "venue-form") return submitVenue(f);
    if (f.id === "report-form") return submitReport(f);
  });

  async function submitTry(f) {
    const err = $("#try-error");
    err.hidden = true;
    const headliner = f.headliner.value.trim();
    const city = f.city.value.trim();
    if (!headliner || !city) {
      err.textContent = !headliner ? "Type an artist to plan around." : "Type the city the show is in.";
      err.hidden = false;
      (!headliner ? f.headliner : f.city).focus();
      return;
    }
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    btn.textContent = "Starting the agent…";
    try {
      const body = {
        headliner, city,
        support: f.support.value.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 3),
        policy: { no_alcohol: f.no_alcohol.checked, dietary: [...f.querySelectorAll("input[name=dietary]:checked")].map((i) => i.value) },
      };
      const { run_id } = await api("/api/try", { method: "POST", body: JSON.stringify(body) });
      state.mode = "with";
      location.hash = `#run/${run_id}`;
      view.focus();
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
      btn.disabled = false;
      btn.innerHTML = `Plan the night ${ARROW}`;
    }
  }

  async function submitVenue(f) {
    const err = $("#venue-error");
    err.hidden = true;
    const contacts = {};
    f.contacts.value.split("\n").forEach((line) => {
      const [name, email] = line.split("=").map((s) => (s || "").trim());
      if (name && email) contacts[name] = email;
    });
    const body = {
      name: f.name.value.trim(), city: f.city.value.trim(), ical_url: f.ical_url.value.trim(), ops_email: f.ops_email.value.trim(),
      policy: { no_alcohol: f.no_alcohol.checked, max_partners: Number(f.max_partners.value || 4),
                dietary: f.dietary.value.split(",").map((s) => s.trim()).filter(Boolean), notes: f.notes.value.trim(), partner_contacts: contacts },
    };
    if (!body.name || !body.city || !body.ical_url) {
      err.textContent = "A venue needs a name, a city and a calendar feed.";
      err.hidden = false;
      return;
    }
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const res = await api("/api/venues", { method: "POST", body: JSON.stringify(body) });
      f.reset();
      $("#connect").open = false;
      await loadVenues();
      $("#venues").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth" });
      if (!res.scheduled) alert(`${res.venue.name} is connected, but its calendar has no upcoming shows yet. The agent will keep checking.`);
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      btn.disabled = false;
    }
  }

  async function submitReport(f) {
    const vals = (n) => [...f.querySelectorAll(`input[name=${n}]:checked`)].map((i) => i.value);
    const body = { run_id: state.run.id, sold_well: vals("sold_well"), flopped: vals("flopped"), declined_partners: vals("declined_partners"), notes: f.notes.value.trim() };
    const err = $("#report-error");
    if (!body.sold_well.length && !body.flopped.length && !body.declined_partners.length && !body.notes) {
      err.textContent = "Tick at least one item or add a note."; err.hidden = false; return;
    }
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      const res = await api(`/api/venues/${state.run.venue_id}/outcomes`, { method: "POST", body: JSON.stringify(body) });
      state.reported = { run: state.run.id, runs: res.replanning };
      renderPlan(state.run);
    } catch (ex) {
      err.textContent = ex.message; err.hidden = false;
      btn.disabled = false; btn.textContent = "Report how it went";
    }
  }

  /* ---------- routing ---------- */
  function route() {
    hideTip();
    clearTimeout(state.pollTimer);
    const m = location.hash.match(/^#run\/([\w-]+)(\/without)?/);
    if (m) {
      if (state.runId !== m[1]) { state.mode = m[2] ? "without" : "with"; state.lastKey = ""; }
      state.runId = m[1];
      state.page = "run";
      view.innerHTML = `<div class="wrap"><p class="muted" role="status" style="padding-top:60px">Loading the plan…</p></div>`;
      window.scrollTo(0, 0);
      loadRun(m[1]);
    } else {
      state.runId = null;
      state.run = null;
      renderHome(location.hash.slice(1) || null);
    }
  }

  window.addEventListener("hashchange", route);
  loadStatus().then(route);
  setInterval(() => { if (!document.hidden && state.page === "home") loadVenues(); }, 10000);
})();
