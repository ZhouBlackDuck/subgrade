import logging.config
from typing import Annotated, Any, Callable

from pydantic import BaseModel, Field
import structlog
from structlog.typing import EventDict

from . import config


class LoggingConfig(BaseModel):
    version: Annotated[int, Field(ge=1, le=1)] = 1
    disable_existing_loggers: bool = False
    incremental: bool = False
    filters: dict[str, dict] = {}
    formatters: dict[str, dict] = {
        "colored": {
            "()": "structlog.stdlib.ProcessorFormatter",
            "processors": [
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            "foreign_pre_chain": [
                structlog.stdlib.add_log_level,
                structlog.stdlib.ExtraAdder(),
                structlog.processors.TimeStamper(fmt="iso"),
            ],
        }
    }
    handlers: dict[str, dict] = {
        "default": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "colored",
        }
    }
    loggers: dict[str, dict] = {}
    root: dict[str, Any] = {
        "level": "INFO",
        "handlers": ["default"],
    }


class StructlogConfig(BaseModel):
    processors: Annotated[
        list[Callable[[Any, str, EventDict], Any]], Field(frozen=True)
    ] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]


class LoggerConfig(config._LibraryConfig):
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    structlog: StructlogConfig = Field(default_factory=StructlogConfig)


def setup_logger(pre_hook: Callable[..., Any] | None = None) -> None:
    if pre_hook is not None:
        pre_hook()
    cfg = LoggerConfig.resolve_instance()
    logging.config.dictConfig(cfg.logging.model_dump())
    structlog.configure(
        processors=cfg.structlog.processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


__all__ = ["setup_logger", "LoggerConfig", "StructlogConfig", "LoggingConfig"]
