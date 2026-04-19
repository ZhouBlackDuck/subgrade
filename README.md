# subgrade

基于 **Pydantic Settings** 的分层配置与基于 **structlog + `logging.dictConfig`** 的统一日志脚手架，用于在业务项目中约定 `configs/`、`settings/` 目录与库内可替换配置。

- **Python**：3.10+
- **许可证**：MIT

## 功能概览

| 模块 | 说明 |
|------|------|
| **配置** | 扫描项目根下 `configs/*.py`，按模块名懒加载；每个模块一个 `Config` 子类，对应 `settings/<模块名>.yaml`；支持环境变量覆盖与 YAML 模板占位。 |
| **库内配置** | `_LibraryConfig`（及子类如 `LoggerConfig`）由元类登记子类链，通过 `resolve_instance()` 解析为**工程侧最末子类**的单例，用于宿主项目覆盖库默认行为。 |
| **日志** | `setup_logger()` 使用 `LoggerConfig.resolve_instance()`，对 `logging` 做 `dictConfig`，并 `structlog.configure`；可继承 `LoggingConfig` / `StructlogConfig` / `LoggerConfig` 定制行为。 |

## 安装

```bash
pip install subgrade
```

若从源码安装：

```bash
pip install .
# 或 poetry install
```

## 项目根与目录约定

导入 `cfg` 时会在**项目根**下查找 `configs/`（必须存在）。项目根解析顺序：

1. 环境变量 `SUBGRADE_PROJECT_ROOT` 或 `PROJECT_ROOT`
2. 若当前工作目录下已有 `configs/`，则使用该目录为根
3. 否则自 `cwd` 向上查找含 `pyproject.toml` / `setup.cfg` / `setup.py` / `.git` 的目录
4. 仍找不到则使用 `cwd`

可选环境变量：

| 变量 | 含义 |
|------|------|
| `CFG_BASEDIR` | 覆盖默认的 `configs` 目录（默认：`<project_root>/configs`） |
| `SETTINGS_BASEDIR` | 覆盖默认的 `settings` 目录（默认：`<project_root>/settings`） |

## 应用侧配置（`cfg`）

在 `configs/` 下为每个 Python 文件（合法标识符名）定义**恰好一个**继承 `Config` 的类，文件名（不含扩展名）即 `cfg` 上的属性名。

```text
<project_root>/
  configs/
    app.py          # -> cfg.app
  settings/
    app.yaml        # 与模块名对应（小写 stem）
```

```python
from subgrade import cfg

# 首次访问 cfg.app 时加载 configs/app.py 并实例化，之后同进程内缓存同一实例
label = cfg.app.label
```

- 默认从 `settings/<模块名>.yaml` 读取；若文件不存在，会按**必填字段**生成占位 YAML 模板（类创建阶段或首次实例化时，视具体类型而定）。
- 环境变量前缀默认由类名推导（去掉 `Config` 后大写 + `_`）。

## 库内可替换配置（`LoggerConfig` 等）

库内类型继承 `_LibraryConfig`（如 `LoggerConfig`）。在**应用**中再继承该类，则 `resolve_instance()` 返回应用子类的单例。

```python
from subgrade.logger import LoggerConfig, setup_logger

class MyLoggerConfig(LoggerConfig):
    # 覆盖或扩展字段；默认 YAML 常为 settings/LOGGERCONFIG.yaml（类名大写）
    pass

setup_logger()  # 内部使用 LoggerConfig.resolve_instance()
```

- 不要在 `_LibraryConfig` 根类上调用 `resolve_instance()`（会报错）；应使用具体子类（如 `LoggerConfig`）。
- 默认设置文件名为**类名全大写** + `.yaml`，位于 `settings/` 下。

## 日志（`setup_logger`）

```python
from subgrade import setup_logger

setup_logger()                          # 按当前解析到的 LoggerConfig 应用 logging + structlog
setup_logger(pre_hook=lambda: None)   # 在 dictConfig / structlog.configure 之前执行钩子
```

- `LoggingConfig`：符合 `logging.config.dictConfig` 的字典结构（默认带 `structlog.stdlib.ProcessorFormatter` 与控制台彩色输出）。
- `StructlogConfig`：`structlog.configure` 的 `processors` 等。
- 三者均可通过**子类**在工程中覆盖（见 `subgrade.logger` 模块内定义）。

## 开发与测试

使用 Poetry 时：

```bash
poetry install --with dev
pytest test/ -v
```

或单独安装 `pytest` 后，在项目根执行 `pytest test/ -v`（见 `pyproject.toml` 中 `pytest` 配置）。

测试依赖项目根下存在可用的 `configs/`（或由用例使用临时目录 + `SUBGRADE_PROJECT_ROOT`）。

## 公开 API（`subgrade` 包）

| 符号 | 说明 |
|------|------|
| `cfg` | `Configs` 单例，按属性名访问各 `configs` 模块配置实例 |
| `Config` | 应用侧 `configs/*.py` 中使用的基类 |
| `project_root` | 从 `subgrade.config` 导入，返回当前解析的项目根 `Path` |
| `setup_logger` | 应用 logging + structlog |
| `LoggerConfig` / `LoggingConfig` / `StructlogConfig` | 日志相关配置模型，可被子类化 |

更底层类型（如 `_LibraryConfig`）位于 `subgrade.config`，一般通过子类与 `resolve_instance()` 使用。

## 相关文件

- `subgrade/config.py` — `cfg`、`Config`、`_LibraryConfig`、项目根与 YAML 逻辑  
- `subgrade/logger.py` — `LoggerConfig`、`setup_logger`、默认 structlog 处理器链  

---

作者见 `pyproject.toml`；问题与贡献请通过仓库 Issue / PR 进行。
