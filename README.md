# FreedomNAS virtual machines module

Community module `vm`, package `freedomnas-module-vm`: virtual machines on the
NAS, with **libvirt and QEMU/KVM on Linux** and **vm-bhyve on FreeBSD**. The
backend comes from the edition's profile in `profiles/vm/`; the Python code
does not know which one it is talking to.

## What it does today

This is the module's skeleton:

- **Set up** installs the edition's hypervisor and prepares the `<pool>/vm`
  dataset on the chosen pool.
- The page shows the hypervisor's state, whether the processor's virtualization
  works, and the machines the hypervisor knows about.
- The reconciler brings all of it back at boot, on any edition.

The page only shows the machines for now: adding, starting and stopping one
goes through the API.

## What config.yaml holds

`modules.vm` holds what was asked for, never how a backend does it:

```yaml
vm:
  enabled: true
  pool: tank
  machines:
    win:
      cpus: 2
      memory_mb: 4096
      state: up
      uuid: 6f1c2e3a-0b1d-4c2e-8f3a-1234567890ab
      disks:
        - size_gb: 60
      nics:
        - bridge: fnas_bridge1
          mac: 52:54:00:aa:bb:cc
```

The reconciler turns each machine into the backend's own definition, using the
templates in the edition's profile: a libvirt domain on Linux, a vm-bhyve
`.conf` on FreeBSD. It creates a disk that is missing, never resizes or removes
one, and starts a machine whose state is `up`.

## Moving a machine between editions

Disks are raw sparse zvols named `<pool>/vm/<machine>/disk<N>`, so they travel
with the pool and need no conversion. Every machine has the same hardware on
every edition:

| Part | Standard |
|------|----------|
| Firmware | UEFI only: bhyve has no plain BIOS boot |
| Disks and networks | virtio, disks from PCI slot 4, then networks |
| Video and input | plain VGA, USB tablet |
| Serial | one port |
| Identity | the uuid and MAC addresses in the record |

What a guest can still notice: the firmware's boot entries stay on the old
edition, so it must boot through the UEFI fallback path
(`\EFI\BOOT\BOOTX64.EFI`), and a few virtual chipset details differ.

## API

| Route | What it does |
|-------|--------------|
| `POST /api/vm/machines` | Creates a machine from `{name, cpus, memory_mb, disks, nics}` |
| `POST /api/vm/machines/<name>/<action>` | `start`, `stop` (the guest shuts itself down) or `poweroff` |
| `DELETE /api/vm/machines/<name>` | Removes a stopped machine; its disks stay unless `?destroy_disks=true` |

## Still to build

1. The page as a list and a detail, like Proxmox without copying it: the list
   of machines, and for the one selected a second menu with Summary, Console,
   Hardware, Passthrough, Options and Snapshots.
2. The console in the browser: noVNC through a WebSocket of the WebUI that
   bridges to the machine's VNC port, which listens on loopback only. It is the
   hardest part and the same on both backends.
3. An ISO store.
4. Device passthrough through the IOMMU.

## Tests

```sh
python3 -m pytest tests
```

The tests expect `freedomnas-core` and `freedomnas-profiles` beside this repo.
