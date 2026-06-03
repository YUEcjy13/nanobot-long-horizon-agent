# Case Study: Repeated Failure Recovery

This case study uses the live benchmark task `v2_provider_repeated_failure_reflection`.

## Task

Turn 1 instruction:

1. call `long_task`
2. read `configs/provider.env`
3. read the same path again without changing it
4. if both attempts fail, only report `同一路径重复失败`
5. do not recover or call `update_goal_state` in the first turn

Turn 2 instruction:

- recover using the available workspace clues and finish the task

## What This Task Tests

- repeated same-path failure
- whether the system recognizes that naive retry is not useful
- whether recovery is guided by explicit supervision telemetry
- whether the final recovery path is visible in traces

## A/B Snapshot

| Variant | Completed | Tool Calls | Latency | Replan Trigger | Reflection | Trajectory Completeness |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| baseline | yes | 8 | 45,542 ms | 0 | 0 | 0.60 |
| nanobot_plus | yes | 12 | 59,726 ms | 0 | 0 | 0.60 |
| reflective_supervisor | yes | 8 | 42,646 ms | 1 | 1 | 1.00 |

## Baseline

### Observed behavior

- first turn reads `configs/provider.env` twice and fails twice
- second turn recovers by falling back to `configs/provider.sample.env`
- recovery succeeds, but there is no explicit verifier/reflection telemetry

### Trace summary

- `goal_started`
- `goal_state_updated(event_type=replan)` only appears after the model itself pivots
- `goal_completed`

### Limitation

The task finishes, but the system does not surface:

- why the original strategy failed
- whether the repeated retry was recognized as a pattern
- what recovery lesson should be reused next time

## Nanobot Plus

### Observed behavior

- also reads `configs/provider.env` twice and fails twice
- eventually falls back to `configs/provider.sample.env`
- records more long-task state updates than baseline
- still lacks explicit verifier and reflection objects

### Execution-memory snapshot

- `failure`: repeated failures recorded
- `replan`: fallback to `configs/provider.sample.env`
- `goal_completed`

### Limitation

Compared with baseline, `nanobot_plus` captures more execution bookkeeping, but the recovery logic is still not exposed as an explicit supervision loop.

## Reflective Supervisor

### Turn-1 failure pattern

Two identical failures are captured with argument-sensitive signatures:

```text
read_file(path="configs/provider.env") -> File not found
read_file(path="configs/provider.env") -> File not found
```

### Verifier decision

After the second failure, the execution verifier emits:

```text
status = repeated_failure
need_replan = true
should_reflect = true
root_cause = The same tool failed repeatedly in the current step
suggested_next_step = Revise the strategy before retrying the same failing action
```

### Reflection

The generated reflection is persisted into execution memory:

```text
Root cause: The same tool failed repeatedly in the current step: read_file("{path: configs/provider.env}")
Avoid next time: Do not retry the same failing action unless new evidence changes the situation.
Suggested strategy: Revise the strategy before retrying the same failing action.
```

### Replan injection

The supervisor then requests replanning with explicit failure notes:

```text
failure_notes = ["read_file: Error: File not found: configs/provider.env"]
decision_status = repeated_failure
```

The next turn rewrites the plan through `update_goal_state(mark_replanned=true)` and pivots to:

- consult `fixtures/runbook/RUNBOOK.md`
- use `configs/provider.sample.env` as fallback

### Final recovery path

The agent successfully recovers:

1. reads `fixtures/runbook/RUNBOOK.md`
2. reads `configs/provider.sample.env`
3. extracts `PROVIDER=deepseek-v4-pro` and `TIMEOUT=30`
4. writes the recovery note to `reflective_outputs/v2_provider_reflection.md`
5. completes the goal

## Why This Case Matters

All three variants finish the task because the underlying model is strong. The difference is not completion alone.

The reflective supervisor is the only variant that makes the recovery loop explicit:

- repeated failure is recognized as a supervision event
- reflection is created and stored
- replanning is triggered by verifier output
- the trajectory is complete enough for post-hoc diagnosis

That is the core value of this upgrade: not just solving the task, but exposing **how** the agent failed, reflected, and recovered.
