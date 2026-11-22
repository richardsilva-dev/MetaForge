import random
import shutil

import pytest

from metaforge.backends.python import PythonBackend
from metaforge.candidates.models import Candidate


def test_python_backend_adapts_existing_worker():
    backend = PythonBackend()
    preparation = backend.prepare()
    assert preparation.backend == "python"
    result = backend.run_repeated(Candidate("heapsort"), [8, 1, 5, 2], repeats=2)
    assert len(result) == 2
    assert all(item.success and item.output == [1, 2, 5, 8] for item in result)
    assert all(item.measurement_scope == "python_tracemalloc_peak" for item in result)


@pytest.mark.parametrize("backend_name", ["rust", "c"])
def test_compiled_backend_matches_sorting_oracle(backend_name):
    if backend_name == "rust":
        if not shutil.which("cargo") or not shutil.which("rustc"):
            pytest.skip("Rust toolchain is not installed")
        from metaforge.backends.rust import RustBackend
        backend = RustBackend()
    else:
        if not (shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")):
            pytest.skip("C compiler is not installed")
        from metaforge.backends.c import CBackend
        backend = CBackend()

    preparation = backend.prepare()
    assert preparation.backend == backend_name
    assert preparation.version
    assert preparation.compile_seconds >= 0

    rng = random.Random(31415)
    datasets = [
        [],
        [1],
        [5, 5, 5, 5],
        [-(2**63), 0, 2**63 - 1, -1, 1],
        list(range(100, -1, -1)),
        [rng.randint(-500, 500) for _ in range(300)],
    ]
    for strategy in ("builtin", "heapsort", "shellsort"):
        for values in datasets:
            runs = backend.run_repeated(Candidate(strategy), values, repeats=2)
            assert len(runs) == 2
            assert all(item.success for item in runs)
            assert all(item.output == sorted(values) for item in runs)
            assert all(item.elapsed_seconds is not None and item.elapsed_seconds >= 0 for item in runs)
            assert all(item.peak_bytes is None for item in runs)
            assert all(item.measurement_scope == f"{backend_name}_algorithm_internal_elapsed" for item in runs)


def test_compiled_backends_reject_unsupported_strategies_without_compiling():
    from metaforge.backends.rust import RustBackend
    from metaforge.backends.c import CBackend

    for backend in (RustBackend(), CBackend()):
        result = backend.run_repeated(Candidate("merge"), [3, 1, 2])[0]
        assert result.success is False
        assert "not supported" in result.error


def test_compiled_backends_reject_integer_overflow_before_execution():
    from metaforge.backends.rust import RustBackend
    from metaforge.backends.c import CBackend

    for backend in (RustBackend(), CBackend()):
        result = backend.run_repeated(Candidate("heapsort"), [2**80])[0]
        assert result.success is False
        assert "64-bit" in result.error
