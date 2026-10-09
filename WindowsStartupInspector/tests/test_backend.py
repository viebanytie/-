import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import CommentStore, Entry, collect_inventory, export_csv, filter_entries, parse_inventory


def payload(section="services", items=None, warnings=None):
    return json.dumps({"section": section, "items": items or [], "warnings": warnings or [],
                       "administrator": False, "collected_at": "2026-10-09"}, ensure_ascii=False)


class InventoryTests(unittest.TestCase):
    def test_unicode_single_row_and_partial_failure_are_preserved(self):
        item = {"id": "service|пример", "name": "Пример", "description": "Описание ☺", "owner": None}
        data = parse_inventory("\ufeff" + payload(items=[item], warnings=["Драйверы недоступны"]), "services")
        self.assertEqual(len(data.items), 1)
        self.assertEqual(data.items[0].description, "Описание ☺")
        self.assertEqual(data.items[0].owner, "")
        self.assertEqual(data.warnings, ["Драйверы недоступны"])

    def test_zero_rows_with_explicit_errors_is_not_relabelled_success(self):
        data = parse_inventory(payload(warnings=["WMI недоступна"]), "services")
        self.assertEqual(data.items, [])
        self.assertEqual(data.warnings, ["WMI недоступна"])

    def test_rejects_wrong_section_malformed_response_and_missing_arrays(self):
        values = ["garbage", "[]", payload("startup"), '{"section":"services"}',
                  '{"section":"services","items":{},"warnings":[]}']
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_inventory(value, "services")

    def test_invalid_or_duplicate_rows_fail_explicitly(self):
        for items in ([{}], ["not a record"], [{"id": "x", "name": "X"}] * 2):
            with self.subTest(items=items), self.assertRaises(ValueError):
                parse_inventory(payload(items=items), "services")

    @patch("backend.sys.platform", "win32")
    def test_windows_launch_uses_literal_script_argument_and_utf8(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, payload(), "")
        with patch.dict("os.environ", {"SystemRoot": "C:\\Windows With Spaces"}):
            collect_inventory("services", runner=run)
        args, kwargs = calls[0]
        self.assertEqual(args[-2:], ["-Section", "services"])
        self.assertIn("-NonInteractive", args)
        self.assertTrue(args[args.index("-File") + 1].endswith("inspect_windows.ps1"))
        self.assertIn("Windows With Spaces", args[0])
        self.assertEqual(kwargs["encoding"], "utf-8-sig")
        self.assertEqual(kwargs["timeout"], 180)

    @patch("backend.sys.platform", "win32")
    def test_process_failure_propagates_and_never_becomes_empty_inventory(self):
        def run(*args, **kwargs):
            return subprocess.CompletedProcess(args, 7, "", "Доступ запрещён")
        with self.assertRaisesRegex(RuntimeError, "код 7"):
            collect_inventory("startup", runner=run)

    @patch("backend.sys.platform", "win32")
    def test_timeout_and_missing_powershell_are_actionable(self):
        for failure, match in [(subprocess.TimeoutExpired("powershell", 180), "180"),
                               (FileNotFoundError("missing"), "PowerShell")]:
            def run(*args, **kwargs):
                raise failure
            with self.subTest(failure=failure), self.assertRaisesRegex(RuntimeError, match):
                collect_inventory("services", runner=run)

    @patch("backend.sys.platform", "win32")
    def test_success_with_stderr_keeps_warning(self):
        def run(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, payload(), "Часть параметров недоступна")
        self.assertEqual(collect_inventory("services", runner=run).warnings, ["Часть параметров недоступна"])

    @patch("backend.sys.platform", "linux")
    def test_non_windows_inventory_does_not_claim_real_windows_data(self):
        with self.assertRaisesRegex(RuntimeError, "Windows 10/11"):
            collect_inventory("startup")

    def test_demo_contains_disabled_services_and_drivers_and_explicit_warning(self):
        data = collect_inventory("services", demo=True)
        self.assertTrue(any(item.kind == "driver" for item in data.items))
        self.assertTrue(any(item.start_mode == "Отключена" for item in data.items))
        self.assertTrue(data.warnings)


class FilterAndExportTests(unittest.TestCase):
    def setUp(self):
        self.entries = collect_inventory("services", demo=True).items

    def test_all_keeps_stopped_disabled_manual_and_driver_records(self):
        self.assertEqual(len(filter_entries(self.entries, "", "all")), 4)

    def test_running_and_automatic_have_different_meanings(self):
        entries = self.entries + [Entry("running-manual", "Ручная", state="Работает", start_mode="Вручную"),
                                  Entry("stopped-auto", "Авто", state="Остановлена", start_mode="Автоматически")]
        self.assertEqual(len(filter_entries(entries, "", "running")), 3)
        self.assertEqual(len(filter_entries(entries, "", "automatic")), 3)
        self.assertNotEqual({e.id for e in filter_entries(entries, "", "running")},
                            {e.id for e in filter_entries(entries, "", "automatic")})

    def test_disabled_and_driver_filters(self):
        self.assertEqual(len(filter_entries(self.entries, "", "disabled")), 1)
        self.assertEqual(filter_entries(self.entries, "", "drivers")[0].kind, "driver")
        startup = collect_inventory("startup", demo=True)
        self.assertEqual(len(filter_entries(startup.items, "", "disabled")), 1)

    def test_case_insensitive_multiword_search_includes_paths_details_and_notes(self):
        self.assertEqual(len(filter_entries(self.entries, "ОТЛОЖЕННО RpcSs", "all")), 1)
        self.assertEqual(len(filter_entries(self.entries, "manual.exe", "all")), 1)
        notes = {self.entries[0].id: "Моя особая заметка"}
        self.assertEqual(len(filter_entries(self.entries, "ОСОБАЯ служба", "all", notes)), 1)

    def test_csv_excel_encoding_and_multiline_roundtrip(self):
        entry = Entry("id", 'Название; "тест"', description="Первая\nВторая", command="C:\\Папка\\программа.exe")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.csv"
            export_csv(path, [entry], {"id": "Заметка;\nещё"})
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle, delimiter=";"))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1][0], entry.name)
            self.assertEqual(rows[1][7], entry.description)
            self.assertEqual(rows[1][-1], "Заметка;\nещё")


class CommentTests(unittest.TestCase):
    def test_save_reload_delete_and_keep_other_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested/comments.json"
            store = CommentStore(path)
            store.save("one", " Русская заметка ")
            store.save("two", "Другая")
            reloaded = CommentStore(path)
            self.assertEqual(reloaded.comments, {"one": "Русская заметка", "two": "Другая"})
            reloaded.save("one", "   ")
            self.assertEqual(CommentStore(path).comments, {"two": "Другая"})

    def test_corrupt_comments_are_preserved_and_not_overwritten(self):
        for bad in ("not json", '[]', '{"one": 42}'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "comments.json"
                path.write_text(bad, encoding="utf-8")
                store = CommentStore(path)
                self.assertTrue(store.warning)
                with self.assertRaises(OSError):
                    store.save("one", "Новая заметка")
                self.assertEqual(path.read_text(encoding="utf-8"), bad)

    def test_failed_disk_write_does_not_change_in_memory_comment(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CommentStore(Path(directory) / "comments.json")
            store.save("one", "До")
            with patch.object(Path, "replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    store.save("one", "После")
            self.assertEqual(store.comments["one"], "До")
            self.assertEqual(CommentStore(store.path).comments["one"], "До")


if __name__ == "__main__":
    unittest.main()
