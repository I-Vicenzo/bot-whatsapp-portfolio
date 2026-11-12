"""Parsers de respostas do cliente: opção numerada, data, hora, nome e sim/não.

Recebem texto normalizado (exceto `parse_name`, que preserva acentos e maiúsculas).
"""

import re
from datetime import date, time, timedelta

_NUMBER_WORDS = {
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
}
_OPTION = re.compile(r"^(?:(?:opcao|op|numero|n)\s*)?(\d{1,2})$|^(?:(?:opcao|numero)\s+)?([a-z]+)$")


def parse_option(text: str, count: int) -> int | None:
    """Número de opção de 1 a `count` ("2", "opção 2", "dois")."""
    match = _OPTION.match(text)
    if not match:
        return None
    digits, word = match.groups()
    number = int(digits) if digits else _NUMBER_WORDS.get(word)
    return number if number is not None and 1 <= number <= count else None


# --- datas -------------------------------------------------------------------------------

_WEEKDAYS = {
    "segunda": 0,
    "seg": 0,
    "terca": 1,
    "ter": 1,
    "quarta": 2,
    "qua": 2,
    "quinta": 3,
    "qui": 3,
    "sexta": 4,
    "sex": 4,
    "sabado": 5,
    "sab": 5,
    "domingo": 6,
    "dom": 6,
}
_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "janeiro",
            "fevereiro",
            "marco",
            "abril",
            "maio",
            "junho",
            "julho",
            "agosto",
            "setembro",
            "outubro",
            "novembro",
            "dezembro",
        ),
        start=1,
    )
}

_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b")
_WRITTEN_DATE = re.compile(rf"\b(\d{{1,2}}) de ({'|'.join(_MONTHS)})(?: de (\d{{4}}))?\b")
_DAY_ONLY = re.compile(r"\bdia (\d{1,2})\b")
_WEEKDAY = re.compile(rf"\b(proxim[ao] )?({'|'.join(_WEEKDAYS)})(?: feira)?\b")


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _without_year(month: int, day: int, today: date) -> date | None:
    """Data sem ano: a próxima ocorrência (hoje ou depois)."""
    candidate = _safe_date(today.year, month, day)
    if candidate is not None and candidate < today:
        candidate = _safe_date(today.year + 1, month, day)
    return candidate


def parse_date(text: str, today: date) -> date | None:
    """hoje, amanhã, depois de amanhã, dia da semana, dd/mm, dd/mm/aaaa, "15 de novembro",
    "dia 15". Datas sem ano ou sem mês viram a próxima ocorrência."""
    if re.search(r"\bdepois de amanha\b", text):
        return today + timedelta(days=2)
    if re.search(r"\bamanha\b", text):
        return today + timedelta(days=1)
    if re.search(r"\bhoje\b", text):
        return today

    if match := _NUMERIC_DATE.search(text):
        day, month, year = match.groups()
        if year is None:
            return _without_year(int(month), int(day), today)
        full_year = int(year) + 2000 if len(year) == 2 else int(year)
        return _safe_date(full_year, int(month), int(day))

    if match := _WRITTEN_DATE.search(text):
        day, month_name, year = match.groups()
        if year is None:
            return _without_year(_MONTHS[month_name], int(day), today)
        return _safe_date(int(year), _MONTHS[month_name], int(day))

    if match := _DAY_ONLY.search(text):
        day = int(match.group(1))
        this_month = _safe_date(today.year, today.month, day)
        if this_month is not None and this_month >= today:
            return this_month
        next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return _safe_date(next_month.year, next_month.month, day)

    if match := _WEEKDAY.search(text):
        next_week, name = match.groups()
        delta = (_WEEKDAYS[name] - today.weekday()) % 7
        if next_week and delta == 0:
            delta = 7
        return today + timedelta(days=delta)

    return None


# --- horas -------------------------------------------------------------------------------

_TIME = re.compile(
    r"\b(\d{1,2})(?:\s*(?::|h|hs|hr|hrs|horas?)\s*(\d{2})?)?(?:\s*(?:min|minutos))?\b"
    r"(?:\s*(?:da|de) (manha|tarde|noite))?"
)


def parse_time(text: str) -> time | None:
    """ "14", "14h", "14:30", "14h30", "2 da tarde", "meio dia"."""
    if re.search(r"\bmeio dia\b", text):
        return time(12, 0)
    match = _TIME.search(text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if match.group(3) in ("tarde", "noite") and hour < 12:
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


# --- nome --------------------------------------------------------------------------------

_NAME_CHARS = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ' -]+")
_PARTICLES = {"da", "de", "do", "das", "dos", "e"}


def parse_name(raw: str) -> str | None:
    """Nome e sobrenome, só letras. Devolve com capitalização padronizada."""
    name = " ".join(raw.split())
    if not 3 <= len(name) <= 80 or not _NAME_CHARS.fullmatch(name):
        return None
    words = name.split(" ")
    if len(words) < 2 or sum(c.isalpha() for c in name) < 4:
        return None
    return " ".join(
        word.lower()
        if word.lower() in _PARTICLES and i > 0
        else word[:1].upper() + word[1:].lower()
        for i, word in enumerate(words)
    )


# --- sim/não -----------------------------------------------------------------------------

_NO = re.compile(r"^(nao|n|negativo|errado|nem|nunca|melhor nao)\b")
_YES = re.compile(
    r"^(sim|s|ss|isso|confirm\w*|pode|ok|okay|claro|certo|positivo|correto|exato"
    r"|beleza|blz|bora|com certeza|quero|perfeito|fechado)\b"
)


def parse_yes_no(text: str) -> bool | None:
    if _NO.search(text):
        return False
    if _YES.search(text):
        return True
    return None
