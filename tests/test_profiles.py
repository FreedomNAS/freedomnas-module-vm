"""Every command of every vm profile formats with the real engine, and every edition has the same keys."""

import json
import shutil

import pytest

from conftest import PROFILES, REPO
from freedomnas.engine import FreedomNASEngine

EDITIONS = ["arch", "artix", "debian", "freebsd", "gentoo", "void"]
NOT_COMMANDS = {"access_group", "backend", "packages", "init_packages", "services", "definition_suffix",
                "definition_template", "disk_template", "nic_template"}
VALUES = {"dataset": "tank/vm", "zvol": "tank/vm/a/disk0", "size_gb": 8, "file": "/tank/vm/a/a.conf", "bridge": "br0", "name": "a"}


def engine_for(profile_dir, edition):
    FreedomNASEngine._instance = None
    FreedomNASEngine._initialized = False
    try:
        return FreedomNASEngine(profile_dir=str(profile_dir), os_override=edition)
    finally:
        FreedomNASEngine._instance = None
        FreedomNASEngine._initialized = False


@pytest.fixture(scope="module")
def profile_dir(tmp_path_factory):
    target = tmp_path_factory.mktemp("profiles") / "profiles"
    shutil.copytree(PROFILES, target)
    shutil.copytree(REPO / "profiles" / "vm", target / "vm")
    return target


def section(edition):
    return json.loads((REPO / f"profiles/vm/vm-{edition}.json").read_text())["vm"]


@pytest.mark.parametrize("edition", EDITIONS)
def test_every_command_formats(profile_dir, edition):
    engine = engine_for(profile_dir, edition)
    commands = [k for k in section(edition) if not k.startswith("_") and k not in NOT_COMMANDS]
    assert "list" in commands and "configure" in commands
    for action in commands:
        engine.get_command("vm", action, **VALUES)


def test_every_edition_has_the_same_keys():
    keys = set(section("debian"))
    for edition in EDITIONS:
        assert set(section(edition)) == keys, edition
