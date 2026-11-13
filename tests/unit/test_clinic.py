from pathlib import Path

import pytest
import yaml

from app.clinic import ClinicDataError, load_clinic

REAL_DATA = Path(__file__).parents[2] / "data" / "clinica.yaml"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "clinic.yaml"


def write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "clinica.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def valid_data() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def test_real_clinic_file_is_valid() -> None:
    clinic = load_clinic(REAL_DATA)
    assert clinic.clinic.timezone == "America/Sao_Paulo"
    assert len(clinic.specialties) == 4
    assert clinic.business_hours.for_weekday(6) == ()


def test_fixture_values(tmp_path: Path) -> None:
    clinic = load_clinic(write(tmp_path, valid_data()))
    cardio = clinic.specialty("cardiologia")
    assert cardio is not None
    assert cardio.price_cents == 28050
    assert cardio.match_terms[0] == "doutor bruno teste"  # mais longo primeiro
    assert {"dr bruno", "doutor bruno", "bruno teste", "cardiologista"} <= set(cardio.match_terms)
    assert "bruno" not in cardio.match_terms  # primeiro nome sozinho é comum demais
    monday = clinic.business_hours.for_weekday(0)
    assert [str(r) for r in monday] == ["08:00-12:00", "13:30-18:00"]
    assert (monday[1].open_min, monday[1].close_min) == (810, 1080)
    assert clinic.specialty("inexistente") is None


def test_missing_file() -> None:
    with pytest.raises(ClinicDataError, match="não encontrado"):
        load_clinic(Path("nao/existe.yaml"))


def test_broken_yaml(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_text("clinic: [", encoding="utf-8")
    with pytest.raises(ClinicDataError, match="YAML inválido"):
        load_clinic(path)


def test_not_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(ClinicDataError, match="mapeamento"):
        load_clinic(write(tmp_path, ["a", "b"]))


def mutate(path: str, value: object) -> dict:
    data = valid_data()
    target = data
    *parents, last = path.split(".")
    for key in parents:
        target = target[int(key)] if isinstance(target, list) else target[key]
    target[last] = value
    return data


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("clinic.timezone", "Marte/Olympus", "fuso horário desconhecido"),
        ("clinic.name", "", "clinic.name"),
        ("business_hours.monday", ["08:15-12:00"], "múltiplos de 30"),
        ("business_hours.monday", ["12:00-08:00"], "início deve ser antes"),
        ("business_hours.monday", ["08:00-12:00", "11:00-13:00"], "sobrepostos"),
        ("business_hours.monday", ["0800"], "HH:MM-HH:MM"),
        ("scheduling.days_ahead", 0, "scheduling.days_ahead"),
        ("specialties", [], "specialties"),
        ("clinic.extra_field", "x", "extra"),
    ],
)
def test_invalid_data_has_clear_message(
    tmp_path: Path, path: str, value: object, message: str
) -> None:
    with pytest.raises(ClinicDataError, match=message) as exc_info:
        load_clinic(write(tmp_path, mutate(path, value)))
    assert "Dados da clínica inválidos" in str(exc_info.value)


def test_duplicate_slug(tmp_path: Path) -> None:
    data = valid_data()
    data["specialties"][1]["slug"] = "clinica-geral"
    with pytest.raises(ClinicDataError, match="slugs de especialidade repetidos"):
        load_clinic(write(tmp_path, data))


def test_same_professional_in_two_specialties(tmp_path: Path) -> None:
    data = valid_data()
    data["specialties"][1]["professional"] = data["specialties"][0]["professional"]
    clinic = load_clinic(write(tmp_path, data))
    assert len(clinic.specialties) == 2


def test_alias_shared_by_two_specialties(tmp_path: Path) -> None:
    data = valid_data()
    data["specialties"][1]["aliases"] = ["Clínico"]
    with pytest.raises(ClinicDataError, match="identifica duas especialidades"):
        load_clinic(write(tmp_path, data))


def test_clinic_must_open_some_day(tmp_path: Path) -> None:
    data = valid_data()
    data["business_hours"] = {}
    with pytest.raises(ClinicDataError, match="pelo menos um dia"):
        load_clinic(write(tmp_path, data))
