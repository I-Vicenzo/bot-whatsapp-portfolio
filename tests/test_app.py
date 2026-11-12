from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app import __version__
from app.clinic import ClinicDataError
from app.clock import FixedClock
from app.config import Settings
from app.db.models import Specialty
from app.main import create_app
from tests.conftest import CLINIC_FILE


def make_settings(tmp_path: Path, clinic_file: Path = CLINIC_FILE) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'var' / 'app.db'}",
        clinic_data_path=clinic_file,
    )


def test_health(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_openapi_is_published(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        assert client.get("/docs").status_code == 200


def test_startup_migrates_and_syncs_database(tmp_path: Path, clock: FixedClock) -> None:
    app = create_app(make_settings(tmp_path), clock=clock)
    with TestClient(app):
        engine = app.state.engine
        assert "appointments" in inspect(engine).get_table_names()
        with app.state.session_factory() as session:
            slugs = set(session.scalars(select(Specialty.slug)))
        assert slugs == {"clinica-geral", "cardiologia"}
    assert (tmp_path / "var" / "app.db").exists()


def test_restart_is_idempotent(tmp_path: Path) -> None:
    for _ in range(2):
        with TestClient(create_app(make_settings(tmp_path))) as client:
            assert client.get("/health").status_code == 200


def test_invalid_clinic_file_prevents_startup(tmp_path: Path) -> None:
    broken = tmp_path / "clinica.yaml"
    broken.write_text("clinic:\n  name: X\n", encoding="utf-8")
    with pytest.raises(ClinicDataError, match="Dados da clínica inválidos"):
        create_app(make_settings(tmp_path, broken))


def test_unsupported_database_is_rejected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path).model_copy(
        update={"database_url": "mysql://u:p@localhost/db"}
    )
    app = create_app(settings)
    with pytest.raises(ValueError, match="não suportado"), TestClient(app):
        pass
