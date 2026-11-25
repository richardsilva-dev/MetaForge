import hashlib
import json
import pytest

from metaforge.evolution.code_replay import CodeReplayEngine
from metaforge.evolution.workloads import (
    INTEGER_SORT_SUITE_NAME,
    INTEGER_SORT_SUITE_VERSION,
    INTEGER_SORT_WORKLOAD_NAMES,
    build_integer_sort_workloads,
)
from metaforge.sandbox.policy import SandboxPolicy
from metaforge.sandbox.runner import SandboxPreparation, SandboxResult, SandboxRunner


IMAGE = "registry.example/python@sha256:" + "c" * 64
SOURCE = "def solve(values):\n    return sorted(values)\n"


class FakeSandboxRunner(SandboxRunner):
    def __init__(self):
        super().__init__(SandboxPolicy(image=IMAGE))
        self.calls = []

    def prepare(self):
        return SandboxPreparation("podman", "fake 1.0", IMAGE, True)

    def run_python(self, source_code, values, timeout_seconds=None, repeats=1, smoke_probe=False):
        self.calls.append((source_code, list(values), repeats, smoke_probe))
        return SandboxResult(
            True, sorted(values), 0.02, "fake_internal", None,
            tuple(0.001 for _ in range(repeats)),
        )


def _artifact():
    dataset = [8, 3, 1, 5]
    fingerprint = hashlib.sha256(SOURCE.encode("utf-8")).hexdigest()
    dataset_hash = hashlib.sha256(json.dumps(dataset, separators=(",", ":")).encode()).hexdigest()
    return {
        "engine": "metaforge_code_evolution",
        "run_id": "original-run",
        "created_at": "2026-11-22T00:00:00+00:00",
        "problem": "integer_sort",
        "dataset_size": len(dataset),
        "dataset_sha256": dataset_hash,
        "replay": {"dataset": dataset},
        "budget": {"repeats": 2},
        "generations": [{
            "generation": 0,
            "evaluations": [{
                "candidate": {
                    "source_code": SOURCE,
                    "fingerprint": fingerprint,
                    "generation": 0,
                    "proposal_index": 0,
                    "parent_fingerprint": None,
                    "rationale": "baseline",
                }
            }],
        }],
    }


def _artifact_with_workload_suite():
    artifact = _artifact()
    dataset = artifact["replay"]["dataset"]
    workloads = build_integer_sort_workloads(dataset)
    artifact["benchmark"] = {
        "suite": INTEGER_SORT_SUITE_NAME,
        "suite_version": INTEGER_SORT_SUITE_VERSION,
        "aggregation": "median_of_workload_medians_equal_weight",
        "workload_names": list(INTEGER_SORT_WORKLOAD_NAMES),
        "workload_sha256": {
            name: hashlib.sha256(
                json.dumps(values, separators=(",", ":")).encode()
            ).hexdigest()
            for name, values in workloads.items()
        },
    }
    return artifact


def test_legacy_code_replay_remains_backward_compatible():
    sandbox = FakeSandboxRunner()
    result = CodeReplayEngine(sandbox).run(_artifact())
    assert result["replay_of"] == "original-run"
    assert result["best_candidate"] is not None
    assert result["best_candidate"]["correctness_passed"] is True
    assert len(result["best_candidate"]["algorithm_samples_seconds"]) == 2
    assert len(sandbox.calls) == 2
    assert sandbox.calls[0][3] is True
    assert sandbox.calls[1][3] is False
    assert result["benchmark"]["suite"] == "legacy-single-input"


def test_code_replay_remeasures_every_archived_workload_without_model_calls():
    sandbox = FakeSandboxRunner()
    result = CodeReplayEngine(sandbox).run(_artifact_with_workload_suite())

    assert result["replay_of"] == "original-run"
    assert result["best_candidate"] is not None
    assert result["best_candidate"]["correctness_passed"] is True
    assert len(result["best_candidate"]["algorithm_samples_seconds"]) == 2 * len(INTEGER_SORT_WORKLOAD_NAMES)
    assert tuple(result["best_candidate"]["workload_results"]) == INTEGER_SORT_WORKLOAD_NAMES
    assert len(sandbox.calls) == 1 + len(INTEGER_SORT_WORKLOAD_NAMES)
    assert sandbox.calls[0][3] is True
    assert all(call[3] is False for call in sandbox.calls[1:])


def test_code_replay_rejects_tampered_dataset_before_sandbox():
    original = _artifact()
    original["replay"]["dataset"][0] = 999
    sandbox = FakeSandboxRunner()
    with pytest.raises(ValueError, match="dataset fingerprint"):
        CodeReplayEngine(sandbox).run(original)
    assert sandbox.calls == []


def test_code_replay_rejects_tampered_workload_schedule_before_sandbox():
    original = _artifact_with_workload_suite()
    original["benchmark"]["workload_sha256"]["ascending"] = "0" * 64
    sandbox = FakeSandboxRunner()

    with pytest.raises(ValueError, match="workload fingerprint"):
        CodeReplayEngine(sandbox).run(original)

    assert sandbox.calls == []


def test_code_replay_rejects_tampered_candidate_source_before_sandbox():
    original = _artifact()
    original["generations"][0]["evaluations"][0]["candidate"]["source_code"] += "# tampered"
    sandbox = FakeSandboxRunner()
    with pytest.raises(ValueError, match="source fingerprint"):
        CodeReplayEngine(sandbox).run(original)
    assert sandbox.calls == []
