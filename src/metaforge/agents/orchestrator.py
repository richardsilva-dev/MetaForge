from __future__ import annotations
from metaforge.agents.builtin import CandidateDesigner, CandidateSafetyReviewer, BenchmarkAnalyst
from metaforge.agents.contracts import AgentTask
from metaforge.agents.registry import AgentRegistry
from metaforge.candidates.models import Candidate

class AgentOrchestrator:
    """Coordinates specialist agents; deterministic policy remains authoritative."""
    def __init__(self, seed: int = 7) -> None:
        self.registry=AgentRegistry()
        self.registry.register("designer",CandidateDesigner(seed))
        self.registry.register("safety_reviewer",CandidateSafetyReviewer())
        self.registry.register("benchmark_analyst",BenchmarkAnalyst())

    def design_population(self,size:int,task_id:str)->list[Candidate]:
        result=self.registry.get("designer").run(
            AgentTask(task_id,"propose a bounded candidate population",{"population_size":size}))
        if not result.success: raise RuntimeError(result.error or "Candidate designer failed")
        return [Candidate.from_dict(item) for item in result.output["candidates"]]

    def approve(self,candidate:Candidate,task_id:str)->bool:
        result=self.registry.get("safety_reviewer").run(
            AgentTask(task_id,"review candidate descriptor",{"candidate":candidate.to_dict()}))
        return result.success and result.output.get("approved") is True

    def analyze(self,evaluations:list[dict],task_id:str)->dict:
        result=self.registry.get("benchmark_analyst").run(
            AgentTask(task_id,"summarize measured candidate results",{"evaluations":evaluations}))
        if not result.success: return {"analysis_error":result.error}
        return dict(result.output)
