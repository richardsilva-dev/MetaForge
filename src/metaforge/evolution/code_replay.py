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

    def run(self, original: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(original, dict) or original.get("engine") != "metaforge_code_evolution":
            raise ValueError("Input is not a MetaForge code-evolution artifact")
        replay_data = original.get("replay")
        dataset = replay_data.get("dataset") if isinstance(replay_data, dict) else None
        if not isinstance(dataset, list) or len(dataset) > 100_000:
            raise ValueError("Artifact does not contain a valid embedded replay dataset")
        if any(type(value) is not int or not -(2**63) <= value < 2**63 for value in dataset):
            raise ValueError("Replay dataset contains invalid integer values")
        dataset_sha256 = self._dataset_digest(dataset)
        if dataset_sha256 != original.get("dataset_sha256"):
            raise ValueError("Embedded replay dataset fingerprint does not match the original artifact")

        budget = original.get("budget")
        repeats = budget.get("repeats") if isinstance(budget, dict) else None
        if type(repeats) is not int or not 1 <= repeats <= 10:
            raise ValueError("Original artifact has an invalid repeat count")

        scheduled: dict[str, dict[str, Any]] = {}
        for generation in original.get("generations", []):
            for evaluation in generation.get("evaluations", []):
                candidate = evaluation.get("candidate", {})
                source = candidate.get("source_code")
                fingerprint = candidate.get("fingerprint")
                if not isinstance(source, str) or not isinstance(fingerprint, str):
                    continue
                digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
                if digest != fingerprint:
                    raise ValueError("Candidate source fingerprint mismatch; refusing replay")
                scheduled.setdefault(fingerprint, candidate)
        if len(scheduled) > 20:
            raise ValueError("Archived candidate schedule exceeds the replay limit")

        deadline = time.monotonic() + self.global_timeout_seconds
        preparation: SandboxPreparation = self.sandbox_runner.prepare()
        if not preparation.rootless:
            raise RuntimeError("Code replay requires a verified rootless sandbox runtime")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Global time budget exhausted during sandbox preflight")
        smoke = self.sandbox_runner.smoke(timeout_seconds=remaining)
        if not isinstance(smoke, SandboxResult) or not smoke.success or len(smoke.algorithm_samples_seconds) != 2:
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
                "container_wall_seconds": None,
                "error": None,
            }
            if not review.approved:
                record["error"] = "Current static review policy rejected this archived source"
                results.append(record)
                continue
            try:
                execution = self.sandbox_runner.run_python(
                    source, dataset, timeout_seconds=remaining, repeats=repeats
                )
            except Exception as exc:
                record["error"] = f"Sandbox runner failed: {type(exc).__name__}"
                results.append(record)
                continue
            record["container_wall_seconds"] = execution.wall_seconds
            samples = execution.algorithm_samples_seconds
            valid_samples = (
                isinstance(execution, SandboxResult)
                and len(samples) == repeats
                and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in samples)
            )
            if execution.success and valid_samples:
                record["correctness_passed"] = True
                record["algorithm_samples_seconds"] = list(samples)
                record["median_algorithm_seconds"] = statistics.median(samples)
            else:
                record["error"] = execution.error or "Sandbox returned invalid timing samples"
            results.append(record)

        valid = [item for item in results if item["correctness_passed"]]
        best = min(valid, key=lambda item: item["median_algorithm_seconds"]) if valid else None
        return {
            "schema_version": "1.0",
            "run_id": uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "engine": "metaforge_code_replay",
            "engine_version": "0.7.0-dev",
            "replay_of": original.get("run_id"),
            "original_created_at": original.get("created_at"),
            "environment": runtime_metadata(),
            "problem": original.get("problem"),
            "dataset_size": len(dataset),
            "dataset_sha256": dataset_sha256,
            "candidate_results": results,
            "best_candidate": best,
            "budget": {
                "candidate_count": len(scheduled),
                "candidates_evaluated": len(results),
                "repeats": repeats,
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
                "Hardware, runtime, image, and load may differ from the original experiment; "
                "compare algorithm samples only under equivalent environments."
            ),
        }
