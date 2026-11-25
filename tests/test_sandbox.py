import json
import os
from pathlib import Path
import sys

import pytest

from metaforge.sandbox.policy import SandboxPolicy
from metaforge.sandbox.runner import SandboxRunner


IMAGE = "registry.example/metaforge-python@sha256:" + "a" * 64


def _fake_runtime(tmp_path: Path, mode: str = "success") -> Path:
    script = tmp_path / f"fake_runtime_{mode}"
    source = f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
import time

if "OPENAI_API_KEY" in os.environ:
    raise SystemExit(9)
args = sys.argv[1:]
mode = {mode!r}
if args == ["--version"]:
    print("fake-rootless-runtime 1.0")
elif args and args[0] == "info":
    print("false" if mode == "not_rootless" else "true")
elif args[:2] == ["image", "inspect"]:
    raise SystemExit(0)
elif args and args[0] in ("kill", "rm"):
    with open(__file__ + ".cleanup", "a", encoding="utf-8") as handle:
        handle.write(" ".join(args) + "\\n")
    if mode == "cleanup_delayed":
        counter_path = Path(__file__ + ".cleanup-count")
        count = int(counter_path.read_text(encoding="utf-8")) + 1 if counter_path.exists() else 1
        counter_path.write_text(str(count), encoding="utf-8")
        if count < 5:
            raise SystemExit(1)
    raise SystemExit(0)
elif args and args[0] == "run":
    cid_arg = next(item for item in args if item.startswith("--cidfile="))
    cid_path = Path(cid_arg.split("=", 1)[1])
    if cid_path.exists():
        raise SystemExit(4)
    if mode != "timeout_before_cid":
        cid_path.write_text("a" * 64, encoding="utf-8")
    Path(__file__ + ".args").write_text("\\n".join(args), encoding="utf-8")
    if mode in ("timeout", "timeout_before_cid"):
        time.sleep(5)
    elif mode == "overflow":
        sys.stdout.write("x" * 4096)
        sys.stdout.flush()
    else:
        mount_arg = next(item for item in args if item.startswith("--mount="))
        mount_spec = mount_arg.split("=", 1)[1]
        source_path = mount_spec.split("source=", 1)[1].split(",target=", 1)[0]
        payload = json.loads((Path(source_path) / "input.json").read_text(encoding="utf-8"))
        repeats = int(args[-4])
        if mode in ("project_success", "project_failure"):
            test_file = Path(source_path) / "project" / "tests" / "test_basic.py"
            if not test_file.is_file():
                raise SystemExit(6)
            Path(__file__ + ".project").write_text(test_file.read_text(encoding="utf-8"), encoding="utf-8")
            result = {{"output": sorted(payload["values"]), "samples_ns": [1000] * repeats,
                      "test_summary": {{"tests_run": 2, "failures": 0 if mode == "project_success" else 1, "errors": 0,
                                       "skipped": 1, "successful": mode == "project_success"}}}}
        else:
            result = {{"output": sorted(payload["values"]), "samples_ns": [1000] * repeats}}
        print(json.dumps(result))
else:
    raise SystemExit(2)
