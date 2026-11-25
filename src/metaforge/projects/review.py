from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

from metaforge.projects.analysis import ProjectAnalysis, analyze_project
from metaforge.projects.models import ProjectContractError, ProjectPatch, ProjectPolicy, ProjectSnapshot
from metaforge.projects.patches import apply_patch_to_copy


def _report(value: ProjectAnalysis | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, ProjectAnalysis):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    raise ProjectContractError("Project analysis reports are required")


def compare_project_analyses(
    before: ProjectAnalysis | dict[str, Any],
    after: ProjectAnalysis | dict[str, Any],
    *,
    allow_breaking_changes: bool = False,
    allow_new_cycles: bool = False,
    allow_unresolved_entrypoints: bool = False,
) -> dict[str, Any]:
    """Compare static architecture reports and gate risky contract regressions."""
    for name, value in (
        ("allow_breaking_changes", allow_breaking_changes),
        ("allow_new_cycles", allow_new_cycles),
        ("allow_unresolved_entrypoints", allow_unresolved_entrypoints),
    ):
        if type(value) is not bool:
            raise ProjectContractError(f"{name} must be a boolean")

    old = _report(before)
    new = _report(after)
    api_breaking: list[dict[str, Any]] = []
    api_additions: list[dict[str, Any]] = []
    old_contracts = {item["path"]: item for item in old.get("module_contracts", [])}
    new_contracts = {item["path"]: item for item in new.get("module_contracts", [])}

    for path in sorted(set(old_contracts) | set(new_contracts), key=str.casefold):
        previous = old_contracts.get(path)
        current = new_contracts.get(path)
        if previous is None:
            for name in current.get("exported_names", []):
                api_additions.append({"path": path, "kind": "module_export_added", "name": name})
            continue
        if current is None:
            for name in previous.get("exported_names", []):
                api_breaking.append({"path": path, "kind": "module_removed", "name": name})
            for symbol in previous.get("public_symbols", []):
                api_breaking.append({"path": path, "kind": "symbol_removed",
                                     "name": symbol["name"], "before": symbol.get("signature")})
            continue

        added_exports = set(current.get("exported_names", [])) - set(previous.get("exported_names", []))
        for name in sorted(added_exports):
            api_additions.append({"path": path, "kind": "export_added", "name": name})
        removed_exports = set(previous.get("exported_names", [])) - set(current.get("exported_names", []))
        for name in sorted(removed_exports):
            api_breaking.append({"path": path, "kind": "export_removed", "name": name})

        old_symbols = {item["name"]: item.get("signature", "") for item in previous.get("public_symbols", [])}
        new_symbols = {item["name"]: item.get("signature", "") for item in current.get("public_symbols", [])}
        for name in sorted(set(new_symbols) - set(old_symbols)):
            api_additions.append({"path": path, "kind": "symbol_added", "name": name,
                                  "after": new_symbols[name]})
        for name in sorted(set(old_symbols) - set(new_symbols)):
            api_breaking.append({"path": path, "kind": "symbol_removed", "name": name,
                                 "before": old_symbols[name]})
        for name in sorted(set(old_symbols) & set(new_symbols)):
            if old_symbols[name] != new_symbols[name]:
                api_breaking.append({"path": path, "kind": "signature_changed", "name": name,
                                     "before": old_symbols[name], "after": new_symbols[name]})

    old_entries = {
        (item["ecosystem"], item["manifest_path"], item["name"]): item
        for item in old.get("declared_entry_points", [])
    }
    new_entries = {
        (item["ecosystem"], item["manifest_path"], item["name"]): item
        for item in new.get("declared_entry_points", [])
    }
    entrypoint_regressions: list[dict[str, Any]] = []
    unresolved_new_entrypoints: list[dict[str, Any]] = []
    for key, previous in old_entries.items():
        current = new_entries.get(key)
        if current is None:
            if previous.get("resolution") == "resolved":
                entrypoint_regressions.append({"name": previous["name"], "kind": "entrypoint_removed",
                                               "before": previous.get("target")})
        elif previous.get("target") != current.get("target"):
            entrypoint_regressions.append({"name": previous["name"], "kind": "entrypoint_target_changed",
                                           "before": previous.get("target"), "after": current.get("target"),
                                           "resolution": current.get("resolution")})
        elif previous.get("resolution") == "resolved" and current.get("resolution") != "resolved":
            entrypoint_regressions.append({"name": previous["name"], "kind": "entrypoint_resolution_regressed",
                                           "before": previous.get("target"), "after": current.get("target"),
                                           "resolution": current.get("resolution")})
    for key, current in new_entries.items():
        if key not in old_entries and current.get("ecosystem") == "python" and current.get("resolution") != "resolved":
            unresolved_new_entrypoints.append({"name": current["name"], "target": current.get("target"),
                                               "resolution": current.get("resolution")})

    old_cycles = {tuple(sorted(group)) for group in old.get("dependency_graph", {}).get("cycles", [])}
    new_cycles = {tuple(sorted(group)) for group in new.get("dependency_graph", {}).get("cycles", [])}
    added_cycles = [list(group) for group in sorted(new_cycles - old_cycles)]

    def import_keys(report: dict[str, Any]) -> set[tuple[str, str, str]]:
        return {
            (item.get("source_path", ""), item.get("target", ""), item.get("kind", ""))
            for item in report.get("imports", [])
            if item.get("resolution") in {"external_or_unresolved", "relative_unresolved"}
        }
    old_unresolved = import_keys(old)
    new_unresolved = import_keys(new)
    new_unresolved_imports = [
        {"source_path": source, "target": target, "kind": kind}
        for source, target, kind in sorted(new_unresolved - old_unresolved)
    ]

    def dependency_keys(report: dict[str, Any]) -> set[tuple[str, str, str, str]]:
        return {
            (item.get("manifest_path", ""), item.get("ecosystem", ""), item.get("name", ""),
             item.get("requirement", ""), item.get("group", ""))
            for item in report.get("dependency_manifests", [])
        }
    old_dependencies = dependency_keys(old)
    new_dependencies = dependency_keys(new)
    dependency_changes = {
        "added": [list(item) for item in sorted(new_dependencies - old_dependencies)],
        "removed": [list(item) for item in sorted(old_dependencies - new_dependencies)],
    }

    old_modules = {item["path"] for item in old.get("modules", [])}
    new_modules = {item["path"] for item in new.get("modules", [])}
    old_isolated = set(old.get("dependency_graph", {}).get("isolated_modules", []))
    new_isolated = set(new.get("dependency_graph", {}).get("isolated_modules", []))

    blocking_reasons = []
    if api_breaking and not allow_breaking_changes:
        blocking_reasons.append("public_api_contract_regression")
    if added_cycles and not allow_new_cycles:
        blocking_reasons.append("new_dependency_cycles")
    if (entrypoint_regressions or unresolved_new_entrypoints) and not allow_unresolved_entrypoints:
        blocking_reasons.append("entrypoint_regression_or_unresolved_target")

    return {
        "schema_version": "1.0",
        "before_fingerprint": old.get("snapshot_fingerprint"),
        "after_fingerprint": new.get("snapshot_fingerprint"),
        "accepted": not blocking_reasons,
        "policy": {
            "allow_breaking_changes": allow_breaking_changes,
            "allow_new_cycles": allow_new_cycles,
            "allow_unresolved_entrypoints": allow_unresolved_entrypoints,
        },
        "blocking_reasons": blocking_reasons,
        "public_api": {"additions": api_additions, "breaking_changes": api_breaking},
        "entry_points": {
            "regressions": entrypoint_regressions,
            "new_unresolved": unresolved_new_entrypoints,
        },
        "dependency_graph": {"new_cycles": added_cycles},
        "dependencies": dependency_changes,
        "imports": {"new_unresolved": new_unresolved_imports},
        "modules": {
            "added": sorted(new_modules - old_modules, key=str.casefold),
            "removed": sorted(old_modules - new_modules, key=str.casefold),
            "newly_isolated": sorted(new_isolated - old_isolated, key=str.casefold),
        },
    }


