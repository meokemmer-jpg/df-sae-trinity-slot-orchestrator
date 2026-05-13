"""K12+K13+K16 Provenance-Integration fuer df-sae-trinity-slot-orchestrator [CRUX-MK].

W49-C/W50-A Pattern (Batch-2, replicated from df-9os-next/loop_orchestrator.py W48).

Adressiert:
- K12: FullProvenanceEnvelope (HMAC + chain_predecessor_hash) pro 200-Slot-State-Snapshot
- K13: RFC3161 External-Anchor (Daily-Anchor + audit/anchors/rfc3161-anchors.jsonl)
- K16: AtomicLock fuer Concurrent-Spawn-Mutex (200-Slot-State Race-Protection)

K_0-RELEVANZ: HIGH (Trinity-200-Slot-State ist SAE-v8-Audit-Pflicht).

[CRUX-MK]
"""

from __future__ import annotations

import json
import logging
import os as _bootstrap_os
import sys as _sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# W48-Foundation: full_provenance_envelope + rfc3161_anchor + atomic_lock
_DF_ROOT = Path(__file__).resolve().parent.parent.parent
_sys.path.insert(0, str(_DF_ROOT))

try:
    from _df_common.full_provenance_envelope import (  # type: ignore
        build_full_envelope,
        verify_full_envelope,
        FullProvenanceEnvelope,
    )
    from _df_common.rfc3161_anchor import (  # type: ignore
        rfc3161_timestamp,
        verify_anchor,
        AnchorRecord,
    )
    from _df_common.atomic_lock import AtomicLock  # type: ignore
    W48_FOUNDATION = True
except ImportError:
    W48_FOUNDATION = False

logger = logging.getLogger(__name__)

# K12 HMAC-Signing-Secret: ENV-Var-gated
_K12_HMAC_SECRET = _bootstrap_os.environ.get(
    "DF_SAE_TRINITY_HMAC_SECRET", "df-sae-trinity-dev-hmac-secret-v1"
)
_K12_ENVELOPE_TTL_S = int(
    _bootstrap_os.environ.get("DF_SAE_TRINITY_ENVELOPE_TTL_S", "86400")
)  # 24h default

# K16 Lock-Path Default
DEFAULT_K16_LOCK_PATH = Path("/tmp/df-sae-trinity.lock.lockfile")


