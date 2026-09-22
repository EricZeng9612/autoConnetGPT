import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import autoConnetGPT as g
import manage


class Clock:
    def __init__(self):
        self.now = 2_000_000.0
        self.hook = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.hook:
            self.hook(seconds)


class FakeMac:
    def __init__(self, clock):
        self.clock = clock
        self.proc = {"pid": 42, "started": clock() - 1000}
        self.server = True
        self.basic_result = "ok"
        self.core_result = True
        self.links = []
        self.link_default = "ok"
        self.calls = []
        self.restart_result = True
        self.restarted = None

    def basic(self, cfg):
        self.calls.append("basic")
        return self.basic_result

    def core(self, cfg):
        self.calls.append("core")
        return self.core_result

    def link(self, cfg):
        self.calls.append("link")
        return self.links.pop(0) if self.links else self.link_default

    def app(self):
        self.calls.append("app")
        return self.proc, self.server

    def start(self, app):
        self.calls.append("start:" + app)
        if app == "ChatGPT":
            self.proc = {"pid": 43, "started": self.clock()}
            self.server = True
        return True

    def restart(self, app):
        self.calls.append("restart:" + app)
        if self.restart_result and app == "ChatGPT":
            self.proc = {"pid": 43, "started": self.clock()}
            self.server = True
            if self.restarted:
                self.restarted()
        return self.restart_result

    def sleep_protected(self):
        return True

    def notify(self, message):
        self.calls.append("notify")


