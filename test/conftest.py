"""pytest 固定装置：将 cwd 与可选环境变量设为各套 fixture 的项目根。"""

from __future__ import annotations
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def project_root_without_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    """不使用 CFG_BASEDIR / SETTINGS_BASEDIR：依赖相对路径 configs、settings，cwd 为项目根。"""
    root = FIXTURES / "without_env"
    monkeypatch.chdir(root)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    yield root


@pytest.fixture
def project_root_with_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    """通过环境变量指定配置目录与设置目录（绝对路径），cwd 仍设为该套 fixture 的项目根。"""
    root = FIXTURES / "with_env"
    monkeypatch.chdir(root)
    monkeypatch.setenv("CFG_BASEDIR", str((root / "configs").resolve()))
    monkeypatch.setenv("SETTINGS_BASEDIR", str((root / "settings").resolve()))
    yield root
