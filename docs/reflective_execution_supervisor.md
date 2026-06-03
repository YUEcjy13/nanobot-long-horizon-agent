# Reflective Execution Supervisor

`Nanobot+` 在现有 long-horizon execution 增强基础上，进一步引入了一个轻量级的 **Reflective Execution Supervisor**，用于把长时任务执行过程组织成可诊断、可追踪、可反思、可重规划、可导出训练数据的闭环。

## 核心组件

1. `TrajectoryTracer`
   - 为每个 `goal_id` 记录独立的执行轨迹 `workspace/traces/trajectory_<goal_id>.jsonl`
   - 关键事件包括：
     - `goal_started`
     - `goal_state_updated`
     - `tool_call_failed`
     - `verifier_decision`
     - `reflection_created`
     - `replan_requested`
     - `goal_completed`

2. `RuleExecutionVerifier`
   - 基于规则诊断当前执行是否卡住
   - 重点信号包括：
     - 同一工具重复失败
     - 当前步骤长期停滞
     - 失败累积但未重规划
     - 计划已完成但未结束

3. `ReflectionBuilder`
   - 将 verifier 结论压缩为可复用的短反思
   - 反思结果同时写入：
     - trajectory
     - execution memory
     - runtime recall

4. `ReplanGate`
   - 只在 verifier 判断 `need_replan=True` 时生成系统级重规划注入
   - 要求模型先调用 `update_goal_state(mark_replanned=true)` 再继续执行

## 环境变量

```bash
export NANOBOT_ENABLE_EXECUTION_VERIFIER=1
export NANOBOT_ENABLE_REFLECTION_MEMORY=1
export NANOBOT_EXECUTION_VERIFIER_MODE=rule
export NANOBOT_MAX_REPLANS_PER_GOAL=3
```

## Benchmark

Reflective benchmark 任务定义与脚本位于：

- `benchmarks/reflective_execution/tasks.json`
- `benchmarks/reflective_execution/stress_tasks.json`
- `benchmarks/reflective_execution/stress_tasks_v2.json`
- `benchmarks/reflective_execution/run_reflective_benchmark.py`
- `benchmarks/reflective_execution/compare_reflective_results.py`

其中：

- `tasks.json`
  - 第一版 seed tasks，偏轻量，主要验证 supervisor 管线是否贯通
- `stress_tasks.json`
  - 第一版 live stress tasks，重点验证真实 WebSocket 场景下的 replan / trajectory 证据
- `stress_tasks_v2.json`
  - 第二版多阶段 stress tasks，显式诱发：
    - repeated same-tool failure
    - stalled step / plateau
    - reflection reuse
    - mark_replanned 驱动的恢复路径

## 第二版 stress task schema

第二版 benchmark 在原有字段基础上，新增了两个用于“分回合施压”的字段：

- `follow_up_prompts`
  - 按回合给出不同 follow-up 提示
  - 第 1 个元素会在第 1 个 agent turn 结束后发送
- `follow_up_setup_files`
  - 与 `follow_up_prompts` 对齐的 staged workspace 注入
  - 例如可在第 3 个 follow-up 前再写入一个“解锁文件”或 runbook clue

示例：

```json
{
  "task_id": "v2_stalled_gate_unlock",
  "prompt": "第一回合只检查 gate，若 LOCKED 就等待下一回合。",
  "follow_up_prompts": [
    "第二回合继续检查 gate。",
    "第三回合继续检查 gate。",
    "第四回合若已解锁则恢复并完成任务。"
  ],
  "follow_up_setup_files": [
    [],
    [],
    [
      {"path": "control/session_gate.txt", "content": "UNLOCKED: use data/recovery/target.txt\n"}
    ]
  ]
}
```

运行方式：

```bash
python benchmarks/reflective_execution/run_reflective_benchmark.py \
  --variant reflective_supervisor \
  --ws-url ws://127.0.0.1:8765
```

运行第二版 stress benchmark：

```bash
python benchmarks/reflective_execution/run_reflective_benchmark.py \
  --variant reflective_supervisor_stress_v2 \
  --tasks-file benchmarks/reflective_execution/stress_tasks_v2.json \
  --ws-url ws://127.0.0.1:8765
```

比较多组结果：

```bash
python benchmarks/reflective_execution/compare_reflective_results.py \
  --records outputs/reflective_benchmark/baseline_records.jsonl \
            outputs/reflective_benchmark/nanobot_plus_records.jsonl \
            outputs/reflective_benchmark/reflective_supervisor_records.jsonl
```

## Preference Export

完成 benchmark 后，可以基于结果导出：

- `preference_pairs.jsonl`
- `sft_tool_policy.jsonl`

相关工具位于：

- `nanobot/utils/preference_export.py`

第一版导出遵循“成功完成、恢复能力更强、重复失败更少、执行代价更低”的排序准则。
