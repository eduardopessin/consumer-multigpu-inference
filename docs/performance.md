# Performance

Measured with `qwen38-bench-20260912/bench.py` on vLLM 0.28.0, Qwen3.8-27B
NVFP4, TP=4, speculative decoding with 4 draft tokens. Primary metric is
`itl_ms_per_step` — wall time per decode iteration, which isolates per-step
cost from how many tokens an iteration happened to emit.

P2P was toggled with `NCCL_P2P_DISABLE=1` via a systemd drop-in, so the driver
and the patched modules were identical across arms.

## 2x2 factorial, concurrent4 ITL/step (ms)

| | P2P ON | P2P OFF |
|---|---:|---:|
| async scheduling OFF | 65.65 (2 runs) | 59.63 (2 runs) |
| async scheduling ON | **56.54 (3 runs)** | 64.10 (2 runs) |

`(async ON, P2P ON)` is the fastest cell measured.

The table does not support a clean additive or interaction model: `async OFF +
P2P OFF` (59.63) comes out ahead of `async OFF + P2P ON` (65.65), which no
straightforward account of P2P explains. The `async ON, P2P OFF` cell also has
a 4.4 ms spread between its two runs where the other cells sit inside 0.9 ms.

`concurrent4` turned out to be noisy across restarts. Pooling every
identical-config sample (async ON, P2P ON, 0.28.0, spec 4) collected across the
session gives 56.951, 56.453, 56.227, 58.353, 71.585 — median around 57.0
against 65.65 for async OFF. That is roughly a 13% difference on ITL/step, but
with an outlier that wide, two-sample cells cannot resolve differences of a few
percent.

## Single-stream: no measurable P2P effect

| Scenario | async OFF, P2P ON | async ON, P2P ON | async ON, P2P OFF |
|---|---:|---:|---:|
| text | 46.07 | 46.29 | 45.95 |
| code | 45.68 | 46.41 | 45.53 |
| agent | 46.10 | 46.08 | 47.20 |

Within ±2% and inconsistent in sign. The arithmetic agrees: about 168
all-reduces of ~10 KB per step account for roughly 4.2 ms of a 46 ms step, and
an ~11% P2P improvement on that slice is ~0.5 ms, about 1% — below the noise
floor.

Under concurrency the messages grow to the 0.125–0.5 MB range, where the
measured P2P advantage on transfers is larger. That is the regime where any
P2P contribution would be visible.

## Validity notes

- **Speculative acceptance is a confounder.** Accepted draft tokens change how
  many steps a completion takes, so `tokens/s` moves for reasons unrelated to
  interconnect. Acceptance varied between arms (for example 35.94% against
  52.31% on the same scenario in a different comparison), which is why
  `itl_ms_per_step` is the metric here and throughput figures are not used.
- **Custom all-reduce is unavailable on this hardware**, so it is not a free
  variable. All three alternative backends disable themselves with explicit
  reasons: `not supported on more than two PCIe-only GPUs`, `not supported for
  world_size=4`, and `Device capability 12.0 not supported`. PyNCCL is the only
  path, and `--disable-custom-all-reduce` was already correct.
- **Cell sizes are 2-3 runs.** Adequate for the async-scheduling effect, where
  the groups do not overlap, and not adequate for resolving P2P at the few
  percent level under a noisy `concurrent4`.

## What would settle the P2P question

More samples per cell on `concurrent4`, interleaved rather than grouped, with
speculation pinned. The single-stream result is already clear — no measurable
effect, consistent with the message-size arithmetic — so the open question is
concurrency, where the current data is suggestive but confounded by variance.

`bench/profile_p2p.sh` answers a different and complementary question: it runs
`nsys` and splits CUDA memcpy time by kind, so `PtoP` versus `HtoD`/`DtoH`
shows whether the peer path is being taken at all, independently of throughput.

## Hardware context

PCIe Gen3 x8 per GPU, all four on the same host bridge (`PHB`). Both the
host-staged and the peer-direct path are bounded by that link.
