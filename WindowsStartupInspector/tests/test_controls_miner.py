import base64
import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Entry
from controls import demo_change, needs_typed_confirmation, perform_action, supported_actions
from miner import MinerReport, analyze, command_signals, demo_audit, scan_for_miners, security_request


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.service = Entry("s", "TestService", kind="service", state="Работает",
                             start_mode="Автоматически (отложенно)", target={"type": "service", "name": "TestService"})

    def test_disable_service_does_not_stop_and_restore_preserves_delayed_mode(self):
        backups = {}
        disabled = demo_change(self.service, "disable", backups)
        self.assertEqual(disabled.state, "Работает")
        self.assertEqual(disabled.start_mode, "Отключена")
        self.assertEqual(demo_change(disabled, "enable", backups).start_mode, self.service.start_mode)

    def test_stop_start_do_not_change_start_mode(self):
        stopped = demo_change(self.service, "stop", {})
        self.assertEqual(stopped.state, "Остановлена")
        self.assertEqual(stopped.start_mode, self.service.start_mode)
        self.assertEqual(demo_change(stopped, "start", {}).state, "Работает")

    def test_registry_toggle_is_reversible_in_demo(self):
        entry = Entry("r", "Run", state="Включено", target={"type": "registry"})
        self.assertEqual(demo_change(entry, "disable", {}).state, "Отключено")
        self.assertEqual(demo_change(entry, "enable", {}).state, "Включено")

    def test_readonly_windows_entries_have_no_control_actions(self):
        entry = Entry("bootexecute", "BootExecute")
        self.assertEqual(supported_actions(entry), set())
        with self.assertRaises(ValueError):
            perform_action(entry, "disable", demo=True)

    def test_task_cannot_be_used_as_service_stop_request(self):
        with self.assertRaises(ValueError):
            perform_action(Entry("t", "Task", target={"type": "task"}), "stop", demo=True)

    def test_critical_services_and_drivers_require_explicit_confirmation(self):
        self.assertTrue(needs_typed_confirmation(Entry("s", "RPC", target={"type": "service", "name": "RpcSs"}), "disable"))
        self.assertTrue(needs_typed_confirmation(Entry("d", "Driver", kind="driver"), "stop"))
        self.assertFalse(needs_typed_confirmation(self.service, "disable"))
        self.assertFalse(needs_typed_confirmation(Entry("d", "Driver", kind="driver"), "enable"))

    @patch("backend.sys.platform", "win32")
    def test_request_is_literal_json_not_powershell_code_and_is_removed(self):
        value = 'a"; Write-Output hacked; # Русский'
        entry = Entry("r", "Registry", target={"type": "registry", "value": value})
        seen = []
        def runner(args, **kwargs):
            path = Path(args[args.index("-RequestFile") + 1])
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["target"]["value"], value)
            self.assertNotIn(value, args)
            seen.append(path)
            return subprocess.CompletedProcess(args, 0, '{"id":"r","success":true,"message":"OK"}', "")
        self.assertTrue(perform_action(entry, "disable", runner=runner)["success"])
        self.assertFalse(seen[0].exists())

    @patch("backend.sys.platform", "win32")
    def test_wrong_acknowledgement_is_not_success(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, '{"id":"other","success":true}', "")
        with self.assertRaises(RuntimeError):
            perform_action(self.service, "disable", runner=runner)

    def test_demo_never_executes_windows_controls(self):
        with patch("controls.run_windows_script", side_effect=AssertionError("must not run")):
            self.assertTrue(perform_action(self.service, "disable", demo=True)["demo"])


def quiet_audit():
    return {"warnings": [], "processes": [{"pid": 10, "name": "browser.exe", "path": r"C:\Program Files\Browser\browser.exe",
                                           "command": "browser.exe", "cpu_percent": 2}],
            "gpu": [], "connections": [], "dns": [], "persistence": [],
            "defender": {"available": True, "status": {"antivirus_enabled": True, "realtime_enabled": True, "signature_age": 0},
                         "threats": [], "exclusions_paths": [], "exclusions_processes": []}}


