"""Compare live benchmark JSONL artifacts and emit a human-readable report."""

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


goal_eval = _load_module("goal_eval", REPO_ROOT / "nanobot" / "utils" / "goal_eval.py")
goal_benchmark = _load_module("goal_benchmark", REPO_ROOT / "nanobot" / "utils" / "goal_benchmark.py")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--enhanced",
        default=str(PROJECT_ROOT / "benchmark_records_enhanced.jsonl"),
        help="Live enhanced benchmark JSONL.",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="Optional live baseline/ablation benchmark JSONL.",
    )
    parser.add_argument(
        "--estimated-baseline",
        default="",
        help="Optional estimated baseline JSONL kept separate from live evidence.",
    )
    parser.add_argument(
        "--output-report",
        default=str(PROJECT_ROOT / "benchmark_report.md"),
        help="Markdown report output path.",
    )
    return parser.parse_args()


def _load_required_records(path: str) -> list[dict[str, Any]]:
    rows = goal_benchmark.load_jsonl_records(path)
    if not rows:
        raise SystemExit(f"No benchmark records found in {path}")
    return rows


def _render_metric_row(summary: dict[str, dict[str, float | int]], left: str, right: str, key: str, label: str, fmt: str) -> str:
    left_val = summary.get(left, {}).get(key, 0)
    right_val = summary.get(right, {}).get(key, 0)
    if fmt == "pct":
        left_str = f"{left_val:.1%}"
        right_str = f"{right_val:.1%}"
        delta = right_val - left_val
        delta_str = f"{delta:+.1%}"
    elif fmt == "float":
        left_str = f"{left_val:.1f}"
        right_str = f"{right_val:.1f}"
        delta = right_val - left_val
        delta_str = f"{delta:+.1f}"
    else:
        left_str = str(int(left_val))
        right_str = str(int(right_val))
        delta = int(right_val) - int(left_val)
        delta_str = f"{delta:+d}"
    return f"| {label} | {left_str} | {right_str} | {delta_str} |"


def _comparison_table(summary: dict[str, dict[str, float | int]], left: str, right: str, *, left_label: str, right_label: str) -> list[str]:
    lines = [
        f"| Metric | {left_label} | {right_label} | Delta |",
        "|---|---:|---:|---:|",
    ]
    metrics = [
        ("task_count", "Tasks Run", "int"),
        ("completion_rate", "Completion Rate", "pct"),
        ("repeated_tool_call_rate", "Repeated Tool Call Rate", "pct"),
        ("failure_recovery_rate", "Failure Recovery Rate", "pct"),
        ("average_step_count", "Avg Steps per Task", "float"),
        ("average_replan_count", "Avg Replans per Task", "float"),
        ("average_latency_ms", "Avg Latency (ms)", "int"),
    ]
    for key, label, fmt in metrics:
        lines.append(_render_metric_row(summary, left, right, key, label, fmt))
    return lines


def _variant_name(rows: list[dict[str, Any]]) -> str:
    variants = {str(row.get("variant") or "").strip() for row in rows}
    variants.discard("")
    return sorted(variants)[0] if len(variants) == 1 else "mixed"


def _summary_block(rows: list[dict[str, Any]], *, title: str) -> list[str]:
    variant = _variant_name(rows)
    summary = goal_eval.aggregate_goal_eval(rows)
    return [
        f"## {title}",
        "",
        f"- Variant label: `{variant}`",
        f"- Evidence type: `{rows[0].get('evidence', 'unknown')}`",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, indent=2),
        "```",
        "",
    ]


def _write_report(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    args = _parse_args()
    enhanced_rows = _load_required_records(args.enhanced)
    report_lines = [
        "# Goal Execution Benchmark Report",
        "",
        f"- Enhanced source: `{args.enhanced}`",
    ]

    baseline_rows: list[dict[str, Any]] = []
    estimated_rows: list[dict[str, Any]] = []
    if args.baseline:
        baseline_rows = _load_required_records(args.baseline)
        report_lines.append(f"- Baseline source: `{args.baseline}`")
    if args.estimated_baseline:
        estimated_rows = _load_required_records(args.estimated_baseline)
        report_lines.append(f"- Estimated baseline source: `{args.estimated_baseline}`")
    report_lines.append("")

    report_lines.extend(_summary_block(enhanced_rows, title="Enhanced Live Summary"))

    if baseline_rows:
        report_lines.extend(_summary_block(baseline_rows, title="Baseline Live Summary"))
        summary = goal_eval.aggregate_goal_eval(enhanced_rows + baseline_rows)
        left = _variant_name(baseline_rows)
        right = _variant_name(enhanced_rows)
        report_lines.extend([
            "## Live A/B Comparison",
            "",
            *_comparison_table(summary, left, right, left_label=left, right_label=right),
            "",
            "Only live JSONL artifacts are compared in the table above.",
            "",
        ])
    elif estimated_rows:
        report_lines.extend(_summary_block(estimated_rows, title="Estimated Baseline Summary"))
        report_lines.extend([
            "## Evidence Note",
            "",
            "No live baseline JSONL was provided, so no mixed delta table is emitted.",
            "The estimated baseline is kept separate from live enhanced metrics.",
            "",
        ])

    _write_report(Path(args.output_report), report_lines)
    print("\n".join(report_lines))
    print(f"Markdown report written to: {args.output_report}")


if __name__ == "__main__":
    main()
