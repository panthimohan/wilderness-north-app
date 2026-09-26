"""Production-mode CORS, operator lease, and single-process guard tests."""
from __future__ import annotations

import time
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import OPERATOR_SESSION_CONFLICT, create_app


class DeploymentConfigurationTests(unittest.TestCase):
    def test_operator_cookie_blocks_a_second_browser_and_health_remains_available(self):
        with TemporaryDirectory() as directory:
            app = create_app(
                deployment_mode="single_operator",
                operator_process_lock_path=Path(directory) / "backend.lock",
                operator_cookie_secure=False,
            )
            with TestClient(app) as client:
                initial = client.get('/api/session')
                self.assertEqual(initial.status_code, 200)
                cookie = initial.cookies.get('wilderness_north_operator')
                self.assertTrue(cookie)
                refreshed = client.get('/api/session')
                self.assertIn('Max-Age=14400', refreshed.headers.get('set-cookie', ''))
                client.cookies.clear()  # Model a second browser profile.
                blocked = client.get('/api/session')
                self.assertEqual(blocked.status_code, 423)
                self.assertEqual(blocked.json()['detail'], OPERATOR_SESSION_CONFLICT)
                self.assertEqual(client.get('/health').json(), {'status': 'ok'})
                client.cookies.set('wilderness_north_operator', cookie)
                self.assertEqual(client.post('/api/session/reset').status_code, 200)

    def test_idle_operator_lease_expires_and_a_new_browser_can_claim(self):
        with TemporaryDirectory() as directory:
            app = create_app(
                deployment_mode="single_operator",
                operator_process_lock_path=Path(directory) / "backend.lock",
                operator_cookie_secure=False,
                operator_session_timeout_minutes=5,
            )
            with TestClient(app) as client:
                initial = client.get('/api/session')
                self.assertEqual(initial.status_code, 200)
                old_cookie = initial.cookies.get('wilderness_north_operator')
                client.cookies.clear()  # A new browser claims after the lease expires.
                app.state.operator_session_last_seen = time.monotonic() - 301
                claimed = client.get('/api/session')
                self.assertEqual(claimed.status_code, 200)
                new_cookie = claimed.cookies.get('wilderness_north_operator')
                self.assertTrue(new_cookie)
                self.assertNotEqual(new_cookie, old_cookie)
                client.cookies.set('wilderness_north_operator', old_cookie)
                self.assertEqual(client.get('/api/session').status_code, 423)

    def test_process_lock_rejects_a_second_backend_instance(self):
        with TemporaryDirectory() as directory:
            lock_path = Path(directory) / "shared.lock"
            first_app = create_app(deployment_mode="single_operator", operator_process_lock_path=lock_path)
            second_app = create_app(deployment_mode="single_operator", operator_process_lock_path=lock_path)
            with TestClient(first_app):
                with self.assertRaisesRegex(RuntimeError, "exactly one backend process/worker"):
                    with TestClient(second_app):
                        pass

    def test_cors_allows_only_configured_origin_with_credentials(self):
        app = create_app(cors_allowed_origins=["https://frontend.example.test"])
        with TestClient(app) as client:
            response = client.options('/api/session', headers={
                'Origin': 'https://frontend.example.test',
                'Access-Control-Request-Method': 'GET',
                'Access-Control-Request-Headers': 'content-type',
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], 'https://frontend.example.test')
        self.assertEqual(response.headers['access-control-allow-credentials'], 'true')

    def test_wildcard_cors_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            create_app(cors_allowed_origins=["*"])


if __name__ == '__main__':
    unittest.main()
