"""W50-A Batch-2: K12+K13+K16 Tests fuer df-sae-trinity provenance_integration [CRUX-MK]."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.provenance_integration import (
    TrinityProvenanceRecorder,
    W48_FOUNDATION,
    DEFAULT_K16_LOCK_PATH,
)


@pytest.mark.skipif(not W48_FOUNDATION, reason="W48 foundation modules not installed")
def test_k12_envelope_emitted(tmp_path: Path):
    """K12: build_full_envelope writes signed envelope per state-snapshot."""
    recorder = TrinityProvenanceRecorder(audit_dir=tmp_path)
    result = recorder.record_state_snapshot(
        operation_id="test-op-001",
        state_payload={"slots_total": 200, "incumbents": 200, "f_cum_avg": 0.74},
        tenant_id="sae-trinity-global",
    )
    assert result is not None
    assert "envelope_path" in result
    assert "payload_hash" in result
    env_file = Path(result["envelope_path"])
    assert env_file.exists()
    with open(env_file) as f:
        env = json.load(f)
    assert env["operation_id"] == "test-op-001"
    assert env["operation_type"] == "df-sae-trinity-state-snapshot"
    assert env["issuer"] == "df-sae-trinity-slot-orchestrator"
    assert "signature" in env
    assert env["payload_hash"] == result["payload_hash"]


@pytest.mark.skipif(not W48_FOUNDATION, reason="W48 foundation modules not installed")
def test_k12_chain_linkage(tmp_path: Path):
    """K12: subsequent envelope references predecessor_hash."""
    recorder = TrinityProvenanceRecorder(audit_dir=tmp_path)
    r1 = recorder.record_state_snapshot("op-001", {"a": 1})
    r2 = recorder.record_state_snapshot("op-002", {"a": 2})
    env2_file = Path(r2["envelope_path"])
    with open(env2_file) as f:
        env2 = json.load(f)
    # Schema-Feldname laut full_provenance_envelope.py: chain_predecessor_hash
    assert env2.get("chain_predecessor_hash") == r1["payload_hash"]


@pytest.mark.skipif(not W48_FOUNDATION, reason="W48 foundation modules not installed")
def test_k13_rfc3161_anchor_appended(tmp_path: Path):
    """K13: rfc3161-anchors.jsonl is appended per state-snapshot."""
    recorder = TrinityProvenanceRecorder(audit_dir=tmp_path)
    recorder.record_state_snapshot("op-anchor-1", {"k": "v"})
    anchor_file = tmp_path / "anchors" / "rfc3161-anchors.jsonl"
    assert anchor_file.exists()
    with open(anchor_file) as f:
        lines = f.readlines()
    assert len(lines) >= 1
    entry = json.loads(lines[-1])
    assert "chain_hash" in entry
    # AnchorRecord-Schema (RFC3161): chain_hash + provider + schema_version (Pflicht-Felder)
    assert "provider" in entry
    assert "schema_version" in entry


def test_k16_lock_path_default():
    """K16: DEFAULT_K16_LOCK_PATH points to expected file."""
    assert DEFAULT_K16_LOCK_PATH.name.endswith(".lockfile")
    assert "df-sae-trinity" in str(DEFAULT_K16_LOCK_PATH)


@pytest.mark.skipif(not W48_FOUNDATION, reason="W48 foundation modules not installed")
def test_k16_concurrent_lock_acquired(tmp_path: Path):
    """K16: AtomicLock prevents concurrent recorder instances."""
    lock_path = tmp_path / "test.lockfile"
    rec1 = TrinityProvenanceRecorder(audit_dir=tmp_path, k16_lock_path=lock_path)
    # First call acquires + releases the lock
    rec1.record_state_snapshot("k16-test", {"foo": "bar"})
    # Subsequent call should also succeed (lock was released after first call)
    rec1.record_state_snapshot("k16-test-2", {"foo": "baz"})
    # If we hold the lock manually, recording should raise
    from _df_common.atomic_lock import AtomicLock  # type: ignore
    manual_lock = AtomicLock(lock_path, ttl_s=600.0)
    assert manual_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="K16 Concurrent-Spawn-Mutex"):
            rec1.record_state_snapshot("k16-blocked", {"x": 1})
    finally:
        manual_lock.release()
