from __future__ import annotations

from typing import Literal, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Groq
    groq_api_key: str = Field(..., env="GROQ_API_KEY")
    groq_vision_model: str = Field("meta-llama/llama-4-scout-17b-16e-instruct", env="GROQ_VISION_MODEL")
    groq_reasoning_model: str = Field("meta-llama/llama-4-maverick-17b-128e-instruct", env="GROQ_REASONING_MODEL")

    # Reasoning provider
    reasoning_provider: Literal["groq", "cerebras"] = Field("groq", env="REASONING_PROVIDER")
    cerebras_api_key: Optional[str] = Field(None, env="CEREBRAS_API_KEY")
    cerebras_base_url: str = Field("https://api.cerebras.ai/v1", env="CEREBRAS_BASE_URL")
    cerebras_reasoning_model: str = Field("llama-3.3-70b", env="CEREBRAS_REASONING_MODEL")

    # Database
    db_url: str = Field("sqlite+aiosqlite:///./novin.db", env="DB_URL")

    # Frame processing
    frame_jpeg_quality: int = Field(75, env="FRAME_JPEG_QUALITY")
    frame_max_width: int = Field(1280, env="FRAME_MAX_WIDTH")

    # Reasoning
    reasoning_timeout_ms: int = Field(400, env="REASONING_TIMEOUT_MS")

    # Notifications
    webhook_url: Optional[str] = Field(None, env="WEBHOOK_URL")
    slack_webhook_url: Optional[str] = Field(None, env="SLACK_WEBHOOK_URL")
    smtp_host: Optional[str] = Field(None, env="SMTP_HOST")
    smtp_port: int = Field(587, env="SMTP_PORT")
    smtp_user: Optional[str] = Field(None, env="SMTP_USER")
    smtp_pass: Optional[str] = Field(None, env="SMTP_PASS")
    alert_email_to: Optional[str] = Field(None, env="ALERT_EMAIL_TO")

    # CORS
    cors_origins: list[str] = Field(["http://localhost:5173"], env="CORS_ORIGINS")

    # Local API credential (optional)
    local_api_credential: Optional[str] = Field(None, env="LOCAL_API_CREDENTIAL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
