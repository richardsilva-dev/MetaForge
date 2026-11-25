from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from uuid import uuid4
from dataclasses import dataclass, field
from typing import Any

from metaforge.agents.code_safety import CodeReviewResult, StaticCodeReviewer
from metaforge.agents.contracts import AgentResult, AgentTask
from metaforge.sandbox.runner import SandboxPreparation, SandboxResult, SandboxRunner
from metaforge.evolution.metadata import runtime_metadata
from metaforge.evolution.workloads import (
    INTEGER_SORT_SUITE_NAME,
    INTEGER_SORT_SUITE_VERSION,
    INTEGER_SORT_WORKLOAD_NAMES,
    build_integer_sort_workloads,
)


@dataclass(frozen=True, slots=True)
class CodeCandidate:
    source_code: str
    rationale: str
    generation: int
    proposal_index: int
    parent_fingerprint: str | None
    model: str | None
    fingerprint: str

    @classmethod
    def create(
        cls,
        source_code: str,
        rationale: str,
        generation: int,
        proposal_index: int,
        parent_fingerprint: str | None,
        model: str | None,
    ) -> "CodeCandidate":
        fingerprint = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
        return cls(
            source_code, rationale[:1000], generation, proposal_index,
            parent_fingerprint, model, fingerprint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_code": self.source_code,
            "rationale": self.rationale,
            "generation": self.generation,
            "proposal_index": self.proposal_index,
            "parent_fingerprint": self.parent_fingerprint,
            "model": self.model,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class CodeEvaluation:
    candidate: CodeCandidate
    review: CodeReviewResult
    success: bool
    algorithm_samples_seconds: tuple[float, ...]
    median_algorithm_seconds: float | None
    container_wall_seconds: float | None
    workload_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "review": {
                "approved": self.review.approved,
                "source_sha256": self.review.source_sha256,
                "imports": list(self.review.imports),
                "findings": list(self.review.findings),
            },
            "correctness_passed": self.success,
            "algorithm_samples_seconds": list(self.algorithm_samples_seconds),
            "median_algorithm_seconds": self.median_algorithm_seconds,
            "container_wall_seconds": self.container_wall_seconds,
            "workload_results": self.workload_results,
            "error": self.error,
        }


