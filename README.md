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

It cannot create, start or stop a machine yet.

## What config.yaml holds

`modules.vm` holds what was asked for, never how a backend does it:

```yaml
vm:
  enabled: true
  pool: tank
```

## Still to build

1. A neutral machine record (processors, memory, firmware, disks, networks),
   written out per backend by the reconciler.
2. Create, start, stop and remove, with one zvol per disk.
3. The page as a list and a detail, like Proxmox without copying it: the list
   of machines, and for the one selected a second menu with Summary, Console,
   Hardware, Passthrough, Options and Snapshots.
4. Networks on FreedomNAS's bridges.
5. The console in the browser: noVNC through a WebSocket of the WebUI that
   bridges to the machine's VNC port, which listens on loopback only. It is the
   hardest part and the same on both backends.
6. An ISO store.
7. Device passthrough through the IOMMU.

## Tests

```sh
python3 -m pytest tests
```

The tests expect `freedomnas-core` and `freedomnas-profiles` beside this repo.
