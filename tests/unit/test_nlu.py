from datetime import date, time

import pytest

from app.nlu.intents import Intent, detect, matches
from app.nlu.normalize import contains_phrase, normalize
from app.nlu.parsers import parse_date, parse_name, parse_option, parse_time, parse_yes_no

TODAY = date(2026, 11, 12)  # quinta-feira


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Olá,   MUNDO!! ", "ola mundo"),
        ("Gostaria de agendar às 14:30", "gostaria de agendar as 14:30"),
        ("Segunda-feira, 15/11", "segunda feira 15/11"),
        ("Convênio? Não sei...", "convenio nao sei"),
        ("Coração ❤️", "coracao"),
        ("1️⃣", "1"),
        ("snake_case", "snake case"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_contains_phrase_uses_word_boundaries() -> None:
    assert contains_phrase("quero cardio amanha", "cardio")
    assert not contains_phrase("cardiologista", "cardio")


# --- intenções ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "intent"),
    [
        ("Quero falar com um ATENDENTE", Intent.HUMAN),
        ("atendete", Intent.HUMAN),
        ("posso falar com alguém?", Intent.HUMAN),
        ("Humano por favor", Intent.HUMAN),
        ("É URGENTE", Intent.URGENT),
        ("urgência!!", Intent.URGENT),
        ("emergência", Intent.URGENT),
        ("menu", Intent.MENU),
        ("Início", Intent.MENU),
        ("sair", Intent.MENU),
        ("0", Intent.MENU),
        ("volta pro menu", Intent.MENU),
        ("minhas consultas", Intent.MY_APPOINTMENTS),
        ("quero ver meus agendamentos", Intent.MY_APPOINTMENTS),
        ("tenho consulta marcada?", Intent.MY_APPOINTMENTS),
        ("quero cancelar", Intent.CANCEL),
        ("cancela minha consulta", Intent.CANCEL),
        ("preciso desmarcar", Intent.CANCEL),
        ("quero agendar", Intent.BOOK),
        ("agnedar", None),
        ("Agenda uma consulta pra mim", Intent.BOOK),
        ("queria marcar com o cardiologista", Intent.BOOK),
        ("tem horário amanhã?", Intent.BOOK),
        ("gostaria de uma consulta", Intent.BOOK),
        ("qual é a agenda do paciente Fernando Lopes", Intent.THIRD_PARTY),
        ("quais as consultas da paciente Joana?", Intent.THIRD_PARTY),
        ("me passa a agenda da Joana", Intent.THIRD_PARTY),
        ("agenda do Dr. Bruno", Intent.THIRD_PARTY),  # o engine agenda com o profissional
        ("como está a agenda da semana?", Intent.BOOK),
        ("tem agenda da tarde?", Intent.BOOK),
        ("agenda do sábado", Intent.BOOK),
        ("qual o endereço?", Intent.ADDRESS),
        ("onde fica a clínica", Intent.ADDRESS),
        ("como chego aí", Intent.ADDRESS),
        ("qual o horário de funcionamento", Intent.HOURS),
        ("que horas abre?", Intent.HOURS),
        ("vocês abrem sábado?", Intent.HOURS),
        ("aceita convênio?", Intent.INSURANCE),
        ("atende plano de saúde", Intent.INSURANCE),
        ("quanto custa a consulta?", Intent.PRICES),
        ("qual o valor", Intent.PRICES),
        ("preço da consulta particular", Intent.PRICES),
        ("quais especialidades vocês têm", Intent.SPECIALTIES),
        ("quais médicos atendem", Intent.SPECIALTIES),
        ("oi", Intent.GREETING),
        ("Oiii", Intent.GREETING),
        ("Bom dia!", Intent.GREETING),
        ("obrigada", Intent.THANKS),
        ("valeu!", Intent.THANKS),
        ("asdfgh", None),
    ],
)
def test_detect(raw: str, intent: Intent | None) -> None:
    assert detect(normalize(raw)) == intent


@pytest.mark.parametrize("raw", ["não é urgente", "nao eh urgente", "não tem urgência"])
def test_negated_urgency(raw: str) -> None:
    assert not matches(Intent.URGENT, normalize(raw))


