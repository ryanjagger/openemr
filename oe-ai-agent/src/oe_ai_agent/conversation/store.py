"""In-memory conversation cache.

The chat surface is ephemeral by design (per ARCH chat addendum): no DB
writes, no disk persistence. The store holds the cached FHIR rows from
turn 1 plus a turn counter so a runaway conversation can't bill the user
forever. A single process owns the dict; horizontal scaling would need
sticky routing or a Redis swap-in.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache

from oe_ai_agent.schemas.tool_results import TypedRow


class TurnLimitError(RuntimeError):
    """Raised when a conversation would exceed its hard turn cap."""


@dataclass
class ConversationEntry:
    conversation_id: str
    patient_uuid: str
    cached_context: list[TypedRow]
    created_at: datetime
    last_seen_at: datetime
    turn_count: int = 0
    messages: list[dict[str, object]] = field(default_factory=list)


class ConversationStore:
    DEFAULT_TTL = timedelta(minutes=30)
    DEFAULT_MAX_ENTRIES = 256
    DEFAULT_MAX_TURNS = 20

    def __init__(
        self,
        *,
        ttl: timedelta = DEFAULT_TTL,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._max_turns = max_turns
        self._lock = asyncio.Lock()
        self._entries: dict[str, ConversationEntry] = {}

    async def get_or_create(
        self,
        conversation_id: str | None,
        patient_uuid: str,
    ) -> ConversationEntry:
        async with self._lock:
            self._evict_expired_locked()
            if conversation_id is not None:
                existing = self._entries.get(conversation_id)
                if existing is not None and existing.patient_uuid == patient_uuid:
                    existing.last_seen_at = datetime.now(tz=UTC)
                    return existing
            # Mint a fresh id when no id was provided, when the id is unknown,
            # OR when an existing entry is bound to a different patient. The
            # last case is a security guard: never let a caller resume a
            # cached chart by guessing another conversation's id.
            mismatch = (
                conversation_id is not None
                and conversation_id in self._entries
                and self._entries[conversation_id].patient_uuid != patient_uuid
            )
            new_id = (
                uuid.uuid4().hex
                if mismatch or conversation_id is None
                else conversation_id
            )
            now = datetime.now(tz=UTC)
            entry = ConversationEntry(
                conversation_id=new_id,
                patient_uuid=patient_uuid,
                cached_context=[],
                created_at=now,
                last_seen_at=now,
            )
            self._entries[new_id] = entry
            self._enforce_capacity_locked()
            return entry

    async def increment_turn(self, conversation_id: str) -> int:
        async with self._lock:
            entry = self._entries.get(conversation_id)
            if entry is None:
                raise KeyError(conversation_id)
            if entry.turn_count >= self._max_turns:
                raise TurnLimitError(
                    f"conversation {conversation_id} hit the {self._max_turns}-turn cap",
                )
            entry.turn_count += 1
            entry.last_seen_at = datetime.now(tz=UTC)
            return entry.turn_count

    async def update_context(
        self,
        conversation_id: str,
        rows: list[TypedRow],
    ) -> None:
        """Append rows to the cached context, deduplicating by ResourceType/id."""
        async with self._lock:
            entry = self._entries.get(conversation_id)
            if entry is None:
                raise KeyError(conversation_id)
            seen = {(r.resource_type, r.resource_id) for r in entry.cached_context}
            for row in rows:
                key = (row.resource_type, row.resource_id)
                if key not in seen:
                    entry.cached_context.append(row)
                    seen.add(key)

    async def append_message(
        self,
        conversation_id: str,
        message: dict[str, object],
    ) -> None:
        async with self._lock:
            entry = self._entries.get(conversation_id)
            if entry is None:
                raise KeyError(conversation_id)
            entry.messages.append(message)

    async def drop(self, conversation_id: str) -> None:
        async with self._lock:
            self._entries.pop(conversation_id, None)

    def _evict_expired_locked(self) -> None:
        now = datetime.now(tz=UTC)
        expired = [
            cid
            for cid, entry in self._entries.items()
            if now - entry.last_seen_at > self._ttl
        ]
        for cid in expired:
            del self._entries[cid]

    def _enforce_capacity_locked(self) -> None:
        if len(self._entries) <= self._max_entries:
            return
        # Drop oldest by last_seen_at until under cap.
        ordered = sorted(self._entries.items(), key=lambda kv: kv[1].last_seen_at)
        to_drop = len(self._entries) - self._max_entries
        for cid, _ in ordered[:to_drop]:
            del self._entries[cid]


@cache
def get_default_store() -> ConversationStore:
    """Process-wide singleton used by the FastAPI app."""
    return ConversationStore()
