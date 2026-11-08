"""
The hypervisor of the virtual machines module, from the edition's profile:
libvirt with QEMU/KVM on Linux, vm-bhyve on FreeBSD. Nothing here knows which
one it is talking to.

config.yaml (modules.vm) holds what was asked for, never how a backend does it:

"enabled": true once set up,
"pool": the data pool whose <pool>/vm dataset holds the virtual machines,
"machines": {name: {"cpus", "memory_mb", "state": "up"|"down", "uuid",
             "disks": [{"size_gb"}], "nics": [{"bridge", "mac"}]}}.

Install images live in <pool>/vm/.iso, a folder that travels with the pool. A
machine starts from one once, with `install_machine`: the record does not keep
it, and the image is left out of the definition again as soon as it has started.

A machine has the same hardware on every edition, so its guest can move with
the pool: UEFI, raw disks and networks on virtio, plain VGA, one serial port,
and the same PCI slots (disks from slot 4, then networks). Its uuid and MAC
addresses are made once and stay in the record, so the guest sees the same
computer.

The reconciler rebuilds the rest on any edition from those values: a dataset
per machine, a sparse zvol per disk that is missing (it never resizes or removes
one), the backend's own definition from the profile's templates, and a machine
that should be up started. A profile's "list" prints one
"<name><TAB>running|stopped" per machine and fails when the hypervisor does not
answer.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
import uuid as uuid_module
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from freedomnas.reconcile import ActionResult
except ImportError:
    from reconcile import ActionResult  # type: ignore[no-redef]

logger = logging.getLogger("FreedomNAS.Module.Vm")

MODULE_ID = "vm"
CATEGORY = "vm"
DATASET = "vm"
_POOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
_MACHINE = re.compile(r"[a-z0-9][a-z0-9_-]{0,30}")
_BRIDGE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,14}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_MAC = re.compile(r"52:54:00(:[0-9a-f]{2}){3}")
STATES = ("up", "down")
ISO_FOLDER = ".iso"
_ISO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\.iso", re.IGNORECASE)
_VNC = re.compile(r"127\.0\.0\.1:(\d{1,5})")
PART = ".part"
CHUNK = 1024 * 1024
FIRST_SLOT = 4
MAX_CPUS = 256
MAX_MEMORY_MB = 16 * 1024 * 1024
MIN_MEMORY_MB = 64
MAX_DISKS = 8
MAX_NICS = 4
MAX_DISK_GB = 1024 * 1024


class VmError(Exception):
    pass


def value(engine, key: str, default: Any = None) -> Any:
    try:
        return engine.get_profile_section(CATEGORY).get(key, default)
    except Exception:
        return default


def installed(engine) -> bool:
    try:
        engine.execute(CATEGORY, "installed_check")
        return True
    except Exception:
        return False


def install(engine) -> None:
    engine.execute("packages", "update_cache", timeout=3600, check=False)
    init = (value(engine, "init_packages", {}) or {}).get(getattr(engine, "init_system", ""), [])
    for name in [*value(engine, "packages", []), *init]:
        engine.execute("packages", "install", timeout=7200, package_name=name)


def accelerated(engine) -> bool:
    """The processor's virtualization is on and the hypervisor can use it."""
    try:
        engine.execute(CATEGORY, "accel_check")
        return True
    except Exception:
        return False


def answers(engine, seconds: int = 30) -> bool:
    """Whether the hypervisor answers, giving one that has just started the time to."""
    for left in range(seconds, 0, -1):
        if machines(engine) is not None:
            return True
        if left > 1:
            time.sleep(1)
    return False


def machines(engine) -> Optional[List[Dict[str, str]]]:
    """The virtual machines, or None when the hypervisor does not answer."""
    try:
        out, _ = engine.execute(CATEGORY, "list")
    except Exception:
        return None
    found = []
    for line in out.splitlines():
        name, _, state = line.partition("\t")
        if name:
            found.append({"name": name, "state": state.strip()})
    return found


def pools(engine) -> List[str]:
    try:
        out, _ = engine.execute("zfs", "pool_list")
    except Exception:
        return []
    return [line.split("\t")[0] for line in out.splitlines() if line.strip()]


