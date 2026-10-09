"""Functional GUI test; requires a real or virtual display. No Windows is simulated as real."""
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import CommentStore, export_csv
from startup_inspector import InspectorApp


def wait(root, predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        root.update()
        if time.monotonic() > deadline:
            raise AssertionError("GUI inventory timed out")
        time.sleep(0.01)
    root.update()


with tempfile.TemporaryDirectory() as directory:
    root = tk.Tk()
    try:
        app = InspectorApp(root, demo=True, comments_path=Path(directory) / "comments.json")
        wait(root, lambda: all(not window.loading for window in app.windows.values()))
        startup, services = app.windows["startup"], app.windows["services"]
        assert len(startup.tree.get_children()) == 3
        assert len(services.tree.get_children()) == 4
        assert services.window.winfo_exists() and startup.window.winfo_exists()
        assert "ДЕМО" in services.window.title()
        services.query.set("ExampleService")
        root.update()
        assert len(services.tree.get_children()) == 1
        services.tree.selection_set(services.tree.get_children()[0])
        root.update()
        assert "service.exe" in services.details.get("1.0", "end")
        services.note.insert("1.0", "Мой комментарий для проверки")
        services.sort("name")
        root.update()
        assert "Мой комментарий" in services.note.get("1.0", "end")
        services.save_note()
        root.update()
        assert len(CommentStore(app.comments.path).comments) == 1
        services.query.set("для проверки")
        root.update()
        assert len(services.tree.get_children()) == 1
        export_csv(Path(directory) / "services.csv", services.visible, app.comments.comments)
        assert "Мой комментарий" in (Path(directory) / "services.csv").read_text(encoding="utf-8-sig")
        services.query.set("")
        services.mode.set("Драйверы")
        services.render()
        root.update()
        assert len(services.tree.get_children()) == 1
        assert services.visible[0].kind == "driver"
        services.mode.set("Все записи")
        services.refresh()
        wait(root, lambda: not services.loading)
        assert len(services.tree.get_children()) == 4
        services.window.withdraw()
        root.update()
        app.show("services")
        root.update()
        assert services.window.state() != "withdrawn"
        services.tree.selection_set("demo|service1")
        services.tree.see("demo|service1")
        root.update()
        services.copy_field("command")
        assert "service.exe" in root.clipboard_get()
        services.copy_field("row")
        assert "Мой комментарий для проверки" in root.clipboard_get()
        bounds = services.tree.bbox("demo|service1")
        with patch("tkinter.Menu.tk_popup") as popup:
            services.context_menu(SimpleNamespace(y=bounds[1]+2, x_root=5, y_root=5))
            assert popup.called
        with patch("startup_inspector.messagebox.askyesno", return_value=True), patch("startup_inspector.messagebox.showinfo"):
            services.change("disable")
            wait(root, lambda: not services.loading)
            assert services.by_id["demo|service1"].start_mode == "Отключена"
            assert services.by_id["demo|service1"].state == "Работает"
            services.change("enable")
            wait(root, lambda: not services.loading)
            assert services.by_id["demo|service1"].start_mode == "Автоматически (отложенно)"
            services.change("stop")
            wait(root, lambda: not services.loading)
            assert services.by_id["demo|service1"].state == "Остановлена"
            services.change("start")
            wait(root, lambda: not services.loading)
            assert services.by_id["demo|service1"].state == "Работает"
            startup.tree.selection_set("demo|run")
            root.update()
            startup.change("disable")
            wait(root, lambda: not startup.loading)
            assert startup.by_id["demo|run"].state == "Отключено"
            startup.change("enable")
            wait(root, lambda: not startup.loading)
            assert startup.by_id["demo|run"].state == "Включено"
        app.show_miner()
        wait(root, lambda: app.miner_window.report is not None and not app.miner_window.busy)
        miner = app.miner_window
        assert miner.report.demo and not miner.report.confirmed_miner
        assert any(f.category == "Процесс" for f in miner.report.findings)
        miner.copy_report()
        assert "ДЕМО" in root.clipboard_get() and "stratum" in root.clipboard_get()
        assert "возможного майнера" in miner.status.get()
        print("GUI PASS: windows, search, notes, CSV, context copy, disable/restore startup/services, stop/start, separate miner report and clipboard.")
    finally:
        root.destroy()
