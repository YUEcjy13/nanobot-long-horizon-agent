"""Compare reflective benchmark JSONL artifacts and emit a markdown report."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

THIS_FILE = Path(__file__).resolve()
BENCH_DIR = THIS_FILE.parent
REPO_ROOT = BENCH_DIR.parent.parent
PROJECT_ROOT = REPO_ROOT.parent


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


goal_benchmark = _load_module("goal_benchmark", REPO_ROOT / "nanobot" / "utils" / "goal_benchmark.py")
trajectory_eval = _load_module("trajectory_eval", REPO_ROOT / "nanobot" / "utils" / "trajectory_eval.py")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", nargs="+", required=True, help="One or more reflective benchmark JSONL files.")
    parser.add_argument(
        "--output-report",
        default=str(PROJECT_ROOT / "outputs" / "reflective_benchmark" / "comparison.md"),
        help="Markdown comparison report path.",
    )
    return parser.parse_args()


def _load_records(path: str) -> list[dict[str, Any]]:
    rows = goal_benchmark.load_jsonl_records(path)
    if not rows:
        raise SystemExit(f"No benchmark records found in {path}")
    return rows


def _render_summary(summary: dict[str, dict[str, float | int]]) -> list[str]:
    lines = [
        "| Variant | Task Success | Step Completion | Repeated Tool Call | Repeated Failed Tool Call | Replan Triggers | Successful Replan | Avg Tool Calls | Avg Turns | Avg Latency (ms) | Trajectory Completeness |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, metrics in summary.items():
        lines.append(
            "| {variant} | {task_success_rate:.1%} | {step_completion_rate:.1%} | {repeated_tool_call_rate:.1%} | {repeated_failed_tool_call_rate:.2f} | {replan_trigger_count} | {successful_replan_rate:.1%} | {avg_tool_calls:.2f} | {avg_turns:.2f} | {avg_latency_ms:.0f} | {trajectory_completeness:.1%} |".format(
                variant=variant,
                **metrics,
            )
        )
    return lines


def main() -> None:
    args = _parse_args()
    rows: list[dict[str, Any]] = []
    for path in args.records:
        rows.extend(_load_records(path))

    summary = trajectory_eval.aggregate_reflective_eval(rows)
    lines = [
        "# Reflective Benchmark Comparison",
        "",
        "## Aggregated Metrics",
        "",
        *_render_summary(summary),
        "",
        "## Raw Summary",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    output_path = Path(args.output_report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"Comparison report written to: {output_path}")


if __name__ == "__main__":
    main()
