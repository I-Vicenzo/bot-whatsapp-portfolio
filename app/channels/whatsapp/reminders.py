"""Lembrete de consulta pelo WhatsApp.

Fora da janela de 24 h a Meta só aceita mensagens de template aprovado, então o lembrete usa um
template (categoria "utilidade") com quatro variáveis: nome, especialidade, data e horário.
Sugestão de texto para cadastrar na Meta, em docs/whatsapp-setup.md.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    `hours_before` horas e ainda não foram lembradas. Devolve quantos foram enviados.

    Consultas marcadas já dentro dessa janela não recebem lembrete: o cliente acabou de receber
    a confirmação. Cada envio roda fora de transação (no SQLite, uma transação aberta trava o
    banco inteiro durante os retries); só a marcação de enviado ou de falha grava no banco.
    Recusa definitiva da Meta (4xx) marca `reminder_failed_at` e não é repetida."""
    window = timedelta(hours=settings.hours_before)
    now = clock.local_now()
    with session_factory() as session:
        due = [
            (a.id, a.patient_phone, _parameters(a))
            for a in session.scalars(
                select(Appointment)
                .where(
                    Appointment.status == AppointmentStatus.CONFIRMED,
                    Appointment.channel == Channel.WHATSAPP,
                    Appointment.reminder_sent_at.is_(None),
                    Appointment.reminder_failed_at.is_(None),
                    Appointment.starts_at > now,
                    Appointment.starts_at <= now + window,
                )
                .order_by(Appointment.starts_at)
            )
            if a.created_at < a.starts_at - window
        ]

    sent = 0
    for appointment_id, phone, parameters in due:
        try:
            client.send_template(phone, settings.template, settings.language, parameters)
        except WhatsAppError as exc:
            if not exc.permanent:
                logger.warning(
                    "Lembrete não enviado para %s; nova tentativa no próximo ciclo",
                    mask_phone(phone),
                )
                continue
            logger.error(
                "Lembrete recusado pela Meta para %s (%s); não será repetido",
                mask_phone(phone),
                exc,
            )
            _mark(session_factory, appointment_id, failed_at=clock.local_now())
            continue
        _mark(session_factory, appointment_id, sent_at=clock.local_now())
        sent += 1
    return sent


def _parameters(appointment: Appointment) -> list[str]:
    return [
        appointment.patient_name.split(" ")[0],
        appointment.specialty.name,
        texts.fmt_date(appointment.starts_at.date()),
        texts.fmt_time(appointment.starts_at),
    ]


def _mark(
    session_factory: sessionmaker[Session],
    appointment_id: int,
    *,
    sent_at: datetime | None = None,
    failed_at: datetime | None = None,
) -> None:
    with session_factory.begin() as session:
        appointment = session.get(Appointment, appointment_id)
        if appointment is None:  # pragma: no cover - consultas não são apagadas
            return
        if sent_at is not None:
            appointment.reminder_sent_at = sent_at
        if failed_at is not None:
            appointment.reminder_failed_at = failed_at


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
