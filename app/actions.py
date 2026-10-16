"""Actuators: partner pitches, plan delivery by email and webhook, and the outbox.

Every action is recorded in the outbox with its real status: sent, queued,
failed, skipped or drafted. Nothing is ever reported as sent unless the
provider accepted it.
"""
from __future__ import annotations

import httpx

from . import store
from .config import settings
from .planner import Plan, clean


def plan_text(plan: Plan, ctx: dict, run_id: str) -> str:
    s = settings()
    lines = [plan.headline, "", f"{ctx['headliner']} at {ctx.get('venue_name') or 'your venue'}, {ctx['city']}"]
    if ctx.get("date"):
        lines.append(ctx["date"])
    lines += ["", plan.audience_read, ""]

    def section(title: str, items) -> None:
        lines.append(title)
        for i in items:
            lines.append(f"- {i.title}" + (f": {i.detail}" if i.detail else ""))
        lines.append("")

    section("Food", plan.food)
    section(f"Drinks ({plan.non_alcoholic_share_pct}% non-alcoholic)", plan.drinks)
    lines.append("DJ set")
    lines += [f"{n}. {clean(i.title)}" for n, i in enumerate(plan.dj_set, 1)]
    lines.append("")
    section("Brand moments", plan.brands)
    section("After-show partners", plan.after_show)
    lines.append("Promo focus")
    lines += [f"- {p}" for p in plan.promo_focus]
    lines += ["", "Run of show"] + [f"{n}. {r}" for n, r in enumerate(plan.run_of_show, 1)]
    lines += ["", f"Full plan with Qloo evidence: {s.public_url}/#run/{run_id}",
              "Planned by Convivium from Qloo taste data. Items marked as inference are suggestions for your team."]
    return "\n".join(lines)


def pitch_text(name: str, kind: str, ctx: dict, affinity: float | None) -> tuple[str, str]:
    venue = ctx.get("venue_name") or "our venue"
    subject = f"{ctx['headliner']} fans at {venue} love {clean(name)}"
    score = f" (Qloo affinity {affinity:.2f})" if isinstance(affinity, (int, float)) else ""
    when = f" on {ctx['date']}" if ctx.get("date") else ""
    body = (
        f"Hello {clean(name)} team,\n\n"
        f"{venue} in {ctx['city']} is hosting {ctx['headliner']}{when}. Qloo's taste data shows this audience "
        f"has a strong affinity for you{score}.\n\n"
        + ("We'd love to send fans your way after the show: a walk-over card, a mention from the stage host and "
           "a shared offer for ticket holders.\n\n" if kind == "partner" else
           "We'd love to talk about a brand moment during the night that fits this crowd.\n\n")
        + f"Reply to this email if you're interested.\n\n{venue}"
    )
    return subject, body


async def send_email(to: str, subject: str, text: str) -> tuple[str, str]:
    s = settings()
    if not s.resend_api_key:
        return "queued", "RESEND_API_KEY not set; ready to forward from the outbox"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.resend.com/emails",
                                  headers={"Authorization": f"Bearer {s.resend_api_key}"},
                                  json={"from": s.mail_from, "to": [to], "subject": subject, "text": text})
        if r.status_code < 300:
            return "sent", f"Resend accepted it (id {r.json().get('id', '?')})"
        return "failed", f"Resend returned HTTP {r.status_code}: {r.text[:160]}"
    except httpx.HTTPError as exc:
        return "failed", f"Resend request failed: {exc.__class__.__name__}"


async def post_webhook(text: str) -> tuple[str, str]:
    url = settings().webhook_url
    if not url:
        return "skipped", "WEBHOOK_URL not configured"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json={"text": text, "content": text[:1900]})
        if r.status_code < 300:
            return "sent", f"Webhook accepted it (HTTP {r.status_code})"
        return "failed", f"Webhook returned HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        return "failed", f"Webhook request failed: {exc.__class__.__name__}"


async def run_actions(plan: Plan, ctx: dict, policy, pools: dict, run_id: str, venue: dict | None,
                      deliver: bool) -> list[dict]:
    venue_id = venue["id"] if venue else None
    contacts = {k.lower().strip(): v for k, v in (policy.partner_contacts or {}).items()}
    affinity = {e["id"]: e.get("affinity") for key in ("brands", "eats", "bars") for e in pools.get(key, [])}
    actions: list[dict] = []

    targets = [(i, "partner") for i in plan.after_show] + [(i, "brand") for i in plan.brands[:3]]
    for item, kind in targets:
        subject, body = pitch_text(item.title, kind, ctx, affinity.get(item.evidence[0]) if item.evidence else None)
        to = contacts.get(item.title.lower().strip()) or contacts.get(clean(item.title).lower())
        if not deliver:
            status, detail = "drafted", "Public try-it run: drafted only, never sent"
        elif to:
            status, detail = await send_email(to, subject, body)
        else:
            status, detail = "queued", "Ready to forward (no contact on file)"
        actions.append(store.add_outbox(run_id, venue_id, f"{kind}_pitch", to or clean(item.title), subject, body,
                                        status, detail))

    text = plan_text(plan, ctx, run_id)
    subject = f"Show plan: {ctx['headliner']}, {ctx.get('venue_name') or ctx['city']}"
    ops = (venue or {}).get("ops_email") or ""
    if not deliver:
        status, detail = "drafted", "Public try-it run: drafted only, never sent"
    elif ops:
        status, detail = await send_email(ops, subject, text)
    else:
        status, detail = "skipped", "No ops email on file for this venue"
    actions.append(store.add_outbox(run_id, venue_id, "plan_email", ops or "venue ops", subject, text, status, detail))

    if deliver:
        status, detail = await post_webhook(text)
    else:
        status, detail = "drafted", "Public try-it run: drafted only, never posted"
    actions.append(store.add_outbox(run_id, venue_id, "webhook", "team channel", subject, text, status, detail))
    return actions
