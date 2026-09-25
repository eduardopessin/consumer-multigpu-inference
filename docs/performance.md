# Performance: what is claimed and what is measured

## Status

Enabling P2P was observed to speed up tensor-parallel serving on this machine.
That observation is **reported, not demonstrated** — no controlled A/B has been
published here yet.

This page states the claim, records what evidence currently exists, and defines
the measurement that would settle it.

## Why P2P should matter here

Without a working peer path, GPU-to-GPU transfers in tensor-parallel serving
are staged through host memory: device to host, then host to device, with the
data crossing PCIe twice and passing through a CPU-side bounce buffer. The BAR1
path lets one GPU write directly into a peer's mapped BAR1 window.

How much that is worth depends on how much the workload actually moves between
GPUs per decode step, relative to how much time it spends reading weights from
local VRAM.

## Existing data, and why it does not settle the question

`qwen38-bench-20260912/` contains 27 runs from a harness that records TTFT,
ITL per step, tokens per step, speculative acceptance, preemptions and prefix
cache hits. Three arms are relevant:

| Scenario | v6 (P2P on) | v7 (`NCCL_P2P_DISABLE=1`) | v8 (both off) |
|---|---|---|---|
| single_decode_text | 47.00 | 45.95 | 46.52 |
| single_decode_code | 46.49 | 45.53 | 47.61 |
| single_decode_agent | 47.01 | 47.20 | 46.56 |
| concurrent4_text | 60.84 | 64.10 | 59.63 |
| longctx_32k_decode | 48.37 | 46.55 | 46.78 |

*ITL ms/step, mean of two repetitions, lower is better.*

These numbers show no P2P advantage. They are **not** treated as refuting the
claim, because the arms are not comparable on the axis being tested:

- Speculative acceptance varies more between arms than any plausible P2P
  effect. `single_decode_text` records 35.94% acceptance in v6 against 52.31%
  in v7. Accepted draft tokens change how many steps a completion takes, which
  moves every derived throughput figure independently of interconnect.
- The arms also differ in other configuration, so `NCCL_P2P_DISABLE` is not the
  only variable.
- `--disable-custom-all-reduce` was set, routing collectives through NCCL
  rather than vLLM's own all-reduce, which uses peer access directly.

A benchmark whose dominant source of variance is not the variable under test
cannot answer the question in either direction.

## The measurement that would settle it

Single variable: `NCCL_P2P_DISABLE`. Everything else held fixed — same server
invocation, same model, same seed, same prompts, back to back on an otherwise
idle machine.

Controls required:

- **Pin speculative decoding**, or disable it. If acceptance drifts between
  arms, the comparison is void. Report `acceptance_pct` per arm as a
  validity check, not as a result.
- **Report `itl_ms_per_step`** as the primary metric. It isolates per-iteration
  cost from how many tokens an iteration happened to emit.
- **Gate on contamination** using the approach in `bench/cleanbench.py`:
  require `vllm:request_success_total` to advance by exactly one per sample.
- **Repeat each arm at least three times**, interleaved rather than grouped, so
  drift over the session does not alias onto the variable.

Regimes worth covering, since the answer is expected to depend on them:

| Regime | Why |
|---|---|
| Low concurrency decode | Current production shape; least peer traffic per step |
| High `--max-num-seqs` | More collective volume per step |
| Long prefill | Largest single transfers |
| With and without `--disable-custom-all-reduce` | vLLM's all-reduce uses peer access directly |

Direct-path confirmation, independent of serving throughput:
`profile_p2p.sh` runs `nsys` and reports CUDA memcpy time split by kind.
`PtoP` traffic means peer-direct; `HtoD`/`DtoH` for the same transfers means
host-staged. This shows whether the path is being taken, separately from what
it is worth.

## Hardware context

PCIe Gen3 x8 per GPU, all four on the same host bridge (`PHB`). Both the
staged and the peer-direct path are constrained by that link, which bounds how
large any difference can be.
