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
    runtime_samples_seconds: list[float] | None = None
    peak_samples_bytes: list[int] | None = None
    runtime_mad_seconds: float | None = None

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
            "runtime_samples_seconds": self.runtime_samples_seconds or [],
            "peak_samples_bytes": self.peak_samples_bytes or [],
            "runtime_mad_seconds": self.runtime_mad_seconds,
        }


def _dominates(left: Evaluation, right: Evaluation) -> bool:
    if not left.valid or not right.valid:
        return False
    left_values = (left.median_seconds, left.median_peak_bytes)
    right_values = (right.median_seconds, right.median_peak_bytes)
    if any(value is None for value in (*left_values, *right_values)):
        return False
    no_worse = all(a <= b for a, b in zip(left_values, right_values))
    strictly_better = any(a < b for a, b in zip(left_values, right_values))
    return no_worse and strictly_better


def _pareto_ranked(evaluations: list[Evaluation]) -> list[Evaluation]:
    """Non-dominated sorting with crowding distance for deterministic tie-breaking."""
    remaining = list(evaluations)
    ranked: list[Evaluation] = []
    while remaining:
        front = [
            item for item in remaining
            if not any(other is not item and _dominates(other, item) for other in remaining)
        ]
        if not front:
            front = [min(remaining, key=lambda item: (item.score, item.candidate.fingerprint))]
        distances = {item.candidate.fingerprint: 0.0 for item in front}
        for attribute in ("median_seconds", "median_peak_bytes"):
            ordered = sorted(front, key=lambda item: getattr(item, attribute))
            if len(ordered) <= 2:
                for item in ordered:
                    distances[item.candidate.fingerprint] = float("inf")
                continue
            distances[ordered[0].candidate.fingerprint] = float("inf")
            distances[ordered[-1].candidate.fingerprint] = float("inf")
            low = getattr(ordered[0], attribute)
            high = getattr(ordered[-1], attribute)
            span = high - low
            if span:
                for index in range(1, len(ordered) - 1):
                    item = ordered[index]
                    if distances[item.candidate.fingerprint] != float("inf"):
                        before = getattr(ordered[index - 1], attribute)
                        after = getattr(ordered[index + 1], attribute)
                        distances[item.candidate.fingerprint] += (after - before) / span
        front.sort(key=lambda item: (
            -distances[item.candidate.fingerprint],
            item.score,
            item.median_peak_bytes,
            item.candidate.fingerprint,
        ))
        ranked.extend(front)
        front_ids = {item.candidate.fingerprint for item in front}
        remaining = [item for item in remaining if item.candidate.fingerprint not in front_ids]
    return ranked


def _select_parents(evaluations: list[Evaluation], limit: int = 4) -> list[Evaluation]:
    ranked = _pareto_ranked(evaluations)
    selected: list[Evaluation] = []
    represented_strategies: set[str] = set()
    # Preserve algorithm-family diversity before filling by Pareto rank/crowding.
    for item in ranked:
        if item.candidate.strategy not in represented_strategies:
            selected.append(item)
            represented_strategies.add(item.candidate.strategy)
            if len(selected) >= limit:
                return selected
    for item in ranked:
        if item not in selected:
            selected.append(item)
            if len(selected) >= limit:
                break
    return selected


def _pareto_front(evaluations: list[Evaluation]) -> list[Evaluation]:
    unique: dict[str, Evaluation] = {}
    for item in evaluations:
        if item.valid:
            current = unique.get(item.candidate.fingerprint)
            if current is None or item.score < current.score:
                unique[item.candidate.fingerprint] = item
    return [
        item for item in unique.values()
        if not any(other is not item and _dominates(other, item) for other in unique.values())
    ]