def test_detect_respects_candidates_order() -> None:
    text = normalize("bom dia, qual o endereço?")
    assert detect(text, (Intent.ADDRESS, Intent.GREETING)) is Intent.ADDRESS
    assert detect(text, (Intent.GREETING,)) is Intent.GREETING


# --- opções ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "count", "expected"),
    [
        ("1", 3, 1),
        ("3", 3, 3),
        ("4", 3, None),
        ("0", 3, None),
        ("opção 2", 3, 2),
        ("op2", 3, 2),
        ("dois", 3, 2),
        ("nove", 9, 9),
        ("2️⃣", 3, 2),
        ("quero a 2", 3, None),
        ("cardiologia", 3, None),
    ],
)
def test_parse_option(raw: str, count: int, expected: int | None) -> None:
    assert parse_option(normalize(raw), count) == expected


# --- datas -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hoje", date(2026, 11, 12)),
        ("amanhã", date(2026, 11, 13)),
        ("AMANHA de manhã", date(2026, 11, 13)),
        ("depois de amanhã", date(2026, 11, 14)),
        ("sexta", date(2026, 11, 13)),
        ("sexta-feira", date(2026, 11, 13)),
        ("Sex", date(2026, 11, 13)),
        ("segunda", date(2026, 11, 16)),
        ("na terça", date(2026, 11, 17)),
        ("quinta", date(2026, 11, 12)),
        ("próxima quinta", date(2026, 11, 19)),
        ("sábado", date(2026, 11, 14)),
        ("15/11", date(2026, 11, 15)),
        ("Sex 13/11", date(2026, 11, 13)),
        ("5/1", date(2027, 1, 5)),
        ("10/11", date(2027, 11, 10)),
        ("20/11/2026", date(2026, 11, 20)),
        ("20/11/26", date(2026, 11, 20)),
        ("31/02", None),
        ("31/02/2027", None),
        ("15 de novembro", date(2026, 11, 15)),
        ("3 de março de 2027", date(2027, 3, 3)),
        ("2 de janeiro", date(2027, 1, 2)),
        ("dia 20", date(2026, 11, 20)),
        ("dia 5", date(2026, 12, 5)),
        ("dia 31", date(2026, 12, 31)),
        ("qualquer dia", None),
        ("", None),
    ],
)
def test_parse_date(raw: str, expected: date | None) -> None:
    assert parse_date(normalize(raw), TODAY) == expected


def test_parse_date_day_only_invalid_next_month() -> None:
    # 31/01 -> "dia 30" cai em fevereiro, que não tem dia 30.
    assert parse_date("dia 30", date(2027, 1, 31)) is None


# --- horas -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14", time(14, 0)),
        ("9", time(9, 0)),
        ("14h", time(14, 0)),
        ("14 h", time(14, 0)),
        ("14hs", time(14, 0)),
        ("14:30", time(14, 30)),
        ("14h30", time(14, 30)),
        ("às 8:00", time(8, 0)),
        ("14 horas", time(14, 0)),
        ("2 da tarde", time(14, 0)),
        ("10 da manhã", time(10, 0)),
        ("meio-dia", time(12, 0)),
        ("25h", None),
        ("14:75", None),
        ("de tarde", None),
        ("143", None),
    ],
)
def test_parse_time(raw: str, expected: time | None) -> None:
    assert parse_time(normalize(raw)) == expected


# --- nome --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("maria silva", "Maria Silva"),
        ("  JOÃO   DA   SILVA ", "João da Silva"),
        ("ana de oliveira e souza", "Ana de Oliveira e Souza"),
        ("Pedro D'Ávila", "Pedro D'ávila"),
        ("Maria", None),
        ("Maria 2", None),
        ("a b", None),
        ("x" * 81, None),
        ("Maria@Silva", None),
    ],
)
def test_parse_name(raw: str, expected: str | None) -> None:
    assert parse_name(raw) == expected


# --- sim/não -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sim", True),
        ("Sim, pode confirmar", True),
        ("S", True),
        ("isso mesmo", True),
        ("confirmo", True),
        ("pode", True),
        ("ok", True),
        ("👍", None),
        ("não", False),
        ("Nao quero", False),
        ("n", False),
        ("negativo", False),
        ("talvez", None),
    ],
)
def test_parse_yes_no(raw: str, expected: bool | None) -> None:
    assert parse_yes_no(normalize(raw)) is expected
