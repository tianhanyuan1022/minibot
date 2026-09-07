# 第 5 章：MCP、Hook、文件工具与手动压缩收尾

前置：第 1–4 章已经能对话、能记住、能持续工作。

目标：把练习项目 `mini-nanobot-rebuild` **对齐到参考仓库当前 `src/` 已经实现的剩余功能**。不是新里程碑，是收尾。

本章不要做：Telegram / Discord、OS sandbox、Redis、Dream Git、Skills、Cron、WebUI（见 `docs/后续升级路线.md`）。

对照参考（卡住再看，不要整文件粘贴）：`tools/basic.py`、`tools/mcp_tools.py`、`tools/__init__.py`、`hooks.py`、`memory/consolidator.py`、`memory/store.py`（`MemoryStore`）、`graph.py`、`service.py`、`config.py`、`mcp_servers.example.json`、`tests/test_basic_tools.py`、`tests/test_packaging.py`、`tests/test_runtime_build.py`。

---

## 5.1 四章之后还缺什么

| 缺口 | 第 1–4 章教到哪 | 参考仓库实际有什么 |
|------|-----------------|-------------------|
| 工作区文件工具 | 第 1 章只有 calculator / 时间；第 4 章只提了一句 `make_basic_tools` | `make_basic_tools(workspace_root)` → `read_text_file` / `write_text_file` |
| Hook | 组装清单里只有名字 | `AgentHook` / `LoggingHook` / `HookRegistry` |
| MCP | 第 1 章 pyproject 没有 `langchain-mcp-adapters` | 可选加载；失败不阻止启动 |
| 手动 `/compact` | 第 3 章把 `Consolidator` 标成可选 | `/compact` 仍走 `Consolidator`；自动压缩走 `SummarizationMiddleware` |
| 组装 | 工具列表、hooks、close 可能不完整 | 见 5.6 |

本章是改写，不是推倒重来。假设第 4 章全量测试已经绿。

---

## 5.2 工作区文件工具

### 问题定义

Agent 必须读写**配置工作区**里的文本，且不能用 `../` 逃出工作区。  
第 1 章的 `BASIC_TOOLS = [calculator, get_current_time]` 不够；也不能在 import 时用 `Path.cwd()` 绑死——运行时根目录是 `cfg.workspace_dir`。

### 要改写的文件

`tools/basic.py`、`tools/__init__.py`、`tests/test_basic_tools.py`（保留算术测试，补文件测试）、`graph.py` 的 `create_app`。

### 测试先行

对照参考：`tests/test_basic_tools.py`。

```python
from pathlib import Path
from mini_nanobot.tools.basic import make_basic_tools


def test_file_tools_are_bound_to_configured_workspace(tmp_path: Path) -> None:
    tools = {item.name: item for item in make_basic_tools(tmp_path)}
    result = tools["write_text_file"].invoke({"path": "notes/demo.txt", "content": "内容"})
    assert "已写入" in result
    assert (tmp_path / "notes" / "demo.txt").read_text(encoding="utf-8") == "内容"
    assert tools["read_text_file"].invoke({"path": "notes/demo.txt"}) == "内容"


def test_file_tools_reject_workspace_escape(tmp_path: Path) -> None:
    tools = {item.name: item for item in make_basic_tools(tmp_path)}
    result = tools["write_text_file"].invoke({"path": "../escape.txt", "content": "x"})
    assert "路径超出了工作区范围" in result
    assert not (tmp_path.parent / "escape.txt").exists()
```

```powershell
uv run pytest tests/test_basic_tools.py -v
```

### 代码骨架

越界抛 `ValueError`，工具函数捕获后**返回错误字符串**（不要甩给模型循环）。

```python
def _resolve_workspace_path(workspace_root: Path, path: str) -> Path:
    target = (workspace_root / path).resolve()
    if target != workspace_root and workspace_root not in target.parents:
        raise ValueError("路径超出了工作区范围，拒绝访问")
    return target


def make_basic_tools(workspace_root: str | Path):
    root = Path(workspace_root).resolve()

    @tool("read_text_file")
    def read_text_file(path: str) -> str:
        """读取相对于工作区根目录的一个 UTF-8 文本文件，返回其内容。"""
        ...

    @tool("write_text_file")
    def write_text_file(path: str, content: str) -> str:
        """向相对于工作区根目录的文件写入 UTF-8 文本，并创建所需父目录。"""
        ...

    return [calculator, get_current_time, read_text_file, write_text_file]

ALL_TOOLS = make_basic_tools(Path.cwd())  # 旧导入兼容；运行时不要用它
```

