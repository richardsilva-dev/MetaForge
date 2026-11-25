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


_WORKER_SOURCE = r"""
import json, runpy, sys

_trusted_system_exit = SystemExit
_trusted_json_dumps = json.dumps
_trusted_stdout_write = sys.stdout.write
_trusted_type = type
_trusted_any = any
_trusted_callable = callable
_trusted_list_type = list
_trusted_int_type = int

def fail(code):
    raise _trusted_system_exit(code)

with open(sys.argv[2], encoding="utf-8") as handle:
    payload = json.load(handle)
values = payload["values"]
namespace = runpy.run_path(sys.argv[1], run_name="metaforge_candidate")
solve = namespace.get("solve")
if not _trusted_callable(solve):
    fail(30)
result = solve(values.copy())
if _trusted_type(result) is not _trusted_list_type or _trusted_any(
    _trusted_type(value) is not _trusted_int_type for value in result
):
    fail(31)
_trusted_stdout_write(_trusted_json_dumps(
    {"output": result}, separators=(",", ":")
) + "\n")

"""

_RUNNER_SOURCE = r"""
import json, os, resource, socket, subprocess, sys, time

_trusted_system_exit = SystemExit

def fail(code):
    raise _trusted_system_exit(code)

def verify_sandbox():
    if os.geteuid() == 0:
        fail(10)
    mounts = {}
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) > 5:
                mounts[fields[4]] = set(fields[5].split(","))
    if "ro" not in mounts.get("/", set()):
        fail(11)
    if "ro" not in mounts.get("/workspace", set()):
        fail(12)
    if "noexec" not in mounts.get("/tmp", set()):
        fail(13)
    status = {}
    with open("/proc/self/status", encoding="utf-8") as handle:
        for line in handle:
            if ":" in line:
                key, value = line.split(":", 1)
                status[key] = value.strip()
    if status.get("NoNewPrivs") != "1":
        fail(14)
    if int(status.get("CapEff", "1"), 16) != 0:
        fail(15)
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft > 64 or hard > 64:
        fail(16)
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    if soft > 1_000_000 or hard > 1_000_000:
        fail(17)
    soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
    if soft != 0 or hard != 0:
        fail(18)
    for path, limit in (
        ("/sys/fs/cgroup/memory.max", 256 * 1024 * 1024),
        ("/sys/fs/cgroup/pids.max", 64),
    ):
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
        if value == "max" or int(value) > limit:
            fail(19)
    with open("/sys/fs/cgroup/cpu.max", encoding="utf-8") as handle:
        quota, period = handle.read().split()
    if quota == "max" or int(quota) <= 0 or int(period) <= 0 or int(quota) / int(period) > 1.01:
        fail(20)
    with open("/sys/fs/cgroup/memory.swap.max", encoding="utf-8") as handle:
        if handle.read().strip() != "0":
            fail(23)
    shm = os.statvfs("/dev/shm")
    if shm.f_frsize * shm.f_blocks > 16 * 1024 * 1024:
        fail(24)
    tmp = os.statvfs("/tmp")
    if tmp.f_frsize * tmp.f_blocks > 16 * 1024 * 1024:
        fail(25)
    if "OPENAI_API_KEY" in os.environ:
        fail(21)
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=0.5)
    except OSError:
        pass
    else:
        fail(22)

if sys.argv[5] == "1":
    verify_sandbox()

# The trusted supervisor never imports or executes candidate code.
# Each sample runs in a fresh child interpreter; only this clock is trusted.
_trusted_clock = time.perf_counter_ns
_trusted_json_loads = json.loads
_trusted_stdout_write = sys.stdout.write
_trusted_sorted = sorted
_trusted_type = type
_trusted_any = any
_trusted_list_type = list
_trusted_int_type = int
_trusted_range = range
_trusted_monotonic = time.monotonic
_trusted_popen = subprocess.Popen
_trusted_killpg = os.killpg
_trusted_process_lookup_error = ProcessLookupError

with open(sys.argv[2], encoding="utf-8") as handle:
    payload = json.load(handle)
values = payload["values"]
repeat_count = int(sys.argv[4])
child_timeout = float(sys.argv[6])
max_output_bytes = int(sys.argv[7])
expected = _trusted_sorted(values)
deadline = _trusted_monotonic() + child_timeout
samples_ns = []
result = None
child_env = {
    "PATH": os.environ.get("PATH", ""),
    "PYTHONHASHSEED": "0",
    "PYTHONIOENCODING": "utf-8",
}

for _ in _trusted_range(repeat_count):
    remaining = deadline - _trusted_monotonic()
    if remaining <= 0:
        fail(33)
    started = _trusted_clock()
    try:
        child = _trusted_popen(
            [sys.executable, "-I", "-B", sys.argv[3], sys.argv[1], sys.argv[2]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            cwd="/workspace",
            start_new_session=True,
        )
        try:
            child_stdout, child_stderr = child.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                _trusted_killpg(child.pid, 9)
            except (OSError, _trusted_process_lookup_error):
                pass
            child_stdout, child_stderr = child.communicate()
            fail(34)
        elapsed = _trusted_clock() - started
        try:
            _trusted_killpg(child.pid, 9)
        except (OSError, _trusted_process_lookup_error):
            pass
    except OSError:
        fail(35)

    if child.returncode != 0:
        fail(36)
    if len(child_stdout) > max_output_bytes or len(child_stderr) > 65_536:
        fail(37)
    try:
        child_payload = _trusted_json_loads(child_stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        fail(38)
    if not isinstance(child_payload, dict) or set(child_payload) != {"output"}:
        fail(39)
    result = child_payload["output"]
    if _trusted_type(result) is not _trusted_list_type or _trusted_any(
        _trusted_type(value) is not _trusted_int_type for value in result
    ):
        fail(40)
    if result != expected:
        fail(41)
    samples_ns.append(max(0, elapsed))

_trusted_stdout_write(json.dumps(
    {"output": result, "samples_ns": samples_ns}, separators=(",", ":")
) + "\n")

"""


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
    algorithm_samples_seconds: tuple[float, ...] = ()


