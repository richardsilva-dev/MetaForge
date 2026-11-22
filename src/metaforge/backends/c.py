from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from metaforge.backends.protocol import BackendPreparation, BackendRun
from metaforge.candidates.models import Candidate


class CBackend:
    """Compile and run the repository-owned C backend; generated source is never accepted."""

    name = "c"
    supported_strategies = frozenset({"builtin", "heapsort", "shellsort"})

    def __init__(self, compile_timeout_seconds: float = 120.0, timeout_seconds: float = 10.0) -> None:
        if compile_timeout_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("Backend timeouts must be positive")
        self.compile_timeout_seconds = compile_timeout_seconds
        self.timeout_seconds = timeout_seconds
        self.repository_root = Path(__file__).resolve().parents[3]
        self.source_path = self.repository_root / "backends" / "c" / "main.c"
        self.build_dir = self.repository_root / ".artifacts" / "backends" / "c"
        self.binary_path = self.build_dir / (
            "metaforge-c-backend.exe" if sys.platform == "win32" else "metaforge-c-backend"
        )
        self._preparation: BackendPreparation | None = None
        self._prepare_lock = threading.Lock()

    def prepare(self) -> BackendPreparation:
        if self._preparation is not None:
            return self._preparation
        with self._prepare_lock:
            if self._preparation is not None:
                return self._preparation
            compiler = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
            if not compiler:
                raise RuntimeError("C backend requires cc, clang, or gcc on PATH")
            self.build_dir.mkdir(parents=True, exist_ok=True)
            env = {"PATH": os.environ.get("PATH", "")}
            version = subprocess.run(
                [compiler, "--version"], capture_output=True, text=True,
                timeout=10, env=env, check=True,
            ).stdout.splitlines()[0]
            started = time.perf_counter()
            subprocess.run(
                [
                    compiler, "-std=c11", "-O3", "-Wall", "-Wextra", "-Werror",
                    str(self.source_path), "-o", str(self.binary_path),
                ],
                cwd=self.repository_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.compile_timeout_seconds,
                check=True,
            )
            compile_seconds = time.perf_counter() - started
            if not self.binary_path.is_file():
                raise RuntimeError("C build completed without producing the expected executable")
            source_hash = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
            artifact_hash = hashlib.sha256(self.binary_path.read_bytes()).hexdigest()
            self._preparation = BackendPreparation(
                self.name,
                version,
                compile_seconds,
                source_hash,
                artifact_hash,
                ("-std=c11", "-O3", "-Wall", "-Wextra", "-Werror"),
            )
            return self._preparation

    def run_repeated(
        self,
        candidate: Candidate,
        values: list[int],
        repeats: int = 1,
        timeout_seconds: float | None = None,
    ) -> list[BackendRun]:
        if candidate.strategy not in self.supported_strategies:
            return [BackendRun(False, None, None, None, "c_algorithm_internal_elapsed",
                               "Candidate strategy is not supported by the C backend")]
        if type(repeats) is not int or not 1 <= repeats <= 20:
            return [BackendRun(False, None, None, None, "c_algorithm_internal_elapsed",
                               "Repeats must be 1..20")]
        if not isinstance(values, list) or len(values) > 100_000:
            return [BackendRun(False, None, None, None, "c_algorithm_internal_elapsed",
                               "Input limit exceeded")]
        if any(type(value) is not int or not -(2**63) <= value < 2**63 for value in values):
            return [BackendRun(False, None, None, None, "c_algorithm_internal_elapsed",
                               "C backend accepts signed 64-bit integers only")]
        try:
            self.prepare()
        except Exception as exc:
            return [BackendRun(False, None, None, None, "c_algorithm_internal_elapsed",
                               f"Backend preparation failed: {type(exc).__name__}")]
        local_timeout = self.timeout_seconds if timeout_seconds is None else min(
            self.timeout_seconds, timeout_seconds
        )
        if local_timeout <= 0:
            return [BackendRun(False, None, None, None, "c_algorithm_internal_elapsed",
                               "Execution budget exhausted")]

        payload = f"{candidate.strategy}\n{len(values)}\n{' '.join(map(str, values))}\n"
        results: list[BackendRun] = []
        for _ in range(repeats):
            try:
                process = subprocess.run(
                    [str(self.binary_path)],
                    input=payload,
                    text=True,
                    capture_output=True,
                    timeout=local_timeout,
                    env={"PATH": os.environ.get("PATH", "")},
                    cwd=self.repository_root,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                results.append(BackendRun(
                    False, None, None, None, "c_algorithm_internal_elapsed", "C execution timed out"
                ))
                continue
            if process.returncode != 0:
                results.append(BackendRun(
                    False, None, None, None, "c_algorithm_internal_elapsed",
                    "C worker rejected the request or failed",
                ))
                continue
            lines = process.stdout.splitlines()
            try:
                elapsed_ns = int(lines[0])
                output = [int(value) for value in lines[1].split()] if len(lines) > 1 else []
            except (ValueError, IndexError):
                results.append(BackendRun(
                    False, None, None, None, "c_algorithm_internal_elapsed",
                    "C worker returned malformed output",
                ))
                continue
            results.append(BackendRun(
                True,
                output,
                elapsed_ns / 1_000_000_000,
                None,
                "c_algorithm_internal_elapsed",
            ))
        return results
