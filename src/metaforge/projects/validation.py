from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any

from metaforge.projects.analysis import ProjectAnalysis, analyze_project
from metaforge.projects.models import ProjectContractError, ProjectSnapshot
from metaforge.projects.secrets import SecretScanPolicy, scan_project_secrets


@dataclass(frozen=True, slots=True)
class ProjectTestTarget:
    path: str
    ecosystem: str
    framework_candidates: tuple[str, ...]
    runner_availability: str
    confidence: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ecosystem": self.ecosystem,
            "framework_candidates": list(self.framework_candidates),
            "runner_availability": self.runner_availability,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def _test_kind(path: str) -> tuple[str, str] | None:
    parts = PurePosixPath(path).parts
    lowered = tuple(part.casefold() for part in parts)
    name = lowered[-1]
    suffix = PurePosixPath(name).suffix
    in_tests = any(part in {"test", "tests", "__tests__", "spec", "specs"} for part in lowered[:-1])

    if suffix == ".py" and (in_tests or name.startswith("test_") or name.endswith("_test.py")):
        return "python", "Python test file pattern"
    if name.endswith("_test.go"):
        return "go", "Go *_test.go convention"
    if suffix == ".rs" and (in_tests or name.startswith("test_")):
        return "rust", "Rust tests directory or test_*.rs pattern"
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"} and (
        re.search(r"\.(?:test|spec)\.(?:js|jsx|ts|tsx|mjs|cjs)$", name) or in_tests
    ):
        return "node", "JavaScript/TypeScript test or spec pattern"
    if suffix in {".java", ".kt"} and (
        in_tests or name.endswith(("test.java", "tests.java", "test.kt", "tests.kt"))
    ):
        return "jvm", "Java/Kotlin test source pattern"
    if suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"} and (
        in_tests or name.startswith("test_") or name.endswith(("_test.cpp", "_test.cc", "_test.c"))
    ):
        return "native", "C/C++ test path or filename pattern"
    if suffix == ".php" and in_tests:
        return "php", "PHP tests directory"
    if suffix == ".rb" and (
        in_tests or name.startswith("test_") or name.endswith("_test.rb") or name.endswith("_spec.rb")
    ):
        return "ruby", "Ruby test/spec filename or directory pattern"
    if suffix == ".swift" and in_tests:
        return "swift", "Swift tests directory"
    return None


