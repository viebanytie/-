"""Local evidence-based miner diagnostics; findings are not an antivirus verdict."""
from __future__ import annotations

import base64
import fnmatch
import json
import ntpath
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend import Entry, collect_inventory, run_windows_script

MINERS = {"xmrig", "xmr-stak", "xmrstak", "lolminer", "nbminer", "t-rex", "trex", "teamredminer",
          "ethminer", "phoenixminer", "gminer", "nanominer", "cpuminer", "minerd", "srbminer", "wildrig"}
POOLS = ("supportxmr.com", "moneroocean.stream", "nanopool.org", "hashvault.pro", "2miners.com",
         "nicehash.com", "f2pool.com", "viabtc.com", "herominers.com", "woolypooly.com",
         "unmineable.com", "miningpoolhub.com", "zpool.ca")
PORTS = {3333, 4444, 5555, 7777, 14444}
MINING_ARGS = re.compile(r"stratum(?:\+(?:tcp|tls|ssl))?://|--(?:algo|coin)\s+(?:randomx|rx/0|monero|ethash|kawpow)|--donate-level\b", re.I)


@dataclass
class Finding:
    category: str
    level: str
    subject: str
    evidence: str


@dataclass
class MinerReport:
    findings: list[Finding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    defender: dict = field(default_factory=dict)
    files: list[dict] = field(default_factory=list)
    confirmed_miner: bool = False
    cancelled: bool = False
    demo: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))

    @property
    def verdict(self) -> str:
        if self.cancelled:
            return "Проверка отменена; заключение не сформировано."
        if self.confirmed_miner:
            return "Microsoft Defender сообщает об активной угрозе типа майнер."
        if any(threat.get("active") is True for threat in self.defender.get("threats", [])):
            return "Defender сообщает об активной угрозе; её тип не подтверждён как майнер."
        if not self.counts.get("Процессы", 0):
            return "Данных о процессах недостаточно; проверку нельзя считать завершённой."
        if any(f.level in {"Высокий", "Средний"} for f in self.findings):
            return "Найдены признаки возможного майнера. Нужна проверка найденных записей."
        return "По выполненным проверкам явных признаков майнера не найдено."

    def text(self) -> str:
        lines = (["ДЕМО — вымышленные данные."] if self.demo else []) + [
            self.verdict, "Это не гарантирует отсутствие майнера; эвристики могут давать ложные срабатывания.",
            f"Снимок: {self.created_at}", "", "Проверенные данные:"]
        lines += [f"{key}: {value}" for key, value in self.counts.items()]
        lines += ["", "Находки:"]
        for f in self.findings:
            lines += [f"[{f.level}] {f.category}: {f.subject}", f.evidence, ""]
        if not self.findings:
            lines += ["Совпадения не обнаружены."]
        lines += ["", "Microsoft Defender:", json.dumps(self.defender, ensure_ascii=False, indent=2)]
        if self.files:
            lines += ["", "Подписи и SHA-256 проверенных файлов:"]
            for item in self.files:
                lines += [str(item.get("path", "")), f"Подпись: {item.get('signature', '')}; издатель: {item.get('signer', '')}",
                          f"SHA-256: {item.get('sha256', '')}", ""]
        lines += ["", "Ошибки и ограничения:"]
        lines += self.warnings or ["Ошибки чтения не зарегистрированы."]
        lines += ["", "Охват: процессы и команды; выборка CPU за 5 секунд; доступные счётчики GPU; TCP и DNS; "
                  "автозапуск, службы/драйверы, все задания, WMI-потребители и IFEO; Defender и исключения; подписи и SHA-256 кандидатов.",
                  "Имя файла, высокий CPU/GPU, порт, DNS-запись, папка AppData или отсутствие подписи сами по себе не доказывают наличие майнера.",
                  "История Defender не означает активное заражение. Полная/быстрая проверка запускаются отдельными кнопками.",
                  "Не исключаются спящие процессы, переименованные майнеры, внедрение в другие процессы, rootkit, скрытые драйверы и новые варианты.",
                  "Автономную проверку Defender можно выполнить через Windows Security после сохранения работы. "
                  "Эта программа не проводит автономную проверку и не выполняет все известные методы анализа."]
        return "\n".join(lines)


