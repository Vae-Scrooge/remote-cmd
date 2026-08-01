"""配置管理模块测试"""

import json
from pathlib import Path

import yaml

from remote_cmd.utils.config import (
    get_default_config,
    get_default_config_path,
    load_config,
    save_config,
    validate_config,
)


class TestGetDefaultConfigPath:
    """默认配置路径搜索测试"""

    def test_returns_yaml_in_cwd(self, tmp_path, monkeypatch):
        """测试：当前目录存在 config.yaml 时优先返回"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text("a: 1")
        assert get_default_config_path() == "config.yaml"

    def test_returns_json_in_cwd(self, tmp_path, monkeypatch):
        """测试：无 config.yaml 但有 config.json 时返回 json"""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text("{}")
        assert get_default_config_path() == "config.json"

    def test_returns_home_config(self, tmp_path, monkeypatch):
        """测试：当前目录无配置文件时检查主目录 ~/.remote_cmd"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / ".remote_cmd" / "config.yaml").parent.mkdir(parents=True)
        (tmp_path / ".remote_cmd" / "config.yaml").write_text("a: 1")
        assert get_default_config_path() == str(tmp_path / ".remote_cmd" / "config.yaml")

    def test_returns_default_when_none_exist(self, tmp_path, monkeypatch):
        """测试：所有位置均无配置文件时返回默认相对路径"""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert get_default_config_path() == "config.yaml"


class TestLoadConfig:
    """配置加载测试"""

    def test_missing_file_returns_default(self, tmp_path):
        """测试：文件不存在时返回默认配置"""
        path = tmp_path / "nope.yaml"
        config = load_config(str(path))
        assert config == get_default_config()

    def test_load_yaml(self, tmp_path):
        """测试：加载 YAML 配置"""
        path = tmp_path / "conf.yaml"
        path.write_text("hosts_file: h.yaml\nport: 22\n", encoding="utf-8")
        config = load_config(str(path))
        assert config == {"hosts_file": "h.yaml", "port": 22}

    def test_load_yml_extension(self, tmp_path):
        """测试：加载 .yml 扩展名配置"""
        path = tmp_path / "conf.yml"
        path.write_text("a: 1", encoding="utf-8")
        assert load_config(str(path)) == {"a": 1}

    def test_load_empty_yaml_returns_dict(self, tmp_path):
        """测试：空 YAML 文件返回空字典而非 None"""
        path = tmp_path / "empty.yaml"
        path.write_text("", encoding="utf-8")
        assert load_config(str(path)) == {}

    def test_load_json(self, tmp_path):
        """测试：加载 JSON 配置"""
        path = tmp_path / "conf.json"
        path.write_text(json.dumps({"hosts_file": "h.json", "port": 22}), encoding="utf-8")
        assert load_config(str(path)) == {"hosts_file": "h.json", "port": 22}

    def test_unsupported_extension_raises(self, tmp_path):
        """测试：不支持的文件格式抛出 ValueError"""
        path = tmp_path / "conf.toml"
        path.write_text("a = 1", encoding="utf-8")
        try:
            load_config(str(path))
            raise AssertionError("应抛出 ValueError")
        except ValueError as e:
            assert "unsupported config file format" in str(e)


class TestSaveConfig:
    """配置保存测试"""

    def test_save_yaml(self, tmp_path):
        """测试：保存为 YAML 并包含默认流样式"""
        path = tmp_path / "out.yaml"
        save_config({"hosts_file": "h.yaml"}, str(path))
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert content == {"hosts_file": "h.yaml"}

    def test_save_yaml_creates_parent_dir(self, tmp_path):
        """测试：目标目录不存在时自动创建"""
        path = tmp_path / "sub" / "deep" / "out.yml"
        save_config({"a": 1}, str(path))
        assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"a": 1}

    def test_save_json(self, tmp_path):
        """测试：保存为 JSON 且保留中文"""
        path = tmp_path / "out.json"
        save_config({"name": "主机"}, str(path))
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content == {"name": "主机"}

    def test_save_unsupported_extension_raises(self, tmp_path):
        """测试：不支持的保存格式抛出 ValueError"""
        path = tmp_path / "out.toml"
        try:
            save_config({"a": 1}, str(path))
            raise AssertionError("应抛出 ValueError")
        except ValueError as e:
            assert "unsupported config file format" in str(e)


class TestGetDefaultConfig:
    """默认配置测试"""

    def test_defaults(self):
        """测试：默认配置项完整"""
        config = get_default_config()
        assert config == {
            "hosts_file": "hosts.json",
            "default_ssh_port": 22,
            "default_timeout": 30,
            "log_level": "INFO",
        }


class TestValidateConfig:
    """配置验证测试"""

    def test_valid_config(self):
        """测试：合法配置返回 True"""
        assert validate_config({"default_ssh_port": 22, "default_timeout": 30, "log_level": "info"})

    def test_port_out_of_range(self):
        """测试：端口超出范围返回 False"""
        assert not validate_config({"default_ssh_port": 0})
        assert not validate_config({"default_ssh_port": 70000})

    def test_port_wrong_type(self):
        """测试：端口类型错误返回 False"""
        assert not validate_config({"default_ssh_port": "22"})

    def test_timeout_non_positive(self):
        """测试：超时时间非正数返回 False"""
        assert not validate_config({"default_timeout": 0})
        assert not validate_config({"default_timeout": -5})

    def test_timeout_wrong_type(self):
        """测试：超时时间类型错误返回 False"""
        assert not validate_config({"default_timeout": "30"})

    def test_invalid_log_level(self):
        """测试：非法日志级别返回 False"""
        assert not validate_config({"log_level": "VERBOSE"})

    def test_empty_config_valid(self):
        """测试：空配置返回 True"""
        assert validate_config({})

    def test_log_level_lowercase_normalized(self):
        """测试：小写日志级别经 upper 归一化后通过"""
        assert validate_config({"log_level": "info"})
