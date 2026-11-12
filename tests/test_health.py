from fastapi.testclient import TestClient

from app import __version__
from app.config import Settings
from app.main import create_app


def test_health_returns_ok() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_openapi_is_published() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    assert client.get("/docs").status_code == 200
