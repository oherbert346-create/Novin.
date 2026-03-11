"""Basic HTTP Authentication middleware for API endpoints."""

from __future__ import annotations

import base64
import logging
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import settings

logger = logging.getLogger(__name__)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic Authentication for /api endpoints."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip auth for public endpoints
        public_paths = ["/health", "/docs", "/openapi.json", "/redoc"]
        if any(request.url.path.startswith(path) for path in public_paths):
            return await call_next(request)

        # Only enforce Basic Auth on /api paths if credentials are configured
        if request.url.path.startswith("/api"):
            if settings.basic_auth_user and settings.basic_auth_pass:
                auth_header = request.headers.get("Authorization")
                
                if not auth_header or not auth_header.startswith("Basic "):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Missing or invalid Authorization header"},
                        headers={"WWW-Authenticate": 'Basic realm="Novin API"'},
                    )

                try:
                    # Decode Basic Auth
                    encoded_credentials = auth_header.split(" ", 1)[1]
                    decoded = base64.b64decode(encoded_credentials).decode("utf-8")
                    username, password = decoded.split(":", 1)

                    # Validate credentials
                    if username != settings.basic_auth_user or password != settings.basic_auth_pass:
                        logger.warning("Failed Basic Auth attempt: user=%s from=%s", username, request.client.host if request.client else "unknown")
                        return JSONResponse(
                            status_code=401,
                            content={"detail": "Invalid credentials"},
                            headers={"WWW-Authenticate": 'Basic realm="Novin API"'},
                        )
                except (ValueError, UnicodeDecodeError):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Malformed Authorization header"},
                        headers={"WWW-Authenticate": 'Basic realm="Novin API"'},
                    )

        return await call_next(request)
