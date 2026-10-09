"""Separate diagnostics window with explicit Defender scan actions."""
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from miner import MinerReport, scan_for_miners, security_request


class MinerWindow:
    def __init__(self, app):
        self.app = app
        self.window = tk.Toplevel(app.root)
        self.window.title("Проверка майнера" + (" [ДЕМО]" if app.demo else ""))
        self.window.geometry("1040x760")
        self.window.minsize(740, 540)
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)
        self.busy = False
        self.cancel = threading.Event()
        self.report = None
        self.defender_note = ""
        frame = ttk.Frame(self.window, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Проверка признаков майнера", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Локальный анализ и Microsoft Defender. Вывод не гарантирует отсутствие майнера. "
                  "Эвристические находки требуют проверки.", wraplength=1000).pack(anchor="w", pady=6)
        commands = ttk.Frame(frame)
        commands.pack(fill="x")
        self.scan_button = ttk.Button(commands, text="Повторить анализ", command=self.start_audit)
        self.scan_button.pack(side="left")
        self.defender_buttons = []
        for mode, label in (("defender_quick", "Быстрый Defender"), ("defender_full", "Полный Defender")):
            button = ttk.Button(commands, text=label, command=lambda value=mode: self.start_defender(value))
            button.pack(side="left", padx=5)
            self.defender_buttons.append(button)
        ttk.Button(commands, text="Автономная: инструкция", command=self.offline_info).pack(side="left", padx=5)
        self.cancel_button = ttk.Button(commands, text="Отменить анализ", command=self.cancel_audit, state="disabled")
        self.cancel_button.pack(side="right")
        export = ttk.Frame(frame)
        export.pack(fill="x", pady=7)
        ttk.Button(export, text="Копировать отчёт", command=self.copy_report).pack(side="left")
        ttk.Button(export, text="Сохранить отчёт TXT", command=self.save_report).pack(side="left", padx=6)
        self.status = tk.StringVar(value="Ожидание…")
        ttk.Label(frame, textvariable=self.status, wraplength=1000).pack(anchor="w", pady=5)
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 6))
        pane = ttk.Panedwindow(frame, orient="vertical")
        pane.pack(fill="both", expand=True)
        table = ttk.Frame(pane)
        pane.add(table, weight=1)
        self.tree = ttk.Treeview(table, columns=("level", "category", "subject"), show="headings", height=8)
        for column, label, width in (("level", "Уровень", 110), ("category", "Проверка", 180), ("subject", "Объект", 650)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=80)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.tree.bind("<Button-3>", self.context_menu)
        text_frame = ttk.Frame(pane)
        pane.add(text_frame, weight=2)
        self.text = app.windows["startup"].make_text(text_frame, height=18, readonly=True)
        app.windows["startup"].bind_text_menu(self.text)
        self.window.after(30, self.start_audit)

    def set_busy(self, value, *, cancellable=False):
        self.busy = value
        self.scan_button.configure(state="disabled" if value else "normal")
        for button in self.defender_buttons:
            button.configure(state="disabled" if value else "normal")
        self.cancel_button.configure(state="normal" if value and cancellable else "disabled")
        if value:
            self.progress.start(10)
        else:
            self.progress.stop()

    def start_audit(self):
        if self.busy:
            return
        self.cancel.clear()
        self.set_busy(True, cancellable=True)
        self.status.set("Анализ выполняется…")
        def worker():
            try:
                result = scan_for_miners(lambda label: self.app.results.put(("miner:progress", label)),
                                         self.cancel, demo=self.app.demo)
            except Exception as exc:
                result = exc
            self.app.results.put(("miner:report", result))
        threading.Thread(target=worker, daemon=True).start()

    def cancel_audit(self):
        self.cancel.set()
        self.status.set("Отмена запрошена; ожидается завершение текущего чтения Windows.")

    def start_defender(self, mode):
        if self.busy:
            return
        if self.app.demo:
            messagebox.showinfo("ДЕМО", "Microsoft Defender в режиме демонстрации не запускается.", parent=self.window)
            return
        label = "полную" if mode == "defender_full" else "быструю"
        if not messagebox.askyesno("Microsoft Defender", f"Запустить {label} проверку?\n"
                                  "Defender может обработать обнаруженные угрозы согласно настройкам Windows. "
                                  "Полная проверка может длиться часами. Закрытие этого окна не отменяет проверку Windows.",
                                  parent=self.window):
            return
        self.set_busy(True)
        self.status.set(f"Microsoft Defender выполняет {label} проверку…")
        def worker():
            try:
                result = security_request(mode)
            except Exception as exc:
                result = exc
            self.app.results.put(("miner:defender", result))
        threading.Thread(target=worker, daemon=True).start()

    def receive(self, kind, result):
        if kind == "progress":
            if not self.cancel.is_set():
                self.status.set(result)
            return
        self.set_busy(False)
        if isinstance(result, Exception):
            self.status.set("Проверка не завершена: " + str(result))
            self.write_text("Результат не сформирован; нельзя считать компьютер проверенным.\n\n" + str(result))
            self.report = None
            self.tree.delete(*self.tree.get_children()) if self.tree.get_children() else None
            return
        if kind == "defender":
            self.defender_note = ("Завершение проверки Defender подтверждено." if result.get("completed") is True else
                                  "Завершение проверки Defender не подтверждено.")
            self.defender_note += f" Код: {result.get('exit_code')}. Код 0 может означать также успешно обработанную угрозу.\n"
            self.defender_note += "\n".join(result.get("warnings", [])) + "\n" + str(result.get("output", ""))
            self.start_audit()
            return
        self.report = result
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        for index, finding in enumerate(result.findings):
            self.tree.insert("", "end", iid=str(index), values=(finding.level, finding.category, finding.subject))
        prefix = "ДЕМО. " if result.demo else ""
        self.status.set(prefix + result.verdict + (f" Ограничений: {len(result.warnings)}." if result.warnings else ""))
        self.write_text(self.report_text())

    def report_text(self):
        if not self.report:
            return self.text.get("1.0", "end-1c")
        return (self.defender_note + "\n\n" if self.defender_note else "") + self.report.text()

    def write_text(self, value):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.configure(state="disabled")

    def select(self, _=None):
        selected = self.tree.selection()
        if not selected or not self.report:
            return
        finding = self.report.findings[int(selected[0])]
        self.write_text(f"[{finding.level}] {finding.category}: {finding.subject}\n{finding.evidence}\n\n" + self.report_text())

    def context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.select()
        menu = tk.Menu(self.tree, tearoff=False)
        menu.add_command(label="Копировать выбранную находку", command=self.copy_finding)
        menu.add_command(label="Копировать весь отчёт", command=self.copy_report)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def copy_finding(self):
        selected = self.tree.selection()
        if selected and self.report:
            finding = self.report.findings[int(selected[0])]
            self.window.clipboard_clear()
            self.window.clipboard_append(f"{finding.level}: {finding.category}: {finding.subject}\n{finding.evidence}")

    def copy_report(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(self.report_text())

    def save_report(self):
        path = filedialog.asksaveasfilename(parent=self.window, initialfile="miner_report.txt",
                                           defaultextension=".txt", filetypes=[("Текст", "*.txt")])
        if path:
            try:
                Path(path).write_text(self.report_text(), encoding="utf-8-sig")
            except OSError as exc:
                messagebox.showerror("Отчёт не сохранён", str(exc), parent=self.window)

    def offline_info(self):
        messagebox.showinfo("Автономная проверка Defender", "Сохраните работу. Откройте:\n"
                            "Безопасность Windows → Защита от вирусов и угроз → Параметры сканирования → "
                            "Автономная проверка Microsoft Defender → Выполнить сканирование.\n\n"
                            "Windows перезагрузится. Результат смотрите в журнале защиты после входа. "
                            "Программа сама не запускает перезагрузку и не считает этот метод выполненным.", parent=self.window)
