# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

mycli 是一个增强的 MySQL 命令行客户端，提供智能自动补全和语法高亮功能。基于 prompt_toolkit 构建，支持上下文感知的智能补全、多行查询、语法高亮等功能。

## 核心技术栈

- **Python**: 3.10+ (使用现代类型注解)
- **包管理**: uv (取代传统的 pip/virtualenv)
- **数据库**: PyMySQL (纯 Python MySQL 客户端)
- **UI**: prompt_toolkit (终端 UI 框架)
- **SQL 解析**: sqlparse, sqlglot
- **测试**: pytest (单元测试), behave (BDD 功能测试)
- **代码质量**: ruff (linting + formatting), mypy (类型检查)

## 开发环境设置

```bash
# 安装依赖 (包含开发和 SSH 支持)
uv sync --extra dev --extra ssh

# 运行 mycli (使用本地更改)
uv run mycli

# 连接到测试数据库
uv run mycli -h localhost -u root
```

## 常用命令

### 测试
```bash
# 运行所有测试 (pytest + behave)
uv run tox

# 仅运行 pytest 单元测试
uv run pytest test/

# 运行单个测试文件
uv run pytest test/test_completion_engine.py

# 运行特定测试
uv run pytest test/test_completion_engine.py::test_function_name

# 运行带覆盖率的测试
coverage run -m pytest -v test
coverage report -m

# 运行 BDD 功能测试
behave test/features

# 运行特定功能测试
behave test/features/auto_vertical.feature
```

### 代码质量检查
```bash
# Ruff linting
ruff check

# Ruff formatting (检查但不修改)
ruff format --diff

# Ruff formatting (自动修复)
ruff format

# 类型检查
mypy mycli
```

### PR 提交前检查
```bash
# 必须运行以下命令确保代码质量
uv run ruff check && uv run ruff format && uv run mypy --install-types .
```
- 添加改动说明到 `changelog.md`
- 添加作者名到 `AUTHORS` 文件 (如果是新贡献者)

### 测试数据库配置
部分测试需要 MySQL 连接。通过环境变量配置：
```bash
export PYTEST_HOST=localhost
export PYTEST_USER=mycli
export PYTEST_PASSWORD=myclirocks
export PYTEST_PORT=3306
export PYTEST_CHARSET=utf8
```

## 架构说明

### 核心组件

#### 1. 主应用入口 (`mycli/main.py`)
- **MyCli 类**: 应用核心，管理 REPL 循环
  - 初始化 prompt_toolkit PromptSession
  - 处理配置文件 (~/.myclirc)
  - 管理数据库连接
  - 协调补全器、执行器、词法分析器等组件
- **关键方法**:
  - `run_cli()`: 主事件循环
  - `execute_command()`: 执行 SQL 或特殊命令
  - `handle_editor_command()`: 处理编辑器集成

#### 2. SQL 执行 (`mycli/sqlexecute.py`)
- **SQLExecute 类**: 数据库连接和查询执行
  - 管理 PyMySQL 连接 (包括 SSH tunnel 支持)
  - 执行查询并返回结果
  - 处理事务、服务器信息、数据库元数据
- **ServerInfo 类**: 识别数据库类型 (MySQL/MariaDB/Percona/TiDB)

#### 3. 智能补全系统
- **SQLCompleter** (`mycli/sqlcompleter.py`): prompt_toolkit Completer 实现
  - 维护数据库对象缓存 (表、列、函数、关键字等)
  - 提供补全建议

- **completion_engine** (`mycli/packages/completion_engine.py`):
  - `suggest_type()`: 根据光标位置和 SQL 上下文决定补全类型
  - 使用 sqlparse 分析 SQL 结构
  - 返回上下文感知的补全建议 (如 FROM 后只建议表名)

- **CompletionRefresher** (`mycli/completion_refresher.py`):
  - 后台线程定期刷新数据库元数据
  - 避免阻塞主 UI 线程

