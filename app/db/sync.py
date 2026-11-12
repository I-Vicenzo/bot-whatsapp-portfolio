"""Sincroniza o YAML da clínica com as tabelas que o banco usa para validar agendamentos."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.clinic import ClinicData
from app.db.models import BusinessHour, ScheduleSettings, Specialty


def sync_clinic(session: Session, clinic: ClinicData) -> None:
    """Idempotente. Especialidades removidas do YAML ficam inativas (consultas antigas
    continuam apontando para elas)."""
    existing = {s.slug: s for s in session.scalars(select(Specialty))}
    for info in clinic.specialties:
        row = existing.pop(info.slug, None) or Specialty(slug=info.slug)
        row.name = info.name
        row.professional = info.professional
        row.price_cents = info.price_cents
        row.active = True
        session.add(row)
    for removed in existing.values():
        removed.active = False

    session.execute(delete(BusinessHour))
    session.add_all(
        BusinessHour(weekday=weekday, open_min=r.open_min, close_min=r.close_min)
        for weekday in range(7)
        for r in clinic.business_hours.for_weekday(weekday)
    )

    settings = session.get(ScheduleSettings, 1) or ScheduleSettings(id=1)
    settings.min_advance_minutes = clinic.scheduling.min_advance_minutes
    session.add(settings)
    session.flush()
