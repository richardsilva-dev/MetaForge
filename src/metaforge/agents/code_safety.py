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
_FORBIDDEN_ATTRIBUTES = frozenset({
    "modules", "path", "meta_path", "path_hooks", "settrace", "setprofile",
    "_getframe", "exit", "environ", "system", "popen", "spawn",
})
_FORBIDDEN_NAMES = frozenset({
    "__builtins__", "__loader__", "__spec__", "__file__", "__cached__", "__package__",
})


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
        except SyntaxError as exc:
            return CodeReviewResult(False, digest, (), (f"Syntax error on line {exc.lineno}",))

        for statement in tree.body:
            if isinstance(statement, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
                if isinstance(statement, ast.FunctionDef) and (
                    statement.decorator_list or statement.args.defaults
                    or any(value is not None for value in statement.args.kw_defaults)
                    or statement.returns is not None
                    or any(arg.annotation is not None for arg in (
                        statement.args.posonlyargs + statement.args.args + statement.args.kwonlyargs
                    ))
                    or (statement.args.vararg and statement.args.vararg.annotation is not None)
                    or (statement.args.kwarg and statement.args.kwarg.annotation is not None)
                ):
                    findings.append("Top-level functions cannot use decorators, defaults, or annotations")
            elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
                if statement is not tree.body[0]:
                    findings.append("Only the module docstring is allowed as a top-level expression")
            elif isinstance(statement, ast.Assign):
                if not all(isinstance(target, ast.Name) for target in statement.targets) or not self._is_literal(
                    statement.value
                ):
                    findings.append("Top-level assignments must use simple literal constants")
            else:
                findings.append(f"Top-level statement not allowed: {type(statement).__name__}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    imports.add(root)
                    if root not in _ALLOWED_IMPORTS:
                        findings.append(f"Import not allowed: {root}")
                    if alias.asname and alias.asname.startswith("_"):
                        findings.append("Private import aliases are not allowed")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if node.level or not root:
                    findings.append("Relative imports are not allowed")
                imports.add(root)
                if root not in _ALLOWED_IMPORTS:
                    findings.append(f"Import not allowed: {root}")
                for alias in node.names:
                    if alias.name.startswith("_") or (alias.asname and alias.asname.startswith("_")):
                        findings.append("Private imports are not allowed")
            elif isinstance(node, ast.Name):
                if node.id in _FORBIDDEN_NAMES:
                    findings.append(f"Restricted runtime name: {node.id}")
            elif isinstance(node, ast.Attribute):
                if node.attr.startswith("__") or node.attr in _FORBIDDEN_ATTRIBUTES:
                    findings.append(f"Restricted attribute: {node.attr}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                    findings.append(f"Restricted call: {node.func.id}")
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in {"write_text", "write_bytes", "unlink", "chmod", "mkdir"}:
                        findings.append(f"Filesystem mutation call: {node.func.attr}")
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                findings.append("Global and nonlocal declarations are not allowed")

        unique_findings = tuple(dict.fromkeys(findings))
        return CodeReviewResult(not unique_findings, digest, tuple(sorted(imports)), unique_findings)