def bridges(engine) -> List[str]:
    try:
        out, _ = engine.execute(CATEGORY, "bridges_list")
    except Exception:
        return []
    return sorted(out.split())


def dataset_of(pool: str) -> str:
    return f"{pool}/{DATASET}"


def check_pool(engine, pool: Any) -> str:
    text = str(pool or "").strip()
    if not _POOL.fullmatch(text):
        raise VmError("Choose the pool that keeps the virtual machines")
    if text not in pools(engine):
        raise VmError(f"No pool named {text}")
    return text


def prepare(engine, pool: str) -> None:
    """The dataset of the virtual machines, the hypervisor told about it and started."""
    engine.execute(CATEGORY, "dataset_ensure", dataset=dataset_of(pool))
    engine.execute(CATEGORY, "configure", dataset=dataset_of(pool))
    out, _ = engine.execute(CATEGORY, "dataset_mountpoint", dataset=dataset_of(pool))
    engine.execute(CATEGORY, "dir_ensure", path=f"{out.strip()}/{ISO_FOLDER}")
    for name in value(engine, "services", []):
        engine.execute("services", "enable", service_name=name)
        out, _ = engine.execute("services", "is_running", check=False, service_name=name)
        if out.strip() != "yes":
            engine.execute("services", "start", service_name=name)


def enabled(config) -> bool:
    return bool((config.get_module_config(MODULE_ID) or {}).get("enabled"))


def grant_access(engine, config, username: Optional[str]) -> None:
    """
    The administrator who set the module up joins the profile's
    "access_group" at once, so it can use the hypervisor's own tools. The
    group gives root's power: only administrators are in it, and it comes
    with the role (the reconciler adds it to every administrator), so the
    record does not name it. An account that is not an administrator, a
    group or account that is not there, or a failure changes nothing else
    about the set up.
    """
    group = value(engine, "access_group")
    user = config.get_user(username) if group and username else None
    if not user or not user.get("admin"):
        return
    try:
        out, _ = engine.execute("users", "group_exists", group=group, check=False)
        if not out.strip():
            logger.warning("No group %s on this system for %s", group, username)
            return
        out, _ = engine.execute("users", "list_groups", username=username, check=False)
        if group in out.split():
            return
        engine.execute("users", "add_to_group", username=username, group=group)
    except Exception as exc:
        logger.warning("Could not add %s to group %s: %s", username, group, exc)


def setup(engine, config, pool: str, username: Optional[str] = None) -> None:
    pool = check_pool(engine, pool)
    if not installed(engine):
        install(engine)
    prepare(engine, pool)
    grant_access(engine, config, username)
    section = dict(config.get_module_config(MODULE_ID) or {})
    section.update(enabled=True, pool=pool)
    config.set_module_config(MODULE_ID, section)


def status(engine, config) -> Dict[str, Any]:
    section = config.get_module_config(MODULE_ID) or {}
    is_installed = installed(engine)
    group = value(engine, "access_group")
    listed = machines(engine) if is_installed and section.get("enabled") else None
    records = section.get("machines") or {}
    states = {m["name"]: m["state"] for m in listed or []}
    pool = section.get("pool") or ""
    shown = [{**record, "name": name, "state": states.get(name, "undefined"), "wanted": record.get("state", "down"),
              "stopping": states.get(name) == "running" and record.get("state", "down") == "down",
              "dataset": f"{dataset_of(pool)}/{name}"}
             for name, record in sorted(records.items())]
    shown += [{"name": name, "state": state, "foreign": True}
              for name, state in sorted(states.items()) if name not in records]
    return {
        "backend": value(engine, "backend", ""),
        "installed": is_installed,
        "enabled": bool(section.get("enabled")),
        "running": listed is not None,
        "accelerated": accelerated(engine) if is_installed else None,
        "pool": section.get("pool"),
        "pools": pools(engine),
        "machines": shown,
        "bridges": bridges(engine),
        "isos": isos(engine, config) if section.get("enabled") else [],
        "console_note": value(engine, "console_note", ""),
        "console_note_code": value(engine, "console_note_code", ""),
        "access_group": group or None,
        "access_members": sorted(u["username"] for u in config.get_users()
                                 if group and u.get("admin")),
    }


