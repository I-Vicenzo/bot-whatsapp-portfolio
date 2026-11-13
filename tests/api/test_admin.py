import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.channels.whatsapp.signature import sign
from app.clock import FixedClock
from app.config import Settings
from app.main import create_app
from tests.conftest import CLINIC_FILE

SIM_CONTACT = "5500912345678"


def login(client: TestClient, username: str = "admin", password: str = "admin") -> httpx.Response:
    return client.post("/api/admin/login", json={"username": username, "password": password})


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    assert login(client).status_code == 200
    return client


def say(client: TestClient, text: str, contact: str = SIM_CONTACT) -> dict:
    return client.post("/api/simulator/messages", json={"contact": contact, "text": text}).json()


def queue(client: TestClient) -> list[dict]:
    return client.get("/api/admin/conversations", params={"status": "waiting_human"}).json()


# --- autenticação ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/admin/me"),
        ("get", "/api/admin/conversations"),
        ("get", "/api/admin/conversations/1"),
        ("post", "/api/admin/conversations/1/assign"),
        ("post", "/api/admin/conversations/1/reply"),
        ("post", "/api/admin/conversations/1/release"),
        ("get", "/api/admin/appointments"),
        ("post", "/api/admin/appointments/1/cancel"),
        ("get", "/api/admin/specialties"),
    ],
)
def test_routes_require_login(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, json={"text": "x"})
    assert response.status_code == 401


def test_admin_page_is_served(client: TestClient) -> None:
    assert "Painel do atendente" in client.get("/admin").text


def test_login_and_logout(client: TestClient) -> None:
    response = login(client)
    assert response.status_code == 200
    assert response.json() == {"username": "admin", "name": "Atendente"}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "secure" not in cookie  # só em produção
    assert client.get("/api/admin/me").status_code == 200

    assert client.post("/api/admin/logout").status_code == 204
    assert client.get("/api/admin/me").status_code == 401


@pytest.mark.parametrize(("username", "password"), [("admin", "errada"), ("outro", "admin")])
def test_invalid_login(client: TestClient, username: str, password: str) -> None:
    response = login(client, username, password)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_tampered_cookie_is_rejected(client: TestClient) -> None:
    client.cookies.set("admin_session", "eyJ1aWQiOjF9.forjado.assinatura")
    assert client.get("/api/admin/me").status_code == 401


def test_login_attempts_are_limited(client: TestClient) -> None:
    for _ in range(5):
        login(client, password="errada")
    response = login(client)
    assert response.status_code == 429


