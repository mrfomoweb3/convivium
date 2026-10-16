"""Venue memory: Mem0 when MEM0_API_KEY is set, SQLite otherwise.

Structured lessons (declined partners, sold well, flopped, past partners) always
live in SQLite too, so the planner can apply them deterministically.
"""
from __future__ import annotations

import asyncio
import logging

from . import store
from .config import settings
from .planner import empty_lessons

log = logging.getLogger(__name__)

LESSON_KINDS = ("declined", "sold_well", "flopped", "past_partners")


class VenueMemory:
    def __init__(self) -> None:
        self._mem0 = None
        key = settings().mem0_api_key
        if key:
            try:
                from mem0 import MemoryClient

                self._mem0 = MemoryClient(api_key=key)
            except Exception as exc:
                log.warning("Mem0 unavailable, using SQLite: %s", exc)

    @property
    def backend(self) -> str:
        return "mem0" if self._mem0 is not None else "sqlite"

    async def add_note(self, venue_id: str, text: str) -> None:
        store.add_memory(venue_id, text)
        if self._mem0 is not None:
            try:
                await asyncio.to_thread(self._mem0.add, [{"role": "user", "content": text}],
                                        user_id=f"venue-{venue_id}")
            except Exception as exc:
                log.warning("Mem0 add failed: %s", exc)

    async def recall(self, venue_id: str, query: str, limit: int = 6) -> list[str]:
        if self._mem0 is not None:
            try:
                res = await asyncio.to_thread(self._mem0.search, query, user_id=f"venue-{venue_id}", limit=limit)
                rows = res.get("results", res) if isinstance(res, dict) else res
                texts = [r.get("memory") or r.get("text") for r in rows or [] if isinstance(r, dict)]
                texts = [t for t in texts if t]
                if texts:
                    return texts[:limit]
            except Exception as exc:
                log.warning("Mem0 search failed, falling back to SQLite: %s", exc)
        return [m["text"] for m in store.list_memories(venue_id, limit)]

    def lessons(self, venue_id: str | None) -> dict:
        out = empty_lessons()
        if not venue_id:
            return out
        for row in store.list_lessons(venue_id):
            bucket = out.setdefault(row["kind"], [])
            if row["value"] not in bucket:
                bucket.append(row["value"])
        return out

    def add_lesson(self, venue_id: str, kind: str, value: str, run_id: str | None = None) -> None:
        if kind not in LESSON_KINDS or not value.strip():
            return
        store.add_lesson(venue_id, kind, value.strip(), run_id)


_MEMORY: VenueMemory | None = None


def get_memory() -> VenueMemory:
    global _MEMORY
    if _MEMORY is None:
        _MEMORY = VenueMemory()
    return _MEMORY


def reset_memory() -> None:
    global _MEMORY
    _MEMORY = None
