"""Explicit Windows control requests; never interpolate commands from inventory data."""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from backend import Entry, run_windows_script

ACTIONS = {"registry": {"disable", "enable"}, "folder": {"disable", "enable"},
           "task": {"disable", "enable"}, "service": {"disable", "enable", "start", "stop"}}
CRITICAL_SERVICES = {"rpcss", "dcomlaunch", "rpceptmapper", "plugplay", "power", "eventlog",
                     "wininit", "lsm", "samss", "profsvc", "schedule", "winmgmt",
                     "windefend", "wdnissvc", "securityhealthservice", "wscsvc"}


def supported_actions(entry: Entry) -> set[str]:
    return ACTIONS.get(entry.target.get("type", ""), set())


def needs_typed_confirmation(entry: Entry, action: str) -> bool:
    return action in {"disable", "stop"} and (
        entry.kind == "driver" or str(entry.target.get("name", "")).casefold() in CRITICAL_SERVICES)


def perform_action(entry: Entry, action: str, *, demo: bool = False, runner=None) -> dict:
    if action not in supported_actions(entry):
        raise ValueError("Для этой системной записи действие не поддерживается. "
                         "Winlogon и BootExecute доступны только для просмотра.")
    if demo:
        return {"success": True, "demo": True, "action": action, "id": entry.id,
                "message": "ДЕМО: изменена только запись на экране, настройки Windows не менялись."}
    # A UTF-8 request file avoids shell/PowerShell injection and Legacy argument quoting problems.
    with tempfile.TemporaryDirectory(prefix="startup-inspector-action-") as folder:
        request = Path(folder) / "request.json"
        request.write_text(json.dumps({"action": action, "id": entry.id, "target": entry.target},
                                      ensure_ascii=False), encoding="utf-8")
        kwargs = {"runner": runner} if runner is not None else {}
        result = run_windows_script("manage_windows.ps1", ["-RequestFile", str(request)], **kwargs)
    try:
        response = json.loads(result.stdout.lstrip("\ufeff"))
    except ValueError as exc:
        raise RuntimeError("Windows не подтвердила результат изменения.") from exc
    if not isinstance(response, dict) or response.get("success") is not True or response.get("id") != entry.id:
        raise RuntimeError("Windows не подтвердила изменение выбранной записи.")
    return response


def demo_change(entry: Entry, action: str, previous_modes: dict[str, str]) -> Entry:
    if action == "disable":
        if entry.target.get("type") == "service":
            previous_modes.setdefault(entry.id, entry.start_mode)
            return replace(entry, start_mode="Отключена")
        return replace(entry, state="Отключено")
    if action == "enable":
        if entry.target.get("type") == "service":
            return replace(entry, start_mode=previous_modes.pop(entry.id, "Вручную"))
        return replace(entry, state="Включено")
    return replace(entry, state="Работает" if action == "start" else "Остановлена")


def restart_elevated() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Повышение прав доступно только в Windows.")
    arguments = [] if getattr(sys, "frozen", False) else [str(Path(__file__).with_name("startup_inspector.py"))]
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable,
                                                subprocess.list2cmdline(arguments), None, 1)
    if result <= 32:
        raise RuntimeError("Windows не разрешила повышение прав или запрос UAC был отменён.")