def test_cross_origin_post_is_blocked(admin: TestClient) -> None:
    response = admin.post(
        "/api/admin/conversations/1/release", headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403


def test_same_origin_post_is_allowed(admin: TestClient) -> None:
    say(admin, "atendente")
    [item] = queue(admin)
    response = admin.post(
        f"/api/admin/conversations/{item['id']}/assign", headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 204


def test_demo_credentials_are_shown_only_with_default_password(
    client: TestClient, settings: Settings, clock: FixedClock
) -> None:
    assert client.get("/api/admin/login-info").json() == {
        "demo_username": "admin",
        "demo_password": "admin",
    }
    custom = Settings(_env_file=None, **{**settings.model_dump(), "admin_password": "outra-senha"})
    with TestClient(create_app(custom, clock=clock)) as other:
        assert other.get("/api/admin/login-info").json()["demo_password"] is None


# --- fila e conversas --------------------------------------------------------------------


def test_queue_puts_urgent_first(admin: TestClient, clock: FixedClock) -> None:
    say(admin, "quero falar com um atendente", contact="5500900000001")
    clock.advance(timedelta(minutes=5))
    say(admin, "oi", contact="5500900000002")  # fica com o bot
    say(admin, "é urgente!", contact="5500900000003")
    clock.advance(timedelta(minutes=2))

    items = queue(admin)
    assert [i["contact"] for i in items] == ["5500900000003", "5500900000001"]
    assert items[0]["priority"] == "urgent"
    assert items[1]["waiting_minutes"] == 7

    everything = admin.get("/api/admin/conversations").json()
    assert {i["contact"] for i in everything} == {"5500900000001", "5500900000002", "5500900000003"}
    assert everything[-1]["status"] == "bot"


def test_conversation_list_keeps_queue_and_most_recent(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O corte é feito no banco: a fila sempre aparece; do resto, as conversas mais recentes."""
    monkeypatch.setattr("app.admin.router.MAX_CONVERSATIONS", 3)
    say(admin, "atendente", contact="5500900000001")
    for contact in ("5500900000002", "5500900000003", "5500900000004"):
        say(admin, "oi", contact=contact)
    say(admin, "oi de novo", contact="5500900000002")

    items = admin.get("/api/admin/conversations").json()
    assert [i["contact"] for i in items] == ["5500900000001", "5500900000002", "5500900000004"]


def test_conversation_detail(admin: TestClient) -> None:
    say(admin, "oi")
    say(admin, "atendente")
    [item] = queue(admin)
    detail = admin.get(f"/api/admin/conversations/{item['id']}").json()
    assert [m["author"] for m in detail["messages"]] == ["customer", "bot", "customer", "bot"]
    assert detail["window_open"] is True
    assert detail["handoff_reason"] == "Cliente pediu atendente"
    assert admin.get("/api/admin/conversations/999").status_code == 404


def test_agent_reply_reaches_simulator_by_polling(admin: TestClient) -> None:
    first = say(admin, "atendente")
    [item] = queue(admin)

    response = admin.post(
        f"/api/admin/conversations/{item['id']}/reply", json={"text": "Olá! Sou a Ana, como ajudo?"}
    )
    assert response.status_code == 200
    assert response.json()["author"] == "agent"

    news = admin.get(
        "/api/simulator/messages",
        params={"contact": SIM_CONTACT, "after": first["last_message_id"]},
    ).json()
    assert [(m["author"], m["text"]) for m in news] == [("agent", "Olá! Sou a Ana, como ajudo?")]

    detail = admin.get(f"/api/admin/conversations/{item['id']}").json()
    assert detail["status"] == "human"
    assert detail["assigned_to"] == "Atendente"
    assert queue(admin) == []

    assert say(admin, "obrigado")["replies"] == []  # com humano, o bot fica quieto


def test_assign_then_release_returns_to_bot(admin: TestClient) -> None:
    say(admin, "atendente")
    [item] = queue(admin)
    cid = item["id"]
    assert admin.post(f"/api/admin/conversations/{cid}/assign").status_code == 204
    assert admin.get(f"/api/admin/conversations/{cid}").json()["status"] == "human"

    assert admin.post(f"/api/admin/conversations/{cid}/release").status_code == 204
    detail = admin.get(f"/api/admin/conversations/{cid}").json()
    assert detail["status"] == "bot"
    assert detail["assigned_to"] is None
    assert detail["handoff_at"] is None
    assert "atendimento com a equipe foi encerrado" in detail["messages"][-1]["text"]

    body = say(admin, "1")
    assert body["debug"]["state"] == "BOOK_SPECIALTY"  # bot respondendo de novo


@pytest.mark.parametrize("action", ["assign", "release"])
def test_actions_on_missing_conversation(admin: TestClient, action: str) -> None:
    assert admin.post(f"/api/admin/conversations/999/{action}").status_code == 404


def test_reply_validation(admin: TestClient) -> None:
    say(admin, "atendente")
    [item] = queue(admin)
    url = f"/api/admin/conversations/{item['id']}/reply"
    assert admin.post(url, json={"text": ""}).status_code == 422
    assert admin.post("/api/admin/conversations/999/reply", json={"text": "x"}).status_code == 404


# --- agenda ------------------------------------------------------------------------------


def book_monday(client: TestClient, contact: str = SIM_CONTACT) -> None:
    for text in ["agendar cardio", "segunda", "10h", "Maria Silva", "sim"]:
        say(client, text, contact=contact)


def test_appointments_list_and_filters(admin: TestClient) -> None:
    book_monday(admin)
    [appointment] = admin.get("/api/admin/appointments").json()
    assert appointment["patient_name"] == "Maria Silva"
    assert appointment["specialty"] == "Cardiologia"
    assert appointment["starts_at"] == "2026-11-16T10:00:00"

    def count(**params: str) -> int:
        return len(admin.get("/api/admin/appointments", params=params).json())

    assert count(date="2026-11-16") == 1
    assert count(date="2026-11-17") == 0
    assert count(specialty="cardiologia") == 1
    assert count(specialty="clinica-geral") == 0
    assert count(status="cancelled") == 0
    assert [s["slug"] for s in admin.get("/api/admin/specialties").json()] == [
        "cardiologia",
        "clinica-geral",
    ]


def test_staff_cancels_appointment(admin: TestClient) -> None:
    book_monday(admin)
    [appointment] = admin.get("/api/admin/appointments").json()
    url = f"/api/admin/appointments/{appointment['id']}/cancel"
    assert admin.post(url).status_code == 204
    assert admin.post(url).status_code == 404  # já cancelada
    [cancelled] = admin.get("/api/admin/appointments", params={"status": "cancelled"}).json()
    assert cancelled["id"] == appointment["id"]


# --- WhatsApp: janela de 24 h e entrega --------------------------------------------------

SECRET = "app-secret"
SEND_URL = "https://graph.facebook.com/v26.0/123456/messages"
WA_ID = "5547999990001"


@pytest.fixture
def wa_admin(
    settings: Settings, clock: FixedClock
) -> Iterator[tuple[TestClient, respx.MockRouter]]:
    wa_settings = Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "whatsapp_token": "token",
            "whatsapp_phone_number_id": "123456",
            "whatsapp_app_secret": SECRET,
            "whatsapp_verify_token": "verify",
        },
    )
    with respx.mock(assert_all_called=False) as meta:
        meta.post(SEND_URL).mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
        )
        with TestClient(create_app(wa_settings, clock=clock)) as client:
            assert login(client).status_code == 200
            yield client, meta


def whatsapp_in(client: TestClient, text: str, message_id: str) -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": WA_ID,
                                    "id": message_id,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body, SECRET)}
    )
    assert response.status_code == 200


def test_agent_reply_is_sent_through_whatsapp(
    wa_admin: tuple[TestClient, respx.MockRouter],
) -> None:
    client, meta = wa_admin
    whatsapp_in(client, "atendente", "wamid.1")
    [item] = queue(client)
    calls_before = len(meta.calls)
    response = client.post(
        f"/api/admin/conversations/{item['id']}/reply", json={"text": "Oi, aqui é a Ana"}
    )
    assert response.status_code == 200
    body = json.loads(meta.calls[calls_before].request.content)
    assert body["to"] == WA_ID
    assert body["text"]["body"] == "Oi, aqui é a Ana"


def test_whatsapp_reply_blocked_after_24h(
    wa_admin: tuple[TestClient, respx.MockRouter], clock: FixedClock
) -> None:
    client, meta = wa_admin
    whatsapp_in(client, "atendente", "wamid.1")
    [item] = queue(client)
    clock.advance(timedelta(hours=24, minutes=1))

    detail = client.get(f"/api/admin/conversations/{item['id']}").json()
    assert detail["window_open"] is False
    calls_before = len(meta.calls)
    response = client.post(f"/api/admin/conversations/{item['id']}/reply", json={"text": "Oi"})
    assert response.status_code == 409
    assert "template" in response.json()["detail"]
    assert len(meta.calls) == calls_before

    # Liberar a conversa fora da janela não tenta enviar o aviso.
    assert client.post(f"/api/admin/conversations/{item['id']}/release").status_code == 204
    assert len(meta.calls) == calls_before


def test_whatsapp_delivery_failure(wa_admin: tuple[TestClient, respx.MockRouter]) -> None:
    client, meta = wa_admin
    whatsapp_in(client, "atendente", "wamid.1")
    [item] = queue(client)
    meta.post(SEND_URL).mock(return_value=httpx.Response(400, json={"error": {"code": 100}}))
    response = client.post(f"/api/admin/conversations/{item['id']}/reply", json={"text": "Oi"})
    assert response.status_code == 502
    detail = client.get(f"/api/admin/conversations/{item['id']}").json()
    assert all(m["author"] != "agent" for m in detail["messages"])  # nada gravado
    assert detail["status"] == "waiting_human"
    # Aviso de fim de atendimento que falha não impede devolver ao bot.
    assert client.post(f"/api/admin/conversations/{item['id']}/release").status_code == 204


# --- produção e dados de demonstração ----------------------------------------------------


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        clinic_data_path=CLINIC_FILE,
        **overrides,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"app_env": "production"},
        {"app_env": "production", "admin_password": "admin", "session_secret": "x" * 40},
        {"app_env": "production", "admin_password": "senha-longa-123", "session_secret": "curto"},
    ],
)
def test_production_refuses_insecure_config(tmp_path: Path, overrides: dict) -> None:
    with pytest.raises(RuntimeError, match="insegura para produção"):
        create_app(make_settings(tmp_path, **overrides))


def test_production_with_secure_config(tmp_path: Path, clock: FixedClock) -> None:
    settings = make_settings(
        tmp_path,
        app_env="production",
        admin_password="senha-longa-123",
        session_secret="s" * 40,
        demo_seed=False,
    )
    with TestClient(create_app(settings, clock=clock)) as client:
        assert client.get("/api/admin/login-info").json()["demo_username"] is None
        response = login(client, password="senha-longa-123")
        assert response.status_code == 200
        assert "secure" in response.headers["set-cookie"].lower()


def test_public_demo_shows_its_login_in_production(tmp_path: Path, clock: FixedClock) -> None:
    settings = make_settings(
        tmp_path,
        app_env="production",
        public_demo=True,
        admin_password="demo-publica-123",
        session_secret="s" * 40,
    )
    with TestClient(create_app(settings, clock=clock)) as client:
        assert client.get("/api/admin/login-info").json() == {
            "demo_username": "admin",
            "demo_password": "demo-publica-123",
        }
        assert login(client, password="demo-publica-123").status_code == 200


def test_public_demo_refuses_real_whatsapp(tmp_path: Path) -> None:
    """Login exposto + WhatsApp real = mensagens e números de pessoas reais no painel aberto."""
    settings = make_settings(
        tmp_path,
        public_demo=True,
        whatsapp_token="t",
        whatsapp_phone_number_id="99",
        whatsapp_app_secret="s",
        whatsapp_verify_token="v",
    )
    with pytest.raises(RuntimeError, match="PUBLIC_DEMO"):
        create_app(settings)


def test_demo_seed_creates_queue_with_urgent(tmp_path: Path, clock: FixedClock) -> None:
    with TestClient(create_app(make_settings(tmp_path), clock=clock)) as client:
        assert login(client).status_code == 200
        items = queue(client)
        assert [i["priority"] for i in items] == ["urgent", "normal"]
        assert len(client.get("/api/admin/conversations").json()) == 3
    # Reiniciar não duplica.
    with TestClient(create_app(make_settings(tmp_path), clock=clock)) as client:
        login(client)
        assert len(client.get("/api/admin/conversations").json()) == 3
