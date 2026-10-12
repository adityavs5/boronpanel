from daemon import appunits


def test_enable_start_app_restarts_an_active_legacy_process(monkeypatch):
    calls = []

    class Result:
        stdout = "active\n"

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return Result()

    monkeypatch.setattr(appunits, "enable_start", lambda name: calls.append(["enable-start", name]))
    monkeypatch.setattr(appunits, "run", fake_run)
    monkeypatch.setattr(appunits, "restart", lambda name: calls.append(["restart", name]))
    monkeypatch.setattr(appunits, "start", lambda name: calls.append(["start", name]))

    appunits.enable_start_app("boron-node-demo-1.service")

    assert calls == [
        ["enable-start", "boron-node-demo-1.socket"],
        ["systemctl", "enable", "boron-node-demo-1.service"],
        ["systemctl", "is-active", "boron-node-demo-1.service"],
        ["restart", "boron-node-demo-1.service"],
    ]


def test_enable_start_app_starts_an_inactive_service(monkeypatch):
    calls = []

    class Result:
        stdout = "inactive\n"

    monkeypatch.setattr(appunits, "enable_start", lambda name: calls.append(["enable-start", name]))
    monkeypatch.setattr(appunits, "run", lambda argv, **kwargs: (calls.append(argv), Result())[1])
    monkeypatch.setattr(appunits, "restart", lambda name: calls.append(["restart", name]))
    monkeypatch.setattr(appunits, "start", lambda name: calls.append(["start", name]))

    appunits.enable_start_app("boron-python-demo-1.service")

    assert calls[-1] == ["start", "boron-python-demo-1.service"]
