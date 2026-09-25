# Measuring the model, not the queue

`cleanbench.py` measures inter-token latency and decode batch size against a
vLLM server, and discards any sample that was contaminated by other traffic.

## Why the gate exists

The server sits behind a LiteLLM gateway that other clients use. A naive
`time curl` against it measures queueing delay plus scheduling interference,
not decode speed. Two unrelated requests arriving mid-sample will change the
number for reasons that have nothing to do with the model or the hardware.

The gate reads `vllm:request_success_total` from `/metrics` before and after
each sample and requires it to have advanced by exactly one:

```python
delta_success = after["success"] - before["success"]
if delta_success != 1:
    return None, f"contaminado (delta_success={delta_success:.0f})"
```

If anything else completed during the window, the sample is thrown away and
retried. The run also waits for `num_requests_running` and
`num_requests_waiting` to both reach zero before starting.

This is the difference between a number you can compare across changes and a
number that drifts with whoever else is using the box.

## What it reports

| Column | Meaning |
|---|---|
| `wall_s` | Wall clock for the request |
| `tokens` | Completion tokens from the usage block |
| `tok/s` | Throughput as observed by the client |
| `steps` | Decode iterations, from `vllm:iteration_tokens_total_count` |
| `L` | Tokens per step — effective decode batch size |
| `ITL/step_ms` | Wall time per decode iteration |

`L` is the useful one for P2P work. It reports how many sequences the scheduler
is actually decoding per step. `ITL/step_ms` isolates per-iteration cost from
how many tokens that iteration produced, so a change in batching does not get
mistaken for a change in speed.

## Usage

```sh
python3 cleanbench.py
```

Collects 3 clean samples, allowing up to 8 attempts. Adjust `BASE` and the
`model` field for a different endpoint.

## Caveats

- Requires the vLLM Prometheus endpoint at `/metrics`.
- `temperature=0.0` for reproducible token counts.
- Sums `request_success_total` across all label sets, so it gates on any
  completion, not just the target model. That is deliberate: interference from
  a different model on the same server still perturbs the sample.
- Client-side timing includes network and gateway overhead. Use `ITL/step_ms`
  rather than `tok/s` when comparing driver or topology changes.