class CodeEvolutionEngine:
    """Generate, review, and sandbox-evaluate Python sorting source across workloads."""

    def __init__(
        self,
        designer: Any,
        sandbox_runner: SandboxRunner,
        reviewer: StaticCodeReviewer | None = None,
        seed: int = 7,
        population_size: int = 2,
        generations: int = 2,
        repeats: int = 3,
        max_agent_calls: int = 8,
        global_timeout_seconds: float = 180.0,
    ) -> None:
        if designer is None or not callable(getattr(designer, "run", None)):
            raise ValueError("designer must provide run(AgentTask)")
        if not isinstance(sandbox_runner, SandboxRunner):
            raise TypeError("sandbox_runner must be a SandboxRunner")
        if type(population_size) is not int or not 1 <= population_size <= 6:
            raise ValueError("population_size must be 1..6")
        if type(generations) is not int or not 1 <= generations <= 5:
            raise ValueError("generations must be 1..5")
        if type(repeats) is not int or not 1 <= repeats <= 10:
            raise ValueError("repeats must be 1..10")
        if type(max_agent_calls) is not int or not 1 <= max_agent_calls <= 20:
            raise ValueError("max_agent_calls must be 1..20")
        if type(global_timeout_seconds) not in (int, float) or not 10 <= global_timeout_seconds <= 900:
            raise ValueError("global_timeout_seconds must be 10..900")
        self.designer = designer
        self.sandbox_runner = sandbox_runner
        self.reviewer = reviewer or StaticCodeReviewer()
        self.seed = seed
        self.population_size = population_size
        self.generations = generations
        self.repeats = repeats
        self.max_agent_calls = max_agent_calls
        self.global_timeout_seconds = float(global_timeout_seconds)

    @staticmethod
    def _dataset_digest(dataset: list[int]) -> str:
        payload = json.dumps(dataset, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _evaluate_candidate(
        self,
        candidate: CodeCandidate,
        review: CodeReviewResult,
        workloads: dict[str, list[int]],
        deadline: float,
    ) -> CodeEvaluation:
        workload_results: dict[str, dict[str, Any]] = {}
        all_samples: list[float] = []
        workload_medians: list[float] = []
        wall_seconds = 0.0

        for name, values in workloads.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return CodeEvaluation(
                    candidate, review, False, tuple(all_samples), None,
                    wall_seconds, workload_results,
                    "Global time budget exhausted before all workloads completed",
                )
            try:
                result = self.sandbox_runner.run_python(
                    candidate.source_code,
                    values,
                    timeout_seconds=remaining,
                    repeats=self.repeats,
                )
            except Exception as exc:
                return CodeEvaluation(
                    candidate, review, False, tuple(all_samples), None,
                    wall_seconds, workload_results,
                    f"Sandbox runner failed for workload '{name}': {type(exc).__name__}",
                )
            if not isinstance(result, SandboxResult):
                return CodeEvaluation(
                    candidate, review, False, tuple(all_samples), None,
                    wall_seconds, workload_results,
                    f"Sandbox returned an invalid result type for workload '{name}'",
                )

            wall_seconds += result.wall_seconds
            samples = result.algorithm_samples_seconds
            samples_valid = (
                len(samples) == self.repeats
                and all(
                    type(value) in (int, float) and math.isfinite(value) and value >= 0
                    for value in samples
                )
            )
            expected = sorted(values)
            if not result.success or result.output != expected or not samples_valid:
                error = result.error or (
                    f"Correctness or timing validation failed for workload '{name}'"
                )
                workload_results[name] = {
                    "correctness_passed": False,
                    "algorithm_samples_seconds": list(samples) if samples_valid else [],
                    "median_algorithm_seconds": (
                        statistics.median(samples) if samples_valid else None
                    ),
                    "container_wall_seconds": result.wall_seconds,
                    "error": error,
                }
                return CodeEvaluation(
                    candidate, review, False, tuple(all_samples), None,
                    wall_seconds, workload_results,
                    f"Workload '{name}' failed: {error}",
                )

            median = statistics.median(samples)
            all_samples.extend(samples)
            workload_medians.append(median)
            workload_results[name] = {
                "correctness_passed": True,
                "algorithm_samples_seconds": list(samples),
                "median_algorithm_seconds": median,
                "container_wall_seconds": result.wall_seconds,
                "error": None,
            }

        # Equal weight per workload prevents a workload's cardinality or
        # sample count from dominating the aggregate score.
        aggregate_median = statistics.median(workload_medians)
        return CodeEvaluation(
            candidate, review, True, tuple(all_samples), aggregate_median,
            wall_seconds, workload_results, None,
        )

    def run(self, dataset: list[int]) -> dict[str, Any]:
        if not isinstance(dataset, list) or len(dataset) > 100_000:
            raise ValueError("dataset must be a list with at most 100000 integers")
        if any(type(value) is not int or not -(2**63) <= value < 2**63 for value in dataset):
            raise ValueError("dataset must contain signed 64-bit integers")
        workloads = build_integer_sort_workloads(dataset)
        workload_digests = {
            name: self._dataset_digest(values) for name, values in workloads.items()
        }

        # Fail closed before spending model tokens or accepting source proposals.
        deadline = time.monotonic() + self.global_timeout_seconds
        preparation = self.sandbox_runner.prepare()
        if not isinstance(preparation, SandboxPreparation) or not preparation.rootless:
            raise RuntimeError("Code evolution requires a verified rootless sandbox runtime")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Global time budget exhausted during sandbox preflight")
        smoke = self.sandbox_runner.smoke(timeout_seconds=remaining)
        if not isinstance(smoke, SandboxResult) or not smoke.success:
            detail = smoke.error if isinstance(smoke, SandboxResult) else "invalid smoke result"
            raise RuntimeError(f"Rootless sandbox smoke failed: {detail or 'policy verification failed'}")
        if len(smoke.algorithm_samples_seconds) != 2:
            raise RuntimeError("Rootless sandbox smoke returned invalid timing samples")
        calls_used = 0
        stop_reason = "generation_limit"
        seen_fingerprints: set[str] = set()
        history: list[dict[str, Any]] = []
        all_valid: list[CodeEvaluation] = []
        parent_pool: list[CodeEvaluation] = []

        for generation in range(self.generations):
            proposals: list[dict[str, Any]] = []
            evaluations: list[CodeEvaluation] = []
            for proposal_index in range(self.population_size):
                if calls_used >= self.max_agent_calls:
                    stop_reason = "agent_call_budget_exhausted"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop_reason = "global_timeout"
                    break

                parent = parent_pool[proposal_index % len(parent_pool)] if parent_pool else None
                payload = {
                    "generation": generation,
                    "proposal_index": proposal_index,
                    "seed": self.seed,
                    "dataset_size": len(dataset),
                    "benchmark_suite": INTEGER_SORT_SUITE_NAME,
                    "benchmark_suite_version": INTEGER_SORT_SUITE_VERSION,
                    "benchmark_workloads": list(INTEGER_SORT_WORKLOAD_NAMES),
                    "max_source_bytes": 50_000,
                    "allowed_imports": [
                        "heapq", "bisect", "math", "operator", "collections",
                        "itertools", "functools", "array", "typing",
                    ],
                    "contract": (
                        "Define a pure solve(values) function returning a sorted list of signed "
                        "64-bit integers. It must remain correct and efficient across every "
                        "named benchmark workload, not only the original input ordering."
                    ),
                    "parent_source": parent.candidate.source_code if parent else None,
                    "parent_fingerprint": parent.candidate.fingerprint if parent else None,
                    "parent_median_algorithm_seconds": parent.median_algorithm_seconds if parent else None,
                    "parent_feedback": parent.error if parent else None,
                }
                task = AgentTask(
                    task_id=f"code-generation-g{generation}-p{proposal_index}",
                    objective=(
                        "Generate an initial correct sorting implementation robust across the benchmark suite."
                        if parent is None else
                        "Mutate the parent sorting implementation while preserving correctness and seeking lower aggregate runtime across all benchmark workloads."
                    ),
                    payload=payload,
                )
                calls_used += 1
                try:
                    agent_result = self.designer.run(task, timeout_seconds=remaining)
                except Exception as exc:
                    proposals.append({
                        "task_id": task.task_id,
                        "success": False,
                        "error": f"Designer failed: {type(exc).__name__}",
                    })
                    continue
                if not isinstance(agent_result, AgentResult):
                    proposals.append({
                        "task_id": task.task_id,
                        "success": False,
                        "error": "Designer returned an invalid result type",
                    })
                    continue
                if not agent_result.success or not isinstance(agent_result.output, dict):
                    proposals.append({
                        "task_id": task.task_id,
                        "success": False,
                        "error": agent_result.error or "Designer returned an invalid proposal",
                    })
                    continue

                source_code = agent_result.output.get("source_code")
                rationale = agent_result.output.get("rationale", "")
                model = agent_result.output.get("model")
                if not isinstance(source_code, str) or not source_code or len(source_code) > 50_000:
                    proposals.append({
                        "task_id": task.task_id,
                        "success": False,
                        "error": "Proposal source is missing or exceeds the 50000-character limit",
                    })
                    continue
                try:
                    candidate = CodeCandidate.create(
                        source_code, str(rationale), generation, proposal_index,
                        parent.candidate.fingerprint if parent else None,
                        str(model) if model is not None else None,
                    )
                except (UnicodeEncodeError, ValueError):
                    proposals.append({
                        "task_id": task.task_id,
                        "success": False,
                        "error": "Proposal source could not be fingerprinted as UTF-8",
                    })
                    continue
                if candidate.fingerprint in seen_fingerprints:
                    proposals.append({
                        "task_id": task.task_id,
                        "success": False,
                        "fingerprint": candidate.fingerprint,
                        "error": "Duplicate source fingerprint",
                    })
                    continue
                seen_fingerprints.add(candidate.fingerprint)
                review = self.reviewer.review(source_code)
                proposal_record = {
                    "task_id": task.task_id,
                    "success": review.approved,
                    "fingerprint": candidate.fingerprint,
                    "review_findings": list(review.findings),
                }
                proposals.append(proposal_record)
                if not review.approved:
                    continue

                evaluation = self._evaluate_candidate(candidate, review, workloads, deadline)
                evaluations.append(evaluation)
                if evaluation.success:
                    all_valid.append(evaluation)
                if time.monotonic() >= deadline:
                    stop_reason = "global_timeout"
                    break

            evaluations.sort(
                key=lambda item: item.median_algorithm_seconds
                if item.success and item.median_algorithm_seconds is not None
                else float("inf")
            )
            history.append({
                "generation": generation,
                "proposals": proposals,
                "evaluations": [item.to_dict() for item in evaluations],
                "valid_count": sum(item.success for item in evaluations),
            })
            current_parents = [item for item in evaluations if item.success]
            parent_pool = current_parents[:2] or sorted(
                all_valid, key=lambda item: item.median_algorithm_seconds if item.median_algorithm_seconds is not None else float("inf")
            )[:2]
            if stop_reason in {"global_timeout", "agent_call_budget_exhausted"}:
                break

        best = min(
            all_valid,
            key=lambda item: item.median_algorithm_seconds
            if item.median_algorithm_seconds is not None else float("inf"),
        ) if all_valid else None
        return {
            "schema_version": "1.1",
            "run_id": uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "engine_version": "0.8.0-dev",
            "environment": runtime_metadata(),
            "engine": "metaforge_code_evolution",
            "problem": "integer_sort",
            "seed": self.seed,
            "dataset_size": len(dataset),
            "dataset_sha256": self._dataset_digest(dataset),
            "benchmark": {
                "suite": INTEGER_SORT_SUITE_NAME,
                "suite_version": INTEGER_SORT_SUITE_VERSION,
                "aggregation": "median_of_workload_medians_equal_weight",
                "workload_names": list(INTEGER_SORT_WORKLOAD_NAMES),
                "workload_sha256": workload_digests,
            },
            "generations": history,
            "best_candidate": best.to_dict() if best else None,
            "budget": {
                "max_agent_calls": self.max_agent_calls,
                "agent_calls_used": calls_used,
                "population_size": self.population_size,
                "generations_requested": self.generations,
                "repeats": self.repeats,
                "workload_count": len(workloads),
                "global_timeout_seconds": self.global_timeout_seconds,
                "stop_reason": stop_reason,
            },
            "sandbox": {
                "runtime": preparation.runtime,
                "runtime_version": preparation.runtime_version,
                "image": preparation.image,
                "rootless": preparation.rootless,
                "smoke_passed": smoke.success,
                "smoke_wall_seconds": smoke.wall_seconds,
                "smoke_algorithm_samples_seconds": list(smoke.algorithm_samples_seconds),
            },
            "measurement_note": (
                "Each candidate is checked on the same deterministic integer-sort workload suite: "
                "original input, ascending, descending, nearly sorted, duplicate-heavy, "
                "low-cardinality, organ-pipe, and all-equal inputs. Each workload receives the "
                "same repeat count; the aggregate score is the median of per-workload medians, "
                "giving every workload equal weight. Raw samples and per-workload medians are "
                "retained. Each sample is measured by the trusted container supervisor around a fresh "
                "candidate-worker process, including interpreter startup and IPC overhead; samples "
                "are not pure algorithm-only timings. Container wall time is reported separately "
                "and is not used for candidate ranking."
            ),
        }
