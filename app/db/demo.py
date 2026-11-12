"""Dados de demonstração: consultas de exemplo para a agenda não começar vazia."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clinic import ClinicData
from app.db.models import Appointment, Channel
from app.scheduling import Scheduler, SchedulingError

# Telefones claramente fictícios (DDD 00).
DEMO_PATIENTS = (
    ("5500900000001", "Carla Mendes"),
    ("5500900000002", "Roberto Alves"),
    ("5500900000003", "Juliana Costa"),
    ("5500900000004", "Marcos Ribeiro"),
    ("5500900000005", "Patrícia Gomes"),
)


def seed_demo_appointments(session: Session, clinic: ClinicData, scheduler: Scheduler) -> int:
    """Só roda com a agenda vazia (primeiro start). Usa a mesma agenda do bot, então as
    consultas respeitam todas as regras do banco. Devolve quantas foram criadas."""
    if session.scalar(select(func.count(Appointment.id))):
        return 0

    created = 0
    for i, info in enumerate(clinic.specialties):
        specialty = scheduler.get_specialty(session, info.slug)
        for j, day in enumerate(scheduler.available_days(session, specialty, limit=4)[1:]):
            slots = scheduler.available_slots(session, specialty, day)
            phone, name = DEMO_PATIENTS[(i + j) % len(DEMO_PATIENTS)]
            try:
                scheduler.book(
                    session,
                    specialty=specialty,
                    starts_at=slots[(i * 3 + j * 5) % len(slots)],
                    patient_phone=phone,
                    patient_name=name,
                    channel=Channel.ADMIN,
                )
            except SchedulingError:
                continue
            created += 1
    return created
