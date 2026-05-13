"""Phronesis-Gate fuer Trinity-Slot-Orchestrator [CRUX-MK].

W45-MEGA P1+P3 Foundation-Service-Integration.

Pattern (per ~/.claude/rules/df-akzeptanz-kriterien.md K17 Pre-Action-Verification):
- P1: HMAC-signed Phronesis-Ticket via PHRONESIS_TICKET env-var
- P3: Full-Provenance-Envelope fuer Audit-Trail
- P2: AtomicLock via _df_common (import-check)

K_0-Naehe: Slot-Decisions sind K_0-relevant (Trinity-Voting beeinflusst Agenten-Lifecycle).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add _df_common to path
_df_common_path = Path(__file__).resolve().parent.parent.parent / "_df_common"
if str(_df_common_path.parent) not in sys.path:
    sys.path.insert(0, str(_df_common_path.parent))

try:
    from _df_common.phronesis_ticket_signer import PhronesisTicketSigner, PhronesisTicket
    from _df_common.full_provenance_envelope import (
        build_full_envelope,
        verify_full_envelope,
        FullProvenanceEnvelope,
    )
    from _df_common.atomic_lock import AtomicLock  # P2 import-check
    FOUNDATION_AVAILABLE = True
except ImportError as e:
    FOUNDATION_AVAILABLE = False
    _IMPORT_ERROR = str(e)


OPERATION_TYPE = "DF_SAE_TRINITY_SLOT_DECISION"


def get_signer() -> "PhronesisTicketSigner | None":
    """Get PhronesisTicketSigner from env-secret. None if foundation unavailable."""
    if not FOUNDATION_AVAILABLE:
        return None
    secret = os.environ.get("PHRONESIS_SECRET", "")
    if not secret or len(secret) < 16:
        return None
    return PhronesisTicketSigner(secret=secret)


def check_phronesis_gate(operation_type: str = OPERATION_TYPE) -> dict:
    """Pre-Action-Verification (K17 + K13 Pflicht).

    Returns:
        dict mit 'status' ('allowed' | 'blocked') und 'reason'.
    """
    if not FOUNDATION_AVAILABLE:
        return {
            "status": "blocked",
            "reason": f"foundation-services-unavailable: {_IMPORT_ERROR}",
        }

    ticket_str = os.environ.get("PHRONESIS_TICKET", "")
    if not ticket_str:
        return {
            "status": "blocked",
            "reason": "no-phronesis-ticket (PHRONESIS_TICKET env-var missing)",
        }

    signer = get_signer()
    if signer is None:
        return {
            "status": "blocked",
            "reason": "no-phronesis-secret (PHRONESIS_SECRET env-var missing or < 16 chars)",
        }

    try:
        ticket = signer.parse(ticket_str)
    except Exception as e:
        return {"status": "blocked", "reason": f"ticket-parse-failed: {e}"}

    ok, reason = signer.verify(ticket, expected_operation=operation_type)
    if not ok:
        return {"status": "blocked", "reason": f"ticket-verify-failed: {reason}"}

    return {"status": "allowed", "reason": "phronesis-gate-passed", "ticket_id": ticket.ticket_id}


def build_audit_envelope(payload: dict, tenant_id: str | None = None) -> "FullProvenanceEnvelope | None":
    """Build P3 Full-Provenance-Envelope fuer Audit-Trail."""
    if not FOUNDATION_AVAILABLE:
        return None
    secret = os.environ.get("PHRONESIS_SECRET", "")
    if not secret or len(secret) < 16:
        return None
    import secrets as _secrets
    return build_full_envelope(
        operation_id=_secrets.token_urlsafe(16),
        operation_type=OPERATION_TYPE,
        issuer="df-sae-trinity-slot-orchestrator",
        payload_dict=payload,
        secret=secret,
        tenant_id=tenant_id,
    )
