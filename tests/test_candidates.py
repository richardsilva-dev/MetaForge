import pytest
from metaforge.candidates.algorithms import run_candidate
from metaforge.candidates.models import Candidate

@pytest.mark.parametrize("candidate",[Candidate("builtin"),Candidate("insertion"),Candidate("merge"),
    Candidate("quicksort","first"),Candidate("quicksort","last"),Candidate("quicksort","median3")])
def test_sorting(candidate):
    for values in ([],[1],[3,2,1],[4,4,4],[-5,0,5,-5],list(range(50,-1,-1))):
        assert run_candidate(candidate,values)==sorted(values)

def test_reject_unknown_strategy():
    with pytest.raises(ValueError): Candidate("untrusted")

@pytest.mark.parametrize("policy",["first","last"])
def test_quicksort_handles_adversarial_order_without_recursion(policy):
    values=list(range(2500))
    assert run_candidate(Candidate("quicksort",policy),values)==values
