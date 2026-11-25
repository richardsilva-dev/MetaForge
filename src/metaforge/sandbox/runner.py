from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from metaforge.sandbox.policy import SandboxPolicy


_WORKER_SOURCE = r"""
import ctypes, json, os, runpy, sys

_trusted_system_exit = SystemExit
_trusted_json_dumps = json.dumps
_trusted_stdout_write = sys.stdout.write
_trusted_type = type
_trusted_any = any
_trusted_callable = callable
_trusted_list_type = list
_trusted_int_type = int
_trusted_dict_type = dict
_trusted_set_type = set
_trusted_bool_type = bool

def fail(code):
    raise _trusted_system_exit(code)

def install_worker_seccomp():
    machine = os.uname().machine.lower()
    policies = {
        "x86_64": (0xC000003E, (62, 200, 234, 129, 297, 424, 101, 310, 311, 438)),
        "amd64": (0xC000003E, (62, 200, 234, 129, 297, 424, 101, 310, 311, 438)),
        "aarch64": (0xC00000B7, (129, 130, 131, 138, 240, 424, 117, 270, 271, 438)),
        "arm64": (0xC00000B7, (129, 130, 131, 138, 240, 424, 117, 270, 271, 438)),
    }
    policy = policies.get(machine)
    if policy is None:
        fail(43)
    audit_arch, denied_syscalls = policy

    class SockFilter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class SockFprog(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ushort),
            ("filter", ctypes.POINTER(SockFilter)),
        ]

    instructions = [
        SockFilter(0x20, 0, 0, 4),  # load seccomp_data.arch
        SockFilter(0x15, 1, 0, audit_arch),
        SockFilter(0x06, 0, 0, 0x80000000),  # kill on unexpected architecture
        SockFilter(0x20, 0, 0, 0),  # load seccomp_data.nr
    ]
    for syscall_number in denied_syscalls:
        instructions.extend((
            SockFilter(0x15, 0, 1, syscall_number),
            SockFilter(0x06, 0, 0, 0x00050001),  # return EPERM
        ))
    instructions.append(SockFilter(0x06, 0, 0, 0x7FFF0000))  # allow other syscalls
    filters = (SockFilter * len(instructions))(*instructions)
    program = SockFprog(len(instructions), filters)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.c_ulong, ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(38, 1, None, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        fail(44)
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:  # PR_SET_SECCOMP
        fail(45)


install_worker_seccomp()

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
response = {"output": result}
summary = namespace.get("__metaforge_test_summary__")
if summary is not None:
    expected_keys = {"tests_run", "failures", "errors", "skipped", "successful"}
    if _trusted_type(summary) is not _trusted_dict_type or _trusted_set_type(summary) != expected_keys:
        fail(46)
    for key in ("tests_run", "failures", "errors", "skipped"):
        if _trusted_type(summary[key]) is not _trusted_int_type or summary[key] < 0:
            fail(47)
    if _trusted_type(summary["successful"]) is not _trusted_bool_type:
        fail(48)
    response["test_summary"] = {
        "tests_run": summary["tests_run"],
        "failures": summary["failures"],
        "errors": summary["errors"],
        "skipped": summary["skipped"],
        "successful": summary["successful"],
    }
_trusted_stdout_write(_trusted_json_dumps(response, separators=(",", ":")) + "\n")

"""


_PROJECT_UNITTEST_SOURCE = r"""
import contextlib, io, os, sys, unittest

project_root = "/workspace/project"
tests_root = os.path.join(project_root, "tests")
source_root = os.path.join(project_root, "src")
sys.path.insert(0, project_root)
if os.path.isdir(source_root):
    sys.path.insert(0, source_root)
os.chdir(project_root)

summary = {"tests_run": 0, "failures": 0, "errors": 0, "skipped": 0, "successful": False}
if os.path.isdir(tests_root):
    top_level = project_root if os.path.isfile(os.path.join(tests_root, "__init__.py")) else tests_root
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            loader = unittest.TestLoader()
            suite = loader.discover(start_dir=tests_root, top_level_dir=top_level)
            result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        summary = {
            "tests_run": int(result.testsRun),
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "successful": bool(result.wasSuccessful() and result.testsRun > 0),
        }
    except Exception:
        summary = {"tests_run": 0, "failures": 0, "errors": 1, "skipped": 0, "successful": False}

__metaforge_test_summary__ = summary

def solve(values):
    return []
"""

