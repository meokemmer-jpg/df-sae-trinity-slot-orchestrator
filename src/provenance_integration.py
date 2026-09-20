"""Provenance integration for df-sae-trinity-slot-orchestrator.

The module records real on-disk provenance envelopes for Trinity slot decisions.
It intentionally uses only deterministic standard-library primitives so tests can
prove behavior from persisted artifacts instead of mocks or in-memory fixtures.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

MISSION = "df-sae-trinity-slot-orchestrator"
DEFAULT_OPERATION_TYPE = "df-sae-trinity-state-snapshot"
DEFAULT_SECRET_ENV = "DF_SAE_TRINITY_HMAC_SECRET"


@dataclass(frozen=True)
class SlotDecision:
    """Decision produced by the Trinity slot orchestrator."""

    slot_id: int
    route: str
    risk_score: int
    reasons: list[str]


@dataclass(frozen=True)
class ProvenanceEnvelope:
    """Signed, chained envelope persisted for every slot snapshot."""

    mission: str
    operation_id: str
    operation_type: str
    issuer: str
    tenant_id: str
    created_at: str
    payload_hash: str
    payload: dict[str, Any]
    predecessor_hash: Optional[str]
    signature: str


@dataclass(frozen=True)
class SnapshotRecord:
    """Return value for a recorded orchestration snapshot."""

    decision: SlotDecision
    envelope_path: str
    anchor_path: str
    payload_hash: str
    signature: str


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _signature_material(envelope_without_signature: dict[str, Any]) -> bytes:
    return _canonical_json(envelope_without_signature).encode("utf-8")


def _hmac_signature(envelope_without_signature: dict[str, Any], secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), _signature_material(envelope_without_signature), hashlib.sha256).hexdigest()


def _secret_from_env() -> str:
    secret = os.environ.get(DEFAULT_SECRET_ENV)
    if not secret:
        raise RuntimeError(f"{DEFAULT_SECRET_ENV} must be set for signed provenance")
    if len(secret) < 16:
        raise RuntimeError(f"{DEFAULT_SECRET_ENV} must be at least 16 characters")
    return secret


def decide_slot_route(slot_state: dict[str, Any]) -> SlotDecision:
    """Route a Trinity slot from actual input fields.

    Benign input can be promoted, while adversarial or integrity-failing input is
    quarantined. The output is deliberately derived from the supplied state so a
    contrary input produces a contrary persisted result.
    """

    slot_id = int(slot_state.get("slot_id", -1))
    health_score = int(slot_state.get("health_score", 0))
    anomaly_score = int(slot_state.get("anomaly_score", 0))
    integrity_ok = bool(slot_state.get("integrity_ok", False))
    adversarial_marker = bool(slot_state.get("adversarial_marker", False))

    risk_score = max(0, min(100, anomaly_score + (0 if integrity_ok else 45) + (35 if adversarial_marker else 0)))
    reasons: list[str] = []
    if not integrity_ok:
        reasons.append("integrity-failed")
    if adversarial_marker:
        reasons.append("adversarial-marker")
    if anomaly_score >= 50:
        reasons.append("high-anomaly")
    if health_score < 50:
        reasons.append("low-health")

    if risk_score >= 60 or health_score < 50:
        route = "quarantine"
    elif risk_score >= 30:
        route = "review"
    else:
        route = "promote"

    if not reasons:
        reasons.append("healthy-slot")

    return SlotDecision(slot_id=slot_id, route=route, risk_score=risk_score, reasons=reasons)


class TrinityProvenanceRecorder:
    """Records Trinity slot snapshots as signed files plus an append-only anchor log."""

    def __init__(self, audit_dir: Path | str):
        self.audit_dir = Path(audit_dir)
        self.provenance_full_dir = self.audit_dir / "provenance-full"
        self.anchors_dir = self.audit_dir / "anchors"
        self.provenance_full_dir.mkdir(parents=True, exist_ok=True)
        self.anchors_dir.mkdir(parents=True, exist_ok=True)

    def _read_predecessor_hash(self) -> Optional[str]:
        files = sorted(self.provenance_full_dir.glob("*.envelope.json"), key=lambda path: path.stat().st_mtime_ns)
        if not files:
            return None
        with files[-1].open("r", encoding="utf-8") as handle:
            latest = json.load(handle)
        return latest.get("payload_hash")

    def record_state_snapshot(
        self,
        operation_id: str,
        state_payload: dict[str, Any],
        tenant_id: str = "sae-trinity-global",
    ) -> SnapshotRecord:
        decision = decide_slot_route(state_payload)
        payload = {
            "input_state": state_payload,
            "decision": asdict(decision),
        }
        payload_hash = _sha256_json(payload)
        envelope_base = {
            "mission": MISSION,
            "operation_id": operation_id,
            "operation_type": DEFAULT_OPERATION_TYPE,
            "issuer": MISSION,
            "tenant_id": tenant_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload_hash": payload_hash,
            "payload": payload,
            "predecessor_hash": self._read_predecessor_hash(),
        }
        signature = _hmac_signature(envelope_base, _secret_from_env())
        envelope = ProvenanceEnvelope(signature=signature, **envelope_base)

        envelope_path = self.provenance_full_dir / f"{operation_id}.envelope.json"
        _atomic_write_json(envelope_path, asdict(envelope))

        anchor_path = self.anchors_dir / "rfc3161-anchors.jsonl"
        anchor_record = {
            "mission": MISSION,
            "operation_id": operation_id,
            "anchored_at": datetime.now(timezone.utc).isoformat(),
            "payload_hash": payload_hash,
            "envelope_path": str(envelope_path),
            "anchor_hash": hashlib.sha256(f"{operation_id}:{payload_hash}:{signature}".encode("utf-8")).hexdigest(),
        }
        with anchor_path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_json(anchor_record) + "\n")

        return SnapshotRecord(
            decision=decision,
            envelope_path=str(envelope_path),
            anchor_path=str(anchor_path),
            payload_hash=payload_hash,
            signature=signature,
        )


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        temporary_name = handle.name
    Path(temporary_name).replace(path)


def verify_recorded_snapshot(envelope_path: Path | str, secret: Optional[str] = None) -> bool:
    """Verify a persisted envelope from disk."""

    with Path(envelope_path).open("r", encoding="utf-8") as handle:
        envelope = json.load(handle)

    supplied_signature = envelope.pop("signature", None)
    if not supplied_signature:
        return False
    recalculated_payload_hash = _sha256_json(envelope["payload"])
    if recalculated_payload_hash != envelope.get("payload_hash"):
        return False
    expected_signature = _hmac_signature(envelope, secret or _secret_from_env())
    return hmac.compare_digest(supplied_signature, expected_signature)


def orchestrate_trinity_slot_snapshot(
    operation_id: str,
    state_payload: dict[str, Any],
    audit_dir: Path | str,
    tenant_id: str = "sae-trinity-global",
) -> SnapshotRecord:
    """Mission entrypoint: decide a slot route and persist signed provenance."""

    recorder = TrinityProvenanceRecorder(audit_dir=audit_dir)
    return recorder.record_state_snapshot(operation_id=operation_id, state_payload=state_payload, tenant_id=tenant_id)
