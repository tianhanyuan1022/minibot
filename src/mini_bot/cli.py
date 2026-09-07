"""程序入口：把 Bus + Channel + AgentService 组装起来跑一个完整的 mini-nanobot。

流程：

    ConsoleChannel --inbound--> MessageBus --consume--> AgentService --graph--> LLM/工具
    ConsoleChannel <--outbound-- MessageBus <--publish-- AgentService

`ChannelManager` 负责启动 channel、把 outbound 消息路由回 channel；
`AgentService` 负责跑 LangGraph 图。两边只通过 `MessageBus` 打交道。
"""

from __future__ import annotations

import asyncio
import logging

from .bus import MessageBus
from .channels import ChannelManager, ConsoleChannel
from .config import ConfigurationError, load_config
from .graph import create_app
from .service import AgentService

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


async def _main() -> None:
    cfg = load_config()
    bus = MessageBus()

    async with create_app(cfg) as runtime:
        console = ConsoleChannel(bus, runtime.sessions)
        manager = ChannelManager(bus, [console])
        service = AgentService(bus, runtime)
        service_task = asyncio.create_task(service.run())
        await manager.start()
        try:
            await manager.wait_until_all_stopped()
        finally:
            service_task.cancel()
            await manager.stop()
            await asyncio.gather(service_task, return_exceptions=True)


def run() -> None:
    """`uv run mini-nanobot` 的入口。"""
    try:
        asyncio.run(_main())
    except ConfigurationError as exc:
        print(f"启动失败：{exc}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
