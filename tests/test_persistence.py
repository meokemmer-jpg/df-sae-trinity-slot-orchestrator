"""Tests fuer Persistence-Layer + 200-Slot-Load-Test [CRUX-MK].

Pflicht-Tests (W46-E Patch-2):
1. test_initial_load_200_slots_x_3_variants - Vollstaendige Init persistiert
2. test_concurrent_voting_under_load - 50T+ Threads concurrent
3. test_relegation_cycle_persists - Promotion-Cycle persistiert
4. test_recovery_after_restart - Load-after-Restart funktional
5. test_atomic_state_update - Atomic q_norm + promote

Plus K11-Concurrency (concurrency-mandatory-tests.md):
- Race-on-Shared-State (50+ Threads)
- Conservation-Law (alle Updates erfolgreich)
- TOCTOU-Detection
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.persistence import SlotRecord, SlotStateStore, get_default_store
from src.trinity_slot_manager import (
    AGENTS_TOTAL,
    AgentID,
    AgentState,
    SLOTS_TOTAL,
    T_CAP,
    TrinitySlotManager,
    VARIANTS_PER_SLOT,
    Variant,
)
from src.voting_engine import VotingEngine


@pytest.fixture
def tmp_store(tmp_path: Path) -> SlotStateStore:
    """Fresh isolated SlotStateStore per test (avoid cross-test contamination)."""
    db_path = tmp_path / f"test-trinity-{uuid.uuid4().hex[:8]}.db"
    return SlotStateStore(db_path=db_path)


@pytest.fixture
def init_manager() -> TrinitySlotManager:
    """Initialized TrinitySlotManager (600 Agenten)."""
    return TrinitySlotManager()


# ============================================================
# Pflicht-Test 1: Initial-Load 200 Slots × 3 Variants
# ============================================================

def test_initial_load_200_slots_x_3_variants(tmp_store: SlotStateStore,
                                                init_manager: TrinitySlotManager) -> None:
    """Pflicht-Test: alle 200 Slots × 3 Varianten = 600 Agenten persistiert."""
    # Sammle alle 600 AgentStates aus Manager
    all_states: list[AgentState] = []
    for slot_id in range(SLOTS_TOTAL):
        all_states.extend(init_manager.get_slot_agents(slot_id))

    assert len(all_states) == AGENTS_TOTAL, f"Erwartet {AGENTS_TOTAL}, gefunden {len(all_states)}"

    # Persist all in single transaction
    persisted_count = tmp_store.persist_all(all_states)
    assert persisted_count == AGENTS_TOTAL

    # Verify count in DB
    assert tmp_store.count_persisted() == AGENTS_TOTAL

    # Verify exactly SLOTS_TOTAL incumbents (Conservative-Default)
    assert tmp_store.count_incumbents() == SLOTS_TOTAL

    # Load + verify Round-Trip
    records = tmp_store.load_all_slots()
    assert len(records) == AGENTS_TOTAL

    # Pro Slot 3 Varianten + 1 incumbent (Conservative)
    by_slot: dict[int, list[SlotRecord]] = {}
    for r in records:
        by_slot.setdefault(r.slot_id, []).append(r)
    assert len(by_slot) == SLOTS_TOTAL
    for slot_id, slot_records in by_slot.items():
        assert len(slot_records) == VARIANTS_PER_SLOT
        incumbents = [r for r in slot_records if r.is_incumbent]
        assert len(incumbents) == 1, f"Slot {slot_id} hat {len(incumbents)} incumbents"
        assert incumbents[0].variant == Variant.CONSERVATIVE


# ============================================================
# Pflicht-Test 2: Concurrent-Voting under Load (50 Threads)
# ============================================================

def test_concurrent_voting_under_load(tmp_store: SlotStateStore,
                                        init_manager: TrinitySlotManager) -> None:
    """Pflicht-Test: 50 Threads concurrent q_norm-Updates → Conservation-Law gehalten."""
    # Init alle 600 Agenten
    all_states = []
    for slot_id in range(SLOTS_TOTAL):
        all_states.extend(init_manager.get_slot_agents(slot_id))
    tmp_store.persist_all(all_states)
    assert tmp_store.count_persisted() == AGENTS_TOTAL

    # 50 Threads: jeder updated 4 zufaellige Slots (200 Updates total)
    N_THREADS = 50
    UPDATES_PER_THREAD = 4
    barrier = threading.Barrier(N_THREADS)
    errors: list[tuple[int, str]] = []

    def worker(worker_id: int) -> int:
        """Each worker performs UPDATES_PER_THREAD q_norm updates."""
        try:
            barrier.wait()  # synchronize all threads to start together
            for i in range(UPDATES_PER_THREAD):
                slot_id = (worker_id * UPDATES_PER_THREAD + i) % SLOTS_TOTAL
                variant = list(Variant)[i % VARIANTS_PER_SLOT]
                agent_id = AgentID(slot_id=slot_id, variant=variant)
                # q_norm cycle: -1.5 .. 1.5
                new_q = ((worker_id + i) % 7 - 3) * 0.5
                tmp_store.update_q_norm_atomic(agent_id, new_q)
            return UPDATES_PER_THREAD
        except Exception as e:
            errors.append((worker_id, str(e)))
            return 0

    with ThreadPoolExecutor(max_workers=N_THREADS) as executor:
        results = list(executor.map(worker, range(N_THREADS)))

    # Conservation-Law: alle Threads erfolgreich (kein Lock-Conflict-Crash)
    successful = sum(results)
    assert len(errors) == 0, f"Race-Conditions: {errors}"
    assert successful == N_THREADS * UPDATES_PER_THREAD, \
        f"Erwartet {N_THREADS * UPDATES_PER_THREAD} Updates, war {successful}"

    # DB-Integrity: count == AGENTS_TOTAL (keine Duplikate, keine Verluste)
    assert tmp_store.count_persisted() == AGENTS_TOTAL


# ============================================================
# Pflicht-Test 3: Relegation-Cycle persists
# ============================================================

def test_relegation_cycle_persists(tmp_store: SlotStateStore,
                                     init_manager: TrinitySlotManager) -> None:
    """Pflicht-Test: Promotion + Demotion ueber Cycle → State persistiert."""
    # Init
    all_states = []
    for slot_id in range(SLOTS_TOTAL):
        all_states.extend(init_manager.get_slot_agents(slot_id))
    tmp_store.persist_all(all_states)

    # Slot 42: promote AGGRESSIVE-Variante
    target_id = AgentID(slot_id=42, variant=Variant.AGGRESSIVE)
    tmp_store.promote_atomic(target_id)

    # Verify Persistence
    slot_records = tmp_store.load_slot(42)
    assert len(slot_records) == VARIANTS_PER_SLOT
    incumbents = [r for r in slot_records if r.is_incumbent]
    assert len(incumbents) == 1
    assert incumbents[0].variant == Variant.AGGRESSIVE

    # Conservative ist demoted
    conservative = [r for r in slot_records if r.variant == Variant.CONSERVATIVE][0]
    assert not conservative.is_incumbent

    # Re-Promotion: CONTRARIAN winner
    contrarian_id = AgentID(slot_id=42, variant=Variant.CONTRARIAN)
    tmp_store.promote_atomic(contrarian_id)
    slot_records = tmp_store.load_slot(42)
    incumbents = [r for r in slot_records if r.is_incumbent]
    assert len(incumbents) == 1
    assert incumbents[0].variant == Variant.CONTRARIAN

    # Global incumbents-count unveraendert (jeder Slot hat genau 1)
    assert tmp_store.count_incumbents() == SLOTS_TOTAL


# ============================================================
# Pflicht-Test 4: Recovery after Restart
# ============================================================

def test_recovery_after_restart(tmp_path: Path,
                                  init_manager: TrinitySlotManager) -> None:
    """Pflicht-Test: Schreibe State → Schliesse Store → Oeffne neu → State wiederhergestellt."""
    db_path = tmp_path / "recovery-test.db"

    # Phase 1: Init + Persist + Update
    store1 = SlotStateStore(db_path=db_path)
    all_states = []
    for slot_id in range(SLOTS_TOTAL):
        all_states.extend(init_manager.get_slot_agents(slot_id))
    store1.persist_all(all_states)

    # Mache Aenderungen: 10 Slots promote AGGRESSIVE
    for slot_id in [5, 12, 23, 47, 89, 100, 134, 167, 188, 199]:
        agent_id = AgentID(slot_id=slot_id, variant=Variant.AGGRESSIVE)
        store1.promote_atomic(agent_id)
        # auch q_norm-Update
        store1.update_q_norm_atomic(agent_id, 1.5)

    # Persist count check
    assert store1.count_persisted() == AGENTS_TOTAL
    pre_restart_records = store1.load_all_slots()

    # Phase 2: "Restart" - neuer Store auf gleicher DB
    del store1
    store2 = SlotStateStore(db_path=db_path)

    # Recovery: load all
    post_restart_records = store2.load_all_slots()
    assert len(post_restart_records) == AGENTS_TOTAL
    assert store2.count_incumbents() == SLOTS_TOTAL

    # Verify die 10 promoted-Slots haben AGGRESSIVE als incumbent + q_norm=1.5
    for slot_id in [5, 12, 23, 47, 89, 100, 134, 167, 188, 199]:
        slot_records = store2.load_slot(slot_id)
        incumbents = [r for r in slot_records if r.is_incumbent]
        assert len(incumbents) == 1
        assert incumbents[0].variant == Variant.AGGRESSIVE
        assert incumbents[0].q_norm == 1.5

    # Andere Slots (z.B. Slot 0): CONSERVATIVE bleibt incumbent
    slot_0_records = store2.load_slot(0)
    incumbents_0 = [r for r in slot_0_records if r.is_incumbent]
    assert len(incumbents_0) == 1
    assert incumbents_0[0].variant == Variant.CONSERVATIVE


# ============================================================
# Pflicht-Test 5: Atomic State-Update (BEGIN IMMEDIATE)
# ============================================================

def test_atomic_state_update(tmp_store: SlotStateStore,
                              init_manager: TrinitySlotManager) -> None:
    """Pflicht-Test: q_norm-Update + promote_atomic sind atomic (kein Half-Write)."""
    # Init
    all_states = []
    for slot_id in range(SLOTS_TOTAL):
        all_states.extend(init_manager.get_slot_agents(slot_id))
    tmp_store.persist_all(all_states)

    # Atomic update: q_norm
    agent_id = AgentID(slot_id=10, variant=Variant.CONTRARIAN)
    tmp_store.update_q_norm_atomic(agent_id, -1.5)
    record = [r for r in tmp_store.load_slot(10) if r.variant == Variant.CONTRARIAN][0]
    assert record.q_norm == -1.5

    # Pre-condition violation: out-of-range q_norm
    with pytest.raises(ValueError):
        tmp_store.update_q_norm_atomic(agent_id, 2.5)
    with pytest.raises(ValueError):
        tmp_store.update_q_norm_atomic(agent_id, -2.5)

    # State unveraendert nach Failed-Update
    record_after = [r for r in tmp_store.load_slot(10) if r.variant == Variant.CONTRARIAN][0]
    assert record_after.q_norm == -1.5  # immer noch der gueltige Wert

    # Atomic promote: genau 1 Incumbent nach Promotion
    tmp_store.promote_atomic(agent_id)
    slot_records = tmp_store.load_slot(10)
    incumbents = [r for r in slot_records if r.is_incumbent]
    assert len(incumbents) == 1
    assert incumbents[0].variant == Variant.CONTRARIAN


# ============================================================
# Bonus-Test: Backend-Switch Factory (per rules/backend-switch-multi-driver.md)
# ============================================================

def test_backend_switch_factory_default_sqlite(tmp_path: Path) -> None:
    """Test: get_default_store() defaults to SQLite-Backend."""
    db_path = tmp_path / "factory-test.db"
    store = get_default_store(db_path=db_path)
    assert isinstance(store, SlotStateStore)
    assert store.count_persisted() == 0  # fresh


def test_backend_switch_factory_postgres_rejected() -> None:
    """Test: postgres-backend raises ValueError (Welle-22+ pending)."""
    with pytest.raises(ValueError, match="Postgres-Backend pending"):
        get_default_store(backend="postgres")


def test_backend_switch_factory_unknown_rejected() -> None:
    """Test: unbekanntes Backend raises ValueError."""
    with pytest.raises(ValueError, match="Unbekanntes Backend"):
        get_default_store(backend="mongodb")


# ============================================================
# Bonus-Test: Concurrent-Promotion (TOCTOU-Test)
# ============================================================

def test_concurrent_promotion_toctou_safe(tmp_store: SlotStateStore,
                                            init_manager: TrinitySlotManager) -> None:
    """TOCTOU-Test: 30 Threads concurrent promote → Conservation-Law gilt."""
    all_states = []
    for slot_id in range(SLOTS_TOTAL):
        all_states.extend(init_manager.get_slot_agents(slot_id))
    tmp_store.persist_all(all_states)

    N_THREADS = 30
    barrier = threading.Barrier(N_THREADS)
    errors: list[str] = []

    def worker(worker_id: int) -> bool:
        try:
            barrier.wait()
            slot_id = worker_id % SLOTS_TOTAL
            variant = list(Variant)[worker_id % VARIANTS_PER_SLOT]
            agent_id = AgentID(slot_id=slot_id, variant=variant)
            tmp_store.promote_atomic(agent_id)
            return True
        except Exception as e:
            errors.append(str(e))
            return False

    with ThreadPoolExecutor(max_workers=N_THREADS) as executor:
        results = list(executor.map(worker, range(N_THREADS)))

    assert len(errors) == 0, f"TOCTOU-Errors: {errors}"
    assert all(results), "Some promotions failed"

    # Conservation: jeder Slot hat genau 1 incumbent
    assert tmp_store.count_incumbents() == SLOTS_TOTAL
