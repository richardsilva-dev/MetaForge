from __future__ import annotations

from typing import Any


def render_report(result: dict[str, Any]) -> str:
    environment = result.get("environment", {})
    configuration = result.get("configuration", {})
    budget = result.get("budget", {})
    lines = [
        "# MetaForge experiment report",
        "",
        f"- Run ID: `{result.get('run_id', 'unknown')}`",
        f"- Problem: {result['problem']}",
        f"- Dataset size: {result['dataset_size']}",
        f"- Dataset SHA-256: `{result.get('dataset_sha256', 'unavailable')}`",
        f"- Seed: {result.get('seed', 'unknown')}",
        f"- Started: {result.get('started_at', 'unknown')}",
        f"- Finished: {result.get('finished_at', 'unknown')}",
        "",
        "## Environment",
        "",
        f"- MetaForge: {environment.get('metaforge_version', 'unknown')}",
        f"- Python: {environment.get('python_version', 'unknown')} ({environment.get('python_implementation', 'unknown')})",
        f"- Platform: {environment.get('platform', 'unknown')}",
        f"- CPU: {environment.get('processor') or environment.get('machine', 'unknown')}",
        f"- CPU count: {environment.get('cpu_count', 'unknown')}",
        f"- Source revision: `{environment.get('source_revision') or 'not supplied'}`",
        "",
        "## Configuration",
        "",
        f"- Generations requested: {configuration.get('generations_requested', len(result['generations']))}",
        f"- Population size: {configuration.get('population_size', 'unknown')}",
        f"- Repeats: {configuration.get('repeats', 'unknown')}",
        f"- Maximum evaluations: {budget.get('max_evaluations', 'unknown')}",
        f"- Evaluations used: {budget.get('evaluations_used', 'unknown')}",
        f"- Parallel workers: {budget.get('max_workers', 'unknown')}",
        f"- Stop reason: {budget.get('stop_reason', 'unknown')}",
        "",
    ]
    best = result.get("best_candidate")
    if best:
        lineage = best.get("lineage", {})
        lines += [
            "## Best valid candidate",
            "",
            f"- Strategy: {best['candidate']['strategy']}",
            f"- Pivot: {best['candidate']['pivot']}",
            f"- Fingerprint: `{best['fingerprint']}`",
            f"- Median time: {best['median_seconds']:.8f} s",
            f"- Median traced peak: {best['median_peak_bytes']} bytes",
            f"- Origin: {lineage.get('origin', 'unknown')}",
            f"- Operator: {lineage.get('operator', 'unknown')}",
            f"- Parent: `{lineage.get('parent_fingerprint') or 'none'}`",
            "",
        ]
    else:
        lines += ["No candidate passed correctness validation.", ""]
    lines += [
        "## Generations",
        "",
        "| Generation | Valid | Unique |",
        "|---:|---:|---:|",
    ]
    for generation in result["generations"]:
        lines.append(
            f"| {generation['generation']} | {generation['valid_count']} | {generation['population_diversity']} |"
        )
    lines += [
        "",
        "Benchmark values are exploratory and hardware-dependent.",
        "Candidate sequence replay can be deterministic; timing replay is not expected to be identical.",
        "",
    ]
    return "\n".join(lines)
