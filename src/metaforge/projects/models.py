from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import unicodedata
from typing import Any


PATCH_SCHEMA_VERSION = "1.0"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ALLOWED_DIRECTORY_ROOTS = frozenset({"src", "tests", "docs", "backends"})
_ALLOWED_ROOT_FILES = frozenset({"README.md", "CHANGELOG.md", "SECURITY.md", "LICENSE"})
_ALLOWED_TEXT_SUFFIXES = frozenset({
    ".py", ".pyi", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".fs", ".fsx", ".vb", ".swift", ".scala", ".sc",
    ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".kt",
    ".rb", ".php", ".lua", ".dart", ".ex", ".exs", ".erl", ".hrl",
    ".hs", ".lhs", ".ml", ".mli", ".m", ".mm", ".r", ".jl", ".pl", ".pm",
    ".md", ".rst", ".txt", ".toml", ".json", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".css", ".scss", ".sql", ".ini", ".cfg",
    ".properties", ".proto",
})
_IGNORED_DIRECTORY_NAMES = frozenset({
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".artifacts", "build", "dist", "target", "vendor", "site-packages",
    ".idea", ".vscode",
})
_PROTECTED_BASENAMES = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "noxfile.py",
    "cargo.toml", "cargo.lock", "package.json", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock",
    "requirements.txt", "requirements.in", "pipfile", "pipfile.lock",
    "go.mod", "go.sum", "composer.json", "composer.lock",
    "gemfile", "gemfile.lock", "mix.exs", "mix.lock", "pom.xml",
    "build.gradle", "settings.gradle", "build.gradle.kts", "settings.gradle.kts",
    "cmakelists.txt", "meson.build", "cmakepresets.json",
    "justfile", "taskfile.yml", "deno.json", "deno.jsonc",
    "bun.lock", "bun.lockb", "pnpm-workspace.yaml", "gradlew", "mvnw",
    "package.swift", "package.resolved", "build.zig", "build.zig.zon",
    "dockerfile",
    "containerfile", "makefile", ".gitignore",
})
_WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
})


class ProjectContractError(ValueError):
    """Raised when a project snapshot or patch violates its contract."""


@dataclass(frozen=True, slots=True)
class ProjectPolicy:
    """Hard limits for snapshots and patch proposals."""

    max_snapshot_files: int = 1_000
    max_snapshot_bytes: int = 20 * 1024 * 1024
    max_patch_files: int = 30
    max_file_bytes: int = 256 * 1024
    max_patch_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        limits = {
            "max_snapshot_files": (self.max_snapshot_files, 1, 10_000),
            "max_snapshot_bytes": (self.max_snapshot_bytes, 1_024, 100 * 1024 * 1024),
            "max_patch_files": (self.max_patch_files, 1, 100),
            "max_file_bytes": (self.max_file_bytes, 1_024, 2 * 1024 * 1024),
            "max_patch_bytes": (self.max_patch_bytes, 1_024, 10 * 1024 * 1024),
        }
        for name, (value, minimum, maximum) in limits.items():
            if type(value) is not int or not minimum <= value <= maximum:
                raise ProjectContractError(
                    f"{name} must be an integer between {minimum} and {maximum}"
                )


@dataclass(frozen=True, slots=True)
class SnapshotFile:
    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ProjectContractError("Snapshot path must be a non-empty string")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise ProjectContractError("Snapshot SHA-256 must be 64 lowercase hexadecimal characters")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ProjectContractError("Snapshot file size must be a non-negative integer")

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size_bytes": self.size_bytes}


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    """Immutable file manifest captured from an allow-listed project surface."""

    root: Path
    files: tuple[SnapshotFile, ...]
    total_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path):
            raise ProjectContractError("Snapshot root must be a pathlib Path")
        if not isinstance(self.files, tuple) or any(
            not isinstance(item, SnapshotFile) for item in self.files
        ):
            raise ProjectContractError("Snapshot files must be a tuple of SnapshotFile records")
        paths = [item.path.casefold() for item in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ProjectContractError("Snapshot manifest must be sorted and path-unique")
        for item in self.files:
            _validate_project_path(item.path, "create")
        if type(self.total_bytes) is not int or self.total_bytes != sum(
            item.size_bytes for item in self.files
        ):
            raise ProjectContractError("Snapshot total_bytes does not match its manifest")

    @property
    def fingerprint(self) -> str:
        manifest = [item.to_dict() for item in self.files]
        payload = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @property
    def file_map(self) -> dict[str, SnapshotFile]:
        return {item.path: item for item in self.files}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "fingerprint": self.fingerprint,
            "file_count": len(self.files),
            "total_bytes": self.total_bytes,
            "files": [item.to_dict() for item in self.files],
        }


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    action: str
    expected_sha256: str | None = None
    content: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.path, str):
            raise ProjectContractError("Patch path must be text")
        if not isinstance(self.action, str) or self.action not in {"create", "replace", "delete"}:
            raise ProjectContractError("Patch action must be create, replace, or delete")
        if self.action == "create":
            if self.expected_sha256 is not None or not isinstance(self.content, str):
                raise ProjectContractError("Create requires content and forbids expected_sha256")
        elif self.action == "replace":
            if (
                not isinstance(self.expected_sha256, str)
                or not _SHA256_RE.fullmatch(self.expected_sha256)
                or not isinstance(self.content, str)
            ):
                raise ProjectContractError("Replace requires content and a valid expected_sha256")
        else:
            if (
                not isinstance(self.expected_sha256, str)
                or not _SHA256_RE.fullmatch(self.expected_sha256)
                or self.content is not None
            ):
                raise ProjectContractError("Delete requires expected_sha256 and forbids content")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "expected_sha256": self.expected_sha256,
            "content": self.content,
        }