class TrinityProvenanceRecorder:
    """Records Trinity-Slot-State mutations with K12+K13 provenance.

    K11 try/except per record-call.
    K12 FullProvenanceEnvelope per state-snapshot.
    K13 RFC3161-Anchor (real if available, mock-fallback).
    K16 Optional AtomicLock for concurrent-mutation protection.
    """

    def __init__(self,
                 audit_dir: Path | str = "branch-hub/audit/df-sae-trinity/",
                 k16_lock_path: Optional[Path] = None,
                 k16_lock_ttl_s: float = 600.0):
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.provenance_full_dir = self.audit_dir / "provenance-full"
        self.provenance_full_dir.mkdir(parents=True, exist_ok=True)
        self.anchors_dir = self.audit_dir / "anchors"
        self.anchors_dir.mkdir(parents=True, exist_ok=True)
        self._k16_lock_path = k16_lock_path
        self._k16_lock_ttl_s = k16_lock_ttl_s

    def _read_predecessor_hash(self) -> Optional[str]:
        """K12 chain-linkage: read payload_hash of most recent envelope."""
        if not W48_FOUNDATION:
            return None
        if not self.provenance_full_dir.exists():
            return None
        try:
            files = sorted(
                self.provenance_full_dir.glob("*.envelope.json"),
                key=lambda p: p.stat().st_mtime,
            )
            if not files:
                return None
            with open(files[-1], "r", encoding="utf-8") as f:
                env = json.load(f)
            return env.get("payload_hash")
        except Exception as e:
            logger.warning(f"K12 predecessor read failed: {e}")
            return None

    def record_state_snapshot(self,
                              operation_id: str,
                              state_payload: dict[str, Any],
                              tenant_id: str = "sae-trinity-global") -> Optional[dict]:
        """Records a 200-Slot-State snapshot with K12 envelope + K13 anchor.

        K16: acquires AtomicLock if k16_lock_path was set in __init__.
        Returns dict with envelope_path + anchor_path, or None if W48 unavailable.
        """
        if not W48_FOUNDATION:
            logger.warning("W48 foundation unavailable, skip provenance recording")
            return None

        # K16 acquire (opt-in)
        k16_lock = None
        if self._k16_lock_path is not None:
            k16_lock = AtomicLock(self._k16_lock_path, ttl_s=self._k16_lock_ttl_s)
            if not k16_lock.acquire():
                raise RuntimeError(
                    f"K16 Concurrent-Spawn-Mutex: another df-sae-trinity recorder running. "
                    f"Lock: {self._k16_lock_path}"
                )

        try:
            return self._record_internal(operation_id, state_payload, tenant_id)
        finally:
            if k16_lock is not None:
                k16_lock.release()

    def _record_internal(self, operation_id: str, state_payload: dict,
                         tenant_id: str) -> dict:
        result: dict[str, Any] = {}

        # K12: build envelope
        try:
            predecessor_hash = self._read_predecessor_hash()
            envelope = build_full_envelope(
                operation_id=operation_id,
                operation_type="df-sae-trinity-state-snapshot",
                issuer="df-sae-trinity-slot-orchestrator",
                payload_dict=state_payload,
                secret=_K12_HMAC_SECRET,
                predecessor_hash=predecessor_hash,
                tenant_id=tenant_id,
                ttl_seconds=_K12_ENVELOPE_TTL_S,
            )
            env_out = self.provenance_full_dir / f"{operation_id}.envelope.json"
            with open(env_out, "w", encoding="utf-8") as f:
                json.dump(asdict(envelope), f, indent=2, default=str, ensure_ascii=False)
            result["envelope_path"] = str(env_out)
            result["payload_hash"] = envelope.payload_hash
            chain_hash_for_anchor = envelope.payload_hash
        except Exception as e:
            logger.warning(f"K12 envelope build failed: {e}")
            return result

        # K13: RFC3161 anchor (Daily-Anchor pattern)
        try:
            rfc_anchor = rfc3161_timestamp(chain_hash_for_anchor, provider="freetsa")
            anchor_file = self.anchors_dir / "rfc3161-anchors.jsonl"
            with open(anchor_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rfc_anchor)) + "\n")
            result["anchor_path"] = str(anchor_file)
        except Exception as e:
            logger.warning(f"K13 RFC3161 anchor failed (non-fatal): {e}")

        return result

    @classmethod
    def from_config(cls, config_path: Optional[Path] = None,
                    audit_dir: Path | str = "branch-hub/audit/df-sae-trinity/",
                    enforce_k16: bool = True) -> "TrinityProvenanceRecorder":
        """Build recorder with K16 lock wired up from config.yaml."""
        if config_path is None:
            config_path = Path(__file__).resolve().parent.parent / "config.yaml"

        k16_lock_path: Optional[Path] = None
        if enforce_k16:
            try:
                import yaml  # type: ignore
                with open(config_path) as f:
                    cfg = yaml.safe_load(f)
                lock_dir = cfg.get("acceptance_criteria", {}).get(
                    "K16_concurrent_spawn_mutex", {}
                ).get("lock_dir", str(DEFAULT_K16_LOCK_PATH))
                lock_dir_str = str(lock_dir).rstrip("/")
                k16_lock_path = (
                    Path(lock_dir_str)
                    if lock_dir_str.endswith(".lockfile")
                    else Path(lock_dir_str + ".lockfile")
                )
            except Exception as e:
                logger.warning(f"K16 config load failed: {e}, K16 lock disabled")

        return cls(audit_dir=audit_dir, k16_lock_path=k16_lock_path)
