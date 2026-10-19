import subprocess
import unittest
from unittest.mock import patch

import autoConnetGPT as g
import test_watchdog


class ListenerTests(unittest.TestCase):
    def test_listener_matches_configured_address_not_another_interface(self):
        raw = "p11\nf3\nn10.0.0.1:7897\np22\nf4\nn127.0.0.1:7897\np33\nf5\nn127.0.0.1:1234\n"
        self.assertEqual(g.Mac.listener_pids(raw, "127.0.0.1", 7897), {22})

    def test_ipv6_wildcard_and_ambiguous_localhost(self):
        raw = "p11\nn[::1]:7897\np22\nn*:7897\n"
        self.assertEqual(g.Mac.listener_pids(raw, "::1", 7897), {11, 22})
        self.assertEqual(g.Mac.listener_pids(raw, "127.0.0.1", 7897), {22})

    def test_only_known_core_paths_are_recognized(self):
        classify = g.Mac.client_for_executable
        self.assertEqual(classify("/Applications/龙猫云_Lite.app/Contents/MacOS/lmclientCore"), "longma")
        self.assertEqual(classify("/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo"), "clash")
        self.assertEqual(classify("/tmp/lmclientCore"), "unknown")
        self.assertEqual(classify("/Applications/Clash Verge.app/Contents/MacOS/clash-verge"), "unknown")

    def test_unicode_executable_is_read_without_c_locale_escaping(self):
        results = [subprocess.CompletedProcess([], 0, "p22\nn127.0.0.1:7897\n"),
                   subprocess.CompletedProcess([], 0, "/Applications/龙猫云_Lite.app/Contents/MacOS/lmclientCore\n"),
                   subprocess.CompletedProcess([], 0, "p22\nn127.0.0.1:7897\n")]
        with patch.object(g.Mac, "run", side_effect=results) as run:
            self.assertEqual(g.Mac().proxy_client(g.DEFAULTS), "longma")
        self.assertTrue(run.call_args_list[1].kwargs["unicode_output"])

    def test_listener_owner_race_is_unknown(self):
        results = [subprocess.CompletedProcess([], 0, "p22\nn127.0.0.1:7897\n"),
                   subprocess.CompletedProcess([], 0, "/Applications/龙猫云_Lite.app/Contents/MacOS/lmclientCore\n"),
                   subprocess.CompletedProcess([], 0, "p23\nn127.0.0.1:7897\n")]
        with patch.object(g.Mac, "run", side_effect=results):
            self.assertEqual(g.Mac().proxy_client(g.DEFAULTS), "unknown")

    def test_lsof_permission_failure_is_not_missing_client(self):
        with patch.object(g.Mac, "run", return_value=subprocess.CompletedProcess([], 2, "")):
            self.assertEqual(g.Mac().proxy_client(g.DEFAULTS), "unknown")

    def test_ambiguous_listener_does_not_select_one(self):
        with patch.object(g.Mac, "run", return_value=subprocess.CompletedProcess(
                [], 0, "p22\nn127.0.0.1:7897\np23\nn127.0.0.1:7897\n")):
            self.assertEqual(g.Mac().proxy_client(g.DEFAULTS), "ambiguous")


