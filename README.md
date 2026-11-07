# FreedomNAS virtual machines module

Community module `vm`, package `freedomnas-module-vm`: virtual machines on the
NAS, with **libvirt and QEMU/KVM on Linux** and **vm-bhyve on FreeBSD**. The
backend comes from the edition's profile in `profiles/vm/`; the Python code
does not know which one it is talking to.

**Not published.** The module is in no package repository, not even `dev`,
and stays out until it is stable and works fully. To try it on a test machine,
copy `modules/vm` and `profiles/vm` from this repository by hand.

## What it does today

The module so far:

- **Set up** installs the edition's hypervisor and prepares the `<pool>/vm`
  dataset on the chosen pool.
- The page shows the hypervisor's state, whether the processor's virtualization
  works, and the machines the hypervisor knows about.
- The reconciler brings all of it back at boot, on any edition.
- On Linux, the account that runs Set up joins the `libvirt` group, to use
  `virsh` and virt-manager with its own login. It has to log out and back in
  first: a session keeps the groups it started with. The WebUI does not need
  this, and FreeBSD has no such group: vm-bhyve is for root only.

The page has the list of machines, and for the one selected a second menu.
Summary, Hardware and Console work: create, start, shut down, power off and remove
a machine, start it from an install image, and use its screen in the browser.
Passthrough, Options and Snapshots are not there yet.

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
| `GET /api/vm/status` | The hypervisor, the pool, the bridges, the machines and the images |
| `POST /api/vm/setup` | Installs the hypervisor if needed and sets it up on `{pool}` (a job) |
| `POST /api/vm/machines` | Creates a machine from `{name, cpus, memory_mb, disks, nics}` |
| `POST /api/vm/machines/<name>/<action>` | `start`, `stop` (the guest shuts itself down) or `poweroff`, kept as what it does at boot |
| `POST /api/vm/machines/<name>/install` | Starts a stopped machine from `{iso}`, this once |
| `WS /api/vm/machines/<name>/console` | The machine's screen, after a first message with the credentials |
| `DELETE /api/vm/machines/<name>` | Removes a stopped machine; its disks stay unless `?destroy_disks=true` |
| `POST /api/vm/isos?name=<file>`, `POST /api/vm/isos/download`, `DELETE /api/vm/isos/<name>` | Upload, fetch or remove an install image |

Every route is described in freedomnas-docs, `docs/api/vm.md`.

## Still to build

1. Editing a machine's hardware.
2. Device passthrough through the IOMMU.
3. A second image at once, for the virtio drivers Windows needs while installing.

## Console and install images

Install images are in `<pool>/vm/.iso`: uploaded, or fetched from an address by
the NAS. A machine starts from one once: the record does not keep it. On FreeBSD
such a start waits for the console to connect, so an installer's first prompt is
not missed; the page connects by itself.

The console is noVNC in the browser, through a WebSocket of the WebUI that first
asks for the credentials and then bridges to the machine's VNC port. That port
listens on loopback only, so nothing is opened on the network. noVNC's core is
bundled in `modules/vm/frontend/static/novnc`, under the MPL-2.0, with its license.

## Network on FreeBSD

The module keeps a permanent tap, `vmhold<N>`, in each bridge machines use. Without
it, every machine's tap joining or leaving changes the capabilities the bridge
imposes on the network card, some cards reinitialise, and the NAS loses the network
for seconds, up to half a minute on the test box's Realtek. With it, starts and
stops lose no packets.

## Tests

```sh
python3 -m pytest tests
```

The tests expect `freedomnas-core` and `freedomnas-profiles` beside this repo.
