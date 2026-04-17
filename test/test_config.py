"""Config / cfg 行为与边界测试。"""

from __future__ import annotations
import importlib
from pathlib import Path
import sys

from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict
import pytest
import yaml

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _import_subgrade_config_fresh() -> object:
    for name in list(sys.modules):
        if name == "subgrade.config" or name.startswith("subgrade._cfgmod."):
            del sys.modules[name]
    return importlib.import_module("subgrade.config")


def _cfg_getattr_fails(cfg_obj: object, name: str) -> bool:
    try:
        getattr(cfg_obj, name)
    except AttributeError:
        return True
    return False


# --- 基础：相对路径 vs 环境变量 ---


def test_config_resolves_relative_dirs_without_env(
    project_root_without_env: Path,
) -> None:
    cfg_mod = _import_subgrade_config_fresh()
    assert Path.cwd() == project_root_without_env
    assert cfg_mod.cfg.app.label == "from-yaml"


def test_config_resolves_paths_via_env_vars(project_root_with_env: Path) -> None:
    cfg_mod = _import_subgrade_config_fresh()
    assert Path.cwd() == project_root_with_env
    assert cfg_mod.cfg.app.label == "from-yaml"


# --- 1. 同一 module 不能定义两个 Config ---


def test_two_config_classes_in_one_module_raise_type_error(
    dup_module_env: Path,
) -> None:
    assert Path.cwd() == dup_module_env
    cfg_mod = _import_subgrade_config_fresh()
    with pytest.raises(TypeError, match="only define one Config subclass"):
        _ = cfg_mod.cfg.dup


# --- 2. 环境变量指向非默认目录名（非 configs / settings），仍能读取 ---


def test_custom_cfg_and_settings_dirs_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    root = FIXTURES / "custom_paths"
    monkeypatch.chdir(root)
    monkeypatch.setenv("CFG_BASEDIR", str((root / "my_labs" / "cfg").resolve()))
    monkeypatch.setenv("SETTINGS_BASEDIR", str((root / "my_labs" / "st").resolve()))
    cfg_mod = _import_subgrade_config_fresh()
    assert cfg_mod.cfg.app.label == "from-custom-env-path"


# --- 3. 不存在 yaml 时自动生成 ---