def review_project_patch(
    snapshot: ProjectSnapshot,
    patch: ProjectPatch,
    policy: ProjectPolicy | None = None,
    *,
    allow_breaking_changes: bool = False,
    allow_new_cycles: bool = False,
    allow_unresolved_entrypoints: bool = False,
) -> dict[str, Any]:
    """Apply a multi-file patch to a disposable copy and review static contract regressions."""
    if not isinstance(snapshot, ProjectSnapshot) or not isinstance(patch, ProjectPatch):
        raise ProjectContractError("A ProjectSnapshot and ProjectPatch are required")
    policy = policy or ProjectPolicy()
    if not isinstance(policy, ProjectPolicy):
        raise ProjectContractError("A ProjectPolicy is required")

    before = analyze_project(snapshot)
    with tempfile.TemporaryDirectory(prefix="metaforge-review-") as temporary:
        destination = Path(temporary) / "candidate"
        patched_snapshot = apply_patch_to_copy(snapshot, patch, destination, policy)
        after = analyze_project(patched_snapshot)
        result = compare_project_analyses(
            before,
            after,
            allow_breaking_changes=allow_breaking_changes,
            allow_new_cycles=allow_new_cycles,
            allow_unresolved_entrypoints=allow_unresolved_entrypoints,
        )
        result["source_fingerprint"] = snapshot.fingerprint
        result["candidate_fingerprint"] = patched_snapshot.fingerprint
        result["candidate_discarded_after_review"] = True
        result["execution_scope"] = {"project_code_executed": False, "tests_executed": False}
        return result