def _number(machine: Dict[str, Any], key: str, low: int, high: int) -> int:
    number = machine.get(key)
    if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
        raise VmError(f"{key} is a whole number from {low} to {high}")
    return number


def check_machine(name: Any, machine: Any) -> Dict[str, Any]:
    """The neutral record of a machine, only its known fields, or VmError."""
    if not isinstance(name, str) or not _MACHINE.fullmatch(name):
        raise VmError("A machine's name is up to 31 lowercase letters, digits, '-' and '_'")
    if not isinstance(machine, dict):
        raise VmError(f"Machine {name} has no settings")
    if not _UUID.fullmatch(str(machine.get("uuid") or "")):
        raise VmError(f"Machine {name} has no valid uuid")
    if machine.get("state", "down") not in STATES:
        raise VmError(f"state is one of: {', '.join(STATES)}")
    disks, nics = machine.get("disks"), machine.get("nics", [])
    if not isinstance(disks, list) or not 1 <= len(disks) <= MAX_DISKS:
        raise VmError(f"A machine has from 1 to {MAX_DISKS} disks")
    if not isinstance(nics, list) or len(nics) > MAX_NICS:
        raise VmError(f"A machine has up to {MAX_NICS} networks")
    for nic in nics:
        if not isinstance(nic, dict) or not _BRIDGE.fullmatch(str(nic.get("bridge") or "")):
            raise VmError("A network names the bridge it is on")
        if not _MAC.fullmatch(str(nic.get("mac") or "")):
            raise VmError(f"Machine {name} has a network without a valid MAC address")
    return {
        "cpus": _number(machine, "cpus", 1, MAX_CPUS),
        "memory_mb": _number(machine, "memory_mb", MIN_MEMORY_MB, MAX_MEMORY_MB),
        "state": machine.get("state", "down"),
        "uuid": machine["uuid"],
        "disks": [{"size_gb": _number(d if isinstance(d, dict) else {}, "size_gb", 1, MAX_DISK_GB)} for d in disks],
        "nics": [{"bridge": nic["bridge"], "mac": nic["mac"]} for nic in nics],
    }


def _new_mac() -> str:
    return "52:54:00:" + ":".join(f"{secrets.randbelow(256):02x}" for _ in range(3))


def new_machine(name: Any, settings: Any) -> Dict[str, Any]:
    """What was asked for plus the identity the machine keeps for good."""
    if not isinstance(settings, dict):
        raise VmError("A machine needs its settings")
    nics = settings.get("nics", [])
    if not isinstance(nics, list):
        raise VmError(f"A machine has up to {MAX_NICS} networks")
    return check_machine(name, {
        **settings, "state": "down", "uuid": str(uuid_module.uuid4()),
        "nics": [{"bridge": nic.get("bridge") if isinstance(nic, dict) else None, "mac": _new_mac()} for nic in nics],
    })


def definition(engine, pool: str, name: str, machine: Dict[str, Any], iso: Optional[str] = None) -> str:
    """The backend's definition of a machine, with the same hardware on every edition; iso boots first."""
    base = f"{dataset_of(pool)}/{name}"
    count = len(machine["disks"])
    disks = "".join(
        value(engine, "disk_template").format(index=index, letter=chr(ord("a") + index), zvol=f"{base}/disk{index}",
                                              slot=FIRST_SLOT + index, boot=index + 2)
        for index in range(count))
    nics = "".join(
        value(engine, "nic_template").format(index=index, bridge=nic["bridge"], mac=nic["mac"],
                                             slot=FIRST_SLOT + count + index)
        for index, nic in enumerate(machine["nics"]))
    return value(engine, "definition_template").format(
        name=name, uuid=machine["uuid"], cpus=machine["cpus"], memory_mb=machine["memory_mb"], disks=disks, nics=nics,
        cdrom=value(engine, "cdrom_template").format(path=iso) if iso else "")


def _definition_file(engine, pool: str, name: str) -> str:
    out, _ = engine.execute(CATEGORY, "dataset_mountpoint", dataset=f"{dataset_of(pool)}/{name}")
    return f"{out.strip()}/{name}.{value(engine, 'definition_suffix')}"


