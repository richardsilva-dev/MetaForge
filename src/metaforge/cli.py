from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from pathlib import Path
import statistics
from typing import Any
from uuid import uuid4

from metaforge.evolution.engine import EvolutionEngine
from metaforge.evolution.metadata import runtime_metadata
from metaforge.candidates.models import Candidate
from metaforge.backends.python import PythonBackend
from metaforge.reporting.markdown import render_report
from metaforge.storage.json_store import archive_run, save_run


def _persist(result: dict[str, Any], output: Path) -> None:
    save_run(result, output)
    archive_path = archive_run(result, output.parent / "runs")
    report = render_report(result)
    output.with_suffix(".md").write_text(report, encoding="utf-8")
    archive_path.with_suffix(".md").write_text(report, encoding="utf-8")
    print(f"JSON: {output}")
    print(f"Archive: {archive_path}")
    print(f"Report: {output.with_suffix('.md')}")


def _persist_code(result: dict[str, Any], output: Path) -> None:
    from metaforge.reporting.code_markdown import render_code_report

    save_run(result, output)
    archive_path = archive_run(result, output.parent / "runs")
    report = render_code_report(result)
    output.with_suffix(".md").write_text(report, encoding="utf-8")
    archive_path.with_suffix(".md").write_text(report, encoding="utf-8")
    print(f"JSON: {output}")
    print(f"Archive: {archive_path}")
    print(f"Report: {output.with_suffix('.md')}")


def _dataset(seed: int, size: int) -> list[int]:
    rng = random.Random(seed)
    return [rng.randint(-10_000, 10_000) for _ in range(size)]


