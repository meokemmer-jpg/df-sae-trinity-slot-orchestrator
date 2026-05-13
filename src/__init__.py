# LAZY-IMPORT: keine Top-Level-Imports zur Vermeidung von Dual-Import-Bugs.
# Per coding.md §1: from core.X statt from sae_v8.core.X
__all__ = [
    "TrinitySlotManager",
    "RelegationEngine",
    "VotingEngine",
    "SlotOrchestrator",
    "AuditLogger",
]


def __getattr__(name):
    if name == "TrinitySlotManager":
        from .trinity_slot_manager import TrinitySlotManager
        return TrinitySlotManager
    if name == "RelegationEngine":
        from .relegation_engine import RelegationEngine
        return RelegationEngine
    if name == "VotingEngine":
        from .voting_engine import VotingEngine
        return VotingEngine
    if name == "SlotOrchestrator":
        from .slot_orchestrator import SlotOrchestrator
        return SlotOrchestrator
    if name == "AuditLogger":
        from .audit_logger import AuditLogger
        return AuditLogger
    raise AttributeError(f"module 'src' has no attribute {name!r}")
