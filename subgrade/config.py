from __future__ import annotations
from functools import partial
import importlib.util
import logging
import os
from pathlib import Path
import sys
import threading
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

logger = logging.getLogger(__name__)

_CFG_MOD_PREFIX = "subgrade._cfgmod"

# 懒加载与缓存并发保护（同进程多线程首次访问同一 cfg.<name>）
_configs_state_lock = threading.RLock()

# 自 cwd 向上探测「项目根」时识别的标记
_ROOT_MARKERS: tuple[tuple[str, str], ...] = (
    ("file", "pyproject.toml"),
    ("file", "setup.cfg"),
    ("file", "setup.py"),
    ("any", ".git"),
)
_MAX_ROOT_WALK = 64


def _directory_has_project_marker(d: Path) -> bool:
    for kind, name in _ROOT_MARKERS:
        p = d / name
        try:
            if kind == "file" and p.is_file():
                return True
            if kind == "any" and p.exists():
                return True
        except OSError:
            continue
    return False


def _discover_project_root_from(start: Path) -> Path | None:
    """从 ``start`` 向父目录查找含工程标记的目录；找不到返回 ``None``。"""
    cur = start.resolve()
    for _ in range(_MAX_ROOT_WALK):
        if _directory_has_project_marker(cur):
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


def _project_root() -> Path:
    """解析「使用方项目根」，用于默认的 ``configs`` / ``settings`` 相对路径。

    顺序（**不**用本包 ``__file__``，避免指向 site-packages）：

    1. ``SUBGRADE_PROJECT_ROOT`` 或 ``PROJECT_ROOT``（部署/CI 显式指定最可靠）
    2. 若 ``cwd`` 下已有 ``configs/`` 目录，则直接把 ``cwd`` 当作项目根（避免在仅含
       ``configs`` 的临时/夹具目录里向上误命中外层 ``pyproject.toml``）
    3. 否则自 ``cwd`` 向上查找含 ``pyproject.toml`` / ``setup.cfg`` / ``setup.py`` / ``.git`` 的目录
    4. 仍找不到则使用 ``cwd``
    """
    raw = os.environ.get("SUBGRADE_PROJECT_ROOT") or os.environ.get("PROJECT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "configs").is_dir():
        logger.debug("Using cwd as project root (configs/ exists here): %s", cwd)
        return cwd
    discovered = _discover_project_root_from(cwd)
    if discovered is not None:
        logger.debug("Project root discovered from cwd walk: %s", discovered)
        return discovered
    logger.debug("No project marker found from cwd; using cwd as project root: %s", cwd)
    return cwd


def project_root() -> Path:
    """返回当前解析到的项目根路径（与默认 ``configs``/``settings`` 的相对基准一致）。"""
    return _project_root()


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
    logger.debug("Writing default settings template to %s", path.resolve())
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
    return _project_root() / "configs"


def _settings_basedir() -> Path:
    raw = os.environ.get("SETTINGS_BASEDIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return _project_root() / "settings"


def _ensure_module_yaml(
    settings_root: Path, module_name: str
) -> tuple[list[Path], bool]:
    """解析或占位设置文件路径。若需新建 ``.yaml``，返回 ``(paths, True)``，由调用方在类创建后写入模板。"""
    yaml_path = settings_root / f"{module_name}.yaml"
    yml_path = settings_root / f"{module_name}.yml"
    paths = [yaml_path, yml_path]
    if yaml_path.is_file() or yml_path.is_file():
        return paths, False
    settings_root.mkdir(parents=True, exist_ok=True)
    logger.debug(
        "No settings file for module %r under %s; will create default %s",
        module_name,
        settings_root.resolve(),
        yaml_path.name,
    )
    return paths, True


_library_config_state_lock = threading.RLock()


def _is_library_config_root(tp: type) -> bool:
    """``subgrade.config`` 内的 ``_LibraryConfig`` 抽象根，不参与 registry、不直接实例化。"""
    return (
        tp.__name__ == "_LibraryConfig" and getattr(tp, "__module__", None) == __name__
    )


class _LibraryConfigMeta(type(BaseModel)):
    """库内配置元类：维护「基类 → 当前最末直接子类」映射，供 ``resolve_instance`` 解析到工程侧覆盖类。"""

    _registry: dict[type, type] = {}
    _instances: dict[type, BaseSettings] = {}
    _pending_yaml_templates: dict[type, Path] = {}

    def __new__(mcs, name: str, bases: tuple[type, ...], dct: dict[str, Any]) -> type:
        mod = dct.get("__module__")
        is_base = name == "_LibraryConfig" and mod == __name__
        yaml_paths: list[Path] = []
        pending_template = False

        if isinstance(mod, str) and not is_base:
            yaml_paths, pending_template = _ensure_module_yaml(
                _settings_basedir(), name.upper()
            )
            env_prefix = f"{name.replace('Config', '').upper()}_"
            default = SettingsConfigDict(
                env_prefix=env_prefix,
                yaml_file=yaml_paths,
                env_file=".env",
                dotenv_filtering="match_prefix",
                env_nested_delimiter="__",
            )
            if "model_config" not in dct:
                dct["model_config"] = default
            else:
                dct["model_config"].setdefault("env_prefix", default["env_prefix"])
                dct["model_config"].setdefault("yaml_file", default["yaml_file"])
                dct["model_config"].setdefault("env_file", default["env_file"])
                dct["model_config"].setdefault(
                    "dotenv_filtering", default["dotenv_filtering"]
                )
                dct["model_config"].setdefault(
                    "env_nested_delimiter", default["env_nested_delimiter"]
                )

        new_cls = super().__new__(mcs, name, bases, dct)
        if is_base:
            return new_cls
        mcs._registry[new_cls] = new_cls
        for b in bases:
            if (
                isinstance(b, type)
                and type(b) is mcs
                and not _is_library_config_root(b)
            ):
                mcs._registry[b] = new_cls
        if isinstance(mod, str) and pending_template:
            mcs._pending_yaml_templates[new_cls] = yaml_paths[0]
        return new_cls

    @classmethod
    def write_pending_template_if_needed(mcs, cls: type) -> None:
        """仅为**当前**被构造的类写入尚缺的默认 YAML（不沿 MRO 为祖先类补写）。"""
        if _is_library_config_root(cls):
            return
        path = mcs._pending_yaml_templates.get(cls, None)
        if path is None:
            return
        if not path.is_file():
            _write_config_yaml_template(cls, path)
        mcs._pending_yaml_templates.pop(cls, None)

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        if not _is_library_config_root(cls):
            with _library_config_state_lock:
                type(cls).write_pending_template_if_needed(cls)
        return super(_LibraryConfigMeta, cls).__call__(cls, *args, **kwargs)


def _load_cfg_module(path: Path, stem: str) -> None:
    qualname = f"{_CFG_MOD_PREFIX}.{stem}"
    if qualname in sys.modules:
        return
    logger.debug("Loading config module %r from %s", stem, path.resolve())
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
        root = _project_root()
        cfg_dir = _cfg_basedir()
        settings_dir = _settings_basedir()
        logger.debug(
            "Configs init: project_root=%s cfg_dir=%s settings_dir=%s",
            root,
            cfg_dir.resolve(),
            settings_dir.resolve(),
        )
        if not cfg_dir.is_dir():
            raise FileNotFoundError(f"Config directory '{cfg_dir}' not found")
        for path in cfg_dir.glob("*.py"):
            stem = path.stem
            if not stem.isidentifier():
                raise ValueError(f"Invalid config module name: '{stem}'")
            Configs.__config_modules__[stem] = partial(_load_cfg_module, path, stem)
        logger.debug(
            "Registered %d config module(s) from %s",
            len(Configs.__config_modules__),
            cfg_dir.resolve(),
        )

    def __getattr__(self, name: str) -> BaseSettings:
        if name not in Configs.__config_modules__:
            raise AttributeError(f"Module '{name}' not found")
        with _configs_state_lock:
            Configs.__config_modules__[name]()
            if name not in Configs.__configs__:
                raise AttributeError(f"Module '{name}' defines no Config subclass")
            if name not in Configs.__cached__:
                logger.debug("Instantiating settings for config module %r", name)
                Configs.__cached__[name] = Configs.__configs__[name]()
            return Configs.__cached__[name]

    def reload(self, *names: str) -> None:
        """丢弃已加载的 ``configs/<name>.py`` 模块与 ``cfg.<name>`` 实例缓存；下次访问时重新执行模块并重建设置实例（会重新读 YAML 等）。

        不传参数时，对 ``Configs`` 初始化时扫描到的全部 ``*.py`` 茎名逐一重载。
        """
        with _configs_state_lock:
            targets = list(names) if names else list(Configs.__config_modules__.keys())
            for name in targets:
                if name not in Configs.__config_modules__:
                    raise AttributeError(f"Module '{name}' not found")
                qualname = f"{_CFG_MOD_PREFIX}.{name}"
                Configs.__cached__.pop(name, None)
                Configs.__configs__.pop(name, None)
                sys.modules.pop(qualname, None)


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
            env_file=".env",
            dotenv_filtering="match_prefix",
            env_nested_delimiter="__",
        )
        if "model_config" not in dct:
            dct["model_config"] = default
        else:
            dct["model_config"].setdefault("env_prefix", default["env_prefix"])
            dct["model_config"].setdefault("yaml_file", default["yaml_file"])
            dct["model_config"].setdefault("env_file", default["env_file"])
            dct["model_config"].setdefault(
                "dotenv_filtering", default["dotenv_filtering"]
            )
            dct["model_config"].setdefault(
                "env_nested_delimiter", default["env_nested_delimiter"]
            )

        new_cls = super().__new__(mcs, name, bases, dct)
        if pending_template:
            primary = yaml_paths[0]
            _write_config_yaml_template(new_cls, primary)
        Configs.__configs__[module_name] = new_cls
        return Configs.__configs__[module_name]


class _LibraryConfig(BaseSettings, metaclass=_LibraryConfigMeta):
    """库内可被子类替换的配置基类；实际实例请通过 ``resolve_instance`` 获取。"""

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

    @classmethod
    def _resolve_leaf_type(cls) -> type[BaseSettings]:
        meta = type(cls)
        cur: type[BaseSettings] = cls
        while True:
            nxt = meta._registry.get(cur, cur)
            if nxt is cur:
                return cur
            cur = nxt

    @classmethod
    def resolve_instance(cls) -> BaseSettings:
        if _is_library_config_root(cls):
            raise TypeError(
                "_LibraryConfig is not a concrete class, please use a concrete subclass"
            )
        meta = type(cls)
        leaf = cls._resolve_leaf_type()
        with _library_config_state_lock:
            if leaf not in meta._instances:
                meta._instances[leaf] = leaf()
        return meta._instances[leaf]

    @classmethod
    def reload_singletons(cls, *roots: type[BaseSettings]) -> None:
        """丢弃 ``resolve_instance`` 使用的单例缓存，使下次调用重新构造实例（重新读 YAML 等）。

        - 无位置参数：若调用方是 ``_LibraryConfig`` 根类，则清空**全部**库内单例；否则只清空
          自该类解析到的**叶子类型**对应的一条缓存（等价于 ``SomeConfig.reload_singletons()``）。
        - 传入一个或多个 ``_LibraryConfig`` 的具体子类时：按各类解析到的叶子类型去重后逐一移除；
          其中不得包含 ``_LibraryConfig`` 根类本身。
        """
        meta = type(cls)
        with _library_config_state_lock:
            if not roots:
                if _is_library_config_root(cls):
                    meta._instances.clear()
                    return
                if not isinstance(cls, type) or not issubclass(cls, _LibraryConfig):
                    raise TypeError(
                        "reload_singletons() without arguments requires a concrete "
                        "_LibraryConfig subclass as the receiver"
                    )
                leaf = cls._resolve_leaf_type()
                meta._instances.pop(leaf, None)
                return
            for r in roots:
                if not isinstance(r, type) or not issubclass(r, _LibraryConfig):
                    raise TypeError(f"Expected _LibraryConfig subclass, got {r!r}")
                if _is_library_config_root(r):
                    raise TypeError(
                        "Cannot reload singleton for _LibraryConfig root; "
                        "call _LibraryConfig.reload_singletons() with no arguments "
                        "to clear all cached instances"
                    )
                leaf = r._resolve_leaf_type()
                meta._instances.pop(leaf, None)


def reload_library_config(*roots: type[BaseSettings]) -> None:
    """``_LibraryConfig.reload_singletons`` 的模块级入口：参数语义与之一致（无参即清空全部库内单例）。"""
    _LibraryConfig.reload_singletons(*roots)


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

__all__ = ["cfg", "Config", "project_root", "reload_library_config"]
