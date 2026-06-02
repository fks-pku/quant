import builtins

from quant.shared.utils.config_loader import ConfigLoader


def test_config_loader_reads_utf8_yaml_when_system_default_is_not_utf8(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_bytes(
        "data:\n  label: 中文\n  tushare:\n    token: abc\n".encode("utf-8")
    )
    real_open = builtins.open

    def ascii_default_open(file, mode="r", *args, **kwargs):
        if "b" not in mode and kwargs.get("encoding") is None:
            kwargs["encoding"] = "ascii"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", ascii_default_open)

    config = ConfigLoader(str(config_dir)).load("config.yaml")

    assert config["data"]["label"] == "中文"
    assert config["data"]["tushare"]["token"] == "abc"
