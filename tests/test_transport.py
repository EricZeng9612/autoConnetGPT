import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import autoConnetGPT as g
import transport_health as t
import test_watchdog


class LogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "logs.sqlite"
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, ts INTEGER, ts_nanos INTEGER, "
                       "target TEXT, feedback_log_body TEXT, process_uuid TEXT)")
        self.servers = [{"pid": 42, "started": 800}]

    def add(self, stamp=990, body="SSE Error: secret-token", target="codex_api::sse::responses", identity="pid:42:one"):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO logs(ts, ts_nanos, target, feedback_log_body, process_uuid) "
                       "VALUES (?,0,?,?,?)", (stamp, target, body, identity))

    def report(self, **kwargs):
        return t.summarize(self.path, self.servers, now=1000, **kwargs)

    def test_pid_start_window_and_future_boundaries(self):
        for stamp in (699, 799, 1001):
            self.add(stamp)
        self.add(identity="pid:43:other")
        self.add()
        report = self.report()
        self.assertEqual(report["http_sse_failures"], 1)
        self.assertNotIn("secret-token", json.dumps(report))
        self.assertEqual(self.report(since=995)["http_sse_failures"], 0)

    def test_model_only_failure_is_not_permission_to_switch(self):
        self.add(); self.add()
        report = self.report()
        self.assertTrue(t.stream_failure(report))
        self.assertFalse(t.cross_channel_failure(report))

    def test_remote_reconnected_still_flapping(self):
        self.add(); self.add()
        target = "codex_app_server_transport::transport::remote_control::websocket"
        for stamp in (980, 985):
            self.add(stamp, "reader disconnected", target)
        self.add(999, "next_status=Connected", target)
        report = self.report()
        self.assertEqual(report["remote_state"], "connected")
        self.assertTrue(t.cross_channel_failure(report))

    def test_stale_handshake_unknown(self):
        self.add(879, "next_status=Connected", "codex_app_server_transport::transport::remote_control::websocket")
        self.assertEqual(self.report()["remote_state"], "unknown")

    def test_connecting_supersedes_connected(self):
        target = "codex_app_server_transport::transport::remote_control::websocket"
        self.add(990, "next_status=Connected", target)
        self.add(991, "next_status=Connecting", target)
        self.assertEqual(self.report()["remote_state"], "unknown")

    def test_pid_reuse_and_schema_change_fail_closed(self):
        self.add(); self.add(identity="pid:42:two")
        report = self.report()
        self.assertFalse(report["available"])
        self.assertFalse(t.stream_failure(report))
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE logs")
        self.assertFalse(self.report()["available"])

    def test_truncated_logs_cannot_trigger_automatic_recovery(self):
        with sqlite3.connect(self.path) as db:
            db.executemany("INSERT INTO logs(ts,ts_nanos,target,feedback_log_body,process_uuid) VALUES(990,0,?,?,?)",
                           [("codex_api::sse::responses", "SSE Error: x", "pid:42:one")] * 1001)
        report = self.report()
        self.assertTrue(report["truncated"])
        self.assertFalse(t.stream_failure(report))
        self.assertLessEqual(len(report["events"]), 12)

    def test_no_servers_or_missing_db_is_unknown(self):
        self.assertFalse(t.summarize(self.path, [], now=1000)["available"])
        self.assertFalse(t.summarize(str(self.path) + ".missing", self.servers)["available"])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.api = g.Mihomo(dict(g.DEFAULTS))

    def test_global_nested_route_and_loop(self):
        values = {"/configs": {"mode": "global"}, "/proxies": {"proxies": {
            "GLOBAL": {"now": "active"}, "active": {"now": "leaf"}}}}
        with patch.object(self.api, "request", side_effect=lambda method, path: values[path]):
            self.assertTrue(self.api.route_group_active("GLOBAL"))
            self.assertTrue(self.api.route_group_active("active"))
            self.assertFalse(self.api.route_group_active("unused"))
            values["/proxies"]["proxies"]["active"]["now"] = "GLOBAL"
            self.assertFalse(self.api.route_group_active("unused"))

    def test_rule_requires_all_observed_openai_connections_on_group(self):
        rows = [{"metadata": {"host": "ws.chatgpt.com"}, "chains": ["leaf", "active"]}]
        with patch.object(self.api, "request", side_effect=lambda method, path:
                          {"mode": "rule"} if path == "/configs" else {"connections": rows}):
            self.assertTrue(self.api.route_group_active("active"))
            rows.append({"metadata": {"host": "api.openai.com"}, "chains": ["DIRECT"]})
            self.assertFalse(self.api.route_group_active("active"))
            rows.clear()
            self.assertFalse(self.api.route_group_active("active"))

    def test_explicit_missing_socket_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            self.api.cfg["controller_socket"] = str(Path(directory) / "missing")
            with self.assertRaises(g.GuardError):
                self.api.controller_socket()

    def test_auto_discovers_current_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "verge-mihomo.sock")
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(path)
                with patch("autoConnetGPT.tempfile.gettempdir", return_value=directory):
                    self.assertEqual(self.api.controller_socket(), path)

    def test_new_app_server_path_and_subcommand(self):
        prefix = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
        self.assertTrue(g.Mac.is_app_server(prefix + " -c x=true app-server"))
        self.assertFalse(g.Mac.is_app_server(prefix + " exec 'echo app-server'"))


