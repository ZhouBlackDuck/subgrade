"""``subgrade.logger``：structlog 与 stdlib ``logging`` 集成及 ``setup_logger``。"""

from __future__ import annotations
import importlib
import json
import logging
from pathlib import Path
import sys
from typing import Any

import pytest
import structlog


def _reset_root_logging_for_subgrade_import() -> None:
    """避免 pytest/capsys 或上一用例 ``dictConfig`` 留下的 ``StreamHandler`` 仍指向已关闭的 stderr。"""
    for _h in logging.root.handlers[:]:
        logging.root.removeHandler(_h)
    logging.root.setLevel(logging.WARNING)
    logging.root.addHandler(logging.NullHandler())


class RecordingHandler(logging.Handler):
    def __init__(self, level: int = logging.NOTSET) -> None:
        super().__init__(level)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _import_subgrade_config_and_logger_fresh() -> Any:
    _reset_root_logging_for_subgrade_import()
    for name in list(sys.modules):
        if (
            name == "subgrade.config"
            or name == "subgrade.logger"
            or name.startswith("subgrade._cfgmod.")
        ):
            del sys.modules[name]
    importlib.import_module("subgrade.config")
    return importlib.import_module("subgrade.logger")


@pytest.fixture
def logger_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """临时工程 + 重载 ``subgrade.logger``；各用例内自行 ``setup_logger``。"""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "dummy.py").write_text(
        "from subgrade.config import Config\n"
        "class DummyConfig(Config):\n"
        "    v: int = 0\n",
        encoding="utf-8",
    )
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "dummy.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("SUBGRADE_PROJECT_ROOT", str(tmp_path.resolve()))
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)

    sm = _import_subgrade_config_and_logger_fresh()

    class ProjectLogger(sm.LoggerConfig):
        """测试用工程侧子类，使 ``LoggerConfig.resolve_instance()`` 返回可实例化配置。"""

    ProjectLogger.model_rebuild()
    yield sm

    if hasattr(structlog, "reset_defaults"):
        structlog.reset_defaults()
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
    logging.root.setLevel(logging.WARNING)
    logging.root.addHandler(logging.NullHandler())


def test_logging_and_structlog_both_emit_same_message_to_stderr(
    logger_env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """默认 ``ProcessorFormatter`` 下，stdlib ``logging`` 与 ``structlog`` 均能输出同一事件文本。"""
    sm = logger_env
    sm.setup_logger()
    logging.getLogger("parity").info("parity-msg")
    err1 = capsys.readouterr().err
    structlog.get_logger("parity2").info("parity-msg")
    err2 = capsys.readouterr().err
    assert "parity-msg" in err1
    assert "parity-msg" in err2


def test_existing_logger_handlers_and_filters_unchanged_after_setup(
    logger_env: Any,
) -> None:
    """未出现在 ``dictConfig`` 中的 logger：``setup_logger`` 后 handler / filter 与实例不变。"""
    sm = logger_env
    lg = logging.getLogger("keepers.pre_setup")
    lg.handlers.clear()
    lg.filters.clear()
    lg.propagate = False
    h = RecordingHandler()

    class Keep(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return True

    f = Keep()
    lg.addHandler(h)
    lg.addFilter(f)
    hid = id(h)
    fid = id(f)

    sm.setup_logger()

    assert logging.getLogger("keepers.pre_setup") is lg
    assert len(lg.handlers) == 1 and id(lg.handlers[0]) == hid
    assert len(lg.filters) == 1 and id(lg.filters[0]) == fid
    lg.warning("kept")
    assert len(h.records) == 1


def test_subclass_logging_config_changes_root_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """继承 ``LoggingConfig`` / ``LoggerConfig`` 可改变 ``dictConfig`` 行为（root 与 handler 级别）。"""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "dummy.py").write_text(
        "from subgrade.config import Config\n"
        "class DummyConfig(Config):\n"
        "    v: int = 0\n",
        encoding="utf-8",
    )
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "dummy.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("SUBGRADE_PROJECT_ROOT", str(tmp_path.resolve()))
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    sm = _import_subgrade_config_and_logger_fresh()

    class LoudLogging(sm.LoggingConfig):
        handlers: dict[str, dict] = {
            "default": {
                "level": "DEBUG",
                "class": "logging.StreamHandler",
                "formatter": "colored",
            }
        }
        root: dict[str, Any] = {"level": "DEBUG", "handlers": ["default"]}

    class Loud(sm.LoggerConfig):
        logging: sm.LoggingConfig = LoudLogging()

    Loud.model_rebuild()
    sm.setup_logger()
    logging.getLogger("dbg").debug("only-if-debug-root")
    assert "only-if-debug-root" in capsys.readouterr().err


def test_subclass_structlog_config_changes_output_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """继承 ``StructlogConfig`` / ``LoggerConfig`` 可改变 structlog 处理器链（如 JSONRenderer）。"""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "dummy.py").write_text(
        "from subgrade.config import Config\n"
        "class DummyConfig(Config):\n"
        "    v: int = 0\n",
        encoding="utf-8",
    )
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "dummy.yaml").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("SUBGRADE_PROJECT_ROOT", str(tmp_path.resolve()))
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    sm = _import_subgrade_config_and_logger_fresh()

    class JsonStruct(sm.StructlogConfig):
        processors: list[Any] = [
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(),
            lambda _, __, event_dict: {"json": json.dumps(event_dict), **event_dict},
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ]

    class JsonLogger(sm.LoggerConfig):
        structlog: sm.StructlogConfig = JsonStruct()

    JsonLogger.model_rebuild()
    sm.setup_logger()
    structlog.get_logger("jsonlog").info("evt-json", k=1)
    err = capsys.readouterr().err
    assert "evt-json" in err
    assert "{" in err