class FakeAPI:
    def __init__(self):
        self.current = "old"
        self.nodes = {"old": {"type": "Shadowsocks"}, "SG slow": {"type": "Shadowsocks"},
                      "SG fast": {"type": "Shadowsocks"}, "US": {"type": "Shadowsocks"},
                      "DIRECT": {"type": "Direct"}, "SG group": {"type": "Selector", "all": ["SG fast"]}}
        self.selections = []
        self.delays = {"SG slow": [100, 10, 120], "SG fast": [30, 35, 32]}
        self.indices = {}
        self.delay_calls = []
        self.fail_select = False
        self.fail_rollback = False

    def group(self, name):
        return {"type": "Selector", "now": self.current, "all": list(self.nodes)}

    def proxies(self):
        return self.nodes

    def select(self, group, node):
        self.selections.append(node)
        if node == "old" and self.fail_rollback:
            raise g.GuardError("rollback failed")
        self.current = node
        if node != "old" and self.fail_select:
            raise g.GuardError("acknowledgement lost after change")

    def delay(self, node, url):
        self.delay_calls.append((node, url))
        if node not in self.delays:
            raise g.GuardError("timeout")
        if "gstatic" in url:
            return 10
        idx = self.indices.get(node, 0)
        self.indices[node] = idx + 1
        return self.delays[node][idx % len(self.delays[node])]


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.clock = Clock()
        self.mac = FakeMac(self.clock)
        self.api = FakeAPI()
        self.cfg = dict(g.DEFAULTS, notifications=False,
                        remote_evidence_file=str(self.path / "remote.json"),
                        task_evidence_file=str(self.path / "tasks.json"))

    def guard(self, dry=False):
        return g.Watchdog(self.cfg, self.path, self.mac, self.api, self.clock, self.clock.sleep, dry)

    def evidence(self, status, task=False, **updates):
        proc = self.mac.proc
        value = {"pid": proc["pid"], "process_started_at": proc["started"],
                 "observed_at": self.clock(), "status": status}
        value.update(updates)
        g.atomic_json(self.path / ("tasks.json" if task else "remote.json"), value)

    def nodes(self):
        self.cfg.update(selector_group="Proxy", singapore_nodes=["SG slow", "SG fast"])

    def test_healthy_requires_remote_evidence(self):
        self.evidence("healthy")
        result = self.guard().check()
        self.assertEqual(result["result"], "HEALTHY")
        self.assertLess(self.mac.calls.index("basic"), self.mac.calls.index("app"))
        self.assertLess(self.mac.calls.index("link"), self.mac.calls.index("app"))

    def test_missing_remote_is_unknown_not_restart(self):
        result = self.guard().check()
        self.assertEqual(result["result"], "REMOTE_UNKNOWN")
        self.assertEqual(self.clock(), 2_000_120)
        self.assertNotIn("restart:ChatGPT", self.mac.calls)

    def test_network_failure_gates_everything(self):
        self.mac.basic_result = "failed"
        self.assertEqual(self.guard().check()["result"], "NETWORK_UNAVAILABLE")
        self.assertEqual(self.mac.calls, ["basic"])
        self.assertEqual(self.api.selections, [])

    def test_unknown_proxy_does_not_switch_or_restart(self):
        self.nodes()
        self.mac.link_default = "unknown"
        self.assertEqual(self.guard().check()["result"], "VPN_PATH_UNKNOWN")
        self.assertNotIn("app", self.mac.calls)
        self.assertEqual(self.api.selections, [])

    def test_transient_failure_rechecks_without_switch(self):
        self.mac.links = ["failed", "ok"]
        self.evidence("healthy")
        self.assertEqual(self.guard().check()["result"], "HEALTHY")
        self.assertEqual(self.api.selections, [])

    def test_switching_requires_explicit_group(self):
        self.mac.link_default = "failed"
        self.assertEqual(self.guard().check()["result"], "VPN_CONFIG_REQUIRED")
        self.assertEqual(self.api.selections, [])

    def test_lowest_median_sg_selected_not_lowest_single_sample(self):
        self.nodes()
        self.mac.links = ["failed", "failed", "ok", "ok"]
        self.evidence("healthy")
        result = self.guard().check()
        self.assertEqual(result["result"], "HEALTHY")
        self.assertEqual(self.api.selections, ["SG fast"])
        self.assertEqual(len(self.api.delay_calls), 12)
        self.assertIsNone(g.read_json(self.path / "recovery.json")["transaction"])

    def test_failed_fast_node_rolls_back_then_slow_succeeds(self):
        self.nodes()
        self.mac.links = ["failed", "failed", "failed", "ok", "ok"]
        self.evidence("healthy")
        self.assertEqual(self.guard().check()["result"], "HEALTHY")
        self.assertEqual(self.api.selections, ["SG fast", "old", "SG slow"])

    def test_all_candidates_fail_restores_original(self):
        self.nodes()
        self.mac.link_default = "failed"
        self.assertEqual(self.guard().check()["result"], "VPN_NEEDS_ATTENTION")
        self.assertEqual(self.api.current, "old")
        self.assertEqual(self.api.selections, ["SG fast", "old", "SG slow", "old"])

    def test_all_delay_tests_fail_no_selection(self):
        self.nodes()
        self.api.delays = {}
        self.mac.link_default = "failed"
        self.assertEqual(self.guard().check()["result"], "VPN_NO_SG_NODE")
        self.assertEqual(self.api.selections, [])

    def test_lost_selection_ack_rolls_back(self):
        self.nodes()
        self.api.fail_select = True
        self.mac.link_default = "failed"
        self.guard().check()
        self.assertEqual(self.api.current, "old")
        self.assertEqual(self.api.selections, ["SG fast", "old", "SG slow", "old"])

    def test_failed_rollback_persists_transaction_and_stops(self):
        self.nodes()
        self.api.fail_rollback = True
        self.mac.link_default = "failed"
        self.assertEqual(self.guard().check()["result"], "VPN_ROLLBACK_REQUIRED")
        state = g.read_json(self.path / "recovery.json")
        self.assertEqual(state["transaction"]["original"], "old")
        self.assertEqual(len(self.api.selections), 2)

    def test_crash_transaction_recovered_despite_cooldown(self):
        g.atomic_json(self.path / "recovery.json", {
            "history": [{"kind": "node", "at": self.clock()}], "deferred": {},
            "transaction": {"group": "Proxy", "original": "old", "candidate": "SG fast"}})
        self.api.current = "SG fast"
        self.evidence("healthy")
        self.assertEqual(self.guard().check()["result"], "HEALTHY")
        self.assertEqual(self.api.selections, ["old"])

    def test_external_selection_not_overwritten_during_recovery(self):
        g.atomic_json(self.path / "recovery.json", {
            "history": [], "deferred": {},
            "transaction": {"group": "Proxy", "original": "old", "candidate": "SG fast"}})
        self.api.current = "US"
        self.assertEqual(self.guard().check()["result"], "VPN_ROLLBACK_REQUIRED")
        self.assertEqual(self.api.selections, [])
        self.assertEqual(self.mac.calls, [])

    def test_corrupt_state_fails_closed(self):
        g.atomic_json(self.path / "recovery.json", {"history": "bad", "deferred": {}})
        self.assertEqual(self.guard().check()["result"], "CHECK_ERROR")
        self.assertEqual(self.mac.calls, [])
        self.assertEqual(g.read_json(self.path / "recovery.json")["history"], "bad")

    def test_same_old_failure_not_two_independent_failures(self):
        self.evidence("failed")
        self.evidence("idle", task=True)
        self.assertEqual(self.guard().check()["result"], "REMOTE_UNKNOWN")
        self.assertNotIn("restart:ChatGPT", self.mac.calls)

    def test_two_failures_idle_restart_once_and_verify_fresh_instance(self):
        self.evidence("failed")
        def refresh(_):
            self.evidence("healthy" if self.mac.proc["pid"] == 43 else "failed")
            self.evidence("idle", task=True)
        self.clock.hook = refresh
        result = self.guard().check()
        self.assertEqual(result["result"], "HEALTHY")
        self.assertEqual(self.mac.calls.count("restart:ChatGPT"), 1)
        self.assertEqual(len(g.read_json(self.path / "recovery.json")["history"]), 1)

    def test_unknown_tasks_defer_twice_then_manual(self):
        self.mac.server = False
        for expected in ("RESTART_DEFERRED", "RESTART_DEFERRED", "TASKS_NEED_ATTENTION"):
            self.assertEqual(self.guard().check()["result"], expected)
            self.clock.sleep(600)
        self.assertNotIn("restart:ChatGPT", self.mac.calls)
        self.assertEqual(g.read_json(self.path / "recovery.json")["deferred"]["count"], 2)

    def test_due_timer_consumed_even_when_network_fails(self):
        g.atomic_json(self.path / "recovery.json", {"history": [], "transaction": None,
                      "deferred": {"identity": "42:1999000", "count": 1, "due": self.clock() - 1}})
        self.mac.basic_result = "failed"
        self.guard().check()
        self.assertEqual(g.read_json(self.path / "recovery.json")["deferred"]["due"], 0)

    def test_auth_requires_manual_not_restart(self):
        self.evidence("auth_required")
        self.assertEqual(self.guard().check()["result"], "REMOTE_MANUAL")
        self.assertNotIn("restart:ChatGPT", self.mac.calls)

    def test_missing_server_still_respects_auth_protection(self):
        self.mac.server = False
        self.evidence("pairing_required")
        self.clock.hook = lambda _: self.evidence("idle", task=True)
        self.assertEqual(self.guard().check()["result"], "REMOTE_MANUAL")
        self.assertNotIn("restart:ChatGPT", self.mac.calls)

    def test_shared_evidence_file_rejected(self):
        self.evidence("healthy")
        (self.path / "remote.json").chmod(0o644)
        self.assertIsNone(g.fresh_evidence(self.cfg["remote_evidence_file"], self.mac.proc, self.clock(), 120))

    def test_termination_during_switch_rolls_back(self):
        self.nodes()
        self.mac.links = ["failed", "failed"]
        def terminate_during_settle(seconds):
            if seconds == 5:
                raise SystemExit(0)
        self.clock.hook = terminate_during_settle
        with self.assertRaises(SystemExit):
            self.guard().check()
        self.assertEqual(self.api.current, "old")
        self.assertIsNone(g.read_json(self.path / "recovery.json")["transaction"])

    def test_readonly_leaves_pending_transaction_intact(self):
        state = {"history": [], "deferred": {},
                 "transaction": {"group": "Proxy", "original": "old", "candidate": "SG fast"}}
        g.atomic_json(self.path / "recovery.json", state)
        self.api.current = "SG fast"
        self.assertEqual(self.guard(dry=True).check()["result"], "VPN_ROLLBACK_REQUIRED")
        self.assertEqual(g.read_json(self.path / "recovery.json"), state)
        self.assertEqual(self.api.selections, [])

    def test_daemon_reads_manual_check_deferred_timer_from_disk(self):
        self.assertEqual(g.deferred_deadline(self.path), 0)
        g.atomic_json(self.path / "recovery.json", {"deferred": {"due": self.clock() + 600}})
        self.assertEqual(g.deferred_deadline(self.path), self.clock() + 600)
        g.atomic_json(self.path / "recovery.json", {"deferred": "corrupted"})
        self.assertEqual(g.deferred_deadline(self.path), 0)

    def test_old_instance_evidence_rejected_after_start(self):
        self.evidence("healthy")
        self.mac.proc = None
        result = self.guard().check()
        self.assertEqual(result["result"], "REMOTE_UNKNOWN")
        self.assertEqual(self.mac.calls.count("start:ChatGPT"), 1)
        self.assertEqual(result["checks"]["app"], "ok")

    def test_app_instance_changes_no_restart(self):
        self.mac.server = False
        self.clock.hook = lambda _: setattr(self.mac, "proc", {"pid": 99, "started": self.clock()})
        self.assertEqual(self.guard().check()["result"], "REMOTE_UNKNOWN")
        self.assertNotIn("restart:ChatGPT", self.mac.calls)

    def test_cooldown_persists_across_instances_and_future_clock(self):
        one = self.guard()
        one.deadline = self.clock() + 900
        one.reserve("gpt")
        for offset in (0, -100):
            self.clock.now += offset
            two = self.guard()
            two.load()
            two.deadline = self.clock() + 900
            with self.assertRaises(g.Halt) as caught:
                two.reserve("gpt")
            self.assertEqual(caught.exception.result, "COOLDOWN")

    def test_twice_per_day_limit(self):
        guard = self.guard()
        for _ in range(2):
            guard.deadline = self.clock() + 900
            guard.reserve("gpt")
            self.clock.now += 21601
        guard.deadline = self.clock() + 900
        with self.assertRaises(g.Halt) as caught:
            guard.reserve("gpt")
        self.assertEqual(caught.exception.result, "RESTART_LIMIT")

    def test_lock_skips_duplicate(self):
        with g.file_lock(self.path / "check-v2.lock") as acquired:
            self.assertTrue(acquired)
            self.assertEqual(self.guard().check()["result"], "CHECK_ALREADY_RUNNING")
        self.evidence("healthy")
        self.assertEqual(self.guard().check()["result"], "HEALTHY")

    def test_diagnose_does_not_write_or_repair(self):
        self.mac.core_result = False
        self.assertEqual(self.guard(dry=True).check()["result"], "ACTION_REQUIRED")
        self.assertFalse((self.path / "status.json").exists())
        self.assertFalse((self.path / "recovery.json").exists())
        self.assertNotIn("start:Clash Verge", self.mac.calls)

    def test_expired_wrong_pid_future_and_nan_evidence_unknown(self):
        for update in ({"observed_at": self.clock() - 121}, {"pid": 1},
                       {"process_started_at": 0}, {"observed_at": self.clock() + 1}, {"pid": True}):
            self.evidence("healthy", **update)
            self.assertIsNone(g.fresh_evidence(self.cfg["remote_evidence_file"], self.mac.proc, self.clock(), 120))

    def test_sg_filter_excludes_nested_current_direct_and_unlisted(self):
        self.cfg["allow_singapore_name_match"] = True
        group = self.api.group("Proxy")
        group["now"] = "SG fast"
        self.assertEqual(g.singapore_candidates(self.api.proxies(), group, self.cfg), ["SG slow"])

    def test_candidate_limit_refuses_truncated_ranking(self):
        self.nodes()
        self.cfg["max_candidates"] = 1
        with self.assertRaises(g.Halt):
            g.singapore_candidates(self.api.proxies(), self.api.group("Proxy"), self.cfg)

    def test_max_three_switches(self):
        self.nodes()
        for name in ("SG 3", "SG 4"):
            self.api.nodes[name] = {"type": "Shadowsocks"}
            self.api.delays[name] = [200, 200, 200]
        self.cfg["singapore_nodes"] = ["SG slow", "SG fast", "SG 3", "SG 4"]
        self.mac.link_default = "failed"
        self.guard().check()
        self.assertEqual(len([x for x in self.api.selections if x != "old"]), 3)
        self.assertEqual(self.api.current, "old")

    def test_budget_stops_before_new_action(self):
        guard = self.guard()
        guard.deadline = self.clock() - 1
        with self.assertRaises(g.Halt) as caught:
            guard.reserve("node")
        self.assertEqual(caught.exception.result, "CHECK_TIMEOUT")
        self.assertFalse((self.path / "recovery.json").exists())


