# Goal Execution Benchmark

This benchmark scaffolding is for comparing a live baseline/ablation run
against the enhanced goal-oriented execution build.

## Task Set

Canonical runnable task definitions live in:

- `benchmarks/goal_execution/tasks.json`

The first three categories are:

1. Multi-step retrieval and download
2. Multi-file workspace analysis
3. Failure recovery and rerouting

## Per-Run Record Schema

Store one JSON object per evaluated task with these fields:

- `task_id`
- `variant`
- `completed`
- `tool_call_count`
- `repeated_tool_calls`
- `had_failure`
- `recovered_after_failure`
- `step_count`
- `replan_count`
- `latency_ms`
- `prompt_tokens`
- `completion_tokens`

Recommended artifact bundle per run:

- `benchmark_records_<variant>.jsonl`
- `benchmark_traces_<variant>.jsonl`
- one human-readable report generated from those JSONL files

Collector scripts live in:

- `benchmarks/goal_execution/collect_live_benchmark.py`
- `benchmarks/goal_execution/compare_live_benchmarks.py`

## Aggregation

Use `nanobot.utils.goal_eval.aggregate_goal_eval()` to compute:

- task completion rate
- repeated tool-call rate
- failure recovery rate
- average step count
- average replan count
- average latency
- average total tokens

## Trace Source

Recommended evidence sources:

- session history
- `memory/execution_memory.jsonl`
- WebSocket turn traces

The benchmark collector should derive tool counts and failures from WebSocket
`message.tool_events` frames, not from ad hoc hand-written enrichment.

## Baseline / Ablation Mode

For a minimal-intrusion live baseline, restart the gateway with:

- `NANOBOT_ENABLE_GOAL_RUNTIME_CONTEXT=0`
- `NANOBOT_ENABLE_EXECUTION_RECALL=0`
- `NANOBOT_ENABLE_AUTO_REPLAN=0`

This disables the benchmarked integrations without requiring manual source
comment-outs between runs.

When possible, keep the raw trace and the normalized benchmark record together
so qualitative debugging and quantitative aggregation stay aligned.
