"""Testes ponta a ponta no navegador (Playwright), contra um servidor real.

Fora da suíte padrão. Rodar: `uv run pytest -m e2e` (no Windows sem Chromium baixado:
`uv run pytest -m e2e --browser-channel msedge`).
"""

import re
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, expect

from app.config import Settings
from app.main import create_app
from tests.conftest import CLINIC_FILE

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def server_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    db = tmp_path_factory.mktemp("e2e") / "app.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{db}",
        clinic_data_path=CLINIC_FILE,
        demo_seed=False,
        simulator_rate_limit_per_minute=1000,
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("servidor de teste não subiu")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def messages(page: Page):  # type: ignore[no-untyped-def]
    return page.locator("#messages")


def test_booking_with_buttons(page: Page, server_url: str) -> None:
    page.goto(server_url)
    expect(page.locator("#chip-whatsapp")).to_have_text("WhatsApp: desligado")

    page.get_by_role("button", name="Agendar consulta").first.click()
    expect(messages(page)).to_contain_text("Qual especialidade")
    page.locator("#options").get_by_role("button", name="Cardiologia").click()
    expect(messages(page)).to_contain_text("Qual dia fica melhor")
    page.locator("#options button").first.click()
    expect(messages(page)).to_contain_text("Horários livres")
    page.locator("#options button").first.click()
    expect(messages(page)).to_contain_text("nome completo")
    page.locator("#input").fill("Maria Teste")
    page.locator("#input").press("Enter")
    expect(messages(page)).to_contain_text("Confira os dados")
    page.locator("#options").get_by_role("button", name="Sim").click()
    expect(messages(page)).to_contain_text("Consulta confirmada")
    expect(page.locator("#dbg-state")).to_have_text("MENU")


def test_customer_text_is_not_rendered_as_html(page: Page, server_url: str) -> None:
    page.goto(server_url)
    payload = '<img src=x onerror="window.__xss = 1"> *negrito*'
    page.locator("#input").fill(payload)
    page.locator("#input").press("Enter")
    expect(messages(page)).to_contain_text("<img src=x")
    assert messages(page).locator("img").count() == 0
    assert page.evaluate("window.__xss") is None


def test_agent_reply_reaches_simulator(browser: Browser, server_url: str) -> None:
    customer = browser.new_context().new_page()
    customer.goto(server_url)
    customer.get_by_role("button", name="Pedir atendente").click()
    expect(messages(customer)).to_contain_text("Chamei alguém da nossa equipe")
    contact = customer.locator("#contact").input_value()

    agent = browser.new_context().new_page()
    agent.goto(f"{server_url}/admin")
    expect(agent.locator("#demo-credentials")).to_contain_text("admin")
    agent.locator("#username").fill("admin")
    agent.locator("#password").fill("admin")
    agent.locator("#password").press("Enter")
    agent.locator("#conversation-list button", has_text=contact).click()
    expect(agent.locator("#detail-meta")).to_contain_text("aguardando")
    agent.locator("#reply-text").fill("Olá, aqui é a Ana da recepção!")
    agent.locator("#reply-text").press("Enter")
    expect(agent.locator("#messages")).to_contain_text("Olá, aqui é a Ana")

    # O simulador recebe por polling (a cada 3 s).
    expect(messages(customer).locator(".msg.agent")).to_contain_text(
        "Olá, aqui é a Ana da recepção!", timeout=10_000
    )
    expect(agent.locator("#detail-meta")).to_contain_text(re.compile("em atendimento"))
