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

There is no way yet to add a machine from the WebUI, or to start or stop one:
machines come from the record in `config.yaml`.

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
      firmware: uefi
      disks:
        - size_gb: 60
      nics:
        - bridge: fnas_bridge1
```

The reconciler turns each machine into the backend's own definition, using the
templates in the edition's profile: a libvirt domain on Linux, a vm-bhyve
`.conf` on FreeBSD. Disks are sparse zvols named `<pool>/vm/<machine>/disk<N>`,
so they travel with the pool. It creates a disk that is missing and never
resizes or removes one. `bios` firmware exists on Linux only: on FreeBSD such a
machine stays undefined, with a warning, and its disks are kept.

## Still to build

1. Create, start, stop and remove from the API.
2. The page as a list and a detail, like Proxmox without copying it: the list
   of machines, and for the one selected a second menu with Summary, Console,
   Hardware, Passthrough, Options and Snapshots.
3. The console in the browser: noVNC through a WebSocket of the WebUI that
   bridges to the machine's VNC port, which listens on loopback only. It is the
   hardest part and the same on both backends.
4. An ISO store.
5. Device passthrough through the IOMMU.

## Tests

```sh
python3 -m pytest tests
```

The tests expect `freedomnas-core` and `freedomnas-profiles` beside this repo.