`write_text_file`：`target.parent.mkdir(parents=True, exist_ok=True)` 再写 UTF-8。

`tools/__init__.py`：导出 `BASIC_TOOLS`（即 `ALL_TOOLS` 别名）、`make_basic_tools`、`GOAL_TOOLS`、`load_mcp_tools`、`make_spawn_tool`。

### 设计原因

闭包把 `root` 绑进工具。`ALL_TOOLS` 指向进程 cwd，只给旧导入；`create_app` 必须 `make_basic_tools(cfg.workspace_dir)`。

### 常见错误

1. 把 `BASIC_TOOLS` 传给 `create_agent` → 文件落到启动 cwd。  
2. 用 `str.startswith` 防逃逸 → `workspace` 与 `workspace_evil` 会误判。  
3. 越界时把异常抛出工具 → 模型循环被打断。

---

## 5.3 Hook 生命周期

### 问题定义

在「一轮对话开始 / 结束 / 出错 / 清理」插入日志，但不改 `graph.py` 节点。  
模型、工具、压缩的执行期钩子已交给 LangChain middleware；这里只保留产品层的一轮运行事件。

### 要创建 / 改写

新建 `hooks.py`；`AppRuntime.hooks`；`service.py` 的 `_run_once` 与超时路径调用它。  
参考仓库没有 `tests/test_hooks.py`，练习项目请自己写。

### 测试先行

```python
class _Boom(AgentHook):
    async def before_run(self, session_id: str) -> None:
        raise RuntimeError("hook 炸了")

@pytest.mark.asyncio
async def test_hook_exception_does_not_break_registry() -> None:
    ok = _Ok()  # before_run 里记录 calls
    await HookRegistry([_Boom(), ok]).before_run("s1")  # 不得抛出
    assert ok.calls == ["before"]
```

### 代码骨架

对照 `src/mini_nanobot/hooks.py`。四个方法：`before_run(session_id)`、`after_run(session_id, final_content)`、`on_error(session_id, error)`、`on_finally(session_id)`。

```python
class HookRegistry:
    def __init__(self, hooks: list[AgentHook] | None = None) -> None:
        self.hooks = hooks or []

    async def _call(self, method: str, *args) -> None:
        for hook in self.hooks:
            try:
                await getattr(hook, method)(*args)
            except Exception:
                logger.exception("hook %s 执行失败", method)
```

`LoggingHook`：`before_run` 记 `time.monotonic()`，`after_run` 打耗时即可。  
`create_app`：`hooks = HookRegistry([LoggingHook()])`。

Service（对照 `_run_once`）：

```text
before_run
try:    ainvoke / astream
except: on_error; raise
finally: after_run; on_finally
```

外层 `TimeoutError` 再调 `hooks.on_error`。`after_run` 在 `finally` 里，**出错也会被调用**——按参考仓库实现。  
服务层测试的假 `hooks` 四个方法都要有，否则超时路径会 `AttributeError`。

### 设计原因

Hook 是观察者。吞异常写在 `HookRegistry._call`，不要散落在 Service。

### 常见错误

1. Hook 异常冒泡 → 用户看到的「处理失败」其实是日志类炸了。  
2. 假对象缺 `on_error`。  
3. 把 middleware 的 `abefore_model` 再包一层 Hook——本章不要做。

```powershell
uv run pytest tests/test_hooks.py -v
```

---

## 5.4 可选 MCP 工具加载

### 问题定义

MCP 让 Agent 使用外部 server 的工具。它是可选的：没配置、没装库、连不上，启动都必须成功。

### 要改写的文件

- `pyproject.toml`：加入 `langchain-mcp-adapters`（参考仓库未钉版本）
- `.env.example`：`MCP_CONFIG_PATH=`
- `config.py`：`mcp_config_path`
- 新建 `tools/mcp_tools.py`；根目录复制 `mcp_servers.example.json`
- `tests/test_packaging.py`：依赖名与 `MCP_CONFIG_PATH=` 必须出现
- 练习项目自写 `tests/test_mcp_tools.py`（参考仓库没有）

### 测试先行

```python
@pytest.mark.asyncio
async def test_empty_path_skips() -> None:
    assert await load_mcp_tools("") == []

@pytest.mark.asyncio
async def test_missing_file_skips(tmp_path) -> None:
    assert await load_mcp_tools(str(tmp_path / "no-such.json")) == []
```

