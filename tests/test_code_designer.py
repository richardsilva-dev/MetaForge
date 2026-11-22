import json
import pytest

from metaforge.agents.code_designer import OpenAICodeDesigner
from metaforge.agents.contracts import AgentTask


def test_openai_code_designer_returns_structured_inert_source():
    pytest.importorskip("agents")
    from agents.testing import ScriptedModel, assistant_message

    source = "def solve(values):\n    return sorted(values)\n"
    model = ScriptedModel([[assistant_message(json.dumps({
        "source_code": source,
        "rationale": "Use the Python sorting primitive as a correct baseline.",
    }))]])
    designer = OpenAICodeDesigner(model=model, timeout_seconds=5)
    task = AgentTask(
        "scripted-code-proposal",
        "Generate a correct Python sorting function",
        {"generation": 0, "parent_source": None, "allowed_imports": []},
    )
    result = designer.run(task)
    assert result.success is True
    assert result.output["source_code"] == source
    assert result.output["model"] == "ScriptedModel"
    model.assert_complete()
