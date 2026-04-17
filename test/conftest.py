"""pytest 固定装置：将 cwd 与可选环境变量设为各套 fixture 的项目根。

使用 ``tmp_path`` / ``tmp_path_factory`` 的用例：pytest 会在单测结束后自动删除对应临时目录。

在 ``test/fixtures`` 下可能写入磁盘的用例（如 dup_module）：通过专用 fixture 在 teardown 中删除生成项。
"""

from __future__ import annotations
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _cleanup_dup_module_generated_settings() -> None:
    """删除 dup_module 测试中由库自动生成的 ``settings/dup.{yaml,yml}`` 及空目录。"""
    settings = FIXTURES / "dup_module" / "settings"
    if not settings.is_dir():
        return
    for name in ("dup.yaml", "dup.yml"):
        p = settings / name
        if p.is_file():
            p.unlink()
    try:
        next(settings.iterdir())
    except StopIteration:
        settings.rmdir()


@pytest.fixture
def dup_module_env(monkeypatch: pytest.MonkeyPatch) -> Path:
    """进入 ``fixtures/dup_module``；测试结束（含失败）后清理自动生成的 settings 文件。"""
    root = FIXTURES / "dup_module"
    monkeypatch.chdir(root)
    monkeypatch.delenv("CFG_BASEDIR", raising=False)
    monkeypatch.delenv("SETTINGS_BASEDIR", raising=False)
    yield root
    _cleanup_dup_module_generated_settings()


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
