from xml.etree import ElementTree

import pytest

import vm
from conftest import FakeConfig, FakeEngine


def test_set_up_installs_the_packages_prepares_the_dataset_and_records_only_the_pool(engine):
    engine.failing.add(("vm", "installed_check"))
    config = FakeConfig()
    vm.setup(engine, config, "tank")
    installed = [kw["package_name"] for c, a, kw in engine.calls if a == "install"]
    assert installed == engine.section["packages"]
    assert ("vm", "dataset_ensure", {"dataset": "tank/vm"}) in engine.calls
    assert config.get_module_config("vm") == {"enabled": True, "pool": "tank"}


def test_set_up_refuses_a_pool_that_does_not_exist(engine):
    with pytest.raises(vm.VmError):
        vm.setup(engine, FakeConfig(), "other")
    assert "vm.dataset_ensure" not in engine.actions()


def test_a_pool_name_cannot_carry_a_command(engine):
    with pytest.raises(vm.VmError):
        vm.check_pool(engine, "tank; reboot")


def test_a_stopped_service_is_started_and_a_running_one_left_alone(engine):
    vm.prepare(engine, "tank")
    assert "services.start" not in engine.actions()
    engine.outputs[("services", "is_running")] = "no\n"
    vm.prepare(engine, "tank")
    assert "services.start" in engine.actions()


def test_init_packages_follow_the_init_system():
    engine = FakeEngine("artix")
    engine.init_system = "dinit"
    vm.install(engine)
    assert "libvirt-dinit" in [kw["package_name"] for c, a, kw in engine.calls if a == "install"]


def test_machines_are_read_from_the_list(engine):
    engine.outputs[("vm", "list")] = "win\trunning\ndebian\tstopped\n"
    assert vm.machines(engine) == [{"name": "win", "state": "running"}, {"name": "debian", "state": "stopped"}]


def test_a_hypervisor_that_does_not_answer_is_not_an_empty_list():
    engine = FakeEngine(failing={("vm", "list")})
    assert vm.machines(engine) is None
    assert vm.status(engine, FakeConfig({"enabled": True}))["running"] is False


def test_reconcile_without_a_setup_does_nothing(engine):
    assert vm.reconcile(engine, {}) == []
    assert engine.calls == []


def test_reconcile_rebuilds_the_hypervisor_from_the_pool_alone():
    engine = FakeEngine("freebsd")
    assert vm.reconcile(engine, {"enabled": True, "pool": "tank"}) == []
    assert ("vm", "configure", {"dataset": "tank/vm"}) in engine.calls


def test_reconcile_reports_a_hypervisor_still_missing_after_its_installation():
    engine = FakeEngine(failing={("vm", "installed_check")})
    results = vm.reconcile(engine, {"enabled": True, "pool": "tank"})
    assert [r.action for r in results] == ["vm.install", "vm.installed"]
    assert results[1].target_state == "failed" and "vm.dataset_ensure" not in engine.actions()


def test_reconcile_reports_a_pool_that_is_gone(engine):
    [result] = vm.reconcile(engine, {"enabled": True, "pool": "other"})
    assert result.target_state == "failed" and "other" in result.error


def test_reconcile_waits_for_a_hypervisor_that_has_just_started(monkeypatch, engine):
    monkeypatch.setattr(vm.time, "sleep", lambda seconds: None)
    replies = iter([None, None, []])
    monkeypatch.setattr(vm, "machines", lambda engine: next(replies))
    assert vm.reconcile(engine, {"enabled": True, "pool": "tank"}) == []


def test_reconcile_reports_a_hypervisor_that_does_not_answer(monkeypatch):
    monkeypatch.setattr(vm.time, "sleep", lambda seconds: None)
    engine = FakeEngine(failing={("vm", "list")})
    [result] = vm.reconcile(engine, {"enabled": True, "pool": "tank"})
    assert (result.action, result.target_state) == ("vm.hypervisor", "failed")


