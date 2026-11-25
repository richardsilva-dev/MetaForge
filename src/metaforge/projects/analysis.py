from __future__ import annotations
import ast
from dataclasses import dataclass
import hashlib
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
class ProjectAnalysis:
    snapshot_fingerprint: str
    files_analyzed: int
    language_counts: tuple[tuple[str, int], ...]
    modules: tuple[ProjectModule, ...]
    imports: tuple[ProjectImport, ...]
    symbols: tuple[ProjectSymbol, ...]
    diagnostics: tuple[dict[str, Any], ...]
    dependency_graph: dict[str, Any]
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0", "snapshot_fingerprint": self.snapshot_fingerprint,
            "files_analyzed": self.files_analyzed, "language_counts": dict(self.language_counts),
            "modules": [x.to_dict() for x in self.modules],
            "imports": [x.to_dict() for x in self.imports],
            "symbols": [x.to_dict() for x in self.symbols],
            "diagnostics": [dict(x) for x in self.diagnostics],
            "dependency_graph": self.dependency_graph,
            "analysis_scope": {"static_only": True, "executed_project_code": False,
                "python_ast": True, "other_languages": "file inventory only; dedicated parsers are future work"},
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
        return imports, symbols, "invalid_utf8", [{"path": path, "code": "python-invalid-utf8", "severity": "error"}]
    except SyntaxError as exc:
        return imports, symbols, "syntax_error", [{"path": path, "code": "python-syntax-error",
            "severity": "warning", "line": exc.lineno or 1}]
    except RecursionError:
        return imports, symbols, "resource_limit", [{"path": path, "code": "python-ast-recursion-limit",
            "severity": "error"}]
    current = _module_name(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                local = target in known or any(x.startswith(target + ".") for x in known)
                imports.append(ProjectImport(path, target, node.lineno, "import",
                    "local" if local else "external_or_unresolved"))
        elif isinstance(node, ast.ImportFrom):
            target = _relative_target(current, node.level, node.module, PurePosixPath(path).stem == "__init__") if node.level else (node.module or "")
            local = bool(target) and (target in known or any(x.startswith(target + ".") for x in known))
            imports.append(ProjectImport(path, target or ".", node.lineno, "from",
                "local" if local else "external_or_unresolved"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            bases = "(" + ", ".join(ast.unparse(x) for x in node.bases) + ")" if node.bases else ""
            symbols.append(ProjectSymbol(path, node.name, "class", node.lineno, f"class {node.name}{bases}"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            sig = f"{prefix}def {node.name}({ast.unparse(node.args)})"
            if node.returns: sig += f" -> {ast.unparse(node.returns)}"
            symbols.append(ProjectSymbol(path, node.name, "function", node.lineno, sig))
    return imports, symbols, "parsed", diagnostics


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
    nodes = sorted(by_name.values(), key=str.casefold)
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
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                finish_order.append(node)
                continue
            stack.append((node, True))
            for neighbor in reversed(adjacency[node]):
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append((neighbor, False))
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
    return {
        "nodes": nodes,
        "edges": [{"source": source, "target": target} for source, target in edges],
        "cycles": cycles,
        "isolated_modules": isolated,
        "entry_points": sorted(entry_points, key=lambda item: (item["path"].casefold(), item["kind"])),
        "metrics": {"module_count": len(nodes), "edge_count": len(edges),
                    "cycle_count": len(cycles), "isolated_module_count": len(isolated)},
        "scope": "Python modules and statically recognized main guards only; no execution",
    }


def analyze_project(snapshot: ProjectSnapshot) -> ProjectAnalysis:
    """Build a deterministic static inventory; never import or execute project source."""
    if not isinstance(snapshot, ProjectSnapshot): raise ProjectContractError("A ProjectSnapshot is required")
    files = []
    for entry in snapshot.files:
        language = _LANGUAGES.get(PurePosixPath(entry.path).suffix.casefold())
        if not language: continue
        raw = _read_relative_regular_file(snapshot.root, entry.path, entry.size_bytes)
        if hashlib.sha256(raw).hexdigest() != entry.sha256:
            raise ProjectContractError(f"Project file changed after snapshot capture: {entry.path}")
        files.append((entry.path, language, raw))
    known = {name for path, lang, _ in files if lang == "python" and (name := _module_name(path))}
    modules, imports, symbols, diagnostics, counts, entry_points = [], [], [], [], {}, []
    for path, language, raw in files:
        counts[language] = counts.get(language, 0) + 1
        module, status = (_module_name(path), "inventory_only") if language == "python" else (None, "inventory_only")
        if language == "python":
            imps, syms, status, diags = _parse_python(path, raw, known)
            imports.extend(imps); symbols.extend(syms); diagnostics.extend(diags)
            if status == "parsed":
                try:
                    tree = ast.parse(raw.decode("utf-8"), filename=path, type_comments=True)
                    if PurePosixPath(path).name == "__main__.py":
                        entry_points.append({"path": path, "module": (_module_name(path) or "").removesuffix(".__main__"), "kind": "python_module"})
                    if _has_main_guard(tree):
                        entry_points.append({"path": path, "module": _module_name(path), "kind": "main_guard"})
                except (SyntaxError, RecursionError, UnicodeDecodeError):
                    pass
        modules.append(ProjectModule(path, language, module, hashlib.sha256(raw).hexdigest(), len(raw), status))
    modules.sort(key=lambda x: x.path.casefold())
    imports.sort(key=lambda x: (x.source_path.casefold(), x.line, x.target))
    symbols.sort(key=lambda x: (x.path.casefold(), x.line, x.name))
    diagnostics.sort(key=lambda x: (str(x.get("path", "")).casefold(), int(x.get("line", 0))))
    graph = _dependency_graph(modules, imports, entry_points)
    return ProjectAnalysis(snapshot.fingerprint, len(modules), tuple(sorted(counts.items())),
        tuple(modules), tuple(imports), tuple(symbols), tuple(diagnostics), graph)
