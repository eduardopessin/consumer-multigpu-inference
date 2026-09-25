# Operations runbook

## Layout

| Thing | Where |
|---|---|
| Patch tree | `/home/eduardopessin/p2p-driver-work/nvidia-595.91.07-p2p` |
| Patched modules | `/lib/modules/<kver>/updates/dkms/` |
| Rebuild hook | `/etc/kernel/postinst.d/zz-nvidia-p2p` |
| Rebuild log | `/var/log/nvidia-p2p-rebuild.log` |
| Health check | `~/check-nvidia-p2p` (copy in `ops/`) |
| Kuma push monitor | `~/scripts/p2p_kuma_push.sh`, config in `~/.config/p2p_kuma.env` |
| RM blob | `<tree>/src/nvidia/_out/Linux_x86_64/nv-kernel.o` (17.6 MB) |

## Is P2P actually on?

```sh
nvidia-smi topo -p2p rw
```

Off-diagonal `OK` means working. `NS` / `GNS` means you are on a stock driver.

Corroborate with module size, which is the fastest way to tell a patched
build from a stock one:

```sh
ls -l /lib/modules/$(uname -r)/updates/dkms/nvidia.ko   # ~33 MB patched, ~18.7 MB stock
```

Check the module and userspace agree:

```sh
cat /proc/driver/nvidia/version          # loaded module
nvidia-smi --query-gpu=driver_version --format=csv,noheader
```

A `Driver/library version mismatch` from `nvidia-smi` means these diverged;
see `incident-2026-09-11.md`.

## After a kernel upgrade

The hook handles this automatically. It runs as `postinst.d` with the new
kernel version in `$1`, and:

1. Refuses to run if the patch tree or the kernel headers are missing.
2. Compares the patch tree version against installed `libcuda.so.<ver>`,
   and installs nothing if they differ.
3. Cleans only `kernel-open/` — `src/` is kernel-independent and the
   `nv-kernel.o` blob already carries the compiled RM patch.
4. Builds, then rejects the result if `nvidia.ko` is under 30 MB.
5. Installs the five modules and runs `depmod`.

It never aborts the kernel `postinst`, even on build failure. A broken GPU
setup is preferable to a system that will not finish installing a kernel.

Verify afterwards:

```sh
tail -5 /var/log/nvidia-p2p-rebuild.log
```

Expect `OK: driver P2P instalado para <kver> (nvidia.ko 33191048 bytes, ...)`.
P2P becomes active after reboot.

`dkms status` will report `Differences between built and installed modules`
for covered kernels. **This is the expected and desired state** — it means the
patched module is in place over what DKMS built.

## After a driver upgrade

**Not automated. This is the known gap.**

The hook does not fire on driver package upgrades. What happens today: DKMS
overwrites the patched modules with stock, the version guard sees the mismatch
on the next kernel event, and P2P stays off until the patch is ported.

Manual procedure when the driver version moves:

1. Fetch the new upstream tree at the new version tag.
2. Apply `patches/p2p-bar1-595.91.07.patch`. Expect conflicts; the RM sources
   move between releases. `src/nvidia/generated/g_kern_bus_nvoc.c` in
   particular is regenerated upstream and the five vtable edits will need
   redoing by hand.
3. Build from the **top level**, not inside `kernel-open/`:
   ```sh
   make modules -j"$(nproc)" SYSSRC=/usr/src/linux-headers-$(uname -r)
   ```
   The top-level Makefile creates the `nv-kernel.o_binary` symlink. Building
   inside `kernel-open/` skips it and yields a stock-sized module.
4. Confirm ~33 MB, install, `depmod`, reboot, re-check `topo -p2p rw`.
5. Point `SRC=` in the hook at the new tree and update this document.

### Upgrade protection (applied 2026-09-25)

Two complementary measures, both in force.

**Holds.** Sixteen packages, not three. The module and `libcuda.so` are a
matched pair, and `libcuda.so` ships in `libnvidia-compute-595`. Holding only
`nvidia-dkms/driver/kernel-common` would let userspace move on its own and
reproduce the 2026-09-11 mismatch from the other side:

```sh
sudo apt-mark hold \
  nvidia-dkms-595-open nvidia-driver-595-open nvidia-kernel-common-595 \
  nvidia-kernel-source-595-open nvidia-compute-utils-595 nvidia-utils-595 \
  nvidia-firmware-595-595.91.07 \
  libnvidia-compute-595 libnvidia-cfg1-595 libnvidia-common-595 \
  libnvidia-decode-595 libnvidia-encode-595 libnvidia-extra-595 \
  libnvidia-fbc1-595 libnvidia-gl-595 \
  xserver-xorg-video-nvidia-595
```

**Blacklist**, in `/etc/apt/apt.conf.d/50unattended-upgrades`, as the backstop
for a manual `dist-upgrade` that overrides holds:

```
Unattended-Upgrade::Package-Blacklist {
    "^nvidia-";
    "^libnvidia-";
    "^xserver-xorg-video-nvidia";
};
```

Verified rather than assumed:

```
$ sudo unattended-upgrade --dry-run --debug | grep -i blacklist
Initial blacklist: ^nvidia- ^libnvidia- ^xserver-xorg-video-nvidia
Applying pinning: PkgPin(pkg='/^^nvidia-/', priority=-32768)

$ apt-get -s upgrade | grep -E '^Inst (nvidia|libnvidia)-...595'
(no matches - driver stack untouched)
```

Releasing a hold is the deliberate act that must precede porting the patch:
`sudo apt-mark unhold <pkg>`.

**Still unheld: `dkms` itself.** A manual `apt upgrade` would move it from
`3.4.1` to `3.4.3`. It does not touch the loaded module, but DKMS is the
mechanism that overwrites patched modules with stock ones, so a behaviour
change there matters here. Left unheld because other packages depend on it.

A hook on driver package upgrades was considered and rejected: dpkg trigger
ordering between DKMS and a custom hook is not guaranteed, so it would race
against the thing it is meant to correct.

## Diagnosing a bad build

| Symptom | Cause |
|---|---|
| `nvidia.ko` ~18 MB | `nv-kernel.o` not linked; built inside `kernel-open/` instead of top level |
| `topo -p2p` shows `NS` | Stock module loaded, or NVOC vtable edits lost |
| `Driver/library version mismatch` | Module and userspace versions differ; reboot or reinstall matching pair |
| Hook logged nothing | Not executable, or not named to sort late in `postinst.d` |
| Build fails on new kernel | Upstream kernel API drift; needs a driver version that supports that kernel |

## Firmware and boot requirements

- **Above 4G Decoding**: enabled
- **Resizable BAR**: enabled
- Kernel cmdline: `intel_iommu=on iommu=pt`

Without a resized BAR1 there is no window to map the peer through, and the
patch cannot compensate. Confirm with:

```sh
nvidia-smi -q | grep -A3 'BAR1 Memory Usage'   # expect Total 16384 MiB
grep EnableResizableBar /proc/driver/nvidia/params   # expect 1
```