UUID = "6f1c2e3a-0b1d-4c2e-8f3a-1234567890ab"
MACHINE = {"cpus": 2, "memory_mb": 2048, "state": "down", "uuid": UUID,
           "disks": [{"size_gb": 20}, {"size_gb": 100}], "nics": [{"bridge": "fnas_bridge1", "mac": "52:54:00:aa:bb:cc"}]}
ASKED = {"cpus": 2, "memory_mb": 2048, "disks": [{"size_gb": 20}], "nics": [{"bridge": "fnas_bridge1"}]}


def ready(records=None):
    return FakeConfig({"enabled": True, "pool": "tank", "machines": records or {}})


def test_the_record_keeps_only_its_neutral_fields():
    assert vm.check_machine("win", {**MACHINE, "zvol": "tank/other", "loader": "grub"}) == MACHINE


@pytest.mark.parametrize("name, change", [
    ("Win 10", {}), ("a;b", {}), ("x" * 32, {}), ("ok", {"cpus": 0}), ("ok", {"cpus": True}), ("ok", {"memory_mb": 1}),
    ("ok", {"state": "paused"}), ("ok", {"uuid": "nope"}), ("ok", {"disks": []}), ("ok", {"disks": [{"size_gb": 0}]}),
    ("ok", {"disks": ["x"]}), ("ok", {"nics": [{"bridge": "br0'/><evil", "mac": "52:54:00:aa:bb:cc"}]}), ("ok", {"nics": [{}]}),
    ("ok", {"nics": [{"bridge": "br0", "mac": "52:54:00:aa:bb:cc'/>"}]}),
])
def test_a_bad_record_is_refused(name, change):
    with pytest.raises(vm.VmError):
        vm.check_machine(name, {**MACHINE, **change})


def test_libvirt_gets_the_standard_hardware():
    root = ElementTree.fromstring(vm.definition(FakeEngine("debian"), "tank", "win", MACHINE))
    assert root.findtext("name") == "win" and root.findtext("uuid") == UUID and root.findtext("vcpu") == "2"
    assert root.find("os").get("firmware") == "efi" and root.find("os/type").get("machine") == "pc"
    assert [d.find("source").get("dev") for d in root.iter("disk")] == [
        "/dev/zvol/tank/vm/win/disk0", "/dev/zvol/tank/vm/win/disk1"]
    assert [d.find("address").get("slot") for d in root.iter("disk")] == ["0x04", "0x05"]
    nic = root.find("devices/interface")
    assert nic.find("address").get("slot") == "0x06" and nic.find("mac").get("address") == "52:54:00:aa:bb:cc"
    assert nic.find("source").get("bridge") == "fnas_bridge1" and nic.find("model").get("type") == "virtio"


def test_vm_bhyve_gets_the_same_identity_and_hardware():
    text = vm.definition(FakeEngine("freebsd"), "tank", "win", MACHINE)
    assert 'loader="uefi"' in text and f'uuid="{UUID}"' in text and "cpu=2" in text and "memory=2048M" in text
    assert 'disk1_name="disk1"' in text and 'disk1_dev="sparse-zvol"' in text
    assert 'network0_switch="fnas_bridge1"' in text and 'network0_mac="52:54:00:aa:bb:cc"' in text


def test_a_new_machine_gets_an_identity_that_stays_in_its_record():
    engine, config = FakeEngine(), ready()
    created = vm.create_machine(engine, config, "win", {**ASKED, "uuid": "ignored", "state": "up"})
    record = config.get_module_config("vm")["machines"]["win"]
    assert record == created and record["state"] == "down" and record["uuid"] != "ignored"
    assert record["nics"][0]["mac"].startswith("52:54:00:")
    assert "vm.define" in engine.actions()


def test_a_machine_cannot_be_created_twice_or_before_the_set_up(engine):
    config = ready({"win": MACHINE})
    with pytest.raises(vm.VmError):
        vm.create_machine(engine, config, "win", ASKED)
    with pytest.raises(vm.VmError):
        vm.create_machine(engine, FakeConfig(), "other", ASKED)
    engine.outputs[("vm", "list")] = "other\tstopped\n"
    with pytest.raises(vm.VmError):
        vm.create_machine(engine, ready(), "other", ASKED)


