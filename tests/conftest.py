import json
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CORE = REPO.parent / "freedomnas-core"
PROFILES = REPO.parent / "freedomnas-profiles"
_LINK = Path(tempfile.mkdtemp(prefix="fnas-core-")) / "freedomnas"
_LINK.symlink_to(CORE)
sys.path.insert(0, str(_LINK.parent))

from freedomnas.engine import CommandExecutionError  # noqa: E402

sys.path.insert(0, str(REPO / "modules" / "vm"))


class FakeConfig:
    def __init__(self, section=None):
        self.modules = {"vm": dict(section or {})}

    def get_module_config(self, module_id):
        return json.loads(json.dumps(self.modules.get(module_id, {})))

    def set_module_config(self, module_id, section):
        self.modules[module_id] = json.loads(json.dumps(section))


class FakeEngine:
    """The Debian profile's values; commands are recorded and `failing` makes one raise."""

    init_system = "systemd"

    def __init__(self, edition="debian", failing=()):
        self.section = json.loads((REPO / f"profiles/vm/vm-{edition}.json").read_text())["vm"]
        self.failing = set(failing)
        self.calls = []
        self.outputs = {("zfs", "pool_list"): "tank\t1\t2\n", ("services", "is_running"): "yes\n"}

    def get_profile_section(self, category):
        return self.section

    def execute(self, category, action, **kwargs):
        self.calls.append((category, action, kwargs))
        if (category, action) in self.failing:
            raise CommandExecutionError(f"{category}.{action} failed")
        return self.outputs.get((category, action), ""), ""

    def actions(self):
        return [f"{c}.{a}" for c, a, _ in self.calls]


@pytest.fixture
def engine():
    return FakeEngine()
