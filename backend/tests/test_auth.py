"""Tests for BasicAuthMiddleware."""

from __future__ import annotations

import base64
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.auth import BasicAuthMiddleware
from backend.config import settings


def _build_app() -> TestClient:
    """Create a minimal FastAPI app with BasicAuthMiddleware applied."""
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware)

    @app.get("/api/test")
    async def api_test():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return TestClient(app, raise_server_exceptions=False)


def _basic_header(username: str, password: str) -> str:
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {credentials}"


# Shared test client — middleware reads settings at request time, so
# patching settings before each request is sufficient.
_CLIENT = _build_app()


class TestBasicAuthMiddlewareDisabled(unittest.TestCase):
    """When no credentials are configured, middleware passes all requests through."""

    def setUp(self):
        self._orig_user = settings.basic_auth_user
        self._orig_pass = settings.basic_auth_pass
        settings.basic_auth_user = None
        settings.basic_auth_pass = None

    def tearDown(self):
        settings.basic_auth_user = self._orig_user
        settings.basic_auth_pass = self._orig_pass

    def test_api_path_allowed_without_auth(self):
        resp = _CLIENT.get("/api/test")
        self.assertEqual(resp.status_code, 200)

    def test_health_path_allowed_without_auth(self):
        resp = _CLIENT.get("/health")
        self.assertEqual(resp.status_code, 200)


class TestBasicAuthMiddlewareEnabled(unittest.TestCase):
    """When credentials are configured, middleware enforces Basic Auth on /api paths."""

    USER = "admin"
    PASS = "secret"

    def setUp(self):
        self._orig_user = settings.basic_auth_user
        self._orig_pass = settings.basic_auth_pass
        settings.basic_auth_user = self.USER
        settings.basic_auth_pass = self.PASS

    def tearDown(self):
        settings.basic_auth_user = self._orig_user
        settings.basic_auth_pass = self._orig_pass

    def test_correct_credentials_allowed(self):
        resp = _CLIENT.get(
            "/api/test",
            headers={"Authorization": _basic_header(self.USER, self.PASS)},
        )
        self.assertEqual(resp.status_code, 200)

    def test_missing_auth_header_returns_401(self):
        resp = _CLIENT.get("/api/test")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("WWW-Authenticate", resp.headers)
        self.assertEqual(resp.headers["WWW-Authenticate"], 'Basic realm="Novin API"')

    def test_wrong_password_returns_401(self):
        resp = _CLIENT.get(
            "/api/test",
            headers={"Authorization": _basic_header(self.USER, "wrongpass")},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "Invalid credentials")

    def test_wrong_username_returns_401(self):
        resp = _CLIENT.get(
            "/api/test",
            headers={"Authorization": _basic_header("wronguser", self.PASS)},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "Invalid credentials")

    def test_both_wrong_returns_401(self):
        resp = _CLIENT.get(
            "/api/test",
            headers={"Authorization": _basic_header("wronguser", "wrongpass")},
        )
        self.assertEqual(resp.status_code, 401)

    def test_bearer_token_rejected(self):
        """Non-Basic Authorization scheme should be rejected."""
        resp = _CLIENT.get(
            "/api/test",
            headers={"Authorization": "Bearer sometoken"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "Missing or invalid Authorization header")

    def test_malformed_base64_returns_401(self):
        resp = _CLIENT.get(
            "/api/test",
            headers={"Authorization": "Basic !!!not-valid-base64!!!"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "Malformed Authorization header")

    def test_no_colon_in_decoded_credentials_returns_401(self):
        """Credentials without ':' separator should be rejected."""
        encoded = base64.b64encode(b"nocolon").decode()
        resp = _CLIENT.get(
            "/api/test",
            headers={"Authorization": f"Basic {encoded}"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "Malformed Authorization header")

    def test_public_health_path_bypasses_auth(self):
        resp = _CLIENT.get("/health")
        self.assertEqual(resp.status_code, 200)


class TestBasicAuthConstantTimeComparison(unittest.TestCase):
    """Verify both credentials are always evaluated and return identical error responses.

    True timing guarantees are provided by hmac.compare_digest at the language level.
    These tests verify the behavioral contract: the same "Invalid credentials" detail
    and status code are returned regardless of which credential is wrong, so an attacker
    cannot enumerate valid usernames from a distinct error response.
    """

    def setUp(self):
        self._orig_user = settings.basic_auth_user
        self._orig_pass = settings.basic_auth_pass
        settings.basic_auth_user = "admin"
        settings.basic_auth_pass = "secret"

    def tearDown(self):
        settings.basic_auth_user = self._orig_user
        settings.basic_auth_pass = self._orig_pass

    def test_wrong_username_correct_password_returns_401(self):
        resp = _CLIENT.get(
            "/api/test",
            headers={"Authorization": _basic_header("baduser", "secret")},
        )
        self.assertEqual(resp.status_code, 401)

    def test_correct_username_wrong_password_returns_401(self):
        resp = _CLIENT.get(
            "/api/test",
            headers={"Authorization": _basic_header("admin", "badpass")},
        )
        self.assertEqual(resp.status_code, 401)

    def test_same_error_detail_regardless_of_which_credential_is_wrong(self):
        """Both wrong-username and wrong-password should return the exact same response body.

        An attacker must not be able to distinguish which credential is incorrect
        from the response detail.
        """
        resp_bad_user = _CLIENT.get(
            "/api/test",
            headers={"Authorization": _basic_header("baduser", "secret")},
        )
        resp_bad_pass = _CLIENT.get(
            "/api/test",
            headers={"Authorization": _basic_header("admin", "badpass")},
        )
        self.assertEqual(resp_bad_user.status_code, resp_bad_pass.status_code)
        self.assertEqual(resp_bad_user.json()["detail"], resp_bad_pass.json()["detail"])


if __name__ == "__main__":
    unittest.main()
