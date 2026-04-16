"""JSONL audit logging.

Per the architecture (§4.4), every agent decision is persisted for debugging,
cost attribution, and post-hoc review. JSONL is chosen over SQLite/Postgres for
this demo because it's trivial to inspect with `tail -f` and `jq` — production
would stream these records into whatever the org uses (Datadog, S3 + Athena, etc).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AuditLog:
    """Append-only JSONL writer scoped to a single agent run."""

    run_id: str
    path: Path
    _start_ts: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate on new run — each run has its own file.
        self.path.write_text("")
        self.record("run_started", {"run_id": self.run_id})

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        """Append one structured event. Never raises — logging failures must
        not crash the agent."""
        entry = {
            "ts": time.time(),
            "elapsed_s": round(time.time() - self._start_ts, 3),
            "run_id": self.run_id,
            "event": event_type,
            **payload,
        }
        try:
            with self.path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            # Best-effort: write to stderr and keep going.
            import sys
            print(f"[audit] failed to write event: {e}", file=sys.stderr)