def build_validation_plan(
    snapshot: ProjectSnapshot,
    analysis: ProjectAnalysis | None = None,
    *,
    secret_scan_policy: SecretScanPolicy | None = None,
) -> dict[str, Any]:
    """Create a deterministic static validation plan; never execute discovered tests."""
    if not isinstance(snapshot, ProjectSnapshot):
        raise ProjectContractError("A ProjectSnapshot is required")
    analysis = analysis or analyze_project(snapshot)
    if not isinstance(analysis, ProjectAnalysis):
        raise ProjectContractError("A ProjectAnalysis is required")
    if analysis.snapshot_fingerprint != snapshot.fingerprint:
        raise ProjectContractError("Analysis and validation snapshot fingerprints do not match")

    dependencies: dict[str, set[str]] = {}
    for item in analysis.dependency_manifests:
        dependencies.setdefault(item.ecosystem, set()).add(_normalized(item.name))
    python_imports: dict[str, set[str]] = {}
    for item in analysis.imports:
        root = item.target.split(".", 1)[0]
        python_imports.setdefault(item.source_path, set()).add(root)

    targets: list[ProjectTestTarget] = []
    for entry in snapshot.files:
        detected = _test_kind(entry.path)
        if detected is None:
            continue
        ecosystem, evidence = detected
        frameworks: list[str] = []
        availability = "unconfirmed"
        confidence = "medium"

        if ecosystem == "python":
            imports = python_imports.get(entry.path, set())
            if "pytest" in imports or "pytest" in dependencies.get("python", set()):
                frameworks.append("pytest")
                availability = "declared_or_imported"
            if "unittest" in imports:
                frameworks.append("unittest")
                availability = "standard_library"
            if not frameworks:
                frameworks = ["pytest", "unittest"]
                evidence += "; framework not identified from imports or manifests"
                confidence = "low"
        elif ecosystem == "go":
            frameworks = ["go_test"]
            availability = "language_default"
            confidence = "high"
        elif ecosystem == "rust":
            frameworks = ["cargo_test"]
            availability = "language_default"
            confidence = "medium"
        elif ecosystem == "node":
            for candidate in ("vitest", "jest", "mocha", "ava"):
                if _normalized(candidate) in dependencies.get("node", set()):
                    frameworks.append(candidate)
            if frameworks:
                availability = "declared_dependency"
            else:
                frameworks = ["runner_unconfirmed"]
                confidence = "low"
        elif ecosystem == "jvm":
            frameworks = ["maven_surefire"] if "pom.xml" in {
                PurePosixPath(item.path).name.casefold() for item in snapshot.files
            } else ["jvm_runner_unconfirmed"]
            availability = "manifest_default" if frameworks[0] == "maven_surefire" else "unconfirmed"
        elif ecosystem == "native":
            frameworks = ["ctest_or_project_runner"]
            confidence = "low"
        elif ecosystem == "php":
            frameworks = ["phpunit"] if "phpunit/phpunit" in dependencies.get("php", set()) else ["phpunit_unconfirmed"]
            availability = "declared_dependency" if frameworks[0] == "phpunit" else "unconfirmed"
        elif ecosystem == "ruby":
            frameworks = ["rspec"] if "rspec" in dependencies.get("ruby", set()) else ["minitest_or_rspec"]
            confidence = "low"
        elif ecosystem == "swift":
            frameworks = ["swift_test"]
            availability = "language_default"

        targets.append(ProjectTestTarget(
            entry.path, ecosystem, tuple(frameworks), availability, confidence, evidence
        ))

    targets.sort(key=lambda item: (item.ecosystem, item.path.casefold()))
    secret_report = scan_project_secrets(snapshot, secret_scan_policy)
    cycles = analysis.dependency_graph.get("cycles", [])
    unresolved_imports = [
        item.to_dict() for item in analysis.imports
        if item.resolution in {"external_or_unresolved", "relative_unresolved"}
    ]
    checks = [
        {"name": "static_architecture", "status": "completed"},
        {"name": "public_api_contracts", "status": "available"},
        {"name": "entrypoint_resolution", "status": "available"},
        {"name": "secret_scan", "status": "findings" if secret_report.findings else "no_detected_findings"},
        {"name": "test_execution", "status": "not_executed", "reason": "Toolchain-specific sandbox execution is a separate validation stage"},
        {"name": "runtime_security", "status": "not_executed", "reason": "No project runtime or build command was invoked"},
        {"name": "performance", "status": "not_executed", "reason": "No benchmark contract or performance workload was executed"},
    ]
    return {
        "schema_version": "1.0",
        "snapshot_fingerprint": snapshot.fingerprint,
        "test_targets": [item.to_dict() for item in targets],
        "checks": checks,
        "risk_summary": {
            "test_targets": len(targets),
            "dependency_cycles": len(cycles),
            "unresolved_imports": len(unresolved_imports),
            "secret_findings": len(secret_report.findings),
            "secret_findings_truncated": secret_report.findings_truncated,
            "analysis_diagnostics": len(analysis.diagnostics),
        },
        "dependency_cycles": cycles,
        "unresolved_imports": unresolved_imports,
        "secret_scan": secret_report.to_dict(),
        "execution_scope": {
            "project_code_executed": False,
            "tests_executed": False,
            "builds_executed": False,
            "package_installation_performed": False,
            "network_accessed": False,
        },
        "next_validation_stage": "Select an explicitly approved toolchain-specific sandbox profile before executing any tests",
    }
