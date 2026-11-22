import json
import pytest

from metaforge.agents.contracts import AgentResult, AgentTask
from metaforge.agents.openai_adapter import OpenAICandidateDesigner
from metaforge.agents.orchestrator import AgentOrchestrator


def test_openai_agent_sdk_adapter_uses_structured_output_without_tools():
    pytest.importorskip("agents")
    from agents.testing import ScriptedModel, assistant_message

    structured = {
        "candidates": [
            {"strategy": "builtin", "pivot": "middle", "insertion_cutoff": 16},
            {"strategy": "hybrid", "pivot": "median3", "insertion_cutoff": 16},
        ],
        "rationale": "Select two allow-listed candidate configurations.",
    }
    model = ScriptedModel([[assistant_message(json.dumps(structured))]])
    designer = OpenAICandidateDesigner(model=model, timeout_seconds=5)
    task = AgentTask(
        "scripted-proposal",
        "propose two candidates",
        {
            "population_size": 2,
            "allowed_candidates": structured["candidates"],
        },
    )
    result = designer.run(task)
    assert result.success is True
    assert len(result.output["candidates"]) == 2
    assert result.output["model"] == "ScriptedModel"
    model.assert_complete()


class UntrustedDesigner:
    name = "untrusted_test_designer"

    def run(self, task):
        return AgentResult(
            self.name,
            task.task_id,
            True,
            {
                "candidates": [
                    {"strategy": "not-registered", "pivot": "middle", "insertion_cutoff": 16},
                    {"strategy": "hybrid", "pivot": "middle", "insertion_cutoff": 999},
                    {"strategy": "hybrid", "pivot": "middle", "insertion_cutoff": 12},
                ],
                "rationale": "Malformed proposals must be rejected.",
            },
        )


def test_orchestrator_rejects_invalid_llm_proposals_and_fills_deterministically():
    orchestrator = AgentOrchestrator(seed=13, candidate_designer=UntrustedDesigner())
    population = orchestrator.design_population(6, "untrusted-proposal")
    assert len(population) == 6
    assert all(candidate.strategy in {"builtin", "insertion", "merge", "quicksort", "heapsort", "shellsort", "hybrid"} for candidate in population)
    assert orchestrator.last_design_metadata["llm_enabled"] is True
    assert orchestrator.last_design_metadata["fallback_used"] is True

def test_cli_refuses_llm_mode_without_environment_key(monkeypatch):
    from metaforge.cli import main

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("METAFORGE_AGENT_MODEL", "gpt-5-mini")
    monkeypatch.setattr("sys.argv", ["metaforge", "run", "--llm-proposals"])
    with pytest.raises(SystemExit):
        main()
