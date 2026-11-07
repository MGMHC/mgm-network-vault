"""Per-switch scans pick their commands from the device platform and survive firmware differences."""
from network_vault import health, optics
from network_vault.models import Device


def test_health_runs_the_platform_commands_and_rates_the_result(app, seed, switches):
    with app.app_context():
        d = Device.query.get(seed["dev"]["core"][0])  # cisco_ios
        switches.script[(d.name, "show processes cpu | include CPU")] = "CPU utilization for five seconds: 95%/1%; one minute: 93%;"
        switches.script[(d.name, "show env all")] = RuntimeError("% Invalid input")  # optional on IOS
        r = health.query_device_health(d)
    assert switches.cmds(d.name) == ["show processes cpu | include CPU", "show processes memory | include Processor",
                                     "show env all"]
    assert r["cpu_pct"] == 93 and r["overall_status"] == "bad"


def test_firewall_health_uses_sonicos_status(app, seed, switches):
    with app.app_context():
        d = Device.query.get(seed["dev"]["fw"][0])
        r = health.query_device_health(d)
    assert switches.cmds(d.name) == ["show status"] and r["overall_status"] == "ok"


def test_ios_optics_falls_back_to_the_short_command(app, seed, switches):
    with app.app_context():
        d = Device.query.get(seed["dev"]["core"][0])
        switches.script[(d.name, "show interfaces transceiver detail")] = RuntimeError("% Invalid input")
        assert optics.query_device_optics(d) == []
    assert switches.cmds(d.name) == ["show interfaces transceiver detail", "show interfaces transceiver"]
