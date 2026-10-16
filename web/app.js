/* Convivium dashboard. Vanilla JS, no build step.
   Magenta chips = grounded in Qloo data; amber markers = agent inference. */
(() => {
  "use strict";

  const $ = (sel, el = document) => el.querySelector(sel);
  const view = $("#view");
  const tip = $("#tip");
  const state = { status: null, venues: [], runId: null, run: null, mode: "with", pollTimer: null, map: null,
                  lastKey: "", reported: null };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fixtureName = (s) => String(s || "").replace(/ \(fixture\)$/, "");
  const fmtAff = (a) => (typeof a === "number" ? a.toFixed(2) : "n/a");

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

  function fmtDate(iso, withYear = false) {
    if (!iso) return "";
    // Show the venue's local calendar date, not the viewer's: use the date part as written.
    const d = new Date(iso.slice(0, 10) + "T12:00:00");
    if (isNaN(d)) return iso;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(withYear ? { year: "numeric", weekday: "short" } : {}) });
  }

  /* ---------- status pills ---------- */
  async function loadStatus() {
    try {
      const s = await api("/api/status");
      state.status = s;
      const live = s.qloo_mode === "live";
      $("#pills").innerHTML = [
        `<li class="pill ${live ? "" : "warn"}">Qloo: <b>${live ? "live data" : "offline fixtures"}</b></li>`,
        `<li class="pill">Planner: <b>${esc(s.llm)}</b></li>`,
        `<li class="pill">Memory: <b>${esc(s.memory)}</b></li>`,
        `<li class="pill">Calendar check: <b>every ${Math.round(s.watch_interval_seconds / 60)} min</b></li>`,
      ].join("");
      $("#fixture-banner").hidden = live;
    } catch (e) {
      $("#pills").innerHTML = `<li class="pill warn">Status unavailable</li>`;
    }
  }

  /* ---------- venues rail ---------- */
  function rulesText(p) {
    const r = [];
    if (p.no_alcohol) r.push("alcohol-free");
    if (p.dietary && p.dietary.length) r.push("must offer " + p.dietary.join(", "));
    r.push(`up to ${p.max_partners} partners`);
    return r.join("; ");
  }

  function statusLabel(ev) {
    if (ev.planning || ev.last_run_status === "running" || ev.last_run_status === "queued") return ["running", "planning"];
    if (ev.last_run_status === "done") return ["done", "planned"];
    if (ev.last_run_status === "error") return ["error", "failed"];
    return ["", "waiting"];
  }

  async function loadVenues() {
    try {
      state.venues = await api("/api/venues");
    } catch (e) {
      $("#venues").innerHTML = `<p class="form-error">Couldn't load venues: ${esc(e.message)}</p>`;
      return;
    }
    if (!state.venues.length) {
      $("#venues").innerHTML = `<p class="hint">No venues yet. Connect one below and the agent starts watching its calendar.</p>`;
      return;
    }
    $("#venues").innerHTML = state.venues.map((v) => `
      <div class="venue">
        <div class="venue-name"><strong>${esc(v.name)}, ${esc(v.city)}</strong>
          <button class="link-btn" data-sync="${esc(v.id)}" type="button">Re-plan</button></div>
        <p class="venue-rules">${esc(rulesText(v.policy))}${v.is_demo ? ". Fictional demo venue." : ""}</p>
        <ul class="events">${v.events.length ? v.events.map((ev) => {
          const [cls, label] = statusLabel(ev);
          const href = ev.last_run_id ? `#run/${ev.last_run_id}` : "";
          const cur = state.runId && ev.last_run_id === state.runId ? ' aria-current="page"' : "";
          const who = esc(ev.headliner) + (ev.support && ev.support.length ? ` <span class="hint">with ${esc(ev.support.join(", "))}</span>` : "");
          return `<li><a ${href ? `href="${href}"` : 'aria-disabled="true"'}${cur}><span class="date">${esc(fmtDate(ev.start))}</span>
            <span>${who}</span><span class="status ${cls}">${label}</span></a></li>`;
        }).join("") : `<li class="hint">No upcoming shows in this calendar.</li>`}</ul>
      </div>`).join("");
  }

  $("#venues").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-sync]");
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = "Re-planning…";
    try { await api(`/api/venues/${btn.dataset.sync}/sync`, { method: "POST" }); } catch (err) { alert(err.message); }
    await loadVenues();
  });

  /* ---------- forms ---------- */
  $("#try-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
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
        policy: { no_alcohol: f.no_alcohol.checked,
                  dietary: [...f.querySelectorAll("input[name=dietary]:checked")].map((i) => i.value) },
      };
      const { run_id } = await api("/api/try", { method: "POST", body: JSON.stringify(body) });
      state.mode = "with";
      location.hash = `#run/${run_id}`;
      $("#main").focus();
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Plan the night";
    }
  });

  $("#venue-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const err = $("#venue-error");
    err.hidden = true;
    const contacts = {};
    f.contacts.value.split("\n").forEach((line) => {
      const [name, email] = line.split("=").map((s) => (s || "").trim());
      if (name && email) contacts[name] = email;
    });
    const body = {
      name: f.name.value.trim(), city: f.city.value.trim(), ical_url: f.ical_url.value.trim(),
      ops_email: f.ops_email.value.trim(), capacity: f.capacity.value ? Number(f.capacity.value) : null,
      policy: { no_alcohol: f.no_alcohol.checked, max_partners: Number(f.max_partners.value || 4),
                dietary: f.dietary.value.split(",").map((s) => s.trim()).filter(Boolean),
                notes: f.notes.value.trim(), partner_contacts: contacts },
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
      if (!res.scheduled) {
        alert(`${res.venue.name} is connected, but its calendar has no upcoming shows yet. The agent will keep checking.`);
      }
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      btn.disabled = false;
    }
  });

  /* ---------- evidence ---------- */
  function poolIndex(pools) {
    const idx = {};
    for (const key of ["artists", "brands", "eats", "bars"]) {
      for (const e of (pools && pools[key]) || []) idx[e.id] = { ...e, pool: key };
    }
    for (const t of (pools && pools.tags) || []) idx[t.id] = { ...t, pool: "tags" };
    return idx;
  }

  function chip(ent) {
    return `<button type="button" class="chip" data-eid="${esc(ent.id)}" aria-label="${esc(ent.name)}, Qloo affinity ${fmtAff(ent.affinity)}">
      ${esc(ent.name)}${typeof ent.affinity === "number" ? ` <span class="aff">${fmtAff(ent.affinity)}</span>` : ""}</button>`;
  }

  function evidenceHtml(item, idx, generic) {
    const ents = generic ? [] : (item.evidence || []).map((id) => idx[id]).filter(Boolean);
    if (!ents.length) return `<div class="chips"><span class="tag-infer">inference</span></div>`;
    return `<div class="chips">${ents.map(chip).join("")}</div>`;
  }

  const POOL_LABEL = { artists: "Artist", brands: "Brand", eats: "Restaurant", bars: "Bar", tags: "Taste tag" };

  function showTip(target) {
    const ent = state.idx && state.idx[target.dataset.eid];
    if (!ent) return;
    const tags = (ent.tags || []).map((t) => fixtureName(t.name)).filter(Boolean).slice(0, 5);
    tip.innerHTML = `<strong>${esc(ent.name)}</strong>
      <div class="muted">${esc(POOL_LABEL[ent.pool] || ent.type)} from ${ent.source === "fixture" ? "offline fixtures" : "Qloo"}</div>
      <div>Affinity ${fmtAff(ent.affinity)}${typeof ent.popularity === "number" ? `, popularity ${fmtAff(ent.popularity)}` : ""}</div>
      ${ent.address ? `<div class="muted">${esc(ent.address)}</div>` : ""}
      ${tags.length ? `<div class="muted">Tags: ${esc(tags.join(", "))}</div>` : ""}
      <div class="muted">${esc(ent.id)}</div>`;
    tip.hidden = false;
    const r = target.getBoundingClientRect();
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    let top = r.bottom + 8;
    if (top + h > window.innerHeight - 8) top = r.top - h - 8;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  const hideTip = () => { tip.hidden = true; };
  document.addEventListener("mouseover", (e) => { const c = e.target.closest(".chip"); if (c) showTip(c); });
  document.addEventListener("mouseout", (e) => { if (e.target.closest(".chip")) hideTip(); });
  document.addEventListener("focusin", (e) => { const c = e.target.closest(".chip"); if (c) showTip(c); else hideTip(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  window.addEventListener("scroll", hideTip, { passive: true });

  /* ---------- run view ---------- */
  function itemsList(items, idx, generic, emptyMsg) {
    if (!items || !items.length) return `<p class="empty">${esc(emptyMsg)}</p>`;
    return `<ul class="items">${items.map((i) => `
      <li><div class="item-title">${esc(i.title)}</div>
        ${i.detail ? `<div class="item-detail">${esc(i.detail)}</div>` : ""}
        ${evidenceHtml(i, idx, generic)}</li>`).join("")}</ul>`;
  }

  function setlist(items, idx, generic) {
    const body = items && items.length ? `<ol>${items.map((i) => {
      const ents = generic ? [] : (i.evidence || []).map((id) => idx[id]).filter(Boolean);
      const meta = ents.length ? chip({ ...ents[0], name: "Qloo" }) : `<span class="tag-infer">inference</span>`;
      return `<li><span class="song">${esc(fixtureName(i.title))}</span><span class="song-meta">${meta}</span></li>`;
    }).join("")}</ol>` : `<p class="empty" style="color:#5A5470">Qloo returned no artists for these fans, so there's no DJ set to suggest.</p>`;
    return `<div class="setlist-wrap"><div class="setlist ${generic ? "generic" : ""}">
      <h2>DJ set between acts</h2>${body}</div></div>`;
  }

  function header(run) {
    const ctx = run.ctx || {};
    const bits = [];
    if (ctx.date) bits.push(`<span>${esc(fmtDate(ctx.date, true))}</span>`);
    bits.push(`<span>${esc(ctx.venue_name || "Any venue")}, ${esc(ctx.city)}</span>`);
    bits.push(`<span>${esc(ctx.headliner)}${ctx.support && ctx.support.length ? " with " + esc(ctx.support.join(", ")) : ""}</span>`);
    if (run.kind === "auto") bits.push(`<span>Planned automatically from the venue calendar</span>`);
    if (run.kind === "replan") bits.push(`<span>Re-planned after an outcome report</span>`);
    if (run.kind === "try") bits.push(`<span>One-off plan, drafted only</span>`);
    const sample = ctx.is_sample ? `<p class="meta sample-note">Sample calendar for the Convivium demo. Not a real tour date.</p>` : "";
    return `<div class="meta">${bits.join("")}</div>${sample}`;
  }

  function stepsLog(run, live) {
    const steps = run.steps || [];
    const rows = steps.map((s) => `<li><span class="dot"></span>
      <div><strong>${esc(s.label)}</strong>${(s.notes || []).map((n) => `<div class="notes">${esc(n)}</div>`).join("")}</div>
      <span class="when">${s.t.toFixed(1)}s</span></li>`);
    if (live) rows.push(`<li><span class="dot live"></span><div><strong>Working…</strong><div class="notes">The agent is calling Qloo and writing the plan.</div></div><span></span></li>`);
    return `<ul class="log">${rows.join("")}</ul>`;
  }

  function receipts(run) {
    const trace = run.trace || [];
    if (!trace.length) return `<p class="empty">No Qloo calls recorded yet.</p>`;
    const total = trace.reduce((a, t) => a + (t.ms || 0), 0);
    return `<p class="hint">${trace.length} calls, ${total.toLocaleString()} ms of Qloo time in total. Each row is a real request this run made${trace[0].data_source === "fixture" ? " (to offline fixtures)" : ""}.</p>
      <div class="scroll-x"><table><thead><tr><th>What for</th><th>Endpoint</th><th class="num">Results</th><th class="num">ms</th><th>Cache</th><th>Status</th></tr></thead>
      <tbody>${trace.map((t) => {
        const p = t.params || {};
        const ep = t.path + (p["filter.type"] ? ` ${p["filter.type"]}` : "") + (p["filter.tags"] ? ` + ${p["filter.tags"].split(":").pop()}` : "");
        return `<tr><td>${esc(t.purpose)}</td><td><code>${esc(ep)}</code></td><td class="num">${t.count}</td>
          <td class="num">${t.ms}</td><td>${t.cache ? "hit" : "miss"}</td><td>${esc(t.status)}</td></tr>`;
      }).join("")}</tbody></table></div>`;
  }

  function guardsHtml(meta) {
    const rows = [];
    for (const d of meta.dropped || []) rows.push(`<li><strong>${esc(d.title)}</strong> <span class="hint">(${esc(d.section)})</span>: ${esc(d.reason)}</li>`);
    for (const f of meta.fixes || []) rows.push(`<li>${esc(f)}</li>`);
    for (const v of meta.violations || []) rows.push(`<li class="form-error">Still open: ${esc(v)}</li>`);
    const head = `<p class="hint">Written by the ${esc(meta.composer)} composer in ${meta.attempts} draft${meta.attempts === 1 ? "" : "s"}. Every artist, brand and venue is checked against Qloo's response in code.</p>`;
    return head + (rows.length ? `<ul class="guards">${rows.join("")}</ul>` : `<p class="empty">Nothing removed or fixed: every grounded item cited a Qloo id and the venue rules held.</p>`);
  }

  function outboxHtml(run) {
    const out = run.outbox || [];
    if (!out.length) return `<p class="empty">No actions recorded.</p>`;
    const label = { partner_pitch: "Partner pitch", brand_pitch: "Brand pitch", plan_email: "Plan to ops", webhook: "Team channel" };
    return `<div class="scroll-x"><table><thead><tr><th>Action</th><th>To</th><th>Status</th></tr></thead><tbody>
      ${out.map((a) => `<tr><td>${esc(label[a.kind] || a.kind)}<div class="hint">${esc(a.subject)}</div></td>
        <td>${esc(fixtureName(a.recipient))}</td>
        <td><span class="st st-${esc(a.status)}">${esc(a.status)}</span><div class="hint">${esc(a.detail)}</div></td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function reportForm(run, plan) {
    if (!run.venue_id) return "";
    if (state.reported && state.reported.run === run.id) {
      const links = state.reported.runs.map((r, i) => `<a href="#run/${r}">re-plan ${i + 1}</a>`).join(", ");
      return `<section class="section"><h2>Report how it went</h2><div class="notice" role="status">Saved to venue memory.
        ${state.reported.runs.length ? `The agent is re-planning the next ${state.reported.runs.length} show${state.reported.runs.length === 1 ? "" : "s"}: ${links}.` : "There are no upcoming shows to re-plan."}</div></section>`;
    }
    const box = (name, items) => items.map((i) => `<label class="check"><input type="checkbox" name="${name}" value="${esc(i.title)}"> ${esc(i.title)}</label>`).join("");
    const sellable = [...plan.food, ...plan.drinks];
    return `<section class="section"><h2>Report how it went</h2>
      <form class="report" id="report-form">
        <p class="hint">Tell the agent what happened. It remembers, then re-plans this venue's next 3 shows on its own.</p>
        <fieldset><legend>Sold well</legend>${box("sold_well", sellable)}</fieldset>
        <fieldset><legend>Flopped</legend>${box("flopped", sellable)}</fieldset>
        <fieldset><legend>Declined to partner</legend>${box("declined_partners", [...plan.after_show, ...plan.brands])}</fieldset>
        <label class="form" style="display:grid;gap:4px">Anything else <textarea name="notes" rows="2" maxlength="2000"></textarea></label>
        <div><button type="submit" class="primary">Report how it went</button></div>
        <p class="form-error" id="report-error" role="alert" hidden></p>
      </form></section>`;
  }

  function renderMap(plan, pools) {
    if (state.map) { state.map.remove(); state.map = null; }
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
    const tiles = (state.status && state.status.map) || { url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", dark_filter: true,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' };
    el.classList.toggle("dark-tiles", !!tiles.dark_filter);
    L.tileLayer(tiles.url, { maxZoom: 19, subdomains: "abcd", attribution: tiles.attribution }).addTo(map);
    const pts = [];
    for (const p of heat) {
      const a = Math.max(0, Math.min(1, p.affinity || 0));
      L.circleMarker([p.lat, p.lon], { radius: 4 + a * 12, color: "#FF4F8B", weight: 1, fillColor: "#FF4F8B", fillOpacity: 0.12 + a * 0.35 })
        .bindTooltip(`Fan density (affinity ${fmtAff(p.affinity)})`).addTo(map);
      pts.push([p.lat, p.lon]);
    }
    for (const e of partners) {
      const name = fixtureName(e.name);
      L.marker([e.lat, e.lon], { icon: L.divIcon({ className: "partner-pin", html: esc(name.length > 26 ? name.slice(0, 25) + "…" : name) }), keyboard: true, title: e.name })
        .bindTooltip(`${e.name}: after-show partner (affinity ${fmtAff(e.affinity)})`).addTo(map);
      pts.push([e.lat, e.lon]);
    }
    if (pts.length === 1) map.setView(pts[0], 13); else map.fitBounds(pts, { padding: [24, 24], maxZoom: 14 });
  }

  function renderPlan(run) {
    const generic = state.mode === "without";
    const fullPlan = run.plan || {};
    const meta = fullPlan._meta || {};
    const plan = generic ? run.baseline : fullPlan;
    const idx = poolIndex(run.pools);
    state.idx = idx;
    const na = plan.non_alcoholic_share_pct;
    view.innerHTML = `
      ${header(run)}
      <h1 class="headline">${esc(plan.headline)}</h1>
      <p class="read">${esc(plan.audience_read)} <span class="tag-infer">inference${generic ? "" : " from Qloo demographics and tags"}</span></p>
      <div class="toggle" role="group" aria-label="Compare with and without Qloo">
        <button type="button" data-mode="with" aria-pressed="${!generic}">With Qloo</button>
        <button type="button" data-mode="without" aria-pressed="${generic}">Without Qloo</button>
      </div>
      ${generic ? `<p class="without-note">No Qloo data: every item here is a guess. Same task, same venue rules, ${run.baseline && meta.baseline_composer === "llm-without-qloo" ? "same LLM" : "a generic template"}, but no idea who the fans are.</p>` : ""}
      <div class="grid">
        <div>${setlist(plan.dj_set, idx, generic)}</div>
        <div class="stack">
          <section class="section"><h2>Food</h2>${itemsList(plan.food, idx, generic, "No food signal came back from Qloo.")}</section>
          <section class="section"><h2>Drinks</h2>
            <div class="na-bar"><div class="na-track" role="img" aria-label="${na}% of the drinks menu is non-alcoholic"><div class="na-fill" style="width:${na}%"></div></div>
            <div class="na-label">${na}% of the bar is non-alcoholic</div></div>
            ${itemsList(plan.drinks, idx, generic, "No drinks suggested.")}</section>
          <section class="section"><h2>Brand moments</h2>${itemsList(plan.brands, idx, generic, "Qloo returned no brands these fans over-index on.")}</section>
          <section class="section"><h2>After-show partners</h2>${itemsList(plan.after_show, idx, generic, "Qloo returned no bars or restaurants in this city for these fans.")}</section>
        </div>
      </div>
      <section class="section" style="margin-top:28px"><h2>Where the fans are</h2>
        <div class="map-wrap">
          ${generic ? `<p class="empty">Without Qloo there's no heatmap, so promotion is spread evenly and hopes for the best.</p>` : `<div id="map" role="region" aria-label="Map of where these fans are in ${esc(run.ctx.city)}"></div>`}
          <div><h3>Promo focus</h3><ul class="promo">${(plan.promo_focus || []).map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>
        </div></section>
      <section class="section" style="margin-top:28px"><h2>Run of show <span class="tag-infer">inference</span></h2>
        <ol class="ros">${(plan.run_of_show || []).map((r) => `<li><span>${esc(r)}</span></li>`).join("")}</ol></section>
      <section class="section" style="margin-top:28px"><h2>What the agent did</h2>
        <div class="cols">
          <div><h3 style="margin-bottom:10px">Step log</h3>${stepsLog(run, false)}</div>
          <div class="stack">
            <div><h3 style="margin-bottom:8px">Actions</h3>${outboxHtml(run)}</div>
            <div><h3 style="margin-bottom:8px">Guards</h3>${guardsHtml(meta)}</div>
            ${meta.memory_used && meta.memory_used.length ? `<div><h3 style="margin-bottom:8px">Memory it used</h3><ul class="guards">${meta.memory_used.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>` : ""}
          </div>
        </div></section>
      <section class="section" style="margin-top:28px"><h2>Qloo receipts</h2>${receipts(run)}</section>
      ${generic ? "" : reportForm(run, fullPlan)}`;
    if (!generic) renderMap(fullPlan, run.pools);
    else if (state.map) { state.map.remove(); state.map = null; }
  }

  function renderProgress(run) {
    view.innerHTML = `${header(run)}
      <h1 class="headline">Planning ${esc(run.ctx.headliner)} in ${esc(run.ctx.city)}…</h1>
      <p class="hint" role="status">The agent works on its own from here: resolve the lineup, ask Qloo what these fans love, write the plan, check it against the venue rules, then act.</p>
      <section class="section" style="margin-top:20px"><h2>What the agent is doing</h2>${stepsLog(run, true)}</section>
      <section class="section" style="margin-top:28px"><h2>Qloo receipts</h2>${receipts(run)}</section>`;
  }

  function renderError(run) {
    view.innerHTML = `${header(run)}
      <div class="error-box" role="alert" style="margin-top:18px"><h2>The agent couldn't plan this night</h2>
      <p>${esc(run.error || "Something went wrong.")}</p>
      <p class="hint">Try again from the form on the left. If the artist is niche, try their best-known name or another act on the bill.</p></div>
      <section class="section" style="margin-top:24px"><h2>What the agent did</h2>${stepsLog(run, false)}</section>
      <section class="section" style="margin-top:24px"><h2>Qloo receipts</h2>${receipts(run)}</section>`;
  }

  view.addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (!b || !state.run) return;
    state.mode = b.dataset.mode;
    renderPlan(state.run);
    const again = view.querySelector(`[data-mode="${state.mode}"]`);
    if (again) again.focus();
  });

  view.addEventListener("submit", async (e) => {
    if (e.target.id !== "report-form") return;
    e.preventDefault();
    const f = e.target;
    const vals = (n) => [...f.querySelectorAll(`input[name=${n}]:checked`)].map((i) => i.value);
    const body = { run_id: state.run.id, sold_well: vals("sold_well"), flopped: vals("flopped"),
                   declined_partners: vals("declined_partners"), notes: f.notes.value.trim() };
    if (!body.sold_well.length && !body.flopped.length && !body.declined_partners.length && !body.notes) {
      const err = $("#report-error"); err.textContent = "Tick at least one item or add a note."; err.hidden = false; return;
    }
    const btn = f.querySelector("button[type=submit]");
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      const res = await api(`/api/venues/${state.run.venue_id}/outcomes`, { method: "POST", body: JSON.stringify(body) });
      state.reported = { run: state.run.id, runs: res.replanning };
      renderPlan(state.run);
      loadVenues();
    } catch (ex) {
      const err = $("#report-error"); err.textContent = ex.message; err.hidden = false;
      btn.disabled = false; btn.textContent = "Report how it went";
    }
  });

  async function loadRun(id) {
    clearTimeout(state.pollTimer);
    let run;
    try {
      run = await api(`/api/runs/${encodeURIComponent(id)}`);
    } catch (e) {
      view.innerHTML = `<div class="error-box" role="alert"><h2>No plan here</h2><p>${esc(e.message)} The link may be from an older deploy; plan the show again from the form.</p></div>`;
      return;
    }
    if (state.runId !== id) return; // navigated away
    const key = `${run.status}|${(run.steps || []).length}|${(run.trace || []).length}|${run.updated}`;
    const changed = key !== state.lastKey;
    state.lastKey = key;
    state.run = run;
    if (run.status === "done") { if (changed) { renderPlan(run); loadVenues(); } }
    else if (run.status === "error") { if (changed) { renderError(run); loadVenues(); } }
    else {
      if (changed) renderProgress(run);
      state.pollTimer = setTimeout(() => loadRun(id), 1000);
    }
  }

  /* ---------- home ---------- */
  async function renderHome() {
    if (state.map) { state.map.remove(); state.map = null; }
    let runs = [];
    try { runs = (await api("/api/runs?limit=12")).filter((r) => r.status === "done"); } catch (_) { /* ignore */ }
    view.innerHTML = `<div class="home">
      <h1>The whole night around the show, from what the fans love.</h1>
      <p class="lead">Convivium is an agent for venue and promoter teams. It watches your calendar, asks Qloo what each headliner's fans love, and plans the food, drinks, DJ set, brand moments and after-show partners. Then it sends the plan and the partner pitches, and learns from how the night went.</p>
      <p class="lead">Try it now: type any artist and city on the left and press <strong>Plan the night</strong>. Or open one of the shows the agent already planned for the demo venues.</p>
      <div class="legend"><span><span class="chip" style="cursor:default">Qloo data <span class="aff">0.91</span></span> grounded in a Qloo response, with its affinity</span>
        <span><span class="tag-infer">inference</span> written by the agent</span></div>
      ${runs.length ? `<h2 style="margin-bottom:8px">Recently planned</h2><ul class="recent">${runs.map((r) => `
        <li><a href="#run/${r.id}"><span>${esc(r.ctx.headliner)} <span class="hint">in ${esc(r.ctx.city)}</span></span>
        <span class="hint">${r.kind === "try" ? "one-off" : r.kind === "replan" ? "re-planned" : "from a calendar"}</span></a></li>`).join("")}</ul>` : ""}
    </div>`;
  }

  /* ---------- routing ---------- */
  function route() {
    hideTip();
    const m = location.hash.match(/^#run\/([\w-]+)(\/without)?/);
    clearTimeout(state.pollTimer);
    document.body.classList.toggle("viewing-run", !!m);
    if (m) {
      if (state.runId !== m[1]) { state.mode = m[2] ? "without" : "with"; state.lastKey = ""; }
      state.runId = m[1];
      view.innerHTML = `<p class="hint" role="status">Loading the plan…</p>`;
      loadRun(m[1]);
    } else {
      state.runId = null;
      state.run = null;
      renderHome();
    }
    loadVenues();
  }

  window.addEventListener("hashchange", route);
  loadStatus();
  route();
  setInterval(() => { if (!document.hidden) loadVenues(); }, 8000);
})();
