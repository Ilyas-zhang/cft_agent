"""Crash-recovery state for the coordinator.

Writes an atomic JSON snapshot (tmp + replace) so a killed process never
leaves a half-written state file. The snapshot is informational plus a
resume aid: CTFd remains the source of truth for what is solved.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_VERSION = 1


def default_state_path(challenges_root: str = "challenges") -> Path:
    return Path(challenges_root).parent / "runtime" / "coordinator_state.json"


def load_state(path: Path) -> dict[str, Any]:
    """Load a previous snapshot. Corrupt/missing files yield an empty state."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load state from {path}: {e}")
        return {}


def save_state(path: Path, results: dict[str, Any], solved: set[str], total_cost_usd: float) -> None:
    """Atomically persist results, solved names, and accumulated cost."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": results,
        "solved": sorted(solved),
        "total_cost_usd": total_cost_usd,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.warning(f"Could not save state to {path}: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
