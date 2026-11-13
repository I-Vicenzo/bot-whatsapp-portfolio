import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.reminders import ReminderSettings, reminder_loop, send_due_reminders
from app.clock import FixedClock
from app.config import Settings
from app.db.models import Appointment, Channel
from app.main import create_app
from app.scheduling import Scheduler
from tests.conftest import CLINIC_FILE

SEND_URL = "https://graph.facebook.com/v26.0/99/messages"
SETTINGS = ReminderSettings(template="lembrete_consulta")


def client() -> WhatsAppClient:
    return WhatsAppClient(
        token="t", phone_number_id="99", api_version="v26.0", sleep=lambda _s: None
    )


def book(
    scheduler: Scheduler,
    session_factory: sessionmaker[Session],
    starts_at: datetime,
    phone: str,
    channel: Channel = Channel.WHATSAPP,
    *,
    booked_ahead: timedelta = timedelta(days=3),
) -> int:
    """Marca a consulta; `booked_ahead` diz com quanta antecedência ela foi marcada."""
    with session_factory.begin() as session:
        appointment = scheduler.book(
            session,
            specialty=scheduler.get_specialty(session, "cardiologia"),
            starts_at=starts_at,
            patient_phone=phone,
            patient_name="Maria Silva",
            channel=channel,
        )
        appointment.created_at = starts_at - booked_ahead
        return appointment.id


@respx.mock
def test_sends_template_once_for_due_whatsapp_appointments(
    scheduler: Scheduler, session_factory: sessionmaker[Session], clock: FixedClock
) -> None:
    route = respx.post(SEND_URL).mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "w"}]})
    )
    due = book(scheduler, session_factory, datetime(2026, 11, 13, 8, 30), "5547999990001")
    book(scheduler, session_factory, datetime(2026, 11, 13, 10, 0), "5547999990002")  # > 24 h
    book(
        scheduler,
        session_factory,
        datetime(2026, 11, 13, 8, 0),
        "5547999990003",
        channel=Channel.SIMULATOR,
    )

    assert send_due_reminders(session_factory, client(), clock, SETTINGS) == 1
    body = json.loads(route.calls[0].request.content)
    assert body["to"] == "5547999990001"
    assert body["type"] == "template"
    template = body["template"]
    assert template["name"] == "lembrete_consulta"
    assert template["language"] == {"code": "pt_BR"}
    assert [p["text"] for p in template["components"][0]["parameters"]] == [
        "Maria",
        "Cardiologia",
        "sexta-feira, 13/11",
        "08:30",
    ]
    with session_factory() as session:
        assert session.get(Appointment, due).reminder_sent_at == datetime(2026, 11, 12, 9, 0)  # type: ignore[union-attr]

    # Não repete; e a consulta das 10:00 entra quando ficar a menos de 24 h.
    assert send_due_reminders(session_factory, client(), clock, SETTINGS) == 0
    clock.advance(timedelta(hours=1))
    assert send_due_reminders(session_factory, client(), clock, SETTINGS) == 1


@respx.mock
def test_temporary_failure_is_retried_next_cycle(
    scheduler: Scheduler, session_factory: sessionmaker[Session], clock: FixedClock
) -> None:
    respx.post(SEND_URL).mock(
        side_effect=[
            *[httpx.Response(503)] * 4,  # 1 tentativa + 3 retries
            httpx.Response(200, json={"messages": [{"id": "w"}]}),
        ]
    )
    book(scheduler, session_factory, datetime(2026, 11, 13, 8, 30), "5547999990001")
    assert send_due_reminders(session_factory, client(), clock, SETTINGS) == 0
    assert send_due_reminders(session_factory, client(), clock, SETTINGS) == 1


@respx.mock
def test_permanent_refusal_is_not_retried(
    scheduler: Scheduler, session_factory: sessionmaker[Session], clock: FixedClock
) -> None:
    """Template não aprovado (400): antes, era reenviado a cada ciclo para sempre."""
    route = respx.post(SEND_URL).mock(
        return_value=httpx.Response(400, json={"error": {"code": 132001}})
    )
    appointment_id = book(
        scheduler, session_factory, datetime(2026, 11, 13, 8, 30), "5547999990001"
    )
    assert send_due_reminders(session_factory, client(), clock, SETTINGS) == 0
    assert send_due_reminders(session_factory, client(), clock, SETTINGS) == 0
    assert route.call_count == 1
    with session_factory() as session:
        appointment = session.get(Appointment, appointment_id)
        assert appointment is not None
        assert appointment.reminder_sent_at is None
        assert appointment.reminder_failed_at == datetime(2026, 11, 12, 9, 0)


@respx.mock
def test_appointment_booked_inside_the_window_gets_no_reminder(
    scheduler: Scheduler, session_factory: sessionmaker[Session], clock: FixedClock
) -> None:
    """Marcada hoje para amanhã: o cliente acabou de receber a confirmação."""
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(200, json={}))
    book(
        scheduler,
        session_factory,
        datetime(2026, 11, 13, 8, 30),
        "5547999990001",
        booked_ahead=timedelta(hours=20),
    )
    assert send_due_reminders(session_factory, client(), clock, SETTINGS) == 0
    assert not route.called


@respx.mock
def test_cancelled_appointments_are_not_reminded(
    scheduler: Scheduler, session_factory: sessionmaker[Session], clock: FixedClock
) -> None:
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(200, json={}))
    appointment_id = book(
        scheduler, session_factory, datetime(2026, 11, 13, 8, 30), "5547999990001"
    )
    with session_factory.begin() as session:
        scheduler.cancel(session, appointment_id, "5547999990001", channel=Channel.WHATSAPP)
    assert send_due_reminders(session_factory, client(), clock, SETTINGS) == 0
    assert not route.called


def test_loop_survives_errors(
    session_factory: sessionmaker[Session], clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def flaky(*_args: object) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("falha simulada")
        raise asyncio.CancelledError

    monkeypatch.setattr("app.channels.whatsapp.reminders.send_due_reminders", flaky)
    fast = ReminderSettings(template="t", interval_seconds=0)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(reminder_loop(session_factory, client(), clock, fast))
    assert len(calls) == 2


def test_app_starts_and_stops_reminder_task(tmp_path: Path, clock: FixedClock) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        clinic_data_path=CLINIC_FILE,
        demo_seed=False,
        whatsapp_token="t",
        whatsapp_phone_number_id="99",
        whatsapp_app_secret="s",
        whatsapp_verify_token="v",
        reminders_enabled=True,
    )
    with TestClient(create_app(settings, clock=clock)) as test_client:
        assert test_client.get("/health").status_code == 200
