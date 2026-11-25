from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

from metaforge.agents.code_safety import StaticCodeReviewer
from metaforge.evolution.metadata import runtime_metadata
from metaforge.evolution.benchmark_selection import (
    MINIMUM_PROMOTION_REPEATS,
    passes_baseline_gate,
)
from metaforge.evolution.workloads import (
    INTEGER_SORT_SUITE_NAME,
    INTEGER_SORT_SUITE_VERSION,
    INTEGER_SORT_WORKLOAD_NAMES,
    build_integer_sort_workloads,
)
from metaforge.sandbox.runner import SandboxPreparation, SandboxResult, SandboxRunner


class CodeReplayEngine:
    """Re-measure archived source candidates without making any model calls."""

    def __init__(
        self,
        sandbox_runner: SandboxRunner,
        reviewer: StaticCodeReviewer | None = None,
        global_timeout_seconds: float = 180.0,
    ) -> None:
        if not isinstance(sandbox_runner, SandboxRunner):
            raise TypeError("sandbox_runner must be a SandboxRunner")
        if type(global_timeout_seconds) not in (int, float) or not 10 <= global_timeout_seconds <= 900:
            raise ValueError("global_timeout_seconds must be 10..900")
        self.sandbox_runner = sandbox_runner
        self.reviewer = reviewer or StaticCodeReviewer()
        self.global_timeout_seconds = float(global_timeout_seconds)

    @staticmethod
    def _dataset_digest(dataset: list[int]) -> str:
        payload = json.dumps(dataset, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _resolve_workloads(self, original: dict[str, Any], dataset: list[int]) -> tuple[dict[str, list[int]], dict[str, Any]]:
        benchmark = original.get("benchmark")
        if benchmark is None:
            # Backward compatibility for V0.7 archives created before the
            # multi-workload benchmark suite existed.
            workloads = {"input": list(dataset)}
            metadata = {
                "suite": "legacy-single-input",
                "suite_version": "0",
                "aggregation": "single_workload_median",
                "workload_names": ["input"],
                "workload_sha256": {"input": self._dataset_digest(dataset)},
            }
            return workloads, metadata

        if not isinstance(benchmark, dict):
            raise ValueError("Original benchmark metadata is invalid")
        if (
            benchmark.get("suite") != INTEGER_SORT_SUITE_NAME
            or benchmark.get("suite_version") != INTEGER_SORT_SUITE_VERSION
        ):
            raise ValueError("Archived benchmark suite version is unsupported")
        workloads = build_integer_sort_workloads(dataset)
        expected_names = list(INTEGER_SORT_WORKLOAD_NAMES)
        expected_hashes = {
            name: self._dataset_digest(values) for name, values in workloads.items()
        }
        if benchmark.get("workload_names") != expected_names:
            raise ValueError("Archived benchmark workload schedule is invalid")
        if benchmark.get("workload_sha256") != expected_hashes:
            raise ValueError("Archived benchmark workload fingerprint mismatch")
        metadata = {
            "suite": INTEGER_SORT_SUITE_NAME,
            "suite_version": INTEGER_SORT_SUITE_VERSION,
            "aggregation": "median_of_workload_medians_equal_weight",
            "workload_names": expected_names,
            "workload_sha256": expected_hashes,
        }
        return workloads, metadata

    def run(self, original: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(original, dict) or original.get("engine") != "metaforge_code_evolution":
            raise ValueError("Input is not a MetaForge code-evolution artifact")
        replay_data = original.get("replay")
        dataset = replay_data.get("dataset") if isinstance(replay_data, dict) else None
        if not isinstance(dataset, list) or len(dataset) > 100_000:
            raise ValueError("Artifact does not contain a valid embedded replay dataset")
        if any(type(value) is not int or not -(2**63) <= value < 2**63 for value in dataset):
            raise ValueError("Replay dataset contains invalid integer values")
        if original.get("dataset_size") != len(dataset):
            raise ValueError("Embedded replay dataset size does not match the original artifact")
        dataset_sha256 = self._dataset_digest(dataset)
        if dataset_sha256 != original.get("dataset_sha256"):
            raise ValueError("Embedded replay dataset fingerprint does not match the original artifact")
        workloads, benchmark_metadata = self._resolve_workloads(original, dataset)

        budget = original.get("budget")
        repeats = budget.get("repeats") if isinstance(budget, dict) else None
        if type(repeats) is not int or not 1 <= repeats <= 10:
            raise ValueError("Original artifact has an invalid repeat count")

        scheduled: dict[str, dict[str, Any]] = {}
        generations = original.get("generations")
        if not isinstance(generations, list):
            raise ValueError("Original artifact has no valid generation schedule")
        for generation in generations:
            if not isinstance(generation, dict):
                raise ValueError("Original generation record is invalid")
            evaluations = generation.get("evaluations", [])
            if not isinstance(evaluations, list):
                raise ValueError("Original evaluation schedule is invalid")
            for evaluation in evaluations:
                if not isinstance(evaluation, dict):
                    raise ValueError("Original candidate evaluation is invalid")
                candidate = evaluation.get("candidate", {})
                if not isinstance(candidate, dict):
                    raise ValueError("Original candidate record is invalid")
                source = candidate.get("source_code")
                fingerprint = candidate.get("fingerprint")
                if not isinstance(source, str) or not isinstance(fingerprint, str):
                    continue
                if len(source) > 50_000:
                    raise ValueError("Candidate source exceeds the replay source limit")
                try:
                    source_bytes = source.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ValueError("Candidate source is not valid UTF-8") from exc
                if not source_bytes or len(source_bytes) > 50_000:
                    raise ValueError("Candidate source exceeds the replay source limit")
                digest = hashlib.sha256(source_bytes).hexdigest()
                if digest != fingerprint:
                    raise ValueError("Candidate source fingerprint mismatch; refusing replay")
                scheduled.setdefault(fingerprint, candidate)
        if len(scheduled) > 20:
            raise ValueError("Archived candidate schedule exceeds the replay limit")

        # Replay measures the same trusted reference so historical candidate
        # rankings use the same promotion rule as a fresh evolution run.
        baseline_source = "def solve(values):\n    return sorted(values)\n"
        baseline_fingerprint = hashlib.sha256(baseline_source.encode("utf-8")).hexdigest()
        scheduled[baseline_fingerprint] = {
            "source_code": baseline_source,
            "fingerprint": baseline_fingerprint,
            "generation": -1,
            "proposal_index": 0,
            "parent_fingerprint": None,
            "rationale": "Trusted Python sorted() reference",
        }

        deadline = time.monotonic() + self.global_timeout_seconds
        preparation = self.sandbox_runner.prepare()
        if not isinstance(preparation, SandboxPreparation) or not preparation.rootless:
            raise RuntimeError("Code replay requires a verified rootless sandbox runtime")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Global time budget exhausted during sandbox preflight")
        smoke = self.sandbox_runner.smoke(timeout_seconds=remaining)
        if (
            not isinstance(smoke, SandboxResult)
            or not smoke.success
            or len(smoke.algorithm_samples_seconds) != 2
            or any(
                type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                for value in smoke.algorithm_samples_seconds
            )
        ):
            raise RuntimeError("Rootless sandbox smoke failed; replay is blocked")

        results: list[dict[str, Any]] = []
        stop_reason = "schedule_complete"
        for fingerprint, candidate in scheduled.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop_reason = "global_timeout"
                break
            source = candidate["source_code"]
            review = self.reviewer.review(source)
            record: dict[str, Any] = {
                "candidate": {
                    "fingerprint": fingerprint,
                    "source_code": source,
                    "generation": candidate.get("generation"),
                    "proposal_index": candidate.get("proposal_index"),
                    "parent_fingerprint": candidate.get("parent_fingerprint"),
                    "rationale": candidate.get("rationale"),
                },
                "review": {
                    "approved": review.approved,
                    "findings": list(review.findings),
                    "imports": list(review.imports),
                },
                "correctness_passed": False,
                "algorithm_samples_seconds": [],
                "median_algorithm_seconds": None,
                "container_wall_seconds": 0.0,
                "workload_results": {},
                "error": None,
            }
            if not review.approved:
                record["error"] = "Current static review policy rejected this archived source"
                results.append(record)
                continue

            all_samples: list[float] = []
            workload_medians: list[float] = []
            workload_failed = False
            for name, values in workloads.items():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop_reason = "global_timeout"
                    record["error"] = "Global time budget exhausted before all workloads completed"
                    workload_failed = True
                    break
                try:
                    execution = self.sandbox_runner.run_python(
                        source, values, timeout_seconds=remaining, repeats=repeats
                    )
                except Exception as exc:
                    record["error"] = f"Sandbox runner failed for workload '{name}': {type(exc).__name__}"
                    workload_failed = True
                    break
                if not isinstance(execution, SandboxResult):
                    record["error"] = f"Sandbox returned an invalid result type for workload '{name}'"
                    workload_failed = True
                    break

                record["container_wall_seconds"] += execution.wall_seconds
                samples = execution.algorithm_samples_seconds
                valid_samples = (
                    len(samples) == repeats
                    and all(
                        type(value) in (int, float) and math.isfinite(value) and value >= 0
                        for value in samples
                    )
                )
                if not execution.success or execution.output != sorted(values) or not valid_samples:
                    error = execution.error or f"Correctness or timing validation failed for workload '{name}'"
                    record["workload_results"][name] = {
                        "correctness_passed": False,
                        "algorithm_samples_seconds": list(samples) if valid_samples else [],
                        "median_algorithm_seconds": statistics.median(samples) if valid_samples else None,
                        "container_wall_seconds": execution.wall_seconds,
                        "error": error,
                    }
                    record["error"] = f"Workload '{name}' failed: {error}"
                    workload_failed = True
                    break

                median = statistics.median(samples)
                all_samples.extend(samples)
                workload_medians.append(median)
                record["workload_results"][name] = {
                    "correctness_passed": True,
                    "algorithm_samples_seconds": list(samples),
                    "median_algorithm_seconds": median,
                    "container_wall_seconds": execution.wall_seconds,
                    "error": None,
                }

            record["algorithm_samples_seconds"] = all_samples
            if not workload_failed and len(workload_medians) == len(workloads):
                record["correctness_passed"] = True
                record["median_algorithm_seconds"] = statistics.median(workload_medians)
            elif record["error"] is None:
                record["error"] = "Candidate did not complete the full benchmark workload suite"
            results.append(record)
            if stop_reason == "global_timeout":
                break

        baseline = next(
            (
                item for item in results
                if item["candidate"].get("fingerprint") == baseline_fingerprint
                and item["correctness_passed"]
            ),
            None,
        )
        if baseline is None:
            raise RuntimeError("Trusted baseline failed during replay; candidate ranking is invalid")
        baseline_score = baseline["median_algorithm_seconds"]
        promoted = [
            item for item in results
            if item["correctness_passed"]
            and item["candidate"].get("fingerprint") != baseline_fingerprint
            and item["median_algorithm_seconds"] <= baseline_score * 0.95
            and passes_baseline_gate(
                item["workload_results"],
                baseline["workload_results"],
                0.05,
            )
        ]
        best = min(promoted, key=lambda item: item["median_algorithm_seconds"]) if promoted else baseline
        selection = {
            "policy": "baseline_gated_minimum_relative_improvement",
            "minimum_improvement_ratio": 0.05,
            "minimum_repeats_for_promotion": MINIMUM_PROMOTION_REPEATS,
            "baseline_median_seconds": baseline_score,
            "selected_median_seconds": best["median_algorithm_seconds"],
            "selected_source": "candidate" if promoted else "trusted_baseline",
            "improvement_ratio": (
                (baseline_score - best["median_algorithm_seconds"]) / baseline_score
                if baseline_score else 0.0
            ),
            "candidate_promoted": bool(promoted),
            "verification_stage": "replay_gate_passed" if promoted else "trusted_baseline_retained",
        }
        return {
            "schema_version": "1.1",
            "run_id": uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "engine": "metaforge_code_replay",
            "engine_version": "0.9.0-dev",
            "replay_of": original.get("run_id"),
            "original_created_at": original.get("created_at"),
            "environment": runtime_metadata(),
            "problem": original.get("problem"),
            "dataset_size": len(dataset),
            "dataset_sha256": dataset_sha256,
            "benchmark": benchmark_metadata,
            "candidate_results": results,
            "baseline": baseline,
            "selection": selection,
            "best_candidate": best,
            "budget": {
                "candidate_count": len(scheduled),
                "candidates_evaluated": len(results),
                "repeats": repeats,
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
            },
            "note": (
                "Replay re-measures archived source candidates without model calls. "
                "Workloads are reconstructed from the integrity-checked source dataset and "
                "verified against archived workload fingerprints. Hardware, runtime, image, "
                "and load may differ from the original experiment; compare samples only under "
                "equivalent environments."
            ),
        }
