from __future__ import annotations

from metaforge.agents.builtin import CandidateDesigner, CandidateSafetyReviewer, BenchmarkAnalyst
from metaforge.agents.contracts import AgentTask, SpecialistAgent
from metaforge.agents.registry import AgentRegistry
from metaforge.candidates.generator import CandidateGenerator
from metaforge.candidates.models import Candidate


class AgentOrchestrator:
    """Coordinates specialists; deterministic safety and correctness gates stay authoritative."""

    def __init__(self, seed: int = 7, candidate_designer: SpecialistAgent | None = None) -> None:
        self.seed = seed
        self.registry = AgentRegistry()
        self.fallback_designer = CandidateDesigner(seed)
        self.external_designer_enabled = candidate_designer is not None
        self.registry.register("designer", candidate_designer or self.fallback_designer)
        self.registry.register("safety_reviewer", CandidateSafetyReviewer())
        self.registry.register("benchmark_analyst", BenchmarkAnalyst())
        self.last_design_metadata: dict = {}

    def design_population(self, size: int, task_id: str) -> list[Candidate]:
        allowed = CandidateGenerator(seed=self.seed).initial_population(15)
        allowed_fingerprints = {candidate.fingerprint for candidate in allowed}
        task = AgentTask(
            task_id,
            "propose a bounded population of allow-listed candidate descriptors",
            {
                "population_size": size,
                "allowed_candidates": [candidate.to_dict() for candidate in allowed],
            },
        )
        result = self.registry.get("designer").run(task)
        raw_candidates = result.output.get("candidates", []) if result.success else []
        accepted: list[Candidate] = []
        seen: set[str] = set()
        if isinstance(raw_candidates, list):
            for raw in raw_candidates[:size]:
                try:
                    candidate = Candidate.from_dict(raw)
                except (TypeError, ValueError):
                    continue
                if candidate.fingerprint in allowed_fingerprints and candidate.fingerprint not in seen:
                    accepted.append(candidate)
                    seen.add(candidate.fingerprint)

        fallback_used = not result.success or len(accepted) < size
        if fallback_used:
            fallback = self.fallback_designer.run(task)
            if fallback.success:
                for raw in fallback.output.get("candidates", []):
                    try:
                        candidate = Candidate.from_dict(raw)
                    except (TypeError, ValueError):
                        continue
                    if candidate.fingerprint not in seen and len(accepted) < size:
                        accepted.append(candidate)
                        seen.add(candidate.fingerprint)

        if not accepted:
            accepted = CandidateGenerator(seed=self.seed).initial_population(size)
        while len(accepted) < size:
            accepted.append(accepted[len(accepted) % len(accepted)])

        self.last_design_metadata = {
            "agent_name": result.agent_name,
            "llm_enabled": self.external_designer_enabled,
            "request_success": result.success,
            "proposed_count": len(raw_candidates) if isinstance(raw_candidates, list) else 0,
            "accepted_count": len(seen),
            "fallback_used": fallback_used,
            "model": result.output.get("model"),
            "rationale": str(result.output.get("rationale", ""))[:500],
            "error": result.error,
        }
        return accepted[:size]

    def approve(self, candidate: Candidate, task_id: str) -> bool:
        result = self.registry.get("safety_reviewer").run(
            AgentTask(task_id, "review candidate descriptor", {"candidate": candidate.to_dict()})
        )
        return result.success and result.output.get("approved") is True

    def analyze(self, evaluations: list[dict], task_id: str) -> dict:
        result = self.registry.get("benchmark_analyst").run(
            AgentTask(task_id, "summarize measured candidate results", {"evaluations": evaluations})
        )
        if not result.success:
            return {"analysis_error": result.error}
        return dict(result.output)
