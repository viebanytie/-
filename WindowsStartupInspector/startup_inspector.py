"""Two simple text windows for the Windows startup inventory."""
from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from backend import (VERSION, CommentStore, Entry, Inventory, collect_inventory,
                     export_csv, filter_entries)
from controls import demo_change, needs_typed_confirmation, perform_action, restart_elevated, supported_actions
from miner_window import MinerWindow

FILTERS = {"Все записи": "all", "Работающие": "running", "Автоматический запуск": "automatic",
           "Отключённые": "disabled", "Драйверы": "drivers"}


class ListWindow:
    def __init__(self, app: "InspectorApp", window: tk.Tk | tk.Toplevel, section: str):
        self.app, self.window, self.section = app, window, section
        self.inventory = Inventory(section)
        self.visible: list[Entry] = []
        self.by_id: dict[str, Entry] = {}
        self.loading = False
        self.selected_id = ""
        title = "Автозапуск Windows" if section == "startup" else "Все службы и драйверы Windows"
        window.title(f"{title} — Windows Startup Inspector {VERSION}" + (" [ДЕМО]" if app.demo else ""))
        window.geometry("1000x730" if section == "startup" else "1040x730")
        window.minsize(700, 520)
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=title, font=("Segoe UI", 14, "bold")).pack(anchor="w")
        hint = ("Реестр, папки Startup, задания при загрузке/входе и параметры Winlogon."
                if section == "startup" else
                "Все записи, включая остановленные, отключённые службы и системные драйверы.")
        ttk.Label(frame, text=hint, wraplength=950).pack(anchor="w", pady=(2, 8))
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Поиск:").pack(side="left")
        self.query = tk.StringVar()
        self.search = ttk.Entry(toolbar, textvariable=self.query)
        self.search.pack(side="left", fill="x", expand=True, padx=6)
        self.query.trace_add("write", lambda *_: self.render())
        self.mode = tk.StringVar(value="Все записи")
        choices = list(FILTERS) if section == "services" else ["Все записи", "Отключённые"]
        mode = ttk.Combobox(toolbar, textvariable=self.mode, values=choices, state="readonly", width=23)
        mode.pack(side="left", padx=(0, 6))
        mode.bind("<<ComboboxSelected>>", lambda _: self.render())
        self.refresh_button = ttk.Button(toolbar, text="Обновить (F5)", command=self.refresh)
        self.refresh_button.pack(side="left")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=7)
        ttk.Button(actions, text="Экспорт списка в CSV", command=self.export).pack(side="left")
        ttk.Button(actions, text="Ошибки / полнота списка", command=self.show_warnings).pack(side="left", padx=6)
        other = "services" if section == "startup" else "startup"
        ttk.Button(actions, text="Открыть другое окно", command=lambda: app.show(other)).pack(side="left")
        ttk.Button(actions, text="Выход", command=app.root.destroy).pack(side="right")
        manage = ttk.Frame(frame)
        manage.pack(fill="x", pady=(0, 7))
        self.action_buttons = {}
        for action, label in (("disable", "Отключить"), ("enable", "Включить обратно"),
                              ("stop", "Остановить сейчас"), ("start", "Запустить сейчас")):
            if section == "startup" and action in {"start", "stop"}:
                continue
            button = ttk.Button(manage, text=label, command=lambda value=action: self.change(value), state="disabled")
            button.pack(side="left", padx=(0, 5))
            self.action_buttons[action] = button
        utilities = ttk.Frame(frame)
        utilities.pack(fill="x", pady=(0, 7))
        ttk.Button(utilities, text="Проверка майнера", command=app.show_miner).pack(side="left")
        ttk.Button(utilities, text="Права администратора", command=app.elevate).pack(side="right")
        pane = ttk.Panedwindow(frame, orient="vertical")
        pane.pack(fill="both", expand=True)
        table_frame = ttk.Frame(pane)
        pane.add(table_frame, weight=3)
        columns = ("name", "state", "start_mode", "owner", "description") if section == "services" else (
            "name", "state", "source", "owner", "description")
        self.columns = columns
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse", height=12)
        labels = {"name": "Название", "state": "Состояние", "start_mode": "Запуск",
                  "owner": "Учётная запись", "description": "Описание / комментарий", "source": "Источник"}
        for column in columns:
            self.tree.heading(column, text=labels[column], command=lambda col=column: self.sort(col))
            self.tree.column(column, width=250 if column in ("name", "description") else 150, minwidth=85)
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.tree.bind("<Button-3>", self.context_menu)
        details_frame = ttk.Frame(pane, padding=(0, 8, 0, 0))
        pane.add(details_frame, weight=2)
        ttk.Label(details_frame, text="Подробности выбранной записи:").pack(anchor="w")
        self.details = self.make_text(details_frame, height=8, readonly=True)
        ttk.Label(details_frame, text="Мой комментарий (сохраняется только на этом компьютере):").pack(anchor="w", pady=(6, 0))
        self.note = self.make_text(details_frame, height=3)
        self.bind_text_menu(self.details)
        self.bind_text_menu(self.note)
        ttk.Button(details_frame, text="Сохранить комментарий", command=self.save_note).pack(anchor="e", pady=3)
        self.status = tk.StringVar(value="Ожидание чтения…")
        ttk.Label(frame, textvariable=self.status, wraplength=970).pack(anchor="w", pady=(7, 0))
        window.bind("<F5>", lambda _: self.refresh())
        window.bind("<Control-f>", lambda _: self.search.focus_set())
        self.sort_column, self.reverse = "name", False

    @staticmethod
    def bind_text_menu(widget):
        menu = tk.Menu(widget, tearoff=False)
        def copy(all_text=False):
            try:
                value = widget.get("1.0", "end-1c") if all_text else widget.get("sel.first", "sel.last")
            except tk.TclError:
                return
            widget.clipboard_clear()
            widget.clipboard_append(value)
        menu.add_command(label="Копировать выделенное", command=copy)
        menu.add_command(label="Копировать весь текст", command=lambda: copy(True))
        def popup(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        widget.bind("<Button-3>", popup)

    def context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.tree.focus(row)
        self.select()
        menu = tk.Menu(self.tree, tearoff=False)
        for field, label in (("row", "Копировать всю запись"), ("name", "Копировать название"),
                             ("command", "Копировать путь / команду"), ("description", "Копировать описание"),
                             ("details", "Копировать подробности")):
            menu.add_command(label=label, command=lambda value=field: self.copy_field(value))
        menu.add_separator()
        entry = self.by_id[row]
        for action, label in (("disable", "Отключить"), ("enable", "Включить обратно"),
                              ("stop", "Остановить сейчас"), ("start", "Запустить сейчас")):
            if action in supported_actions(entry):
                menu.add_command(label=label, command=lambda value=action: self.change(value),
                                 state="disabled" if self.loading else "normal")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def copy_field(self, field):
        entry = self.by_id.get(self.selected_id)
        if not entry:
            return
        value = self.entry_text(entry) if field == "row" else getattr(entry, field)
        if field == "row":
            value += "\nМой комментарий: " + self.app.comments.comments.get(entry.id, "")
        self.window.clipboard_clear()
        self.window.clipboard_append(value)

    def update_actions(self):
        entry = self.by_id.get(self.selected_id)
        allowed = supported_actions(entry) if entry and not self.loading else set()
        for action, button in self.action_buttons.items():
            button.configure(state="normal" if action in allowed else "disabled")

    def change(self, action):
        entry = self.by_id.get(self.selected_id)
        if self.loading or not entry or action not in supported_actions(entry):
            return
        explanation = {"disable": "Отключить автозапуск выбранной записи?\n"
                       "Для службы изменится тип запуска, но работающая служба продолжит работать.\n"
                       "Прежние параметры сохраняются для обратного включения.",
                       "enable": "Включить запись обратно? Для службы будет восстановлен прежний тип запуска.\n"
                       "Если копии нет, будет выбран ручной запуск. Сам процесс/служба сейчас не запускается.",
                       "stop": "Остановить выбранную службу сейчас? Это может нарушить работу зависимых программ.\n"
                       "Тип запуска не меняется. Принудительная остановка зависимостей не выполняется.",
                       "start": "Запустить выбранную службу сейчас? Тип запуска не меняется."}[action]
        if self.app.demo:
            explanation = "ДЕМО: это изменит только вымышленную запись на экране.\n" + explanation
        if not messagebox.askyesno("Изменение Windows", entry.name + "\n\n" + explanation, parent=self.window):
            return
        if needs_typed_confirmation(entry, action) and not self.app.demo:
            expected = str(entry.target.get("name", ""))
            typed = simpledialog.askstring("Системная служба / драйвер", "Изменение может нарушить загрузку, вход или защиту Windows.\n"
                                           f"Для подтверждения введите системное имя:\n{expected}", parent=self.window)
            if typed != expected:
                return
        self.loading = True
        self.refresh_button.configure(state="disabled")
        self.update_actions()
        self.status.set("Применение выбранного действия…")
        def worker():
            try:
                result = (entry, action, perform_action(entry, action, demo=self.app.demo))
            except Exception as exc:
                result = exc
            self.app.results.put((self.section + ":action", result))
        threading.Thread(target=worker, daemon=True).start()

    def receive_action(self, result):
        self.loading = False
        self.refresh_button.configure(state="normal")
        self.update_actions()
        if isinstance(result, Exception):
            self.status.set("Действие не подтверждено: " + str(result))
            messagebox.showerror("Изменение не выполнено", str(result), parent=self.window)
            # The OS may have applied part of a request before an error; read actual state.
            self.refresh()
            return
        entry, action, response = result
        if self.app.demo:
            changed = demo_change(entry, action, self.app.demo_previous_modes)
            self.inventory.items = [changed if item.id == entry.id else item for item in self.inventory.items]
            self.by_id[changed.id] = changed
            self.render()
        else:
            self.refresh()
        messagebox.showinfo("Результат", response.get("message", "Действие выполнено."), parent=self.window)

    @staticmethod
    def make_text(parent, *, height: int, readonly: bool = False) -> tk.Text:
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)
        text = tk.Text(container, height=height, wrap="word", font=("Segoe UI", 10),
                       relief="solid", borderwidth=1, undo=not readonly)
        scroll = ttk.Scrollbar(container, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        if readonly:
            text.configure(state="disabled")
        return text

    def refresh(self):
        if self.loading:
            return
        self.loading = True
        self.refresh_button.configure(state="disabled")
        self.update_actions()
        self.status.set("Чтение Windows… окно остаётся доступным. При большом числе заданий это может занять время.")

        def worker():
            try:
                result = collect_inventory(self.section, demo=self.app.demo)
            except Exception as exc:
                result = exc
            self.app.results.put((self.section, result))

        threading.Thread(target=worker, daemon=True).start()

    def receive(self, result):
        self.loading = False
        self.refresh_button.configure(state="normal")
        if isinstance(result, Exception):
            self.inventory.warnings.append(str(result))
            self.status.set("Обновление не выполнено; отображаются предыдущие данные. " + str(result))
            messagebox.showerror("Ошибка чтения Windows", str(result), parent=self.window)
            return
        self.inventory = result
        self.by_id = {entry.id: entry for entry in result.items}
        self.render()

    def render(self):
        selection = self.tree.selection()
        selected = selection[0] if selection else self.selected_id
        self.visible = filter_entries(self.inventory.items, self.query.get(), FILTERS[self.mode.get()],
                                      self.app.comments.comments)
        self.visible.sort(key=lambda entry: getattr(entry, self.sort_column).casefold(), reverse=self.reverse)
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        for entry in self.visible:
            values = [getattr(entry, column) for column in self.columns]
            comment = self.app.comments.comments.get(entry.id)
            if comment:
                values[-1] = f"{entry.description} | Заметка: {comment}".replace("\n", " ")
            self.tree.insert("", "end", iid=entry.id, values=values)
        if self.tree.exists(selected):
            self.tree.selection_set(selected)
        else:
            self.selected_id = ""
            self.set_details("")
            self.note.delete("1.0", "end")
        self.update_actions()
        if not self.loading:
            warnings = len(self.inventory.warnings) + bool(self.app.comments.warning)
            access = "администратор" if self.inventory.administrator else "обычный пользователь"
            prefix = "ДЕМО — вымышленные данные. " if self.app.demo else ""
            self.status.set(f"{prefix}Показано {len(self.visible)} из {len(self.inventory.items)}. "
                            f"Ошибок/ограничений: {warnings}. Доступ: {access}. "
                            f"Снимок: {self.inventory.collected_at}")

    def sort(self, column):
        self.reverse = not self.reverse if self.sort_column == column else False
        self.sort_column = column
        self.render()

    def set_details(self, value):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", value)
        self.details.configure(state="disabled")

    def select(self, _=None):
        selection = self.tree.selection()
        if not selection:
            return
        entry = self.by_id[selection[0]]
        if self.selected_id == entry.id:
            # Preserve the unsaved note when search or sorting keeps the same selection.
            self.set_entry_details(entry)
            return
        self.selected_id = entry.id
        self.update_actions()
        self.set_entry_details(entry)
        self.note.delete("1.0", "end")
        self.note.insert("1.0", self.app.comments.comments.get(entry.id, ""))

    def set_entry_details(self, entry):
        self.set_details(self.entry_text(entry))

    @staticmethod
    def entry_text(entry):
        return (f"Название: {entry.name}\nСостояние: {entry.state}\n"
                         f"Тип запуска: {entry.start_mode or '—'}\nИсточник: {entry.source}\n"
                         f"Учётная запись: {entry.owner or '—'}\nКоманда / путь: {entry.command or '—'}\n"
                         f"Описание Windows/файла: {entry.description or 'Не предоставлено'}\n\n{entry.details}")

    def save_note(self):
        if not self.selected_id:
            messagebox.showinfo("Комментарий", "Выберите запись в списке.", parent=self.window)
            return
        try:
            self.app.comments.save(self.selected_id, self.note.get("1.0", "end-1c"))
        except OSError as exc:
            messagebox.showerror("Комментарий не сохранён", str(exc), parent=self.window)
            return
        self.render()

    def show_warnings(self):
        warnings = list(self.inventory.warnings)
        if self.app.comments.warning:
            warnings.append(self.app.comments.warning)
        coverage = ("Автозапуск: Run/RunOnce/политики Explorer в 32- и 64-битном реестре, "
                    "загруженные профили пользователей, доступные папки Startup, задания с триггерами "
                    "Boot/Logon, Shell/Userinit Winlogon и BootExecute. Это обзор основных источников, "
                    "а не полный аналог Autoruns. Незагруженные пользовательские реестры и другие "
                    "механизмы расширений Windows не читаются.\n\n"
                    "Службы: Win32_Service и Win32_SystemDriver, независимо от состояния и типа запуска. "
                    "Автоматическая служба не обязательно уже работает; ручную службу может запускать "
                    "триггер или другая программа. Описание берётся из Windows и свойств файла.\n\n"
                    "Для максимально доступного списка запустите программу от имени администратора "
                    "и нажмите «Обновить» в обоих окнах. Кнопки управления меняют только выбранную запись после подтверждения. "
                    "Winlogon и BootExecute остаются доступными только для просмотра.")
        window = tk.Toplevel(self.window)
        window.title("Источники данных и ошибки чтения")
        window.geometry("760x520")
        text = self.make_text(window, height=20)
        text.insert("1.0", coverage + "\n\nОшибки/ограничения текущего снимка:\n" +
                    ("\n\n".join(warnings) if warnings else "Ошибки чтения не зарегистрированы."))
        text.configure(state="disabled")

    def export(self):
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension=".csv",
                                           initialfile=f"windows_{self.section}.csv",
                                           filetypes=[("CSV для Excel", "*.csv")])
        if not path:
            return
        try:
            export_csv(Path(path), self.visible, self.app.comments.comments)
        except OSError as exc:
            messagebox.showerror("Экспорт не выполнен", str(exc), parent=self.window)
            return
        messagebox.showinfo("Экспорт", f"Сохранено записей: {len(self.visible)}", parent=self.window)


