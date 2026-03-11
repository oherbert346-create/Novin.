from __future__ import annotations

from contextvars import ContextVar

from backend.config import settings

_memory_enabled_override: ContextVar[bool | None] = ContextVar("memory_enabled_override", default=None)
_benchmark_enabled_override: ContextVar[bool | None] = ContextVar("benchmark_enabled_override", default=None)


def memory_enabled() -> bool:
    override = _memory_enabled_override.get()
    if override is not None:
        return override
    return settings.enable_agent_memory


def set_memory_enabled(enabled: bool | None):
    return _memory_enabled_override.set(enabled)


def reset_memory_enabled(token) -> None:
    _memory_enabled_override.reset(token)


def benchmark_enabled() -> bool:
    override = _benchmark_enabled_override.get()
    if override is not None:
        return override
    return False


def set_benchmark_enabled(enabled: bool | None):
    return _benchmark_enabled_override.set(enabled)


def reset_benchmark_enabled(token) -> None:
    _benchmark_enabled_override.reset(token)
