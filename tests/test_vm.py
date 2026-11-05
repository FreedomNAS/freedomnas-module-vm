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
