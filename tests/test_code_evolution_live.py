import json
import os

import pytest

pytest.importorskip("agents")

from agents.testing import ScriptedModel, assistant_message

from metaforge.agents.code_designer import OpenAICodeDesigner
from metaforge.evolution.code_engine import CodeEvolutionEngine
from metaforge.sandbox.policy import SandboxPolicy
from metaforge.sandbox.runner import SandboxRunner


def test_scripted_code_agent_executes_only_inside_live_rootless_sandbox():
    image = os.environ.get("METAFORGE_SANDBOX_IMAGE")
    runtime = os.environ.get("METAFORGE_SANDBOX_RUNTIME", "podman")
    if not image:
        pytest.skip("Live sandbox image is not configured in this test environment")

    source = "def solve(values):\n    return sorted(values)\n"
    model = ScriptedModel([[assistant_message(json.dumps({
        "source_code": source,
        "rationale": "A correct baseline implementation for the sorting task.",
    }))]])
    designer = OpenAICodeDesigner(model=model, timeout_seconds=5)
    sandbox = SandboxRunner(SandboxPolicy(
        image=image,
        runtime=runtime,
        timeout_seconds=10,
    ))
    engine = CodeEvolutionEngine(
        designer,
        sandbox,
        population_size=1,
        generations=1,
        repeats=2,
        max_agent_calls=1,
        global_timeout_seconds=60,
    )
    result = engine.run([9, 1, 5, 3, 3, -2, 8])
    best = result["best_candidate"]
    assert best is not None
    assert best["correctness_passed"] is True
    assert len(best["algorithm_samples_seconds"]) == 2
    assert best["candidate"]["source_code"] == source
    assert result["sandbox"]["rootless"] is True
    assert result["sandbox"]["smoke_passed"] is True
    model.assert_complete()