@pytest.mark.parametrize("action, state", [("start", "up"), ("stop", "down"), ("poweroff", "down")])
def test_an_action_is_kept_as_the_state_to_restore(engine, action, state):
    config = ready({"win": {**MACHINE, "state": "up" if state == "down" else "down"}})
    vm.machine_action(engine, config, "win", action)
    assert ("vm", action, {"name": "win"}) in engine.calls
    assert config.get_module_config("vm")["machines"]["win"]["state"] == state


def test_a_failed_action_does_not_change_the_state():
    engine, config = FakeEngine(failing={("vm", "start")}), ready({"win": MACHINE})
    with pytest.raises(Exception):
        vm.machine_action(engine, config, "win", "start")
    assert config.get_module_config("vm")["machines"]["win"]["state"] == "down"


def test_an_unknown_action_is_refused(engine):
    with pytest.raises(vm.VmError):
        vm.machine_action(engine, ready({"win": MACHINE}), "win", "reboot; rm")


def test_removing_a_machine_keeps_its_disks_unless_asked(engine):
    config = ready({"win": MACHINE})
    vm.remove_machine(engine, config, "win")
    assert "vm.undefine" in engine.actions() and "vm.dataset_destroy" not in engine.actions()
    assert config.get_module_config("vm")["machines"] == {}
    config = ready({"win": MACHINE})
    vm.remove_machine(engine, config, "win", destroy_disks=True)
    assert ("vm", "dataset_destroy", {"dataset": "tank/vm/win"}) in engine.calls


def test_a_running_machine_is_not_removed(engine):
    engine.outputs[("vm", "list")] = "win\trunning\n"
    config = ready({"win": MACHINE})
    with pytest.raises(vm.VmError):
        vm.remove_machine(engine, config, "win", destroy_disks=True)
    assert "vm.undefine" not in engine.actions() and "vm.dataset_destroy" not in engine.actions()


def test_reconcile_builds_the_machine_and_starts_it_only_when_up():
    engine = FakeEngine("freebsd")
    desired = {"enabled": True, "pool": "tank", "machines": {"win": MACHINE, "linux": {**MACHINE, "state": "up"}}}
    results = vm.reconcile(engine, desired)
    assert [(r.action, r.target_state) for r in results] == [("vm.machines.linux", "applied"), ("vm.machines.win", "applied")]
    assert ("vm", "disk_ensure", {"zvol": "tank/vm/win/disk1", "size_gb": 100}) in engine.calls
    assert [kw["name"] for c, a, kw in engine.calls if a == "start"] == ["linux"]


def test_reconcile_leaves_a_running_machine_alone():
    engine = FakeEngine()
    engine.outputs[("vm", "list")] = "win\trunning\n"
    vm.reconcile(engine, {"enabled": True, "pool": "tank", "machines": {"win": {**MACHINE, "state": "up"}}})
    assert "vm.start" not in engine.actions()


def test_one_bad_machine_does_not_stop_the_others():
    desired = {"enabled": True, "pool": "tank", "machines": {"bad": {"cpus": 0}, "good": MACHINE}}
    results = vm.reconcile(FakeEngine(), desired)
    assert [r.target_state for r in results] == ["failed", "applied"]


def test_status_shows_records_and_machines_it_did_not_make(engine):
    engine.outputs[("vm", "list")] = "win\trunning\nother\tstopped\n"
    shown = vm.status(engine, ready({"win": MACHINE}))["machines"]
    assert [(m["name"], m["state"], m.get("foreign", False)) for m in shown] == [("win", "running", False), ("other", "stopped", True)]


def test_set_up_puts_its_administrator_in_the_access_group_and_not_in_its_record(engine):
    engine.outputs[("users", "group_exists")] = "libvirt:x:79:\n"
    config = FakeConfig(users=[{"username": "sarah", "groups": [], "admin": True},
                               {"username": "mario", "groups": []}])
    vm.setup(engine, config, "tank", "sarah")
    assert ("users", "add_to_group", {"username": "sarah", "group": "libvirt"}) in engine.calls
    assert config.get_user("sarah")["groups"] == []
    shown = vm.status(engine, config)
    assert shown["access_group"] == "libvirt" and shown["access_members"] == ["sarah"]


