# Stress v2 Live Benchmark Summary

This directory contains compact public artifacts for the v2 live A/B benchmark used in the README.

## Files

- `stress_v2_metrics.json`: aggregated metrics for `baseline`, `nanobot_plus`, and `reflective_supervisor`
- `stress_v2_records.jsonl`: per-task distilled records without full raw traces

## Benchmark Setting

- 4 stress tasks
- Real WebSocket execution
- Provider: `deepseek-v4-pro`
- Variants: `baseline`, `nanobot_plus`, `reflective_supervisor`

## Aggregated Result

| Variant | Success Rate | Avg Tool Calls | Avg Latency | Replans | Reflections | Reflection Reuse | Trajectory Completeness |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 100% | 9.25 | 50,463 ms | 0 | 0 | 0% | 60.0% |
| nanobot_plus | 100% | 11.75 | 63,186 ms | 0 | 0 | 0% | 60.0% |
| reflective_supervisor | 100% | 9.50 | 52,658 ms | 3 | 3 | 100% | 100.0% |

## Interpretation

The strong model keeps completion rate saturated at 100%, so the main signal comes from supervision-sensitive metrics rather than success alone.

The important difference is that `reflective_supervisor` is the only variant that surfaces a real recovery loop in live execution:

- verifier-detected repeated failure
- reflection creation
- reflection reuse
- verifier-gated replanning
- full trajectory coverage for post-hoc diagnosis