"""
    script.write_text(source, encoding="utf-8")
    script.chmod(0o755)
    return script


def test_sandbox_policy_requires_pinned_image_and_bounded_resources():
    with pytest.raises(ValueError):
        SandboxPolicy(image="python:3.12-slim")
    with pytest.raises(ValueError):
        SandboxPolicy(image=IMAGE, memory_mb=32)
    with pytest.raises(ValueError):
        SandboxPolicy(image=IMAGE, runtime="shell")


def test_rootless_sandbox_runs_with_required_isolation_flags(tmp_path):
    runtime = _fake_runtime(tmp_path)
    runner = SandboxRunner(SandboxPolicy(image=IMAGE, runtime="podman"), str(runtime))
    result = runner.run_python("def solve(values): return sorted(values)", [5, 1, 3, 2], repeats=2)
    assert result.success is True
    assert result.output == [1, 2, 3, 5]
    assert len(result.algorithm_samples_seconds) == 2
    assert result.measurement_scope == "rootless_container_external_candidate_process_wall_time_including_startup"
    args = Path(str(runtime) + ".args").read_text(encoding="utf-8")
    for required in (
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--user=65534:65534",
        "--pids-limit=64", "--memory=256m", "--memory-swap=256m", "--shm-size=16m", "--ulimit=core=0:0",
        "--pull=never", "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
    ):
        assert required in args
    assert "OPENAI_API_KEY" not in args
    assert "--mount=type=bind,source=" in args
    assert ",target=/workspace,readonly" in args
    assert "/workspace/runner.py" in args
    assert "/workspace/candidate_worker.py" in args
    assert "--name=metaforge-" in args


def test_sandbox_fails_closed_when_runtime_is_not_rootless(tmp_path):
    runtime = _fake_runtime(tmp_path, "not_rootless")
    runner = SandboxRunner(SandboxPolicy(image=IMAGE), str(runtime))
    result = runner.run_python("print('x')", [1])
    assert result.success is False
    assert "preflight failed" in result.error


def test_sandbox_enforces_timeout_and_output_caps(tmp_path):
    timeout_runtime = _fake_runtime(tmp_path, "timeout")
    timeout_runner = SandboxRunner(
        SandboxPolicy(image=IMAGE, timeout_seconds=0.2), str(timeout_runtime)
    )
    timed_out = timeout_runner.run_python("print('x')", [1])
    assert timed_out.success is False
    assert timed_out.error == "Sandbox execution timed out"

    overflow_runtime = _fake_runtime(tmp_path, "overflow")
    overflow_runner = SandboxRunner(
        SandboxPolicy(image=IMAGE, max_output_bytes=1024), str(overflow_runtime)
    )
    overflow = overflow_runner.run_python("print('x')", [1])
    assert overflow.success is False
    assert overflow.error == "Sandbox output limit exceeded"


def test_invalid_source_is_rejected_before_runtime_preflight():
    runner = SandboxRunner(SandboxPolicy(image=IMAGE), "/runtime/does/not/exist")
    result = runner.run_python("", [1])
    assert result.success is False
    assert result.error == "Source size limit exceeded"

def test_host_api_key_is_not_passed_to_runtime_client(tmp_path, monkeypatch):
    runtime = _fake_runtime(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-must-not-propagate")
    runner = SandboxRunner(SandboxPolicy(image=IMAGE), str(runtime))
    result = runner.run_python("def solve(values): return sorted(values)", [2, 1])
    assert result.success is True


def test_sandbox_policy_rejects_boolean_resource_limits():
    with pytest.raises(ValueError):
        SandboxPolicy(image=IMAGE, cpu_limit=True)
    with pytest.raises(ValueError):
        SandboxPolicy(image=IMAGE, timeout_seconds=True)

def test_sandbox_policy_rejects_non_string_image_and_runtime():
    with pytest.raises(ValueError):
        SandboxPolicy(image=None)
    with pytest.raises(ValueError):
        SandboxPolicy(image=IMAGE, runtime=[])

def test_trusted_sandbox_smoke_uses_probe_mode(tmp_path):
    runtime = _fake_runtime(tmp_path)
    runner = SandboxRunner(SandboxPolicy(image=IMAGE), str(runtime))
    result = runner.smoke()
    assert result.success is True
    args = Path(str(runtime) + ".args").read_text(encoding="utf-8")
    assert args.splitlines()[-3] == "1"
    assert len(result.algorithm_samples_seconds) == 2


@pytest.mark.skipif(sys.platform != "linux", reason="runner wrapper depends on Linux resource limits")
def test_candidate_cannot_spoof_timing_or_wrapper_output_by_monkeypatching(tmp_path):
    import subprocess

    from metaforge.sandbox.runner import _RUNNER_SOURCE, _WORKER_SOURCE

    runner_path = tmp_path / "runner.py"
    worker_path = tmp_path / "candidate_worker.py"
    candidate_path = tmp_path / "candidate.py"
    input_path = tmp_path / "input.json"
    runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
    worker_path.write_text(_WORKER_SOURCE, encoding="utf-8")
    candidate_path.write_text(
        "import builtins, json, sys, time\n"
        "time.perf_counter_ns = lambda: 0\n"
        "json.dumps = lambda *args, **kwargs: '{}'\n"
        "sys.stdout.write = lambda value: 0\n"
        "builtins.sorted = lambda values: []\n"
        "builtins.range = lambda *args: []\n"
        "def solve(values):\n"
        "    values.sort()\n"
        "    return values\n",
        encoding="utf-8",
    )
    input_path.write_text(json.dumps({"values": [9, 1, 5, 2]}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-I", str(runner_path), str(candidate_path), str(input_path), str(worker_path), "3", "0", "5", "1000000"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["output"] == [1, 2, 5, 9]
    assert len(payload["samples_ns"]) == 3
    assert all(type(sample) is int and sample > 0 for sample in payload["samples_ns"])


@pytest.mark.skipif(sys.platform != "linux", reason="supervisor dumpability is a Linux control")
def test_candidate_worker_cannot_write_to_supervisor_stdout_via_procfs(tmp_path):
    import subprocess

    from metaforge.sandbox.runner import _RUNNER_SOURCE, _WORKER_SOURCE

    runner_path = tmp_path / "runner.py"
    worker_path = tmp_path / "candidate_worker.py"
    candidate_path = tmp_path / "candidate.py"
    input_path = tmp_path / "input.json"
    runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
    worker_path.write_text(_WORKER_SOURCE, encoding="utf-8")
    candidate_path.write_text(
        "import os\n"
        "def solve(values):\n"
        "    try:\n"
        "        with open(f'/proc/{os.getppid()}/fd/1', 'w') as stream:\n"
        "            stream.write('FORGED-SUPERVISOR-OUTPUT')\n"
        "    except OSError:\n"
        "        pass\n"
        "    values.sort()\n"
        "    return values\n",
        encoding="utf-8",
    )
    input_path.write_text(json.dumps({"values": [9, 1, 5, 2]}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-I", str(runner_path), str(candidate_path), str(input_path), str(worker_path), "2", "0", "5", "1000000"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "FORGED-SUPERVISOR-OUTPUT" not in completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["output"] == [1, 2, 5, 9]
    assert len(payload["samples_ns"]) == 2



@pytest.mark.skipif(sys.platform != "linux", reason="worker seccomp is a Linux control")
def test_candidate_cannot_signal_or_ptrace_the_trusted_supervisor(tmp_path):
    import subprocess

    from metaforge.sandbox.runner import _RUNNER_SOURCE, _WORKER_SOURCE

    runner_path = tmp_path / "runner.py"
    worker_path = tmp_path / "candidate_worker.py"
    candidate_path = tmp_path / "candidate.py"
    input_path = tmp_path / "input.json"
    runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
    worker_path.write_text(_WORKER_SOURCE, encoding="utf-8")
    candidate_path.write_text(
        "import os, signal\n"
        "def solve(values):\n"
        "    try:\n"
        "        os.kill(os.getppid(), signal.SIGKILL)\n"
        "    except PermissionError:\n"
        "        pass\n"
        "    else:\n"
        "        return []\n"
        "    values.sort()\n"
        "    return values\n",
        encoding="utf-8",
    )
    input_path.write_text(json.dumps({"values": [9, 1, 5, 2]}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-I", str(runner_path), str(candidate_path), str(input_path), str(worker_path), "2", "0", "5", "1000000"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["output"] == [1, 2, 5, 9]
    assert len(payload["samples_ns"]) == 2


def test_sandbox_timeout_cleans_container_even_before_cidfile_is_written(tmp_path):
    runtime = _fake_runtime(tmp_path, "timeout_before_cid")
    runner = SandboxRunner(
        SandboxPolicy(image=IMAGE, timeout_seconds=0.2), str(runtime)
    )

    result = runner.run_python("def solve(values): return sorted(values)", [2, 1])

    assert result.success is False
    assert result.error == "Sandbox execution timed out"
    cleanup = Path(str(runtime) + ".cleanup").read_text(encoding="utf-8")
    assert "kill metaforge-" in cleanup
    assert "rm -f metaforge-" in cleanup



@pytest.mark.skipif(sys.platform != "linux", reason="worker supervisor is a Linux process")
def test_candidate_stdout_is_bounded_before_supervisor_buffers_it(tmp_path):
    import subprocess

    from metaforge.sandbox.runner import _RUNNER_SOURCE, _WORKER_SOURCE

    runner_path = tmp_path / "runner.py"
    worker_path = tmp_path / "candidate_worker.py"
    candidate_path = tmp_path / "candidate.py"
    input_path = tmp_path / "input.json"
    runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
    worker_path.write_text(_WORKER_SOURCE, encoding="utf-8")
    candidate_path.write_text(
        "def solve(values):\n"
        "    print('x' * 1000000)\n"
        "    values.sort()\n"
        "    return values\n",
        encoding="utf-8",
    )
    input_path.write_text(json.dumps({"values": [9, 1, 5, 2]}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-I", str(runner_path), str(candidate_path), str(input_path), str(worker_path), "1", "0", "5", "1024"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode != 0
    assert len(completed.stdout.encode("utf-8")) < 4096



def test_container_cleanup_retries_until_the_name_becomes_removable(tmp_path):
    runtime = _fake_runtime(tmp_path, "cleanup_delayed")
    runner = SandboxRunner(SandboxPolicy(image=IMAGE), str(runtime))
    missing_cid = tmp_path / "container.id"

    runner._stop_container(
        str(runtime),
        missing_cid,
        runner._runtime_env(),
        "metaforge-" + "a" * 32,
    )

    attempts = int(Path(str(runtime) + ".cleanup-count").read_text(encoding="utf-8"))
    assert attempts >= 5


@pytest.mark.skipif(sys.platform != "linux", reason="worker supervisor is a Linux process")
def test_candidate_stderr_is_bounded_before_supervisor_buffers_it(tmp_path):
    import subprocess

    from metaforge.sandbox.runner import _RUNNER_SOURCE, _WORKER_SOURCE

    runner_path = tmp_path / "runner.py"
    worker_path = tmp_path / "candidate_worker.py"
    candidate_path = tmp_path / "candidate.py"
    input_path = tmp_path / "input.json"
    runner_path.write_text(_RUNNER_SOURCE, encoding="utf-8")
    worker_path.write_text(_WORKER_SOURCE, encoding="utf-8")
    candidate_path.write_text(
        "import sys\n"
        "def solve(values):\n"
        "    sys.stderr.write('x' * 1000000)\n"
        "    values.sort()\n"
        "    return values\n",
        encoding="utf-8",
    )
    input_path.write_text(json.dumps({"values": [9, 1, 5, 2]}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-I", str(runner_path), str(candidate_path), str(input_path), str(worker_path), "1", "0", "5", "1000000"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode != 0
    assert len(completed.stdout.encode("utf-8")) < 4096


def test_project_unittest_stages_snapshot_read_only_and_returns_bounded_summary(tmp_path):
    from metaforge.projects import capture_project_snapshot

    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_basic.py").write_text(
        "import unittest\nclass BasicTests(unittest.TestCase):\n"
        "    def test_one(self): self.assertEqual(1, 1)\n",
        encoding="utf-8",
    )
    snapshot = capture_project_snapshot(root)
    runtime = _fake_runtime(tmp_path, "project_success")
    runner = SandboxRunner(SandboxPolicy(image=IMAGE), str(runtime))

    result = runner.run_project_unittest(snapshot)

    assert result.supported is True
    assert result.executed is True
    assert result.success is True
    assert result.tests_run == 2
    assert result.failures == 0
    assert result.skipped == 1
    assert Path(str(runtime) + ".project").read_text(encoding="utf-8").startswith("import unittest")
    args = Path(str(runtime) + ".args").read_text(encoding="utf-8")
    assert "--network=none" in args
    assert "--read-only" in args
    assert ",target=/workspace,readonly" in args


def test_project_unittest_refuses_pytest_or_ambiguous_frameworks_before_runtime(tmp_path):
    from metaforge.projects import capture_project_snapshot

    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_basic.py").write_text(
        "import pytest\ndef test_one(): assert True\n", encoding="utf-8"
    )
    runtime = _fake_runtime(tmp_path, "not_rootless")
    runner = SandboxRunner(SandboxPolicy(image=IMAGE), str(runtime))

    result = runner.run_project_unittest(capture_project_snapshot(root))

    assert result.supported is False
    assert "unittest" in result.error
    assert not Path(str(runtime) + ".args").exists()


def test_additional_project_files_reject_traversal_before_runtime(tmp_path):
    runtime = _fake_runtime(tmp_path)
    runner = SandboxRunner(SandboxPolicy(image=IMAGE), str(runtime))

    result = runner.run_python(
        "def solve(values): return values",
        [],
        additional_files={"../outside.py": b"pass"},
    )

    assert result.success is False
    assert result.error == "Additional project path is invalid"
    assert not Path(str(runtime) + ".args").exists()


def test_sandbox_policy_bounds_project_workspace_size():
    with pytest.raises(ValueError):
        SandboxPolicy(image=IMAGE, max_project_files=0)
    with pytest.raises(ValueError):
        SandboxPolicy(image=IMAGE, max_project_bytes=512)
    with pytest.raises(ValueError):
        SandboxPolicy(image=IMAGE, max_project_files=True)


@pytest.mark.skipif(sys.platform != "linux", reason="worker seccomp is Linux-specific")
def test_trusted_worker_returns_unittest_summary_without_test_output(tmp_path):
    import subprocess

    from metaforge.sandbox.runner import _PROJECT_UNITTEST_SOURCE, _WORKER_SOURCE

    workspace = tmp_path
    project_tests = workspace / "project" / "tests"
    project_tests.mkdir(parents=True)
    (project_tests / "test_basic.py").write_text(
        "import unittest\n"
        "class BasicTests(unittest.TestCase):\n"
        "    def test_pass(self): self.assertEqual(2, 2)\n"
        "    def test_fail(self): self.assertEqual(1, 2)\n",
        encoding="utf-8",
    )
    worker_path = workspace / "candidate_worker.py"
    candidate_path = workspace / "candidate.py"
    input_path = workspace / "input.json"
    worker_path.write_text(_WORKER_SOURCE, encoding="utf-8")
    candidate_path.write_text(_PROJECT_UNITTEST_SOURCE, encoding="utf-8")
    input_path.write_text(json.dumps({"values": []}), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-I", str(worker_path), str(candidate_path), str(input_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["output"] == []
    assert payload["test_summary"] == {
        "tests_run": 2, "failures": 1, "errors": 0, "skipped": 0, "successful": False,
    }
    assert "AssertionError" not in completed.stdout

def test_project_unittest_reports_test_failures_as_execution_not_success(tmp_path):
    from metaforge.projects import capture_project_snapshot

    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_basic.py").write_text(
        "import unittest\nclass BasicTests(unittest.TestCase):\n"
        "    def test_one(self): self.assertEqual(1, 1)\n",
        encoding="utf-8",
    )
    runtime = _fake_runtime(tmp_path, "project_failure")
    result = SandboxRunner(SandboxPolicy(image=IMAGE), str(runtime)).run_project_unittest(
        capture_project_snapshot(root)
    )

    assert result.supported is True
    assert result.executed is True
    assert result.success is False
    assert result.tests_run == 2
    assert result.failures == 1
