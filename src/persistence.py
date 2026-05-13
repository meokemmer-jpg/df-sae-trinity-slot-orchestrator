"""SQLite-Backend fuer Trinity-Slot-State [CRUX-MK].

Persistence-Layer fuer 200 Slots × 3 Varianten = 600 Agenten.
Pattern: persistent-state-sqlite-pattern (W19-C SQLiteStore + W46-E DurableEventStore).

Schema:
    slots (
        slot_id INT,
        variant TEXT,
        agent_id TEXT,            -- "{slot_id}:{variant}"
        q_norm REAL,
        t_budget INT,
        w REAL,
        f_cum REAL,
        is_incumbent INT,         -- 0/1 boolean
        updated_ts REAL,
        PRIMARY KEY (slot_id, variant)
    )

Pflicht-Properties (Welle-46-E Patch-2):
- WAL-mode + per-DB-File-Lock (Thread-Safety)
- Atomic-Update via BEGIN IMMEDIATE
- Recovery-After-Restart via load_all_slots()
- Concurrent-Voting unter Last (50T+ Threads)
- Backward-compatible: optional, default None = in-memory-only

Adressiert: W46-E Patch-2 Cross-LLM-Verdict MODIFY-AT-RISK (Skeleton-Status -> Persistence-Layer).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .trinity_slot_manager import (
    AGENTS_TOTAL,
    AgentID,
    AgentState,
    SLOTS_TOTAL,
    Variant,
)


# Per-DB-File Lock-Registry (Thread-safe SQLite access)
_DB_LOCKS: dict[str, threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()


def _get_db_lock(db_path: str) -> threading.Lock:
    """Hole per-File-Lock fuer SQLite (Thread-Safety)."""
    with _REGISTRY_LOCK:
        lock = _DB_LOCKS.get(db_path)
        if lock is None:
            lock = threading.Lock()
            _DB_LOCKS[db_path] = lock
        return lock


@dataclass(frozen=True)
class SlotRecord:
    """Persisted Slot-Record (immutable)."""
    slot_id: int
    variant: Variant
    q_norm: float
    t_budget: int
    w: float
    f_cum: float
    is_incumbent: bool
    updated_ts: float


class SlotStateStore:
    """SQLite-Backend fuer Trinity-Slot-State.

    Pre-Conditions:
    - db_path (oder Default ~/.df-state/sae-trinity-slot.db) ist beschreibbar
    - Schema wird via _init_schema() initialisiert (idempotent)

    Post-Conditions:
    - persist_slot_state(): atomic INSERT OR REPLACE in DB
    - load_all_slots(): vollstaendige Recovery aller persistierten Records
    - count_persisted(): exact count of records in slots table

    Pattern: WAL-mode + per-DB-File-Lock + BEGIN IMMEDIATE.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".df-state" / "sae-trinity-slot.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _get_db_lock(str(self.db_path))
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        """Connect mit WAL-mode + 30s Timeout (Thread-Safety)."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        """Initialize Schema (idempotent)."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS slots (
                        slot_id INTEGER NOT NULL,
                        variant TEXT NOT NULL,
                        q_norm REAL NOT NULL DEFAULT 0.0,
                        t_budget INTEGER NOT NULL,
                        w REAL NOT NULL DEFAULT 1.0,
                        f_cum REAL NOT NULL DEFAULT 1.0,
                        is_incumbent INTEGER NOT NULL DEFAULT 0,
                        updated_ts REAL NOT NULL,
                        PRIMARY KEY (slot_id, variant)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_slots_slot_id ON slots(slot_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_slots_incumbent ON slots(is_incumbent)")
                conn.commit()

    def persist_slot_state(self, agent_state: AgentState) -> None:
        """Persist single AgentState atomically (INSERT OR REPLACE).

        Pre: agent_state.agent_id in [0, SLOTS_TOTAL) × Variant
        Post: Record in slots table, updated_ts = now()
        """
        now = time.time()
        slot_id = agent_state.agent_id.slot_id
        variant = agent_state.agent_id.variant.value
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""
                    INSERT OR REPLACE INTO slots
                    (slot_id, variant, q_norm, t_budget, w, f_cum, is_incumbent, updated_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    slot_id,
                    variant,
                    agent_state.q_norm,
                    agent_state.t_budget,
                    agent_state.w,
                    agent_state.f_cum,
                    1 if agent_state.is_incumbent else 0,
                    now,
                ))
                conn.commit()

    def persist_all(self, agent_states: list[AgentState]) -> int:
        """Persist multiple AgentStates in single transaction.

        Pre: agent_states is non-empty
        Post: returns count persisted, all in single atomic transaction
        """
        now = time.time()
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                count = 0
                for state in agent_states:
                    conn.execute("""
                        INSERT OR REPLACE INTO slots
                        (slot_id, variant, q_norm, t_budget, w, f_cum, is_incumbent, updated_ts)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        state.agent_id.slot_id,
                        state.agent_id.variant.value,
                        state.q_norm,
                        state.t_budget,
                        state.w,
                        state.f_cum,
                        1 if state.is_incumbent else 0,
                        now,
                    ))
                    count += 1
                conn.commit()
                return count

    def load_all_slots(self) -> list[SlotRecord]:
        """Recovery: load all persisted slot records.

        Pre: Schema initialized
        Post: returns list of SlotRecord, sorted by (slot_id, variant)
        """
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute("""
                    SELECT slot_id, variant, q_norm, t_budget, w, f_cum, is_incumbent, updated_ts
                    FROM slots
                    ORDER BY slot_id ASC, variant ASC
                """).fetchall()

        records: list[SlotRecord] = []
        for row in rows:
            records.append(SlotRecord(
                slot_id=row[0],
                variant=Variant(row[1]),
                q_norm=row[2],
                t_budget=row[3],
                w=row[4],
                f_cum=row[5],
                is_incumbent=bool(row[6]),
                updated_ts=row[7],
            ))
        return records

    def load_slot(self, slot_id: int) -> list[SlotRecord]:
        """Load all 3 variants of a single slot.

        Pre: slot_id in [0, SLOTS_TOTAL)
        Post: returns list of SlotRecord (0-3 entries)
        """
        if not 0 <= slot_id < SLOTS_TOTAL:
            raise ValueError(f"slot_id muss in [0, {SLOTS_TOTAL}), war {slot_id}")
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute("""
                    SELECT slot_id, variant, q_norm, t_budget, w, f_cum, is_incumbent, updated_ts
                    FROM slots
                    WHERE slot_id = ?
                    ORDER BY variant ASC
                """, (slot_id,)).fetchall()

        return [
            SlotRecord(
                slot_id=row[0],
                variant=Variant(row[1]),
                q_norm=row[2],
                t_budget=row[3],
                w=row[4],
                f_cum=row[5],
                is_incumbent=bool(row[6]),
                updated_ts=row[7],
            )
            for row in rows
        ]

    def count_persisted(self) -> int:
        """Count records in slots table."""
        with self._lock:
            with self._connect() as conn:
                return conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]

    def count_incumbents(self) -> int:
        """Count active incumbents (sollte == SLOTS_TOTAL bei voll-init)."""
        with self._lock:
            with self._connect() as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM slots WHERE is_incumbent = 1"
                ).fetchone()[0]

    def update_q_norm_atomic(self, agent_id: AgentID, new_q: float) -> None:
        """Atomic q_norm-Update via BEGIN IMMEDIATE.

        Pre: new_q in [-2, +2]
        Post: q_norm aktualisiert, updated_ts = now()
        """
        if not -2.0 <= new_q <= 2.0:
            raise ValueError(f"q_norm muss in [-2, +2], war {new_q}")
        now = time.time()
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""
                    UPDATE slots
                    SET q_norm = ?, updated_ts = ?
                    WHERE slot_id = ? AND variant = ?
                """, (new_q, now, agent_id.slot_id, agent_id.variant.value))
                conn.commit()

    def promote_atomic(self, agent_id: AgentID) -> None:
        """Atomic Promotion: setze new incumbent, demote old in same slot.

        Pre: agent_id existiert in DB
        Post: genau 1 Record in slot ist is_incumbent=1
        """
        now = time.time()
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                # Demote alle anderen Variants im Slot
                conn.execute("""
                    UPDATE slots
                    SET is_incumbent = 0, updated_ts = ?
                    WHERE slot_id = ? AND variant != ?
                """, (now, agent_id.slot_id, agent_id.variant.value))
                # Promote den new incumbent
                conn.execute("""
                    UPDATE slots
                    SET is_incumbent = 1, updated_ts = ?
                    WHERE slot_id = ? AND variant = ?
                """, (now, agent_id.slot_id, agent_id.variant.value))
                conn.commit()

    def clear(self) -> None:
        """Reset store (test helper)."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM slots")
                conn.commit()


# ============================================================
# Backend-Switch Factory (per rules/backend-switch-multi-driver.md)
# ============================================================

def get_default_store(backend: Optional[str] = None,
                       db_path: Optional[Path] = None) -> SlotStateStore:
    """Backend-Factory fuer SlotStateStore.

    Args:
        backend: "sqlite" (default). "postgres" reserved fuer Welle-22+.
        db_path: optional override

    Returns:
        SlotStateStore-Instance.
    """
    import os
    if backend is None:
        backend = os.environ.get("DF_TRINITY_BACKEND", "sqlite")

    if backend == "postgres":
        raise ValueError(
            "Postgres-Backend pending Welle-22+ + Phronesis-Pflicht Martin "
            "(Real-Provider-Vertrag). Aktuell nur sqlite-Backend implementiert."
        )
    elif backend == "sqlite":
        return SlotStateStore(db_path=db_path)
    else:
        raise ValueError(f"Unbekanntes Backend: {backend}")
