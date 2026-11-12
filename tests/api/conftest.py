from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.clock import FixedClock
from app.config import Settings
from app.main import create_app
from tests.conftest import CLINIC_FILE


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        clinic_data_path=CLINIC_FILE,
        demo_seed=False,
    )


@pytest.fixture
def app(settings: Settings, clock: FixedClock) -> FastAPI:
    return create_app(settings, clock=clock)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