`test_runtime_build.py` 传 `mcp_config_path=""`，避免测试去连真实 MCP。

### 代码骨架

对照 `src/mini_nanobot/tools/mcp_tools.py`。

```python
async def load_mcp_tools(mcp_config_path: str) -> list[BaseTool]:
    if not mcp_config_path:
        return []
    path = Path(mcp_config_path)
    if not path.exists():
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        return []
    servers = json.loads(path.read_text(encoding="utf-8"))
    if not servers:
        return []
    try:
        return await MultiServerMCPClient(servers).get_tools()
    except Exception:
        logger.exception("连接 MCP server 失败，本次启动不加载 MCP 工具。")
        return []
```

示例配置见参考仓库 `mcp_servers.example.json`（`npx` + `@modelcontextprotocol/server-filesystem` + `transport: stdio`）。

```python
mcp_config_path: str = Field(default_factory=lambda: os.getenv("MCP_CONFIG_PATH", ""))
```

```powershell
uv add langchain-mcp-adapters
uv run pytest tests/test_mcp_tools.py tests/test_packaging.py tests/test_runtime_build.py -v
```

### 设计原因

协议细节交给 `langchain-mcp-adapters`。我们只读 JSON、失败降级。

### 常见错误

1. MCP 失败让 `create_app` 抛错 → 没配 MCP 就无法启动。  
2. 只把 MCP 加进主 Agent，或把 `spawn` / `GOAL_TOOLS` 给了 Subagent。见 5.6。  
3. 不要自己做 MCP 客户端关闭、SSRF、工具重名——升级路线后续项；参考仓库现在只 `get_tools()` 后返回列表。

---

## 5.5 手动 `/compact` 与 MemoryStore 同步外观

### 问题定义

自动压缩：第 3 章的 `SummarizationMiddleware` + `SummaryArchiveMiddleware`。  
手动压缩：`/compact` 时参考仓库**另外**走 `Consolidator`。

`docs/后续升级路线.md` P1 写明两条路径语义不同，以后才统一。本章对齐现状，不要合并。

### 要改写的文件

补全 `memory/consolidator.py`；`MemoryStore` 与 `FileMemoryBackend` **同一目录、同一套加锁/原子写**；`memory/__init__.py` 导出 `Consolidator`、`MemoryStore`、`estimate_tokens`；`create_app` 放入 `AppRuntime`；`service.py` 的 `/compact` 调 `compact(..., force=True)` 再 `aupdate_state`。

### 两条路径（必须能口述）

| | 自动 | 手动 `/compact` |
|--|------|-----------------|
| 实现 | `SummarizationMiddleware` | `Consolidator` |
| 触发 | token 阈值 | 用户命令，`force=True` |
| 删旧消息 | middleware 自己处理 | `RemoveMessage` + 一条 `[历史摘要]` |
| 归档 | 异步 `memory.append_history` | 同步 `MemoryStore.append_history` |
| 失败 | middleware 行为 | LLM 失败则截断原文，`kind="raw"` |

### 测试先行

参考仓库没有 `test_consolidator.py`。练习项目最少覆盖：

1. 条数 `<= keep_recent`（默认 6）→ `compact(force=True)` 返回 `[]`。  
2. 更长列表 + fake LLM → 若干 `RemoveMessage`（旧消息要有 `id`）+ `[历史摘要]`；`history.jsonl` 多一行。  
3. fake LLM 抛错 → 仍推进，归档 `kind == "raw"`。  
4. `test_sync_store_remains_compatible` 必须绿（第 3 章已有）。

### 代码骨架

对照 `src/mini_nanobot/memory/consolidator.py`。

```python
@dataclass
class Consolidator:
    llm: BaseChatModel
    memory: MemoryStore
    context_window: int
    consolidation_ratio: float
    keep_recent: int = 6

    async def compact(self, messages, *, force: bool = False) -> list[BaseMessage]:
        if not force and not self.should_compact(messages):
            return []
        if len(messages) <= self.keep_recent:
            return []
        old_messages = messages[: -self.keep_recent]
        # LLM 摘要；失败则 transcript[:2000]，kind="raw"
        self.memory.append_history(summary, kind=kind)
        removals = [RemoveMessage(id=m.id) for m in old_messages if m.id is not None]
        return [*removals, HumanMessage(content=f"[历史摘要]\n{summary}")]
```

```python
memory = FileMemoryBackend(memory_dir=cfg.memory_dir)
await memory.initialize()
sync_memory = MemoryStore(memory_dir=cfg.memory_dir)  # 同一目录
consolidator = Consolidator(llm=llm, memory=sync_memory, ...)
```

