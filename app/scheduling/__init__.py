"""Agenda da clínica."""

from app.scheduling.errors import (
    AppointmentNotFound,
    BlockedDay,
    InvalidSlot,
    OutsideBusinessHours,
    PatientAlreadyBooked,
    SchedulingError,
    SlotTaken,
    SpecialtyNotFound,
    TooFarAhead,
    TooSoon,
)
from app.scheduling.service import Scheduler

__all__ = [
    "AppointmentNotFound",
    "BlockedDay",
    "InvalidSlot",
    "OutsideBusinessHours",
    "PatientAlreadyBooked",
    "Scheduler",
    "SchedulingError",
    "SlotTaken",
    "SpecialtyNotFound",
    "TooFarAhead",
    "TooSoon",
]
