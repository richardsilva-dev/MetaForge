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
        repeats = int(args[-2])
        print(json.dumps({{"output": sorted(payload["values"]), "samples_ns": [1000] * repeats}}))
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
    assert args.rstrip().endswith("1")
    assert len(result.algorithm_samples_seconds) == 2


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
