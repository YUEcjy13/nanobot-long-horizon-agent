# Nanobot Long-Horizon Agent

A runnable fork of [HKUDS/nanobot](https://github.com/HKUDS/nanobot) with long-horizon execution enhancements for goal tracking, execution memory, retrieval-aware recall, and failure-aware replanning.

This repository keeps the full `nanobot` runtime skeleton so it can be cloned and run directly, while integrating the project-specific improvements developed for long-task agent execution.

## What This Fork Adds

Compared with upstream `nanobot`, this fork adds a compact execution layer for long-horizon tasks:

- **Structured goal state**
  - Stable fields such as `goal_id`, `plan_steps`, `current_step`, `completed_steps`, `progress_summary`, `blocked_reason`, `recent_failures`, `verified_facts`, and `replan_count`
  - Shared between backend runtime context and WebUI state

- **Episodic execution memory**
  - Dedicated task-focused JSONL memory for execution traces
  - Records `goal_started`, `progress_update`, `failure`, `replan`, and `goal_completed`

- **Retrieval-aware recall**
  - Recalls high-value execution-memory snippets before each decision turn
  - Improves continuity across long multi-step tasks without overloading the prompt

- **Failure-aware replanning**
  - Triggers replanning when the agent encounters repeated failures, blocked progress, or step plateaus
  - Reuses the existing long-task tool path instead of adding a heavyweight planner subsystem

- **Live benchmark pipeline**
  - Includes runnable scripts for baseline vs enhanced comparisons
  - Covers completion, repeated tool calls, recovery behavior, replans, and latency

## Repository Layout

```text
nanobot/
webui/
benchmarks/
docs/
tests/
```

- `nanobot/`: backend agent framework and long-horizon execution changes
- `webui/`: full WebUI frontend
- `benchmarks/goal_execution/`: benchmark collection and comparison scripts
- `docs/goal-execution-benchmark.md`: benchmark methodology
- `tests/`: upstream tests plus focused regression coverage for the new execution path

## Key Modified Areas

Core enhancement logic lives mainly in:

- `nanobot/agent/context.py`
- `nanobot/agent/execution_memory.py`
- `nanobot/agent/loop.py`
- `nanobot/agent/runner.py`
- `nanobot/agent/tools/long_task.py`
- `nanobot/session/goal_state.py`
- `nanobot/providers/base.py`
- `nanobot/providers/openai_compat_provider.py`
- `nanobot/utils/goal_benchmark.py`
- `nanobot/utils/goal_eval.py`
- `webui/src/components/thread/ThreadComposer.tsx`
- `webui/src/lib/types.ts`

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/YUEcjy13/nanobot-long-horizon-agent.git
cd nanobot-long-horizon-agent
```

### 2. Create a Python environment and install backend dependencies

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

### 3. Install WebUI dependencies

```bash
cd webui
npm install
cd ..
```

## Quick Start With DeepSeek + WebUI

### 1. Create a local config file

Create `local.config.json` at the repository root:

```json
{
  "providers": {
    "deepseek": {
      "apiKey": "${DEEPSEEK_API_KEY}"
    }
  },
  "agents": {
    "defaults": {
      "provider": "deepseek",
      "model": "deepseek-v4-pro"
    }
  },
  "channels": {
    "websocket": {
      "host": "127.0.0.1",
      "port": 8765,
      "websocketRequiresToken": false
    }
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 18790
  }
}
```

### 2. Export your API key

```bash
export DEEPSEEK_API_KEY="your_deepseek_api_key"
```

### 3. Start the gateway

```bash
source .venv/bin/activate
nanobot gateway --config ./local.config.json
```

### 4. Start the WebUI in another terminal

```bash
cd webui
npm run dev
```

Then open:

```text
http://127.0.0.1:5173
```

## Runtime Notes

- This fork has been validated locally with the WebSocket gateway + WebUI flow.
- The DeepSeek path depends on your own valid API key and model access.
- `nanobot.local.config.json` and other personal local config files are intentionally not tracked in this repository.

## Benchmark

The benchmark task set and scripts live in:

- `benchmarks/goal_execution/tasks.json`
- `benchmarks/goal_execution/collect_live_benchmark.py`
- `benchmarks/goal_execution/compare_live_benchmarks.py`
- `docs/goal-execution-benchmark.md`

### Example benchmark workflow

Run the enhanced build:

```bash
python benchmarks/goal_execution/collect_live_benchmark.py \
  --variant enhanced \
  --output ./benchmark_records_enhanced.jsonl \
  --trace-output ./benchmark_traces_enhanced.jsonl
```

Run a baseline/ablation build by restarting the gateway with:

```bash
export NANOBOT_ENABLE_GOAL_RUNTIME_CONTEXT=0
export NANOBOT_ENABLE_EXECUTION_RECALL=0
export NANOBOT_ENABLE_AUTO_REPLAN=0
```

Then collect baseline records:

```bash
python benchmarks/goal_execution/collect_live_benchmark.py \
  --variant baseline \
  --output ./benchmark_records_baseline.jsonl \
  --trace-output ./benchmark_traces_baseline.jsonl
```

Compare runs:

```bash
python benchmarks/goal_execution/compare_live_benchmarks.py \
  --enhanced ./benchmark_records_enhanced.jsonl \
  --baseline ./benchmark_records_baseline.jsonl
```

## Live Benchmark Snapshot

On the current 3-task safe benchmark slice with a strong model configuration:

| Metric | Baseline | Enhanced |
| --- | ---: | ---: |
| Completion rate | 100% | 100% |
| Repeated tool-call rate | 3.8% | 0.0% |
| Average latency | 71,018 ms | 60,332 ms |

Interpretation:

- In this setting, the main observed gain is **higher execution efficiency** and **less redundant tool use**
- With stronger frontier models, long-horizon enhancements may show up first as stability and efficiency gains rather than completion-rate gains

## Focused Tests

The long-horizon execution path is covered by focused regression tests such as:

```bash
pytest tests/agent/test_context_builder.py \
  tests/agent/test_execution_memory.py \
  tests/agent/test_loop_auto_replan.py \
  tests/agent/tools/test_long_task.py \
  tests/session/test_goal_state.py \
  tests/providers/test_provider_input_sanitization.py \
  tests/utils/test_goal_benchmark.py \
  tests/utils/test_goal_eval.py
```

## Acknowledgement

This project is built on top of the excellent open-source `nanobot` framework from HKUDS. Please also credit the upstream project if you use this fork for research, demos, or engineering work.