class InspectorApp:
    def __init__(self, root: tk.Tk, *, demo: bool = False, comments_path: Path | None = None):
        self.root, self.demo = root, demo
        self.comments = CommentStore(comments_path)
        self.results: queue.Queue = queue.Queue()
        self.demo_previous_modes = {}
        self.miner_window = None
        self.windows = {"startup": ListWindow(self, root, "startup"),
                        "services": ListWindow(self, tk.Toplevel(root), "services")}
        screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
        height = max(520, min(730, screen_height - 90))
        if screen_width >= 1460:
            width = (screen_width - 60) // 2
            self.windows["startup"].window.geometry(f"{width}x{height}+20+20")
            self.windows["services"].window.geometry(f"{width}x{height}+{width + 40}+20")
        else:
            width = max(700, min(1000, screen_width - 80))
            self.windows["startup"].window.geometry(f"{width}x{height}+20+20")
            self.windows["services"].window.geometry(f"{width}x{height}+50+50")
        self.windows["startup"].window.protocol("WM_DELETE_WINDOW", root.destroy)
        root.after(50, self.poll)
        for window in self.windows.values():
            window.refresh()

    def show(self, section):
        window = self.windows[section].window
        window.deiconify()
        window.lift()

    def show_miner(self):
        if self.miner_window is None:
            self.miner_window = MinerWindow(self)
        self.miner_window.window.deiconify()
        self.miner_window.window.lift()

    def elevate(self):
        if self.demo:
            messagebox.showinfo("ДЕМО", "В демонстрационном режиме права Windows не меняются.", parent=self.root)
            return
        try:
            restart_elevated()
        except Exception as exc:
            messagebox.showerror("Повышение прав", str(exc), parent=self.root)
            return
        messagebox.showinfo("Права администратора", "Запущена отдельная копия с запросом UAC. "
                            "Сохраните заметки и закройте прежнюю копию.", parent=self.root)

    def poll(self):
        try:
            while True:
                section, result = self.results.get_nowait()
                if section.startswith("miner:"):
                    if self.miner_window:
                        self.miner_window.receive(section.split(":", 1)[1], result)
                elif section.endswith(":action"):
                    self.windows[section.split(":", 1)[0]].receive_action(result)
                else:
                    self.windows[section].receive(result)
        except queue.Empty:
            pass
        self.root.after(75, self.poll)


def main():
    parser = argparse.ArgumentParser(description="Автозапуск и все службы Windows в двух текстовых окнах.")
    parser.add_argument("--demo", action="store_true", help="Вымышленные данные для проверки интерфейса")
    parser.add_argument("--dump", choices=["startup", "services"], help="Получить JSON без открытия окон")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    if args.dump:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        try:
            inventory = collect_inventory(args.dump, demo=args.demo)
        except Exception as exc:
            parser.exit(1, f"{exc}\n")
        print(json.dumps({"section": inventory.section, "collected_at": inventory.collected_at,
                          "administrator": inventory.administrator,
                          "items": [entry.__dict__ for entry in inventory.items],
                          "warnings": inventory.warnings}, ensure_ascii=False, indent=2))
        return
    root = tk.Tk()
    InspectorApp(root, demo=args.demo)
    root.mainloop()


if __name__ == "__main__":
    main()
