"""Métricas do atendimento para o painel."""

from dataclasses import dataclass
from datetime import datetime
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Appointment,
    AppointmentStatus,
    Channel,
    Handoff,
    LLMCall,
    LLMOutcome,
    Message,
    Priority,
)


@dataclass(frozen=True)
class Metrics:
    start: datetime
    end: datetime
    conversations: int
    resolved_by_bot: int
    handoffs: int
    urgent_handoffs: int
    handoffs_answered: int
    median_minutes_to_human: float | None
    max_minutes_to_human: float | None
    ai_answered: int
    ai_handoffs: int
    ai_errors: int
    ai_median_latency_ms: int | None
    appointments_by_bot: int
    appointments_by_staff: int
    cancellations: int

    @property
    def resolved_rate(self) -> float | None:
        return self.resolved_by_bot / self.conversations if self.conversations else None


def compute_metrics(session: Session, start: datetime, end: datetime) -> Metrics:
    active = set(
        session.scalars(
            select(Message.conversation_id)
            .where(Message.direction == "in", Message.created_at >= start, Message.created_at < end)
            .distinct()
        )
    )
    handoffs = session.scalars(
        select(Handoff).where(Handoff.requested_at >= start, Handoff.requested_at < end)
    ).all()
    handed_off = {h.conversation_id for h in handoffs}
    waits = [
        (h.first_response_at - h.requested_at).total_seconds() / 60
        for h in handoffs
        if h.first_response_at is not None
    ]

    llm_rows = session.execute(
        select(LLMCall.outcome, LLMCall.latency_ms).where(
            LLMCall.created_at >= start, LLMCall.created_at < end
        )
    ).all()
    latencies = [latency for _, latency in llm_rows]

    def count_llm(outcome: LLMOutcome) -> int:
        return sum(1 for o, _ in llm_rows if o == outcome)

    created = dict(
        session.execute(
            select(Appointment.channel, func.count(Appointment.id))
            .where(Appointment.created_at >= start, Appointment.created_at < end)
            .group_by(Appointment.channel)
        ).all()
    )
    cancellations = session.scalar(
        select(func.count(Appointment.id)).where(
            Appointment.status == AppointmentStatus.CANCELLED,
            Appointment.cancelled_at >= start,
            Appointment.cancelled_at < end,
        )
    )

    return Metrics(
        start=start,
        end=end,
        conversations=len(active),
        resolved_by_bot=len(active - handed_off),
        handoffs=len(handoffs),
        urgent_handoffs=sum(1 for h in handoffs if h.priority == Priority.URGENT),
        handoffs_answered=len(waits),
        median_minutes_to_human=round(median(waits), 1) if waits else None,
        max_minutes_to_human=round(max(waits), 1) if waits else None,
        ai_answered=count_llm(LLMOutcome.ANSWERED),
        ai_handoffs=count_llm(LLMOutcome.HANDOFF),
        ai_errors=count_llm(LLMOutcome.ERROR),
        ai_median_latency_ms=int(median(latencies)) if latencies else None,
        appointments_by_bot=created.get(Channel.WHATSAPP, 0) + created.get(Channel.SIMULATOR, 0),
        appointments_by_staff=created.get(Channel.ADMIN, 0),
        cancellations=cancellations or 0,
    )
