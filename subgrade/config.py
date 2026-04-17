from functools import partial
import importlib.util
import os
from pathlib import Path
import sys
import types
from typing import Annotated, Any, Dict, List, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)
import yaml

_CFG_MOD_PREFIX = "subgrade._cfgmod"


def _unwrap_annotated(annotation: Any) -> Any:
    """剥去 ``Annotated[T, ...]``，得到 ``T``。"""
    ann = annotation
    while get_origin(ann) is Annotated:
        args = get_args(ann)
        if not args:
            break
        ann = args[0]
    return ann


def _effective_annotation(annotation: Any) -> Any:
    return _strip_optional(_unwrap_annotated(annotation))


def _strip_optional(annotation: Any) -> Any:
    """将 ``Optional[T]`` / ``T | None`` 归一为 ``T``（单层）。"""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Union:
        filtered = [a for a in args if a not in (type(None), types.NoneType)]
        if len(filtered) == 1:
            return _strip_optional(filtered[0])
        return annotation
    if hasattr(types, "UnionType") and origin is types.UnionType:
        filtered = [a for a in args if a not in (type(None), types.NoneType)]
        if len(filtered) == 1:
            return _strip_optional(filtered[0])
        return annotation
    return annotation


def _is_base_model_type(tp: Any) -> bool:
    tp = _effective_annotation(tp)
    if not isinstance(tp, type):
        return False
    try:
        return issubclass(tp, BaseModel)
    except TypeError:
        return False


def _placeholder_for_annotation(annotation: Any) -> Any:
    """占位：标量为 ``None``（写入 YAML 为 ``null``）；列表为 ``[]``；``dict`` 为 ``{}``；嵌套模型递归为字典。"""
    ann = _effective_annotation(annotation)
    origin = get_origin(ann)

    if _is_base_model_type(ann):
        return _required_fields_placeholder_dict(ann)

    if origin in (list, List):
        return []

    if origin in (dict, Dict):
        return {}

    return None


def _required_fields_placeholder_dict(model_cls: type[BaseModel]) -> dict[str, Any]:
    """仅包含 Pydantic 判定为必填的字段；嵌套 ``BaseModel`` 递归展开。"""
    out: dict[str, Any] = {}
    for fname, finfo in model_cls.model_fields.items():
        if not finfo.is_required():
            continue
        out[fname] = _placeholder_for_annotation(finfo.annotation)
    return out


def _write_config_yaml_template(settings_cls: type[BaseSettings], path: Path) -> None:
    """写入 YAML：必填字段为键；标量占位为 ``null``，列表为 ``[]``，映射为 ``{}``，嵌套为字典。"""
    data = _required_fields_placeholder_dict(settings_cls)
    text = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _cfg_basedir() -> Path:
    raw = os.environ.get("CFG_BASEDIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("configs")


def _settings_basedir() -> Path:
    raw = os.environ.get("SETTINGS_BASEDIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("settings")


def _ensure_module_yaml(
    settings_root: Path, module_name: str
) -> tuple[list[Path], bool]:
    """解析或占位设置文件路径。若需新建 ``.yaml``，返回 ``(paths, True)``，由调用方在类创建后写入模板。"""
    yaml_path = settings_root / f"{module_name}.yaml"
    yml_path = settings_root / f"{module_name}.yml"
    if yaml_path.is_file() or yml_path.is_file():
        paths: list[Path] = []
        if yaml_path.is_file():
            paths.append(yaml_path)
        if yml_path.is_file():
            paths.append(yml_path)
        return paths, False
    settings_root.mkdir(parents=True, exist_ok=True)
    return [yaml_path], True


def _load_cfg_module(path: Path, stem: str) -> None:
    qualname = f"{_CFG_MOD_PREFIX}.{stem}"
    if qualname in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(qualname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load config module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualname] = module
    spec.loader.exec_module(module)


class Configs:
    __config_modules__: dict[str, object] = {}
    __configs__: dict[str, type[BaseSettings]] = {}
    __cached__: dict[str, BaseSettings] = {}

    def __init__(self) -> None:
        cfg_dir = _cfg_basedir()
        if not cfg_dir.is_dir():
            raise FileNotFoundError(f"Config directory '{cfg_dir}' not found")
        for path in cfg_dir.glob("*.py"):
            stem = path.stem
            if not stem.isidentifier():
                raise ValueError(f"Invalid config module name: '{stem}'")
            Configs.__config_modules__[stem] = partial(_load_cfg_module, path, stem)

    def __getattr__(self, name: str) -> BaseSettings:
        if name not in Configs.__config_modules__:
            raise AttributeError(f"Module '{name}' not found")
        Configs.__config_modules__[name]()
        if name not in Configs.__configs__:
            raise AttributeError(f"Module '{name}' defines no Config subclass")
        if name not in Configs.__cached__:
            Configs.__cached__[name] = Configs.__configs__[name]()
        return Configs.__cached__[name]


class ConfigMeta(type(BaseModel)):
    def __new__(mcs, name, bases, dct):
        mod = dct.get("__module__")

        if name == "Config" and mod == __name__:
            return super().__new__(mcs, name, bases, dct)

        if not isinstance(mod, str) or not mod.startswith(f"{_CFG_MOD_PREFIX}."):
            return super().__new__(mcs, name, bases, dct)

        module_name = mod.rsplit(".", 1)[-1]
        if module_name in Configs.__configs__:
            raise TypeError(
                f"Module '{module_name}' may only define one Config subclass"
            )

        yaml_paths, pending_template = _ensure_module_yaml(
            _settings_basedir(), module_name
        )
        env_prefix = f"{name.replace('Config', '').upper()}_"
        default = SettingsConfigDict(
            env_prefix=env_prefix,
            yaml_file=yaml_paths,
        )
        if "model_config" not in dct:
            dct["model_config"] = default
        else:
            dct["model_config"].setdefault("env_prefix", default["env_prefix"])
            dct["model_config"].setdefault("yaml_file", default["yaml_file"])

        new_cls = super().__new__(mcs, name, bases, dct)
        if pending_template:
            primary = yaml_paths[0]
            _write_config_yaml_template(new_cls, primary)
        Configs.__configs__[module_name] = new_cls
        return Configs.__configs__[module_name]


class Config(BaseSettings, metaclass=ConfigMeta):
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            YamlConfigSettingsSource(settings_cls),
        )


cfg = Configs()

__all__ = ["cfg", "Config"]
