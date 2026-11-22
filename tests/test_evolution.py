from metaforge.evolution.engine import EvolutionEngine

def test_evolution_finds_valid_candidate_and_runs_agents():
    result=EvolutionEngine(seed=11,repeats=1,population_size=6).run([5,3,1,4,2,2],generations=2)
    assert len(result["generations"])==2
    assert result["best_candidate"] is not None
    assert all(g["valid_count"]>0 for g in result["generations"])
    assert all(g["agent_analysis"]["valid_count"]>0 for g in result["generations"])
