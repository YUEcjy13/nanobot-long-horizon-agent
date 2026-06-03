# Nanobot+: Reflective Execution Supervisor for Long-Horizon Agents

`nanobot-long-horizon-agent` is a runnable research fork of [HKUDS/nanobot](https://github.com/HKUDS/nanobot) focused on **long-horizon tool-using agents**.

This project upgrades the original agent loop with a lightweight **Reflective Execution Supervisor** that explicitly tracks goal progress, diagnoses repeated failures, writes reflection memory, and triggers verifier-gated replanning in multi-turn execution.

## Why This Fork

Strong frontier models can often finish tasks even when the execution process is opaque, brittle, or inefficient. In practice, that means completion rate alone is a weak signal for agent quality.

This fork focuses on the supervision layer behind long-horizon execution:

- **Structured goal state** for multi-step objectives
- **Execution memory** for task-focused episodic traces
- **Trajectory tracing** for observable execution history
- **Rule-based execution verifier** for failure diagnosis
- **Reflection memory** for reusable recovery hints
- **Verifier-gated replanning** for controlled recovery instead of blind retries

The result is a more diagnosable and benchmarkable agent system, not just a prompt tweak.

## Core Upgrades

### 1. Structured Goal Runtime

Active goals are represented with structured fields such as:

- `goal_id`
- `plan_steps`
- `current_step`
- `completed_steps`
- `verified_facts`
- `blocked_reason`
- `replan_count`

These states are synchronized to the WebUI through WebSocket so long-running tasks remain visible instead of becoming hidden inside free-form chat.

### 2. Task-Focused Execution Memory

This fork adds an episodic execution memory store specialized for long-horizon tasks. It records events such as:

- `goal_started`
- `progress_update`
- `failure`
- `reflection`
- `replan`
- `goal_completed`

Relevant memory is recalled into runtime context before subsequent decisions.

### 3. Reflective Execution Supervisor

The new supervisor layer adds:

- **Trajectory tracing**: writes per-goal execution traces
- **Execution verifier**: detects repeated failure, stalled steps, and replanning conditions
- **Reflection builder**: turns verifier decisions into reusable recovery guidance
- **Replan gate**: injects controlled replanning instructions back into the loop

This creates the full supervision chain:

`goal state -> trajectory tracing -> verifier diagnosis -> reflection memory -> gated replanning`

### 4. Live Benchmarking and Data Export

The repo includes benchmark utilities for comparing:

- `baseline`
- `nanobot_plus`
- `reflective_supervisor`

It also includes utilities to export trajectory-derived preference / SFT-style data for future training.

## Repository Layout

```text
nanobot/agent/
  execution_memory.py
  trajectory.py
  verifier.py
  reflection.py
  replan_gate.py

benchmarks/
  goal_execution/
  reflective_execution/

docs/
  goal-execution-benchmark.md
  reflective_execution_supervisor.md

tests/
  agent/
  utils/
```

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YUEcjy13/nanobot-long-horizon-agent.git
cd nanobot-long-horizon-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Install WebUI dependencies

```bash
cd webui
npm install
cd ..
```

### 3. Configure your provider

Create or edit your config file:

```bash
nanobot onboard
```

For example, with DeepSeek:

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
      "enabled": true,
      "host": "127.0.0.1",
      "port": 8765
    }
  },
  "gateway": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 18790
  }
}
```

Then export your key:

```bash
export DEEPSEEK_API_KEY=your_key_here
```

### 4. Launch the gateway

```bash
nanobot gateway --config /path/to/your/config.json
```

### 5. Launch the WebUI

```bash
cd webui
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

## Reflective Supervisor Controls

The reflective components are controlled by environment variables:

```bash
export NANOBOT_ENABLE_GOAL_RUNTIME_CONTEXT=1
export NANOBOT_ENABLE_EXECUTION_RECALL=1
export NANOBOT_ENABLE_AUTO_REPLAN=1
export NANOBOT_ENABLE_EXECUTION_VERIFIER=1
export NANOBOT_EXECUTION_VERIFIER_MODE=rule
export NANOBOT_ENABLE_REFLECTION_MEMORY=1
export NANOBOT_MAX_REPLANS_PER_GOAL=3
```

## Benchmark

### Goal-execution benchmark

```bash
python benchmarks/goal_execution/collect_live_benchmark.py
python benchmarks/goal_execution/compare_live_benchmarks.py
```

### Reflective supervisor benchmark

```bash
python benchmarks/reflective_execution/run_reflective_benchmark.py \
  --tasks benchmarks/reflective_execution/stress_tasks_v2.json

python benchmarks/reflective_execution/compare_reflective_results.py
```

## Live A/B Result Snapshot

The most informative benchmark is the **v2 live stress benchmark** using real WebSocket execution and `deepseek-v4-pro`.

### Task setting

- 4 stress tasks
- multi-turn tool-use
- repeated-failure and stalled-step pressure
- baseline vs `nanobot_plus` vs `reflective_supervisor`

### Main result

| Variant | Success Rate | Avg Tool Calls | Avg Latency | Replans | Reflections | Reflection Reuse | Trajectory Completeness |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 100% | 9.25 | 50,463 ms | 0 | 0 | 0% | 60.0% |
| nanobot_plus | 100% | 11.75 | 63,186 ms | 0 | 0 | 0% | 60.0% |
| reflective_supervisor | 100% | 9.50 | 52,658 ms | 3 | 3 | 100% | 100.0% |

### Interpretation

With a strong model, completion rate alone does not separate systems well. The key improvement from the reflective supervisor is the **quality of execution supervision**:

- explicit verifier-triggered replanning
- reflection generation and reuse
- full trajectory coverage for post-hoc diagnosis
- lower tool-call overhead than `nanobot_plus` under the same stress tasks

## Key Files for the Upgrade

- [`nanobot/agent/context.py`](./nanobot/agent/context.py)
- [`nanobot/agent/loop.py`](./nanobot/agent/loop.py)
- [`nanobot/agent/tools/long_task.py`](./nanobot/agent/tools/long_task.py)
- [`nanobot/agent/execution_memory.py`](./nanobot/agent/execution_memory.py)
- [`nanobot/agent/trajectory.py`](./nanobot/agent/trajectory.py)
- [`nanobot/agent/verifier.py`](./nanobot/agent/verifier.py)
- [`nanobot/agent/reflection.py`](./nanobot/agent/reflection.py)
- [`nanobot/agent/replan_gate.py`](./nanobot/agent/replan_gate.py)
- [`docs/reflective_execution_supervisor.md`](./docs/reflective_execution_supervisor.md)

## Testing

Run the core regression and supervisor tests with:

```bash
pytest tests/agent tests/utils tests/session tests/providers
```

## Acknowledgement

This project is built on top of the excellent open-source foundation from [HKUDS/nanobot](https://github.com/HKUDS/nanobot). This fork keeps the original runnable agent stack while extending it toward **long-horizon execution supervision, reflection, and evaluation**.
