import pytest
from metaforge.candidates.algorithms import run_candidate
from metaforge.candidates.models import Candidate

@pytest.mark.parametrize("candidate",[Candidate("builtin"),Candidate("insertion"),Candidate("merge"),
    Candidate("quicksort","first"),Candidate("quicksort","last"),Candidate("quicksort","median3"),Candidate("heapsort"),Candidate("shellsort"),
    Candidate("hybrid","middle",8),Candidate("hybrid","median3",32)])
def test_sorting(candidate):
    for values in ([],[1],[3,2,1],[4,4,4],[-5,0,5,-5],list(range(50,-1,-1))):
        assert run_candidate(candidate,values)==sorted(values)

def test_reject_unknown_strategy():
    with pytest.raises(ValueError): Candidate("untrusted")

@pytest.mark.parametrize("policy",["first","last"])
def test_quicksort_handles_adversarial_order_without_recursion(policy):
    values=list(range(2500))
    assert run_candidate(Candidate("quicksort",policy),values)==values

def test_candidate_rejects_invalid_hybrid_cutoff():
    with pytest.raises(ValueError):
        Candidate("hybrid", "middle", 2)

def test_candidate_rejects_cutoff_for_non_hybrid_strategy():
    with pytest.raises(ValueError):
        Candidate("heapsort", "middle", 32)

def test_all_generated_candidates_match_independent_sorting_oracle():
    import random
    from metaforge.candidates.generator import CandidateGenerator

    rng = random.Random(2026)
    candidates = CandidateGenerator(seed=3).initial_population(15)
    for candidate in candidates:
        for _ in range(40):
            values = [rng.randint(-20, 20) for _ in range(rng.randint(0, 100))]
            original = values.copy()
            assert run_candidate(candidate, values) == sorted(original)
            assert values == original