#### 4. 特殊命令系统 (`mycli/packages/special/`)
- **架构**: 装饰器模式注册特殊命令 (以 `\` 开头)
- **main.py**:
  - `@special_command` 装饰器
  - `COMMANDS` 全局注册表
  - `execute()` 函数分发命令
- **子模块**:
  - `dbcommands.py`: 数据库操作命令 (\u, \d, \dt, \l 等)
  - `iocommands.py`: I/O 重定向 (\o, \i, \T 等)
  - `favoritequeries.py`: 收藏查询 (\fs, \f, \fd)
  - `llm.py`: LLM 集成命令 (可通过 `MYCLI_LLM_OFF=1` 禁用)
  - `delimitercommand.py`: DELIMITER 命令支持

#### 5. 语法高亮 (`mycli/lexer.py`)
- **MyCliLexer**: 基于 Pygments 的 SQL 词法分析器
- 集成到 prompt_toolkit 的 PygmentsLexer

#### 6. 键盘绑定 (`mycli/key_bindings.py`)
- **关键绑定**:
  - `F2`: 切换智能补全
  - `F3`: 切换多行模式
  - `F4`: 切换 Vi/Emacs 编辑模式
  - `Tab`: 强制补全
  - `C-n/C-p`: 补全导航
  - 支持自定义 fzf 历史搜索

#### 7. 配置管理 (`mycli/config.py`)
- 读取配置文件优先级:
  1. `/etc/myclirc`
  2. `$XDG_CONFIG_HOME/mycli/myclirc`
  3. `~/.myclirc` (用户配置)
- 支持 MySQL 配置文件 (~/.my.cnf)
- 使用 configobj 解析

### 数据流

```
用户输入
  ↓
main.py (MyCli.run_cli)
  ↓
prompt_toolkit PromptSession
  ├→ SQLCompleter (补全建议)
  │   └→ completion_engine.suggest_type() (上下文分析)
  ↓
输入完成 (回车)
  ↓
MyCli.execute_command()
  ├→ 特殊命令? → special.execute() → 各种处理器
  └→ SQL 语句? → SQLExecute.run() → PyMySQL → 数据库
  ↓
格式化输出 (cli_helpers.tabular_output)
```

### 测试架构

- **test/**: pytest 单元测试
  - 测试各个模块的功能 (补全、解析、配置等)
  - 使用 mock 隔离数据库依赖

- **test/features/**: behave BDD 功能测试
  - `.feature` 文件: Gherkin 语法测试场景
  - `steps/`: 步骤定义 (Python)
  - 需要真实 MySQL 连接
  - 使用 pexpect 模拟终端交互

## 特殊命令框架使用

添加新特殊命令:

```python
# 在 mycli/packages/special/dbcommands.py 或其他模块

from mycli.packages.special.main import special_command, ArgType

@special_command('\\mycommand', '\\mc', '命令描述', arg_type=ArgType.NO_QUERY)
def my_command_handler(cur, arg, **kwargs):
    """处理 \\mycommand 命令"""
    # cur: pymysql Cursor 对象
    # arg: 命令参数字符串
    # 返回 (标题, 行数据, 状态消息) 的可迭代对象
    yield (None, None, "执行成功")
```

## prompt_toolkit 定制

mycli 深度集成 prompt_toolkit:
- **Lexer**: 自定义语法高亮
- **Completer**: 智能 SQL 补全
- **KeyBindings**: 自定义快捷键
- **Toolbar**: 动态状态栏显示
- **Auto-suggest**: 基于历史的建议
- **ModalCursorShapeConfig**: 根据编辑模式改变光标形状

## 代码风格

- 遵循 ruff 配置 (pyproject.toml)
- 最大行长: 140 字符
- 使用现代类型注解 (from __future__ import annotations)
- 相对导入被禁止 (ban-relative-imports)
- 保持格式一致性: 字符串引号保持原样 (quote-style = 'preserve')

## CI/CD

**PR 触发**: lint, typecheck, 多版本测试 (Python 3.10-3.14)

**发布流程**: 在 GitHub 创建新 release 会自动触发 GitHub Action，运行测试、构建 wheel 并上传到 PyPI