class ModeTests(unittest.TestCase):
    guard = test_watchdog.WatchdogTests.guard
    evidence = test_watchdog.WatchdogTests.evidence
    nodes = test_watchdog.WatchdogTests.nodes

    def setUp(self):
        test_watchdog.WatchdogTests.setUp(self)
        self.cfg["proxy_client"] = "auto"
        self.client = "longma"
        self.owner = patch.object(self.mac, "proxy_client", side_effect=lambda cfg: self.client)
        self.owner.start()
        self.addCleanup(self.owner.stop)

    def assert_no_vpn_mutations(self):
        self.assertFalse(any("Clash" in c or "龙猫" in c for c in self.mac.calls))
        self.assertEqual(self.api.selections, [])

    def test_longma_success_reaches_application_and_remote(self):
        self.evidence("healthy")
        with patch.object(self.api, "request", create=True, side_effect=AssertionError("No Clash API")):
            report = self.guard().check()
        self.assertEqual(report["result"], "HEALTHY")
        self.assertEqual(report["checks"]["proxy_client"], "longma")
        self.assertEqual(report["checks"]["clash"], "not_selected")
        self.assert_no_vpn_mutations()

    def test_longma_bad_network_never_switches_to_clash(self):
        self.nodes()
        self.mac.link_default = "failed"
        report = self.guard().check()
        self.assertEqual(report["result"], "VPN_NEEDS_ATTENTION")
        self.assertIn("龙猫云", report["detail"])
        self.assert_no_vpn_mutations()

    def test_longma_stream_errors_ignore_clash_recovery_opt_in(self):
        self.nodes()
        self.cfg["recover_stream_failures"] = True
        with patch("transport_health.summarize", return_value={
                "available": True, "http_sse_failures": 2, "remote_disconnects": 2}):
            self.assertEqual(self.guard().check()["result"], "STREAM_UNSTABLE")
        self.assert_no_vpn_mutations()

    def test_explicit_mode_mismatch_preserves_existing_client(self):
        self.cfg["proxy_client"] = "clash"
        self.assertEqual(self.guard().check()["result"], "VPN_MODE_MISMATCH")
        self.client = "clash"
        self.cfg["proxy_client"] = "longma"
        self.assertEqual(self.guard().check()["result"], "VPN_MODE_MISMATCH")
        self.assert_no_vpn_mutations()

    def test_auto_no_listener_does_not_guess_which_app_to_start(self):
        self.client = "none"
        self.assertEqual(self.guard().check()["result"], "VPN_CLIENT_UNAVAILABLE")
        self.assert_no_vpn_mutations()

    def test_auto_clash_preserves_node_selection(self):
        self.client = "clash"
        self.nodes()
        self.mac.links = ["failed", "failed"]
        self.evidence("healthy")
        self.assertEqual(self.guard().check()["result"], "HEALTHY")
        self.assertEqual(self.api.selections, ["SG fast"])

    def test_switch_to_longma_during_node_ranking_aborts_before_put(self):
        self.client = "clash"
        self.nodes()
        self.mac.links = ["failed", "failed"]
        original = self.api.delay
        def change(*args):
            value = original(*args)
            self.client = "longma"
            return value
        with patch.object(self.api, "delay", side_effect=change):
            self.assertEqual(self.guard().check()["result"], "VPN_MODE_MISMATCH")
        self.assert_no_vpn_mutations()

    def test_pending_clash_rollback_does_not_touch_longma(self):
        tx = {"group": "Proxy", "original": "old", "candidate": "SG fast"}
        g.atomic_json(self.path / "recovery.json", {"history": [], "deferred": {}, "transaction": tx})
        with patch.object(self.api, "group", side_effect=AssertionError("No Clash API")):
            self.assertEqual(self.guard().check()["result"], "VPN_ROLLBACK_REQUIRED")
        self.assertEqual(g.read_json(self.path / "recovery.json")["transaction"], tx)
        self.assert_no_vpn_mutations()

    def test_mode_change_during_probes_cancels_application_recovery(self):
        self.mac.proc = None
        def change(cfg):
            self.client = "clash"
            return "ok"
        with patch.object(self.mac, "link", side_effect=change):
            self.assertEqual(self.guard().check()["result"], "VPN_MODE_MISMATCH")
        self.assertNotIn("start:ChatGPT", self.mac.calls)

    def test_invalid_mode_rejected(self):
        for value in ("both", "Longma", "", 42):
            g.atomic_json(self.path / "config.json", {"proxy_client": value})
            with self.assertRaises(g.GuardError):
                g.config_load(self.path / "config.json")
