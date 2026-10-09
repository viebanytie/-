"""Functional GUI test; requires a real or virtual display. No Windows is simulated as real."""
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

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
        print("GUI PASS: two windows, background inventory, details, search, sorting, comments, CSV, driver filter, refresh, reopen.")
    finally:
        root.destroy()