def apply_machine(engine, pool: str, name: str, machine: Dict[str, Any], iso: Optional[str] = None) -> None:
    """A machine's dataset, missing disks and definition."""
    base = f"{dataset_of(pool)}/{name}"
    engine.execute(CATEGORY, "dataset_ensure", dataset=base)
    for index, disk in enumerate(machine["disks"]):
        engine.execute(CATEGORY, "disk_ensure", zvol=f"{base}/disk{index}", size_gb=disk["size_gb"])
    for nic in machine["nics"]:
        engine.execute(CATEGORY, "bridge_ensure", bridge=nic["bridge"])
    file = _definition_file(engine, pool, name)
    engine.execute_with_stdin(CATEGORY, "definition_write", definition(engine, pool, name, machine, iso), file=file)
    engine.execute(CATEGORY, "define", file=file)


def _section_ready(config) -> Dict[str, Any]:
    section = dict(config.get_module_config(MODULE_ID) or {})
    if not section.get("enabled"):
        raise VmError("Set up the virtual machines first")
    return section


def _running(engine, name: str) -> bool:
    return any(m["name"] == name and m["state"] == "running" for m in machines(engine) or [])


def create_machine(engine, config, name: Any, settings: Any) -> Dict[str, Any]:
    section = _section_ready(config)
    machine = new_machine(name, settings)
    records = dict(section.get("machines") or {})
    if name in records or any(m["name"] == name for m in machines(engine) or []):
        raise VmError(f"There is already a machine named {name}")
    apply_machine(engine, section["pool"], name, machine)
    records[name] = machine
    section["machines"] = records
    config.set_module_config(MODULE_ID, section)
    return machine


def _record(section: Dict[str, Any], name: str) -> Dict[str, Any]:
    machine = (section.get("machines") or {}).get(name)
    if machine is None:
        raise VmError(f"No machine named {name}")
    return machine


def machine_action(engine, config, name: str, action: str) -> None:
    """start, stop (the guest shuts itself down) or poweroff, kept as the state the reconciler restores."""
    if action not in ("start", "stop", "poweroff"):
        raise VmError(f"Unknown action {action}")
    section = _section_ready(config)
    machine = dict(_record(section, name))
    engine.execute(CATEGORY, action, name=name)
    machine["state"] = "up" if action == "start" else "down"
    section["machines"] = {**section["machines"], name: machine}
    config.set_module_config(MODULE_ID, section)


def iso_folder(engine, config) -> Path:
    section = _section_ready(config)
    out, _ = engine.execute(CATEGORY, "dataset_mountpoint", dataset=dataset_of(section["pool"]))
    return Path(out.strip()) / ISO_FOLDER


def clean_iso_name(name: Any) -> str:
    """A file name safe in every backend's definition, from what a browser or URL calls it."""
    text = re.sub(r"[^A-Za-z0-9._+-]+", "-", os.path.basename(str(name or ""))).strip("-.")
    if not _ISO.fullmatch(text):
        raise VmError("An install image is a file whose name ends in .iso")
    return text


def isos(engine, config) -> List[Dict[str, Any]]:
    try:
        folder = iso_folder(engine, config)
        return sorted(({"name": entry.name, "size": entry.stat().st_size} for entry in os.scandir(folder)
                       if entry.is_file() and _ISO.fullmatch(entry.name)), key=lambda item: item["name"].lower())
    except (OSError, KeyError, VmError):
        return []


def iso_target(engine, config, name: Any) -> Path:
    """Where a new image goes; a name in use is refused, so nothing is overwritten by accident."""
    clean = clean_iso_name(name)
    target = iso_folder(engine, config) / clean
    if target.exists():
        raise VmError(f"There is already an image named {clean}")
    return target


def download_target(engine, config, url: Any, name: Any = None) -> Path:
    text = str(url or "").strip()
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise VmError("The address of an image starts with http:// or https://")
    return iso_target(engine, config, name or urllib.parse.unquote(parts.path))