_RUNNER_SOURCE = r"""
import ctypes, json, os, resource, selectors, socket, subprocess, sys, time

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
_trusted_dict_type = dict
_trusted_set_type = set
_trusted_bool_type = bool
_trusted_range = range
_trusted_monotonic = time.monotonic
_trusted_popen = subprocess.Popen
_trusted_killpg = os.killpg
_trusted_process_lookup_error = ProcessLookupError

def collect_bounded_child_output(child, timeout, stdout_limit, stderr_limit):
    selector = selectors.DefaultSelector()
    buffers = {child.stdout: bytearray(), child.stderr: bytearray()}
    limits = {child.stdout: stdout_limit, child.stderr: stderr_limit}
    failure = None
    for stream in buffers:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)

    deadline = _trusted_monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - _trusted_monotonic()
            if remaining <= 0:
                failure = "timeout"
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                try:
                    chunk = os.read(key.fd, 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                target = buffers[key.fileobj]
                if len(target) + len(chunk) > limits[key.fileobj]:
                    failure = "overflow"
                    break
                target.extend(chunk)
            if failure is not None:
                break

        if failure is not None:
            try:
                _trusted_killpg(child.pid, 9)
            except (OSError, _trusted_process_lookup_error):
                pass
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=1)
            return None, None, failure

        child.wait()
        return bytes(buffers[child.stdout]), bytes(buffers[child.stderr]), None
    finally:
        selector.close()
        for stream in buffers:
            try:
                stream.close()
            except OSError:
                pass


_libc = ctypes.CDLL(None, use_errno=True)
if _libc.prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE=0 prevents child /proc fd inspection.
    fail(42)

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
            cwd=os.path.dirname(os.path.abspath(sys.argv[3])),
            start_new_session=True,
        )
        child_stdout, child_stderr, child_failure = collect_bounded_child_output(
            child, remaining, max_output_bytes, 65_536
        )
        if child_failure == "timeout":
            fail(34)
        if child_failure == "overflow":
            fail(37)
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
    if not isinstance(child_payload, dict) or set(child_payload) not in (
        {"output"}, {"output", "test_summary"}
    ):
        fail(39)
    result = child_payload["output"]
    test_summary = child_payload.get("test_summary")
    if test_summary is not None:
        expected_summary_keys = {"tests_run", "failures", "errors", "skipped", "successful"}
        if type(test_summary) is not _trusted_dict_type or _trusted_set_type(test_summary) != expected_summary_keys:
            fail(46)
        for key in ("tests_run", "failures", "errors", "skipped"):
            if type(test_summary[key]) is not _trusted_int_type or test_summary[key] < 0:
                fail(47)
        if type(test_summary["successful"]) is not _trusted_bool_type:
            fail(48)
    if _trusted_type(result) is not _trusted_list_type or _trusted_any(
        _trusted_type(value) is not _trusted_int_type for value in result
    ):
        fail(40)
    if result != expected:
        fail(41)
    samples_ns.append(max(0, elapsed))

response = {"output": result, "samples_ns": samples_ns}
if test_summary is not None:
    response["test_summary"] = test_summary
_trusted_stdout_write(json.dumps(response, separators=(",", ":")) + "\n")

"""


@dataclass(frozen=True, slots=True)
class SandboxPreparation:
    runtime: str
    runtime_version: str
    image: str
    rootless: bool


