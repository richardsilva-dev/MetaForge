"""Safe project snapshot and structured patch contracts."""

from metaforge.projects.analysis import DeclaredEntryPoint, ExternalDependencyUsage, ManifestDependency, ProjectAnalysis, ProjectImport, ProjectModule, ProjectModuleContract, ProjectSymbol, analyze_project
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
from metaforge.projects.review import compare_project_analyses, review_project_patch
from metaforge.projects.snapshot import capture_project_snapshot
from metaforge.projects.secrets import (
    SecretFinding,
    SecretScanPolicy,
    SecretScanReport,
    scan_project_secrets,
)

__all__ = [
    "PATCH_SCHEMA_VERSION",
    "DeclaredEntryPoint",
    "DeclaredEntryPoint",
    "ExternalDependencyUsage",
    "ManifestDependency",
    "ProjectAnalysis",
    "ProjectImport",
    "ProjectModule",
    "ProjectModuleContract",
    "ProjectSymbol",
    "analyze_project",
    "FileChange",
    "ProjectContractError",
    "ProjectPatch",
    "ProjectPolicy",
    "ProjectSnapshot",
    "SnapshotFile",
    "SecretFinding",
    "SecretScanPolicy",
    "SecretScanReport",
    "scan_project_secrets",
    "apply_patch_to_copy",
    "capture_project_snapshot",
    "validate_patch_against_snapshot",
    "compare_project_analyses",
    "review_project_patch",
]
