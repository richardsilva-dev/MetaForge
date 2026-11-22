from metaforge.agents.builtin import CandidateDesigner, CandidateSafetyReviewer, BenchmarkAnalyst
from metaforge.agents.contracts import AgentTask
from metaforge.agents.registry import AgentRegistry
from metaforge.agents.workflow import AgentWorkflow

def test_registry_and_workflow():
    registry=AgentRegistry()
    registry.register("designer",CandidateDesigner(seed=3))
    registry.register("reviewer",CandidateSafetyReviewer())
    proposed=AgentTask("proposal","propose candidates",{"population_size":4})
    results=AgentWorkflow(registry).execute([("designer",proposed)])
    assert results[0].success and len(results[0].output["candidates"])==4

def test_safety_reviewer_rejects_unregistered_strategy():
    result=CandidateSafetyReviewer().run(AgentTask("review","review",{"candidate":{"strategy":"shell","pivot":"middle"}}))
    assert result.success and result.output["approved"] is False

def test_benchmark_analyst_only_ranks_valid_results():
    result=BenchmarkAnalyst().run(AgentTask("analysis","rank",{"evaluations":[
        {"valid":True,"median_seconds":0.2,"fingerprint":"a"},
        {"valid":False,"median_seconds":0.01,"fingerprint":"b"}]}))
    assert result.output["valid_count"]==1
    assert result.output["ranked_fingerprints"]==["a"]