def _candidate_signature(result: dict[str, Any]) -> list[tuple[str, bool]]:
    return [
        (item["fingerprint"], item["valid"])
        for generation in result["generations"]
        for item in generation["evaluations"]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(prog="metaforge")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a controlled integer sorting experiment")
    run.add_argument("--size", type=int, default=200)
    run.add_argument("--generations", type=int, default=3)
    run.add_argument("--seed", type=int, default=7)
    run.add_argument("--repeats", type=int, default=3)
    run.add_argument("--population-size", type=int, default=15)
    run.add_argument("--max-evaluations", type=int, default=64)
    run.add_argument("--max-workers", type=int, default=2)
    run.add_argument("--global-timeout", type=float, default=120.0)
    run.add_argument("--llm-proposals", action="store_true", help="use an optional OpenAI Agents SDK candidate designer")
    run.add_argument("--llm-model", default=os.environ.get("METAFORGE_AGENT_MODEL"))
    run.add_argument("--llm-timeout", type=float, default=25.0)
    run.add_argument("--output", type=Path, default=Path(".artifacts/latest.json"))

    code_evolve = sub.add_parser("code-evolve", help="generate, review, and sandbox-evaluate Python algorithm source")
    code_evolve.add_argument("--size", type=int, default=5000)
    code_evolve.add_argument("--seed", type=int, default=7)
    code_evolve.add_argument("--population-size", type=int, default=2)
    code_evolve.add_argument("--generations", type=int, default=2)
    code_evolve.add_argument("--repeats", type=int, default=3)
    code_evolve.add_argument("--max-agent-calls", type=int, default=8)
    code_evolve.add_argument("--global-timeout", type=float, default=180.0)
    code_evolve.add_argument("--agent-timeout", type=float, default=30.0)
    code_evolve.add_argument("--llm-model", default=os.environ.get("METAFORGE_AGENT_MODEL"))
    code_evolve.add_argument("--sandbox-runtime", choices=["podman", "docker"], default=os.environ.get("METAFORGE_SANDBOX_RUNTIME", "podman"))
    code_evolve.add_argument("--sandbox-image", default=os.environ.get("METAFORGE_SANDBOX_IMAGE"))
    code_evolve.add_argument("--sandbox-timeout", type=float, default=10.0)
    code_evolve.add_argument("--output", type=Path, default=Path(".artifacts/code-evolution.json"))

    backend_check = sub.add_parser("backend-check", help="compare correctness across Python and compiled backends")
    backend_check.add_argument("--backend", choices=["rust", "c", "both"], default="both")
    backend_check.add_argument("--strategy", choices=["builtin", "heapsort", "shellsort"], default="heapsort")
    backend_check.add_argument("--size", type=int, default=1000)
    backend_check.add_argument("--seed", type=int, default=7)
    backend_check.add_argument("--repeats", type=int, default=3)
    backend_check.add_argument("--timeout", type=float, default=10.0)
    backend_check.add_argument("--output", type=Path, default=Path(".artifacts/backend-check.json"))

    sandbox_check = sub.add_parser("sandbox-check", help="verify a rootless runtime and pinned sandbox image without executing code")
    sandbox_check.add_argument("--runtime", choices=["podman", "docker"], default=os.environ.get("METAFORGE_SANDBOX_RUNTIME", "podman"))
    sandbox_check.add_argument("--image", default=os.environ.get("METAFORGE_SANDBOX_IMAGE"))
    sandbox_smoke = sub.add_parser("sandbox-smoke", help="run a fixed trusted isolation probe inside the configured sandbox")
    sandbox_smoke.add_argument("--runtime", choices=["podman", "docker"], default=os.environ.get("METAFORGE_SANDBOX_RUNTIME", "podman"))
    sandbox_smoke.add_argument("--image", default=os.environ.get("METAFORGE_SANDBOX_IMAGE"))

    replay = sub.add_parser("replay", help="re-run an archived experiment using its stored dataset")
    replay.add_argument("--run", type=Path, required=True, help="path to a prior experiment JSON")
    replay.add_argument("--output", type=Path, default=Path(".artifacts/replay-latest.json"))

    args = parser.parse_args()

    if args.command == "run":
        if not 1 <= args.size <= 100_000:
            parser.error("--size must be 1..100000")
        if not 1 <= args.generations <= 20:
            parser.error("--generations must be 1..20")
        candidate_designer = None
        if args.llm_proposals:
            if not os.environ.get("OPENAI_API_KEY"):
                parser.error("--llm-proposals requires OPENAI_API_KEY in the environment")
            if not args.llm_model:
                parser.error("set --llm-model or METAFORGE_AGENT_MODEL when using --llm-proposals")
            try:
                from metaforge.agents.openai_adapter import OpenAICandidateDesigner
                candidate_designer = OpenAICandidateDesigner(args.llm_model, args.llm_timeout)
            except (ImportError, ValueError) as exc:
                parser.error(str(exc))
        dataset = _dataset(args.seed, args.size)
        engine = EvolutionEngine(
            seed=args.seed,
            repeats=args.repeats,
            population_size=args.population_size,
            max_evaluations=args.max_evaluations,
            max_workers=args.max_workers,
            global_timeout_seconds=args.global_timeout,
            candidate_designer=candidate_designer,
        )
        result = engine.run(dataset, args.generations)
        result["replay"] = {
            "dataset": dataset,
            "dataset_storage": "embedded_synthetic_cli_dataset",
        }
        _persist(result, args.output)
        if result["best_candidate"]:
            print("Best:", result["best_candidate"]["candidate"])
        return 0

    if args.command == "code-evolve":
        if not os.environ.get("OPENAI_API_KEY"):
            parser.error("code-evolve requires OPENAI_API_KEY in the local environment")
        if not args.llm_model:
            parser.error("set --llm-model or METAFORGE_AGENT_MODEL")
        if not args.sandbox_image:
            parser.error("set --sandbox-image or METAFORGE_SANDBOX_IMAGE to a local sha256-pinned image")
        if not 1 <= args.size <= 100_000:
            parser.error("--size must be 1..100000")
        try:
            from metaforge.agents.code_designer import OpenAICodeDesigner
            from metaforge.evolution.code_engine import CodeEvolutionEngine
            from metaforge.sandbox.policy import SandboxPolicy
            from metaforge.sandbox.runner import SandboxRunner
            designer = OpenAICodeDesigner(args.llm_model, args.agent_timeout)
            sandbox = SandboxRunner(SandboxPolicy(
                image=args.sandbox_image,
                runtime=args.sandbox_runtime,
                timeout_seconds=args.sandbox_timeout,
            ))
            engine = CodeEvolutionEngine(
                designer,
                sandbox,
                seed=args.seed,
                population_size=args.population_size,
                generations=args.generations,
                repeats=args.repeats,
                max_agent_calls=args.max_agent_calls,
                global_timeout_seconds=args.global_timeout,
            )
            dataset = _dataset(args.seed, args.size)
            result = engine.run(dataset)
        except Exception as exc:
            parser.error(f"code-evolve stopped safely: {type(exc).__name__}: {exc}")
        result["replay"] = {
            "dataset": dataset,
            "dataset_storage": "embedded_synthetic_cli_dataset",
        }
        _persist_code(result, args.output)
        if result["best_candidate"]:
            best = result["best_candidate"]
            print("Best fingerprint:", best["candidate"]["fingerprint"])
            print("Median algorithm seconds:", best["median_algorithm_seconds"])
            return 0
        return 1

    if args.command in {"sandbox-check", "sandbox-smoke"}:
        if not args.image:
            parser.error("set --image or METAFORGE_SANDBOX_IMAGE to a locally available sha256-pinned image")
        try:
            from metaforge.sandbox.policy import SandboxPolicy
            from metaforge.sandbox.runner import SandboxRunner
            sandbox_runner = SandboxRunner(SandboxPolicy(image=args.image, runtime=args.runtime))
            preparation = sandbox_runner.prepare()
        except Exception as exc:
            parser.error(f"sandbox preflight failed: {type(exc).__name__}")
        if args.command == "sandbox-check":
            print({
                "runtime": preparation.runtime,
                "runtime_version": preparation.runtime_version,
                "image": preparation.image,
                "rootless": preparation.rootless,
                "code_executed": False,
            })
            return 0
        result = sandbox_runner.smoke()
        print({
            "runtime": preparation.runtime,
            "image": preparation.image,
            "sandbox_smoke_passed": result.success,
            "measurement_scope": result.measurement_scope,
            "algorithm_samples_seconds": result.algorithm_samples_seconds,
            "error": result.error,
        })
        return 0 if result.success else 1

    if args.command == "backend-check":
        if not 1 <= args.size <= 100_000 or not 1 <= args.repeats <= 20 or args.timeout <= 0:
            parser.error("backend-check size, repeats, or timeout is outside allowed bounds")
        dataset = _dataset(args.seed, args.size)
        candidate = Candidate(args.strategy)
        expected = sorted(dataset)
        backends = [PythonBackend()]
        if args.backend in {"rust", "both"}:
            from metaforge.backends.rust import RustBackend
            backends.append(RustBackend(timeout_seconds=args.timeout))
        if args.backend in {"c", "both"}:
            from metaforge.backends.c import CBackend
            backends.append(CBackend(timeout_seconds=args.timeout))
        results = []
        for backend in backends:
            try:
                preparation = backend.prepare()
                runs = backend.run_repeated(candidate, dataset, args.repeats, args.timeout)
                correct = len(runs) == args.repeats and all(
                    item.success and item.output == expected for item in runs
                )
                if not correct:
                    results.append({
                        "backend": backend.name,
                        "version": preparation.version,
                        "compile_seconds": preparation.compile_seconds,
                        "correctness_passed": False,
                        "error": next((item.error for item in runs if not item.success), "Output mismatch"),
                    })
                    continue
                times = [item.elapsed_seconds for item in runs if item.elapsed_seconds is not None]
                peaks = [item.peak_bytes for item in runs if item.peak_bytes is not None]
                results.append({
                    "backend": backend.name,
                    "version": preparation.version,
                    "compile_seconds": preparation.compile_seconds,
                    "source_sha256": preparation.source_sha256,
                    "artifact_sha256": preparation.artifact_sha256,
                    "build_flags": list(preparation.build_flags),
                    "correctness_passed": True,
                    "samples_seconds": times,
                    "median_algorithm_seconds": statistics.median(times),
                    "median_peak_bytes": statistics.median(peaks) if peaks else None,
                    "measurement_scope": runs[0].measurement_scope,
                })
            except Exception as exc:
                results.append({
                    "backend": backend.name,
                    "correctness_passed": False,
                    "error": f"{type(exc).__name__}: backend preparation or execution failed",
                })
        report = {
            "schema_version": "1.0",
            "run_id": uuid4().hex,
            "problem": "integer_sort",
            "strategy": args.strategy,
            "dataset_size": len(dataset),
            "dataset_sha256": hashlib.sha256(json.dumps(dataset, separators=(",", ":")).encode()).hexdigest(),
            "seed": args.seed,
            "environment": runtime_metadata(),
            "results": results,
            "comparison_note": "Compile time is separate. Runtime and memory metrics have backend-specific scopes and must not be treated as directly comparable.",
        }
        save_run(report, args.output)
        for item in results:
            print(item)
        print(f"Report: {args.output}")
        return 0 if all(item.get("correctness_passed") for item in results) else 1

    try:
        original = json.loads(args.run.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read experiment JSON: {exc}")
    replay_data = original.get("replay", {})
    dataset = replay_data.get("dataset") if isinstance(replay_data, dict) else None
    if not isinstance(dataset, list) or any(type(value) is not int for value in dataset):
        parser.error("the selected run does not contain a valid embedded replay dataset")
    digest = hashlib.sha256(json.dumps(dataset, separators=(",", ":")).encode("utf-8")).hexdigest()
    if digest != original.get("dataset_sha256"):
        parser.error("embedded dataset hash does not match the original experiment")
    config = original.get("configuration", {})
    required = ("generations_requested", "population_size", "repeats",
                "max_evaluations", "max_workers", "global_timeout_seconds")
    if any(key not in config for key in required):
        parser.error("the selected run is missing required configuration metadata")
    engine = EvolutionEngine(
        seed=original["seed"],
        repeats=config["repeats"],
        population_size=config["population_size"],
        max_evaluations=config["max_evaluations"],
        max_workers=config["max_workers"],
        global_timeout_seconds=config["global_timeout_seconds"],
    )
    stored_generations = original.get("generations", [])
    has_schedule = (
        bool(stored_generations)
        and all(isinstance(item, dict) and isinstance(item.get("candidate_population"), list)
                for item in stored_generations)
    )
    replay_generations = len(stored_generations) if has_schedule else config["generations_requested"]
    candidate_schedule = (
        [item["candidate_population"] for item in stored_generations] if has_schedule else None
    )
    result = engine.run(
        dataset,
        replay_generations,
        candidate_schedule=candidate_schedule,
    )
    result["replay"] = replay_data
    result["replay_of"] = {
        "run_id": original.get("run_id"),
        "dataset_sha256": digest,
        "candidate_schedule_replayed": has_schedule,
        "generations_replayed": replay_generations,
        "generations_requested_original": config["generations_requested"],
        "candidate_sequence_matches": _candidate_signature(original) == _candidate_signature(result),
        "performance_comparison": "Timing values are new measurements and are not expected to match.",
    }
    _persist(result, args.output)
    print("Candidate sequence matches:", result["replay_of"]["candidate_sequence_matches"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