class AdapterTests(unittest.TestCase):
    def test_http_codes_not_all_healthy(self):
        mac = g.Mac()
        for code, expected in ((200, "ok"), (302, "ok"), (403, "unknown"), (429, "unknown"), (503, "unknown")):
            with patch.object(mac, "run", return_value=subprocess.CompletedProcess([], 0, str(code), "")):
                self.assertEqual(mac.http("https://example.com"), expected)
        with patch.object(mac, "run", return_value=subprocess.CompletedProcess([], 28, "000", "")):
            self.assertEqual(mac.http("https://example.com"), "failed")

    def test_curl_disables_implicit_proxy_and_config(self):
        mac = g.Mac()
        with patch.object(mac, "run", return_value=subprocess.CompletedProcess([], 0, "200", "")) as run:
            mac.http("https://example.com")
            argv = run.call_args[0][0]
            self.assertEqual(argv[1], "--disable")
            self.assertEqual(argv[argv.index("--proxy") + 1], "")
            self.assertEqual(argv[argv.index("--noproxy") + 1], "*")

    def test_only_actual_child_server_counts(self):
        raw = ("42 1 Mon Sep 21 12:00:00 2026 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT\n"
               "43 99 Mon Sep 21 12:00:01 2026 /Applications/ChatGPT.app/Contents/Resources/codex app-server\n")
        mac = g.Mac()
        rows = g.Mac.parse_processes(raw)
        with patch.object(mac, "processes", return_value=rows):
            self.assertFalse(mac.app()[1])
            rows[1]["ppid"] = 42
            self.assertTrue(mac.app()[1])

    def test_controller_delay_encodes_node_name(self):
        api = g.Mihomo(dict(g.DEFAULTS))
        with patch.object(api, "request", return_value={"delay": 25}) as request:
            self.assertEqual(api.delay("SG/a b", "https://example.com/"), 25)
            self.assertIn("SG%2Fa%20b/delay?", request.call_args[0][1])
        for delay in (0, -1, True, float("nan"), 6000):
            with patch.object(api, "request", return_value={"delay": delay}):
                with self.assertRaises(g.GuardError):
                    api.delay("SG", "https://example.com/")

    def test_app_server_accepts_current_build_global_flags(self):
        executable = "/Applications/ChatGPT.app/Contents/Resources/codex"
        self.assertTrue(g.Mac.is_app_server(executable + " -c features.code_mode_host=true app-server"))
        self.assertTrue(g.Mac.is_app_server(executable + " app-server --telemetry"))
        self.assertFalse(g.Mac.is_app_server(executable + " exec echo app-server-wrong"))
        self.assertFalse(g.Mac.is_app_server(executable + " exec echo app-server"))
        self.assertFalse(g.Mac.is_app_server(executable + " -c app-server exec"))
        self.assertFalse(g.Mac.is_app_server("python -c '" + executable + " app-server'"))

    def test_configuration_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            self.assertEqual(g.config_load(path)["interval_seconds"], 3600)
            for cfg in ({"controller_url": "http://1.2.3.4:9090"}, {"interval_seconds": True},
                        {"max_switches": 4}, {"controller_secret_file": "relative"},
                        {"basic_urls": []}, {"chatgpt_url": "http://example.com"}, {"unknown": 1}):
                g.atomic_json(path, cfg)
                with self.assertRaises(g.GuardError):
                    g.config_load(path)

    def test_installer_plist_pins_python_and_launches_once(self):
        directory = Path('/Users/test/A&B Project/autoConnetGPT')
        result = plistlib.loads(plistlib.dumps(manage.launchagent(directory, "/usr/bin/python3")))
        self.assertEqual(result["ProgramArguments"], ["/usr/bin/python3", str(directory / "autoConnetGPT.py"), "--daemon"])
        self.assertTrue(result["RunAtLoad"])
        self.assertTrue(result["KeepAlive"])
        self.assertEqual(result["StandardErrorPath"], "/dev/null")


if __name__ == "__main__":
    unittest.main()
