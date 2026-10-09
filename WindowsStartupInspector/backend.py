"""Windows inventory protocol, filtering, CSV export and personal comments."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

VERSION = "1.0.0"


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    kind: str = ""
    source: str = ""
    owner: str = ""
    command: str = ""
    description: str = ""
    state: str = ""
    start_mode: str = ""
    details: str = ""

    @classmethod
    def from_dict(cls, value: dict) -> "Entry":
        if not isinstance(value, dict) or not value.get("id") or not value.get("name"):
            raise ValueError("Запись Windows не содержит имени или идентификатора.")
        return cls(**{key: str(value.get(key) or "") for key in cls.__dataclass_fields__})


@dataclass
class Inventory:
    section: str
    items: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    collected_at: str = ""
    administrator: bool = False


def resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


def parse_inventory(output: str, section: str) -> Inventory:
    try:
        data = json.loads(output.lstrip("\ufeff"))
    except (ValueError, TypeError) as exc:
        raise ValueError("Windows вернула некорректный JSON вместо списка.") from exc
    if not isinstance(data, dict) or data.get("section") != section:
        raise ValueError("Ответ Windows относится к другому списку.")
    items, warnings = data.get("items"), data.get("warnings")
    if not isinstance(items, list) or not isinstance(warnings, list):
        raise ValueError("В ответе Windows отсутствуют массивы items/warnings.")
    entries = [Entry.from_dict(value) for value in items]
    if len({entry.id for entry in entries}) != len(entries):
        raise ValueError("Windows вернула повторяющиеся идентификаторы записей.")
    return Inventory(section, entries, [str(warning) for warning in warnings],
                     str(data.get("collected_at") or ""), bool(data.get("administrator")))


def collect_inventory(section: str, *, demo: bool = False,
                      runner: Callable = subprocess.run) -> Inventory:
    if section not in ("startup", "services"):
        raise ValueError("Неизвестный раздел.")
    if demo:
        data = json.loads(resource_path("demo.json").read_text(encoding="utf-8"))
        return parse_inventory(json.dumps(data[section]), section)
    if sys.platform != "win32":
        raise RuntimeError("Списки Windows доступны только в Windows 10/11. "
                           "Для просмотра интерфейса используйте --demo.")
    # Use Windows PowerShell 5.1, whose CIM and ScheduledTasks modules ship with Windows.
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    shell = system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    if sys.maxsize <= 2**32 and os.environ.get("PROCESSOR_ARCHITEW6432"):
        shell = system_root / "Sysnative/WindowsPowerShell/v1.0/powershell.exe"
    args = [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
            "Bypass", "-File", str(resource_path("inspect_windows.ps1")), "-Section", section]
    try:
        result = runner(args, capture_output=True, text=True, encoding="utf-8-sig",
                        errors="replace", timeout=180,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Windows не завершила чтение за 180 секунд. "
                           "Проверьте службы WMI и планировщик заданий и повторите обновление.") from exc
    except OSError as exc:
        raise RuntimeError(f"Не удалось запустить Windows PowerShell: {exc}") from exc
    if result.returncode:
        message = (result.stderr or result.stdout).strip()[:3000]
        raise RuntimeError(f"Ошибка чтения Windows (код {result.returncode}):\n{message}")
    inventory = parse_inventory(result.stdout, section)
    if result.stderr.strip():
        inventory.warnings.append(result.stderr.strip()[:3000])
    return inventory


def filter_entries(entries: list[Entry], query: str, mode: str,
                   comments: dict[str, str] | None = None) -> list[Entry]:
    words = query.casefold().split()
    comments = comments or {}
    result = []
    for entry in entries:
        if mode == "running" and entry.state != "Работает":
            continue
        if mode == "automatic" and not entry.start_mode.startswith(("Авто", "Загрузка", "Система")):
            continue
        if mode == "disabled" and entry.state != "Отключено" and entry.start_mode != "Отключена":
            continue
        if mode == "drivers" and entry.kind != "driver":
            continue
        haystack = " ".join(str(value) for value in entry.__dict__.values())
        haystack = (haystack + " " + comments.get(entry.id, "")).casefold()
        if all(word in haystack for word in words):
            result.append(entry)
    return result


def export_csv(path: Path, entries: list[Entry], comments: dict[str, str]) -> None:
    fields = ("name", "kind", "state", "start_mode", "source", "owner", "command", "description", "details")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["Название", "Тип", "Состояние", "Тип запуска", "Источник", "Учётная запись",
                         "Команда / путь", "Описание", "Подробности", "Мой комментарий"])
        for entry in entries:
            writer.writerow([getattr(entry, field) for field in fields] + [comments.get(entry.id, "")])


class CommentStore:
    def __init__(self, path: Path | None = None):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
        self.path = path or base / "WindowsStartupInspector/comments.json"
        self.comments: dict[str, str] = {}
        self.warning = ""
        self.writable = True
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                      for k, v in data.items()):
                    raise ValueError("Некорректный формат заметок")
                self.comments = data
            except (OSError, ValueError) as exc:
                self.warning = f"Не удалось прочитать заметки: {exc}. Исходный файл сохранён."
                self.writable = False

    def save(self, entry_id: str, text: str) -> None:
        if not self.writable:
            raise OSError("Сохранение отключено, чтобы не перезаписать повреждённый файл заметок. "
                          f"Сделайте резервную копию и исправьте или переименуйте {self.path}.")
        updated = dict(self.comments)
        if text.strip():
            updated[entry_id] = text.strip()
        else:
            updated.pop(entry_id, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self.comments = updated
