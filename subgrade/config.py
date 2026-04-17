from functools import partial
import importlib.util
import os
from pathlib import Path
import sys

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

_CFG_MOD_PREFIX = "subgrade._cfgmod"


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


def _ensure_module_yaml(settings_root: Path, module_name: str) -> list[Path]:
    yaml_path = settings_root / f"{module_name}.yaml"
    yml_path = settings_root / f"{module_name}.yml"
    if yaml_path.is_file() or yml_path.is_file():
        paths: list[Path] = []
        if yaml_path.is_file():
            paths.append(yaml_path)
        if yml_path.is_file():
            paths.append(yml_path)
        return paths
    settings_root.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text("{}\n", encoding="utf-8")
    return [yaml_path]


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

        yaml_path = _ensure_module_yaml(_settings_basedir(), module_name)
        env_prefix = f"{name.replace('Config', '').upper()}_"
        default = SettingsConfigDict(
            env_prefix=env_prefix,
            yaml_file=yaml_path,
        )
        if "model_config" not in dct:
            dct["model_config"] = default
        else:
            dct["model_config"].setdefault("env_prefix", default["env_prefix"])
            dct["model_config"].setdefault("yaml_file", default["yaml_file"])

        Configs.__configs__[module_name] = super().__new__(mcs, name, bases, dct)
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
