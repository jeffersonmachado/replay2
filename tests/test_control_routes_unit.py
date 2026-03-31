import io
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_ROOT = ROOT / "gateway"
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))


def _load_module(module_name: str, relative_path: str):
    module_path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


admin_routes = _load_module("admin_routes_module", "gateway/control/routes/admin_routes.py")
catalog_routes = _load_module("catalog_routes_module", "gateway/control/routes/catalog_routes.py")
gateway_routes = _load_module("gateway_routes_module", "gateway/control/routes/gateway_routes.py")
operational_routes = _load_module("operational_routes_module", "gateway/control/routes/operational_routes.py")


class _FakeConnection:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return self


class _FakeHandler:
    def __init__(self, *, user=None, connection=None):
        self.user = user if user is not None else {"id": 7, "username": "admin", "role": "admin"}
        self.connection = connection or _FakeConnection()
        self.server = types.SimpleNamespace(cookie_secret=b"secret")
        self.status_code = None
        self.headers = []
        self.ended = False
        self.cookies = []
        self.cleared = []
        self.auth_called = False
        self.db_released = 0
        self.required_roles = []
        self.wfile = io.BytesIO()

    def _require(self, roles=None):
        self.required_roles.append(roles)
        return self.user

    def _db(self):
        return self.connection

    def _db_release(self, con):
        self.db_released += 1

    def _set_cookie(self, key, value):
        self.cookies.append((key, value))

    def _clear_cookie(self, key):
        self.cleared.append(key)

    def _auth(self):
        self.auth_called = True
        return self.user

    def send_response(self, code):
        self.status_code = code

    def send_header(self, key, value):
        self.headers.append((key, value))

    def end_headers(self):
        self.ended = True

    def json_payload(self):
        raw = self.wfile.getvalue().decode("utf-8")
        return json.loads(raw) if raw else None


