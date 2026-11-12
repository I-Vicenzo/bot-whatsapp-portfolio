from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import __version__
from app.clock import FixedClock
from app.config import Settings
from app.db.models import Appointment
from app.main import create_app

CONTACT = "5500912345678"


def say(client: TestClient, text: str, contact: str = CONTACT) -> dict:
    response = client.post("/api/simulator/messages", json={"contact": contact, "text": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_simulator_page_and_assets(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "Simulador do assistente" in page.text
    assert client.get("/static/simulator/app.js").status_code == 200
    assert client.get("/static/simulator/style.css").status_code == 200


def test_status_in_demo_mode(client: TestClient) -> None:
    assert client.get("/api/status").json() == {
        "version": __version__,
        "clinic_name": "Clínica Teste",
        "demo_mode": True,
        "whatsapp_enabled": False,
        "ai_enabled": False,
        "fictitious_data": True,
    }


def test_send_returns_structured_replies_and_debug(client: TestClient) -> None:
    body = say(client, "oi")
    [reply] = body["replies"]
    assert "Clínica Teste" in reply["text"]
    assert reply["options"][0] == "Agendar consulta"
    assert reply["numbered"] is True
    assert "*1.*" not in reply["text"]  # lista numerada é coisa do WhatsApp; aqui vira botão
    assert body["debug"] == {
        "state": "MENU",
        "status": "bot",
        "intent": "greeting",
        "source": "rule",
    }
    assert body["last_message_id"] == 2


def test_full_booking_through_api(client: TestClient, app: object) -> None:
    for text in ["agendar cardio", "segunda", "10h", "Maria Silva"]:
        say(client, text)
    body = say(client, "sim")
    assert "Consulta confirmada" in body["replies"][0]["text"]
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        assert session.scalar(select(func.count(Appointment.id))) == 1


def test_yes_no_buttons_send_their_label(client: TestClient) -> None:
    for text in ["agendar cardio", "segunda", "10h"]:
        say(client, text)
    reply = say(client, "Maria Silva")["replies"][-1]
    assert reply["options"] == ["Sim", "Não"]
    assert reply["numbered"] is False
    # O simulador envia o rótulo de opções não numeradas.
    assert "Consulta confirmada" in say(client, reply["options"][0])["replies"][0]["text"]


def test_history_and_polling(client: TestClient) -> None:
    first = say(client, "oi")
    history = client.get("/api/simulator/messages", params={"contact": CONTACT}).json()
    assert [m["author"] for m in history] == ["customer", "bot"]
    assert history[0]["text"] == "oi"
    assert "*1.* Agendar consulta" in history[1]["text"]  # gravado como sairia no WhatsApp

    after = client.get(
        "/api/simulator/messages",
        params={"contact": CONTACT, "after": first["last_message_id"]},
    ).json()
    assert after == []


def test_human_handoff_makes_bot_silent(client: TestClient) -> None:
    say(client, "atendente")
    body = say(client, "alguém aí?")
    assert body["replies"] == []
    assert body["debug"]["source"] == "silent"
    assert body["debug"]["status"] == "waiting_human"


def test_contacts_are_isolated(client: TestClient) -> None:
    say(client, "agendar", contact="5500900000001")
    body = say(client, "2", contact="5500900000002")
    assert body["debug"]["state"] == "MENU"  # "2" no menu = minhas consultas


def test_reset_deletes_conversation(client: TestClient) -> None:
    say(client, "agendar cardio")
    assert client.post("/api/simulator/reset", json={"contact": CONTACT}).status_code == 204
    assert client.get("/api/simulator/messages", params={"contact": CONTACT}).json() == []
    body = say(client, "xyz")
    assert "Clínica Teste" in body["replies"][0]["text"]  # recomeça como primeiro contato


def test_reset_unknown_contact_is_noop(client: TestClient) -> None:
    assert client.post("/api/simulator/reset", json={"contact": CONTACT}).status_code == 204


@pytest.mark.parametrize(
    "payload",
    [
        {"contact": "abc", "text": "oi"},
        {"contact": "123", "text": "oi"},
        {"contact": CONTACT, "text": ""},
        {"contact": CONTACT, "text": "x" * 1001},
        {"contact": CONTACT},
    ],
)
def test_invalid_payloads(client: TestClient, payload: dict) -> None:
    assert client.post("/api/simulator/messages", json=payload).status_code == 422


def test_whitespace_only_message(client: TestClient) -> None:
    response = client.post("/api/simulator/messages", json={"contact": CONTACT, "text": "   "})
    assert response.status_code == 422
    assert response.json()["detail"] == "Mensagem vazia."


def test_poll_validates_contact(client: TestClient) -> None:
    assert client.get("/api/simulator/messages", params={"contact": "x"}).status_code == 422


def test_rate_limit(settings: Settings, clock: FixedClock) -> None:
    limited = settings.model_copy(update={"simulator_rate_limit_per_minute": 2})
    with TestClient(create_app(limited, clock=clock)) as client:
        say(client, "oi")
        say(client, "oi")
        response = client.post("/api/simulator/messages", json={"contact": CONTACT, "text": "oi"})
    assert response.status_code == 429
    assert "Aguarde" in response.json()["detail"]


def test_demo_seed_creates_appointments_once(tmp_path: Path, clock: FixedClock) -> None:
    from tests.conftest import CLINIC_FILE

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'demo.db'}",
        clinic_data_path=CLINIC_FILE,
    )
    counts = []
    for _ in range(2):
        app = create_app(settings, clock=clock)
        with TestClient(app), app.state.session_factory() as session:
            counts.append(session.scalar(select(func.count(Appointment.id))))
    assert counts[0] == counts[1]
    assert counts[0] >= 4