class MinerTests(unittest.TestCase):
    def test_known_name_and_stratum_are_evidence_not_confirmed_infection(self):
        report = analyze(demo_audit(), [])
        self.assertTrue(any(f.category == "Процесс" for f in report.findings))
        self.assertFalse(report.confirmed_miner)
        self.assertIn("возможного", report.verdict)

    def test_encoded_powershell_is_decoded_without_execution(self):
        encoded = base64.b64encode("xmrig.exe -o stratum+tcp://pool.supportxmr.com:3333".encode("utf-16-le")).decode()
        self.assertTrue(command_signals("powershell.exe -EncodedCommand " + encoded))
        self.assertEqual(command_signals("powershell -enc thisIsNotValidBase64"), [])

    def test_similar_filename_is_not_classified_as_miner(self):
        self.assertEqual(command_signals("notepad.exe C:\\xmrig-notes.txt"), [])

    def test_ordinary_high_cpu_and_unsigned_file_alone_are_not_miner_verdict(self):
        audit = quiet_audit()
        audit["processes"][0]["cpu_percent"] = 90
        report = analyze(audit, [], [{"path": audit["processes"][0]["path"], "signature": "NotSigned"}])
        self.assertIn("явных признаков", report.verdict)
        self.assertTrue(any(f.level == "Контекст" for f in report.findings))

    def test_combined_load_writable_path_and_unsigned_binary_raise_suspicion(self):
        audit = quiet_audit()
        audit["processes"][0].update(cpu_percent=90, path=r"C:\Users\Demo\AppData\Roaming\worker.exe")
        report = analyze(audit, [], [{"path": audit["processes"][0]["path"], "signature": "NotSigned"}])
        self.assertIn("возможного", report.verdict)
        self.assertFalse(report.confirmed_miner)

    def test_valid_signature_does_not_hide_mining_arguments(self):
        audit = demo_audit()
        report = analyze(audit, [], [{"path": audit["processes"][0]["path"], "signature": "Valid", "signer": "Trusted"}])
        self.assertTrue(any(f.category == "Процесс" for f in report.findings))

    def test_port_without_mining_signals_does_not_flag_ordinary_process(self):
        audit = quiet_audit()
        audit["connections"] = [{"pid": 10, "address": "192.0.2.1", "port": 3333}]
        self.assertEqual(analyze(audit, []).findings, [])

    def test_dns_lookalike_is_not_mining_pool_and_cache_alone_is_context(self):
        audit = quiet_audit()
        audit["dns"] = [{"name": "supportxmr.com.evil.test", "data": "192.0.2.1"}]
        self.assertEqual(analyze(audit, []).findings, [])
        audit["dns"] = [{"name": "pool.supportxmr.com", "data": "192.0.2.1"}]
        report = analyze(audit, [])
        self.assertEqual(report.findings[0].level, "Контекст")
        self.assertFalse(report.confirmed_miner)

    def test_defender_history_is_not_active_infection(self):
        audit = quiet_audit()
        audit["defender"]["threats"] = [{"name": "PUA:Win32/CoinMiner", "active": False, "resources": []}]
        report = analyze(audit, [])
        self.assertFalse(report.confirmed_miner)
        self.assertIn("явных признаков", report.verdict)
        self.assertEqual(report.findings[0].category, "Defender: история")

    def test_active_coinminer_is_confirmed_by_defender_but_other_threat_is_distinct(self):
        audit = quiet_audit()
        audit["defender"]["threats"] = [{"name": "Trojan:Win32/CoinMiner", "active": True}]
        report = analyze(audit, [])
        self.assertTrue(report.confirmed_miner)
        self.assertIn("активной угрозе типа майнер", report.verdict)
        audit["defender"]["threats"][0]["name"] = "Trojan:Win32/Other"
        report = analyze(audit, [])
        self.assertFalse(report.confirmed_miner)
        self.assertIn("тип не подтверждён", report.verdict)

    def test_disabled_persistence_remains_visible_as_context(self):
        entry = Entry("x", "Old miner", state="Отключено", command="xmrig.exe -o stratum+tcp://example.test:3333")
        report = analyze(quiet_audit(), [entry])
        self.assertEqual(report.findings[0].level, "Контекст")
        self.assertIn("отключено", report.findings[0].evidence)

    def test_exclusions_and_missing_defender_are_reported_without_clean_claim(self):
        audit = quiet_audit()
        audit["defender"]["exclusions_processes"] = ["browser.exe"]
        self.assertTrue(any(f.category == "Исключение Defender" for f in analyze(audit, []).findings))
        audit["defender"] = {"available": False}
        self.assertTrue(any("антивирусная проверка не выполнена" in s for s in analyze(audit, []).warnings))

    def test_no_process_data_is_an_incomplete_scan(self):
        audit = quiet_audit()
        audit["processes"] = []
        self.assertIn("недостаточно", analyze(audit, []).verdict)

    def test_cancelled_scan_and_failed_audit_do_not_produce_negative_verdict(self):
        cancel = threading.Event()
        cancel.set()
        with patch("miner.security_request", side_effect=AssertionError("must not run")):
            report = scan_for_miners(cancel=cancel)
        self.assertTrue(report.cancelled)
        self.assertIn("отменена", report.verdict)
        with patch("miner.security_request", side_effect=RuntimeError("Windows denied")):
            with self.assertRaises(RuntimeError):
                scan_for_miners()

    def test_demo_scan_does_not_read_windows_or_start_defender(self):
        with patch("miner.security_request", side_effect=AssertionError("must not run")):
            report = scan_for_miners(demo=True)
        self.assertTrue(report.demo)
        self.assertIn("ДЕМО", report.text())

    @patch("backend.sys.platform", "win32")
    def test_security_reply_must_match_requested_mode(self):
        def runner(args, **kwargs):
            return subprocess.CompletedProcess(args, 0, '{"mode":"defender_full","warnings":[]}', "")
        with self.assertRaises(RuntimeError):
            security_request("audit", runner=runner)


if __name__ == "__main__":
    unittest.main()
