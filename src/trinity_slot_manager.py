"""Trinity-Slot-Manager: 200 Slots × 3 Variants = 600 Agenten [CRUX-MK].

Pflicht-Konstanten per coding.md §10:
- F_CUM_DECAY = 0.98
- Q_SCALE_INTEGRAL = 11.11
- T_CAP = 50000
- W_CAP = 3.0

Welle-46-E Patch-2 Integration:
- Optional persistence_store: SlotStateStore fuer Cross-Restart-Recovery
- Bei Mutation (update_q_norm, update_fitness, promote): auto-persist
- Default None = in-memory-only (backward-compatible mit P1/P3 Tests)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from .persistence import SlotStateStore

# Pflicht-Konstanten (per ~/.claude/rules/coding.md §10)
SLOTS_TOTAL: int = 200
VARIANTS_PER_SLOT: int = 3
AGENTS_TOTAL: int = SLOTS_TOTAL * VARIANTS_PER_SLOT  # 600
F_CUM_DECAY: float = 0.98
Q_SCALE_INTEGRAL: float = 11.11
T_MIN: int = 2000
T_CAP: int = 50000
T_RECOVERY_FLOOR: int = 20000
W_CAP: float = 3.0
RELEGATION_THRESHOLD: float = 0.3
INCUMBENT_ADVANTAGE: float = 1.15


class Variant(str, Enum):
    """Trinity-Varianten pro Slot."""
    CONSERVATIVE = "conservative"
    AGGRESSIVE = "aggressive"
    CONTRARIAN = "contrarian"


@dataclass(frozen=True)
class AgentID:
    """Eindeutige Agenten-Identifikation."""
    slot_id: int
    variant: Variant

    def __post_init__(self) -> None:
        if not 0 <= self.slot_id < SLOTS_TOTAL:
            raise ValueError(f"slot_id muss in [0, {SLOTS_TOTAL}), war {self.slot_id}")


@dataclass
class AgentState:
    """Zustand pro Agent.

    Pre-Conditions:
    - q in [-2, +2] (governance_q_norm)
    - t_budget in [T_MIN, T_CAP]
    - w in [1.0, W_CAP]
    - f_cum >= 0
    """
    agent_id: AgentID
    q_norm: float = 0.0           # Governance-Score, normiert
    t_budget: int = T_CAP // 2
    w: float = 1.0
    f_cum: float = 1.0            # Fitness kumuliert
    is_incumbent: bool = False    # Aktuell aktiv im Slot

    def __post_init__(self) -> None:
        if not -2.0 <= self.q_norm <= 2.0:
            raise ValueError(f"q_norm muss in [-2, +2], war {self.q_norm}")
        if not T_MIN <= self.t_budget <= T_CAP:
            raise ValueError(f"t_budget muss in [{T_MIN}, {T_CAP}], war {self.t_budget}")
        if not 1.0 <= self.w <= W_CAP:
            raise ValueError(f"w muss in [1.0, {W_CAP}], war {self.w}")
        if self.f_cum < 0:
            raise ValueError(f"f_cum muss >= 0, war {self.f_cum}")


class TrinitySlotManager:
    """Verwaltet 200 Slots × 3 Variants = 600 Agenten.

    Post-Condition: nach __init__ existieren exakt AGENTS_TOTAL Agenten.
    """

    def __init__(self, persistence_store: Optional["SlotStateStore"] = None,
                 recover_from_store: bool = False) -> None:
        """Initialize Trinity-Slot-Manager.

        Args:
            persistence_store: optional SlotStateStore fuer Cross-Restart-Recovery (W46-E Patch-2)
            recover_from_store: wenn True + store!=None: lade State aus DB statt fresh-init
        """
        self._agents: Dict[AgentID, AgentState] = {}
        self._store = persistence_store

        if recover_from_store and persistence_store is not None:
            self._recover_from_store()
        else:
            self._initialize_slots()
            # Nach Init: persist vollstaendigen Initial-State falls Store gesetzt
            if persistence_store is not None:
                persistence_store.persist_all(list(self._agents.values()))

        assert len(self._agents) == AGENTS_TOTAL, (
            f"Erwartet {AGENTS_TOTAL} Agenten, gefunden {len(self._agents)}"
        )

    def _recover_from_store(self) -> None:
        """Recovery: lade alle Records aus SlotStateStore."""
        assert self._store is not None
        records = self._store.load_all_slots()
        if len(records) == 0:
            # Empty store -> fresh init + persist
            self._initialize_slots()
            self._store.persist_all(list(self._agents.values()))
            return
        for record in records:
            agent_id = AgentID(slot_id=record.slot_id, variant=record.variant)
            self._agents[agent_id] = AgentState(
                agent_id=agent_id,
                q_norm=record.q_norm,
                t_budget=record.t_budget,
                w=record.w,
                f_cum=record.f_cum,
                is_incumbent=record.is_incumbent,
            )

    def _initialize_slots(self) -> None:
        """Initialisiert 600 Agenten (200 Slots × 3 Variants).

        Defaults:
        - Conservative-Variante = Initial-Incumbent (is_incumbent=True)
        - Aggressive + Contrarian = Challenger (is_incumbent=False)
        """
        for slot_id in range(SLOTS_TOTAL):
            for variant in Variant:
                agent_id = AgentID(slot_id=slot_id, variant=variant)
                self._agents[agent_id] = AgentState(
                    agent_id=agent_id,
                    is_incumbent=(variant == Variant.CONSERVATIVE),
                )

    def get_agent(self, agent_id: AgentID) -> Optional[AgentState]:
        """Holt Agenten-State (None wenn nicht existent)."""
        return self._agents.get(agent_id)

    def get_slot_agents(self, slot_id: int) -> List[AgentState]:
        """Gibt alle 3 Varianten eines Slots zurueck.

        Post-Condition: len(return) == VARIANTS_PER_SLOT.
        """
        if not 0 <= slot_id < SLOTS_TOTAL:
            raise ValueError(f"slot_id muss in [0, {SLOTS_TOTAL}), war {slot_id}")
        agents = [
            self._agents[AgentID(slot_id=slot_id, variant=v)] for v in Variant
        ]
        assert len(agents) == VARIANTS_PER_SLOT
        return agents

    def get_incumbent(self, slot_id: int) -> AgentState:
        """Gibt aktiven Incumbent-Agent fuer Slot zurueck.

        Pre-Condition: genau 1 Agent pro Slot ist is_incumbent=True.
        """
        agents = self.get_slot_agents(slot_id)
        incumbents = [a for a in agents if a.is_incumbent]
        if len(incumbents) != 1:
            raise RuntimeError(
                f"Slot {slot_id} hat {len(incumbents)} Incumbents, erwartet 1"
            )
        return incumbents[0]

    def update_q_norm(self, agent_id: AgentID, new_q: float) -> None:
        """Setzt q_norm via Property-Guard (Q_SCALE_INTEGRAL = 11.11).

        Pre-Condition: new_q in [-2, +2].
        Post: bei persistence_store gesetzt → auch in DB persistiert (W46-E).
        """
        if not -2.0 <= new_q <= 2.0:
            raise ValueError(f"q_norm muss in [-2, +2], war {new_q}")
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(f"Agent {agent_id} nicht gefunden")
        agent.q_norm = new_q
        if self._store is not None:
            self._store.update_q_norm_atomic(agent_id, new_q)

    def update_fitness(self, agent_id: AgentID, performance: float) -> float:
        """Aktualisiert f_cum via F_CUM_DECAY (= 0.98).

        Formel: f_cum_neu = f_cum_alt * F_CUM_DECAY + performance
        Post: bei persistence_store gesetzt → auch in DB persistiert (W46-E).
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(f"Agent {agent_id} nicht gefunden")
        agent.f_cum = agent.f_cum * F_CUM_DECAY + performance
        if self._store is not None:
            self._store.persist_slot_state(agent)
        return agent.f_cum

    def count_total_agents(self) -> int:
        """Gibt Gesamt-Agenten-Anzahl zurueck."""
        return len(self._agents)

    def count_incumbents(self) -> int:
        """Zaehlt aktive Incumbents (sollte == SLOTS_TOTAL sein)."""
        return sum(1 for a in self._agents.values() if a.is_incumbent)

    def promote(self, agent_id: AgentID) -> None:
        """Promoviert Agent zum Incumbent, demotiert vorherigen Incumbent.

        Per myz33-Trinity-Voting: nur 1 Incumbent pro Slot.
        Post: bei persistence_store gesetzt → atomic-promote in DB (W46-E).
        """
        new_incumbent = self._agents.get(agent_id)
        if new_incumbent is None:
            raise KeyError(f"Agent {agent_id} nicht gefunden")
        # Demote bestehende Incumbents im selben Slot
        for variant in Variant:
            other_id = AgentID(slot_id=agent_id.slot_id, variant=variant)
            self._agents[other_id].is_incumbent = (other_id == agent_id)
        if self._store is not None:
            self._store.promote_atomic(agent_id)
