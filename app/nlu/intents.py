"""Intenções reconhecidas por regex sobre texto normalizado (ver `normalize`).

A ordem da tabela é a prioridade: a primeira intenção que casa vence.
"""

import re
from enum import StrEnum


class Intent(StrEnum):
    URGENT = "urgent"
    HUMAN = "human"
    MENU = "menu"
    CANCEL = "cancel"
    MY_APPOINTMENTS = "my_appointments"
    BOOK = "book"
    ADDRESS = "address"
    HOURS = "hours"
    INSURANCE = "insurance"
    PRICES = "prices"
    SPECIALTIES = "specialties"
    GREETING = "greeting"
    THANKS = "thanks"


GLOBAL_INTENTS = (Intent.URGENT, Intent.HUMAN, Intent.MENU)
# CANCEL antes: "cancela minha consulta" também casa com MY_APPOINTMENTS.
ACTION_INTENTS = (Intent.CANCEL, Intent.MY_APPOINTMENTS, Intent.BOOK)
INFO_INTENTS = (
    Intent.ADDRESS,
    Intent.HOURS,
    Intent.INSURANCE,
    Intent.PRICES,
    Intent.SPECIALTIES,
)
SOCIAL_INTENTS = (Intent.GREETING, Intent.THANKS)


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


_PATTERNS: dict[Intent, re.Pattern[str]] = {
    Intent.URGENT: _rx(r"\b(urgen(te|tes|cia|cias)|emergen(te|cia|cias)|socorro)\b"),
    Intent.HUMAN: _rx(
        r"\b(atende(n)?tes?|humano|pessoa de verdade|recepcionista"
        r"|falar com (alguem|uma pessoa|algum atendente|a recepcao|voces))\b"
    ),
    Intent.MENU: _rx(r"^(menu|inicio|sair|0)$|\bmenu\b|^voltar ao (menu|inicio)$"),
    Intent.MY_APPOINTMENTS: _rx(
        r"\b(minhas? consultas?|meus? agendamentos?|minhas? marcac\w*"
        r"|ver (as |minhas )?consultas?|tenho (alguma )?consulta"
        r"|consultas? (agendadas?|marcadas?))\b"
    ),
    Intent.CANCEL: _rx(r"\b(cancel\w*|desmarc\w*)\b"),
    Intent.BOOK: _rx(
        r"\b(ag[ae]nd\w*|marcar|nova consulta|tem (horario|vaga)s?"
        r"|(quero|queria|gostaria de|preciso de|preciso) (uma |de uma )?consulta)\b"
    ),
    Intent.ADDRESS: _rx(
        r"\b(endere[cs]o|onde (fica|ficam|voces ficam|e a clinica)|localizac\w*"
        r"|como (eu )?chego|como chegar|mapa)\b"
    ),
    Intent.HOURS: _rx(
        r"\b(horarios?|funcionamento|que horas (abre|fecha|abrem|fecham)"
        r"|abre|abrem|fecha|fecham|aberto|aberta)\b"
    ),
    Intent.INSURANCE: _rx(r"\b(convenios?|planos? de saude|planos?)\b"),
    Intent.PRICES: _rx(r"\b(valor(es)?|precos?|quanto (custa|e|fica|sai)|custa|particular)\b"),
    Intent.SPECIALTIES: _rx(r"\b(especialidades?|medic[oa]s?|especialistas?|doutor(es|a|as)?)\b"),
    Intent.GREETING: _rx(r"^(oi+e?|ola|opa|bom dia|boa tarde|boa noite|e ai|hey|hello|alo)\b"),
    Intent.THANKS: _rx(r"\b(obrigad[oa]s?|valeu|agradec\w*|brigad[oa])\b"),
}

_NEGATED_URGENCY = _rx(r"\bnao (e |eh |e nada |tem )?(urgen|emergen)")


def matches(intent: Intent, text: str) -> bool:
    if intent is Intent.URGENT and _NEGATED_URGENCY.search(text):
        return False
    return _PATTERNS[intent].search(text) is not None


def detect(text: str, candidates: tuple[Intent, ...] = tuple(Intent)) -> Intent | None:
    """Primeira intenção de `candidates` (na ordem dada) que casa com o texto normalizado."""
    return next((intent for intent in candidates if matches(intent, text)), None)
