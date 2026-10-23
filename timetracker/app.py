"""Tkinter user interface for the time tracker."""

from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from queue import Empty, SimpleQueue
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Literal

from PIL import Image, ImageDraw

from .invoices import (
    create_invoice_docx,
    first_invoice_number,
    invoice_client_code,
    sessions_for_invoice,
)
from .models import Project, WorkSession
from .storage import JsonStorage
from .tray import StatusNotifierTray


class TimeTrackerApp:
    def __init__(self, root: tk.Tk, storage: JsonStorage) -> None:
        self.root = root
        self.storage = storage
        self.active_session: WorkSession | None = None
        self.break_started_at: datetime | None = None
        self.accumulated_break_seconds = 0
        self.timer_after_id: str | None = None
        self.tray_actions: SimpleQueue[tuple[str, Exception | None]] = SimpleQueue()
        self.tray_after_id: str | None = None
        self.tray_icon: StatusNotifierTray | None = None
        root.title("Time Tracker")
        root.minsize(1024, 500)
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.client_name = tk.StringVar()
        self.project_name = tk.StringVar()
        self.hourly_rate = tk.StringVar()
        self.turn_count = tk.StringVar(value="0")
        self.status = tk.StringVar(value="No active session")
        self.elapsed_display = tk.StringVar(value="00:00:00")

        self._build_ui()
        self._reload_projects()
        self._reload_sessions()
        self._restore_in_progress_session()
        self._start_tray_icon()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        project_frame = ttk.LabelFrame(main, text="Project", padding=8)
        project_frame.pack(fill="x")
        ttk.Label(project_frame, text="Client").grid(row=0, column=0, sticky="w")
        self.client_picker = ttk.Combobox(
            project_frame, textvariable=self.client_name, state="normal"
        )
        self.client_picker.grid(row=0, column=1, columnspan=4, sticky="ew", pady=(0, 8))
        ttk.Label(project_frame, text="Project").grid(row=1, column=0, sticky="w")
        self.project_picker = ttk.Combobox(
            project_frame, textvariable=self.project_name, state="normal", width=24
        )
        self.project_picker.grid(row=1, column=1, sticky="w", padx=(8, 12))
        self.project_picker.bind("<<ComboboxSelected>>", self._select_project)
        ttk.Label(project_frame, text="Hourly rate").grid(row=1, column=2, sticky="w")
        self.rate_input = ttk.Entry(project_frame, textvariable=self.hourly_rate, width=12)
        self.rate_input.grid(row=1, column=3, sticky="ew", padx=(8, 12))
        self.save_project_button = ttk.Button(
            project_frame, text="Save project", command=self._save_project
        )
        self.save_project_button.grid(row=1, column=4)
        self.invoice_button = ttk.Button(
            project_frame, text="Create invoice", command=self._open_invoice_dialog
        )
        self.invoice_button.grid(row=1, column=5, padx=(8, 0))

        controls = ttk.Frame(main, padding=(0, 12))
        controls.pack(fill="x")
        self.start_button = ttk.Button(controls, text="Start session", command=self._start)
        self.start_button.pack(side="left")
        self.end_button = ttk.Button(
            controls, text="End session", command=self._end, state="disabled"
        )
        self.end_button.pack(side="left")
        ttk.Label(controls, text="Worked").pack(side="left", padx=(16, 4))
        ttk.Label(
            controls,
            textvariable=self.elapsed_display,
            font=("TkDefaultFont", 12, "bold"),
        ).pack(side="left")
        self.break_indicator = tk.Label(
            controls,
            text="WORKING",
            foreground="white",
            background="#2e7d32",
            padx=8,
            pady=4,
        )
        self.break_indicator.pack(side="left", padx=12)
        self.break_indicator.pack_forget()
        ttk.Label(controls, textvariable=self.status).pack(side="left")

        session_actions = ttk.Frame(controls)
        session_actions.pack(side="right")
        turn_display = ttk.Frame(session_actions)
        turn_display.pack(side="right", padx=(8, 0))
        ttk.Label(turn_display, text="Turns").pack(side="left", padx=(0, 5))
        self.turn_count_input = ttk.Entry(
            turn_display,
            textvariable=self.turn_count,
            width=5,
            justify="center",
            font=("TkDefaultFont", 14, "bold"),
            state="disabled",
        )
        self.turn_count_input.pack(side="left")
        self.turn_count_input.bind("<Return>", self._commit_turn_count)
        self.turn_count_input.bind("<FocusOut>", self._commit_turn_count)
        self.next_turn_button = ttk.Button(
            session_actions, text="Next turn", command=self._next_turn, state="disabled"
        )
        self.next_turn_button.pack(side="right", padx=(8, 0))
        self.reset_turns_button = ttk.Button(
            session_actions,
            text="Reset turns",
            command=self._reset_turns,
            state="disabled",
        )
        self.reset_turns_button.pack(side="right", padx=(8, 0))
        self.break_button = ttk.Button(
            session_actions,
            text="Start break",
            command=self._toggle_break,
            state="disabled",
        )
        self.break_button.pack(side="right")

        history = ttk.LabelFrame(main, text="This month’s completed sessions", padding=8)
        history.pack(fill="both", expand=True)
        columns = (
            "project",
            "client",
            "rate",
            "started",
            "ended",
            "duration",
            "break",
            "turns",
            "pay",
        )
        table_frame = ttk.Frame(history)
        table_frame.pack(fill="both", expand=True)
        self.session_table = ttk.Treeview(
            table_frame, columns=columns, show="headings"
        )
        headings = {
            "project": ("Project", 130),
            "client": ("Client", 120),
            "rate": ("Hourly rate", 85),
            "started": ("Started", 135),
            "ended": ("Ended", 135),
            "duration": ("Worked", 85),
            "break": ("Break", 75),
            "turns": ("Turns", 55),
            "pay": ("Total pay", 80),
        }
        for column, (label, width) in headings.items():
            self.session_table.heading(column, text=label)
            self.session_table.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.session_table.yview
        )
        self.session_table.configure(yscrollcommand=scrollbar.set)
        self.session_table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        totals_frame = ttk.Frame(history)
        totals_frame.pack(fill="x", pady=(8, 0))
        self.monthly_hours_total = tk.StringVar(value="Total hours worked: 0h 0m")
        self.monthly_pay_total = tk.StringVar(value="Total pay: $0.00")
        ttk.Label(
            totals_frame,
            textvariable=self.monthly_hours_total,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(side="left")
        ttk.Label(
            totals_frame,
            textvariable=self.monthly_pay_total,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(side="right")

    def _reload_projects(self) -> None:
        self.projects = self.storage.load_projects()
        self.project_picker["values"] = [project.name for project in self.projects]
        self.client_picker["values"] = self.storage.load_clients()

    def _open_invoice_dialog(self) -> None:
        today = date.today()
        period_start = today.replace(day=1)
        period_end = today.replace(
            day=calendar.monthrange(today.year, today.month)[1]
        )

        dialog = tk.Toplevel(self.root)
        dialog.title("Create invoice")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        clients = sorted(
            {
                session.client_name
                for session in self.storage.load_all_sessions()
                if session.client_name
            }
            | set(self.storage.load_clients()),
            key=str.casefold,
        )
        client_value = tk.StringVar(value=clients[0] if clients else "")
        start_value = tk.StringVar(value=period_start.isoformat())
        end_value = tk.StringVar(value=period_end.isoformat())
        error_value = tk.StringVar()

        ttk.Label(frame, text="Client").grid(row=0, column=0, sticky="w", pady=4)
        client_picker = ttk.Combobox(
            frame, textvariable=client_value, values=clients, state="readonly", width=28
        )
        client_picker.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="Start date (YYYY-MM-DD)").grid(
            row=1, column=0, sticky="w", pady=4
        )
        ttk.Entry(frame, textvariable=start_value, width=30).grid(
            row=1, column=1, sticky="ew", pady=4
        )
        ttk.Label(frame, text="End date (YYYY-MM-DD)").grid(
            row=2, column=0, sticky="w", pady=4
        )
        ttk.Entry(frame, textvariable=end_value, width=30).grid(
            row=2, column=1, sticky="ew", pady=4
        )
        ttk.Label(frame, textvariable=error_value, foreground="red", wraplength=360).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=4
        )

        def generate() -> None:
            try:
                start = date.fromisoformat(start_value.get().strip())
                end = date.fromisoformat(end_value.get().strip())
                selected_sessions = sessions_for_invoice(
                    self.storage.load_all_sessions(),
                    client_value.get(),
                    start,
                    end,
                )
            except (OSError, ValueError, KeyError) as exc:
                error_value.set(str(exc))
                return
            if not selected_sessions:
                error_value.set("No completed sessions found for this client and date range.")
                return
            safe_client_name = re.sub(
                r"[^A-Za-z0-9._-]+", "_", client_value.get()
            ).strip("_")
            default_name = (
                f"Invoice_{safe_client_name}_{start:%Y%m%d}_{end:%Y%m%d}.docx"
            )
            output_name = filedialog.asksaveasfilename(
                parent=dialog,
                title="Save invoice DOCX",
                initialfile=default_name,
                defaultextension=".docx",
                filetypes=[("Word documents", "*.docx")],
            )
            if not output_name:
                return
            try:
                client_code = invoice_client_code(client_value.get())
                invoice_number = self.storage.allocate_invoice_number(
                    today.year,
                    client_code,
                    first_invoice_number(today.year, client_code),
                )
                total = create_invoice_docx(
                    selected_sessions,
                    client_value.get(),
                    start,
                    end,
                    invoice_number,
                    Path(output_name),
                )
            except (OSError, ValueError) as exc:
                error_value.set(str(exc))
                return
            dialog.destroy()
            messagebox.showinfo(
                "Invoice created",
                f"Invoice saved to:\n{output_name}\n\nTotal: {total}",
                parent=self.root,
            )

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(buttons, text="Generate DOCX", command=generate).pack(side="right")

    def _reload_sessions(self) -> None:
        for item in self.session_table.get_children():
            self.session_table.delete(item)
        sessions = [
            session
            for session in self.storage.load_sessions()
            if session.is_complete
        ]
        total_minutes = 0
        total_pay = Decimal("0.00")
        for session in sessions:
            hours, minutes = session.total_hours_and_minutes
            total_minutes += hours * 60 + minutes
            total_pay += session.total_pay
            self.session_table.insert(
                "",
                "end",
                values=(
                    session.project_name,
                    session.client_name,
                    str(session.hourly_rate),
                    session.started_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                    session.ended_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                    f"{hours}h {minutes}m",
                    self._format_duration(session.total_break_seconds),
                    session.turn_count,
                    str(session.total_pay),
                ),
            )
        total_hours, remaining_minutes = divmod(total_minutes, 60)
        self.monthly_hours_total.set(
            f"Total hours worked: {total_hours}h {remaining_minutes}m"
        )
        self.monthly_pay_total.set(f"Total pay: ${total_pay:.2f}")

    def _select_project(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        for project in self.projects:
            if project.name == self.project_name.get():
                self.hourly_rate.set(str(project.hourly_rate))
                self.client_name.set(project.client_name)
                return

    def _save_project(self) -> None:
        try:
            project = Project(
                self.project_name.get(),
                self.hourly_rate.get(),
                self.client_name.get(),
            )
            self.storage.save_project(project)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Could not save project", str(exc), parent=self.root)
            return
        self._reload_projects()
        self.project_name.set(project.name)
        self.hourly_rate.set(str(project.hourly_rate))

    def _restore_in_progress_session(self) -> None:
        try:
            session = self.storage.load_in_progress_session()
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror(
                "Could not restore work session", str(exc), parent=self.root
            )
            return
        if session is None:
            return
        self.active_session = session
        self.turn_count.set(str(session.turn_count))
        self.accumulated_break_seconds = session.total_break_seconds
        self.break_started_at = session.break_started_at
        self.client_name.set(session.client_name)
        self.project_name.set(session.project_name)
        self.hourly_rate.set(str(session.hourly_rate))
        self._set_session_controls(active=True)
        self._set_break_indicator(
            on_break=session.break_started_at is not None,
            active=True,
        )
        self.break_button.configure(
            text="End break" if session.break_started_at else "Start break"
        )
        self.status.set(f"Recovered session — {session.project_name}")
        self._update_timer()

    def _start(self) -> None:
        try:
            project = Project(
                self.project_name.get(),
                self.hourly_rate.get(),
                self.client_name.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Project required", str(exc), parent=self.root)
            return
        saved_project = next(
            (
                item
                for item in self.projects
                if item.name.casefold() == project.name.casefold()
            ),
            None,
        )
        if saved_project is not None and (
            saved_project.client_name.casefold() != project.client_name.casefold()
        ):
            messagebox.showerror(
                "Project client changed",
                "Save the project before starting a session with a different client.",
                parent=self.root,
            )
            return
        session = WorkSession(
            project_name=project.name,
            hourly_rate=project.hourly_rate,
            started_at=datetime.now().astimezone(),
            client_name=project.client_name,
        )
        try:
            self.storage.start_session(session)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Could not start session", str(exc), parent=self.root)
            return
        self.active_session = session
        self.turn_count.set(str(session.turn_count))
        self.accumulated_break_seconds = 0
        self.break_started_at = None
        self._set_session_controls(active=True)
        self._set_break_indicator(on_break=False, active=True)
        self.status.set(f"Session active — {project.name}")
        self._update_timer()

    def _toggle_break(self) -> None:
        if self.active_session is None:
            return
        now = datetime.now().astimezone()
        if self.break_started_at is None:
            updated_session = self.active_session.start_break(now)
        else:
            updated_session = self.active_session.end_break(now)
        try:
            self.storage.update_session(updated_session)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Could not update session", str(exc), parent=self.root)
            return
        self.active_session = updated_session
        self.accumulated_break_seconds = updated_session.total_break_seconds
        self.break_started_at = updated_session.break_started_at
        if self.break_started_at is not None:
            self._set_break_indicator(on_break=True)
            self.break_button.configure(text="End break")
            self.status.set("Break in progress")
        else:
            self._set_break_indicator(on_break=False)
            self.break_button.configure(text="Start break")
            self.status.set(f"Session active — {self.active_session.project_name}")
        self._update_timer()

    def _set_break_indicator(self, on_break: bool, active: bool = True) -> None:
        if not active:
            self.break_indicator.pack_forget()
            return
        if not self.break_indicator.winfo_manager():
            self.break_indicator.pack(side="left", padx=12)
        if on_break:
            self.break_indicator.configure(text="ON BREAK", background="#c62828")
        else:
            self.break_indicator.configure(text="WORKING", background="#2e7d32")

    def _current_break_seconds(self, now: datetime) -> int:
        if self.active_session is None:
            return 0
        return self.active_session.break_seconds_at(now)

    def _update_timer(self) -> None:
        if self.timer_after_id is not None:
            self.root.after_cancel(self.timer_after_id)
        self.timer_after_id = None
        if self.active_session is None:
            return
        now = datetime.now().astimezone()
        elapsed_seconds = self.active_session.worked_seconds_at(now)
        self.elapsed_display.set(self._format_duration(elapsed_seconds))
        self.timer_after_id = self.root.after(1000, self._timer_tick)

    def _timer_tick(self) -> None:
        self.timer_after_id = None
        self._update_timer()

    def _next_turn(self) -> None:
        if self.active_session is None:
            return
        updated_session = self.active_session.next_turn()
        try:
            self.storage.update_session(updated_session)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Could not update session", str(exc), parent=self.root)
            return
        self.active_session = updated_session
        self.turn_count.set(str(updated_session.turn_count))
        self.status.set(f"Session active — {self.active_session.project_name}")

    def _reset_turns(self) -> None:
        if self.active_session is None or self.active_session.turn_count == 0:
            return
        updated_session = self.active_session.reset_turns()
        try:
            self.storage.update_session(updated_session)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Could not update session", str(exc), parent=self.root)
            return
        self.active_session = updated_session
        self.turn_count.set("0")
        self.status.set(
            "Break in progress"
            if self.active_session.break_started_at is not None
            else f"Session active — {self.active_session.project_name}"
        )

    def _commit_turn_count(self, event: tk.Event[tk.Misc] | None = None) -> str:
        if self.active_session is None:
            self.turn_count.set("0")
            return "break"
        value = self.turn_count.get().strip()
        if not value.isdecimal():
            self.turn_count.set(str(self.active_session.turn_count))
            if event is not None and event.type == tk.EventType.FocusOut:
                self.status.set("Turn count unchanged; enter a non-negative whole number.")
            else:
                messagebox.showerror(
                    "Invalid turn count",
                    "Turn count must be a non-negative whole number.",
                    parent=self.root,
                )
            return "break"
        try:
            updated_session = self.active_session.set_turn_count(int(value))
            self.storage.update_session(updated_session)
        except (OSError, ValueError, KeyError) as exc:
            self.turn_count.set(str(self.active_session.turn_count))
            messagebox.showerror("Could not update turn count", str(exc), parent=self.root)
            return "break"
        self.active_session = updated_session
        self.turn_count.set(str(updated_session.turn_count))
        self.status.set(
            "Break in progress"
            if updated_session.break_started_at is not None
            else f"Session active — {updated_session.project_name}"
        )
        return "break"

    def _end(self) -> None:
        if self.active_session is None:
            return
        now = datetime.now().astimezone()
        total_break_seconds = self._current_break_seconds(now)
        reviewed = self._review_session(
            self.active_session, now, total_break_seconds
        )
        if reviewed is None:
            return
        if reviewed == "discard":
            try:
                self.storage.delete_session(self.active_session)
            except (OSError, ValueError, KeyError) as exc:
                messagebox.showerror("Could not discard session", str(exc), parent=self.root)
                return
            self._clear_active_session()
            self._reload_sessions()
            return
        try:
            self.storage.save_session(reviewed)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Could not save session", str(exc), parent=self.root)
            return
        self._clear_active_session()
        self._reload_sessions()

    def _clear_active_session(self) -> None:
        self.active_session = None
        self.break_started_at = None
        self.accumulated_break_seconds = 0
        if self.timer_after_id is not None:
            self.root.after_cancel(self.timer_after_id)
            self.timer_after_id = None
        self._set_session_controls(active=False)
        self._set_break_indicator(on_break=False, active=False)
        self.break_button.configure(text="Start break")
        self.elapsed_display.set("00:00:00")
        self.status.set("No active session")

    def _review_session(
        self,
        session: WorkSession,
        default_end: datetime,
        total_break_seconds: int,
    ) -> WorkSession | Literal["discard"] | None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Review work session")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        start_value = tk.StringVar(
            value=session.started_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        )
        end_value = tk.StringVar(value=default_end.strftime("%Y-%m-%d %H:%M:%S"))
        break_value = tk.StringVar(value=self._format_duration(total_break_seconds))
        pay_value = tk.StringVar()
        worked_value = tk.StringVar()
        error_value = tk.StringVar()

        for row, (label, variable) in enumerate(
            (
                ("Start time (YYYY-MM-DD HH:MM:SS)", start_value),
                ("Stop time (YYYY-MM-DD HH:MM:SS)", end_value),
                ("Total break (HH:MM:SS)", break_value),
            )
        ):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(frame, textvariable=variable, width=25).grid(
                row=row, column=1, sticky="ew", pady=4
            )
        ttk.Label(frame, text="Worked time").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Label(frame, textvariable=worked_value).grid(
            row=3, column=1, sticky="w", pady=4
        )
        ttk.Label(frame, text="Total pay").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Label(frame, textvariable=pay_value).grid(
            row=4, column=1, sticky="w", pady=4
        )
        ttk.Label(frame, textvariable=error_value, foreground="red", wraplength=360).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=4
        )

        result: WorkSession | Literal["discard"] | None = None

        def build_session() -> WorkSession:
            start = datetime.strptime(
                start_value.get().strip(), "%Y-%m-%d %H:%M:%S"
            ).astimezone()
            end = datetime.strptime(
                end_value.get().strip(), "%Y-%m-%d %H:%M:%S"
            ).astimezone()
            break_seconds = self._parse_duration(break_value.get())
            return WorkSession(
                project_name=session.project_name,
                hourly_rate=session.hourly_rate,
                started_at=start,
                ended_at=end,
                turn_count=session.turn_count,
                client_name=session.client_name,
                total_break_seconds=break_seconds,
                session_id=session.session_id,
            )

        def refresh_preview(*_args: str) -> None:
            try:
                reviewed = build_session()
            except (ValueError, OverflowError) as exc:
                worked_value.set("—")
                pay_value.set("—")
                error_value.set(str(exc))
                return
            hours, minutes = reviewed.total_hours_and_minutes
            worked_value.set(f"{hours}h {minutes}m")
            pay_value.set(str(reviewed.total_pay))
            error_value.set("")

        def confirm() -> None:
            nonlocal result
            try:
                result = build_session()
            except (ValueError, OverflowError) as exc:
                error_value.set(str(exc))
                return
            dialog.destroy()

        def discard() -> None:
            nonlocal result
            result = "discard"
            dialog.destroy()

        start_value.trace_add("write", refresh_preview)
        end_value.trace_add("write", refresh_preview)
        break_value.trace_add("write", refresh_preview)
        refresh_preview()

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Discard", command=discard).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(buttons, text="Save session", command=confirm).pack(side="right")
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_window()
        return result

    @staticmethod
    def _parse_duration(value: str) -> int:
        parts = value.strip().split(":")
        if len(parts) != 3:
            raise ValueError("Break time must use HH:MM:SS format.")
        try:
            hours, minutes, seconds = (int(part) for part in parts)
        except ValueError as exc:
            raise ValueError("Break time must use HH:MM:SS format.") from exc
        if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
            raise ValueError("Enter a valid, non-negative break duration.")
        return hours * 3600 + minutes * 60 + seconds

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        hours, remainder = divmod(max(0, total_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _set_session_controls(self, active: bool) -> None:
        state = "disabled" if active else "normal"
        self.client_picker.configure(state=state)
        self.project_picker.configure(state="disabled" if active else "normal")
        self.rate_input.configure(state=state)
        self.save_project_button.configure(state=state)
        self.start_button.configure(state=state)
        self.break_button.configure(state="normal" if active else "disabled")
        self.next_turn_button.configure(state="normal" if active else "disabled")
        self.reset_turns_button.configure(state="normal" if active else "disabled")
        self.turn_count_input.configure(state="normal" if active else "disabled")
        self.end_button.configure(state="normal" if active else "disabled")

    @staticmethod
    def _create_tray_image() -> Image.Image:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 60, 60), fill="#2e7d32")
        draw.ellipse((12, 12, 52, 52), outline="white", width=4)
        draw.line((32, 18, 32, 32, 43, 38), fill="white", width=4)
        return image

    def _start_tray_icon(self) -> None:
        self.tray_icon = StatusNotifierTray(
            self._create_tray_pixmap(),
            self._queue_show_window,
            self._queue_tray_error,
        )
        self.tray_icon.start()
        self.tray_after_id = self.root.after(100, self._process_tray_actions)

    @classmethod
    def _create_tray_pixmap(cls) -> bytes:
        image = cls._create_tray_image().convert("RGBA")
        argb = bytearray()
        for red, green, blue, alpha in image.getdata():
            argb.extend((alpha, red, green, blue))
        return bytes(argb)

    def _queue_show_window(self) -> None:
        self.tray_actions.put(("show", None))

    def _queue_tray_error(self, error: Exception) -> None:
        self.tray_actions.put(("error", error))

    def _process_tray_actions(self) -> None:
        self.tray_after_id = None
        while True:
            try:
                action, error = self.tray_actions.get_nowait()
            except Empty:
                break
            if action == "show":
                self._bring_to_front()
            elif error is not None:
                messagebox.showwarning(
                    "Tray icon unavailable",
                    f"The tray icon could not be started: {error}",
                    parent=self.root,
                )
        if self.root.winfo_exists():
            self.tray_after_id = self.root.after(100, self._process_tray_actions)

    def _bring_to_front(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.focus_force()
        self.root.after(250, self._clear_topmost)

    def _clear_topmost(self) -> None:
        if self.root.winfo_exists():
            self.root.attributes("-topmost", False)

    def close(self) -> None:
        if self.timer_after_id is not None:
            self.root.after_cancel(self.timer_after_id)
            self.timer_after_id = None
        if self.tray_after_id is not None:
            self.root.after_cancel(self.tray_after_id)
            self.tray_after_id = None
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.destroy()


def main() -> None:
    data_directory = Path.home() / ".timetracker"
    storage = JsonStorage(data_directory)
    root = tk.Tk(className="TimeTracker")
    TimeTrackerApp(root, storage)
    root.mainloop()
