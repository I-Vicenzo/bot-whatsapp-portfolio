"""IA de reserva: responde perguntas fora do roteiro com os dados da clínica.

Nunca executa ações. Sem resposta nos dados, devolve o marcador e o bot encaminha para um
humano; erro ou timeout seguem o mesmo caminho da IA desligada ("não entendi").
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.client import LLMClient, LLMError
from app.ai.prompt import HANDOFF_MARKER, build_system_prompt
from app.clinic import ClinicData
from app.clock import Clock
from app.db.models import Conversation, LLMCall, LLMOutcome
from app.nlu.normalize import normalize

logger = logging.getLogger(__name__)
MAX_ANSWER_CHARS = 1000
# A IA não executa ações; se a resposta afirmar que executou, ela não chega ao cliente.
_CLAIMS_ACTION = re.compile(
    r"\b(agendei|marquei|remarquei|desmarquei|cancelei|confirmei|reservei)\b"
)


class AIStatus(StrEnum):
    ANSWERED = "answered"
    HANDOFF = "handoff"
    ERROR = "error"
    LIMITED = "limited"  # limites de uso: não chama o provedor
    SKIPPED = "skipped"  # pergunta longa demais


@dataclass(frozen=True)
class AILimits:
    max_question_chars: int = 300
    calls_per_contact_per_hour: int = 10
    daily_cap: int = 200


@dataclass(frozen=True)
class AIResult:
    status: AIStatus
    text: str | None = None


@dataclass(frozen=True)
class AIExchange:
    """Resultado bruto da chamada ao provedor, ainda não gravado no banco."""

    raw: str | None  # None quando a chamada falhou
    latency_ms: int


class AIResponder:
    def __init__(
        self, llm: LLMClient, clinic: ClinicData, clock: Clock, limits: AILimits | None = None
    ) -> None:
        self.llm = llm
        self.clock = clock
        self.limits = limits or AILimits()
        self.system_prompt = build_system_prompt(clinic)

    # Três partes para que a de rede (`ask`) rode fora da transação: no SQLite a transação de
    # escrita trava o banco inteiro, e o provedor pode levar segundos para responder.

    def answer(self, session: Session, conversation: Conversation, question: str) -> AIResult:
        """As três partes de uma vez, na mesma sessão (testes e scripts)."""
        blocked = self.precheck(session, conversation, question)
        if blocked is not None:
            return blocked
        return self.settle(session, conversation, self.ask(question))

    def precheck(
        self, session: Session, conversation: Conversation, question: str
    ) -> AIResult | None:
        """Pergunta vazia ou longa, ou limite de uso estourado: não chama o provedor."""
        question = question.strip()
        if not question or len(question) > self.limits.max_question_chars:
            return AIResult(AIStatus.SKIPPED)
        if self._over_limits(session, conversation):
            return AIResult(AIStatus.LIMITED)
        return None

    def ask(self, question: str) -> AIExchange:
        """Só a chamada ao provedor; não toca no banco."""
        started = time.perf_counter()
        raw: str | None
        try:
            raw = self.llm.complete(self.system_prompt, question.strip())
        except LLMError as exc:
            logger.warning("IA: falha na chamada (%s)", exc)
            raw = None
        return AIExchange(raw, int((time.perf_counter() - started) * 1000))

    def settle(
        self, session: Session, conversation: Conversation, exchange: AIExchange
    ) -> AIResult:
        """Filtra a resposta e grava o registro de uso."""
        if exchange.raw is None:
            self._record(session, conversation, exchange.latency_ms, LLMOutcome.ERROR)
            return AIResult(AIStatus.ERROR)
        text = exchange.raw.strip()
        if _CLAIMS_ACTION.search(normalize(text)):
            logger.warning("IA: resposta descartada por afirmar uma ação que não executa")
            text = ""
        if not text or HANDOFF_MARKER in text:
            self._record(session, conversation, exchange.latency_ms, LLMOutcome.HANDOFF)
            return AIResult(AIStatus.HANDOFF)
        self._record(session, conversation, exchange.latency_ms, LLMOutcome.ANSWERED)
        return AIResult(AIStatus.ANSWERED, text[:MAX_ANSWER_CHARS])

    def _over_limits(self, session: Session, conversation: Conversation) -> bool:
        now = self.clock.local_now()
        recent = session.scalar(
            select(func.count(LLMCall.id)).where(
                LLMCall.conversation_id == conversation.id,
                LLMCall.created_at > now - timedelta(hours=1),
            )
        )
        if (recent or 0) >= self.limits.calls_per_contact_per_hour:
            return True
        start_of_day = now.replace(hour=0, minute=0, second=0)
        today = session.scalar(
            select(func.count(LLMCall.id)).where(LLMCall.created_at >= start_of_day)
        )
        return (today or 0) >= self.limits.daily_cap

    def _record(
        self, session: Session, conversation: Conversation, latency_ms: int, outcome: LLMOutcome
    ) -> None:
        session.add(
            LLMCall(
                conversation_id=conversation.id,
                created_at=self.clock.local_now(),
                latency_ms=latency_ms,
                outcome=outcome,
            )
        )
        session.flush()