@dataclass(frozen=True, slots=True)
class ProjectTestExecutionResult:
    supported: bool
    executed: bool | None
    success: bool
    tests_run: int
    failures: int
    errors: int
    skipped: int
    wall_seconds: float
    measurement_scope: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxResult:
    success: bool
    output: list[int] | None
    wall_seconds: float
    measurement_scope: str
    error: str | None = None
    algorithm_samples_seconds: tuple[float, ...] = ()
    test_summary: dict[str, int | bool] | None = None
    execution_started: bool = False


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

    def _stop_container(
        self,
        runtime: str,
        cid_file: Path,
        env: dict[str, str],
        container_name: str,
    ) -> None:
        try:
            container_ref = cid_file.read_text(encoding="utf-8").strip()
        except OSError:
            container_ref = ""
        has_container_id = bool(re.fullmatch(r"[A-Fa-f0-9]{12,64}", container_ref))
        if not has_container_id:
            container_ref = container_name
        if not (
            has_container_id
            or re.fullmatch(r"metaforge-[0-9a-f]{32}", container_ref)
        ):
            return

        cleanup_deadline = time.monotonic() + 3.0
        while True:
            remaining = cleanup_deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                subprocess.run(
                    [runtime, "kill", container_ref],
                    capture_output=True,
                    timeout=min(0.5, remaining),
                    env=env,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

            remaining = cleanup_deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                removed = subprocess.run(
                    [runtime, "rm", "-f", container_ref],
                    capture_output=True,
                    timeout=min(0.5, remaining),
                    env=env,
                    check=False,
                )
                if removed.returncode == 0:
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass

            remaining = cleanup_deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def smoke(self, timeout_seconds: float | None = None) -> SandboxResult:
        """Run only the fixed trusted isolation probe; never executes model output."""
        return self.run_python(
            "def solve(values):\n    return sorted(values)\n",
            [7, 2, 9, 1],
            timeout_seconds=timeout_seconds,
            repeats=2,
            smoke_probe=True,
        )

    def run_project_unittest(
        self,
        snapshot: Any,
        timeout_seconds: float | None = None,
    ) -> ProjectTestExecutionResult:
        """Run only statically identified unittest suites inside the rootless sandbox."""
        started = time.perf_counter()
        scope = "rootless_container_external_candidate_process_wall_time_including_startup"
        from metaforge.projects.models import ProjectSnapshot
        from metaforge.projects.snapshot import _read_relative_regular_file, capture_project_snapshot
        from metaforge.projects.analysis import analyze_project
        from metaforge.projects.validation import build_validation_plan

        if not isinstance(snapshot, ProjectSnapshot):
            return ProjectTestExecutionResult(False, False, False, 0, 0, 0, 0, 0.0, "not_executed",
                                              "A ProjectSnapshot is required")
        try:
            current = capture_project_snapshot(snapshot.root)
            if current.fingerprint != snapshot.fingerprint:
                raise ValueError("Project snapshot changed before sandbox staging")
            analysis = analyze_project(snapshot)
            plan = build_validation_plan(snapshot, analysis)
        except Exception:
            return ProjectTestExecutionResult(False, False, False, 0, 0, 0, 0, 0.0, "not_executed",
                                              "Project preflight failed")

        python_targets = [item for item in plan["test_targets"] if item["ecosystem"] == "python"]
        if not python_targets or any(item["framework_candidates"] != ["unittest"] for item in python_targets):
            return ProjectTestExecutionResult(False, False, False, 0, 0, 0, 0, 0.0, "not_executed",
                                              "Only explicitly identified Python unittest suites are supported")
        if any(not item["path"].startswith("tests/") for item in python_targets):
            return ProjectTestExecutionResult(False, False, False, 0, 0, 0, 0, 0.0, "not_executed",
                                              "Python unittest files must be under tests/")
        file_paths = {entry.path for entry in snapshot.files}
        for item in python_targets:
            parent = PurePosixPath(item["path"]).parent
            while str(parent) != "tests":
                if f"{parent}/__init__.py" not in file_paths:
                    return ProjectTestExecutionResult(False, False, False, 0, 0, 0, 0, 0.0, "not_executed",
                                                      "Nested unittest directories must be Python packages")
                parent = parent.parent

        additional_files: dict[str, bytes] = {}
        total_bytes = 0
        try:
            for entry in snapshot.files:
                raw = _read_relative_regular_file(snapshot.root, entry.path, self.policy.max_project_bytes)
                if len(raw) != entry.size_bytes or hashlib.sha256(raw).hexdigest() != entry.sha256:
                    raise ValueError("Snapshot file changed during staging")
                total_bytes += len(raw)
                if total_bytes > self.policy.max_project_bytes:
                    raise ValueError("Project workspace size limit exceeded")
                additional_files[entry.path] = raw
        except Exception:
            return ProjectTestExecutionResult(False, False, False, 0, 0, 0, 0, 0.0, "not_executed",
                                              "Project snapshot could not be staged safely")

        sandbox_result = self.run_python(
            _PROJECT_UNITTEST_SOURCE,
            [],
            timeout_seconds=timeout_seconds,
            repeats=1,
            additional_files=additional_files,
        )
        if not sandbox_result.success or sandbox_result.test_summary is None:
            return ProjectTestExecutionResult(
                True, (True if sandbox_result.test_summary is not None else (None if sandbox_result.execution_started else False)), False, 0, 0, 0, 0, sandbox_result.wall_seconds, scope,
                "Sandboxed unittest execution failed or returned an invalid summary",
            )
        summary = sandbox_result.test_summary
        successful = bool(summary["successful"]) and summary["tests_run"] > 0
        return ProjectTestExecutionResult(
            True, True, successful, int(summary["tests_run"]), int(summary["failures"]),
            int(summary["errors"]), int(summary["skipped"]), sandbox_result.wall_seconds,
            scope, None if successful else "One or more tests failed or no tests were discovered",
        )

    def run_python(
        self,
        source_code: str,
        values: list[int],
        timeout_seconds: float | None = None,
        repeats: int = 1,
        smoke_probe: bool = False,
        additional_files: dict[str, bytes] | None = None,
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
        if additional_files is not None:
            if not isinstance(additional_files, dict) or len(additional_files) > self.policy.max_project_files:
                return SandboxResult(False, None, time.perf_counter() - started, scope,
                                     "Additional project file count limit exceeded")
            total_project_bytes = 0
            reserved = {"candidate.py", "input.json", "runner.py", "candidate_worker.py"}
            for relative_path, content in additional_files.items():
                if (not isinstance(relative_path, str) or not relative_path or len(relative_path) > 1024 or not isinstance(content, bytes)
                        or "\\" in relative_path or ":" in relative_path
                        or any(ord(char) < 32 for char in relative_path)
                        or any(part in {"", ".", ".."} for part in relative_path.split("/"))):
                    return SandboxResult(False, None, time.perf_counter() - started, scope,
                                         "Additional project path is invalid")
                relative = PurePosixPath(relative_path)
                if relative.is_absolute() or relative.parts[0] in reserved:
                    return SandboxResult(False, None, time.perf_counter() - started, scope,
                                         "Additional project path is reserved or absolute")
                total_project_bytes += len(content)
                if total_project_bytes > self.policy.max_project_bytes:
                    return SandboxResult(False, None, time.perf_counter() - started, scope,
                                         "Additional project byte limit exceeded")
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
            if additional_files:
                project_root = workspace / "project"
                try:
                    for relative_path, content in additional_files.items():
                        destination = project_root.joinpath(*PurePosixPath(relative_path).parts)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with destination.open("xb") as handle:
                            handle.write(content)
                except OSError as exc:
                    return SandboxResult(False, None, time.perf_counter() - started, scope,
                                         f"Additional project staging failed: {type(exc).__name__}")
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
            container_name = f"metaforge-{uuid.uuid4().hex}"
            mount = f"type=bind,source={workspace},target=/workspace,readonly"
            command = [
                runtime, "run", "--rm", "--pull=never", f"--name={container_name}", f"--cidfile={cid_file}",
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
                self._stop_container(runtime, cid_file, env, container_name)
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
                return SandboxResult(False, None, elapsed, scope, "Sandbox execution timed out", execution_started=True)
            if overflow.is_set():
                return SandboxResult(False, None, elapsed, scope, "Sandbox output limit exceeded", execution_started=True)
            if process.returncode != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()[:2000]
                error = f"Sandbox process returned non-zero status ({process.returncode})"
                if detail:
                    error += f": {detail}"
                return SandboxResult(False, None, elapsed, scope, error, execution_started=True)
            try:
                parsed = json.loads(stdout.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return SandboxResult(False, None, elapsed, scope, "Sandbox returned invalid JSON", execution_started=True)
            if not isinstance(parsed, dict) or set(parsed) not in (
                {"output", "samples_ns"}, {"output", "samples_ns", "test_summary"}
            ):
                return SandboxResult(False, None, elapsed, scope, "Sandbox output schema is invalid", execution_started=True)
            output = parsed["output"]
            samples_ns = parsed["samples_ns"]
            test_summary = parsed.get("test_summary")
            if test_summary is not None:
                expected_summary_keys = {"tests_run", "failures", "errors", "skipped", "successful"}
                if (not isinstance(test_summary, dict) or set(test_summary) != expected_summary_keys
                        or any(type(test_summary[key]) is not int or test_summary[key] < 0
                               for key in ("tests_run", "failures", "errors", "skipped"))
                        or type(test_summary["successful"]) is not bool):
                    return SandboxResult(False, None, elapsed, scope, "Sandbox test summary is invalid", execution_started=True)
            if not isinstance(output, list) or any(type(value) is not int for value in output):
                return SandboxResult(False, None, elapsed, scope, "Sandbox output values are invalid", execution_started=True)
            if not isinstance(samples_ns, list) or len(samples_ns) != repeats or any(
                type(value) is not int or value < 0 for value in samples_ns
            ):
                return SandboxResult(False, None, elapsed, scope, "Sandbox timing samples are invalid", execution_started=True)
            if output != sorted(values):
                return SandboxResult(False, None, elapsed, scope, "Independent correctness check failed", execution_started=True)
            samples = tuple(value / 1_000_000_000 for value in samples_ns)
            return SandboxResult(True, output, elapsed, scope, None, samples, test_summary, True)
