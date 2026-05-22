# Nanobot Long-Horizon Agent

Lightweight long-horizon execution enhancements for [HKUDS/nanobot](https://github.com/HKUDS/nanobot), focused on structured goal tracking, episodic execution memory, retrieval-aware recall, and failure-aware replanning.

This repository contains the **core implementation delta** and the **focused benchmark/test suite** behind the project. Private development notes, resume materials, local configs, and runtime artifacts are intentionally excluded.

## Overview

Modern coding and tool-use agents often perform well on short tasks but become unstable on long-horizon objectives:

- goal progress is hard to track across turns
- intermediate facts are not reused effectively
- failures can lead to repeated tool calls or stagnant execution
- frontend progress visibility and runtime state can drift apart

This project extends `nanobot` with a compact long-task execution stack that keeps the original codebase lightweight while making multi-step objectives more observable and recoverable.

## Key Features

### 1. Structured goal state

The agent maintains an explicit goal runtime state with stable fields such as:

- `goal_id`
- `plan_steps`
- `current_step`
- `completed_steps`
- `progress_summary`
- `blocked_reason`
- `recent_failures`
- `verified_facts`
- `replan_count`

These fields are shared between backend runtime context and WebUI state so that planning, execution, and visualization stay aligned.

### 2. Episodic execution memory

Task execution events are stored as goal-focused JSONL memory rather than mixed into generic conversational memory. The memory stream tracks events such as:

- `goal_started`
- `progress_update`
- `failure`
- `replan`
- `goal_completed`

Each record keeps the minimum evidence needed for later recall, including goal identifier, step, summary, status, verified facts, and timestamp.

### 3. Retrieval-aware recall

Before each decision turn, the agent recalls the most relevant execution-memory snippets for the active goal. This improves continuity on multi-step tasks without flooding the prompt with full history.

### 4. Failure-aware replanning

The agent can automatically trigger replanning when it detects patterns such as:

- repeated failures on the same path
- consecutive failed turns
- blocked state with recent failures
- progress plateau on the same step

The replanning flow updates goal state, records the failure cause, and rewrites the active plan through the existing long-task tool path instead of introducing a separate planner subsystem.

### 5. Focused benchmark pipeline

The repository includes a small live benchmark pipeline for comparing enhanced and baseline behavior on long-horizon tasks, with metrics covering completion, repeated tool calls, recovery, step count, and latency.

## Repository Layout

```text
nanobot/
  agent/
  session/
  utils/
webui/
  src/
benchmarks/
  goal_execution/
tests/
```

- `nanobot/`: core backend changes for goal state, execution memory, loop integration, and evaluation utilities
- `webui/`: focused frontend changes for goal-aware UI behavior
- `benchmarks/goal_execution/`: live benchmark collection and comparison scripts
- `tests/`: focused regression tests for the enhancement path

## Included Core Files

This repository is intentionally narrow. It contains only the public-facing implementation slices needed to show:

- the backend long-horizon agent changes
- the WebUI integration points
- the benchmark scripts and task set
- the focused regression tests

It does **not** include:

- private resume files
- private project records or handoff notes
- local API keys or config files
- local benchmark outputs and traces
- unrelated upstream files

## How To Use

This repository is best treated as an **extracted implementation package** built on top of `nanobot`, not a full standalone replacement for the upstream project.

### Option A: Read and study the implementation

Use this repository as a compact reference for the long-horizon execution enhancement:

- inspect the backend goal-state and memory changes
- inspect the WebUI state wiring
- inspect the benchmark scripts and tests

### Option B: Apply the enhancement on top of upstream nanobot

1. Clone the upstream project:

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
```

2. Copy the corresponding files from this repository into the upstream checkout.

3. Install dependencies and run the upstream project as usual:

```bash
pip install -e .
cd webui
npm install
```

4. Run the focused tests from the upstream checkout:

```bash
pytest tests/agent/test_context_builder.py \
  tests/agent/test_execution_memory.py \
  tests/agent/test_loop_auto_replan.py \
  tests/agent/tools/test_long_task.py \
  tests/session/test_goal_state.py \
  tests/utils/test_goal_benchmark.py \
  tests/utils/test_goal_eval.py
```

## Benchmark

The benchmark task set is located at:

```text
benchmarks/goal_execution/tasks.json
```

It currently emphasizes three safe long-horizon task types:

- multi-step retrieval and download
- multi-file inspection and organization
- failure recovery and route switching

### Collect live records

```bash
python benchmarks/goal_execution/collect_live_benchmark.py
```

### Compare baseline and enhanced runs

```bash
python benchmarks/goal_execution/compare_live_benchmarks.py
```

## Results

Using the current 3-task live benchmark with a strong model configuration, the enhanced build preserved task completion while reducing redundant tool behavior:

| Metric | Baseline | Enhanced |
| --- | ---: | ---: |
| Completion rate | 100% | 100% |
| Repeated tool-call rate | 3.8% | 0.0% |
| Average latency | 71,018 ms | 60,332 ms |

### Interpretation

- On this benchmark slice, the main gain is **execution efficiency and lower repetition**, not a completion-rate jump.
- Under stronger models, long-horizon enhancements may show up first as fewer redundant actions, cleaner recovery behavior, and better observability.

## Testing

The focused test suite covers:

- goal-state state propagation
- execution-memory recording and recall
- long-task tool behavior
- auto-replanning heuristics
- benchmark metrics and reporting
- WebUI goal-state integration

## Acknowledgement

This project is built on top of the excellent [HKUDS/nanobot](https://github.com/HKUDS/nanobot) open-source agent framework.

If you use this extracted enhancement package in your own research or engineering work, please also credit the upstream `nanobot` project.