def test_setup_logger_twice_idempotent_no_crash(logger_env: Any) -> None:
    """连续调用 ``setup_logger`` 不致崩溃。"""
    sm = logger_env
    sm.setup_logger()
    sm.setup_logger()
    logging.getLogger("idemp").info("ok")


def test_dictconfig_declared_logger_receives_structlog_events(
    logger_env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """``LoggingConfig.loggers`` 中声明的 logger 可被 structlog 使用并输出到 stderr。"""
    sm = logger_env
    base = sm.LoggingConfig()
    named_logging = base.model_copy(
        update={
            "loggers": {
                "named": {
                    "level": "INFO",
                    "handlers": ["default"],
                    "propagate": False,
                }
            }
        }
    )

    class WithNamed(sm.LoggerConfig):
        logging: sm.LoggingConfig = named_logging

    WithNamed.model_rebuild()
    sm.setup_logger()
    structlog.get_logger("named").warning("named-via-structlog")
    assert "named-via-structlog" in capsys.readouterr().err


def test_pre_hook_runs_before_dictconfig(logger_env: Any) -> None:
    """``pre_hook`` 在 ``dictConfig`` / ``structlog.configure`` 之前执行。"""
    sm = logger_env
    buf: list[str] = []

    def hook() -> None:
        buf.append("first")

    sm.setup_logger(pre_hook=hook)
    assert buf == ["first"]


def test_root_logger_accepts_both_stdlib_and_structlog(
    logger_env: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """root 上经 ``dictConfig`` 安装默认 handler 后，``logging`` 与 ``structlog`` 均可写 root。"""
    sm = logger_env
    sm.setup_logger()
    logging.getLogger().error("root-stdlib")
    structlog.get_logger().error("root-struct")
    err = capsys.readouterr().err
    assert "root-stdlib" in err and "root-struct" in err


def test_disable_existing_loggers_false_keeps_unlisted_logger_active(
    logger_env: Any,
) -> None:
    """``disable_existing_loggers: False`` 时，未列在配置里的 logger 仍可 ``emit``。"""
    sm = logger_env
    lg = logging.getLogger("unlisted.z")
    lg.propagate = False
    h = RecordingHandler()
    lg.addHandler(h)
    sm.setup_logger()
    lg.info("still-here")
    assert len(h.records) == 1


def test_propagate_preserved_for_existing_logger(logger_env: Any) -> None:
    """``setup_logger`` 不修改已存在 logger 的 ``propagate``。"""
    sm = logger_env
    lg = logging.getLogger("prop.keep")
    lg.propagate = False
    lg.addHandler(RecordingHandler())
    before = lg.propagate
    sm.setup_logger()
    assert lg.propagate is before