`AgentContext.memory` 仍是异步 `FileMemoryBackend`。不要把 Dream 改回同步 API。

`/compact`：非 IDLE 则拒绝（第 4 章维护任务已占 session run）；有增量则 `aupdate_state({"messages": additions})`。没有 `id` 的旧消息无法删除——测试消息请带 `id`。

### 常见错误

1. `/compact` 去调 middleware 或 `force_compact`——参考仓库还没这条路径。  
2. 对 async `append_history` 忘了 `await`。  
3. 两个后端不同目录 → Dream 读不到手动压缩的摘要。

---

## 5.6 组装核对：`create_app` / cli / pyproject

对照 `graph.py` 的 `create_app`：

```text
mcp_tools      = await load_mcp_tools(cfg.mcp_config_path)
basic_tools    = make_basic_tools(cfg.workspace_dir)
subagent_tools = [*basic_tools, *mcp_tools]          # 无 spawn、无 GOAL
spawn_tool     = make_spawn_tool(subagents)
all_tools      = [*basic_tools, *GOAL_TOOLS, *mcp_tools, spawn_tool]

AppRuntime: graph, llm, memory(FileMemoryBackend), sessions, subagents,
            consolidator, hooks, max_iterations, run_timeout_seconds,
            dream_auto_threshold
finally:    await subagents.close()
```

- [ ] 文件工具绑 `cfg.workspace_dir`，不是 `Path.cwd()` / `ALL_TOOLS`
- [ ] Subagent 没有 `spawn`、没有 `create_goal` / `update_goal`
- [ ] `hooks = HookRegistry([LoggingHook()])`
- [ ] `consolidator` 用同一 `memory_dir` 的 `MemoryStore`
- [ ] `cli.py` 不必再 close subagents——`create_app` 的 `finally` 负责
- [ ] pyproject 有 `langchain-mcp-adapters`；`.env.example` 有 `MCP_CONFIG_PATH=`

```powershell
uv run pytest tests/test_runtime_build.py -v
```

---

## 5.7 Goal replace：练习项目不要回退

第 4 章要求：没有 active goal（也没有本轮 `/goal` 授权）时，`update_goal(action="replace")` 必须拒绝。

**参考仓库此处仍有已知 Bug**（`docs/后续升级路线.md` P0）：`tools/goal.py` 的 `replace` 会无条件写出新的 `status=active`。

练习项目请**继续按第 4 章更严规则实现**，不要为了逐行一致把安全检查删掉。对照参考时跳过这一处。

---

## 5.8 全量验收

```powershell
uv sync --dev
uv run pytest -v
uv run ruff check src tests
uv run mini-nanobot
```

手动：`write_text_file` / `read_text_file` 落在 `workspace/`；`../escape.txt` 被拒；不设 `MCP_CONFIG_PATH` 仍能启动；`/compact` 后历史变短且 `history.jsonl` 增长；日志有 `[hook] session=...`；`/goal` 外不能靠 replace 新建目标。

---

## 5.9 本章完成标准

- [ ] `make_basic_tools` + 路径逃逸测试绿  
- [ ] Hook 抛错不打断主流程；Service 调齐四个生命周期  
- [ ] 无 MCP 配置 / 缺文件 / 连接失败时启动成功  
- [ ] MCP 进入 `all_tools` **和** `subagent_tools`；spawn / goal 不进 Subagent  
- [ ] `/compact` 走 `Consolidator.force=True` + `RemoveMessage`；能说明它与自动压缩不同  
- [ ] `MemoryStore` 与 `FileMemoryBackend` 同目录  
- [ ] 练习项目的 replace 规则仍比参考仓库更严  
- [ ] 全量 pytest / ruff 绿  

建议提交：`feat: 完成第5章 文件工具、Hook、可选 MCP 与手动压缩`

---

## 写完五章之后你该做什么

练习项目应已覆盖参考仓库 **当前 `src/` 已实现** 的核心功能。接下来：

1. 自己 diff 练习目录和参考 `src/`，列出仍简化的点。  
2. 阅读 `docs/后续升级路线.md`。优先 **P0：Goal replace 授权**（参考仓库仍有洞；练习项目若已按第 4/5 章收紧可跳过）和 **Console pending 回执**。  
3. **不要**立刻上 Redis / WebUI / Telegram / Discord。

卡在某一节时，把问题缩到**一个函数**再问：章节名、文件路径、测试名、完整报错（不要贴 API Key）。