def download_iso(engine, config, url: Any, name: Any = None) -> None:
    text = str(url or "").strip()
    target = download_target(engine, config, text, name)
    part = target.with_name(target.name + PART)
    request = urllib.request.Request(text, headers={"User-Agent": "FreedomNAS"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as out:
            while chunk := response.read(CHUNK):
                out.write(chunk)
        part.rename(target)
    finally:
        part.unlink(missing_ok=True)


def remove_iso(engine, config, name: str) -> None:
    if not _ISO.fullmatch(str(name or "")):
        raise VmError("An install image is a file whose name ends in .iso")
    path = iso_folder(engine, config) / name
    if not path.is_file():
        raise VmError(f"No image named {name}")
    path.unlink()


def install_machine(engine, config, name: str, iso: Any) -> None:
    """Start a stopped machine from an image, this once."""
    section = _section_ready(config)
    machine = check_machine(name, _record(section, name))
    if not _ISO.fullmatch(str(iso or "")):
        raise VmError("Choose the image to start from")
    path = iso_folder(engine, config) / iso
    if not path.is_file():
        raise VmError(f"No image named {iso}")
    if _running(engine, name):
        raise VmError(f"{name} is running: stop it first")
    apply_machine(engine, section["pool"], name, machine, str(path))
    try:
        engine.execute(CATEGORY, "install", name=name, iso=iso)
    finally:
        apply_machine(engine, section["pool"], name, machine)
    machine["state"] = "up"
    section["machines"] = {**section["machines"], name: machine}
    config.set_module_config(MODULE_ID, section)


def console_port(engine, config, name: str) -> int:
    """The machine's VNC port, on loopback only, or VmError."""
    _record(_section_ready(config), name)
    if not _running(engine, name):
        raise VmError(f"{name} is not running")
    out, _ = engine.execute(CATEGORY, "vnc_address", name=name)
    match = _VNC.fullmatch(out.strip())
    if not match:
        raise VmError(f"{name} has no console yet")
    return int(match.group(1))


def remove_machine(engine, config, name: str, destroy_disks: bool = False) -> None:
    """The machine goes; its dataset and disks stay unless destroy_disks asks for them too."""
    section = _section_ready(config)
    _record(section, name)
    if _running(engine, name):
        raise VmError(f"{name} is running: stop it first")
    engine.execute(CATEGORY, "undefine", name=name, file=_definition_file(engine, section["pool"], name))
    if destroy_disks:
        engine.execute(CATEGORY, "dataset_destroy", dataset=f"{dataset_of(section['pool'])}/{name}")
    records = dict(section["machines"])
    del records[name]
    section["machines"] = records
    config.set_module_config(MODULE_ID, section)


def _result(action: str, error: Optional[str] = None) -> ActionResult:
    return ActionResult(action=f"vm.{action}", params={},
                        target_state="failed" if error else "applied", error=error)


def ensure_installed(engine, desired: Dict[str, Any]) -> List[ActionResult]:
    """
    The hypervisor installed when the configuration has it set up: at the
    reconcile, and in an installer's chroot before the first boot.
    """
    if not desired.get("enabled") or installed(engine):
        return []
    try:
        install(engine)
    except Exception as exc:
        return [_result("installed", f"The hypervisor could not be installed: {exc}")]
    if not installed(engine):
        return [_result("install"), _result("installed", "The hypervisor is still missing after its installation")]
    return [_result("install")]


def reconcile(engine, desired: Dict[str, Any]) -> List[ActionResult]:
    """The edition's hypervisor back on the configured pool, whichever edition set it up."""
    if not desired.get("enabled"):
        return []
    results = ensure_installed(engine, desired)
    if any(r.target_state == "failed" for r in results):
        return results
    try:
        prepare(engine, check_pool(engine, desired.get("pool")))
    except Exception as exc:
        results.append(_result("hypervisor", str(exc)))
        return results
    if not answers(engine):
        results.append(_result("hypervisor", "The hypervisor does not answer"))
        return results
    for name, machine in sorted((desired.get("machines") or {}).items()):
        try:
            checked = check_machine(name, machine)
            apply_machine(engine, desired["pool"], name, checked)
            if checked["state"] == "up" and not _running(engine, name):
                engine.execute(CATEGORY, "start", name=name)
            results.append(_result(f"machines.{name}"))
        except Exception as exc:
            results.append(_result(f"machines.{name}", str(exc)))
    return results
