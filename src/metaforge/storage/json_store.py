from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _write_atomic(result: dict[str, Any], destination: Path, *, exclusive: bool = False) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and destination.exists():
        raise FileExistsError(f"Experiment archive already exists: {destination}")
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    if exclusive and destination.exists():
        temporary.unlink(missing_ok=True)
        raise FileExistsError(f"Experiment archive already exists: {destination}")
    temporary.replace(destination)
    return destination


def save_run(result: dict[str, Any], destination: Path) -> Path:
    """Atomically replace a named pointer such as .artifacts/latest.json."""
    return _write_atomic(result, destination)


def archive_run(result: dict[str, Any], directory: Path) -> Path:
    """Persist an immutable run artifact named by its generated run identifier."""
    run_id = result.get("run_id")
    if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise ValueError("A valid 32-character run_id is required for archival")
    destination = directory / f"{run_id}.json"
    return _write_atomic(result, destination, exclusive=True)
