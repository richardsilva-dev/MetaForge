from __future__ import annotations

import platform

from metaforge.candidates.models import Candidate
from metaforge.execution.runner import CandidateRunner
from metaforge.backends.protocol import BackendPreparation, BackendRun


class PythonBackend:
    """Adapter exposing the existing trusted Python worker through the backend contract."""

    name = "python"

    def __init__(self, runner: CandidateRunner | None = None) -> None:
        self.runner = runner or CandidateRunner()

    def prepare(self) -> BackendPreparation:
        return BackendPreparation(self.name, platform.python_version(), 0.0)

    def run_repeated(
        self,
        candidate: Candidate,
        values: list[int],
        repeats: int = 1,
        timeout_seconds: float | None = None,
    ) -> list[BackendRun]:
        return [
            BackendRun(
                item.success,
                item.output,
                item.elapsed_seconds,
                item.peak_bytes,
                "python_tracemalloc_peak",
                item.error,
            )
            for item in self.runner.run_repeated(candidate, values, repeats, timeout_seconds)
        ]
