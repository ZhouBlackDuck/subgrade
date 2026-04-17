"""Config / cfg 行为与边界测试。"""

from __future__ import annotations
import importlib
from pathlib import Path
import sys
import threading

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


# --- 需求：统一入口 / 懒加载 / 实例缓存 ---


def test_unified_entry_cfg_is_configs_with_getattr(
    project_root_without_env: Path,
) -> None:
    """统一入口：``from subgrade.config import cfg`` 为 ``Configs`` 单例，通过 ``cfg.<模块名>`` 取配置实例。"""
    cfg_mod = _import_subgrade_config_fresh()
    assert Path.cwd() == project_root_without_env
    assert type(cfg_mod.cfg).__name__ == "Configs"
    assert cfg_mod.cfg.app.label == "from-yaml"


def test_lazy_load_dynamic_module_not_in_sys_modules_until_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """懒加载：import ``subgrade.config`` 只注册路径，不 ``exec`` configs 下脚本，首次 ``cfg.<stem>`` 才加载模块。"""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "lazyonly.py").write_text(
        "from subgrade.config import Config\n"
        "class LazyonlyConfig(Config):\n"
        "    n: int = 0\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    qual = "subgrade._cfgmod.lazyonly"
    cfg_mod = _import_subgrade_config_fresh()
    assert qual not in sys.modules
    assert cfg_mod.cfg.lazyonly.n == 0
    assert qual in sys.modules


def test_cfg_access_returns_same_cached_instance(
    project_root_without_env: Path,
) -> None:
    """同一 ``cfg.<name>`` 多次访问返回同一缓存实例。"""
    cfg_mod = _import_subgrade_config_fresh()
    a = cfg_mod.cfg.app
    b = cfg_mod.cfg.app
    assert a is b


def test_auto_scan_picks_up_all_py_modules_in_configs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自动扫描：``configs`` 下每个合法 ``*.py`` 对应一个 ``cfg.<stem>``。"""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "alpha.py").write_text(
        "from subgrade.config import Config\n"
        "class AlphaConfig(Config):\n"
        "    v: int = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "configs" / "beta.py").write_text(
        "from subgrade.config import Config\n"
        "class BetaConfig(Config):\n"
        "    w: int = 2\n",
        encoding="utf-8",
    )
    (tmp_path / "settings").mkdir()
    for stem in ("alpha", "beta"):
        (tmp_path / "settings" / f"{stem}.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    cfg_mod = _import_subgrade_config_fresh()
    assert cfg_mod.cfg.alpha.v == 1
    assert cfg_mod.cfg.beta.w == 2


@pytest.mark.parametrize("root_env", ("SUBGRADE_PROJECT_ROOT", "PROJECT_ROOT"))
def test_default_paths_resolve_to_external_project_root_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_env: str
) -> None:
    """未单独设置 CFG/SETTINGS 时，``configs``/``settings`` 相对 ``SUBGRADE_PROJECT_ROOT``（或 ``PROJECT_ROOT``），与 cwd 无关。"""
    app = tmp_path / "external_app"
    (app / "configs").mkdir(parents=True)
    (app / "settings").mkdir()
    (app / "configs" / "rooted.py").write_text(
        "from subgrade.config import Config\n"
        "class RootedConfig(Config):\n"
        "    x: int = 1\n",
        encoding="utf-8",
    )
    (app / "settings" / "rooted.yaml").write_text("{}\n", encoding="utf-8")
    wrong_cwd = tmp_path / "wrong_cwd"
    wrong_cwd.mkdir(parents=True)
    monkeypatch.chdir(wrong_cwd)
    monkeypatch.delenv("SUBGRADE_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    monkeypatch.setenv(root_env, str(app.resolve()))
    cfg_mod = _import_subgrade_config_fresh()
    assert cfg_mod.project_root() == app.resolve()
    assert cfg_mod.cfg.rooted.x == 1


def test_project_root_discovered_by_walking_up_from_cwd_for_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设置环境变量时，自 cwd 向上查找 ``pyproject.toml`` 等标记以确定项目根（子目录内启动也可）。"""
    repo = tmp_path / "repo"
    (repo / "nested" / "deep").mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname='t'\n", encoding="utf-8")
    (repo / "configs").mkdir()
    (repo / "configs" / "inf.py").write_text(
        "from subgrade.config import Config\n"
        "class InfConfig(Config):\n"
        "    n: int = 7\n",
        encoding="utf-8",
    )
    (repo / "settings").mkdir()
    (repo / "settings" / "inf.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(repo / "nested" / "deep")
    monkeypatch.delenv("SUBGRADE_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    cfg_mod = _import_subgrade_config_fresh()
    assert cfg_mod.project_root() == repo.resolve()
    assert cfg_mod.cfg.inf.n == 7


def test_concurrent_first_access_yields_single_cached_instance(
    project_root_without_env: Path,
) -> None:
    """多线程同时首次访问同一 ``cfg.<name>`` 时仅产生一个实例。"""
    cfg_mod = _import_subgrade_config_fresh()
    bag: list[object] = []

    def grab() -> None:
        bag.append(cfg_mod.cfg.app)

    threads = [threading.Thread(target=grab) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(bag) == 16
    assert all(obj is bag[0] for obj in bag)


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
