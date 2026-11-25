from __future__ import annotations
import ast
from dataclasses import dataclass
import hashlib
import json
import re
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from typing import Any
from metaforge.projects.models import ProjectContractError, ProjectSnapshot
from metaforge.projects.snapshot import _read_relative_regular_file

_LANGUAGES = {
    ".py": "python", ".pyi": "python", ".rs": "rust", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".java": "java", ".kt": "kotlin",
    ".cs": "csharp", ".swift": "swift", ".scala": "scala", ".rb": "ruby", ".php": "php",
    ".lua": "lua", ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
    ".hs": "haskell", ".ml": "ocaml", ".m": "objective-c", ".mm": "objective-c++",
}

@dataclass(frozen=True, slots=True)
class ProjectModule:
    path: str
    language: str
    module_name: str | None
    sha256: str
    size_bytes: int
    parse_status: str
    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "language": self.language, "module_name": self.module_name,
                "sha256": self.sha256, "size_bytes": self.size_bytes, "parse_status": self.parse_status}

@dataclass(frozen=True, slots=True)
class ProjectImport:
    source_path: str
    target: str
    line: int
    kind: str
    resolution: str
    def to_dict(self) -> dict[str, Any]:
        return {"source_path": self.source_path, "target": self.target, "line": self.line,
                "kind": self.kind, "resolution": self.resolution}

@dataclass(frozen=True, slots=True)
class ProjectSymbol:
    path: str
    name: str
    kind: str
    line: int
    signature: str
    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name, "kind": self.kind,
                "line": self.line, "signature": self.signature}

@dataclass(frozen=True, slots=True)
class ManifestDependency:
    manifest_path: str
    ecosystem: str
    name: str
    requirement: str
    group: str
    source: str = "declared"
    def to_dict(self) -> dict[str, Any]:
        return {"manifest_path": self.manifest_path, "ecosystem": self.ecosystem,
                "name": self.name, "requirement": self.requirement,
                "group": self.group, "source": self.source}

@dataclass(frozen=True, slots=True)
class ExternalDependencyUsage:
    import_name: str
    occurrences: tuple[dict[str, Any], ...]
    status: str
    matched_declaration: str | None
    confidence: str
    def to_dict(self) -> dict[str, Any]:
        return {"import_name": self.import_name, "occurrences": [dict(item) for item in self.occurrences],
                "status": self.status, "matched_declaration": self.matched_declaration,
                "confidence": self.confidence}

@dataclass(frozen=True, slots=True)
class ProjectModuleContract:
    path: str
    module_name: str | None
    declared_all: bool
    exported_names: tuple[str, ...]
    reexported_names: tuple[str, ...]
    public_symbols: tuple[ProjectSymbol, ...]
    confidence: str
    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "module_name": self.module_name,
                "declared_all": self.declared_all, "exported_names": list(self.exported_names),
                "reexported_names": list(self.reexported_names), "public_symbols": [item.to_dict() for item in self.public_symbols],
                "confidence": self.confidence}

@dataclass(frozen=True, slots=True)
class ProjectAnalysis:
    snapshot_fingerprint: str
    files_analyzed: int
    language_counts: tuple[tuple[str, int], ...]
    modules: tuple[ProjectModule, ...]
    imports: tuple[ProjectImport, ...]
    symbols: tuple[ProjectSymbol, ...]
    diagnostics: tuple[dict[str, Any], ...]
    dependency_graph: dict[str, Any]
    dependency_manifests: tuple[ManifestDependency, ...]
    external_dependency_usage: tuple[ExternalDependencyUsage, ...]
    module_contracts: tuple[ProjectModuleContract, ...]
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.2", "snapshot_fingerprint": self.snapshot_fingerprint,
            "files_analyzed": self.files_analyzed, "language_counts": dict(self.language_counts),
            "modules": [x.to_dict() for x in self.modules],
            "imports": [x.to_dict() for x in self.imports],
            "symbols": [x.to_dict() for x in self.symbols],
            "diagnostics": [dict(x) for x in self.diagnostics],
            "dependency_graph": self.dependency_graph,
            "dependency_manifests": [item.to_dict() for item in self.dependency_manifests],
            "external_dependency_usage": [item.to_dict() for item in self.external_dependency_usage],
            "module_contracts": [item.to_dict() for item in self.module_contracts],
            "analysis_scope": {"static_only": True, "executed_project_code": False,
                "python_ast": True, "other_languages": "file inventory only; dedicated parsers are future work",
                "manifest_inventory": "explicit root allow-list; declarations only; no resolution, installation, or execution",
                "external_dependency_matching": "Python top-level import names compared to normalized declared distribution names; exact-name heuristic only",
                "public_api_contracts": "literal __all__ when available; otherwise inferred public definitions; dynamic exports are warnings"},
        }