def command_signals(command: str) -> list[str]:
    signals = []
    expanded = command
    encoded = re.search(r"-(?:enc|encodedcommand)\s+[\"']?([A-Za-z0-9+/=]{16,})", command, re.I)
    if encoded and len(encoded.group(1)) <= 65536:
        try:
            decoded = base64.b64decode(encoded.group(1), validate=True).decode("utf-16-le")
            expanded += "\n" + decoded
        except (ValueError, UnicodeError):
            pass
    if MINING_ARGS.search(expanded):
        signals.append("Аргументы содержат Stratum или характерные параметры майнинга.")
    lowered = expanded.casefold()
    for domain in POOLS:
        if re.search(r"(?<![\w.-])(?:[\w-]+\.)*" + re.escape(domain) + r"(?![\w.-])", lowered):
            signals.append(f"В команде указан домен майнинг-пула: {domain}.")
            break
    # Token boundaries avoid flagging names such as xmrig-notes.txt or unrelated substrings.
    if re.search(r"(?<![\w.-])(?:" + "|".join(re.escape(name) for name in sorted(MINERS)) + r")(?:\.exe)?(?![\w.-])", lowered):
        signals.append("В команде встречается название известного майнера.")
    return signals


def executable_path(command: str) -> str:
    match = re.match(r'^\s*"([^"]+)"', command) or re.match(r"^\s*(.+?\.exe)(?=\s|$)", command, re.I)
    return match.group(1) if match else ""


def writable_location(path: str) -> bool:
    lowered = path.replace("/", "\\").casefold()
    return any(token in lowered for token in ("\\appdata\\", "\\temp\\", "\\downloads\\", "\\public\\"))


