"""Voting-Engine: Best-of-3-Wins per Trinity-Pattern [CRUX-MK]."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .trinity_slot_manager import (
    AgentID,
    AgentState,
    TrinitySlotManager,
    VARIANTS_PER_SLOT,
    Variant,
)


@dataclass(frozen=True)
class Vote:
    """Vote einer Variante (Trinity-Pattern)."""
    agent_id: AgentID
    score: float
    reasoning: str


@dataclass(frozen=True)
class VotingResult:
    """Ergebnis einer Trinity-Voting-Runde.

    Pre-Condition: votes hat genau 3 Eintraege (1 pro Variante).
    """
    slot_id: int
    winner: AgentID
    winning_score: float
    all_votes: List[Vote]

    def __post_init__(self) -> None:
        if len(self.all_votes) != VARIANTS_PER_SLOT:
            raise ValueError(
                f"all_votes muss {VARIANTS_PER_SLOT} Eintraege haben, "
                f"hat {len(self.all_votes)}"
            )


class VotingEngine:
    """Implementiert Best-of-3-Wins-Voting fuer Trinity-Slots.

    Per coding.md §2: Trinity ist sakrosankt. Voting ist deterministisch.
    """

    def __init__(self, slot_manager: TrinitySlotManager) -> None:
        self._slot_manager = slot_manager

    def collect_votes(self, slot_id: int, scores: Dict[Variant, float],
                       reasonings: Dict[Variant, str] | None = None) -> List[Vote]:
        """Sammelt 3 Votes (1 pro Variante).

        Pre-Condition: scores hat genau 3 Eintraege (Variant.CONSERVATIVE/AGGRESSIVE/CONTRARIAN).
        """
        if set(scores.keys()) != set(Variant):
            raise ValueError(
                f"scores muss alle 3 Variants enthalten, hat {set(scores.keys())}"
            )
        reasonings = reasonings or {v: "" for v in Variant}
        votes = [
            Vote(
                agent_id=AgentID(slot_id=slot_id, variant=v),
                score=scores[v],
                reasoning=reasonings.get(v, ""),
            )
            for v in Variant
        ]
        return votes

    def vote(self, slot_id: int, scores: Dict[Variant, float],
              reasonings: Dict[Variant, str] | None = None) -> VotingResult:
        """Fuehrt Best-of-3-Voting durch.

        Bei Tie: Conservative gewinnt (sicherheits-bevorzugt).
        """
        votes = self.collect_votes(slot_id, scores, reasonings)
        # Tie-Break: bei gleichem Score gewinnt Conservative (Variant.CONSERVATIVE),
        # dann Aggressive, dann Contrarian (deterministisch).
        variant_priority = {
            Variant.CONSERVATIVE: 0,
            Variant.AGGRESSIVE: 1,
            Variant.CONTRARIAN: 2,
        }
        winner_vote = max(
            votes,
            key=lambda v: (v.score, -variant_priority[v.agent_id.variant]),
        )
        return VotingResult(
            slot_id=slot_id,
            winner=winner_vote.agent_id,
            winning_score=winner_vote.score,
            all_votes=votes,
        )

    def consensus_score(self, scores: Dict[Variant, float]) -> float:
        """Konsens-Score: 1.0 = perfekter Konsens, 0.0 = max Divergenz.

        Berechnet ueber Standardabweichung der Scores.
        """
        if set(scores.keys()) != set(Variant):
            raise ValueError("scores muss alle 3 Variants enthalten")
        values = list(scores.values())
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std_dev = variance ** 0.5
        # Normalisiere: std_dev > 1.0 → consensus = 0
        return max(0.0, 1.0 - std_dev)
