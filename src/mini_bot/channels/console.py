"""控制台 Channel —— BaseChannel 的参考实现。

这就是之前直接写在 `cli.py` 里的输入/输出循环，现在被规整成一个符合
`BaseChannel` 接口的实现。等你要接入真正的平台（Telegram/Discord/……）时，
只需要照着这个文件的结构再写一个，`AgentService` 和 `graph.py` 都不需要改。
"""

from __future__ import annotations

import asyncio
from typing import Any

from rich.console import Console as RichConsole
from rich.markdown import Markdown

from ..bus import MessageBus
from ..session import SessionManager
from .base import BaseChannel

_console = RichConsole()

HELP_TEXT = (
    "[bold]可用命令[/bold]\n"
    "  /new      开启一个新会话（新的 session_id，历史不再延续）\n"
    "  /goal 目标 创建并持续执行一个目标\n"
    "  /stop     停止当前会话正在执行的任务\n"
    "  /compact  手动触发一次上下文压缩（Consolidator）\n"
    "  /dream    手动触发一次长期记忆巩固（Dream）\n"
    "  /status   查看当前会话状态\n"
    "  /help     显示本帮助信息\n"
    "  /exit     退出\n"
)


class ConsoleChannel(BaseChannel):
    """用标准输入/输出模拟一个聊天平台。"""

    name = "console"
    supports_streaming = True

    def __init__(self, bus: MessageBus, sessions: SessionManager) -> None:
        super().__init__(bus)
        self.sessions = sessions
        self.session_id = ""
        self._stream_started: set[str] = set()

    def _restore_active_session(self) -> None:
        """恢复持久化的活动会话；首次启动时创建一个。"""
        active = self.sessions.get_active()
        if active is None:
            active = self.sessions.create("控制台会话")
        self.sessions.activate(active.thread_id)
        self.session_id = active.thread_id

    def _create_session(self) -> None:
        """创建并激活一个可持久化的新会话。"""
        session = self.sessions.create("控制台会话")
        self.sessions.activate(session.thread_id)
        self.session_id = session.thread_id

    async def start(self) -> None:
        self._restore_active_session()
        self._running = True
        _console.print(
            "[bold cyan]mini-nanobot[/bold cyan] —— 输入消息开始对话，输入 /help 查看命令。\n" \
            "[bold red]mcp[/bold red]-- [bold yellow]12306-mcp, 八字-mcp[/bold yellow]\n"
        )
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                line = await loop.run_in_executor(
                    None, _console.input, "[bold green]you>[/bold green] "
                )
            except (EOFError, KeyboardInterrupt):
                self._running = False
                break

            line = line.strip()
            if not line:
                continue
            if line == "/exit":
                self._running = False
                break
            if line == "/new":
                self._create_session()
                _console.print("[yellow]已开启一个新会话。[/yellow]\n")
                continue
            if line == "/help":
                _console.print(HELP_TEXT)
                continue

            # 其余斜杠命令原样转发给 AgentService 处理，
            # 普通文本也是同一条路径——channel 不需要知道两者的区别。
            await self._handle_message(self.session_id, line)

    async def stop(self) -> None:
        self._running = False

    async def send(
        self, session_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        # 只有非流式路径（比如 /status /compact /dream 的回复）会走到这里；
        # 正常对话走 send_delta + send_delta_end。
        _console.print("[bold magenta]bot>[/bold magenta]")
        _console.print(Markdown(content) if content else "[dim](空回复)[/dim]")
        _console.print()

    async def send_delta(
        self, session_id: str, delta: str, metadata: dict[str, Any] | None = None
    ) -> None:
        if session_id not in self._stream_started:
            self._stream_started.add(session_id)
            _console.print("[bold magenta]bot>[/bold magenta] ", end="")
        _console.print(delta, end="")

    async def send_delta_end(
        self, session_id: str, metadata: dict[str, Any] | None = None
    ) -> None:
        self._stream_started.discard(session_id)
        _console.print("\n")
