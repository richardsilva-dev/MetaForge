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




def _read_regular_file(path: Path, max_bytes: int | None = None) -> bytes:
    """Read a regular file without following a final-component symlink."""
    try:
        before = path.lstat()
    except OSError as exc:
        raise ProjectContractError(f"Cannot stat project file: {path.name}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
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
        if not stat.S_ISREG(opened.st_mode):
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
    supplied_root = Path(root).expanduser()
    try:
        root_stat = supplied_root.lstat()
    except OSError as exc:
        raise ProjectContractError("Project root does not exist or cannot be accessed") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise ProjectContractError("Project root must be a real directory, not a symlink")
    project_root = supplied_root.resolve(strict=True)

    entries: list[SnapshotFile] = []
    total_bytes = 0

    def add_file(path: Path, relative: str) -> None:
        nonlocal total_bytes
        raw = _read_regular_file(path, policy.max_file_bytes)
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
        add_file(candidate, root_file)

    for directory_name in sorted(_ALLOWED_DIRECTORY_ROOTS):
        start = project_root / directory_name
        if not start.exists() and not start.is_symlink():
            continue
        try:
            metadata = start.lstat()
        except OSError as exc:
            raise ProjectContractError(f"Cannot inspect project root: {directory_name}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ProjectContractError(f"Allow-listed project root must be a real directory: {directory_name}")

        stack = [start]
        while stack:
            current = stack.pop()
            try:
                children = sorted(os.scandir(current), key=lambda item: item.name.casefold())
            except OSError as exc:
                raise ProjectContractError(f"Cannot enumerate project directory: {current.name}") from exc
            subdirectories: list[Path] = []
            for child in children:
                if child.name.casefold() in _IGNORED_DIRECTORY_NAMES or child.name.startswith("."):
                    continue
                child_path = Path(child.path)
                try:
                    if child.is_symlink():
                        raise ProjectContractError(
                            f"Symlinks are forbidden inside snapshot roots: {child.name}"
                        )
                    if child.is_dir(follow_symlinks=False):
                        subdirectories.append(child_path)
                        continue
                    if not child.is_file(follow_symlinks=False):
                        raise ProjectContractError(
                            f"Special files are forbidden inside snapshot roots: {child.name}"
                        )
                except OSError as exc:
                    raise ProjectContractError(f"Cannot inspect project entry: {child.name}") from exc

                relative = child_path.relative_to(project_root).as_posix()
                if _is_text_candidate(relative):
                    add_file(child_path, relative)
            # Stack order is reversed to preserve ascending traversal.
            stack.extend(reversed(subdirectories))

    entries.sort(key=lambda entry: entry.path.casefold())
    if len({entry.path.casefold() for entry in entries}) != len(entries):
        raise ProjectContractError("Project contains paths that collide case-insensitively")
    return ProjectSnapshot(project_root, tuple(entries), total_bytes)
