from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib


_ALLOWED_IMPORTS = frozenset({
    "heapq", "bisect", "math", "operator", "collections",
    "itertools", "functools", "array", "typing",
})
_FORBIDDEN_CALLS = frozenset({
    "eval", "exec", "compile", "__import__", "input", "print", "open", "breakpoint", "help",
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "dir",
})
# These operator helpers turn strings into attribute/method access. They can
# otherwise bypass the direct ast.Attribute dunder-name checks below.
_FORBIDDEN_OPERATOR_REFLECTION = frozenset({"attrgetter", "methodcaller"})
_FORBIDDEN_ATTRIBUTES = frozenset({
    "modules", "path", "meta_path", "path_hooks", "settrace", "setprofile",
    "_getframe", "exit", "environ", "system", "popen", "spawn",
})
_FORBIDDEN_NAMES = frozenset({
    "__builtins__", "__loader__", "__spec__", "__file__", "__cached__", "__package__",
})
_MAX_AST_NODES = 20_000
_MAX_FINDINGS = 100


@dataclass(frozen=True, slots=True)
class CodeReviewResult:
    approved: bool
    source_sha256: str
    imports: tuple[str, ...]
    findings: tuple[str, ...]


class StaticCodeReviewer:
    """Conservative AST policy gate; not a substitute for the container sandbox."""

    def __init__(self, max_source_bytes: int = 50_000) -> None:
        if type(max_source_bytes) is not int or not 1024 <= max_source_bytes <= 256_000:
            raise ValueError("max_source_bytes must be 1024..256000")
        self.max_source_bytes = max_source_bytes

    @staticmethod
    def _is_literal(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return all(StaticCodeReviewer._is_literal(item) for item in node.elts)
        if isinstance(node, ast.Dict):
            return all(
                key is not None and StaticCodeReviewer._is_literal(key)
                and StaticCodeReviewer._is_literal(value)
                for key, value in zip(node.keys, node.values)
            )
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub, ast.Not)):
            return StaticCodeReviewer._is_literal(node.operand)
        return False

    def review(self, source_code: str) -> CodeReviewResult:
        findings: list[str] = []
        imports: set[str] = set()

        def add_finding(message: str) -> None:
            if len(findings) < _MAX_FINDINGS:
                findings.append(message)
            elif len(findings) == _MAX_FINDINGS:
                findings.append("Additional findings omitted by the reviewer limit")

        if not isinstance(source_code, str):
            return CodeReviewResult(False, "", (), ("Source must be text",))
        try:
            source_bytes = source_code.encode("utf-8")
        except UnicodeEncodeError:
            return CodeReviewResult(False, "", (), ("Source is not valid UTF-8",))
        digest = hashlib.sha256(source_bytes).hexdigest()
        if not source_bytes or len(source_bytes) > self.max_source_bytes:
            return CodeReviewResult(False, digest, (), ("Source size limit exceeded",))
        try:
            tree = ast.parse(source_code, mode="exec")
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            line = getattr(exc, "lineno", None)
            detail = f" on line {line}" if isinstance(line, int) else ""
            return CodeReviewResult(
                False, digest, (), (f"Source could not be parsed safely{detail}",)
            )

        # Materialize once so the size is bounded before any policy traversal.
        try:
            nodes = tuple(ast.walk(tree))
        except (RecursionError, MemoryError):
            return CodeReviewResult(
                False, digest, (), ("AST traversal exceeded complexity limits",)
            )
        if len(nodes) > _MAX_AST_NODES:
            return CodeReviewResult(
                False, digest, (), (f"AST node limit exceeded ({_MAX_AST_NODES})",)
            )

        operator_module_names = {"operator"}
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    imports.add(root)
                    if root not in _ALLOWED_IMPORTS:
                        add_finding(f"Import not allowed: {root}")
                    if alias.asname and alias.asname.startswith("_"):
                        add_finding("Private import aliases are not allowed")
                    if root == "operator":
                        operator_module_names.add(alias.asname or root)
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if node.level or not root:
                    add_finding("Relative imports are not allowed")
                imports.add(root)
                if root not in _ALLOWED_IMPORTS:
                    add_finding(f"Import not allowed: {root}")
                for alias in node.names:
                    if alias.name == "*":
                        add_finding("Wildcard imports are not allowed")
                        continue
                    if alias.name.startswith("_") or (alias.asname and alias.asname.startswith("_")):
                        add_finding("Private imports are not allowed")
                    if root == "operator" and alias.name in _FORBIDDEN_OPERATOR_REFLECTION:
                        add_finding(f"Reflection helper import not allowed: operator.{alias.name}")
            elif isinstance(node, ast.FunctionDef):
                if (
                    node.decorator_list or node.args.defaults
                    or any(value is not None for value in node.args.kw_defaults)
                    or node.returns is not None
                    or any(arg.annotation is not None for arg in (
                        node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                    ))
                    or (node.args.vararg and node.args.vararg.annotation is not None)
                    or (node.args.kwarg and node.args.kwarg.annotation is not None)
                ):
                    add_finding("Functions cannot use decorators, defaults, or annotations")
            elif isinstance(node, ast.ClassDef):
                add_finding("Class definitions are not allowed")
            elif isinstance(node, ast.AsyncFunctionDef):
                add_finding("Async functions are not allowed")
            elif isinstance(node, ast.Name):
                if node.id in _FORBIDDEN_NAMES:
                    add_finding(f"Restricted runtime name: {node.id}")
            elif isinstance(node, ast.Attribute):
                if node.attr.startswith("__") or node.attr in _FORBIDDEN_ATTRIBUTES:
                    add_finding(f"Restricted attribute: {node.attr}")
                if (
                    node.attr in _FORBIDDEN_OPERATOR_REFLECTION
                    and isinstance(node.value, ast.Name)
                    and node.value.id in operator_module_names
                ):
                    add_finding(f"Reflection helper call is not allowed: operator.{node.attr}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                    add_finding(f"Restricted call: {node.func.id}")
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in {"write_text", "write_bytes", "unlink", "chmod", "mkdir"}:
                        add_finding(f"Filesystem mutation call: {node.func.attr}")
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                add_finding("Global and nonlocal declarations are not allowed")

        unique_findings = tuple(dict.fromkeys(findings))
        return CodeReviewResult(not unique_findings, digest, tuple(sorted(imports)), unique_findings)
