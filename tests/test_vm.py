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


def test_reconcile_reports_a_hypervisor_that_does_not_answer():
    engine = FakeEngine(failing={("vm", "list")})
    [result] = vm.reconcile(engine, {"enabled": True, "pool": "tank"})
    assert (result.action, result.target_state) == ("vm.hypervisor", "failed")


MACHINE = {"cpus": 2, "memory_mb": 2048, "firmware": "uefi",
           "disks": [{"size_gb": 20}, {"size_gb": 100}], "nics": [{"bridge": "fnas_bridge1"}]}


def test_the_record_keeps_only_its_neutral_fields():
    checked = vm.check_machine("win", {**MACHINE, "zvol": "tank/other", "loader": "grub"})
    assert checked == MACHINE


@pytest.mark.parametrize("name, change", [
    ("Win 10", {}), ("a;b", {}), ("x" * 32, {}), ("ok", {"cpus": 0}), ("ok", {"cpus": True}), ("ok", {"memory_mb": 1}),
    ("ok", {"firmware": "grub"}), ("ok", {"disks": []}), ("ok", {"disks": [{"size_gb": 0}]}), ("ok", {"disks": ["x"]}),
    ("ok", {"nics": [{"bridge": "br0'/><evil"}]}), ("ok", {"nics": [{}]}),
])
def test_a_bad_record_is_refused(name, change):
    with pytest.raises(vm.VmError):
        vm.check_machine(name, {**MACHINE, **change})


def test_libvirt_gets_its_xml_from_the_neutral_record():
    text = vm.definition(FakeEngine("debian"), "tank", "win", MACHINE)
    root = ElementTree.fromstring(text)
    assert root.findtext("name") == "win" and root.findtext("vcpu") == "2" and root.findtext("memory") == "2048"
    assert root.find("os").get("firmware") == "efi"
    assert [d.find("source").get("dev") for d in root.iter("disk")] == [
        "/dev/zvol/tank/vm/win/disk0", "/dev/zvol/tank/vm/win/disk1"]
    assert [d.find("target").get("dev") for d in root.iter("disk")] == ["vda", "vdb"]
    assert root.find("devices/interface/source").get("bridge") == "fnas_bridge1"


def test_vm_bhyve_gets_its_conf_from_the_same_record():
    text = vm.definition(FakeEngine("freebsd"), "tank", "win", MACHINE)
    assert 'loader="uefi"' in text and "cpu=2" in text and "memory=2048M" in text
    assert 'disk1_name="disk1"' in text and 'disk1_dev="sparse-zvol"' in text
    assert 'network0_switch="fnas_bridge1"' in text


def test_reconcile_builds_the_machine_and_keeps_existing_disks_by_command():
    engine = FakeEngine("freebsd")
    results = vm.reconcile(engine, {"enabled": True, "pool": "tank", "machines": {"win": MACHINE}})
    assert [(r.action, r.target_state) for r in results] == [("vm.machines.win", "applied")]
    assert ("vm", "disk_ensure", {"zvol": "tank/vm/win/disk1", "size_gb": 100}) in engine.calls
    assert engine.section["disk_ensure"].startswith("zfs list")


def test_a_firmware_this_edition_lacks_is_a_warning_and_keeps_the_disks():
    engine = FakeEngine("freebsd")
    desired = {"enabled": True, "pool": "tank", "machines": {"old": {**MACHINE, "firmware": "bios"}}}
    [result] = vm.reconcile(engine, desired)
    assert result.target_state == "warning" and "bios" in result.error
    assert "vm.disk_ensure" in engine.actions() and "vm.definition_write" not in engine.actions()


def test_one_bad_machine_does_not_stop_the_others():
    desired = {"enabled": True, "pool": "tank", "machines": {"bad": {"cpus": 0}, "good": MACHINE}}
    results = vm.reconcile(FakeEngine(), desired)
    assert [r.target_state for r in results] == ["failed", "applied"]
