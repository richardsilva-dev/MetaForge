from __future__ import annotations
from metaforge.agents.contracts import AgentTask, AgentResult
from metaforge.candidates.generator import CandidateGenerator
from metaforge.candidates.models import Candidate, STRATEGIES, PIVOTS

class CandidateDesigner:
    name = "candidate_designer"
    def __init__(self, seed: int = 7) -> None:
        self.generator = CandidateGenerator(seed)

    def run(self, task: AgentTask) -> AgentResult:
        size = task.payload.get("population_size", 8)
        if type(size) is not int or not 2 <= size <= 32:
            return AgentResult(self.name, task.task_id, False, error="population_size must be 2..32")
        candidates = self.generator.initial_population(size)
        return AgentResult(self.name, task.task_id, True,
            {"candidates": [c.to_dict() for c in candidates],
             "rationale": "Seeded exploration of the registered candidate families."})

class CandidateSafetyReviewer:
    name = "candidate_safety_reviewer"
    def run(self, task: AgentTask) -> AgentResult:
        raw = task.payload.get("candidate")
        try:
            candidate = Candidate.from_dict(raw)
            if candidate.strategy not in STRATEGIES or candidate.pivot not in PIVOTS:
                raise ValueError("Candidate parameters are outside the allow-list")
        except (TypeError, ValueError) as exc:
            return AgentResult(self.name, task.task_id, True,
                {"approved": False, "reason": str(exc)})
        return AgentResult(self.name, task.task_id, True,
            {"approved": True, "candidate": candidate.to_dict(),
             "reason": "Descriptor passed the static allow-list review."})

class BenchmarkAnalyst:
    name = "benchmark_analyst"
    def run(self, task: AgentTask) -> AgentResult:
        evaluations = task.payload.get("evaluations", [])
        if not isinstance(evaluations, list):
            return AgentResult(self.name, task.task_id, False, error="evaluations must be a list")
        valid = [e for e in evaluations if isinstance(e, dict) and e.get("valid") is True]
        ranked = sorted((e for e in valid if isinstance(e.get("median_seconds"), (int, float))),
                        key=lambda e: e["median_seconds"])
        return AgentResult(self.name, task.task_id, True,
            {"valid_count": len(valid), "ranked_fingerprints": [e.get("fingerprint") for e in ranked],
             "interpretation": "Descriptive ranking only; it does not establish a general performance improvement."})
