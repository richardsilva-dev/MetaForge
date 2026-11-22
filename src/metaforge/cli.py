from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from metaforge.evolution.engine import EvolutionEngine
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
    run.add_argument("--output", type=Path, default=Path(".artifacts/latest.json"))

    replay = sub.add_parser("replay", help="re-run an archived experiment using its stored dataset")
    replay.add_argument("--run", type=Path, required=True, help="path to a prior experiment JSON")
    replay.add_argument("--output", type=Path, default=Path(".artifacts/replay-latest.json"))

    args = parser.parse_args()

    if args.command == "run":
        if not 1 <= args.size <= 100_000:
            parser.error("--size must be 1..100000")
        if not 1 <= args.generations <= 20:
            parser.error("--generations must be 1..20")
        dataset = _dataset(args.seed, args.size)
        engine = EvolutionEngine(
            seed=args.seed,
            repeats=args.repeats,
            population_size=args.population_size,
            max_evaluations=args.max_evaluations,
            max_workers=args.max_workers,
            global_timeout_seconds=args.global_timeout,
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
