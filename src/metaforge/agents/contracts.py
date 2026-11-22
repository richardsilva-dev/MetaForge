from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

@dataclass(frozen=True, slots=True)
class AgentTask:
    task_id: str
    objective: str
    payload: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class AgentResult:
    agent_name: str
    task_id: str
    success: bool
    output: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

class SpecialistAgent(Protocol):
    """Adapter contract implemented by native or third-party agent runtimes."""
    name: str
    def run(self, task: AgentTask) -> AgentResult: ...