def _module_name(path: str) -> str | None:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] in {"src", "backends"}: parts.pop(0)
    if parts and parts[-1] == "__init__": parts.pop()
    return ".".join(parts) if parts and all(x.isidentifier() for x in parts) else None

def _relative_target(current: str | None, level: int, target: str | None, package_init: bool) -> str:
    parts = current.split(".") if current else []
    if parts and not package_init: parts.pop()
    remove = max(level - 1, 0)
    if remove: parts = parts[:-remove] if remove <= len(parts) else []
    if target: parts.extend(target.split("."))
    return ".".join(parts)

def _parse_python(path: str, raw: bytes, known: set[str]):
    imports, symbols, diagnostics = [], [], []
    try:
        tree = ast.parse(raw.decode("utf-8"), filename=path, type_comments=True)
    except UnicodeDecodeError:
        return imports, symbols, "invalid_utf8", [{"path": path, "code": "python-invalid-utf8", "severity": "error"}], None
    except SyntaxError as exc:
        return imports, symbols, "syntax_error", [{"path": path, "code": "python-syntax-error",
            "severity": "warning", "line": exc.lineno or 1}], None
    except RecursionError:
        return imports, symbols, "resource_limit", [{"path": path, "code": "python-ast-recursion-limit",
            "severity": "error"}], None
    current = _module_name(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                local = target in known or any(x.startswith(target + ".") for x in known)
                root_name = target.split(".", 1)[0]
                resolution = "local" if local else ("stdlib" if root_name in sys.stdlib_module_names else "external_or_unresolved")
                imports.append(ProjectImport(path, target, node.lineno, "import", resolution))
        elif isinstance(node, ast.ImportFrom):
            target = _relative_target(current, node.level, node.module, PurePosixPath(path).stem == "__init__") if node.level else (node.module or "")
            def is_known(name: str) -> bool:
                return name in known or any(module.startswith(name + ".") for module in known)
            alias_targets = []
            for alias in node.names:
                candidate = f"{target}.{alias.name}" if target else alias.name
                if alias.name != "*" and is_known(candidate):
                    alias_targets.append(candidate)
            if alias_targets:
                for alias_target in sorted(set(alias_targets)):
                    imports.append(ProjectImport(path, alias_target, node.lineno, "from_alias", "local"))
            else:
                local = bool(target) and is_known(target)
                if node.level:
                    resolution = "local" if local else "relative_unresolved"
                else:
                    root_name = (target or "").split(".", 1)[0]
                    resolution = "local" if local else ("stdlib" if root_name in sys.stdlib_module_names else "external_or_unresolved")
                imports.append(ProjectImport(path, target or ".", node.lineno, "from", resolution))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            bases = "(" + ", ".join(ast.unparse(x) for x in node.bases) + ")" if node.bases else ""
            symbols.append(ProjectSymbol(path, node.name, "class", node.lineno, f"class {node.name}{bases}"))
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and (not member.name.startswith("_") or (member.name.startswith("__") and member.name.endswith("__"))):
                    prefix = "async " if isinstance(member, ast.AsyncFunctionDef) else ""
                    signature = f"{prefix}def {node.name}.{member.name}({ast.unparse(member.args)})"
                    if member.returns: signature += f" -> {ast.unparse(member.returns)}"
                    symbols.append(ProjectSymbol(path, f"{node.name}.{member.name}", "method", member.lineno, signature))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            sig = f"{prefix}def {node.name}({ast.unparse(node.args)})"
            if node.returns: sig += f" -> {ast.unparse(node.returns)}"
            symbols.append(ProjectSymbol(path, node.name, "function", node.lineno, sig))
    return imports, symbols, "parsed", diagnostics, tree



def _literal_string_sequence(node: ast.AST | None) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        values.append(item.value)
    return tuple(values)


def _module_contract(path: str, tree: ast.Module,
                     symbols: list[ProjectSymbol]) -> tuple[ProjectModuleContract, list[dict[str, Any]]]:
    declared = False
    dynamic = False
    exported: tuple[str, ...] | None = None
    diagnostics: list[dict[str, Any]] = []
    for node in tree.body:
        value = None
        is_all = False
        if isinstance(node, ast.Assign):
            is_all = any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            is_all = isinstance(node.target, ast.Name) and node.target.id == "__all__"
            value = node.value
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == "__all__":
            is_all = True
            value = node.value
        if not is_all:
            continue
        declared = True
        literal = _literal_string_sequence(value)
        if literal is None:
            dynamic = True
            exported = None
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.op, ast.Add) and exported is not None:
                exported = exported + literal
            else:
                dynamic = True
                exported = None
        else:
            exported = literal
    all_public_symbols = tuple(sorted(
        (item for item in symbols if item.path == path),
        key=lambda item: (item.line, item.name),
    ))
    reexports: set[str] = set()
    wildcard_reexport = False
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", 1)[0]
                if not bound_name.startswith("_"):
                    reexports.add(bound_name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    wildcard_reexport = True
                    continue
                bound_name = alias.asname or alias.name
                if not bound_name.startswith("_"):
                    reexports.add(bound_name)
    reexported_names = tuple(sorted(reexports, key=str.casefold))
    inferred_names = tuple(sorted(
        {item.name for item in all_public_symbols if item.kind in {"class", "function"}} | reexports,
        key=str.casefold,
    ))
    if wildcard_reexport:
        diagnostics.append({"path": path, "code": "python-wildcard-reexport-unsupported",
                            "severity": "warning"})
    if declared and exported is not None and not dynamic:
        names = tuple(dict.fromkeys(exported))
        allowed = set(names)
        public_symbols = tuple(item for item in all_public_symbols
                               if item.name in allowed or (
                                   item.kind == "method" and item.name.split(".", 1)[0] in allowed
                               ))
        confidence = "declared_static_all"
    elif declared:
        names = inferred_names
        public_symbols = all_public_symbols
        confidence = "dynamic_all_fallback"
        diagnostics.append({"path": path, "code": "python-dynamic-all-unsupported",
                            "severity": "warning"})
    else:
        names = inferred_names
        public_symbols = all_public_symbols
        confidence = "inferred_public_definitions_and_imports" if reexports else "inferred_public_definitions"
    return ProjectModuleContract(path, _module_name(path), declared, names,
                                 reexported_names, public_symbols, confidence), diagnostics


def _has_main_guard(tree: ast.AST) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
            left, right = test.left, test.comparators[0]
            if (isinstance(left, ast.Name) and left.id == "__name__"
                    and isinstance(right, ast.Constant) and right.value == "__main__"):
                return True
            if (isinstance(right, ast.Name) and right.id == "__name__"
                    and isinstance(left, ast.Constant) and left.value == "__main__"):
                return True
    return False


def _dependency_graph(modules: list[ProjectModule], imports: list[ProjectImport],
                      entry_points: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {m.module_name: m.path for m in modules
               if m.language == "python" and m.module_name}
    nodes = sorted(by_name.keys(), key=str.casefold)
    module_for_path = {path: name for name, path in by_name.items()}
    edges_set: set[tuple[str, str]] = set()
    for item in imports:
        if item.resolution != "local":
            continue
        source = module_for_path.get(item.source_path)
        target_path = by_name.get(item.target)
        if target_path is None:
            candidates = [(name, path) for name, path in by_name.items()
                          if name.startswith(item.target + ".")]
            if candidates:
                target_path = min(candidates, key=lambda pair: (len(pair[0]), pair[0]))[1]
        target = module_for_path.get(target_path) if target_path else None
        if source and target:
            edges_set.add((source, target))
    edges = sorted(edges_set)
    adjacency = {node: [] for node in nodes}
    incoming = {node: 0 for node in nodes}
    for source, target in edges:
        adjacency[source].append(target)
        incoming[target] += 1
    for values in adjacency.values():
        values.sort()
    # Iterative Kosaraju SCC avoids recursion on deep project graphs.
    visited: set[str] = set()
    finish_order: list[str] = []
    for root in nodes:
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(adjacency[root]))]
        while stack:
            node, neighbors = stack[-1]
            try:
                neighbor = next(neighbors)
            except StopIteration:
                finish_order.append(node)
                stack.pop()
                continue
            if neighbor not in visited:
                visited.add(neighbor)
                stack.append((neighbor, iter(adjacency[neighbor])))
    reverse = {node: [] for node in nodes}
    for source, targets in adjacency.items():
        for target in targets:
            reverse[target].append(source)
    for values in reverse.values():
        values.sort()
    assigned: set[str] = set()
    cycles: list[list[str]] = []
    for root in reversed(finish_order):
        if root in assigned:
            continue
        component = []
        stack = [root]
        assigned.add(root)
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in reverse[node]:
                if neighbor not in assigned:
                    assigned.add(neighbor)
                    stack.append(neighbor)
        component.sort()
        if len(component) > 1 or root in adjacency[root]:
            cycles.append(component)
    cycles.sort(key=lambda group: tuple(group))
    isolated = sorted(node for node in nodes if not adjacency[node] and incoming[node] == 0)
    entry_point_nodes = sorted({
        module_for_path[item["path"]]
        for item in entry_points
        if item.get("path") in module_for_path
    })
    reachable: set[str] = set()
    pending = list(reversed(entry_point_nodes))
    while pending:
        node = pending.pop()
        if node in reachable:
            continue
        reachable.add(node)
        pending.extend(reversed(adjacency[node]))
    module_metrics = {
        node: {
            "in_degree": incoming[node],
            "out_degree": len(adjacency[node]),
            "is_entry_point": node in entry_point_nodes,
            "reachable_from_entry_point": node in reachable,
        }
        for node in nodes
    }
    return {
        "nodes": nodes,
        "edges": [{"source": source, "target": target} for source, target in edges],
        "cycles": cycles,
        "isolated_modules": isolated,
        "entry_points": sorted(entry_points, key=lambda item: (item["path"].casefold(), item["kind"])),
        "entry_point_nodes": entry_point_nodes,
        "module_metrics": module_metrics,
        "metrics": {"module_count": len(nodes), "edge_count": len(edges),
                    "cycle_count": len(cycles), "isolated_module_count": len(isolated),
                    "entry_point_count": len(entry_point_nodes),
                    "reachable_module_count": len(reachable)},
        "scope": "Python modules and statically recognized main guards only; no execution",
    }


