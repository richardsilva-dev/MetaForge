import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings, strategies as st

from metaforge.candidates.algorithms import run_candidate
from metaforge.candidates.generator import CandidateGenerator
from metaforge.candidates.models import Candidate


CANDIDATES = CandidateGenerator(seed=2026).initial_population(15)


@settings(max_examples=60, deadline=None)
@given(
    st.lists(
        st.integers(min_value=-(2**63), max_value=(2**63) - 1),
        min_size=0,
        max_size=256,
    )
)
def test_all_candidate_families_match_independent_sorting_oracle(values):
    expected = sorted(values)
    original = values.copy()
    for candidate in CANDIDATES:
        assert run_candidate(candidate, values) == expected
    assert values == original


@settings(max_examples=100, deadline=None)
@given(
    st.sampled_from(["builtin", "insertion", "merge", "quicksort", "heapsort", "shellsort", "hybrid"]),
    st.sampled_from(["middle", "first", "last", "median3"]),
    st.integers(min_value=0, max_value=100),
)
def test_candidate_descriptor_validation_is_total(strategy, pivot, cutoff):
    descriptor = {
        "strategy": strategy,
        "pivot": pivot,
        "insertion_cutoff": cutoff,
    }
    try:
        candidate = Candidate.from_dict(descriptor)
    except ValueError:
        return
    assert candidate.strategy in {
        "builtin", "insertion", "merge", "quicksort", "heapsort", "shellsort", "hybrid"
    }
    assert candidate.fingerprint
