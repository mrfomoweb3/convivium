from datetime import datetime, timezone

import pytest

from app import store
from app.calendar_watch import Watcher, parse_events, parse_lineup, resolve_feed_path

TITLES = [
    ("Little Simz with Sampha", "Little Simz", ["Sampha"]),
    ("Florence + the Machine", "Florence + the Machine", []),
    ("Tyler, The Creator", "Tyler, The Creator", []),
    ("Tyler, The Creator w/ Kali Uchis", "Tyler, The Creator", ["Kali Uchis"]),
    ("Fred again.. feat. Joy Anonymous", "Fred again..", ["Joy Anonymous"]),
    ("Bring Me The Horizon — Post Human Tour", "Bring Me The Horizon", []),
    ("Laufey (Live)", "Laufey", []),
    ("Turnstile supported by Militarie Gun, Ekkstacy & Mannequin Pussy", "Turnstile",
     ["Militarie Gun", "Ekkstacy", "Mannequin Pussy"]),
    ("Burna Boy ft. Wizkid and Tems", "Burna Boy", ["Wizkid", "Tems"]),
    ("Arctic Monkeys featuring The Hives + Fontaines D.C.", "Arctic Monkeys", ["The Hives", "Fontaines D.C."]),
    ("Mumford & Sons Support: Florence + the Machine", "Mumford & Sons", ["Florence + the Machine"]),
    ("Earth, Wind & Fire", "Earth, Wind & Fire", []),
    ("Peso Pluma - Éxodo World Tour", "Peso Pluma", []),
]


@pytest.mark.parametrize("title,head,support", TITLES)
def test_lineup_parser(title, head, support):
    assert parse_lineup(title) == (head, support)


def test_description_overrides_title():
    assert parse_lineup("A Big Night Out", "Headliner: YOASOBI\nSupport: Hiromi, Nils Frahm") == (
        "YOASOBI", ["Hiromi", "Nils Frahm"])


def ics(*events):
    body = "".join(
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:{start}\r\nSUMMARY:{summary}\r\nEND:VEVENT\r\n"
        for uid, start, summary in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n{body}END:VCALENDAR\r\n"


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_past_events_are_ignored():
    evs = parse_events(ics(("1", "20250101T200000Z", "Old Show"), ("2", "20261201T200000Z", "New Show")), NOW)
    assert [e["headliner"] for e in evs] == ["New Show"]


def test_local_paths_outside_project_are_refused():
    with pytest.raises(ValueError):
        resolve_feed_path("/etc/passwd")
    with pytest.raises(ValueError):
        resolve_feed_path("../../../../etc/hosts")
    assert resolve_feed_path("samples/kiln-live.ics").name == "kiln-live.ics"


async def test_change_detection(monkeypatch):
    from app import calendar_watch

    feed = {"text": ics(("1", "20991201T200000Z", "Laufey"))}

    async def fake_fetch(url):
        return feed["text"]

    monkeypatch.setattr(calendar_watch, "fetch_feed", fake_fetch)
    v = store.create_venue("Test Hall", "London", "https://example.com/cal.ics", {})
    w = Watcher()
    assert len(await w.sync_venue(v["id"])) == 1  # new event plans
    await w.idle()
    assert await w.sync_venue(v["id"]) == []  # unchanged event doesn't
    feed["text"] = ics(("1", "20991201T200000Z", "Laufey with Sampha"))
    assert len(await w.sync_venue(v["id"])) == 1  # changed title re-plans
    await w.idle()
    feed["text"] = ics(("1", "20991202T200000Z", "Laufey with Sampha"))
    assert len(await w.sync_venue(v["id"])) == 1  # changed date re-plans
    await w.idle()
    runs = store.list_runs(venue_id=v["id"])
    assert len(runs) == 3 and all(r["status"] == "done" and r["kind"] == "auto" for r in runs)
