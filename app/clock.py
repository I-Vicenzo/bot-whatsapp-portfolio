"""Relógio injetável no fuso da clínica.

Convenção do projeto: todos os horários gravados no banco são "horário de parede" da clínica,
sem offset (naive). O relógio é a única fonte de "agora", tanto para o código quanto para a
função SQL `clinic_now()` usada pelos triggers.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, tzinfo

DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class Clock(ABC):
    def __init__(self, tz: tzinfo) -> None:
        self.tz = tz

    @abstractmethod
    def now(self) -> datetime:
        """Agora, com fuso."""

    def local_now(self) -> datetime:
        """Agora no fuso da clínica, sem offset e sem microssegundos (formato do banco)."""
        return self.now().astimezone(self.tz).replace(tzinfo=None, microsecond=0)

    def today(self) -> date:
        return self.local_now().date()


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(self.tz)


class FixedClock(Clock):
    """Relógio controlável para testes."""

    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("FixedClock exige datetime com fuso")
        super().__init__(now.tzinfo)
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        if now.tzinfo is None:
            raise ValueError("FixedClock exige datetime com fuso")
        self._now = now

    def advance(self, delta: timedelta) -> None:
        self._now += delta
