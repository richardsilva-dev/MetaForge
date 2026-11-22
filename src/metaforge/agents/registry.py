from __future__ import annotations
from metaforge.agents.contracts import SpecialistAgent

class AgentRegistry:
    """Explicit allow-list of agent adapters; no dynamic import or auto-discovery."""
    def __init__(self) -> None:
        self._agents: dict[str, SpecialistAgent] = {}

    def register(self, role: str, agent: SpecialistAgent) -> None:
        if not role or role in self._agents:
            raise ValueError("Agent role must be non-empty and unique")
        self._agents[role] = agent

    def get(self, role: str) -> SpecialistAgent:
        try: return self._agents[role]
        except KeyError as exc: raise ValueError(f"Unknown agent role: {role}") from exc

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(sorted(self._agents))
