from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from metaforge.projects.models import (
    ProjectContractError,
    ProjectPatch,
    ProjectPolicy,
    ProjectSnapshot,
    _validate_project_path,
)
from metaforge.projects.snapshot import _read_regular_file, capture_project_snapshot


def validate_patch_against_snapshot(
    snapshot: ProjectSnapshot,
    patch: ProjectPatch,
    policy: ProjectPolicy | None = None,
) -> None:
    """Validate every operation against a captured immutable source manifest."""
    policy = policy or ProjectPolicy()
    if not isinstance(policy, ProjectPolicy):
        raise ProjectContractError("A ProjectPolicy is required")
    if not isinstance(snapshot, ProjectSnapshot) or not isinstance(patch, ProjectPatch):
        raise ProjectContractError("A ProjectSnapshot and ProjectPatch are required")
    if len(patch.changes) > policy.max_patch_files:
        raise ProjectContractError("Patch file-count limit exceeded")

    file_map = snapshot.file_map
    folded_map = {path.casefold(): item for path, item in file_map.items()}
    total_content_bytes = 0
    change_paths: set[str] = set()
    for change in patch.changes:
        _validate_project_path(change.path, change.action)
        folded = change.path.casefold()
        if folded in change_paths:
            raise ProjectContractError("Patch contains duplicate or case-colliding paths")
        change_paths.add(folded)

        current = file_map.get(change.path)
        folded_current = folded_map.get(folded)
        if folded_current is not None and folded_current.path != change.path:
            raise ProjectContractError("Patch path collides with an existing path by case")

        source_path = Path(snapshot.root) / PurePosixPath(change.path)
        source_exists = source_path.exists() or source_path.is_symlink()
        if change.action == "create":
            if current is not None or source_exists:
                raise ProjectContractError(f"Create target already exists: {change.path}")
            if change.expected_sha256 is not None or change.content is None:
                raise ProjectContractError("Create requires content and no expected SHA-256")
        else:
            if current is None:
                raise ProjectContractError(f"Patch target is not in the snapshot: {change.path}")
            if change.expected_sha256 != current.sha256:
                raise ProjectContractError(f"Patch precondition hash mismatch: {change.path}")
            if not source_exists:
                raise ProjectContractError(f"Patch target disappeared: {change.path}")
            if change.action == "replace" and change.content is None:
                raise ProjectContractError("Replace requires new text content")
            if change.action == "delete" and change.content is not None:
                raise ProjectContractError("Delete cannot contain replacement content")

        if change.content is not None:
            encoded = change.content.encode("utf-8")
            if b"\x00" in encoded:
                raise ProjectContractError(f"Patch content contains NUL: {change.path}")
            if len(encoded) > policy.max_file_bytes:
                raise ProjectContractError(f"Patch file-size limit exceeded: {change.path}")
            total_content_bytes += len(encoded)

    if total_content_bytes > policy.max_patch_bytes:
        raise ProjectContractError("Patch total-content byte limit exceeded")


def _write_file_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o644)
    try:
        view = memoryview(content)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("Short write while materializing a patch file")
            written += count
    finally:
        os.close(descriptor)
    try:
        path.chmod(0o644)
    except OSError:
        pass


def _replace_file_atomically(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".metaforge-patch-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o644)
        except OSError:
            pass
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise


def _verify_non_overlapping_roots(source_root: Path, destination_root: Path) -> None:
    if (
        destination_root == source_root
        or destination_root.is_relative_to(source_root)
        or source_root.is_relative_to(destination_root)
    ):
        raise ProjectContractError("Patch destination must not overlap the source project")


def apply_patch_to_copy(
    snapshot: ProjectSnapshot,
    patch: ProjectPatch,
    destination: str | os.PathLike[str],
    policy: ProjectPolicy | None = None,
) -> ProjectSnapshot:
    """Apply a validated PatchSet to a new disposable copy, never the source tree.

    The source is re-snapshotted before copying and every copied file is hashed
    again. The destination must not exist. All writes occur in a temporary
    sibling directory and are atomically renamed into place only after the
    complete staged snapshot is valid. No candidate code, build, or test
    command is executed by this function.
    """
    policy = policy or ProjectPolicy()
    if not isinstance(policy, ProjectPolicy):
        raise ProjectContractError("A ProjectPolicy is required")
    if not isinstance(snapshot, ProjectSnapshot):
        raise ProjectContractError("A ProjectSnapshot is required")
    if not isinstance(patch, ProjectPatch):
        raise ProjectContractError("A ProjectPatch is required")

    source_root = Path(snapshot.root).resolve(strict=True)
    current_snapshot = capture_project_snapshot(source_root, policy)
    if current_snapshot.fingerprint != snapshot.fingerprint:
        raise ProjectContractError("Source project changed after snapshot capture")
    validate_patch_against_snapshot(snapshot, patch, policy)

    try:
        supplied_destination = Path(destination).expanduser()
    except (TypeError, ValueError) as exc:
        raise ProjectContractError("Destination must be a valid filesystem path") from exc
    if not supplied_destination.name or supplied_destination.name in {".", ".."}:
        raise ProjectContractError("Destination must name a new directory")
    parent = supplied_destination.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ProjectContractError("Destination parent must be an existing directory")
    target_root = parent / supplied_destination.name
    if target_root.exists() or target_root.is_symlink():
        raise ProjectContractError("Destination already exists; refusing to overwrite it")
    _verify_non_overlapping_roots(source_root, target_root.resolve(strict=False))

    stage = Path(tempfile.mkdtemp(prefix=f".{target_root.name}.metaforge-", dir=parent))
    installed = False
    try:
        for entry in snapshot.files:
            source = source_root / PurePosixPath(entry.path)
            target = stage / PurePosixPath(entry.path)
            raw = _read_regular_file(source, policy.max_file_bytes)
            if hashlib.sha256(raw).hexdigest() != entry.sha256:
                raise ProjectContractError(f"Source file changed while copying: {entry.path}")
            _write_file_exclusive(target, raw)

        for change in patch.changes:
            target = stage / PurePosixPath(change.path)
            if change.action == "create":
                if target.exists() or target.is_symlink():
                    raise ProjectContractError(f"Create target appeared in staged copy: {change.path}")
                _write_file_exclusive(target, change.content.encode("utf-8"))
            elif change.action == "replace":
                try:
                    old_content = _read_regular_file(target, policy.max_file_bytes)
                except ProjectContractError as exc:
                    raise ProjectContractError(f"Replace target is unavailable: {change.path}") from exc
                if hashlib.sha256(old_content).hexdigest() != change.expected_sha256:
                    raise ProjectContractError(f"Staged replace precondition mismatch: {change.path}")
                _replace_file_atomically(target, change.content.encode("utf-8"))
            else:
                try:
                    old_content = _read_regular_file(target, policy.max_file_bytes)
                except ProjectContractError as exc:
                    raise ProjectContractError(f"Delete target is unavailable: {change.path}") from exc
                if hashlib.sha256(old_content).hexdigest() != change.expected_sha256:
                    raise ProjectContractError(f"Staged delete precondition mismatch: {change.path}")
                target.unlink()

        # Validate the staged tree before making it visible as the destination.
        staged_snapshot = capture_project_snapshot(stage, policy)
        os.replace(stage, target_root)
        installed = True
        final_snapshot = capture_project_snapshot(target_root, policy)
        if final_snapshot.fingerprint != staged_snapshot.fingerprint:
            raise ProjectContractError("Installed project snapshot changed during finalization")
        return final_snapshot
    except Exception:
        if installed:
            shutil.rmtree(target_root, ignore_errors=True)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
