from metaforge.candidates.models import Candidate
from metaforge.execution.runner import CandidateRunner

def test_runner_returns_correct_output():
    result=CandidateRunner().run(Candidate("merge"),[5,1,4,1,-2])
    assert result.success and result.output==[-2,1,1,4,5]
    assert result.elapsed_seconds is not None and result.peak_bytes is not None

def test_runner_rejects_non_integer_input():
    result=CandidateRunner().run(Candidate("builtin"),[1,True,3])
    assert not result.success and "integer" in result.error

def test_runner_input_limit():
    result=CandidateRunner(max_input_items=2).run(Candidate("builtin"),[1,2,3])
    assert not result.success and "limit" in result.error