def test_an_account_that_does_not_administer_never_joins_the_access_group(engine):
    engine.outputs[("users", "group_exists")] = "libvirt:x:79:\n"
    config = FakeConfig(users=[{"username": "mario", "groups": []}])
    vm.grant_access(engine, config, "mario")
    assert "users.add_to_group" not in engine.actions()


def test_an_account_already_in_the_group_is_left_alone(engine):
    engine.outputs[("users", "group_exists")] = "libvirt:x:79:\n"
    engine.outputs[("users", "list_groups")] = "sarah wheel libvirt"
    config = FakeConfig(users=[{"username": "sarah", "groups": [], "admin": True}])
    vm.grant_access(engine, config, "sarah")
    assert "users.add_to_group" not in engine.actions()


def test_a_missing_group_or_a_failure_does_not_stop_the_set_up_or_touch_the_record():
    config = FakeConfig(users=[{"username": "sarah", "groups": [], "admin": True}])
    engine = FakeEngine()
    vm.setup(engine, config, "tank", "sarah")
    assert "users.add_to_group" not in engine.actions() and config.get_user("sarah")["groups"] == []
    engine = FakeEngine(failing={("users", "add_to_group")})
    engine.outputs[("users", "group_exists")] = "libvirt:x:79:\n"
    vm.setup(engine, config, "tank", "sarah")
    assert config.get_module_config("vm")["enabled"] and config.get_user("sarah")["groups"] == []


def test_freebsd_has_no_access_group():
    engine, config = FakeEngine("freebsd"), FakeConfig(users=[{"username": "sarah", "groups": []}])
    vm.setup(engine, config, "tank", "sarah")
    assert "users.add_to_group" not in engine.actions() and vm.status(engine, config)["access_group"] is None


def test_status_gives_the_page_what_it_shows(engine):
    engine.outputs[("vm", "list")] = "win\trunning\n"
    engine.outputs[("vm", "bridges_list")] = "fnas_bridge1\nbr0\n"
    status = vm.status(engine, ready({"win": MACHINE}))
    assert status["bridges"] == ["br0", "fnas_bridge1"]
    [win] = status["machines"]
    assert win["dataset"] == "tank/vm/win" and win["nics"] == MACHINE["nics"]


def test_status_passes_the_console_note_of_the_profile(engine):
    status = vm.status(engine, ready({"win": MACHINE}))
    assert "console_note" in status and "console_note_code" in status


def test_a_machine_told_to_stop_that_still_runs_is_stopping(engine):
    engine.outputs[("vm", "list")] = "win\trunning\nother\trunning\n"
    records = {"win": MACHINE, "other": {**MACHINE, "state": "up"}}
    shown = {m["name"]: m for m in vm.status(engine, ready(records))["machines"]}
    assert shown["win"]["stopping"] is True and shown["win"]["state"] == "running"
    assert shown["other"]["stopping"] is False


@pytest.fixture
def store(tmp_path):
    (tmp_path / ".iso").mkdir()
    engine = FakeEngine()
    engine.outputs[("vm", "dataset_mountpoint")] = str(tmp_path) + "\n"
    return engine, tmp_path / ".iso"


@pytest.mark.parametrize("given, clean", [
    ("debian 13 (netinst).ISO", "debian-13-netinst-.ISO"), ("/x/y/alpine.iso", "alpine.iso"), ("../../etc/a.iso", "a.iso"),
])
def test_an_image_name_is_made_safe(given, clean):
    assert vm.clean_iso_name(given) == clean


@pytest.mark.parametrize("given", ["", "a.txt", "..iso", "a.iso.sh"])
def test_only_iso_files_are_images(given):
    with pytest.raises(vm.VmError):
        vm.clean_iso_name(given)


def test_images_are_listed_without_unfinished_ones(store):
    engine, folder = store
    (folder / "b.iso").write_bytes(b"12")
    (folder / "a.iso").write_bytes(b"1")
    (folder / "c.iso.part").write_bytes(b"123")
    assert vm.isos(engine, ready()) == [{"name": "a.iso", "size": 1}, {"name": "b.iso", "size": 2}]


