import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.channels.whatsapp.signature import sign
from app.clock import FixedClock
from app.config import Settings
from app.db.models import Conversation, Message
from app.main import create_app

SECRET = "app-secret-de-teste"
VERIFY = "verify-de-teste"
TOKEN = "token-super-secreto"
SEND_URL = "https://graph.facebook.com/v26.0/123456/messages"
WA_ID = "5547999990001"


@pytest.fixture
def wa_settings(settings: Settings) -> Settings:
    # Settings(...) revalida os campos (model_copy não converteria para SecretStr).
    return Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "whatsapp_token": TOKEN,
            "whatsapp_phone_number_id": "123456",
            "whatsapp_app_secret": SECRET,
            "whatsapp_verify_token": VERIFY,
        },
    )


@pytest.fixture
def meta() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.post(SEND_URL).mock(
            return_value=httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
        )
        yield router


@pytest.fixture
def wa_client(
    wa_settings: Settings, clock: FixedClock, meta: respx.MockRouter
) -> Iterator[TestClient]:
    with TestClient(create_app(wa_settings, clock=clock)) as client:
        yield client


def webhook(*messages: dict[str, Any], statuses: list[dict] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"phone_number_id": "123456"},
        "contacts": [{"wa_id": WA_ID, "profile": {"name": "Ana Teste"}}],
    }
    if messages:
        value["messages"] = list(messages)
    if statuses:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}],
    }


def text_message(body: str, message_id: str = "wamid.1") -> dict[str, Any]:
    return {
        "from": WA_ID,
        "id": message_id,
        "timestamp": "1",
        "type": "text",
        "text": {"body": body},
    }


def post(
    client: TestClient, payload: Any, secret: str = SECRET, signed: bool = True
) -> httpx.Response:
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signed:
        headers["X-Hub-Signature-256"] = sign(body, secret)
    return client.post("/webhook/whatsapp", content=body, headers=headers)


def sent_bodies(meta: respx.MockRouter) -> list[dict[str, Any]]:
    return [json.loads(call.request.content) for call in meta.calls]


def sent_texts(meta: respx.MockRouter) -> list[str]:
    """Texto visível de cada envio (mensagem de texto ou corpo da interativa)."""
    return [
        b["text"]["body"] if b["type"] == "text" else b["interactive"]["body"]["text"]
        for b in sent_bodies(meta)
    ]


# --- verificação -------------------------------------------------------------------------


def test_verification_returns_challenge(wa_client: TestClient) -> None:
    response = wa_client.get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )
    assert response.status_code == 200
    assert response.text == "1158201444"


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"},
        {},
    ],
)
def test_verification_rejects(wa_client: TestClient, params: dict) -> None:
    assert wa_client.get("/webhook/whatsapp", params=params).status_code == 403


def test_webhook_disabled_without_credentials(client: TestClient) -> None:
    assert client.get("/webhook/whatsapp").status_code == 404
    assert client.post("/webhook/whatsapp", content=b"{}").status_code == 404


# --- assinatura --------------------------------------------------------------------------


def test_missing_signature(wa_client: TestClient, meta: respx.MockRouter) -> None:
    assert post(wa_client, webhook(text_message("oi")), signed=False).status_code == 401
    assert not meta.calls


def test_invalid_signature(wa_client: TestClient, meta: respx.MockRouter) -> None:
    assert post(wa_client, webhook(text_message("oi")), secret="outro").status_code == 401
    assert not meta.calls


def test_signature_covers_raw_body(wa_client: TestClient) -> None:
    body = json.dumps(webhook(text_message("oi"))).encode()
    tampered = body.replace(b"oi", b"ol")
    response = wa_client.post(
        "/webhook/whatsapp",
        content=tampered,
        headers={"X-Hub-Signature-256": sign(body, SECRET)},
    )
    assert response.status_code == 401


def test_invalid_json_with_valid_signature(wa_client: TestClient) -> None:
    body = b"not json"
    response = wa_client.post(
        "/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sign(body, SECRET)}
    )
    assert response.status_code == 400


# --- recebimento e resposta --------------------------------------------------------------


def test_text_message_gets_reply(wa_client: TestClient, meta: respx.MockRouter) -> None:
    response = post(wa_client, webhook(text_message("oi")))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    [call] = meta.calls
    assert call.request.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(call.request.content)
    assert body["to"] == WA_ID
    assert body["type"] == "interactive"
    interactive = body["interactive"]
    assert interactive["type"] == "list"  # 7 opções do menu viram lista
    assert "Clínica Teste" in interactive["body"]["text"]
    rows = interactive["action"]["sections"][0]["rows"]
    assert len(rows) == 7
    assert rows[0] == {"id": "opt:1", "title": "Agendar consulta"}
    assert all(len(r["title"]) <= 24 for r in rows)
    assert rows[5]["description"] == "Endereço e horário de funcionamento"

    with wa_client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        conversation = session.scalar(select(Conversation))
        assert conversation is not None
        assert conversation.channel == "whatsapp"
        assert conversation.display_name == "Ana Teste"
        assert conversation.last_inbound_at is not None


def test_same_payload_twice_answers_once(wa_client: TestClient, meta: respx.MockRouter) -> None:
    payload = webhook(text_message("oi", message_id="wamid.repetido"))
    post(wa_client, payload)
    post(wa_client, payload)
    assert len(meta.calls) == 1
    with wa_client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        assert len(session.scalars(select(Message)).all()) == 2


def test_statuses_are_ignored(wa_client: TestClient, meta: respx.MockRouter) -> None:
    payload = webhook(statuses=[{"id": "wamid.out", "status": "delivered", "recipient_id": WA_ID}])
    assert post(wa_client, payload).status_code == 200
    assert not meta.calls


