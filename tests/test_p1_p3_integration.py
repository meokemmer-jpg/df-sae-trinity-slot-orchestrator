"""P1+P3 Foundation-Service-Integration-Tests [CRUX-MK].

W45-MEGA Integration-Verification.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Add src to path
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from phronesis_gate import (
    check_phronesis_gate,
    build_audit_envelope,
    FOUNDATION_AVAILABLE,
    OPERATION_TYPE,
)


def test_foundation_imports_available():
    """Test 1: Foundation-Services importable via _df_common."""
    assert FOUNDATION_AVAILABLE, "Foundation services must be importable"


def test_p1_phronesis_blocked_without_ticket(monkeypatch):
    """Test 2: Kein PHRONESIS_TICKET → blocked."""
    monkeypatch.delenv("PHRONESIS_TICKET", raising=False)
    monkeypatch.setenv("PHRONESIS_SECRET", "a" * 32)
    result = check_phronesis_gate()
    assert result["status"] == "blocked"
    assert "phronesis" in result["reason"].lower() or "ticket" in result["reason"].lower()


def test_p1_phronesis_blocked_without_secret(monkeypatch):
    """Test 3: Kein PHRONESIS_SECRET → blocked."""
    monkeypatch.delenv("PHRONESIS_SECRET", raising=False)
    monkeypatch.setenv("PHRONESIS_TICKET", "fake-ticket-string")
    result = check_phronesis_gate()
    assert result["status"] == "blocked"


def test_p1_phronesis_allowed_with_valid_ticket(monkeypatch):
    """Test 4: Gueltiger HMAC-Ticket → allowed."""
    if not FOUNDATION_AVAILABLE:
        pytest.skip("Foundation services unavailable")
    from _df_common.phronesis_ticket_signer import PhronesisTicketSigner

    secret = "test-secret-min-16-chars-long" + "x" * 8
    monkeypatch.setenv("PHRONESIS_SECRET", secret)

    signer = PhronesisTicketSigner(secret=secret)
    ticket = signer.issue(OPERATION_TYPE, ttl_seconds=3600)
    serialized = signer.serialize(ticket)
    monkeypatch.setenv("PHRONESIS_TICKET", serialized)

    result = check_phronesis_gate()
    assert result["status"] == "allowed", f"Expected allowed, got: {result}"


def test_p3_envelope_build_with_secret(monkeypatch):
    """Test 5: P3 Full-Provenance-Envelope buildable."""
    if not FOUNDATION_AVAILABLE:
        pytest.skip("Foundation services unavailable")
    monkeypatch.setenv("PHRONESIS_SECRET", "x" * 32)
    env = build_audit_envelope({"slot_id": 42, "decision": "promote"}, tenant_id="hotel-test")
    assert env is not None
    assert env.tenant_id == "hotel-test"
    assert env.operation_type == OPERATION_TYPE
    assert env.signature  # HMAC signed