def test_an_image_is_not_overwritten_and_removal_stays_in_the_folder(store):
    engine, folder = store
    (folder / "a.iso").write_bytes(b"1")
    config = ready()
    with pytest.raises(vm.VmError):
        vm.iso_target(engine, config, "a.iso")
    with pytest.raises(vm.VmError):
        vm.remove_iso(engine, config, "../a.iso")
    vm.remove_iso(engine, config, "a.iso")
    assert not (folder / "a.iso").exists()


@pytest.mark.parametrize("url", ["ftp://x/a.iso", "file:///etc/a.iso", "a.iso", ""])
def test_an_image_comes_only_from_http(store, url):
    with pytest.raises(vm.VmError):
        vm.download_iso(store[0], ready(), url)


def test_starting_from_an_image_is_only_this_once(store):
    engine, folder = store
    (folder / "a.iso").write_bytes(b"1")
    config = ready({"win": MACHINE})
    vm.install_machine(engine, config, "win", "a.iso")
    writes = [c for c in engine.calls if c[1] == "definition_write"]
    assert len(writes) == 2 and "a.iso" not in engine.stdin
    assert ("vm", "install", {"name": "win", "iso": "a.iso"}) in engine.calls
    assert config.get_module_config("vm")["machines"]["win"]["state"] == "up"
    assert "iso" not in config.get_module_config("vm")["machines"]["win"]


def test_libvirt_boots_the_image_first_and_the_disks_after():
    root = ElementTree.fromstring(vm.definition(FakeEngine("debian"), "tank", "win", MACHINE, "/tank/vm/.iso/a.iso"))
    cdrom, *disks = list(root.iter("disk"))
    assert cdrom.get("device") == "cdrom" and cdrom.find("source").get("file") == "/tank/vm/.iso/a.iso"
    assert [d.find("boot").get("order") for d in [cdrom, *disks]] == ["1", "2", "3"]
    assert [d.find("address").get("slot") for d in disks] == ["0x04", "0x05"]
    assert not list(ElementTree.fromstring(vm.definition(FakeEngine("debian"), "tank", "win", MACHINE)).iter("readonly"))


def test_bhyve_conf_never_carries_the_image():
    assert "a.iso" not in vm.definition(FakeEngine("freebsd"), "tank", "win", MACHINE, "/tank/vm/.iso/a.iso")


@pytest.mark.parametrize("change", [{"name": "ghost"}, {"iso": "none.iso"}, {"iso": "../a.iso"}, {"iso": None}])
def test_a_bad_start_from_an_image_is_refused(store, change):
    engine, folder = store
    (folder / "a.iso").write_bytes(b"1")
    asked = {"name": "win", "iso": "a.iso", **change}
    with pytest.raises(vm.VmError):
        vm.install_machine(engine, ready({"win": MACHINE}), asked["name"], asked["iso"])
    assert "vm.install" not in engine.actions()


def test_a_running_machine_is_not_started_from_an_image(store):
    engine, folder = store
    (folder / "a.iso").write_bytes(b"1")
    engine.outputs[("vm", "list")] = "win\trunning\n"
    with pytest.raises(vm.VmError):
        vm.install_machine(engine, ready({"win": MACHINE}), "win", "a.iso")


def test_the_console_only_reaches_loopback(engine):
    config = ready({"win": MACHINE})
    engine.outputs[("vm", "list")] = "win\trunning\n"
    engine.outputs[("vm", "vnc_address")] = "127.0.0.1:5901\n"
    assert vm.console_port(engine, config, "win") == 5901
    engine.outputs[("vm", "vnc_address")] = "192.168.1.9:5901\n"
    with pytest.raises(vm.VmError):
        vm.console_port(engine, config, "win")


def test_a_stopped_or_unknown_machine_has_no_console(engine):
    with pytest.raises(vm.VmError):
        vm.console_port(engine, ready({"win": MACHINE}), "win")
    with pytest.raises(vm.VmError):
        vm.console_port(engine, ready(), "other")