def analyze(audit: dict, entries: list[Entry], files: list[dict] | None = None) -> MinerReport:
    report = MinerReport(warnings=list(audit.get("warnings", [])), defender=audit.get("defender", {}),
                         files=files or [])
    processes = audit.get("processes", [])
    report.counts = {"Процессы": len(processes), "CPU-измерения": sum(p.get("cpu_percent") is not None for p in processes),
                     "GPU-счётчики": len(audit.get("gpu", [])), "TCP-подключения": len(audit.get("connections", [])),
                     "DNS-записи": len(audit.get("dns", [])), "Записи автозапуска и служб": len(entries),
                     "Дополнительные записи закрепления": len(audit.get("persistence", [])), "Проверенные файлы": len(report.files)}
    metadata = {str(item.get("path", "")).casefold(): item for item in report.files}
    flagged_pids = set()
    for process in processes:
        name, path, command = str(process.get("name", "")), str(process.get("path", "")), str(process.get("command", ""))
        signals = command_signals(command)
        if ntpath.splitext(name)[0].casefold() in MINERS:
            signals.append("Имя процесса совпадает с названием известного майнера; файл может быть переименован или легитимен.")
        pid = process.get("pid")
        cpu = process.get("cpu_percent")
        gpu = max((float(g.get("percent") or 0) for g in audit.get("gpu", []) if g.get("pid") == pid), default=0)
        if signals:
            flagged_pids.add(pid)
            report.findings.append(Finding("Процесс", "Высокий" if len(signals) >= 2 else "Средний",
                                           f"{name} (PID {pid})", "\n".join(signals) + f"\nПуть: {path}\nКоманда: {command}"))
        if (isinstance(cpu, (float, int)) and cpu >= 20) or gpu >= 70:
            info = metadata.get(path.casefold(), {})
            untrusted = info.get("signature") in {"NotSigned", "HashMismatch", "NotTrusted"}
            level = "Средний" if untrusted and writable_location(path) else "Контекст"
            report.findings.append(Finding("Нагрузка", level, f"{name} (PID {pid})",
                                           f"CPU: {cpu if cpu is not None else 'недоступно'}% всего компьютера; GPU: {gpu}% отдельного движка.\n"
                                           f"Путь: {path}; подпись: {info.get('signature', 'не проверена')}.\n"
                                           "Высокая нагрузка возможна у игры, браузера, сборщика или антивируса."))
            if level == "Средний":
                flagged_pids.add(pid)
        for exclusion in report.defender.get("exclusions_processes", []):
            if fnmatch.fnmatch(name.casefold(), str(exclusion).casefold()) or (path and fnmatch.fnmatch(path.casefold(), str(exclusion).casefold())):
                report.findings.append(Finding("Исключение Defender", "Контекст", name, f"Процесс входит в исключения: {exclusion}."))
        if path:
            for exclusion in report.defender.get("exclusions_paths", []):
                mask = str(exclusion).rstrip("\\/").casefold()
                if path.casefold() == mask or path.casefold().startswith(mask + "\\") or fnmatch.fnmatch(path.casefold(), mask):
                    report.findings.append(Finding("Исключение Defender", "Контекст", name, f"Путь входит в исключения: {exclusion}."))
    for row in [dict(name=e.name, source=e.source, command=e.command,
                     enabled=e.state != "Отключено" and e.start_mode != "Отключена") for e in entries] + audit.get("persistence", []):
        signals = command_signals(str(row.get("command", "")))
        if signals:
            state = "включено" if row.get("enabled") is True else "отключено" if row.get("enabled") is False else "активность не определена"
            report.findings.append(Finding("Закрепление", "Средний" if row.get("enabled") is not False else "Контекст",
                                           f"{row.get('name')} — {row.get('source')}",
                                           "\n".join(signals) + f"\nСостояние: {state}\nКоманда: {row.get('command')}"))
    addresses = {}
    for item in audit.get("dns", []):
        domain = str(item.get("name", "")).rstrip(".").casefold()
        if any(domain == pool or domain.endswith("." + pool) for pool in POOLS):
            addresses[str(item.get("data", ""))] = domain
            report.findings.append(Finding("Кэш DNS", "Контекст", domain,
                                           "В кэше есть домен майнинг-пула. Это может быть след посещения сайта или старого подключения."))
    for connection in audit.get("connections", []):
        domain = addresses.get(str(connection.get("address")))
        port = connection.get("port")
        if domain or (port in PORTS and connection.get("pid") in flagged_pids):
            report.findings.append(Finding("Подключение", "Средний" if domain else "Контекст",
                                           f"PID {connection.get('pid')}",
                                           f"Активное TCP: {connection.get('address')}:{port}; DNS-сопоставление: {domain or 'нет'}.\n"
                                           "Порт не доказывает протокол Stratum, а домен не доказывает заражение."))
    defender = report.defender
    if not defender.get("available"):
        report.warnings.append("Microsoft Defender недоступен; антивирусная проверка не выполнена.")
    else:
        status = defender.get("status", {})
        if not status.get("antivirus_enabled") or not status.get("realtime_enabled"):
            report.warnings.append("Защита Defender отключена или работает в пассивном режиме. Проверьте активный антивирус.")
        if status.get("signature_age", 0) > 3:
            report.warnings.append("Сигнатуры Defender старше трёх дней.")
    for threat in defender.get("threats", []):
        active = threat.get("active") is True
        miner = bool(re.search(r"coinminer|cryptominer|(?:^|[:/])miner\b|xmrig|monero", str(threat.get("name", "")), re.I))
        if active and miner:
            report.confirmed_miner = True
        report.findings.append(Finding("Defender: активная угроза" if active else "Defender: история",
                                       "Высокий" if active else "Контекст", str(threat.get("name", "")),
                                       f"Активна: {active}. Ресурсы: {threat.get('resources', [])}"))
    return report


