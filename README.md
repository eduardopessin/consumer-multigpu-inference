# Consumer multi-GPU inference: PCIe BAR1 P2P on 4x RTX 5060 Ti

Getting tensor-parallel LLM inference to work on consumer Blackwell cards,
and keeping it working across kernel and driver upgrades.

Consumer GeForce drivers ship with no usable GPU-to-GPU P2P path. The mailbox
route is fused off and the BAR1 route is compiled to stubs that return
"not supported" on non-datacenter parts.

This repository documents the driver patch that enables the BAR1 route, the
operational glue that keeps it in place across kernel and driver upgrades, and
the evidence that it is active.

Enabling P2P was observed to speed up tensor-parallel serving on this machine.
That observation is not yet backed by a published measurement: a controlled
P2P-on versus P2P-off comparison is pending and will be added here when it is
run. Treat the speedup as reported, not demonstrated.

## Current state

Verified on the machine this was developed on:

```
$ nvidia-smi topo -p2p rw
        GPU0    GPU1    GPU2    GPU3
 GPU0   X       OK      OK      OK
 GPU1   OK      X       OK      OK
 GPU2   OK      OK      X       OK
 GPU3   OK      OK      OK      X
```

| | |
|---|---|
| GPUs | 4x RTX 5060 Ti 16 GB, all `PHB` (same host bridge) |
| PCIe | Gen3 x8 max per GPU |
| Driver | 595.91.07 open kernel modules |
| Kernel | 5.15.0-190-generic (Ubuntu 22.04) |
| BAR1 | 16384 MiB per GPU, `EnableResizableBar: 1` |
| P2P | 12/12 ordered pairs OK |

Full command output: [`docs/evidence.md`](docs/evidence.md).

## Contents

| Path | What it is |
|---|---|
| `patches/p2p-bar1-595.91.07.patch` | The driver patch, applies cleanly to upstream tag `595.91.07` |
| `ops/zz-nvidia-p2p` | Kernel postinst hook that rebuilds the patched modules |
| `ops/check-nvidia-p2p` | Health check: module size on disk plus runtime peer matrix |
| `bench/cleanbench.py` | Serving benchmark with a contamination gate |
| `docs/evidence.md` | Raw command output showing P2P active |
| `docs/performance.md` | The speedup claim, existing data, and the pending measurement |
| `docs/patch.md` | What the patch changes and why |
| `docs/operations.md` | Runbook: upgrades, diagnosis, known gaps |
| `docs/incident-2026-09-11.md` | Post-mortem: driver upgrade silently broke CUDA |

## Applying the patch

The patch is against the upstream open kernel modules tree, not a fork you
need to track:

```sh
git clone https://github.com/NVIDIA/open-gpu-kernel-modules.git
cd open-gpu-kernel-modules
git checkout 595.91.07
git apply /path/to/patches/p2p-bar1-595.91.07.patch
make modules -j"$(nproc)"
```

The patched `nvidia.ko` is roughly 33 MB. If yours comes out around 18 MB the
`nv-kernel.o` blob did not make it into the link and you have built a driver
with no P2P support. See `docs/operations.md`.

Firmware requirements: **Above 4G Decoding** and **Resizable BAR** must be
enabled, and the kernel needs `iommu=pt`. Without a resized BAR1 there is no
window to map the peer through and the patch cannot help you.

## Credit

The original BAR1 P2P work for 595.71.05 is by
[aikitoria](https://github.com/aikitoria). This repository carries a port to
595.91.07 plus the operational tooling around it.

## Scope and honesty

This is a patched proprietary-adjacent driver running outside its supported
configuration. It works here and is in daily use, but:

- The patch comments out a left-over-mapping assertion in the IOVAS destructor
  rather than fixing the underlying lifetime question. See `docs/patch.md`.
- `src/nvidia/generated/g_kern_bus_nvoc.c` is a generated file edited by hand.
  Regenerating NVOC will silently drop those edits.
- Driver upgrades, as opposed to kernel upgrades, are still not automatically
  handled. See `docs/operations.md`.
