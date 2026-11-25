from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from metaforge.projects.models import (
    ProjectContractError,
    ProjectPolicy,
    ProjectSnapshot,
    SnapshotFile,
    _ALLOWED_DIRECTORY_ROOTS,
    _ALLOWED_ROOT_FILES,
    _ALLOWED_TEXT_SUFFIXES,
    _PROTECTED_BASENAMES,
    _IGNORED_DIRECTORY_NAMES,
)





def _is_symlink_or_reparse_point(metadata: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _read_relative_regular_file(
    root: Path,
    relative_path: str,
    max_bytes: int | None = None,
) -> bytes:
    """Open each path component relative to a root descriptor without following symlinks."""
    root = Path(root)
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise ProjectContractError("Snapshot root is unavailable during file access") from exc
    if _is_symlink_or_reparse_point(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        raise ProjectContractError("Snapshot root is no longer a real directory")
    try:
        if root.resolve(strict=True) != root:
            raise ProjectContractError("Snapshot root contains a symlinked or junction parent")
    except OSError as exc:
        raise ProjectContractError("Snapshot root cannot be resolved safely") from exc

    parts = relative_path.split("/")
    if not parts or any(part in {"", ".", ".."} or "\\\\" in part or ":" in part for part in parts):
        raise ProjectContractError("Snapshot path is not a safe relative path")

    # POSIX dir_fd traversal prevents an attacker from swapping an intermediate
    # directory for a symlink between lstat and open. Windows does not expose
    # the same dir_fd API, so it uses the per-component lstat fallback below.
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"):
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptors: list[int] = []
        try:
            root_descriptor = os.open(root, directory_flags)
            descriptors.append(root_descriptor)
            if os.fstat(root_descriptor).st_dev != root_metadata.st_dev:
                raise ProjectContractError("Snapshot root filesystem changed during access")
            current_descriptor = root_descriptor
            for component in parts[:-1]:
                next_descriptor = os.open(
                    component, directory_flags, dir_fd=current_descriptor
                )
                descriptors.append(next_descriptor)
                if os.fstat(next_descriptor).st_dev != root_metadata.st_dev:
                    raise ProjectContractError("Project path crosses a filesystem boundary")
                current_descriptor = next_descriptor
            file_descriptor = os.open(
                parts[-1], file_flags | getattr(os, "O_BINARY", 0),
                dir_fd=current_descriptor,
            )
            descriptors.append(file_descriptor)
            return _read_open_descriptor(file_descriptor, relative_path, max_bytes, root_metadata.st_dev)
        except OSError as exc:
            raise ProjectContractError(
                f"Cannot safely open project path without following symlinks: {relative_path}"
            ) from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    current = root
    for component in parts[:-1]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ProjectContractError(f"Cannot inspect project path: {relative_path}") from exc
        if _is_symlink_or_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise ProjectContractError(f"Project path has a symlink or non-directory parent: {relative_path}")
        if metadata.st_dev != root_metadata.st_dev:
            raise ProjectContractError("Project path crosses a filesystem boundary")
    return _read_regular_file(current / parts[-1], max_bytes, root_metadata.st_dev)


def _read_open_descriptor(
    descriptor: int,
    display_path: str,
    max_bytes: int | None = None,
    expected_device: int | None = None,
) -> bytes:
    opened = os.fstat(descriptor)
    if expected_device is not None and opened.st_dev != expected_device:
        raise ProjectContractError(f"Project path crosses a filesystem boundary: {display_path}")
    if not stat.S_ISREG(opened.st_mode):
        raise ProjectContractError(f"Project entries must be regular files: {display_path}")
    if max_bytes is not None and opened.st_size > max_bytes:
        raise ProjectContractError(f"Project file exceeds configured size limit: {display_path}")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise ProjectContractError(f"Project file exceeds configured size limit: {display_path}")
        chunks.append(chunk)
    after = os.fstat(descriptor)
    if (
        after.st_dev != opened.st_dev
        or after.st_ino != opened.st_ino
        or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns
        or total != opened.st_size
    ):
        raise ProjectContractError(f"Project file changed while being read: {display_path}")
    return b"".join(chunks)

def _read_regular_file(
    path: Path,
    max_bytes: int | None = None,
    expected_device: int | None = None,
) -> bytes:
    """Read a regular file without following a final-component symlink."""
    try:
        before = path.lstat()
    except OSError as exc:
        raise ProjectContractError(f"Cannot stat project file: {path.name}") from exc
    if _is_symlink_or_reparse_point(before) or not stat.S_ISREG(before.st_mode):
        raise ProjectContractError(f"Project entries must be regular non-symlink files: {path.name}")
    if max_bytes is not None and before.st_size > max_bytes:
        raise ProjectContractError(f"Project file exceeds configured size limit: {path.name}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProjectContractError(f"Cannot safely open project file: {path.name}") from exc
    try:
        opened = os.fstat(descriptor)
        if expected_device is not None and opened.st_dev != expected_device:
            raise ProjectContractError(f"Project path crosses a filesystem boundary: {path.name}")
        if _is_symlink_or_reparse_point(opened) or not stat.S_ISREG(opened.st_mode):
            raise ProjectContractError(f"Project entries must be regular files: {path.name}")
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_mtime_ns != before.st_mtime_ns
        ):
            raise ProjectContractError(f"Project file changed during snapshot: {path.name}")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ProjectContractError(f"Project file exceeds configured size limit: {path.name}")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != opened.st_dev
            or after.st_ino != opened.st_ino
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or total != opened.st_size
        ):
            raise ProjectContractError(f"Project file changed while being read: {path.name}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _is_text_candidate(relative_path: str) -> bool:
    path = Path(relative_path)
    if relative_path in _ALLOWED_ROOT_FILES:
        return True
    if path.name.casefold() in _PROTECTED_BASENAMES:
        return False
    return path.suffix.casefold() in _ALLOWED_TEXT_SUFFIXES


def capture_project_snapshot(
    root: str | os.PathLike[str],
    policy: ProjectPolicy | None = None,
) -> ProjectSnapshot:
    """Capture deterministic hashes of the allow-listed text project surface.

    Hidden paths, dependency/build manifests, binary artifacts, caches, and
    environments are intentionally excluded. Symlinks and special files
    encountered inside included roots fail closed.
    """
    policy = policy or ProjectPolicy()
    if not isinstance(policy, ProjectPolicy):
        raise ProjectContractError("A ProjectPolicy is required")
    supplied_root = Path(root).expanduser().absolute()
    try:
        root_stat = supplied_root.lstat()
    except OSError as exc:
        raise ProjectContractError("Project root does not exist or cannot be accessed") from exc
    if _is_symlink_or_reparse_point(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        raise ProjectContractError("Project root must be a real directory, not a symlink or junction")
    project_root = supplied_root.resolve(strict=True)
    if project_root != supplied_root:
        raise ProjectContractError("Symlinked parent directories are not allowed in project roots")

    entries: list[SnapshotFile] = []
    total_bytes = 0
    visited_entries = 0
    root_device = root_stat.st_dev

    def add_file(relative: str) -> None:
        nonlocal total_bytes
        raw = _read_relative_regular_file(project_root, relative, policy.max_file_bytes)
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectContractError(f"Non-UTF-8 file is outside the text snapshot contract: {relative}") from exc
        total_bytes += len(raw)
        if total_bytes > policy.max_snapshot_bytes:
            raise ProjectContractError("Project snapshot byte limit exceeded")
        entries.append(SnapshotFile(
            path=relative,
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
        ))
        if len(entries) > policy.max_snapshot_files:
            raise ProjectContractError("Project snapshot file-count limit exceeded")

    for root_file in sorted(_ALLOWED_ROOT_FILES):
        candidate = project_root / root_file
        if not candidate.exists() and not candidate.is_symlink():
            continue
        add_file(root_file)

    for directory_name in sorted(_ALLOWED_DIRECTORY_ROOTS):
        start = project_root / directory_name
        if not start.exists() and not start.is_symlink():
            continue
        try:
            metadata = start.lstat()
        except OSError as exc:
            raise ProjectContractError(f"Cannot inspect project root: {directory_name}") from exc
        if _is_symlink_or_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise ProjectContractError(f"Allow-listed project root must be a real directory: {directory_name}")
        if metadata.st_dev != root_device or os.path.ismount(start):
            raise ProjectContractError(f"Allow-listed project root crosses a filesystem boundary: {directory_name}")

        stack = [(start, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                children = []
                with os.scandir(current) as iterator:
                    for child in iterator:
                        visited_entries += 1
                        if visited_entries > policy.max_snapshot_entries:
                            raise ProjectContractError("Project snapshot directory-entry limit exceeded")
                        children.append(child)
                children.sort(key=lambda item: item.name.casefold())
            except ProjectContractError:
                raise
            except OSError as exc:
                raise ProjectContractError(f"Cannot enumerate project directory: {current.name}") from exc
            subdirectories: list[tuple[Path, int]] = []
            for child in children:
                if child.name.casefold() in _IGNORED_DIRECTORY_NAMES or child.name.startswith("."):
                    continue
                child_path = Path(child.path)
                try:
                    child_metadata = child.stat(follow_symlinks=False)
                    if _is_symlink_or_reparse_point(child_metadata):
                        raise ProjectContractError(
                            f"Symlinks and junctions are forbidden inside snapshot roots: {child.name}"
                        )
                    if stat.S_ISDIR(child_metadata.st_mode):
                        if child_metadata.st_dev != root_device or os.path.ismount(child_path):
                            raise ProjectContractError(
                                f"Filesystem mount points are forbidden inside snapshot roots: {child.name}"
                            )
                        if depth + 1 > policy.max_snapshot_depth:
                            raise ProjectContractError("Project snapshot directory-depth limit exceeded")
                        subdirectories.append((child_path, depth + 1))
                        continue
                    if not stat.S_ISREG(child_metadata.st_mode):
                        raise ProjectContractError(
                            f"Special files are forbidden inside snapshot roots: {child.name}"
                        )
                except OSError as exc:
                    raise ProjectContractError(f"Cannot inspect project entry: {child.name}") from exc

                relative = child_path.relative_to(project_root).as_posix()
                if _is_text_candidate(relative):
                    add_file(relative)
            # Stack order is reversed to preserve ascending traversal.
            stack.extend(reversed(subdirectories))

    entries.sort(key=lambda entry: entry.path.casefold())
    if len({entry.path.casefold() for entry in entries}) != len(entries):
        raise ProjectContractError("Project contains paths that collide case-insensitively")
    return ProjectSnapshot(project_root, tuple(entries), total_bytes)
