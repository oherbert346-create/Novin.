from __future__ import annotations

from typing import Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Groq
    groq_api_key: Optional[str] = Field(None)
    groq_vision_model: str = Field("meta-llama/llama-4-scout-17b-16e-instruct")
    groq_reasoning_model: str = Field("qwen/qwen3-32b")
    groq_reasoning_max_tokens: int = Field(600)
    groq_enable_thinking: bool = Field(False)

    # Vision provider
    vision_provider: Literal["groq", "together", "siliconflow"] = Field("siliconflow")

    # Reasoning provider
    reasoning_provider: Literal["groq", "cerebras", "together", "siliconflow"] = Field("groq")
    cerebras_api_key: Optional[str] = Field(None)
    cerebras_base_url: str = Field("https://api.cerebras.ai/v1")
    cerebras_reasoning_model: str = Field("gpt-oss-120b")
    cerebras_max_completion_tokens: int = Field(1000)
    together_api_key: Optional[str] = Field(None)
    together_base_url: str = Field("https://api.together.xyz/v1")
    together_vision_model: str = Field("Qwen/Qwen3-VL-8B-Instruct")
    together_reasoning_model: str = Field("MiniMaxAI/MiniMax-M2.5")
    together_reasoning_max_tokens: int = Field(1000)
    siliconflow_api_key: Optional[str] = Field(None)
    siliconflow_base_url: str = Field("https://api.siliconflow.com/v1")
    siliconflow_vision_model: str = Field("Qwen/Qwen2.5-VL-7B-Instruct")
    siliconflow_reasoning_model: str = Field("deepseek-ai/DeepSeek-V3.2")

    # Database
    db_url: str = Field("sqlite+aiosqlite:///./novin-home.db")

    # Frame processing
    frame_jpeg_quality: int = Field(75)
    frame_max_width: int = Field(1280)

    # Reasoning
    reasoning_timeout_ms: int = Field(1200)
    alert_threshold: float = Field(0.70)
    min_severity_to_alert: str = Field("low")
    reasoning_temperature: float = Field(0.0)
    reasoning_top_p: float = Field(0.1)
    siliconflow_reasoning_max_tokens: int = Field(1000)
    siliconflow_enable_thinking: bool = Field(True)
    siliconflow_thinking_budget: int = Field(1024)
    release_latency_budget_ms: int = Field(3000)
    vision_latency_budget_ms: int = Field(1200)
    reasoning_latency_budget_ms: int = Field(1200)
    overhead_latency_budget_ms: int = Field(600)

    # Notifications — WEBHOOK_URL = default; WEBHOOK_URL_{home_id} = per-home override
    webhook_url: Optional[str] = Field(None)
    slack_webhook_url: Optional[str] = Field(None)
    smtp_host: Optional[str] = Field(None)
    smtp_port: int = Field(587)
    smtp_user: Optional[str] = Field(None)
    smtp_pass: Optional[str] = Field(None)
    alert_email_to: Optional[str] = Field(None)
    shadow_mode: bool = Field(True)  # Default ON for pilot safety; disable explicitly when ready for live notifications
    shadow_webhook_url: Optional[str] = Field(None)

    # Webhook security — sign outbound payloads with HMAC-SHA256 (X-Novin-Signature header)
    webhook_secret: Optional[str] = Field(None)

    # Notification reliability
    webhook_timeout_s: float = Field(10.0)  # httpx timeout for outbound webhook/Slack calls
    webhook_max_retries: int = Field(3)      # max retry attempts (exponential backoff: 1s, 2s, 4s)
    slack_rate_limit_s: float = Field(1.0)   # minimum seconds between Slack messages per site

    # CORS
    cors_origins: list[str] = Field(["http://localhost:8000"])

    # API credentials — required for /api paths
    local_api_credential: Optional[str] = Field(None)
    ingest_api_key: Optional[str] = Field(None)
    
    # Basic HTTP Auth — required for all /api endpoints
    basic_auth_user: Optional[str] = Field(None)
    basic_auth_pass: Optional[str] = Field(None)

    # Ingest
    ingest_async_default: bool = Field(True)
    stream_sample_every_n_frames: int = Field(30)
    enable_agent_memory: bool = Field(True)


settings = Settings()
