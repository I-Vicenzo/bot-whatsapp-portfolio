"""Agenda: horários livres, agendar, consultar e cancelar.

O cálculo de horários livres é a validação "amigável" do código. Em `book()` o código não
repete as checagens que o banco faz: insere e traduz a recusa do banco em erro de domínio. Assim
o caminho de produção é o mesmo que protege contra dois clientes disputando o mesmo horário.
"""

from collections.abc import Iterator
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clinic import ClinicData
from app.clinic.models import SLOT_MINUTES
from app.clock import Clock
from app.db.models import Appointment, AppointmentStatus, Channel, Specialty
from app.scheduling.errors import (
    AppointmentNotFound,
    SpecialtyNotFound,
    TooFarAhead,
    translate_integrity_error,
)

SLOT = timedelta(minutes=SLOT_MINUTES)


class Scheduler:
    def __init__(self, clinic: ClinicData, clock: Clock) -> None:
        self.clinic = clinic
        self.clock = clock

    # --- janela e grade -------------------------------------------------------------------

    @property
    def last_bookable_day(self) -> date:
        return self.clock.today() + timedelta(days=self.clinic.scheduling.days_ahead)

    def earliest_start(self) -> datetime:
        return self.clock.local_now() + timedelta(
            minutes=self.clinic.scheduling.min_advance_minutes
        )

    def _grid(self, day: date, specialty: Specialty) -> Iterator[datetime]:
        """Inícios de consulta do dia pelo horário de funcionamento (nada se bloqueado)."""
        if self.clinic.closure_for(day, specialty.slug) is not None:
            return
        for interval in self.clinic.business_hours.for_weekday(day.weekday()):
            start = datetime.combine(day, interval.start)
            end = datetime.combine(day, interval.end)
            while start + SLOT <= end:
                yield start
                start += SLOT

    def is_open(self, day: date) -> bool:
        return bool(self.clinic.business_hours.for_weekday(day.weekday()))

    # --- consultas ------------------------------------------------------------------------

    def get_specialty(self, session: Session, slug: str) -> Specialty:
        specialty = session.scalar(
            select(Specialty).where(Specialty.slug == slug, Specialty.active.is_(True))
        )
        if specialty is None:
            raise SpecialtyNotFound(slug)
        return specialty

    def _taken(
        self, session: Session, specialty: Specialty, first: date, last: date
    ) -> set[datetime]:
        return set(
            session.scalars(
                select(Appointment.starts_at).where(
                    Appointment.specialty_id == specialty.id,
                    Appointment.status == AppointmentStatus.CONFIRMED,
                    Appointment.starts_at >= datetime.combine(first, datetime.min.time()),
                    Appointment.starts_at
                    < datetime.combine(last + timedelta(days=1), datetime.min.time()),
                )
            )
        )

    def _free(self, day: date, specialty: Specialty, taken: set[datetime]) -> list[datetime]:
        earliest = self.earliest_start()
        return [s for s in self._grid(day, specialty) if s >= earliest and s not in taken]

    def available_slots(self, session: Session, specialty: Specialty, day: date) -> list[datetime]:
        if not self.clock.today() <= day <= self.last_bookable_day:
            return []
        return self._free(day, specialty, self._taken(session, specialty, day, day))

    def available_days(
        self, session: Session, specialty: Specialty, limit: int | None = None
    ) -> list[date]:
        first, last = self.clock.today(), self.last_bookable_day
        taken = self._taken(session, specialty, first, last)
        days: list[date] = []
        day = first
        while day <= last and (limit is None or len(days) < limit):
            if self._free(day, specialty, taken):
                days.append(day)
            day += timedelta(days=1)
        return days

    def list_upcoming(self, session: Session, phone: str) -> list[Appointment]:
        return list(
            session.scalars(
                select(Appointment)
                .where(
                    Appointment.patient_phone == phone,
                    Appointment.status == AppointmentStatus.CONFIRMED,
                    Appointment.starts_at >= self.clock.local_now(),
                )
                .order_by(Appointment.starts_at)
            )
        )

    # --- escrita --------------------------------------------------------------------------

    def book(
        self,
        session: Session,
        *,
        specialty: Specialty,
        starts_at: datetime,
        patient_phone: str,
        patient_name: str,
        channel: Channel,
    ) -> Appointment:
        """Grava a consulta. Regras de horário são do banco; a recusa vira erro de domínio.

        Usa savepoint: em caso de erro, a transação de quem chamou continua válida.
        """
        if starts_at.date() > self.last_bookable_day:
            raise TooFarAhead(starts_at.isoformat())
        appointment = Appointment(
            specialty=specialty,
            starts_at=starts_at,
            patient_phone=patient_phone,
            patient_name=patient_name.strip(),
            channel=channel,
            status=AppointmentStatus.CONFIRMED,
            created_at=self.clock.local_now(),
        )
        try:
            with session.begin_nested():
                session.add(appointment)
        except IntegrityError as exc:
            error = translate_integrity_error(exc)
            if error is None:
                raise
            raise error from exc
        return appointment

    def cancel_by_staff(self, session: Session, appointment_id: int) -> Appointment:
        """Cancelamento pelo painel: qualquer consulta confirmada e futura."""
        appointment = session.scalar(
            select(Appointment).where(
                Appointment.id == appointment_id,
                Appointment.status == AppointmentStatus.CONFIRMED,
                Appointment.starts_at >= self.clock.local_now(),
            )
        )
        if appointment is None:
            raise AppointmentNotFound(str(appointment_id))
        return self._mark_cancelled(session, appointment)

    def _mark_cancelled(self, session: Session, appointment: Appointment) -> Appointment:
        appointment.status = AppointmentStatus.CANCELLED
        appointment.cancelled_at = self.clock.local_now()
        session.flush()
        return appointment

    def cancel(self, session: Session, appointment_id: int, phone: str) -> Appointment:
        """Cancela uma consulta futura do próprio telefone. Cancelar libera o horário."""
        appointment = session.scalar(
            select(Appointment).where(
                Appointment.id == appointment_id,
                Appointment.patient_phone == phone,
                Appointment.status == AppointmentStatus.CONFIRMED,
                Appointment.starts_at >= self.clock.local_now(),
            )
        )
        if appointment is None:
            raise AppointmentNotFound(str(appointment_id))
        return self._mark_cancelled(session, appointment)
