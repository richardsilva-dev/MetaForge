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

def test_safety_reviewer_is_a_mandatory_execution_gate():
    from metaforge.candidates.models import Candidate

    class NeverRun:
        def run_repeated(self, *args, **kwargs):
            raise AssertionError("candidate execution must not occur after rejection")

    engine = EvolutionEngine(runner=NeverRun(), repeats=1)
    engine.agents.approve = lambda candidate, task_id: False
    result = engine.evaluate(Candidate("builtin"), [2, 1])
    assert result.valid is False
    assert result.error == "Candidate rejected by safety reviewer"


def test_run_records_environment_dataset_hash_and_candidate_lineage():
    import hashlib
    import json

    dataset = [4, 1, 3, 2]
    result = EvolutionEngine(seed=17, repeats=1, population_size=6).run(dataset, generations=2)
    expected = hashlib.sha256(json.dumps(dataset, separators=(",", ":")).encode()).hexdigest()
    assert result["dataset_sha256"] == expected
    assert result["environment"]["python_version"]
    first = result["generations"][0]["evaluations"]
    assert all(item["lineage"]["origin"] == "candidate_designer" for item in first)
    if len(result["generations"]) > 1:
        later = result["generations"][1]["evaluations"]
        assert all(item["lineage"]["operator"] in {"elite_carryover", "mutation", "crossover", "restart_mutation", "mutation_duplicate_fallback"} for item in later)

def test_pareto_selection_preserves_time_memory_tradeoffs():
    from metaforge.evolution.engine import Evaluation, _pareto_front, _select_parents
    from metaforge.candidates.models import Candidate

    fastest = Evaluation(Candidate("builtin"), True, 1.0, 100)
    memory_efficient = Evaluation(Candidate("merge"), True, 2.0, 50)
    dominated = Evaluation(Candidate("insertion"), True, 3.0, 200)
    front = _pareto_front([fastest, memory_efficient, dominated])
    assert {item.candidate.strategy for item in front} == {"builtin", "merge"}
    selected = _select_parents([fastest, memory_efficient, dominated], limit=2)
    assert {item.candidate.strategy for item in selected} == {"builtin", "merge"}


def test_generation_records_pareto_front_and_strategy_diversity():
    result = EvolutionEngine(seed=19, repeats=1, population_size=9).run([9, 1, 5, 3, 2], generations=1)
    generation = result["generations"][0]
    assert generation["strategy_diversity"] >= 5
    assert generation["pareto_front_fingerprints"]
    assert result["pareto_front"]


def test_candidate_generator_supports_crossover_and_mutation():
    from metaforge.candidates.generator import CandidateGenerator
    from metaforge.candidates.models import Candidate

    generator = CandidateGenerator(seed=41)
    first = Candidate("quicksort", "first")
    second = Candidate("hybrid", "median3", 32)
    child = generator.crossover(first, second)
    assert child.strategy in {"quicksort", "hybrid"}
    assert child.fingerprint != ""
    mutated = generator.mutate(first)
    assert mutated != first

def test_evaluation_preserves_raw_samples_and_robust_spread():
    result = EvolutionEngine(seed=29, repeats=3, population_size=4, max_evaluations=4).run(
        [8, 2, 5, 1, 3], generations=1
    )
    valid = [item for item in result["generations"][0]["evaluations"] if item["valid"]]
    assert valid
    assert all(len(item["runtime_samples_seconds"]) == 3 for item in valid)
    assert all(len(item["peak_samples_bytes"]) == 3 for item in valid)
    assert all(item["runtime_mad_seconds"] is not None for item in valid)
