from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any

from metaforge.sandbox.policy import SandboxPolicy


@dataclass(frozen=True, slots=True)
class SandboxPreparation:
    runtime: str
    runtime_version: str
    image: str
    rootless: bool


@dataclass(frozen=True, slots=True)
class SandboxResult:
    success: bool
    output: list[int] | None
    wall_seconds: float
    measurement_scope: str
    error: str | None = None


class SandboxRunner:
    """Fail-closed container execution for Python source supplied as untrusted data."""

    def __init__(
        self,
        policy: SandboxPolicy,
        runtime_executable: str | None = None,
    ) -> None:
        self.policy = policy
        self.runtime_executable = runtime_executable
        self._preparation: SandboxPreparation | None = None
        self._prepare_lock = threading.Lock()

    def _runtime_env(self) -> dict[str, str]:
        allowed = (
            "PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
            "CONTAINER_HOST", "XDG_RUNTIME_DIR", "SYSTEMROOT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        )
        return {key: os.environ[key] for key in allowed if os.environ.get(key)}

    def prepare(self) -> SandboxPreparation:
        if self._preparation is not None:
            return self._preparation
        with self._prepare_lock:
            if self._preparation is not None:
                return self._preparation
            runtime = self.runtime_executable or shutil.which(self.policy.runtime)
            if not runtime:
                raise RuntimeError(f"Required sandbox runtime '{self.policy.runtime}' is unavailable")
            env = self._runtime_env()
            if self.policy.runtime == "podman":
                info = subprocess.run(
                    [runtime, "info", "--format", "{{.Host.Security.Rootless}}"],
                    capture_output=True, text=True, timeout=10, env=env, check=True,
                )
                rootless = info.stdout.strip().lower() == "true"
            else:
                info = subprocess.run(
                    [runtime, "info", "--format", "{{json .SecurityOptions}}"],
                    capture_output=True, text=True, timeout=10, env=env, check=True,
                )
                rootless = "rootless" in info.stdout.lower()
            if not rootless:
                raise RuntimeError("Sandbox runtime must operate in rootless mode; refusing to continue")
            version = subprocess.run(
                [runtime, "--version"], capture_output=True, text=True,
                timeout=10, env=env, check=True,
            ).stdout.strip()
            subprocess.run(
                [runtime, "image", "inspect", self.policy.image],
                capture_output=True, text=True, timeout=10, env=env, check=True,
            )
            self._preparation = SandboxPreparation(
                self.policy.runtime, version, self.policy.image, True
            )
            return self._preparation

    def _stop_container(self, runtime: str, cid_file: Path, env: dict[str, str]) -> None:
        try:
            container_id = cid_file.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if not re.fullmatch(r"[A-Fa-f0-9]{12,64}", container_id):
            return
        for command in ([runtime, "kill", container_id], [runtime, "rm", "-f", container_id]):
            try:
                subprocess.run(
                    command, capture_output=True, timeout=3, env=env, check=False
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

    def run_python(self, source_code: str, values: list[int]) -> SandboxResult:
        started = time.perf_counter()
        scope = "rootless_container_end_to_end_wall"
        if not isinstance(source_code, str):
            return SandboxResult(False, None, time.perf_counter() - started, scope, "Source must be text")
        try:
            source_bytes = source_code.encode("utf-8")
        except UnicodeEncodeError:
            return SandboxResult(False, None, time.perf_counter() - started, scope, "Source is not valid UTF-8")
        if not source_bytes or len(source_bytes) > self.policy.max_source_bytes:
            return SandboxResult(False, None, time.perf_counter() - started, scope, "Source size limit exceeded")
        if not isinstance(values, list) or len(values) > self.policy.max_input_items:
            return SandboxResult(False, None, time.perf_counter() - started, scope, "Input size limit exceeded")
        if any(type(value) is not int or not -(2**63) <= value < 2**63 for value in values):
            return SandboxResult(False, None, time.perf_counter() - started, scope,
                                 "Input must contain signed 64-bit integers")
        try:
            preparation = self.prepare()
        except Exception as exc:
            return SandboxResult(False, None, time.perf_counter() - started, scope,
                                 f"Sandbox preflight failed: {type(exc).__name__}")

        env = self._runtime_env()
        runtime = self.runtime_executable or shutil.which(self.policy.runtime)
        if not runtime:
            return SandboxResult(False, None, time.perf_counter() - started, scope, "Sandbox runtime disappeared")

        with tempfile.TemporaryDirectory(prefix="metaforge-sandbox-") as workspace_name:
            workspace = Path(workspace_name).resolve()
            if "," in str(workspace):
                return SandboxResult(False, None, time.perf_counter() - started, scope,
                                     "Workspace path contains an unsupported mount delimiter")
            source_path = workspace / "candidate.py"
            source_path.write_bytes(source_bytes)
            workspace.chmod(0o755)
            cid_directory = tempfile.TemporaryDirectory(prefix="metaforge-cid-")
            cid_file = Path(cid_directory.name) / "container.id"
            payload = json.dumps({"values": values}, separators=(",", ":")).encode("utf-8")
            mount = f"type=bind,source={workspace},target=/workspace,readonly"
            command = [
                runtime, "run", "--rm", "--interactive", "--attach=stdin", "--pull=never", f"--cidfile={cid_file}",
                "--network=none", "--read-only", f"--memory={self.policy.memory_mb}m",
                f"--memory-swap={self.policy.memory_mb}m", f"--cpus={self.policy.cpu_limit}",
                f"--pids-limit={self.policy.pids_limit}", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--user=65534:65534",
                "--ulimit=nofile=64:64", "--ulimit=core=0:0", "--shm-size=16m",
                f"--ulimit=fsize={self.policy.max_output_bytes}:{self.policy.max_output_bytes}",
                f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={self.policy.tmpfs_mb}m,mode=1777",
                f"--mount={mount}", "--workdir=/workspace", "--entrypoint=python",
                preparation.image, "-I", "-B", "/workspace/candidate.py",
            ]
            stdout = bytearray()
            stderr = bytearray()
            overflow = threading.Event()
            lock = threading.Lock()
            process: subprocess.Popen | None = None

            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    cwd=workspace,
                )
            except OSError as exc:
                cid_directory.cleanup()
                return SandboxResult(False, None, time.perf_counter() - started, scope,
                                     f"Sandbox runtime launch failed: {type(exc).__name__}")

            def drain(stream: Any, target: bytearray, limit: int) -> None:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        return
                    with lock:
                        if len(target) + len(chunk) > limit:
                            overflow.set()
                            return
                        target.extend(chunk)

            def send_input() -> None:
                try:
                    process.stdin.write(payload)
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass

            readers = [
                threading.Thread(target=drain, args=(process.stdout, stdout, self.policy.max_output_bytes), daemon=True),
                threading.Thread(target=drain, args=(process.stderr, stderr, 65_536), daemon=True),
                threading.Thread(target=send_input, daemon=True),
            ]
            for thread in readers:
                thread.start()

            timed_out = False
            deadline = time.monotonic() + self.policy.timeout_seconds
            while process.poll() is None:
                if overflow.wait(0.05):
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
            if overflow.is_set() or timed_out:
                self._stop_container(runtime, cid_file, env)
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            for thread in readers:
                thread.join(timeout=2)

            elapsed = time.perf_counter() - started
            cid_directory.cleanup()
            if timed_out:
                return SandboxResult(False, None, elapsed, scope, "Sandbox execution timed out")
            if overflow.is_set():
                return SandboxResult(False, None, elapsed, scope, "Sandbox output limit exceeded")
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()[:2000]
                error = f"Sandbox process returned non-zero status ({process.returncode})"
                if detail:
                    error += f": {detail}"
                return SandboxResult(False, None, elapsed, scope, error)
            try:
                parsed = json.loads(stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return SandboxResult(False, None, elapsed, scope, "Sandbox returned invalid JSON")
            if not isinstance(parsed, list) or any(type(value) is not int for value in parsed):
                return SandboxResult(False, None, elapsed, scope, "Sandbox output schema is invalid")
            if parsed != sorted(values):
                return SandboxResult(False, None, elapsed, scope, "Independent correctness check failed")
            return SandboxResult(True, parsed, elapsed, scope)
