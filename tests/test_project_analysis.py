from pathlib import Path
import pytest
from metaforge.projects import ProjectContractError, analyze_project, capture_project_snapshot

def _project(root: Path):
    (root / "src" / "sample").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "src" / "sample" / "__init__.py").write_text("from .service import calculate\n", encoding="utf-8")
    (root / "src" / "sample" / "service.py").write_text(
        "import json\nfrom .models import Record\nclass Service: pass\ndef calculate(value: int) -> int:\n    return value + 1\n", encoding="utf-8")
    (root / "src" / "sample" / "models.py").write_text("class Record: pass\n", encoding="utf-8")
    (root / "backends").mkdir()
    (root / "backends" / "native.rs").write_text("pub fn run() {}\n", encoding="utf-8")
    (root / "docs" / "design.md").write_text("# design\n", encoding="utf-8")

def test_analysis_is_static_deterministic_and_builds_import_graph(tmp_path):
    root = tmp_path / "project"; root.mkdir(); _project(root)
    snapshot = capture_project_snapshot(root)
    result = analyze_project(snapshot)
    assert result == analyze_project(snapshot)
    report = result.to_dict()
    assert report["analysis_scope"]["executed_project_code"] is False
    assert report["language_counts"] == {"python": 3, "rust": 1}
    assert any(x["target"] == "sample.models" and x["resolution"] == "local" for x in report["imports"])
    assert any(x["source_path"] == "src/sample/__init__.py" and x["target"] == "sample.service" for x in report["imports"])
    assert any(x["name"] == "calculate" and x["signature"] == "def calculate(value: int) -> int" for x in report["symbols"])

def test_analysis_reports_syntax_errors(tmp_path):
    root = tmp_path / "project"; root.mkdir(); _project(root)
    (root / "src" / "sample" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    report = analyze_project(capture_project_snapshot(root)).to_dict()
    assert any(x["code"] == "python-syntax-error" for x in report["diagnostics"])

def test_analysis_rejects_stale_snapshot(tmp_path):
    root = tmp_path / "project"; root.mkdir(); _project(root)
    snapshot = capture_project_snapshot(root)
    (root / "src" / "sample" / "service.py").write_text("changed = True\n", encoding="utf-8")
    with pytest.raises(ProjectContractError, match="changed after snapshot"):
        analyze_project(snapshot)

def test_analysis_never_executes_project_source(tmp_path):
    root = tmp_path / "project"; root.mkdir(); _project(root)
    marker = root / "executed.txt"
    (root / "src" / "sample" / "danger.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n", encoding="utf-8")
    analyze_project(capture_project_snapshot(root))
    assert not marker.exists()


def test_unresolved_relative_import_is_not_reported_as_local(tmp_path):
    root = tmp_path / "project"; root.mkdir(); _project(root)
    (root / "src" / "sample" / "service.py").write_text("from .missing import Thing\n", encoding="utf-8")
    report = analyze_project(capture_project_snapshot(root)).to_dict()
    edge = next(x for x in report["imports"] if x["target"] == "sample.missing")
    assert edge["resolution"] == "external_or_unresolved"


def test_dependency_graph_detects_cycles_isolated_modules_and_entry_points(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _project(root)
    package = root / "src" / "sample"
    (package / "alpha.py").write_text("from .beta import value\\n", encoding="utf-8")
    (package / "beta.py").write_text("from .alpha import value\\n", encoding="utf-8")
    (package / "lonely.py").write_text("VALUE = 1\\n", encoding="utf-8")
    (package / "runner.py").write_text('if __name__ == "__main__":\\n    pass\\n', encoding="utf-8")
    (package / "__main__.py").write_text("from .service import calculate\\n", encoding="utf-8")

    graph = analyze_project(capture_project_snapshot(root)).to_dict()["dependency_graph"]

    assert ["sample.alpha", "sample.beta"] in graph["cycles"]
    assert "sample.lonely" in graph["isolated_modules"]
    assert {"path": "src/sample/runner.py", "module": "sample.runner", "kind": "main_guard"} in graph["entry_points"]
    assert {"path": "src/sample/__main__.py", "module": "sample", "kind": "python_module"} in graph["entry_points"]
    assert graph["metrics"]["cycle_count"] == 1
    assert graph["metrics"]["module_count"] == len(graph["nodes"])