def test_other_objects_are_ignored(wa_client: TestClient, meta: respx.MockRouter) -> None:
    assert post(wa_client, {"object": "page", "entry": []}).status_code == 200
    assert not meta.calls


def test_list_choice_is_handled_by_option_id(wa_client: TestClient, meta: respx.MockRouter) -> None:
    choice = {
        "from": WA_ID,
        "id": "wamid.list",
        "type": "interactive",
        "interactive": {
            "type": "list_reply",
            "list_reply": {"id": "opt:6", "title": "Endereço e horário"},  # título truncado
        },
    }
    post(wa_client, webhook(choice))
    assert "Horário de funcionamento" in sent_texts(meta)[0]


def test_yes_no_go_as_buttons(wa_client: TestClient, meta: respx.MockRouter) -> None:
    for i, text in enumerate(["agendar cardio", "segunda", "10h", "Maria Silva"]):
        post(wa_client, webhook(text_message(text, f"wamid.{i}")))
    last = sent_bodies(meta)[-1]["interactive"]
    assert last["type"] == "button"
    assert [b["reply"] for b in last["action"]["buttons"]] == [
        {"id": "opt:Sim", "title": "Sim"},
        {"id": "opt:Não", "title": "Não"},
    ]
    button = {
        "from": WA_ID,
        "id": "wamid.yes",
        "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": "opt:Sim", "title": "Sim"}},
    }
    post(wa_client, webhook(button))
    assert "Consulta confirmada" in sent_texts(meta)[-1]


def test_time_slots_go_as_text(wa_client: TestClient, meta: respx.MockRouter) -> None:
    for i, text in enumerate(["agendar cardio", "segunda"]):
        post(wa_client, webhook(text_message(text, f"wamid.{i}")))
    last = sent_bodies(meta)[-1]
    assert last["type"] == "text"  # 17 horários: o texto já lista as opções
    assert "Manhã: 08:00" in last["text"]["body"]


def test_interactive_can_be_disabled(
    settings: Settings, clock: FixedClock, meta: respx.MockRouter
) -> None:
    plain = Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "whatsapp_token": TOKEN,
            "whatsapp_phone_number_id": "123456",
            "whatsapp_app_secret": SECRET,
            "whatsapp_verify_token": VERIFY,
            "whatsapp_interactive": False,
        },
    )
    with TestClient(create_app(plain, clock=clock)) as client:
        post(client, webhook(text_message("oi")))
    [body] = sent_bodies(meta)
    assert body["type"] == "text"
    assert "*1.* Agendar consulta" in body["text"]["body"]


def test_interactive_reply(wa_client: TestClient, meta: respx.MockRouter) -> None:
    message = {
        "from": WA_ID,
        "id": "wamid.btn",
        "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": "1", "title": "Agendar consulta"},
        },
    }
    post(wa_client, webhook(message))
    assert "Qual especialidade" in sent_texts(meta)[0]


@pytest.mark.parametrize("kind", ["audio", "image", "sticker", "location"])
def test_unsupported_types_get_standard_reply(
    wa_client: TestClient, meta: respx.MockRouter, kind: str
) -> None:
    post(wa_client, webhook({"from": WA_ID, "id": f"wamid.{kind}", "type": kind, kind: {}}))
    assert "só consigo ler mensagens de texto" in sent_texts(meta)[0]
    with wa_client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        inbound = session.scalars(select(Message).where(Message.direction == "in")).one()
        assert inbound.text == f"[mensagem do tipo {kind}]"


def test_unsupported_type_while_with_human_is_silent(
    wa_client: TestClient, meta: respx.MockRouter
) -> None:
    post(wa_client, webhook(text_message("atendente", "wamid.a")))
    post(wa_client, webhook({"from": WA_ID, "id": "wamid.b", "type": "audio", "audio": {}}))
    assert len(meta.calls) == 1  # só a resposta do pedido de atendente


def test_conversation_flow_over_whatsapp(wa_client: TestClient, meta: respx.MockRouter) -> None:
    for i, text in enumerate(["agendar cardio", "segunda", "10h", "Maria Silva", "sim"]):
        post(wa_client, webhook(text_message(text, f"wamid.{i}")))
    assert "Consulta confirmada" in sent_texts(meta)[-1]


def test_send_failure_does_not_break_webhook(
    wa_client: TestClient, meta: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    meta.post(SEND_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"code": 131030, "message": "Recipient not allowed"}}
        )
    )
    assert post(wa_client, webhook(text_message("oi"))).status_code == 200
    assert "131030" in caplog.text
    assert TOKEN not in caplog.text
    assert WA_ID not in caplog.text  # telefone mascarado


def test_processing_error_is_logged_and_isolated(
    wa_client: TestClient,
    meta: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = wa_client.app.state.conversation_service  # type: ignore[attr-defined]
    original = service.receive

    def flaky(channel, contact, text, **kwargs):  # type: ignore[no-untyped-def]
        if text == "explode":
            raise RuntimeError("falha simulada")
        return original(channel, contact, text, **kwargs)

    monkeypatch.setattr(service, "receive", flaky)
    post(wa_client, webhook(text_message("explode", "wamid.x"), text_message("oi", "wamid.y")))
    assert "erro ao processar" in caplog.text
    assert len(meta.calls) == 1  # a segunda mensagem do lote foi respondida


def test_status_reports_whatsapp_enabled(wa_client: TestClient) -> None:
    status = wa_client.get("/api/status").json()
    assert status["whatsapp_enabled"] is True
    assert status["demo_mode"] is False
