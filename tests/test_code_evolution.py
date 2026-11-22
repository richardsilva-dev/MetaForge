import pytest

from metaforge.agents.code_safety import StaticCodeReviewer
from metaforge.agents.contracts import AgentResult
from metaforge.evolution.code_engine import CodeEvolutionEngine
from metaforge.sandbox.policy import SandboxPolicy
from metaforge.sandbox.runner import SandboxPreparation, SandboxResult, SandboxRunner


IMAGE = "registry.example/python@sha256:" + "b" * 64
SOURCE_A = "def solve(values):\n    return sorted(values)\n"
SOURCE_B = "def solve(values):\n    result = list(values)\n    result.sort()\n    return result\n"
SOURCE_C = "def solve(values):\n    return list(sorted(values))\n"
SOURCE_BAD = "import os\ndef solve(values):\n    return sorted(values)\n"


class FakeSandboxRunner(SandboxRunner):
    def __init__(self, fail_prepare=False):
        super().__init__(SandboxPolicy(image=IMAGE))
        self.fail_prepare = fail_prepare
        self.run_calls = []

    def prepare(self):
        if self.fail_prepare:
            raise RuntimeError("rootless runtime unavailable")
        return SandboxPreparation("podman", "fake 1.0", IMAGE, True)

    def run_python(self, source_code, values, timeout_seconds=None, repeats=1, smoke_probe=False):
        self.run_calls.append((source_code, list(values), timeout_seconds, repeats, smoke_probe))
        samples = tuple(0.002 for _ in range(repeats))
        return SandboxResult(True, sorted(values), 0.03, "fake_internal", None, samples)


class FakeDesigner:
    name = "fake_code_designer"

    def __init__(self, sources):
        self.sources = list(sources)
        self.tasks = []

    def run(self, task, timeout_seconds=None):
        self.tasks.append(task)
        source = self.sources[len(self.tasks) - 1]
        return AgentResult(
            self.name, task.task_id, True,
            {"source_code": source, "rationale": "scripted test proposal", "model": "FakeDesigner"},
        )


def test_static_reviewer_accepts_pure_solve_function():
    review = StaticCodeReviewer().review(
        "import heapq\n\ndef solve(values):\n    return sorted(values)\n"
    )
    assert review.approved is True
    assert review.imports == ("heapq",)
    assert len(review.source_sha256) == 64


@pytest.mark.parametrize("source", [
    SOURCE_BAD,
    "def solve(values):\n    return eval('sorted(values)')\n",
    "print('unexpected top-level output')\ndef solve(values):\n    return sorted(values)\n",
    "def solve(values=[]):\n    return sorted(values)\n",
    "def solve(values):\n    open('/etc/passwd')\n    return sorted(values)\n",
])
def test_static_reviewer_rejects_unsafe_or_unbounded_source(source):
    assert StaticCodeReviewer().review(source).approved is False


def test_code_evolution_runs_reviewed_candidates_only_and_tracks_lineage():
    designer = FakeDesigner([SOURCE_A, SOURCE_B, SOURCE_C, SOURCE_A + "# second mutation\n"])
    sandbox = FakeSandboxRunner()
    engine = CodeEvolutionEngine(
        designer, sandbox, population_size=2, generations=2, repeats=2,
        max_agent_calls=4, global_timeout_seconds=60,
    )
    result = engine.run([5, 1, 4, 2, 3])
    assert result["best_candidate"] is not None
    assert result["budget"]["agent_calls_used"] == 4
    assert result["sandbox"]["smoke_passed"] is True
    assert len(sandbox.run_calls) == 5
    assert sandbox.run_calls[0][4] is True
    assert all(call[4] is False for call in sandbox.run_calls[1:])
    assert len(result["generations"]) == 2
    assert all(item["correctness_passed"] for generation in result["generations"] for item in generation["evaluations"])
    assert all(task.payload["parent_source"] is None for task in designer.tasks[:2])
    assert all(task.payload["parent_fingerprint"] for task in designer.tasks[2:])


def test_invalid_source_is_rejected_without_sandbox_execution():
    designer = FakeDesigner([SOURCE_BAD])
    sandbox = FakeSandboxRunner()
    engine = CodeEvolutionEngine(
        designer, sandbox, population_size=1, generations=1, repeats=1,
        max_agent_calls=1, global_timeout_seconds=30,
    )
    result = engine.run([3, 1, 2])
    assert result["best_candidate"] is None
    assert len(sandbox.run_calls) == 1 and sandbox.run_calls[0][4] is True
    assert result["generations"][0]["proposals"][0]["success"] is False


def test_sandbox_preflight_happens_before_any_model_call():
    designer = FakeDesigner([SOURCE_A])
    sandbox = FakeSandboxRunner(fail_prepare=True)
    engine = CodeEvolutionEngine(
        designer, sandbox, population_size=1, generations=1, repeats=1,
        max_agent_calls=1, global_timeout_seconds=30,
    )
    with pytest.raises(RuntimeError):
        engine.run([2, 1])
    assert designer.tasks == []

def test_failed_sandbox_smoke_blocks_all_model_calls():
    class FailedSmokeRunner(FakeSandboxRunner):
        def smoke(self, timeout_seconds=None):
            return SandboxResult(False, None, 0.1, "fake_internal", "probe failed", ())

    designer = FakeDesigner([SOURCE_A])
    sandbox = FailedSmokeRunner()
    engine = CodeEvolutionEngine(
        designer, sandbox, population_size=1, generations=1, repeats=1,
        max_agent_calls=1, global_timeout_seconds=30,
    )
    with pytest.raises(RuntimeError, match="sandbox smoke failed"):
        engine.run([2, 1])
    assert designer.tasks == []
