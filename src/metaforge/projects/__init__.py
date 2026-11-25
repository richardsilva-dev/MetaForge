"""Safe project snapshot and structured patch contracts."""

from metaforge.projects.models import (
    PATCH_SCHEMA_VERSION,
    FileChange,
    ProjectContractError,
    ProjectPatch,
    ProjectPolicy,
    ProjectSnapshot,
    SnapshotFile,
)
from metaforge.projects.patches import apply_patch_to_copy, validate_patch_against_snapshot
from metaforge.projects.snapshot import capture_project_snapshot

__all__ = [
    "PATCH_SCHEMA_VERSION",
    "FileChange",
    "ProjectContractError",
    "ProjectPatch",
    "ProjectPolicy",
    "ProjectSnapshot",
    "SnapshotFile",
    "apply_patch_to_copy",
    "capture_project_snapshot",
    "validate_patch_against_snapshot",
]
