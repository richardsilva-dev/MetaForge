from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import random
import statistics
import time
from typing import Any
from uuid import uuid4

from metaforge.agents.orchestrator import AgentOrchestrator
from metaforge.candidates.generator import CandidateGenerator
from metaforge.candidates.models import Candidate
from metaforge.evolution.metadata import runtime_metadata
from metaforge.execution.runner import CandidateRunner


@dataclass(frozen=True, slots=True)
class Evaluation:
    candidate: Candidate
    valid: bool
    median_seconds: float | None
    median_peak_bytes: int | None
    error: str | None = None
    lineage: dict[str, Any] | None = None

    @property
    def score(self) -> float:
        return self.median_seconds if self.valid and self.median_seconds is not None else float("inf")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "fingerprint": self.candidate.fingerprint,
            "valid": self.valid,
            "median_seconds": self.median_seconds,
            "median_peak_bytes": self.median_peak_bytes,
            "error": self.error,
            "lineage": self.lineage or {},
        }


class EvolutionEngine:
    def __init__(
        self,
        runner: CandidateRunner | None = None,
        seed: int = 7,
        repeats: int = 3,
        population_size: int = 8,
        max_evaluations: int = 64,
        max_workers: int = 2,
        global_timeout_seconds: float = 120.0,
    ) -> None:
        if not 1 <= repeats <= 20 or not 2 <= population_size <= 32:
            raise ValueError("repeats must be 1..20 and population_size 2..32")
        if not 1 <= max_evaluations <= 1000:
            raise ValueError("max_evaluations must be 1..1000")
        if not 1 <= max_workers <= 8:
            raise ValueError("max_workers must be 1..8")
        if global_timeout_seconds <= 0:
            raise ValueError("global_timeout_seconds must be positive")
        self.runner = runner or CandidateRunner()
        self.generator = CandidateGenerator(seed)
        self.agents = AgentOrchestrator(seed)
        self.random = random.Random(seed)
        self.seed = seed
        self.repeats = repeats
        self.population_size = population_size
        self.max_evaluations = max_evaluations
        self.max_workers = max_workers
        self.global_timeout_seconds = global_timeout_seconds

    def evaluate(
        self,
        candidate: Candidate,
        dataset: list[int],
        timeout_seconds: float | None = None,
        lineage: dict[str, Any] | None = None,
    ) -> Evaluation:
        if not self.agents.approve(candidate, f"safety-review-{candidate.fingerprint[:12]}"):
            return Evaluation(candidate, False, None, None, "Candidate rejected by safety reviewer", lineage)
        runs = self.runner.run_repeated(candidate, dataset, self.repeats, timeout_seconds)
        expected = sorted(dataset)
        times: list[float] = []
        peaks: list[int] = []
        for run in runs:
            if not run.success:
                return Evaluation(candidate, False, None, None, run.error, lineage)
            if run.output != expected:
                return Evaluation(candidate, False, None, None, "Correctness check failed", lineage)
            if run.elapsed_seconds is None or run.peak_bytes is None:
                return Evaluation(candidate, False, None, None, "Worker omitted required metrics", lineage)
            times.append(run.elapsed_seconds)
            peaks.append(run.peak_bytes)
        return Evaluation(
            candidate, True, statistics.median(times), int(statistics.median(peaks)), None, lineage
        )

    def run(self, dataset: list[int], generations: int = 3) -> dict[str, Any]:
        if not isinstance(dataset, list) or len(dataset) > 100_000:
            raise ValueError("dataset must be a list containing at most 100000 integers")
        if any(type(value) is not int for value in dataset):
            raise ValueError("dataset must contain only integers (booleans are not accepted)")
        if generations < 1 or generations > 20:
            raise ValueError("Generations must be 1..20")

        dataset_bytes = json.dumps(dataset, separators=(",", ":")).encode("utf-8")
        dataset_sha256 = hashlib.sha256(dataset_bytes).hexdigest()
        run_id = uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        deadline = time.monotonic() + self.global_timeout_seconds
        population = self.agents.design_population(self.population_size, "generation-0-design")
        lineage_by_fingerprint = {
            candidate.fingerprint: {
                "generation_created": 0,
                "operator": "initial_population",
                "parent_fingerprint": None,
                "origin": "candidate_designer",
            }
            for candidate in population
        }
        history: list[dict[str, Any]] = []
        evaluations_used = 0
        stop_reason = "generation_limit"

        for generation in range(generations):
            unique = {candidate.fingerprint: candidate for candidate in population}
            candidates = list(unique.values())
            evaluations: list[Evaluation] = []
            index = 0

            while index < len(candidates) and evaluations_used < self.max_evaluations:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop_reason = "global_timeout"
                    break
                batch = candidates[index:index + self.max_workers]
                batch = batch[: self.max_evaluations - evaluations_used]
                futures = {}
                pool = ThreadPoolExecutor(max_workers=len(batch))
                try:
                    for candidate in batch:
                        futures[pool.submit(
                            self.evaluate,
                            candidate,
                            dataset,
                            remaining,
                            lineage_by_fingerprint.get(candidate.fingerprint),
                        )] = candidate
                    try:
                        for future in as_completed(futures, timeout=remaining):
                            candidate = futures[future]
                            try:
                                evaluations.append(future.result())
                            except Exception as exc:
                                evaluations.append(Evaluation(
                                    candidate, False, None, None,
                                    f"Evaluation worker failed: {type(exc).__name__}: {exc}",
                                    lineage_by_fingerprint.get(candidate.fingerprint),
                                ))
                    except FuturesTimeout:
                        stop_reason = "global_timeout"
                        for future, candidate in futures.items():
                            if not future.done():
                                future.cancel()
                                evaluations.append(Evaluation(
                                    candidate, False, None, None, "Global time budget exceeded",
                                    lineage_by_fingerprint.get(candidate.fingerprint),
                                ))
                finally:
                    pool.shutdown(wait=True, cancel_futures=True)
                evaluations_used += len(batch)
                index += len(batch)
                if stop_reason == "global_timeout":
                    break

            valid = sorted((item for item in evaluations if item.valid), key=lambda item: item.score)
            records = [item.to_dict() for item in evaluations]
            analysis = self.agents.analyze(records, f"generation-{generation}-analysis")
            history.append({
                "generation": generation,
                "evaluations": records,
                "valid_count": len(valid),
                "population_diversity": len(unique),
                "agent_analysis": analysis,
            })

            if stop_reason == "global_timeout":
                break
            if evaluations_used >= self.max_evaluations:
                stop_reason = "evaluation_budget_exhausted"
                break
            if generation < generations - 1:
                parents = valid[: max(1, min(3, len(valid)))]
                next_population: list[Candidate] = []
                next_lineage: dict[str, dict[str, Any]] = {}
                if not parents:
                    next_population = self.generator.initial_population(self.population_size)
                    for candidate in next_population:
                        next_lineage.setdefault(candidate.fingerprint, {
                            "generation_created": generation + 1,
                            "operator": "restart_after_no_valid_candidates",
                            "parent_fingerprint": None,
                            "origin": "candidate_generator",
                        })
                else:
                    for item in parents:
                        candidate = item.candidate
                        next_population.append(candidate)
                        next_lineage.setdefault(candidate.fingerprint, {
                            "generation_created": generation + 1,
                            "operator": "elite_carryover",
                            "parent_fingerprint": candidate.fingerprint,
                            "origin": "selection",
                        })
                    while len(next_population) < self.population_size:
                        parent = self.random.choice(parents).candidate
                        child = self.generator.mutate(parent)
                        next_population.append(child)
                        next_lineage.setdefault(child.fingerprint, {
                            "generation_created": generation + 1,
                            "operator": "mutation",
                            "parent_fingerprint": parent.fingerprint,
                            "origin": "candidate_generator",
                        })
                population = next_population
                lineage_by_fingerprint = next_lineage

        all_valid = [
            record
            for generation_record in history
            for record in generation_record["evaluations"]
            if record["valid"]
        ]
        best = min(all_valid, key=lambda record: record["median_seconds"]) if all_valid else None
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": "1.1",
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "problem": "integer_sort",
            "dataset_size": len(dataset),
            "dataset_sha256": dataset_sha256,
            "seed": self.seed,
            "environment": runtime_metadata(),
            "configuration": {
                "generations_requested": generations,
                "population_size": self.population_size,
                "repeats": self.repeats,
                "max_evaluations": self.max_evaluations,
                "max_workers": self.max_workers,
                "global_timeout_seconds": self.global_timeout_seconds,
            },
            "generations": history,
            "best_candidate": best,
            "budget": {
                "max_evaluations": self.max_evaluations,
                "evaluations_used": evaluations_used,
                "max_workers": self.max_workers,
                "global_timeout_seconds": self.global_timeout_seconds,
                "stop_reason": stop_reason,
            },
            "note": "Exploratory local microbenchmark; repeat under controlled conditions.",
        }
