from __future__ import annotations

from openai import AsyncOpenAI

from backend.config import settings

_together_client: AsyncOpenAI | None = None
_siliconflow_client: AsyncOpenAI | None = None


def active_vision_model() -> str:
    if settings.vision_provider == "together":
        return settings.together_vision_model
    if settings.vision_provider == "siliconflow":
        return settings.siliconflow_vision_model
    return settings.groq_vision_model


def active_reasoning_model() -> str:
    if settings.reasoning_provider == "together":
        return settings.together_reasoning_model
    if settings.reasoning_provider == "cerebras":
        return settings.cerebras_reasoning_model
    if settings.reasoning_provider == "siliconflow":
        return settings.siliconflow_reasoning_model
    return settings.groq_reasoning_model


def get_together_client() -> AsyncOpenAI:
    global _together_client
    if _together_client is None:
        _together_client = AsyncOpenAI(
            api_key=settings.together_api_key,
            base_url=settings.together_base_url,
            timeout=None,
            max_retries=1,
        )
    return _together_client


def get_siliconflow_client() -> AsyncOpenAI:
    global _siliconflow_client
    if _siliconflow_client is None:
        _siliconflow_client = AsyncOpenAI(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            timeout=None,
            max_retries=1,
        )
    return _siliconflow_client
