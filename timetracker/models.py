"""Domain models for projects and completed work sessions."""

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import uuid4


def parse_hourly_rate(value: str | Decimal) -> Decimal:
    """Parse and validate a non-negative hourly rate."""
    try:
        rate = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Hourly rate must be a number.") from exc
    if not rate.is_finite() or rate < 0:
        raise ValueError("Hourly rate must be a finite, non-negative number.")
    return rate


@dataclass(frozen=True)
class Project:
    name: str
    hourly_rate: Decimal
    client_name: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Project name cannot be empty.")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "hourly_rate", parse_hourly_rate(self.hourly_rate))
        object.__setattr__(self, "client_name", self.client_name.strip())

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "hourly_rate": str(self.hourly_rate),
            "client_name": self.client_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        return cls(
            name=data["name"],
            hourly_rate=parse_hourly_rate(data["hourly_rate"]),
            client_name=data.get("client_name", ""),
        )


@dataclass(frozen=True)
class WorkSession:
    project_name: str
    hourly_rate: Decimal
    started_at: datetime
    ended_at: datetime | None = None
    turn_count: int = 0
    client_name: str = ""
    total_break_seconds: int = 0
    break_started_at: datetime | None = None
    session_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not self.project_name.strip():
            raise ValueError("Project name cannot be empty.")
        object.__setattr__(self, "project_name", self.project_name.strip())
        object.__setattr__(self, "hourly_rate", parse_hourly_rate(self.hourly_rate))
        object.__setattr__(self, "client_name", self.client_name.strip())
        if self.turn_count < 0:
            raise ValueError("Turn count cannot be negative.")
        if self.total_break_seconds < 0:
            raise ValueError("Total break time cannot be negative.")
        if self.break_started_at is not None and self.ended_at is not None:
            raise ValueError("A completed session cannot have an active break.")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("End time cannot be before start time.")
        if (
            self.ended_at is not None
            and self.total_break_seconds > self.elapsed_seconds
        ):
            raise ValueError("Total break time cannot exceed session duration.")

    @property
    def is_complete(self) -> bool:
        return self.ended_at is not None

    @property
    def total_hours_and_minutes(self) -> tuple[int, int]:
        if self.ended_at is None:
            raise ValueError("An unfinished session has no total duration.")
        total_minutes = self.billable_seconds // 60
        return divmod(total_minutes, 60)

    @property
    def elapsed_seconds(self) -> int:
        if self.ended_at is None:
            raise ValueError("An unfinished session has no total duration.")
        return int((self.ended_at - self.started_at).total_seconds())

    def elapsed_seconds_at(self, moment: datetime) -> int:
        return max(0, int((moment - self.started_at).total_seconds()))

    def break_seconds_at(self, moment: datetime) -> int:
        total = self.total_break_seconds
        if self.break_started_at is not None:
            total += max(0, int((moment - self.break_started_at).total_seconds()))
        return total

    def worked_seconds_at(self, moment: datetime) -> int:
        return max(0, self.elapsed_seconds_at(moment) - self.break_seconds_at(moment))

    @property
    def billable_seconds(self) -> int:
        if self.ended_at is None:
            raise ValueError("An unfinished session has no total duration.")
        return self.elapsed_seconds - self.total_break_seconds

    @property
    def total_pay(self) -> Decimal:
        if self.ended_at is None:
            raise ValueError("An unfinished session has no total pay.")
        billable_minutes = self.billable_seconds // 60
        amount = self.hourly_rate * Decimal(billable_minutes) / Decimal(60)
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def next_turn(self) -> "WorkSession":
        return replace(self, turn_count=self.turn_count + 1)

    def set_turn_count(self, turn_count: int) -> "WorkSession":
        return replace(self, turn_count=turn_count)

    def reset_turns(self) -> "WorkSession":
        return replace(self, turn_count=0)

    def start_break(self, started_at: datetime) -> "WorkSession":
        if self.is_complete:
            raise ValueError("A completed session cannot start a break.")
        if self.break_started_at is not None:
            raise ValueError("A break is already in progress.")
        return replace(self, break_started_at=started_at)

    def end_break(self, ended_at: datetime) -> "WorkSession":
        if self.break_started_at is None:
            raise ValueError("There is no active break to end.")
        break_seconds = int((ended_at - self.break_started_at).total_seconds())
        if break_seconds < 0:
            raise ValueError("Break end time cannot be before its start.")
        return replace(
            self,
            total_break_seconds=self.total_break_seconds + break_seconds,
            break_started_at=None,
        )

    def finish(
        self, ended_at: datetime, total_break_seconds: int = 0
    ) -> "WorkSession":
        if self.is_complete:
            raise ValueError("Session is already complete.")
        return replace(
            self,
            ended_at=ended_at,
            total_break_seconds=total_break_seconds,
            break_started_at=None,
        )

    def to_dict(self) -> dict[str, Any]:
        if self.is_complete:
            total_hours, total_minutes = self.total_hours_and_minutes
            total_pay: str | None = str(self.total_pay)
        else:
            total_hours = None
            total_minutes = None
            total_pay = None
        return {
            "session_id": self.session_id,
            "project_name": self.project_name,
            "client_name": self.client_name,
            "hourly_rate": str(self.hourly_rate),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "break_started_at": (
                self.break_started_at.isoformat() if self.break_started_at else None
            ),
            "total_hours": total_hours,
            "total_minutes": total_minutes,
            "turn_count": self.turn_count,
            "total_break_seconds": self.total_break_seconds,
            "total_pay": total_pay,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkSession":
        ended_at = data["ended_at"]
        return cls(
            project_name=data["project_name"],
            hourly_rate=parse_hourly_rate(data["hourly_rate"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            ended_at=datetime.fromisoformat(ended_at) if ended_at else None,
            turn_count=int(data["turn_count"]),
            client_name=data.get("client_name", ""),
            total_break_seconds=int(data.get("total_break_seconds", 0)),
            break_started_at=(
                datetime.fromisoformat(data["break_started_at"])
                if data.get("break_started_at")
                else None
            ),
            session_id=data.get("session_id", uuid4().hex),
        )
