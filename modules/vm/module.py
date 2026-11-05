"""
VmModule — virtual machines on the NAS.

The work is done in vm.py, shared with api_routes.py; both load it by file
path, as freedomnas-core loads this file, so the module needs no importable
package.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import List, Tuple

try:
    from freedomnas.orchestrator import FreedomNASModule
except ImportError:
    from orchestrator import FreedomNASModule  # type: ignore[no-redef]

logger = logging.getLogger("FreedomNAS.Module.Vm")


def load(stem: str):
    name = f"freedomnas_vm_{stem}"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{stem}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


vm = load("vm")


class VmModule(FreedomNASModule):
    """The edition's hypervisor set up on a pool, its virtual machines listed."""

    @property
    def name(self) -> str:
        return vm.MODULE_ID

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def health_check(self) -> bool:
        return self.health()[0]

    def health(self) -> Tuple[bool, str]:
        try:
            if not vm.enabled(self._config):
                return True, "Virtual machines not set up"
            machines = vm.machines(self._engine)
            if machines is None:
                return False, "The hypervisor does not answer"
            return True, f"{len(machines)} virtual machines"
        except Exception as exc:
            logger.warning("VM health check failed: %s", exc)
            return False, str(exc)

    def reconcile(self, desired: dict, mode: str = "declarative") -> List:
        return vm.reconcile(self._engine, desired)

    def prepare(self, desired: dict) -> List:
        return vm.ensure_installed(self._engine, desired)