class StreamWatchdogTests(unittest.TestCase):
    setUp = test_watchdog.WatchdogTests.setUp
    guard = test_watchdog.WatchdogTests.guard
    evidence = test_watchdog.WatchdogTests.evidence
    nodes = test_watchdog.WatchdogTests.nodes

    def test_model_errors_override_positive_remote(self):
        self.evidence("healthy")
        with patch("transport_health.summarize", return_value={"available": True, "http_sse_failures": 2}):
            self.assertEqual(self.guard().check()["result"], "STREAM_UNSTABLE")
        self.assertEqual(self.api.selections, [])
        self.assertNotIn("restart:ChatGPT", self.mac.calls)

    def test_wrong_route_stops_before_selection(self):
        self.nodes()
        self.mac.link_default = "failed"
        with patch.object(self.api, "route_group_active", return_value=False):
            self.assertEqual(self.guard().check()["result"], "VPN_ROUTE_UNCONFIRMED")
        self.assertEqual(self.api.selections, [])

    def test_foreign_proxy_does_not_start_clash(self):
        self.mac.core_result = False
        with patch.object(self.mac, "proxy_port_open", return_value=True):
            self.assertEqual(self.guard().check()["result"], "VPN_CLIENT_UNSUPPORTED")
        self.assertNotIn("start:Clash Verge", self.mac.calls)
        self.assertNotIn("restart:Clash Verge", self.mac.calls)
        self.assertEqual(self.api.selections, [])

    def test_failed_attempt_history_does_not_hide_stream_errors(self):
        g.atomic_json(self.path / "recovery.json", {"history": [{"kind": "node", "at": self.clock()}],
                                                  "deferred": {}, "transaction": None})
        with patch("transport_health.summarize", return_value={"available": True, "http_sse_failures": 2}) as collect:
            self.assertEqual(self.guard().check()["result"], "STREAM_UNSTABLE")
        self.assertIsNone(collect.call_args.args[3])

    def test_stream_failures_after_switch_roll_back(self):
        self.nodes()
        self.mac.links = ["failed", "failed"]
        with patch("transport_health.summarize", return_value={"available": True, "http_sse_failures": 2}):
            self.assertEqual(self.guard().check()["result"], "VPN_NEEDS_ATTENTION")
        self.assertEqual(self.api.current, "old")
        self.assertEqual(self.api.selections, ["SG fast", "old", "SG slow", "old"])

    def test_cross_channel_recovery_requires_opt_in(self):
        self.nodes()
        report = {"available": True, "http_sse_failures": 2, "remote_disconnects": 2}
        with patch("transport_health.summarize", return_value=report):
            self.assertEqual(self.guard().check()["result"], "STREAM_UNSTABLE")
        self.assertEqual(self.api.selections, [])

    def test_opt_in_does_not_change_nodes_in_read_only_mode(self):
        self.nodes()
        self.cfg["recover_stream_failures"] = True
        report = {"available": True, "http_sse_failures": 2, "remote_disconnects": 2}
        with patch("transport_health.summarize", return_value=report):
            self.assertEqual(self.guard(dry=True).check()["result"], "STREAM_UNSTABLE")
        self.assertEqual(self.api.selections, [])

    def test_opt_in_cross_channel_recovery_uses_existing_node_safety(self):
        self.nodes()
        self.cfg["recover_stream_failures"] = True
        self.evidence("healthy")
        reports = [{"available": True, "http_sse_failures": 2, "remote_disconnects": 2},
                   {"available": True}, {"available": True}]
        with patch("transport_health.summarize", side_effect=reports):
            self.assertEqual(self.guard().check()["result"], "HEALTHY")
        self.assertEqual(self.api.selections, ["SG fast"])
        self.assertNotIn("restart:ChatGPT", self.mac.calls)
