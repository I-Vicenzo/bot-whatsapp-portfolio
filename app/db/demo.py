"""Dados de demonstração: agenda e painel não começam vazios."""

from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clinic import ClinicData
from app.db.models import Appointment, Channel, Conversation
from app.scheduling import Scheduler, SchedulingError

if TYPE_CHECKING:
    from app.conversation.service import ConversationService

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


DEMO_CONVERSATIONS = (
    ("5500900000101", "Fernanda Lopes", ("Oi", "Queria falar com um atendente, por favor")),
    (
        "5500900000102",
        "Paulo Henrique",
        ("Boa tarde", "É urgente, minha filha está com febre alta desde ontem"),
    ),
    ("5500900000103", "Luciana Prado", ("Olá", "Quanto custa a consulta de dermatologia?")),
)


def seed_demo_conversations(service: ConversationService) -> int:
    """Conversas de exemplo no simulador (uma na fila, uma urgente), só com o banco vazio.
    Passam pelo mesmo serviço do bot, então estado e fila ficam coerentes."""
    with service.session_factory() as session:
        if session.scalar(select(func.count(Conversation.id))):
            return 0
    for contact, name, messages in DEMO_CONVERSATIONS:
        for text in messages:
            service.receive(Channel.SIMULATOR, contact, text, display_name=name)
    return len(DEMO_CONVERSATIONS)
