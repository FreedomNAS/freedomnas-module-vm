"""
The hypervisor of the virtual machines module, from the edition's profile:
libvirt with QEMU/KVM on Linux, vm-bhyve on FreeBSD. Nothing here knows which
one it is talking to.

config.yaml (modules.vm) holds what was asked for, never how a backend does it:

"enabled": true once set up,
"pool": the data pool whose <pool>/vm dataset holds the virtual machines,
"machines": {name: {"cpus", "memory_mb", "firmware": "uefi"|"bios",
             "disks": [{"size_gb"}], "nics": [{"bridge"}]}}.

The reconciler rebuilds the rest on any edition from those values: a dataset
per machine, a sparse zvol per disk that is missing (it never resizes or removes
one), and the backend's own definition, written from the profile's templates. A
firmware the edition does not offer leaves that machine undefined there, with a
warning; its disks stay. A profile's "list" prints one
"<name><TAB>running|stopped" per machine and fails when the hypervisor does not
answer.
"""

from __future__ import annotations

import logging
import re
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
FIRMWARES = ("uefi", "bios")
MAX_CPUS = 256
MAX_MEMORY_MB = 16 * 1024 * 1024
MIN_MEMORY_MB = 64
MAX_DISKS = 16
MAX_NICS = 8
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
    for name in value(engine, "services", []):
        engine.execute("services", "enable", service_name=name)
        out, _ = engine.execute("services", "is_running", check=False, service_name=name)
        if out.strip() != "yes":
            engine.execute("services", "start", service_name=name)


def enabled(config) -> bool:
    return bool((config.get_module_config(MODULE_ID) or {}).get("enabled"))


def setup(engine, config, pool: str) -> None:
    pool = check_pool(engine, pool)
    if not installed(engine):
        install(engine)
    prepare(engine, pool)
    section = dict(config.get_module_config(MODULE_ID) or {})
    section.update(enabled=True, pool=pool)
    config.set_module_config(MODULE_ID, section)


def status(engine, config) -> Dict[str, Any]:
    section = config.get_module_config(MODULE_ID) or {}
    is_installed = installed(engine)
    listed = machines(engine) if is_installed and section.get("enabled") else None
    return {
        "backend": value(engine, "backend", ""),
        "installed": is_installed,
        "enabled": bool(section.get("enabled")),
        "running": listed is not None,
        "accelerated": accelerated(engine) if is_installed else None,
        "pool": section.get("pool"),
        "pools": pools(engine),
        "machines": listed or [],
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
    if machine.get("firmware") not in FIRMWARES:
        raise VmError(f"firmware is one of: {', '.join(FIRMWARES)}")
    disks, nics = machine.get("disks"), machine.get("nics", [])
    if not isinstance(disks, list) or not 1 <= len(disks) <= MAX_DISKS:
        raise VmError(f"A machine has from 1 to {MAX_DISKS} disks")
    if not isinstance(nics, list) or len(nics) > MAX_NICS:
        raise VmError(f"A machine has up to {MAX_NICS} networks")
    for nic in nics:
        if not isinstance(nic, dict) or not _BRIDGE.fullmatch(str(nic.get("bridge") or "")):
            raise VmError("A network names the bridge it is on")
    return {
        "cpus": _number(machine, "cpus", 1, MAX_CPUS),
        "memory_mb": _number(machine, "memory_mb", MIN_MEMORY_MB, MAX_MEMORY_MB),
        "firmware": machine["firmware"],
        "disks": [{"size_gb": _number(d if isinstance(d, dict) else {}, "size_gb", 1, MAX_DISK_GB)} for d in disks],
        "nics": [{"bridge": nic["bridge"]} for nic in nics],
    }


def definition(engine, pool: str, name: str, machine: Dict[str, Any]) -> Optional[str]:
    """The backend's definition of a machine, or None when this edition lacks its firmware."""
    firmware = (value(engine, "firmwares", {}) or {}).get(machine["firmware"])
    if firmware is None:
        return None
    base = f"{dataset_of(pool)}/{name}"
    disks = "".join(
        value(engine, "disk_template").format(index=index, letter=chr(ord("a") + index), zvol=f"{base}/disk{index}")
        for index in range(len(machine["disks"])))
    nics = "".join(
        value(engine, "nic_template").format(index=index, bridge=nic["bridge"])
        for index, nic in enumerate(machine["nics"]))
    return value(engine, "definition_template").format(
        name=name, cpus=machine["cpus"], memory_mb=machine["memory_mb"], firmware=firmware, disks=disks, nics=nics)


def apply_machine(engine, pool: str, name: str, machine: Dict[str, Any]) -> bool:
    """A machine's dataset, missing disks and definition; False when this edition cannot define it."""
    base = f"{dataset_of(pool)}/{name}"
    engine.execute(CATEGORY, "dataset_ensure", dataset=base)
    for index, disk in enumerate(machine["disks"]):
        engine.execute(CATEGORY, "disk_ensure", zvol=f"{base}/disk{index}", size_gb=disk["size_gb"])
    text = definition(engine, pool, name, machine)
    if text is None:
        return False
    for nic in machine["nics"]:
        engine.execute(CATEGORY, "bridge_ensure", bridge=nic["bridge"])
    out, _ = engine.execute(CATEGORY, "dataset_mountpoint", dataset=base)
    file = f"{out.strip()}/{name}.{value(engine, 'definition_suffix')}"
    engine.execute_with_stdin(CATEGORY, "definition_write", text, file=file)
    engine.execute(CATEGORY, "define", file=file)
    return True


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
    if machines(engine) is None:
        results.append(_result("hypervisor", "The hypervisor does not answer"))
        return results
    for name, machine in sorted((desired.get("machines") or {}).items()):
        try:
            checked = check_machine(name, machine)
            if apply_machine(engine, desired["pool"], name, checked):
                results.append(_result(f"machines.{name}"))
            else:
                results.append(ActionResult(
                    action=f"vm.machines.{name}", params={}, target_state="warning",
                    error=(f"{name} uses {checked['firmware']} firmware, which this edition does not offer: "
                           "it is not defined here and its disks are kept")))
        except Exception as exc:
            results.append(_result(f"machines.{name}", str(exc)))
    return results
