import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from timetracker.models import Project, WorkSession
from timetracker.storage import JsonStorage


class WorkSessionTests(unittest.TestCase):
    def test_loads_session_without_break_fields_as_zero_break(self) -> None:
        session = WorkSession.from_dict(
            {
                "project_name": "Project",
                "hourly_rate": "60",
                "started_at": "2026-01-02T09:00:00+00:00",
                "ended_at": "2026-01-02T10:00:00+00:00",
                "total_hours": 1,
                "total_minutes": 0,
                "turn_count": 0,
            }
        )

        self.assertEqual(session.total_break_seconds, 0)
        self.assertEqual(session.total_hours_and_minutes, (1, 0))
        self.assertEqual(session.total_pay, Decimal("60.00"))

    def test_new_session_starts_with_zero_turns_and_computes_duration(self) -> None:
        session = WorkSession(
            "Project",
            Decimal("42.50"),
            datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(session.turn_count, 0)
        with self.assertRaises(ValueError):
            _ = session.total_hours_and_minutes

        session = session.next_turn().finish(
            datetime(2026, 1, 2, 10, 37, tzinfo=timezone.utc),
            total_break_seconds=600,
        )

        self.assertEqual(session.total_hours_and_minutes, (1, 27))
        self.assertEqual(session.turn_count, 1)
        self.assertEqual(session.total_pay, Decimal("61.63"))
        self.assertEqual(session.total_break_seconds, 600)

    def test_reset_turns_returns_session_turn_count_to_zero(self) -> None:
        session = WorkSession(
            "Project",
            Decimal("42.50"),
            datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
        ).next_turn().next_turn()

        reset = session.reset_turns()

        self.assertEqual(reset.turn_count, 0)
        self.assertEqual(session.turn_count, 2)

    def test_turn_count_can_be_set_to_manual_value(self) -> None:
        session = WorkSession(
            "Project",
            Decimal("42.50"),
            datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
        ).next_turn().set_turn_count(7)

        self.assertEqual(session.turn_count, 7)
        with self.assertRaises(ValueError):
            session.set_turn_count(-1)

    def test_rejects_break_longer_than_session(self) -> None:
        session = WorkSession(
            "Project",
            Decimal("42.50"),
            datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
        )

        with self.assertRaises(ValueError):
            session.finish(
                datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
                total_break_seconds=3601,
            )

    def test_break_state_survives_serialization_and_counts_elapsed_break(self) -> None:
        started_at = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
        break_started_at = datetime(2026, 1, 2, 9, 20, tzinfo=timezone.utc)
        session = WorkSession(
            "Project",
            Decimal("42.50"),
            started_at,
            client_name="Client",
        ).start_break(break_started_at)

        restored = WorkSession.from_dict(session.to_dict())

        self.assertEqual(restored.session_id, session.session_id)
        self.assertEqual(restored.break_started_at, break_started_at)
        self.assertEqual(
            restored.worked_seconds_at(
                datetime(2026, 1, 2, 9, 30, tzinfo=timezone.utc)
            ),
            20 * 60,
        )

    def test_pay_uses_actual_whole_minutes_without_rounding_up(self) -> None:
        session = WorkSession(
            "Project",
            Decimal("60"),
            datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
        ).finish(datetime(2026, 1, 2, 9, 1, 59, tzinfo=timezone.utc))

        self.assertEqual(session.total_hours_and_minutes, (0, 1))
        self.assertEqual(session.total_pay, Decimal("1.00"))

    def test_rejects_end_before_start(self) -> None:
        session = WorkSession(
            "Project",
            Decimal("42.50"),
            datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
        )

        with self.assertRaises(ValueError):
            session.finish(datetime(2026, 1, 2, 8, 59, tzinfo=timezone.utc))


class JsonStorageTests(unittest.TestCase):
    def test_session_ending_in_next_month_is_moved_to_end_month_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonStorage(Path(directory))
            started_at = datetime(2026, 1, 31, 23, 30, tzinfo=timezone.utc)
            ended_at = datetime(2026, 2, 1, 0, 30, tzinfo=timezone.utc)
            session = WorkSession(
                "Month-end project",
                Decimal("60"),
                started_at,
                client_name="Client",
            )
            storage.start_session(session)

            storage.save_session(session.finish(ended_at))

            january_file = Path(directory) / "months" / "2026-01.json"
            february_file = Path(directory) / "months" / "2026-02.json"
            january_data = json.loads(january_file.read_text(encoding="utf-8"))
            february_data = json.loads(february_file.read_text(encoding="utf-8"))
            self.assertEqual(january_data["sessions"], [])
            self.assertEqual(len(february_data["sessions"]), 1)
            self.assertEqual(
                february_data["sessions"][0]["ended_at"], ended_at.isoformat()
            )

    def test_incomplete_session_is_saved_recovered_and_updated_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonStorage(Path(directory))
            started_at = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
            session = WorkSession(
                "Project",
                Decimal("42.50"),
                started_at,
                client_name="Client",
            )
            storage.start_session(session)

            monthly_file = Path(directory) / "months" / "2026-01.json"
            saved_entry = json.loads(monthly_file.read_text(encoding="utf-8"))[
                "sessions"
            ][0]
            self.assertIsNone(saved_entry["ended_at"])
            self.assertIsNone(saved_entry["total_pay"])

            recovered = JsonStorage(Path(directory)).load_in_progress_session()
            self.assertEqual(recovered, session)
            self.assertEqual(len(storage.load_sessions(started_at)), 1)

            break_started_at = datetime(2026, 1, 2, 9, 30, tzinfo=timezone.utc)
            on_break = recovered.start_break(break_started_at)
            storage.update_session(on_break)
            restarted = JsonStorage(Path(directory)).load_in_progress_session()
            self.assertEqual(restarted.break_started_at, break_started_at)

            finished = restarted.end_break(
                datetime(2026, 1, 2, 9, 40, tzinfo=timezone.utc)
            ).finish(datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc))
            storage.save_session(finished)
            records = storage.load_sessions(started_at)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0], finished)
            self.assertIsNone(storage.load_in_progress_session())

    def test_refuses_to_start_second_incomplete_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonStorage(Path(directory))
            started_at = datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
            storage.start_session(WorkSession("First", Decimal("20"), started_at))

            with self.assertRaises(ValueError):
                storage.start_session(
                    WorkSession("Second", Decimal("20"), started_at)
                )

    def test_discard_removes_in_progress_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonStorage(Path(directory))
            session = WorkSession(
                "Project",
                Decimal("42.50"),
                datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
            )
            storage.start_session(session)

            storage.delete_session(session)

            self.assertEqual(storage.load_sessions(session.started_at), [])
            self.assertIsNone(storage.load_in_progress_session())

    def test_projects_and_completed_sessions_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonStorage(Path(directory))
            storage.save_project(Project("Project", Decimal("42.50"), "Client"))
            session = WorkSession(
                "Project",
                Decimal("42.50"),
                datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
                client_name="Client",
            ).next_turn().finish(
                datetime(2026, 1, 2, 10, 30, tzinfo=timezone.utc),
                total_break_seconds=300,
            )
            storage.save_session(session)

            self.assertEqual(
                storage.load_projects(),
                [Project("Project", Decimal("42.50"), "Client")],
            )
            self.assertEqual(storage.load_clients(), ["Client"])
            month_file = Path(directory) / "months" / "2026-01.json"
            saved = json.loads(month_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["sessions"][0]["client_name"], "Client")
            self.assertEqual(saved["sessions"][0]["turn_count"], 1)
            self.assertEqual(saved["sessions"][0]["total_hours"], 1)
            self.assertEqual(saved["sessions"][0]["total_minutes"], 25)
            self.assertEqual(saved["sessions"][0]["total_break_seconds"], 300)
            self.assertEqual(saved["sessions"][0]["total_pay"], "60.21")
            self.assertEqual(storage.load_sessions(session.started_at), [session])

    def test_does_not_save_an_incomplete_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonStorage(Path(directory))
            session = WorkSession(
                "Project",
                Decimal("42.50"),
                datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc),
            )

            with self.assertRaises(ValueError):
                storage.save_session(session)

    def test_existing_files_without_client_fields_remain_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonStorage(Path(directory))
            storage.projects_path.write_text(
                '{"projects": [{"name": "Legacy", "hourly_rate": "20"}]}',
                encoding="utf-8",
            )

            self.assertEqual(storage.load_clients(), [])
            self.assertEqual(
                storage.load_projects(),
                [Project("Legacy", Decimal("20"))],
            )

    def test_project_requires_client_when_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonStorage(Path(directory))

            with self.assertRaises(ValueError):
                storage.save_project(Project("Project", Decimal("42.50")))


if __name__ == "__main__":
    unittest.main()