class SandboxRunner:
    """Fail-closed container execution for Python source supplied as untrusted data."""

    def __init__(
        self,
        policy: SandboxPolicy,
        runtime_executable: str | None = None,
    ) -> None:
        if not isinstance(policy, SandboxPolicy):
            raise TypeError("policy must be a SandboxPolicy")
        self.policy = policy
        self.runtime_executable = runtime_executable
        self._preparation: SandboxPreparation | None = None
        self._prepare_lock = threading.Lock()

    def _runtime_env(self) -> dict[str, str]:
        allowed = (
            "PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
            "CONTAINER_HOST", "XDG_RUNTIME_DIR", "SYSTEMROOT", "WINDIR",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA",
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
                subprocess.run(command, capture_output=True, timeout=3, env=env, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def smoke(self, timeout_seconds: float | None = None) -> SandboxResult:
        """Run only the fixed trusted isolation probe; never executes model output."""
        return self.run_python(
            "def solve(values):\n    return sorted(values)\n",
            [7, 2, 9, 1],
            timeout_seconds=timeout_seconds,
            repeats=2,
            smoke_probe=True,
        )

    def run_python(
        self,
        source_code: str,
        values: list[int],
        timeout_seconds: float | None = None,
        repeats: int = 1,
        smoke_probe: bool = False,
    ) -> SandboxResult:
        started = time.perf_counter()
        scope = "rootless_container_external_candidate_process_wall_time_including_startup"
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
        if timeout_seconds is not None and (
            type(timeout_seconds) not in (int, float) or timeout_seconds <= 0
        ):
            return SandboxResult(False, None, time.perf_counter() - started, scope,
                                 "Execution timeout budget is invalid")
        if type(repeats) is not int or not 1 <= repeats <= 10:
            return SandboxResult(False, None, time.perf_counter() - started, scope,
                                 "Repeats must be 1..10")
        if type(smoke_probe) is not bool:
            return SandboxResult(False, None, time.perf_counter() - started, scope,
                                 "smoke_probe must be boolean")
        local_timeout = self.policy.timeout_seconds if timeout_seconds is None else min(
            self.policy.timeout_seconds, timeout_seconds
        )
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
            input_path = workspace / "input.json"
            input_path.write_text(json.dumps({"values": values}, separators=(",", ":")), encoding="utf-8")
            runner_path = workspace / "runner.py"
            runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
            worker_path = workspace / "candidate_worker.py"
            worker_path.write_text(_WORKER_SOURCE, encoding="utf-8")
            workspace.chmod(0o755)
            cid_directory = tempfile.TemporaryDirectory(prefix="metaforge-cid-")
            cid_file = Path(cid_directory.name) / "container.id"
            mount = f"type=bind,source={workspace},target=/workspace,readonly"
            command = [
                runtime, "run", "--rm", "--pull=never", f"--cidfile={cid_file}",
                "--network=none", "--read-only", f"--memory={self.policy.memory_mb}m",
                f"--memory-swap={self.policy.memory_mb}m", f"--cpus={self.policy.cpu_limit}",
                f"--pids-limit={self.policy.pids_limit}", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--user=65534:65534",
                "--ulimit=nofile=64:64", "--ulimit=core=0:0", "--shm-size=16m",
                f"--ulimit=fsize={self.policy.max_output_bytes}:{self.policy.max_output_bytes}",
                f"--tmpfs=/tmp:rw,noexec,nosuid,nodev,size={self.policy.tmpfs_mb}m,mode=1777",
                f"--mount={mount}", "--workdir=/workspace", "--entrypoint=python",
                preparation.image, "-I", "-B", "/workspace/runner.py",
                "/workspace/candidate.py", "/workspace/input.json", "/workspace/candidate_worker.py",
                str(repeats), "1" if smoke_probe else "0", str(local_timeout),
                str(self.policy.max_output_bytes),
            ]
            stdout = bytearray()
            stderr = bytearray()
            overflow = threading.Event()
            lock = threading.Lock()
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
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

            readers = [
                threading.Thread(target=drain, args=(process.stdout, stdout, self.policy.max_output_bytes), daemon=True),
                threading.Thread(target=drain, args=(process.stderr, stderr, 65_536), daemon=True),
            ]
            for thread in readers:
                thread.start()

            timed_out = False
            deadline = time.monotonic() + local_timeout
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
            if not isinstance(parsed, dict) or set(parsed) != {"output", "samples_ns"}:
                return SandboxResult(False, None, elapsed, scope, "Sandbox output schema is invalid")
            output = parsed["output"]
            samples_ns = parsed["samples_ns"]
            if not isinstance(output, list) or any(type(value) is not int for value in output):
                return SandboxResult(False, None, elapsed, scope, "Sandbox output values are invalid")
            if not isinstance(samples_ns, list) or len(samples_ns) != repeats or any(
                type(value) is not int or value < 0 for value in samples_ns
            ):
                return SandboxResult(False, None, elapsed, scope, "Sandbox timing samples are invalid")
            if output != sorted(values):
                return SandboxResult(False, None, elapsed, scope, "Independent correctness check failed")
            samples = tuple(value / 1_000_000_000 for value in samples_ns)
            return SandboxResult(True, output, elapsed, scope, None, samples)
