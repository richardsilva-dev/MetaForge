from __future__ import annotations

import hashlib
from pathlib import Path

from metaforge.projects import (
    ProjectPatch,
    analyze_project,
    capture_project_snapshot,
    compare_project_analyses,
    review_project_patch,
)


def _project(root: Path) -> None:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "pkg" / "module.py").write_text(
        "def public(value: int) -> int:\n    return value\n", encoding="utf-8"
    )
    (root / "docs" / "design.md").write_text("# Design\n", encoding="utf-8")


def _patch(*changes: dict) -> ProjectPatch:
    return ProjectPatch.from_dict({
        "schema_version": "1.0",
        "rationale": "Review a coordinated multi-file change",
        "changes": [
            {
                "path": item["path"],
                "action": item["action"],
                "expected_sha256": item.get("expected_sha256"),
                "content": item.get("content"),
            }
            for item in changes
        ],
    })


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_review_accepts_nonbreaking_multi_file_patch_and_discards_candidate(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    snapshot = capture_project_snapshot(root)
    patch = _patch(
        {"path": "src/pkg/new_module.py", "action": "create",
         "content": "def helper() -> int:\n    return 2\n"},
        {"path": "docs/design.md", "action": "replace",
         "expected_sha256": snapshot.file_map["docs/design.md"].sha256,
         "content": "# Updated design\n"},
    )

    report = review_project_patch(snapshot, patch)

    assert report["accepted"] is True
    assert report["modules"]["added"] == ["src/pkg/new_module.py"]
    assert report["candidate_discarded_after_review"] is True
    assert report["execution_scope"] == {"project_code_executed": False, "tests_executed": False}
    assert (root / "docs" / "design.md").read_text(encoding="utf-8") == "# Design\n"
    assert not (root / "src" / "pkg" / "new_module.py").exists()


def test_review_blocks_public_signature_regressions_by_default(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    snapshot = capture_project_snapshot(root)
    source = root / "src" / "pkg" / "module.py"
    patch = _patch({
        "path": "src/pkg/module.py",
        "action": "replace",
        "expected_sha256": _digest(source),
        "content": "def public(name: int) -> int:\n    return name\n",
    })

    report = review_project_patch(snapshot, patch)

    assert report["accepted"] is False
    assert "public_api_contract_regression" in report["blocking_reasons"]
    assert any(item["kind"] == "signature_changed" and item["name"] == "public"
               for item in report["public_api"]["breaking_changes"])


def test_review_blocks_new_cycles_from_coordinated_multi_file_changes(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    (root / "src" / "pkg" / "a.py").write_text("def a(): return 1\n", encoding="utf-8")
    (root / "src" / "pkg" / "b.py").write_text("def b(): return 2\n", encoding="utf-8")
    snapshot = capture_project_snapshot(root)
    a = root / "src" / "pkg" / "a.py"
    b = root / "src" / "pkg" / "b.py"
    patch = _patch(
        {"path": "src/pkg/a.py", "action": "replace", "expected_sha256": _digest(a),
         "content": "from . import b\ndef a(): return b.b()\n"},
        {"path": "src/pkg/b.py", "action": "replace", "expected_sha256": _digest(b),
         "content": "from . import a\ndef b(): return a.a()\n"},
    )

    report = review_project_patch(snapshot, patch)

    assert report["accepted"] is False
    assert "new_dependency_cycles" in report["blocking_reasons"]
    assert ["pkg.a", "pkg.b"] in report["dependency_graph"]["new_cycles"]


def test_review_detects_removed_declared_entrypoint(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    cli = root / "src" / "pkg" / "cli.py"
    cli.write_text("def main(): return 0\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\n[project.scripts]\npkg-cli = "pkg.cli:main"\n',
        encoding="utf-8",
    )
    snapshot = capture_project_snapshot(root)
    patch = _patch({
        "path": "src/pkg/cli.py",
        "action": "delete",
        "expected_sha256": _digest(cli),
    })

    report = review_project_patch(snapshot, patch)

    assert report["accepted"] is False
    assert any(item["kind"] == "entrypoint_resolution_regressed"
               for item in report["entry_points"]["regressions"])


def test_contract_comparison_requires_explicit_boolean_policy():
    before = {"module_contracts": [], "declared_entry_points": [], "dependency_graph": {}, "imports": []}
    after = {"module_contracts": [], "declared_entry_points": [], "dependency_graph": {}, "imports": []}
    try:
        compare_project_analyses(before, after, allow_new_cycles=1)
    except ValueError as exc:
        assert "boolean" in str(exc)
    else:
        raise AssertionError("Non-boolean policy values must be rejected")
