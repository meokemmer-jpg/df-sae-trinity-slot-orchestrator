"""P1+P3 integration proof for df-sae-trinity-slot-orchestrator."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add src to path
_src = Path(__file__).resolve().parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from provenance_integration import (
    MISSION,
    orchestrate_trinity_slot_snapshot,
    verify_recorded_snapshot,
)


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def test_mission_is_proven_by_real_files_and_countercase_discrimination(tmp_path, monkeypatch):
    monkeypatch.setenv("DF_SAE_TRINITY_HMAC_SECRET", "integration-secret-with-real-hmac")
    audit_dir = tmp_path / "audit"

    benign_state = {
        "slot_id": 17,
        "health_score": 96,
        "anomaly_score": 2,
        "integrity_ok": True,
        "adversarial_marker": False,
    }
    adversarial_state = {
        "slot_id": 17,
        "health_score": 12,
        "anomaly_score": 88,
        "integrity_ok": False,
        "adversarial_marker": True,
    }

    benign = orchestrate_trinity_slot_snapshot("slot-17-benign", benign_state, audit_dir, tenant_id="tenant-a")
    adversarial = orchestrate_trinity_slot_snapshot("slot-17-adversarial", adversarial_state, audit_dir, tenant_id="tenant-a")

    assert Path(benign.envelope_path).is_file()
    assert Path(adversarial.envelope_path).is_file()
    assert Path(adversarial.anchor_path).is_file()
    assert verify_recorded_snapshot(benign.envelope_path)
    assert verify_recorded_snapshot(adversarial.envelope_path)

    benign_envelope = _read_json(benign.envelope_path)
    adversarial_envelope = _read_json(adversarial.envelope_path)

    assert benign_envelope["mission"] == MISSION
    assert adversarial_envelope["mission"] == MISSION
    assert benign_envelope["payload"]["input_state"] == benign_state
    assert adversarial_envelope["payload"]["input_state"] == adversarial_state

    benign_decision = benign_envelope["payload"]["decision"]
    adversarial_decision = adversarial_envelope["payload"]["decision"]
    assert benign_decision != adversarial_decision
    assert benign_decision["route"] != adversarial_decision["route"]
    assert benign.payload_hash != adversarial.payload_hash
    assert benign.signature != adversarial.signature
    assert adversarial_envelope["predecessor_hash"] == benign_envelope["payload_hash"]

    anchor_lines = Path(adversarial.anchor_path).read_text(encoding="utf-8").strip().splitlines()
    anchors = [json.loads(line) for line in anchor_lines]
    assert [anchor["operation_id"] for anchor in anchors] == ["slot-17-benign", "slot-17-adversarial"]
    assert anchors[-1]["payload_hash"] == adversarial_envelope["payload_hash"]
