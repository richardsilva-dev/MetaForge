from __future__ import annotations
from metaforge.agents.contracts import AgentTask, AgentResult
from metaforge.agents.registry import AgentRegistry

class AgentWorkflow:
    """Runs named specialist steps in an explicit order and records every result."""
    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry

    def execute(self, steps: list[tuple[str, AgentTask]]) -> list[AgentResult]:
        results=[]
        for role, task in steps:
            result=self.registry.get(role).run(task)
            results.append(result)
            if not result.success:
                break
        return results