def test_missing_yaml_file_is_auto_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    (root / "configs").mkdir()
    (root / "configs" / "solo.py").write_text(
        "from subgrade.config import Config\n"
        "class SoloConfig(Config):\n"
        "    x: int = 42\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(root)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    solo_yaml = root / "settings" / "solo.yaml"
    assert not solo_yaml.exists()
    cfg_mod = _import_subgrade_config_fresh()
    assert cfg_mod.cfg.solo.x == 42
    assert solo_yaml.is_file()
    assert solo_yaml.read_text(encoding="utf-8").strip() == "{}"


def test_auto_yaml_template_nested_required_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新建 yaml 时嵌套必填标量为 ``null``；模板含 null 时实例化可能校验失败，需捕获。"""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "nest.py").write_text(
        "from pydantic import BaseModel\n"
        "from subgrade.config import Config\n"
        "class Inner(BaseModel):\n"
        "    a: str\n"
        "class NestConfig(Config):\n"
        "    inner: Inner\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    cfg_mod = _import_subgrade_config_fresh()
    try:
        _ = cfg_mod.cfg.nest
    except ValidationError:
        pass
    nest_yaml = tmp_path / "settings" / "nest.yaml"
    text = nest_yaml.read_text(encoding="utf-8")
    assert "inner:" in text
    assert yaml.safe_load(text)["inner"]["a"] is None


def test_auto_yaml_template_list_placeholder_is_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """必填列表字段占位为 ``[]``。"""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "lst.py").write_text(
        "from subgrade.config import Config\n"
        "class LstConfig(Config):\n"
        "    items: list[str]\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    cfg_mod = _import_subgrade_config_fresh()
    try:
        _ = cfg_mod.cfg.lst
    except ValidationError:
        pass
    lst_yaml = tmp_path / "settings" / "lst.yaml"
    data = yaml.safe_load(lst_yaml.read_text(encoding="utf-8"))
    assert data == {"items": []}


def test_auto_yaml_template_composite_type_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """常见复合注解拆包：``Union``、``typing.List/Dict``、``Annotated``、嵌套模型、带默认的 ``Optional`` 不出现在模板。"""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "comp.py").write_text(
        "from __future__ import annotations\n"
        "from typing import Annotated, Dict, List, Optional, Union\n"
        "from pydantic import BaseModel, Field\n"
        "from subgrade.config import Config\n"
        "class Inner(BaseModel):\n"
        "    z: int\n"
        "class CompConfig(Config):\n"
        "    scalar_union: Union[str, int]\n"
        "    lst_typing: List[str]\n"
        "    mapping: Dict[str, int]\n"
        "    ann: Annotated[str, Field(description='d')]\n"
        "    inner: Inner\n"
        "    optional_inner: Optional[Inner]\n"
        "    maybe_skip: Optional[str] = None\n"
        "    maybe_skip_2: Annotated[Optional[str], Field(default=None)]\n"
        "    nested_annotated: Annotated[Inner, Field(description='d')]\n"
        "    nested_annotated_2: Annotated[Inner | None, Field(description='d')]\n"
        "    nested_annotated_3: Annotated[Inner | Dict, Field(description='d')]\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    cfg_mod = _import_subgrade_config_fresh()
    try:
        _ = cfg_mod.cfg.comp
    except ValidationError:
        pass
    data = yaml.safe_load(
        (tmp_path / "settings" / "comp.yaml").read_text(encoding="utf-8")
    )
    assert "maybe_skip" not in data
    assert data == {
        "scalar_union": None,
        "lst_typing": [],
        "mapping": {},
        "ann": None,
        "inner": {"z": None},
        "optional_inner": {"z": None},
        "nested_annotated": {"z": None},
        "nested_annotated_2": {"z": None},
        "nested_annotated_3": None,
    }


# --- 4. 仅存在 .yml 时可读取 ---


def test_settings_yml_file_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    root = FIXTURES / "yml_only"
    monkeypatch.chdir(root)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    cfg_mod = _import_subgrade_config_fresh()
    assert cfg_mod.cfg.app.label == "from-yml-extension"


# --- 5. 不存在的 module；不在 configs 下的 Config ---


def test_unknown_cfg_module_raises_attribute_error(
    project_root_without_env: Path,
) -> None:
    cfg_mod = _import_subgrade_config_fresh()
    assert Path.cwd() == project_root_without_env
    with pytest.raises(AttributeError, match="Module 'missing' not found"):
        _ = cfg_mod.cfg.missing


def test_config_class_outside_cfg_dir_not_on_cfg_but_instantiable(
    project_root_without_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """定义在普通模块（非 subgrade._cfgmod.*）中的子类不会注册到 cfg，但可自带 yaml 实例化。"""
    monkeypatch.chdir(project_root_without_env)
    cfg_mod = _import_subgrade_config_fresh()
    from subgrade.config import Config

    yaml_path = tmp_path / "standalone.yaml"
    yaml_path.write_text("value: from-standalone\n", encoding="utf-8")

    class StandaloneConfig(Config):
        model_config = SettingsConfigDict(
            env_prefix="STANDALONE_",
            yaml_file=[str(yaml_path.resolve())],
        )
        value: str = "default"

    assert _cfg_getattr_fails(cfg_mod.cfg, "standalone")
    assert StandaloneConfig().value == "from-standalone"


# --- 6. 其它边界 ---


def test_invalid_config_filename_not_identifier_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "bad-name.py").write_text(
        "# invalid stem\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    with pytest.raises(ValueError, match="Invalid config module name"):
        _import_subgrade_config_fresh()


def test_missing_configs_directory_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    with pytest.raises(FileNotFoundError, match="Config directory"):
        _import_subgrade_config_fresh()


def test_empty_configs_dir_no_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "configs").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    cfg_mod = _import_subgrade_config_fresh()
    with pytest.raises(AttributeError, match="Module 'app' not found"):
        _ = cfg_mod.cfg.app


def test_module_without_config_subclass_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "bare.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    cfg_mod = _import_subgrade_config_fresh()
    with pytest.raises(AttributeError, match="defines no Config subclass"):
        _ = cfg_mod.cfg.bare


def test_both_yaml_and_yml_present_merge_keys_from_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同时存在 .yaml 与 .yml 时，应能从两个文件合并读取（不依赖同名字段覆盖顺序）。"""
    (tmp_path / "configs").mkdir()
    (tmp_path / "settings").mkdir()
    (tmp_path / "configs" / "twice.py").write_text(
        "from subgrade.config import Config\n"
        "class TwiceConfig(Config):\n"
        "    from_yaml: str = ''\n"
        "    from_yml: str = ''\n",
        encoding="utf-8",
    )
    (tmp_path / "settings" / "twice.yaml").write_text(
        "from_yaml: a\n", encoding="utf-8"
    )
    (tmp_path / "settings" / "twice.yml").write_text("from_yml: b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    cfg_mod = _import_subgrade_config_fresh()
    assert cfg_mod.cfg.twice.from_yaml == "a"
    assert cfg_mod.cfg.twice.from_yml == "b"
