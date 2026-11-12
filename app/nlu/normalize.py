"""Normalização de texto em português para o reconhecimento por regex."""

import re
import unicodedata

_NON_TEXT = re.compile(r"[^\w\s:/]")


def normalize(text: str) -> str:
    """Minúsculas, sem acentos, sem pontuação (mantém `:` e `/` de horas e datas), espaços
    colapsados.

    >>> normalize("  Olá, GOSTARIA de agendar às 14:30!  ")
    'ola gostaria de agendar as 14:30'
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = _NON_TEXT.sub(" ", without_accents.lower()).replace("_", " ")
    return " ".join(cleaned.split())


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    """`phrase` (já normalizada) aparece como palavra(s) inteira(s) no texto."""
    return re.search(rf"\b{re.escape(phrase)}\b", normalized_text) is not None
