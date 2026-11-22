import hashlib
import json
import pytest

from metaforge.evolution.code_replay import CodeReplayEngine
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


def test_code_replay_remeasures_archived_source_without_model_calls():
    sandbox = FakeSandboxRunner()
    result = CodeReplayEngine(sandbox).run(_artifact())
    assert result["replay_of"] == "original-run"
    assert result["best_candidate"] is not None
    assert result["best_candidate"]["correctness_passed"] is True
    assert len(result["best_candidate"]["algorithm_samples_seconds"]) == 2
    assert len(sandbox.calls) == 2
    assert sandbox.calls[0][3] is True
    assert sandbox.calls[1][3] is False


def test_code_replay_rejects_tampered_dataset_before_sandbox():
    original = _artifact()
    original["replay"]["dataset"][0] = 999
    sandbox = FakeSandboxRunner()
    with pytest.raises(ValueError, match="dataset fingerprint"):
        CodeReplayEngine(sandbox).run(original)
    assert sandbox.calls == []


def test_code_replay_rejects_tampered_candidate_source_before_sandbox():
    original = _artifact()
    original["generations"][0]["evaluations"][0]["candidate"]["source_code"] += "# tampered"
    sandbox = FakeSandboxRunner()
    with pytest.raises(ValueError, match="source fingerprint"):
        CodeReplayEngine(sandbox).run(original)
    assert sandbox.calls == []
