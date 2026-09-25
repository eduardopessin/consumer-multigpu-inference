# What the patch changes

Port of aikitoria's 595.71.05 BAR1 P2P patch to 595.91.07. 14 files,
+457/-125.

The problem in one sentence: consumer Blackwell has working PCIe BAR1
hardware for peer access, but the driver never dispatches to it, because the
BAR1 P2P vtable slots are bound to stub implementations on anything that is
not a datacenter part.

## Resource Manager side

### Select the BAR1 route at all

`src/nvidia/src/kernel/gpu/bif/kernel_bif.c`

```c
- pKernelBif->p2pOverride = BIF_P2P_NOT_OVERRIDEN;
+ pKernelBif->p2pOverride = 0x11;          /* reads + writes */

- pKernelBif->pcieP2PType = NV_REG_STR_RM_PCIEP2P_TYPE_DEFAULT;
+ pKernelBif->pcieP2PType = NV_REG_STR_RM_PCIEP2P_TYPE_BAR1;
```

The default resolves to the mailbox path, which is fused off on these parts.

### Dispatch `_PCIE_BAR1` connections

`src/nvidia/src/kernel/gpu/bus/arch/pascal/kern_bus_gp100.c` gains a branch in
both `kbusCreateP2PMapping_GP100` and `kbusRemoveP2PMapping_GP100`:

```c
if (FLD_TEST_DRF(_P2PAPI, _ATTRIBUTES, _CONNECTION_TYPE, _PCIE_BAR1, attributes))
    return kbusCreateP2PMappingForBar1P2P_HAL(...);
```

Without this the attribute is recognised but falls through to the mailbox
branch.

### Unstub the vtable

`src/nvidia/generated/g_kern_bus_nvoc.c` — five slots repointed from stubs to
the real GH100 implementations:

| Slot | Was | Now |
|---|---|---|
| `kbusGetBar1P2PDmaInfo` | `_395e98` | `_GH100` |
| `kbusCreateP2PMappingForBar1P2P` | `_395e98` | `_GH100` |
| `kbusRemoveP2PMappingForBar1P2P` | `_395e98` | `_GH100` |
| `kbusHasPcieBar1P2PMapping` | `_d69453` | `_GH100` |
| `kbusIsPcieBar1P2PMappingSupported` | `_d69453` | `_GH100` |

The `_395e98` and `_d69453` suffixes are NVOC's generated stubs; they return
failure and `NV_FALSE` respectively. This is the single most important hunk:
everything else is plumbing that never executes while these stubs are bound.

**This is a generated file.** Regenerating NVOC drops these edits without
warning, and the failure mode is silent loss of P2P rather than a build error.

### Aperture rewriting

`src/nvidia/src/kernel/rmapi/nv_gpu_ops.c` and
`src/nvidia/arch/nvalloc/unix/src/osmemdesc.c` rewrite peer apertures to
`SYS_COH`, so the resulting PTEs describe the peer's BAR1 as system-coherent
memory rather than as peer video memory.

### Suppressed assertion

`src/nvidia/src/kernel/mem_mgr/io_vaspace.c` — `iovaspaceDestruct_IMPL` had an
assertion that the IOVAS has no mappings left at teardown. It is commented out,
with the original author's note `TODO: might keep p2p mappings...`.

This is a suppressed diagnostic, not a fix. BAR1 P2P mappings can outlive the
IOVAS destructor and the correct answer is to understand that lifetime, not to
silence the check. It is carried forward as-is because changing it was out of
scope for a version port, but it is the first thing to address if this ever
goes anywhere serious.

## Kernel module side

### Resizable BAR on by default

`kernel-open/nvidia/nv-reg.h`: `__NV_ENABLE_RESIZABLE_BAR` default `0` -> `1`.

BAR1 must be large enough to host the peer window. On this machine it resolves
to 16384 MiB per GPU, matching the full 16 GB of VRAM. Requires Above 4G
Decoding and Resizable BAR in firmware.

### Hugepage compound order tracking

`kernel-open/nvidia/os-mlock.c`, `nv-dma.c`, `nv.c`, plus struct fields in
`kernel-open/common/inc/nv-linux.h`.

Locked user pages can be 2 MB compound pages. The stock path walks them as
4 KB pages, so a mapping is described with 512x more entries than it has, and
the DMA addresses after the first are wrong.

The patch propagates the compound order alongside the page array. The
mechanism is a hidden header prepended by `os_lock_user_pages()`:

```
[num_entries:NvU64][compound_order:NvU64][struct page * array...]
```

read back by `nv_get_page_array_compound_order()`. `nv_register_user_pages()`
then divides the page count by the compound order before allocating its
tracking array.

Prepending a hidden header to an array whose pointer is passed around as
`void *` is fragile — any caller that allocates or frees that array without
going through these helpers corrupts the heap. It is the approach the original
patch took and it works, but it is not the design one would choose freely.

### UVM coherence

`kernel-open/nvidia-uvm/uvm_gpu.h` — `uvm_parent_gpu_is_coherent()` returns
true unconditionally for `GB100` and later.

UVM's P2P registration must take the coherent route to match the `SYS_COH`
aperture rewrite above. Otherwise the `ZONE_DEVICE` peer DMA setup contradicts
the PTEs that describe BAR1 as system memory, and the two disagree about what
the mapping is.

## Verifying a build

```sh
ls -l kernel-open/nvidia.ko     # expect ~33 MB, not ~18 MB
nvidia-smi topo -p2p rw         # expect OK off the diagonal
```

A ~18 MB module means the `nv-kernel.o` symlink was not resolved at link time
and you have a stock driver wearing a patched version number.
