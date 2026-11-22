from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from metaforge.candidates.models import Candidate


@dataclass(frozen=True, slots=True)
class BackendPreparation:
    backend: str
    version: str
    compile_seconds: float


@dataclass(frozen=True, slots=True)
class BackendRun:
    success: bool
    output: list[int] | None
    elapsed_seconds: float | None
    peak_bytes: int | None
    measurement_scope: str
    error: str | None = None


class ExecutionBackend(Protocol):
    name: str

    def prepare(self) -> BackendPreparation: ...

    def run_repeated(
        self,
        candidate: Candidate,
        values: list[int],
        repeats: int = 1,
        timeout_seconds: float | None = None,
    ) -> list[BackendRun]: ...
