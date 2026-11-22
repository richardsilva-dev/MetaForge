from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from uuid import uuid4
from dataclasses import dataclass
from typing import Any

from metaforge.agents.code_safety import CodeReviewResult, StaticCodeReviewer
from metaforge.agents.contracts import AgentResult, AgentTask
from metaforge.sandbox.runner import SandboxPreparation, SandboxResult, SandboxRunner
from metaforge.evolution.metadata import runtime_metadata


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
            "error": self.error,
        }


class CodeEvolutionEngine:
    """Generate, statically review, and sandbox-evaluate Python algorithm source."""

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

    def run(self, dataset: list[int]) -> dict[str, Any]:
        if not isinstance(dataset, list) or len(dataset) > 100_000:
            raise ValueError("dataset must be a list with at most 100000 integers")
        if any(type(value) is not int or not -(2**63) <= value < 2**63 for value in dataset):
            raise ValueError("dataset must contain signed 64-bit integers")

        # Fail closed before spending model tokens or accepting source proposals.
        deadline = time.monotonic() + self.global_timeout_seconds
        preparation: SandboxPreparation = self.sandbox_runner.prepare()
        if not preparation.rootless:
            raise RuntimeError("Code evolution requires a rootless sandbox runtime")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Global time budget exhausted during sandbox preflight")
        smoke = self.sandbox_runner.smoke(timeout_seconds=remaining)
        if not smoke.success:
            raise RuntimeError(f"Rootless sandbox smoke failed: {smoke.error or 'policy verification failed'}")
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
                    "max_source_bytes": 50_000,
                    "allowed_imports": [
                        "heapq", "bisect", "math", "operator", "collections",
                        "itertools", "functools", "array", "typing",
                    ],
                    "contract": "Define a pure solve(values) function returning a sorted list of signed 64-bit integers.",
                    "parent_source": parent.candidate.source_code if parent else None,
                    "parent_fingerprint": parent.candidate.fingerprint if parent else None,
                    "parent_median_algorithm_seconds": parent.median_algorithm_seconds if parent else None,
                    "parent_feedback": parent.error if parent else None,
                }
                task = AgentTask(
                    task_id=f"code-generation-g{generation}-p{proposal_index}",
                    objective=(
                        "Generate an initial correct sorting implementation."
                        if parent is None else
                        "Mutate the parent sorting implementation while preserving correctness and seeking lower runtime."
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
                if not isinstance(source_code, str) or not source_code:
                    proposals.append({
                        "task_id": task.task_id,
                        "success": False,
                        "error": "Proposal did not contain source_code",
                    })
                    continue
                candidate = CodeCandidate.create(
                    source_code, str(rationale), generation, proposal_index,
                    parent.candidate.fingerprint if parent else None,
                    str(model) if model is not None else None,
                )
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

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop_reason = "global_timeout"
                    break
                try:
                    result = self.sandbox_runner.run_python(
                        source_code,
                        dataset,
                        timeout_seconds=remaining,
                        repeats=self.repeats,
                    )
                except Exception as exc:
                    evaluations.append(CodeEvaluation(
                        candidate, review, False, (), None, None,
                        f"Sandbox runner failed: {type(exc).__name__}",
                    ))
                    continue
                if not isinstance(result, SandboxResult):
                    evaluations.append(CodeEvaluation(
                        candidate, review, False, (), None, None,
                        "Sandbox returned an invalid result type",
                    ))
                    continue
                samples = result.algorithm_samples_seconds
                samples_valid = (
                    len(samples) == self.repeats
                    and all(
                        type(value) in (int, float) and math.isfinite(value) and value >= 0
                        for value in samples
                    )
                )
                if result.success and samples_valid:
                    median = statistics.median(samples)
                    evaluation = CodeEvaluation(
                        candidate, review, True, samples,
                        median, result.wall_seconds, None,
                    )
                    evaluations.append(evaluation)
                    all_valid.append(evaluation)
                else:
                    error = result.error or "Sandbox returned invalid timing samples"
                    evaluations.append(CodeEvaluation(
                        candidate, review, False, (), None, result.wall_seconds, error,
                    ))
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
                all_valid, key=lambda item: item.median_algorithm_seconds or float("inf")
            )[:2]
            if stop_reason in {"global_timeout", "agent_call_budget_exhausted"}:
                break

        best = min(
            all_valid,
            key=lambda item: item.median_algorithm_seconds
            if item.median_algorithm_seconds is not None else float("inf"),
        ) if all_valid else None
        return {
            "schema_version": "1.0",
            "run_id": uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "engine_version": "0.7.0-dev",
            "environment": runtime_metadata(),
            "engine": "metaforge_code_evolution",
            "problem": "integer_sort",
            "seed": self.seed,
            "dataset_size": len(dataset),
            "dataset_sha256": self._dataset_digest(dataset),
            "generations": history,
            "best_candidate": best.to_dict() if best else None,
            "budget": {
                "max_agent_calls": self.max_agent_calls,
                "agent_calls_used": calls_used,
                "population_size": self.population_size,
                "generations_requested": self.generations,
                "repeats": self.repeats,
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
                "Algorithm samples are measured inside the trusted container wrapper with perf_counter_ns. "
                "Container wall time is reported separately and is not used for candidate ranking. "
                "Native cross-language metrics are not compared against these Python measurements."
            ),
        }
