"""Tests for the in-memory conversation store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from oe_ai_agent.conversation import ConversationStore, TurnLimitError
from oe_ai_agent.schemas.tool_results import TypedRow

PATIENT = "patient-uuid-1"


def _row(rid: str, resource_type: str = "MedicationRequest") -> TypedRow:
    return TypedRow(
        resource_type=resource_type,
        resource_id=rid,
        patient_id=PATIENT,
        last_updated=datetime.now(tz=UTC),
        fields={},
    )


@pytest.mark.asyncio
async def test_get_or_create_mints_new_id_when_none() -> None:
    store = ConversationStore()
    entry = await store.get_or_create(None, PATIENT)
    assert entry.conversation_id
    assert entry.patient_uuid == PATIENT
    assert entry.turn_count == 0


@pytest.mark.asyncio
async def test_get_or_create_returns_same_entry_for_same_id() -> None:
    store = ConversationStore()
    entry1 = await store.get_or_create(None, PATIENT)
    entry2 = await store.get_or_create(entry1.conversation_id, PATIENT)
    assert entry1 is entry2


@pytest.mark.asyncio
async def test_get_or_create_mints_new_id_when_patient_mismatch() -> None:
    """Don't reuse a cache entry across patients even if the id matches —
    chart caches contain PHI that must not cross patient boundaries."""
    store = ConversationStore()
    entry1 = await store.get_or_create(None, PATIENT)
    entry2 = await store.get_or_create(entry1.conversation_id, "other-patient")
    assert entry2.conversation_id != entry1.conversation_id


@pytest.mark.asyncio
async def test_increment_turn_blocks_at_cap() -> None:
    store = ConversationStore(max_turns=2)
    entry = await store.get_or_create(None, PATIENT)
    await store.increment_turn(entry.conversation_id)
    await store.increment_turn(entry.conversation_id)
    with pytest.raises(TurnLimitError):
        await store.increment_turn(entry.conversation_id)


@pytest.mark.asyncio
async def test_update_context_dedupes_by_resource_type_and_id() -> None:
    store = ConversationStore()
    entry = await store.get_or_create(None, PATIENT)
    await store.update_context(entry.conversation_id, [_row("a"), _row("b")])
    await store.update_context(
        entry.conversation_id,
        [_row("b"), _row("b", "Observation"), _row("c")],
    )
    assert [(r.resource_type, r.resource_id) for r in entry.cached_context] == [
        ("MedicationRequest", "a"),
        ("MedicationRequest", "b"),
        ("Observation", "b"),
        ("MedicationRequest", "c"),
    ]


@pytest.mark.asyncio
async def test_expired_entries_are_evicted() -> None:
    store = ConversationStore(ttl=timedelta(seconds=0))
    entry = await store.get_or_create(None, PATIENT)
    # Force expiry by shifting last_seen_at backwards.
    entry.last_seen_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    new_entry = await store.get_or_create(entry.conversation_id, PATIENT)
    assert new_entry is not entry
    assert new_entry.turn_count == 0


@pytest.mark.asyncio
async def test_capacity_evicts_oldest() -> None:
    store = ConversationStore(max_entries=2)
    a = await store.get_or_create(None, PATIENT)
    b = await store.get_or_create(None, PATIENT)
    # Make `a` older than `b` and `c`.
    a.last_seen_at = datetime.now(tz=UTC) - timedelta(minutes=10)
    c = await store.get_or_create(None, PATIENT)
    assert a.conversation_id not in {
        entry.conversation_id for entry in (b, c)
    }
    # Re-fetching `a`'s id should mint a fresh entry since `a` was evicted.
    revived = await store.get_or_create(a.conversation_id, PATIENT)
    assert revived.conversation_id == a.conversation_id
    assert revived.turn_count == 0
