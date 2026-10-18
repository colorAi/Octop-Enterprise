import json

from octop.config import load_config


def test_device_channel_feature_flag_and_environment_override(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"device_channel_enabled": True}))
    assert load_config(path).device_channel_enabled
    monkeypatch.setenv("OCTOP_DEVICE_CHANNEL_ENABLED", "false")
    assert not load_config(path).device_channel_enabled
    monkeypatch.setenv("OCTOP_DEVICE_CHANNEL_ENABLED", "1")
    assert load_config(path).device_channel_enabled
