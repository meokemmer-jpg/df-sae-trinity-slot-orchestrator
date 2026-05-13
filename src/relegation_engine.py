"""Relegation-Engine: F_CUM_DECAY-basierte Relegation per coding.md §10 [CRUX-MK].

F_CUM_DECAY = 0.98 (HWZ ~34 Tage). Familien-Ewigkeits-Horizont gemaess
Martin-Direktive 2026-04-17 (NICHT 0.70 Trading-Wert).
"""
from __future__ import annotations

from typing import List, Optional

from .trinity_slot_manager import (
    AgentID,
    AgentState,
    F_CUM_DECAY,
    INCUMBENT_ADVANTAGE,
    RELEGATION_THRESHOLD,
    TrinitySlotManager,
    Variant,
)


class RelegationEngine:
    """Relegiert Agenten basierend auf F_CUM und Challenger-Performance.

    Pre-Condition: F_cum dominiert Relegation (NIE Governance allein, per
    coding.md §4 Invariante #6).
    """

    def __init__(self, slot_manager: TrinitySlotManager) -> None:
        self._slot_manager = slot_manager

    def should_relegate(self, agent_id: AgentID) -> bool:
        """Prueft ob Agent relegiert werden sollte.

        Bedingung: f_cum < RELEGATION_THRESHOLD (= 0.3).
        """
        agent = self._slot_manager.get_agent(agent_id)
        if agent is None:
            raise KeyError(f"Agent {agent_id} nicht gefunden")
        return agent.f_cum < RELEGATION_THRESHOLD

    def find_best_challenger(self, slot_id: int) -> Optional[AgentState]:
        """Findet besten Challenger fuer einen Slot.

        Incumbent muss um INCUMBENT_ADVANTAGE (1.15) ueberboten werden.
        """
        agents = self._slot_manager.get_slot_agents(slot_id)
        incumbent = next((a for a in agents if a.is_incumbent), None)
        if incumbent is None:
            return None

        challengers = [a for a in agents if not a.is_incumbent]
        if not challengers:
            return None

        # Bester Challenger nach f_cum
        best_challenger = max(challengers, key=lambda a: a.f_cum)

        # Incumbent-Vorteil: Challenger muss 15% besser sein
        required_score = incumbent.f_cum * INCUMBENT_ADVANTAGE
        if best_challenger.f_cum > required_score:
            return best_challenger
        return None

    def evaluate_slot(self, slot_id: int) -> Optional[AgentID]:
        """Evaluiert Slot: Promotion noetig?

        Returns:
            AgentID des neuen Incumbent (falls Promotion) ODER None.
        """
        challenger = self.find_best_challenger(slot_id)
        if challenger is None:
            return None
        # Challenger schlaegt Incumbent → Promotion
        self._slot_manager.promote(challenger.agent_id)
        return challenger.agent_id

    def decay_all(self) -> None:
        """Wendet F_CUM_DECAY = 0.98 auf alle Agenten an.

        f_cum_neu = f_cum_alt * 0.98 (kein performance-Add).
        Aufruf typischerweise einmal pro Zyklus (Daily-Cron).
        """
        for agent_id in self._collect_agent_ids():
            agent = self._slot_manager.get_agent(agent_id)
            if agent is not None:
                agent.f_cum = agent.f_cum * F_CUM_DECAY

    def _collect_agent_ids(self) -> List[AgentID]:
        """Sammelt alle 600 AgentIDs."""
        from .trinity_slot_manager import SLOTS_TOTAL
        return [
            AgentID(slot_id=s, variant=v)
            for s in range(SLOTS_TOTAL)
            for v in Variant
        ]
