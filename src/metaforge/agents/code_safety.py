from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib


_ALLOWED_IMPORTS = frozenset({
    "json", "sys", "heapq", "bisect", "math", "operator", "collections",
    "itertools", "functools", "array", "typing",
})
_FORBIDDEN_CALLS = frozenset({
    "eval", "exec", "compile", "__import__", "input", "breakpoint", "help",
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

    def __init__(self, max_source_bytes: int = 200_000) -> None:
        if type(max_source_bytes) is not int or not 1024 <= max_source_bytes <= 256_000:
            raise ValueError("max_source_bytes must be 1024..256000")
        self.max_source_bytes = max_source_bytes

    @staticmethod
    def _is_sys_argv_one(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "sys"
            and node.value.attr == "argv"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == 1
        )

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
                if isinstance(node.func, ast.Name):
                    if node.func.id in _FORBIDDEN_CALLS:
                        findings.append(f"Restricted call: {node.func.id}")
                    elif node.func.id == "open":
                        path_ok = bool(node.args) and self._is_sys_argv_one(node.args[0])
                        mode_ok = len(node.args) < 2 or (
                            isinstance(node.args[1], ast.Constant)
                            and node.args[1].value in {"r", "rt"}
                        )
                        for keyword in node.keywords:
                            if keyword.arg == "mode":
                                mode_ok = (
                                    isinstance(keyword.value, ast.Constant)
                                    and keyword.value.value in {"r", "rt"}
                                )
                        if not path_ok or not mode_ok:
                            findings.append("File access is limited to read-only sys.argv[1]")
                elif isinstance(node.func, ast.Attribute):
                    if node.func.attr in {"write_text", "write_bytes", "unlink", "chmod", "mkdir"}:
                        findings.append(f"Filesystem mutation call: {node.func.attr}")
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                findings.append("Global and nonlocal declarations are not allowed")

        unique_findings = tuple(dict.fromkeys(findings))
        return CodeReviewResult(not unique_findings, digest, tuple(sorted(imports)), unique_findings)
