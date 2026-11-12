from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.client import FakeLLM
from app.ai.responder import AIResponder
from app.clock import FixedClock
from app.db.models import Handoff


def say(client: TestClient, text: str, contact: str) -> dict:
    return client.post("/api/simulator/messages", json={"contact": contact, "text": text}).json()


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    assert (
        client.post("/api/admin/login", json={"username": "admin", "password": "admin"})
    ).status_code == 200
    return client


def metrics(client: TestClient, days: int = 7) -> dict:
    response = client.get("/api/admin/metrics", params={"days": days})
    assert response.status_code == 200
    return response.json()


def test_metrics_require_login(client: TestClient) -> None:
    assert client.get("/api/admin/metrics").status_code == 401


def test_empty_metrics(admin: TestClient) -> None:
    m = metrics(admin)
    assert m["conversations"] == 0
    assert m["resolved_rate"] is None
    assert m["median_minutes_to_human"] is None


def test_metrics_summary(admin: TestClient, clock: FixedClock) -> None:
    # Resolvida pelo bot: agenda sozinha.
    for text in ["agendar cardio", "segunda", "10h", "Maria Silva", "sim"]:
        say(admin, text, "5500900000001")
    # Só pergunta, também resolvida.
    say(admin, "qual o endereço?", "5500900000002")
    # Pede atendente e é atendida 12 minutos depois.
    say(admin, "atendente", "5500900000003")
    # Urgência ainda sem resposta.
    say(admin, "é urgente", "5500900000004")

    clock.advance(timedelta(minutes=12))
    queue = admin.get("/api/admin/conversations", params={"status": "waiting_human"}).json()
    normal = next(c for c in queue if c["contact"] == "5500900000003")
    admin.post(f"/api/admin/conversations/{normal['id']}/reply", json={"text": "Olá!"})
    # Cancelamento pela equipe.
    [appointment] = admin.get("/api/admin/appointments").json()
    admin.post(f"/api/admin/appointments/{appointment['id']}/cancel")

    m = metrics(admin)
    assert m["conversations"] == 4
    assert m["resolved_by_bot"] == 2
    assert m["resolved_rate"] == 0.5
    assert m["handoffs"] == 2
    assert m["urgent_handoffs"] == 1
    assert m["handoffs_answered"] == 1
    assert m["median_minutes_to_human"] == 12.0
    assert m["appointments_by_bot"] == 1
    assert m["appointments_by_staff"] == 0
    assert m["cancellations"] == 1


def test_urgency_while_waiting_upgrades_handoff(admin: TestClient) -> None:
    say(admin, "atendente", "5500900000005")
    say(admin, "socorro, é urgente", "5500900000005")
    with admin.app.state.session_factory() as session:  # type: ignore[attr-defined]
        [handoff] = session.scalars(select(Handoff)).all()
        assert handoff.priority == "urgent"
    assert metrics(admin)["urgent_handoffs"] == 1


def test_only_first_agent_reply_counts(admin: TestClient, clock: FixedClock) -> None:
    say(admin, "atendente", "5500900000006")
    [item] = admin.get("/api/admin/conversations", params={"status": "waiting_human"}).json()
    clock.advance(timedelta(minutes=3))
    admin.post(f"/api/admin/conversations/{item['id']}/reply", json={"text": "Oi"})
    clock.advance(timedelta(minutes=30))
    admin.post(f"/api/admin/conversations/{item['id']}/reply", json={"text": "Mais algo?"})
    assert metrics(admin)["median_minutes_to_human"] == 3.0


def test_period_filter(admin: TestClient, clock: FixedClock) -> None:
    say(admin, "oi", "5500900000007")
    clock.advance(timedelta(days=3))
    assert metrics(admin, days=1)["conversations"] == 0
    assert metrics(admin, days=7)["conversations"] == 1
    assert admin.get("/api/admin/metrics", params={"days": 0}).status_code == 422


def test_ai_usage(admin: TestClient) -> None:
    engine = admin.app.state.conversation_engine  # type: ignore[attr-defined]
    engine.ai = AIResponder(FakeLLM("Sim, atendemos."), engine.clinic, engine.clock)
    say(admin, "oi", "5500900000008")
    say(admin, "vocês atendem crianças?", "5500900000008")
    m = metrics(admin)
    assert m["ai_answered"] == 1
    assert m["ai_median_latency_ms"] is not None
