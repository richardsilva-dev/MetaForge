from metaforge.evolution.engine import EvolutionEngine

def test_evolution_finds_valid_candidate_and_runs_agents():
    result=EvolutionEngine(seed=11,repeats=1,population_size=6).run([5,3,1,4,2,2],generations=2)
    assert len(result["generations"])==2
    assert result["best_candidate"] is not None
    assert all(g["valid_count"]>0 for g in result["generations"])
    assert all(g["agent_analysis"]["valid_count"]>0 for g in result["generations"])

def test_global_evaluation_budget_stops_new_generations():
    result=EvolutionEngine(seed=5,repeats=1,population_size=6,max_evaluations=1,max_workers=1).run([3,1,2],generations=5)
    assert len(result["generations"])==1
    assert result["budget"]["evaluations_used"]==1
    assert result["budget"]["stop_reason"]=="evaluation_budget_exhausted"

def test_invalid_parallelism_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        EvolutionEngine(max_workers=0)
