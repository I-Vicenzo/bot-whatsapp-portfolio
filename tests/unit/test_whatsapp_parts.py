import json

import httpx
import pytest
import respx

from app.channels.whatsapp.client import WhatsAppClient, WhatsAppError
from app.channels.whatsapp.payload import parse_webhook
from app.channels.whatsapp.signature import sign, verify_signature
from app.logging_utils import mask_phone

URL = "https://graph.facebook.com/v26.0/99/messages"


def make_client(sleeps: list[float]) -> WhatsAppClient:
    return WhatsAppClient(token="t", phone_number_id="99", api_version="v26.0", sleep=sleeps.append)


# --- assinatura --------------------------------------------------------------------------


def test_signature_roundtrip() -> None:
    body = b'{"a": 1}'
    assert verify_signature(body, sign(body, "s"), "s")


@pytest.mark.parametrize("header", [None, "", "sha1=abc", "sha256=", "sha256=00" * 32])
def test_signature_rejects(header: str | None) -> None:
    assert not verify_signature(b"{}", header, "s")


# --- payload -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"object": "whatsapp_business_account"},
        {"object": "whatsapp_business_account", "entry": "x"},
        {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "x"}]}]},
        {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": {"messages": [{"id": 1}]}}]}],
        },
    ],
)
def test_malformed_payloads_are_ignored(payload: object) -> None:
    assert parse_webhook(payload) == []


def test_list_reply_and_template_button() -> None:
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
                                    "from": "55",
                                    "id": "a",
                                    "type": "interactive",
                                    "interactive": {"list_reply": {"title": "Cardiologia"}},
                                },
                                {
                                    "from": "55",
                                    "id": "b",
                                    "type": "button",
                                    "button": {"text": "Sim"},
                                },
                                {"from": "55", "id": "c", "type": "text", "text": {"body": "  "}},
                            ]
                        },
                    }
                ]
            }
        ],
    }
    messages = parse_webhook(payload)
    assert [m.text for m in messages] == ["Cardiologia", "Sim", None]
    assert messages[0].profile_name is None


# --- cliente de envio --------------------------------------------------------------------


@respx.mock
def test_send_text() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "w1"}]})
    )
    assert make_client([]).send_text("5547999990001", "x" * 5000) == "w1"
    body = json.loads(route.calls[0].request.content)
    assert len(body["text"]["body"]) == 4096
    assert body["text"]["preview_url"] is False


@respx.mock
def test_retries_on_429_and_5xx_with_backoff() -> None:
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(503),
            httpx.Response(200, json={"messages": [{"id": "w1"}]}),
        ]
    )
    sleeps: list[float] = []
    assert make_client(sleeps).send_text("5547999990001", "oi") == "w1"
    assert sleeps == [2.0, 1.0]  # Retry-After respeitado; depois backoff exponencial


@respx.mock
def test_gives_up_after_retries() -> None:
    respx.post(URL).mock(return_value=httpx.Response(500))
    sleeps: list[float] = []
    with pytest.raises(WhatsAppError):
        make_client(sleeps).send_text("5547999990001", "oi")
    assert sleeps == [0.5, 1.0, 2.0]


@respx.mock
def test_client_error_is_not_retried() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(401, text="not json"))
    with pytest.raises(WhatsAppError, match="401"):
        make_client([]).send_text("5547999990001", "oi")
    assert route.call_count == 1


@respx.mock
def test_network_errors_are_retried() -> None:
    respx.post(URL).mock(
        side_effect=[httpx.ConnectError("x"), httpx.Response(200, json={"messages": [{"id": "w"}]})]
    )
    assert make_client([]).send_text("55", "oi") == "w"


@respx.mock
def test_network_error_on_last_attempt() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(WhatsAppError, match="rede"):
        make_client([]).send_text("55", "oi")


@respx.mock
def test_mark_as_read() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"success": True}))
    client = make_client([])
    client.mark_as_read("wamid.1")
    client.close()
    assert json.loads(route.calls[0].request.content)["status"] == "read"


@pytest.mark.parametrize(
    ("phone", "masked"),
    [("5547999990001", "5547*****0001"), ("12345678", "********"), ("", "")],
)
def test_mask_phone(phone: str, masked: str) -> None:
    assert mask_phone(phone) == masked