class ControlRoutesUnitTests(unittest.TestCase):
    def test_admin_get_gateway_status_returns_json(self):
        handler = _FakeHandler()
        parsed = types.SimpleNamespace(path="/api/gateway/status")

        handled = admin_routes.handle_admin_get_route(
            handler,
            parsed,
            gateway_service_status=lambda: {"running": True, "platform": "linux"},
            query_all_fn=lambda *args, **kwargs: [],
        )

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.json_payload()["running"], True)

    def test_admin_post_login_sets_session_cookie(self):
        handler = _FakeHandler(connection=_FakeConnection())
        parsed = types.SimpleNamespace(path="/api/login")
        body = {"username": "admin", "password": "secret"}
        auth_module = types.SimpleNamespace(
            verify_password=lambda password, password_hash: password == "secret" and password_hash == "hash",
            new_session_token=lambda: "tok",
            sha256_hex=lambda value: "digest",
            sign_cookie=lambda secret, username, token, exp: f"{username}:{token}:{exp}",
            pbkdf2_hash_password=lambda password: f"hashed:{password}",
        )

        handled = admin_routes.handle_admin_post_route(
            handler,
            parsed,
            body,
            auth_module=auth_module,
            query_one_fn=lambda con, sql, params: {"id": 1, "username": "admin", "role": "admin", "password_hash": "hash"},
            now_ms_fn=lambda: 123456,
            gateway_toggle_fn=lambda enabled: {"running": enabled},
        )

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.cookies[0][0], "dakota_session")
        self.assertEqual(handler.db_released, 1)
        self.assertTrue(handler.connection.executed)

    def test_catalog_get_targets_applies_gateway_required_filter(self):
        handler = _FakeHandler()
        parsed = types.SimpleNamespace(path="/api/targets", query="gateway_required=true")

        with patch.object(
            catalog_routes,
            "list_target_environments",
            return_value=[
                {"id": 1, "gateway_required": True},
                {"id": 2, "gateway_required": False},
            ],
        ):
            handled = catalog_routes.handle_catalog_get_route(handler, parsed)

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.json_payload(), {"targets": [{"id": 1, "gateway_required": True}]})

    def test_catalog_post_target_policy_updates_and_returns_target(self):
        handler = _FakeHandler()
        parsed = types.SimpleNamespace(path="/api/targets/9/policy")
        body = {"gateway_required": True, "direct_ssh_policy": "gateway_only"}

        with patch.object(catalog_routes, "query_one", return_value={"id": 9}), patch.object(
            catalog_routes,
            "list_target_environments",
            return_value=[{"id": 9, "gateway_required": True, "direct_ssh_policy": "gateway_only"}],
        ), patch.object(
            catalog_routes,
            "normalize_direct_ssh_policy_payload",
            return_value={
                "gateway_required": True,
                "direct_ssh_policy": "gateway_only",
                "capture_start_mode": "login_required",
                "capture_compliance_mode": "strict",
                "allow_admin_direct_access": False,
            },
        ), patch.object(catalog_routes, "now_ms", return_value=1010):
            handled = catalog_routes.handle_catalog_post_route(handler, parsed, body)

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.json_payload()["target"]["id"], 9)
        self.assertEqual(handler.db_released, 1)

    def test_operational_get_forwards_filters_to_service(self):
        handler = _FakeHandler(user={"id": 44, "username": "ops", "role": "operator"})
        parsed = types.SimpleNamespace(
            path="/api/operational-scenarios",
            query="environment=hml&favorite_only=true&sort_by=most-used&used_from_ms=10&used_to_ms=20",
        )

        with patch.object(operational_routes, "list_operational_scenarios", return_value=[{"id": 5, "name": "cat"}]) as list_mock:
            handled = operational_routes.handle_operational_get_route(handler, parsed, catalog_routes.parse_qs)

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.json_payload()["scenarios"][0]["id"], 5)
        self.assertEqual(list_mock.call_args.kwargs["environment"], "hml")
        self.assertEqual(list_mock.call_args.kwargs["favorite_only"], True)
        self.assertEqual(list_mock.call_args.kwargs["sort_by"], "most-used")

    def test_operational_delete_returns_deleted_payload(self):
        handler = _FakeHandler(user={"id": 9, "username": "ops", "role": "operator"})
        parsed = types.SimpleNamespace(path="/api/operational-scenarios/11")

        with patch.object(operational_routes, "delete_operational_scenario", return_value=True), patch.object(
            operational_routes, "list_operational_scenarios", return_value=[{"id": 11, "name": "scenario"}]
        ):
            handled = operational_routes.handle_operational_delete_route(handler, parsed)

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.json_payload()["ok"], True)

    def test_gateway_monitor_route_reads_limit_and_returns_payload(self):
        handler = _FakeHandler()
        parsed = types.SimpleNamespace(path="/api/gateway/monitor", query="log_dir=/tmp/audit&limit=25")

        handled = gateway_routes.handle_gateway_get_route(
            handler,
            parsed,
            parse_qs_fn=catalog_routes.parse_qs,
            query_one_fn=lambda *args, **kwargs: None,
            read_gateway_monitor_fn=lambda log_dir, limit: {"log_dir": log_dir, "summary": {"window_events": limit}},
            read_gateway_sessions_fn=lambda *args, **kwargs: {},
            read_gateway_session_detail_fn=lambda *args, **kwargs: {},
        )

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.json_payload()["summary"]["window_events"], 25)

    def test_gateway_sessions_route_resolves_target_policy(self):
        handler = _FakeHandler()
        parsed = types.SimpleNamespace(path="/api/gateway/sessions", query="log_dir=/tmp/audit&target_env_id=9&actor=ops")

        with patch.object(gateway_routes, "_resolve_target_policy", return_value={"id": 9, "gateway_required": True}) as policy_mock:
            handled = gateway_routes.handle_gateway_get_route(
                handler,
                parsed,
                parse_qs_fn=catalog_routes.parse_qs,
                query_one_fn=lambda *args, **kwargs: None,
                read_gateway_monitor_fn=lambda *args, **kwargs: {},
                read_gateway_sessions_fn=lambda log_dir, **kwargs: {"log_dir": log_dir, "sessions": [{"session_id": "s-1"}], "target_policy": kwargs.get("target_policy")},
                read_gateway_session_detail_fn=lambda *args, **kwargs: {},
            )

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 200)
        self.assertEqual(handler.json_payload()["sessions"][0]["session_id"], "s-1")
        self.assertEqual(policy_mock.call_args.args[1], 9)

    def test_gateway_session_compliance_returns_404_when_session_missing(self):
        handler = _FakeHandler()
        parsed = types.SimpleNamespace(path="/api/gateway/sessions/s-404/compliance", query="log_dir=/tmp/audit")

        handled = gateway_routes.handle_gateway_get_route(
            handler,
            parsed,
            parse_qs_fn=catalog_routes.parse_qs,
            query_one_fn=lambda *args, **kwargs: None,
            read_gateway_monitor_fn=lambda *args, **kwargs: {},
            read_gateway_sessions_fn=lambda *args, **kwargs: {"sessions": []},
            read_gateway_session_detail_fn=lambda *args, **kwargs: {},
        )

        self.assertTrue(handled)
        self.assertEqual(handler.status_code, 404)


if __name__ == "__main__":
    unittest.main()