_MANIFEST_NAMES = {
    "pyproject.toml": "python", "requirements.txt": "python",
    "package.json": "node", "cargo.toml": "rust", "go.mod": "go",
    "composer.json": "php", "pom.xml": "maven",
}

def _manifest_dependencies(path: str, raw: bytes) -> tuple[list[ManifestDependency], list[dict[str, Any]]]:
    """Inventory explicitly allow-listed root manifests without resolving or installing dependencies."""
    name = PurePosixPath(path).name.casefold()
    ecosystem = _MANIFEST_NAMES.get(name)
    if ecosystem is None:
        return [], []
    found: list[ManifestDependency] = []
    diagnostics: list[dict[str, Any]] = []
    def add(dep_name: Any, requirement: Any, group: str, source: str = "declared") -> None:
        if not isinstance(dep_name, str) or not isinstance(requirement, (str, int, float)):
            return
        safe_name = dep_name.strip()
        safe_requirement = str(requirement).strip()
        if (not safe_name or len(safe_name) > 256 or len(safe_requirement) > 512
                or any(ord(char) < 32 for char in safe_name + safe_requirement + group)):
            return
        if "://" in safe_requirement or "git+" in safe_requirement.casefold():
            safe_requirement = "[direct-reference-redacted]"
        found.append(ManifestDependency(path, ecosystem, safe_name, safe_requirement, group, source))
    try:
        if name == "pyproject.toml":
            data = tomllib.loads(raw.decode("utf-8"))
            project = data.get("project", {})
            if isinstance(project, dict):
                for item in project.get("dependencies", []):
                    if isinstance(item, str):
                        match = re.match(r"\s*([A-Za-z0-9_.-]+)", item)
                        if match: add(match.group(1), item, "runtime")
                optional = project.get("optional-dependencies", {})
                if isinstance(optional, dict):
                    for group, values in optional.items():
                        if isinstance(values, list):
                            for item in values:
                                if isinstance(item, str):
                                    match = re.match(r"\s*([A-Za-z0-9_.-]+)", item)
                                    if match: add(match.group(1), item, "optional:" + group)
            poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
            if isinstance(poetry, dict):
                for dep_name, requirement in poetry.items():
                    if dep_name.casefold() != "python":
                        value = json.dumps(requirement, sort_keys=True) if isinstance(requirement, (dict, list)) else requirement
                        add(dep_name, value, "poetry")
        elif name in {"package.json", "composer.json"}:
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict):
                keys = (("dependencies", "runtime"), ("devDependencies", "development"),
                        ("peerDependencies", "peer"), ("optionalDependencies", "optional")) if name == "package.json" else (("require", "runtime"), ("require-dev", "development"))
                for key, group in keys:
                    values = data.get(key, {})
                    if isinstance(values, dict):
                        for dep_name, requirement in values.items():
                            if name != "composer.json" or dep_name != "php": add(dep_name, requirement, group)
        elif name == "cargo.toml":
            data = tomllib.loads(raw.decode("utf-8"))
            for section, group in (("dependencies", "runtime"), ("dev-dependencies", "development"), ("build-dependencies", "build")):
                values = data.get(section, {})
                if isinstance(values, dict):
                    for dep_name, requirement in values.items():
                        value = json.dumps(requirement, sort_keys=True) if isinstance(requirement, (dict, list)) else requirement
                        add(dep_name, value, group)
        elif name == "go.mod":
            in_require = False
            for line in raw.decode("utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("require ("): in_require = True; continue
                if in_require and stripped == ")": in_require = False; continue
                if stripped.startswith("require "):
                    parts = stripped.split()
                    if len(parts) >= 3: add(parts[1], parts[2], "runtime", "indirect" if "// indirect" in stripped else "declared")
                elif in_require and stripped and not stripped.startswith("//"):
                    parts = stripped.split()
                    if len(parts) >= 2: add(parts[0], parts[1], "runtime", "indirect" if "// indirect" in stripped else "declared")
        elif name == "requirements.txt":
            for line in raw.decode("utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", "-", "git+", "http://", "https://")): continue
                match = re.match(r"^([A-Za-z0-9_.-]+)(.*)$", stripped)
                if match: add(match.group(1), stripped, "requirements")
        elif name == "pom.xml":
            root = ET.fromstring(raw)
            for dep in root.findall(".//{*}dependency"):
                group_id = dep.findtext("{*}groupId") or ""
                artifact_id = dep.findtext("{*}artifactId") or ""
                version = dep.findtext("{*}version") or "unspecified"
                scope = dep.findtext("{*}scope") or "compile"
                if group_id and artifact_id: add(group_id + ":" + artifact_id, version, scope)
    except (UnicodeDecodeError, ValueError, TypeError, KeyError, AttributeError, RecursionError, ET.ParseError):
        diagnostics.append({"path": path, "code": "dependency-manifest-parse-error", "severity": "warning"})
    found.sort(key=lambda item: (item.group.casefold(), item.name.casefold(), item.requirement))
    return found, diagnostics



def _external_dependency_usage(imports: list[ProjectImport],
                              manifests: list[ManifestDependency]) -> tuple[ExternalDependencyUsage, ...]:
    """Compare Python import roots with declared Python distributions by exact normalized name only."""
    declared: dict[str, str] = {}
    for item in manifests:
        if item.ecosystem != "python":
            continue
        normalized = re.sub(r"[-_.]+", "-", item.name).casefold()
        declared.setdefault(normalized, item.name)
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for item in imports:
        if item.resolution != "external_or_unresolved":
            continue
        root_name = item.target.split(".", 1)[0]
        if not root_name:
            continue
        occurrences.setdefault(root_name, []).append({"source_path": item.source_path, "line": item.line})
    result = []
    for import_name, sites in sorted(occurrences.items(), key=lambda pair: pair[0].casefold()):
        matched = declared.get(re.sub(r"[-_.]+", "-", import_name).casefold())
        result.append(ExternalDependencyUsage(
            import_name=import_name,
            occurrences=tuple(sorted(sites, key=lambda item: (item["source_path"].casefold(), item["line"]))),
            status="exact_name_match" if matched else "no_exact_name_match",
            matched_declaration=matched,
            confidence="exact_name_heuristic" if matched else "unresolved_mapping",
        ))
    return tuple(result)


def analyze_project(snapshot: ProjectSnapshot) -> ProjectAnalysis:
    """Build a deterministic static inventory; never import or execute project source."""
    if not isinstance(snapshot, ProjectSnapshot): raise ProjectContractError("A ProjectSnapshot is required")
    files = []
    modules, imports, symbols, diagnostics, counts, entry_points, manifest_dependencies, module_contracts = [], [], [], [], {}, [], [], []
    for entry in snapshot.files:
        language = _LANGUAGES.get(PurePosixPath(entry.path).suffix.casefold())
        is_manifest = PurePosixPath(entry.path).name.casefold() in _MANIFEST_NAMES and "/" not in entry.path
        if not language and not is_manifest: continue
        raw = _read_relative_regular_file(snapshot.root, entry.path, entry.size_bytes)
        if hashlib.sha256(raw).hexdigest() != entry.sha256:
            raise ProjectContractError(f"Project file changed after snapshot capture: {entry.path}")
        if is_manifest:
            deps, manifest_diags = _manifest_dependencies(entry.path, raw)
            manifest_dependencies.extend(deps)
            diagnostics.extend(manifest_diags)
            continue
        files.append((entry.path, language, raw))
    known = {name for path, lang, _ in files if lang == "python" and (name := _module_name(path))}
    for path, language, raw in files:
        counts[language] = counts.get(language, 0) + 1
        module, status = (_module_name(path), "inventory_only") if language == "python" else (None, "inventory_only")
        if language == "python":
            imps, syms, status, diags, tree = _parse_python(path, raw, known)
            imports.extend(imps); symbols.extend(syms); diagnostics.extend(diags)
            if status == "parsed":
                contract, contract_diags = _module_contract(path, tree, syms)
                module_contracts.append(contract)
                diagnostics.extend(contract_diags)
            if status == "parsed":
                if PurePosixPath(path).name == "__main__.py":
                    entry_points.append({"path": path, "module": (_module_name(path) or "").removesuffix(".__main__"), "kind": "python_module"})
                if _has_main_guard(tree):
                    entry_points.append({"path": path, "module": _module_name(path), "kind": "main_guard"})
        modules.append(ProjectModule(path, language, module, hashlib.sha256(raw).hexdigest(), len(raw), status))
    modules.sort(key=lambda x: x.path.casefold())
    imports.sort(key=lambda x: (x.source_path.casefold(), x.line, x.target))
    symbols.sort(key=lambda x: (x.path.casefold(), x.line, x.name))
    diagnostics.sort(key=lambda x: (str(x.get("path", "")).casefold(), int(x.get("line", 0))))
    graph = _dependency_graph(modules, imports, entry_points)
    ordered_manifests = tuple(sorted(manifest_dependencies, key=lambda item: (item.ecosystem, item.manifest_path, item.group, item.name.casefold(), item.requirement)))
    external_usage = _external_dependency_usage(imports, manifest_dependencies)
    module_contracts.sort(key=lambda item: item.path.casefold())
    return ProjectAnalysis(snapshot.fingerprint, len(modules), tuple(sorted(counts.items())),
        tuple(modules), tuple(imports), tuple(symbols), tuple(diagnostics), graph,
        ordered_manifests, external_usage, tuple(module_contracts))