@dataclass(frozen=True, slots=True)
class ProjectPatch:
    rationale: str
    changes: tuple[FileChange, ...]
    schema_version: str = PATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PATCH_SCHEMA_VERSION:
            raise ProjectContractError("Unsupported project patch schema version")
        if not isinstance(self.rationale, str) or not self.rationale.strip() or len(self.rationale) > 1_000:
            raise ProjectContractError("Patch rationale must contain 1..1000 characters")
        if not isinstance(self.changes, tuple) or not self.changes:
            raise ProjectContractError("Patch must contain at least one file change")
        if any(not isinstance(change, FileChange) for change in self.changes):
            raise ProjectContractError("Patch changes must contain FileChange records")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rationale": self.rationale,
            "changes": [change.to_dict() for change in self.changes],
        }

    @classmethod
    def from_dict(
        cls,
        value: Any,
        policy: ProjectPolicy | None = None,
    ) -> "ProjectPatch":
        policy = policy or ProjectPolicy()
        if not isinstance(policy, ProjectPolicy):
            raise ProjectContractError("A ProjectPolicy is required")
        if not isinstance(value, dict) or set(value) != {"schema_version", "rationale", "changes"}:
            raise ProjectContractError("Patch must contain exactly schema_version, rationale, and changes")
        if value.get("schema_version") != PATCH_SCHEMA_VERSION:
            raise ProjectContractError("Unsupported project patch schema version")
        raw_changes = value.get("changes")
        if not isinstance(raw_changes, list) or not raw_changes:
            raise ProjectContractError("Patch changes must be a non-empty array")
        if len(raw_changes) > policy.max_patch_files:
            raise ProjectContractError("Patch file-count limit exceeded")

        changes: list[FileChange] = []
        total_content_bytes = 0
        for index, raw in enumerate(raw_changes):
            if not isinstance(raw, dict) or set(raw) != {
                "path", "action", "expected_sha256", "content"
            }:
                raise ProjectContractError(f"Change {index} has an invalid shape")
            try:
                change = FileChange(**raw)
            except TypeError as exc:
                raise ProjectContractError(f"Change {index} has invalid field values") from exc
            _validate_project_path(change.path, change.action)
            if change.content is not None:
                try:
                    encoded = change.content.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ProjectContractError(f"Change {index} content is not valid UTF-8") from exc
                if b"\x00" in encoded:
                    raise ProjectContractError(f"Change {index} contains a NUL byte")
                if len(encoded) > policy.max_file_bytes:
                    raise ProjectContractError(f"Change {index} exceeds the per-file byte limit")
                total_content_bytes += len(encoded)
            changes.append(change)
        if total_content_bytes > policy.max_patch_bytes:
            raise ProjectContractError("Patch total-content byte limit exceeded")

        paths = [change.path.casefold() for change in changes]
        if len(paths) != len(set(paths)):
            raise ProjectContractError("Patch contains duplicate or case-colliding paths")
        return cls(value["rationale"], tuple(changes), value["schema_version"])


def _validate_project_path(path: str, action: str) -> str:
    """Validate a strict POSIX relative path inside the project edit surface."""
    if not isinstance(path, str) or not path or len(path) > 240:
        raise ProjectContractError("Patch path must contain 1..240 characters")
    if "\x00" in path or "\\" in path or ":" in path:
        raise ProjectContractError("Patch path contains a forbidden character")
    if unicodedata.normalize("NFC", path) != path:
        raise ProjectContractError("Patch path must use Unicode NFC normalization")
    if any(unicodedata.category(char).startswith("C") for char in path):
        raise ProjectContractError("Patch path contains a control or formatting character")
    if path.startswith("/") or path.endswith("/") or "//" in path:
        raise ProjectContractError("Patch path must be a normalized relative POSIX path")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ProjectContractError("Patch path traversal or empty path components are forbidden")
    if any(
        part.startswith(".") or part.endswith((".", " "))
        or part.casefold() in _IGNORED_DIRECTORY_NAMES
        for part in parts
    ):
        raise ProjectContractError("Hidden, generated, and excluded paths are forbidden")
    if any(part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES for part in parts):
        raise ProjectContractError("Windows reserved device names are forbidden")
    if parts[0] not in _ALLOWED_DIRECTORY_ROOTS and path not in _ALLOWED_ROOT_FILES:
        raise ProjectContractError("Patch path is outside the allow-listed project roots")
    if parts[-1].casefold() in _PROTECTED_BASENAMES:
        raise ProjectContractError("Dependency, build, and execution configuration files are protected")
    if path.casefold().endswith((
        ".lock", ".pem", ".key", ".p12", ".pfx", ".sh", ".ps1",
        ".bat", ".cmd", ".kts", ".cmake", ".csproj", ".sln", ".props",
        ".targets", ".vbproj", ".fsproj",
    )):
        raise ProjectContractError("Lockfiles, secret files, scripts, and build manifests are protected")
    if path not in _ALLOWED_ROOT_FILES and PurePosixPath(path).suffix.casefold() not in _ALLOWED_TEXT_SUFFIXES:
        raise ProjectContractError("Only allow-listed text source/documentation file types may be patched")
    if parts[0] == "tests" and action != "create":
        raise ProjectContractError("Existing acceptance tests are immutable; new tests may only be added")
    return str(PurePosixPath(path))
