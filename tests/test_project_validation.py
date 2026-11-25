from __future__ import annotations

import json
from pathlib import Path

import pytest

from metaforge.projects import (
    ProjectContractError,
    analyze_project,
    build_validation_plan,
    capture_project_snapshot,
)


def _project(root: Path) -> None:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "pkg" / "module.py").write_text("def value(): return 1\n", encoding="utf-8")


def test_validation_plan_discovers_test_targets_across_ecosystems(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    (root / "tests").mkdir()
    (root / "tests" / "test_api.py").write_text("import pytest\ndef test_value(): assert True\n", encoding="utf-8")
    (root / "tests" / "sample_test.go").write_text("package sample\n", encoding="utf-8")
    (root / "tests" / "smoke.rs").write_text("#[test]\nfn smoke() {}\n", encoding="utf-8")
    (root / "src" / "web").mkdir(parents=True)
    (root / "src" / "web" / "app.test.ts").write_text("describe('app', () => {});\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\n[project.optional-dependencies]\ntest = ["pytest>=8"]\n',
        encoding="utf-8",
    )
    (root / "package.json").write_text('{"devDependencies":{"vitest":"^2.0.0"}}', encoding="utf-8")
    (root / "Cargo.toml").write_text('[package]\nname = "sample"\nversion = "0.1.0"\n', encoding="utf-8")

    snapshot = capture_project_snapshot(root)
    plan = build_validation_plan(snapshot)
    targets = {item["path"]: item for item in plan["test_targets"]}

    assert targets["tests/test_api.py"]["framework_candidates"] == ["pytest"]
    assert targets["tests/sample_test.go"]["framework_candidates"] == ["go_test"]
    assert targets["tests/smoke.rs"]["framework_candidates"] == ["cargo_test"]
    assert targets["src/web/app.test.ts"]["framework_candidates"] == ["vitest"]
    assert plan["execution_scope"]["project_code_executed"] is False
    assert plan["execution_scope"]["tests_executed"] is False
    assert all(item["status"] == "not_executed" for item in plan["checks"] if item["name"] in {"test_execution", "runtime_security", "performance"})


def test_validation_plan_secret_scan_is_redacted_and_fail_closed(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    tests = root / "tests"
    tests.mkdir()
    secret = "sk-proj-" + "A" * 32
    (tests / "test_secret.py").write_text(f'API_KEY = "{secret}"\n', encoding="utf-8")

    plan = build_validation_plan(capture_project_snapshot(root))

    assert plan["risk_summary"]["secret_findings"] >= 1
    assert secret not in json.dumps(plan)
    assert plan["secret_scan"]["findings"][0]["path"] == "tests/test_secret.py"
    assert plan["secret_scan"]["findings"][0]["line"] == 1


def test_validation_plan_rejects_analysis_from_another_snapshot(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    snapshot = capture_project_snapshot(root)
    analysis = analyze_project(snapshot)
    (root / "src" / "pkg" / "module.py").write_text("def value(): return 2\n", encoding="utf-8")
    changed = capture_project_snapshot(root)

    with pytest.raises(ProjectContractError, match="fingerprints"):
        build_validation_plan(changed, analysis)


def test_validation_plan_does_not_execute_test_modules(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    tests = root / "tests"
    tests.mkdir()
    marker = root / "executed"
    (tests / "test_side_effect.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    plan = build_validation_plan(capture_project_snapshot(root))

    assert len(plan["test_targets"]) == 1
    assert not marker.exists()
    assert plan["execution_scope"]["project_code_executed"] is False


def test_validation_plan_cli_writes_static_report(tmp_path, monkeypatch):
    import sys
    from metaforge.cli import main

    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_basic.py").write_text("def test_basic(): assert True\n", encoding="utf-8")
    output = tmp_path / "validation-plan.json"
    monkeypatch.setattr(sys, "argv", [
        "metaforge", "project", "validation-plan", str(root), "--output", str(output),
    ])

    assert main() == 0
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["risk_summary"]["test_targets"] == 1
    assert plan["execution_scope"]["tests_executed"] is False
