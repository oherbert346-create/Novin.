from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    from_agent: str
    payload: Any


class AgentMessageBus:
    def __init__(self, agent_ids: list[str]) -> None:
        self._queues: dict[str, asyncio.Queue] = {
            agent_id: asyncio.Queue() for agent_id in agent_ids
        }
        self._published: dict[str, Any] = {}
        self._done_event = asyncio.Event()
        self._total = len(agent_ids)

    async def publish(self, from_agent: str, payload: Any) -> None:
        self._published[from_agent] = payload
        msg = AgentMessage(from_agent=from_agent, payload=payload)
        for agent_id, q in self._queues.items():
            if agent_id != from_agent:
                await q.put(msg)
        if len(self._published) >= self._total:
            self._done_event.set()

    def get_published(self, agent_id: str | None = None) -> dict[str, Any]:
        if agent_id:
            return {k: v for k, v in self._published.items() if k != agent_id}
        return dict(self._published)

    async def wait_for_all(self, timeout_ms: int | None = None) -> bool:
        if timeout_ms is None:
            await self._done_event.wait()
            return True

        try:
            await asyncio.wait_for(self._done_event.wait(), timeout=timeout_ms / 1000.0)
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "Message bus timeout after %d ms — published: %s",
                timeout_ms,
                list(self._published.keys()),
            )
            return False

    async def drain(self, agent_id: str) -> list[AgentMessage]:
        q = self._queues.get(agent_id)
        if not q:
            return []
        messages = []
        while not q.empty():
            try:
                messages.append(q.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages
