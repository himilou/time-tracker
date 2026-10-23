"""JSON persistence for project definitions and monthly work sessions."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Project, WorkSession


class JsonStorage:
    def __init__(self, data_directory: Path) -> None:
        self.data_directory = data_directory
        self.monthly_directory = data_directory / "months"
        self.projects_path = data_directory / "projects.json"
        self.invoice_sequences_path = data_directory / "invoice_sequences.json"
        self.monthly_directory.mkdir(parents=True, exist_ok=True)
        if not self.projects_path.exists():
            self._write_json(self.projects_path, {"clients": [], "projects": []})
        self.ensure_month_file(datetime.now().astimezone())

    def ensure_month_file(self, moment: datetime) -> Path:
        path = self.monthly_directory / f"{moment:%Y-%m}.json"
        if not path.exists():
            self._write_json(path, {"month": moment.strftime("%Y-%m"), "sessions": []})
        return path

    def load_projects(self) -> list[Project]:
        data = self._read_json(self.projects_path)
        return [Project.from_dict(project) for project in data["projects"]]

    def load_clients(self) -> list[str]:
        data = self._read_json(self.projects_path)
        clients = data.get("clients", [])
        if not isinstance(clients, list) or not all(
            isinstance(client, str) for client in clients
        ):
            raise ValueError(f"Expected a list of client names in {self.projects_path}.")
        return clients

    def allocate_invoice_number(
        self, year: int, client_code: str, first_number: int = 1
    ) -> str:
        if year < 1 or not client_code:
            raise ValueError("Invoice year and client code are required.")
        if first_number < 1:
            raise ValueError("The first invoice number must be positive.")
        if self.invoice_sequences_path.exists():
            data = self._read_json(self.invoice_sequences_path)
        else:
            data = {"last_numbers": {}}
        last_numbers = data.get("last_numbers")
        if not isinstance(last_numbers, dict):
            raise ValueError(
                f"Expected a last_numbers object in {self.invoice_sequences_path}."
            )
        key = f"{year}-{client_code}"
        last_number = last_numbers.get(key, first_number - 1)
        if not isinstance(last_number, int) or isinstance(last_number, bool):
            raise ValueError(
                f"Expected an integer invoice sequence for {key} in "
                f"{self.invoice_sequences_path}."
            )
        number = max(last_number + 1, first_number)
        last_numbers[key] = number
        self._write_json(self.invoice_sequences_path, {"last_numbers": last_numbers})
        return f"{key}-{number:03d}"

    def save_project(self, project: Project) -> None:
        if not project.client_name:
            raise ValueError("A client is required for each project.")
        data = self._read_json(self.projects_path)
        projects = [Project.from_dict(item) for item in data["projects"]]
        for index, saved_project in enumerate(projects):
            if saved_project.name.casefold() == project.name.casefold():
                projects[index] = project
                break
        else:
            projects.append(project)
        clients = data.get("clients", [])
        if project.client_name.casefold() not in {client.casefold() for client in clients}:
            clients.append(project.client_name)
        self._write_json(
            self.projects_path,
            {
                "clients": clients,
                "projects": [item.to_dict() for item in projects],
            },
        )

    def load_sessions(self, moment: datetime | None = None) -> list[WorkSession]:
        month = moment or datetime.now().astimezone()
        path = self.ensure_month_file(month)
        data = self._read_json(path)
        return [WorkSession.from_dict(session) for session in data["sessions"]]

    def load_all_sessions(self) -> list[WorkSession]:
        sessions: list[WorkSession] = []
        for path in sorted(self.monthly_directory.glob("????-??.json")):
            data = self._read_json(path)
            sessions.extend(
                WorkSession.from_dict(entry) for entry in data.get("sessions", [])
            )
        return sessions

    def load_in_progress_session(self) -> WorkSession | None:
        latest_entry: dict[str, Any] | None = None
        latest_path: Path | None = None
        for path in sorted(self.monthly_directory.glob("????-??.json"), reverse=True):
            data = self._read_json(path)
            sessions = data.get("sessions", [])
            if sessions:
                latest_entry = sessions[-1]
                latest_path = path
                break
        if latest_entry is None or latest_path is None:
            return None
        is_complete = (
            latest_entry.get("ended_at") is not None
            and latest_entry.get("total_pay") is not None
            and latest_entry.get("total_hours") is not None
            and latest_entry.get("total_minutes") is not None
        )
        if is_complete:
            return None
        session = WorkSession.from_dict(latest_entry)
        if session.is_complete:
            session = WorkSession(
                project_name=session.project_name,
                client_name=session.client_name,
                hourly_rate=session.hourly_rate,
                started_at=session.started_at,
                turn_count=session.turn_count,
                total_break_seconds=session.total_break_seconds,
                break_started_at=session.break_started_at,
                session_id=session.session_id,
            )
        self.update_session(session)
        return session

    def start_session(self, session: WorkSession) -> None:
        if session.is_complete:
            raise ValueError("A new session must be incomplete.")
        if self.load_in_progress_session() is not None:
            raise ValueError("An unfinished work session already exists.")
        path = self.ensure_month_file(session.started_at)
        data = self._read_json(path)
        data["sessions"].append(session.to_dict())
        self._write_json(path, data)

    def update_session(self, session: WorkSession) -> None:
        path = self.ensure_month_file(session.started_at)
        data = self._read_json(path)
        sessions = data["sessions"]
        for index, saved in enumerate(sessions):
            if self._matches_session(saved, session):
                sessions[index] = session.to_dict()
                self._write_json(path, data)
                return
        raise ValueError(f"Work session {session.session_id} was not found in {path}.")

    def save_session(self, session: WorkSession) -> None:
        if not session.is_complete:
            raise ValueError("Only completed sessions can be saved.")
        start_path = self.ensure_month_file(session.started_at)
        end_path = self.ensure_month_file(session.ended_at)
        entry = session.to_dict()

        if start_path == end_path:
            data = self._read_json(start_path)
            for index, saved in enumerate(data["sessions"]):
                if self._matches_session(saved, session):
                    data["sessions"][index] = entry
                    self._write_json(start_path, data)
                    return
            data["sessions"].append(entry)
            self._write_json(start_path, data)
            return

        end_data = self._read_json(end_path)
        for index, saved in enumerate(end_data["sessions"]):
            if self._matches_session(saved, session):
                end_data["sessions"][index] = entry
                break
        else:
            end_data["sessions"].append(entry)
        self._write_json(end_path, end_data)

        start_data = self._read_json(start_path)
        start_data["sessions"] = [
            saved
            for saved in start_data["sessions"]
            if not self._matches_session(saved, session)
        ]
        self._write_json(start_path, start_data)

    def delete_session(self, session: WorkSession) -> None:
        path = self.ensure_month_file(session.started_at)
        data = self._read_json(path)
        for index, saved in enumerate(data["sessions"]):
            if self._matches_session(saved, session):
                del data["sessions"][index]
                self._write_json(path, data)
                return
        raise ValueError(f"Work session {session.session_id} was not found in {path}.")

    @staticmethod
    def _matches_session(saved: dict[str, Any], session: WorkSession) -> bool:
        if saved.get("session_id"):
            return saved["session_id"] == session.session_id
        return (
            saved.get("started_at") == session.started_at.isoformat()
            and saved.get("project_name") == session.project_name
            and saved.get("client_name", "") == session.client_name
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(f"Expected a JSON object in {path}.")
        return data

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write("\n")
        temporary_path.replace(path)
