import hashlib
import json
import pytest

from metaforge.projects import (
    ProjectContractError,
    ProjectPatch,
    ProjectPolicy,
    apply_patch_to_copy,
    capture_project_snapshot,
    validate_patch_against_snapshot,
)


def make_project(root):
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "backends" / "rust").mkdir(parents=True)
    (root / "src" / "pkg" / "module.py").write_text(
        "def value():\n    return 1\n", encoding="utf-8"
    )
    (root / "tests" / "test_baseline.py").write_text(
        "def test_baseline():\n    assert True\n", encoding="utf-8"
    )
    (root / "docs" / "design.md").write_text("# Design\n", encoding="utf-8")
    (root / "backends" / "rust" / "main.rs").write_text(
        "fn main() {}\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# Fixture\n", encoding="utf-8")
    (root / ".github").mkdir()
    (root / ".github" / "workflows").mkdir()
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "name: ci\n", encoding="utf-8"
    )
    (root / ".venv").mkdir()
    (root / ".venv" / "private.txt").write_text("not in snapshot", encoding="utf-8")
    (root / ".artifacts").mkdir()
    (root / ".artifacts" / "run.json").write_text("{}", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (root / "Cargo.lock").write_text("lock", encoding="utf-8")


def patch_dict(*changes, rationale="Test a bounded project patch"):
    return {
        "schema_version": "1.0",
        "rationale": rationale,
        "changes": [
            {
                "path": change["path"],
                "action": change["action"],
                "expected_sha256": change.get("expected_sha256"),
                "content": change.get("content"),
            }
            for change in changes
        ],
    }


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_snapshot_is_deterministic_allowlisted_and_does_not_expose_absolute_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    make_project(root)

    first = capture_project_snapshot(root)
    second = capture_project_snapshot(root)

    assert first.fingerprint == second.fingerprint
    assert [item.path for item in first.files] == sorted(
        (item.path for item in first.files), key=str.casefold
    )
    paths = {item.path for item in first.files}
    assert paths == {
        "README.md",
        "backends/rust/main.rs",
        "docs/design.md",
        "src/pkg/module.py",
        "tests/test_baseline.py",
    }
    serialized = json.dumps(first.to_dict())
    assert str(root) not in serialized
    assert first.to_dict()["fingerprint"] == first.fingerprint


def test_snapshot_rejects_a_symlink_as_the_project_root(tmp_path):
    real = tmp_path / "real-project"
    real.mkdir()
    (real / "src").mkdir()
    alias = tmp_path / "alias-project"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host")

    with pytest.raises(ProjectContractError, match="real directory"):
        capture_project_snapshot(alias)


def test_snapshot_rejects_symlinks_inside_included_roots(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    try:
        (root / "src" / "leak.py").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host")

    with pytest.raises(ProjectContractError, match="Symlinks.*forbidden"):
        capture_project_snapshot(root)


def test_snapshot_rejects_non_utf8_and_enforces_file_and_byte_budgets(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "bad.py").write_bytes(b"\xff\xfe")

    with pytest.raises(ProjectContractError, match="Non-UTF-8"):
        capture_project_snapshot(root)

    (root / "src" / "bad.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "second.py").write_text("y = 2\n", encoding="utf-8")
    (root / "src" / "large.py").write_text("x" * 1_500, encoding="utf-8")
    with pytest.raises(ProjectContractError, match="file-count"):
        capture_project_snapshot(root, ProjectPolicy(max_snapshot_files=1))
    with pytest.raises(ProjectContractError, match="byte limit"):
        capture_project_snapshot(root, ProjectPolicy(max_snapshot_bytes=1_024))
    with pytest.raises(ProjectContractError, match="directory-entry"):
        capture_project_snapshot(root, ProjectPolicy(max_snapshot_entries=1))
    with pytest.raises(ProjectContractError):
        ProjectPolicy(max_snapshot_entries=True)


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "src/../../escape.py",
        "/absolute.py",
        "C:/windows.py",
        "src\\windows.py",
        "\\\\server\\share.py",
        ".github/workflows/ci.yml",
        "pyproject.toml",
        "src/pkg/Cargo.toml",
        "src/pkg/requirements.txt",
        "src/pkg/.env",
        "src/pkg/private.pem",
        "src/pkg/COM1.py",
        "src/pkg/café.py",
        "src/pkg/file.exe",
        "src/scripts/run.sh",
        "src/scripts/run.ps1",
        "src/pkg/go.mod",
        "src/pkg/build.gradle.kts",
        "src/pkg/custom-script.kts",
        "src/pkg/Package.swift",
        "src/pkg/trailing.py ",
        "src//double.py",
        "src/node_modules/pkg.py",
        "src/__pycache__/module.py",
    ],
)
def test_patch_schema_rejects_unsafe_or_out_of_scope_paths(path):
    with pytest.raises(ProjectContractError):
        ProjectPatch.from_dict(patch_dict({
            "path": path, "action": "create", "content": "value = 1\n"
        }))


@pytest.mark.parametrize("path", [
    "src/pkg/Example.cs",
    "src/pkg/main.rs",
    "src/pkg/main.go",
    "src/pkg/Main.kt",
    "src/pkg/index.ts",
])
def test_patch_contract_accepts_allowlisted_multilanguage_source_files(path):
    patch = ProjectPatch.from_dict(patch_dict({
        "path": path, "action": "create", "content": "// candidate source\\n"
    }))
    assert patch.changes[0].path == path


def test_patch_schema_rejects_unknown_fields_duplicate_paths_and_bad_actions():
    change = {"path": "src/pkg/a.py", "action": "create", "content": "x = 1\n"}
    payload = patch_dict(change)
    payload["unexpected"] = True
    with pytest.raises(ProjectContractError):
        ProjectPatch.from_dict(payload)

    with pytest.raises(ProjectContractError, match="duplicate"):
        ProjectPatch.from_dict(patch_dict(
            change,
            {"path": "src/pkg/A.py", "action": "create", "content": "x = 2\n"},
        ))

    with pytest.raises(ProjectContractError):
        ProjectPatch.from_dict(patch_dict({
            "path": "src/pkg/a.py", "action": [], "content": "x = 1\n"
        }))


def test_patch_schema_enforces_file_count_byte_and_content_limits():
    with pytest.raises(ProjectContractError, match="file-count"):
        ProjectPatch.from_dict(
            patch_dict(
                {"path": "src/pkg/a.py", "action": "create", "content": "x = 1\n"},
                {"path": "src/pkg/b.py", "action": "create", "content": "x = 2\n"},
            ),
            ProjectPolicy(max_patch_files=1),
        )
    with pytest.raises(ProjectContractError, match="per-file"):
        ProjectPatch.from_dict(
            patch_dict({"path": "src/pkg/a.py", "action": "create", "content": "x" * 1_025}),
            ProjectPolicy(max_file_bytes=1_024),
        )
    with pytest.raises(ProjectContractError, match="NUL"):
        ProjectPatch.from_dict(
            patch_dict({"path": "src/pkg/a.py", "action": "create", "content": "x\x00y"})
        )
    with pytest.raises(ProjectContractError, match="total-content"):
        ProjectPatch.from_dict(
            patch_dict(
                {"path": "src/pkg/a.py", "action": "create", "content": "a" * 600},
                {"path": "src/pkg/b.py", "action": "create", "content": "b" * 600},
            ),
            ProjectPolicy(max_patch_bytes=1_024),
        )
    with pytest.raises(ProjectContractError):
        ProjectPolicy(max_patch_files=True)


def test_patch_contract_requires_precondition_hash_for_replace_and_delete():
    for action in ("replace", "delete"):
        with pytest.raises(ProjectContractError):
            ProjectPatch.from_dict(patch_dict({
                "path": "src/pkg/module.py",
                "action": action,
                "content": "x = 1\n" if action == "replace" else None,
            }))


def test_patch_rejects_case_collisions_with_existing_source_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    make_project(root)
    snapshot = capture_project_snapshot(root)
    patch = ProjectPatch.from_dict(patch_dict({
        "path": "src/PKG/module.py",
        "action": "create",
        "content": "def value(): return 9\\n",
    }))

    with pytest.raises(ProjectContractError, match="collides"):
        validate_patch_against_snapshot(snapshot, patch)


def test_patch_can_only_add_tests_and_cannot_rewrite_existing_acceptance_tests(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    make_project(root)
    snapshot = capture_project_snapshot(root)
    baseline = (root / "tests" / "test_baseline.py").read_text(encoding="utf-8")

    with pytest.raises(ProjectContractError, match="immutable"):
        ProjectPatch.from_dict(patch_dict({
            "path": "tests/test_baseline.py",
            "action": "replace",
            "expected_sha256": digest(baseline),
            "content": "def test_baseline(): assert False\n",
        }))

    patch = ProjectPatch.from_dict(patch_dict({
        "path": "tests/test_new.py",
        "action": "create",
        "content": "def test_new():\n    assert True\n",
    }))
    validate_patch_against_snapshot(snapshot, patch)


def test_patch_apply_create_replace_and_delete_on_new_copy_only(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    make_project(root)
    source_snapshot = capture_project_snapshot(root)
    old_source = (root / "src" / "pkg" / "module.py").read_text(encoding="utf-8")
    old_docs = (root / "docs" / "design.md").read_text(encoding="utf-8")
    patch = ProjectPatch.from_dict(patch_dict(
        {
            "path": "src/pkg/module.py",
            "action": "replace",
            "expected_sha256": digest(old_source),
            "content": "def value():\n    return 2\n",
        },
        {
            "path": "docs/design.md",
            "action": "delete",
            "expected_sha256": digest(old_docs),
        },
        {
            "path": "src/pkg/new_module.py",
            "action": "create",
            "content": "def new_value():\n    return 3\n",
        },
        {
            "path": "tests/test_added.py",
            "action": "create",
            "content": "def test_added():\n    assert True\n",
        },
    ))

    target = tmp_path / "patched-copy"
    result = apply_patch_to_copy(source_snapshot, patch, target)

    assert result.root == target
    assert (root / "src" / "pkg" / "module.py").read_text(encoding="utf-8") == old_source
    assert (root / "docs" / "design.md").read_text(encoding="utf-8") == old_docs
    assert (target / "src" / "pkg" / "module.py").read_text(encoding="utf-8") == (
        "def value():\n    return 2\n"
    )
    assert not (target / "docs" / "design.md").exists()
    assert (target / "src" / "pkg" / "new_module.py").exists()
    assert (target / "tests" / "test_added.py").exists()
    assert result.fingerprint != source_snapshot.fingerprint


def test_patch_rejects_stale_hash_and_existing_create_before_materializing_copy(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    make_project(root)
    snapshot = capture_project_snapshot(root)
    content = "x = 4\n"
    stale = ProjectPatch.from_dict(patch_dict({
        "path": "src/pkg/module.py",
        "action": "replace",
        "expected_sha256": "0" * 64,
        "content": content,
    }))
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ProjectContractError, match="hash mismatch"):
        apply_patch_to_copy(snapshot, stale, destination)
    assert not destination.exists()

    conflict = ProjectPatch.from_dict(patch_dict({
        "path": "src/pkg/module.py",
        "action": "create",
        "content": content,
    }))
    with pytest.raises(ProjectContractError, match="already exists"):
        apply_patch_to_copy(snapshot, conflict, destination)
    assert not destination.exists()


def test_patch_application_refuses_source_changes_after_snapshot(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    make_project(root)
    snapshot = capture_project_snapshot(root)
    (root / "src" / "pkg" / "module.py").write_text("tampered = True\n", encoding="utf-8")
    patch = ProjectPatch.from_dict(patch_dict({
        "path": "src/pkg/new_module.py",
        "action": "create",
        "content": "value = 2\n",
    }))

    with pytest.raises(ProjectContractError, match="changed after snapshot"):
        apply_patch_to_copy(snapshot, patch, tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_patch_application_refuses_existing_destination_and_overlap(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    make_project(root)
    snapshot = capture_project_snapshot(root)
    patch = ProjectPatch.from_dict(patch_dict({
        "path": "src/pkg/new.py", "action": "create", "content": "value = 1\n"
    }))

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ProjectContractError, match="already exists"):
        apply_patch_to_copy(snapshot, patch, existing)
    with pytest.raises(ProjectContractError, match="overlap"):
        apply_patch_to_copy(snapshot, patch, root / "nested")