class EvolutionEngine:
    def __init__(
        self,
        runner: CandidateRunner | None = None,
        seed: int = 7,
        repeats: int = 3,
        population_size: int = 15,
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
        median_time = statistics.median(times)
        median_absolute_deviation = statistics.median(abs(value - median_time) for value in times)
        return Evaluation(
            candidate, True, median_time, int(statistics.median(peaks)), None, lineage,
            times, peaks, median_absolute_deviation,
        )

    def run(
        self,
        dataset: list[int],
        generations: int = 3,
        candidate_schedule: list[list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(dataset, list) or len(dataset) > 100_000:
            raise ValueError("dataset must be a list containing at most 100000 integers")
        if any(type(value) is not int for value in dataset):
            raise ValueError("dataset must contain only integers (booleans are not accepted)")
        if generations < 1 or generations > 20:
            raise ValueError("Generations must be 1..20")
        if candidate_schedule is not None and (
            not isinstance(candidate_schedule, list) or len(candidate_schedule) != generations
        ):
            raise ValueError("candidate_schedule must contain exactly one population per generation")

        def load_scheduled_population(index: int) -> tuple[list[Candidate], dict[str, dict[str, Any]]]:
            entries = candidate_schedule[index] if candidate_schedule is not None else None
            if not isinstance(entries, list) or not 2 <= len(entries) <= 32:
                raise ValueError("Each scheduled population must contain 2..32 candidate records")
            scheduled_candidates: list[Candidate] = []
            scheduled_lineage: dict[str, dict[str, Any]] = {}
            for entry in entries:
                if not isinstance(entry, dict) or "candidate" not in entry:
                    raise ValueError("Invalid candidate schedule entry")
                candidate = Candidate.from_dict(entry["candidate"])
                lineage = entry.get("lineage", {})
                if not isinstance(lineage, dict):
                    raise ValueError("Candidate lineage must be an object")
                scheduled_candidates.append(candidate)
                scheduled_lineage[candidate.fingerprint] = lineage
            return scheduled_candidates, scheduled_lineage

        dataset_bytes = json.dumps(dataset, separators=(",", ":")).encode("utf-8")
        dataset_sha256 = hashlib.sha256(dataset_bytes).hexdigest()
        run_id = uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        deadline = time.monotonic() + self.global_timeout_seconds
        if candidate_schedule is None:
            population = self.agents.design_population(self.population_size, "generation-0-design")
            lineage_by_fingerprint = {
                candidate.fingerprint: {
                    "generation_created": 0,
                    "operator": "initial_population",
                    "parent_fingerprint": None,
                    "parent_fingerprints": [],
                    "origin": "candidate_designer",
                }
                for candidate in population
            }
        else:
            population, lineage_by_fingerprint = load_scheduled_population(0)
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

            evaluated_by_fingerprint = {item.candidate.fingerprint: item for item in evaluations}
            evaluations = [
                evaluated_by_fingerprint[candidate.fingerprint]
                for candidate in candidates
                if candidate.fingerprint in evaluated_by_fingerprint
            ]
            valid = [item for item in evaluations if item.valid]
            records = [item.to_dict() for item in evaluations]
            analysis = self.agents.analyze(records, f"generation-{generation}-analysis")
            front = _pareto_front(valid)
            history.append({
                "generation": generation,
                "candidate_population": [
                    {
                        "candidate": candidate.to_dict(),
                        "lineage": lineage_by_fingerprint.get(candidate.fingerprint, {}),
                    }
                    for candidate in candidates
                ],
                "evaluations": records,
                "valid_count": len(valid),
                "population_diversity": len(unique),
                "strategy_diversity": len({item.candidate.strategy for item in evaluations}),
                "pareto_front_fingerprints": [item.candidate.fingerprint for item in front],
                "agent_analysis": analysis,
            })

            if stop_reason == "global_timeout":
                break
            if evaluations_used >= self.max_evaluations:
                stop_reason = "evaluation_budget_exhausted"
                break
            if generation < generations - 1:
                if candidate_schedule is not None:
                    population, lineage_by_fingerprint = load_scheduled_population(generation + 1)
                    continue
                parents = _select_parents(valid, limit=min(4, max(1, len(valid))))
                next_population: list[Candidate] = []
                next_lineage: dict[str, dict[str, Any]] = {}
                seen: set[str] = set()
                elite_count = min(2, len(parents))
                for item in parents[:elite_count]:
                    candidate = item.candidate
                    if candidate.fingerprint in seen:
                        continue
                    next_population.append(candidate)
                    seen.add(candidate.fingerprint)
                    next_lineage[candidate.fingerprint] = {
                        "generation_created": generation + 1,
                        "operator": "elite_carryover",
                        "parent_fingerprint": candidate.fingerprint,
                        "parent_fingerprints": [candidate.fingerprint],
                        "origin": "selection",
                    }

                attempts = 0
                while len(next_population) < self.population_size and attempts < self.population_size * 12:
                    attempts += 1
                    if not parents:
                        child = self.generator.mutate(self.random.choice(population))
                        operator = "restart_mutation"
                        parent_fingerprints = []
                    elif len(parents) > 1 and self.random.random() < 0.65:
                        first, second = self.random.sample(parents, 2)
                        child = self.generator.crossover(first.candidate, second.candidate)
                        operator = "crossover"
                        parent_fingerprints = [first.candidate.fingerprint, second.candidate.fingerprint]
                    else:
                        parent = self.random.choice(parents)
                        child = self.generator.mutate(parent.candidate)
                        operator = "mutation"
                        parent_fingerprints = [parent.candidate.fingerprint]
                    if child.fingerprint in seen:
                        continue
                    next_population.append(child)
                    seen.add(child.fingerprint)
                    next_lineage[child.fingerprint] = {
                        "generation_created": generation + 1,
                        "operator": operator,
                        "parent_fingerprint": parent_fingerprints[0] if parent_fingerprints else None,
                        "parent_fingerprints": parent_fingerprints,
                        "origin": "candidate_generator",
                    }

                while len(next_population) < self.population_size:
                    parent = self.random.choice(parents).candidate if parents else self.random.choice(population)
                    child = self.generator.mutate(parent)
                    next_population.append(child)
                    next_lineage.setdefault(child.fingerprint, {
                        "generation_created": generation + 1,
                        "operator": "mutation_duplicate_fallback",
                        "parent_fingerprint": parent.fingerprint,
                        "parent_fingerprints": [parent.fingerprint],
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
        all_evaluations = [
            Evaluation(
                Candidate.from_dict(record["candidate"]),
                True,
                record["median_seconds"],
                record["median_peak_bytes"],
                lineage=record.get("lineage"),
                runtime_samples_seconds=record.get("runtime_samples_seconds"),
                peak_samples_bytes=record.get("peak_samples_bytes"),
                runtime_mad_seconds=record.get("runtime_mad_seconds"),
            )
            for record in all_valid
        ]
        pareto_records = [item.to_dict() for item in _pareto_front(all_evaluations)]
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": "1.3",
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
                "candidate_space_version": "0.3",
                "selection": "pareto_crowding_with_strategy_diversity",
            },
            "generations": history,
            "best_candidate": best,
            "pareto_front": pareto_records,
            "budget": {
                "max_evaluations": self.max_evaluations,
                "evaluations_used": evaluations_used,
                "max_workers": self.max_workers,
                "global_timeout_seconds": self.global_timeout_seconds,
                "stop_reason": stop_reason,
            },
            "note": "Exploratory local microbenchmark; repeat under controlled conditions.",
        }
