"""Lembrete de consulta pelo WhatsApp.

Fora da janela de 24 h a Meta só aceita mensagens de template aprovado, então o lembrete usa um
template (categoria "utilidade") com quatro variáveis: nome, especialidade, data e horário.
Sugestão de texto para cadastrar na Meta, em docs/whatsapp-setup.md.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.channels.whatsapp.client import WhatsAppClient, WhatsAppError
from app.clock import Clock
from app.conversation import texts
from app.db.models import Appointment, AppointmentStatus, Channel
from app.logging_utils import mask_phone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReminderSettings:
    template: str
    language: str = "pt_BR"
    hours_before: int = 24
    interval_seconds: int = 300


def send_due_reminders(
    session_factory: sessionmaker[Session],
    client: WhatsAppClient,
    clock: Clock,
    settings: ReminderSettings,
) -> int:
    """Envia lembretes de consultas confirmadas pelo WhatsApp que começam nas próximas
    `hours_before` horas e ainda não foram lembradas. Devolve quantos foram enviados."""
    now = clock.local_now()
    sent = 0
    with session_factory() as session:
        due = session.scalars(
            select(Appointment)
            .where(
                Appointment.status == AppointmentStatus.CONFIRMED,
                Appointment.channel == Channel.WHATSAPP,
                Appointment.reminder_sent_at.is_(None),
                Appointment.starts_at > now,
                Appointment.starts_at <= now + timedelta(hours=settings.hours_before),
            )
            .order_by(Appointment.starts_at)
        ).all()
        for appointment in due:
            parameters = [
                appointment.patient_name.split(" ")[0],
                appointment.specialty.name,
                texts.fmt_date(appointment.starts_at.date()),
                texts.fmt_time(appointment.starts_at),
            ]
            try:
                client.send_template(
                    appointment.patient_phone, settings.template, settings.language, parameters
                )
            except WhatsAppError:
                logger.warning(
                    "Lembrete não enviado para %s; nova tentativa no próximo ciclo",
                    mask_phone(appointment.patient_phone),
                )
                continue
            appointment.reminder_sent_at = clock.local_now()
            session.commit()
            sent += 1
    return sent


async def reminder_loop(
    session_factory: sessionmaker[Session],
    client: WhatsAppClient,
    clock: Clock,
    settings: ReminderSettings,
) -> None:
    """Roda enquanto a aplicação estiver de pé (uma instância só)."""
    while True:
        try:
            sent = await run_in_threadpool(
                send_due_reminders, session_factory, client, clock, settings
            )
            if sent:
                logger.info("Lembretes enviados: %d", sent)
        except Exception:
            logger.exception("Erro no ciclo de lembretes")
        await asyncio.sleep(settings.interval_seconds)