def security_request(mode: str, *, paths: list[str] | None = None, runner=None) -> dict:
    if mode not in {"audit", "files", "defender_quick", "defender_full"}:
        raise ValueError("Неизвестная проверка.")
    with tempfile.TemporaryDirectory(prefix="startup-inspector-scan-") as folder:
        request = Path(folder) / "files.json"
        request.write_text(json.dumps({"paths": paths or []}, ensure_ascii=False), encoding="utf-8")
        kwargs = {"runner": runner} if runner is not None else {}
        response = run_windows_script("security_probe.ps1", ["-Mode", mode, "-RequestFile", str(request)],
                                      timeout=7200 if mode == "defender_full" else 1800 if mode == "defender_quick" else 480, **kwargs)
    data = json.loads(response.stdout.lstrip("\ufeff"))
    if not isinstance(data, dict) or data.get("mode") != mode or not isinstance(data.get("warnings"), list):
        raise RuntimeError("Некорректный ответ Windows при диагностике.")
    if response.stderr.strip():
        data["warnings"].append(response.stderr.strip()[:3000])
    return data


def demo_audit() -> dict:
    return {"warnings": ["ДЕМО: данные придуманы, компьютер не проверялся."],
            "processes": [{"pid": 123, "name": "xmrig.exe", "path": r"C:\Users\Demo\AppData\Roaming\xmrig.exe",
                           "command": "xmrig.exe -o stratum+tcp://pool.supportxmr.com:3333", "cpu_percent": 85}],
            "gpu": [], "connections": [{"pid": 123, "address": "192.0.2.1", "port": 3333}],
            "dns": [{"name": "pool.supportxmr.com", "data": "192.0.2.1"}], "persistence": [],
            "defender": {"available": True, "status": {"antivirus_enabled": True, "realtime_enabled": True,
                                                     "signature_age": 0}, "threats": []}}


def scan_for_miners(progress=lambda text: None, cancel: threading.Event | None = None, *, demo=False) -> MinerReport:
    cancel = cancel or threading.Event()
    def checkpoint(label):
        if cancel.is_set():
            raise InterruptedError()
        progress(label)
    try:
        checkpoint("Чтение процессов, CPU/GPU, сети, закрепления и Defender…")
        audit = demo_audit() if demo else security_request("audit")
        entries = []
        for section in ("startup", "services"):
            checkpoint("Чтение " + ("автозапуска…" if section == "startup" else "служб и драйверов…"))
            try:
                inventory = collect_inventory(section, demo=demo)
                entries += inventory.items
                audit["warnings"].extend(inventory.warnings)
            except Exception as exc:
                audit["warnings"].append(f"{section}: {exc}")
        checkpoint("Проверка подписей и SHA-256 файлов с подозрительными признаками…")
        candidates = set()
        for process in audit.get("processes", []):
            cpu = process.get("cpu_percent")
            gpu_busy = any(g.get("pid") == process.get("pid") and float(g.get("percent") or 0) >= 70
                           for g in audit.get("gpu", []))
            if process.get("path") and (command_signals(str(process.get("command", ""))) or
                                       writable_location(str(process["path"])) or
                                       gpu_busy or isinstance(cpu, (float, int)) and cpu >= 20):
                candidates.add(process["path"])
        for command in [e.command for e in entries] + [str(r.get("command", "")) for r in audit.get("persistence", [])]:
            if command_signals(command) and (path := executable_path(command)):
                candidates.add(path)
        file_data = {"files": [], "warnings": []}
        if candidates and not demo:
            try:
                file_data = security_request("files", paths=sorted(candidates))
            except Exception as exc:
                audit["warnings"].append(f"Подписи/хеши: {exc}")
        audit["warnings"].extend(file_data["warnings"])
        checkpoint("Сопоставление признаков…")
        report = analyze(audit, entries, file_data.get("files", []))
        report.demo = demo
        return report
    except InterruptedError:
        return MinerReport(cancelled=True, demo=demo)
