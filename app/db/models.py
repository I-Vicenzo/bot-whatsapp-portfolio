"""Modelos ORM. Espelham a migração inicial (migrations/versions/0001_initial.py), que é a
fonte da verdade das regras do banco, incluindo os triggers.

Horários: "horário de parede" da clínica, sem offset (ver app/clock.py).
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.dialects.sqlite import DATETIME as SQLITE_DATETIME
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Sem microssegundos: o texto gravado é exatamente o que os CHECKs e triggers esperam.
LocalDateTime = DateTime().with_variant(
    SQLITE_DATETIME(
        storage_format="%(year)04d-%(month)02d-%(day)02d %(hour)02d:%(minute)02d:%(second)02d",
        regexp=r"(\d+)-(\d+)-(\d+) (\d+):(\d+):(\d+)",
    ),
    "sqlite",
)

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AppointmentStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    SIMULATOR = "simulator"
    ADMIN = "admin"


class ConversationStatus(StrEnum):
    BOT = "bot"
    WAITING_HUMAN = "waiting_human"
    HUMAN = "human"


class Priority(StrEnum):
    NORMAL = "normal"
    URGENT = "urgent"


class Author(StrEnum):
    CUSTOMER = "customer"
    BOT = "bot"
    AI = "ai"
    AGENT = "agent"


def _in(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


class Specialty(Base):
    __tablename__ = "specialties"
    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="price_non_negative"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_empty"),
        CheckConstraint("length(trim(slug)) > 0", name="slug_not_empty"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    professional: Mapped[str] = mapped_column(String(100))
    price_cents: Mapped[int]
    active: Mapped[bool] = mapped_column(default=True, server_default=true())


class BusinessHour(Base):
    __tablename__ = "business_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint(
            "open_min >= 0 AND close_min <= 1440 AND open_min < close_min", name="interval"
        ),
        CheckConstraint("open_min % 30 = 0 AND close_min % 30 = 0", name="slot_aligned"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    weekday: Mapped[int] = mapped_column(comment="0 = segunda ... 6 = domingo")
    open_min: Mapped[int]
    close_min: Mapped[int]


class ScheduleSettings(Base):
    __tablename__ = "schedule_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("min_advance_minutes >= 0", name="min_advance_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    min_advance_minutes: Mapped[int]


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(
            "starts_at = coalesce(strftime('%Y-%m-%d %H:%M:%S', starts_at), '')",
            name="starts_at_format",
        ),
        CheckConstraint(
            "coalesce(strftime('%M:%S', starts_at), '') IN ('00:00', '30:00')",
            name="starts_at_slot",
        ),
        CheckConstraint(_in("status", AppointmentStatus), name="status_valid"),
        CheckConstraint(_in("channel", Channel), name="channel_valid"),
        CheckConstraint("length(trim(patient_name)) > 0", name="patient_name_not_empty"),
        CheckConstraint("length(trim(patient_phone)) > 0", name="patient_phone_not_empty"),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)", name="cancelled_at_consistent"
        ),
        Index(
            "uq_appointments_specialty_slot",
            "specialty_id",
            "starts_at",
            unique=True,
            sqlite_where=text("status = 'confirmed'"),
        ),
        Index(
            "uq_appointments_patient_slot",
            "patient_phone",
            "starts_at",
            unique=True,
            sqlite_where=text("status = 'confirmed'"),
        ),
        Index("ix_appointments_starts_at", "starts_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    specialty_id: Mapped[int] = mapped_column(ForeignKey("specialties.id"))
    patient_phone: Mapped[str] = mapped_column(String(32))
    patient_name: Mapped[str] = mapped_column(String(120))
    starts_at: Mapped[datetime] = mapped_column(LocalDateTime)
    status: Mapped[str] = mapped_column(
        String(16), default=AppointmentStatus.CONFIRMED, server_default="confirmed"
    )
    channel: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(LocalDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(LocalDateTime)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(LocalDateTime)

    specialty: Mapped[Specialty] = relationship(lazy="joined")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(LocalDateTime)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("channel", "contact"),
        CheckConstraint(_in("channel", Channel), name="channel_valid"),
        CheckConstraint(_in("status", ConversationStatus), name="status_valid"),
        CheckConstraint(_in("priority", Priority), name="priority_valid"),
        CheckConstraint("unknown_count >= 0", name="unknown_count_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    contact: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(16), default=ConversationStatus.BOT, server_default="bot"
    )
    state: Mapped[str] = mapped_column(String(32), default="MENU", server_default="MENU")
    context: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, server_default="{}"
    )
    priority: Mapped[str] = mapped_column(
        String(8), default=Priority.NORMAL, server_default="normal"
    )
    handoff_reason: Mapped[str | None] = mapped_column(String(200))
    handoff_at: Mapped[datetime | None] = mapped_column(LocalDateTime)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    unknown_count: Mapped[int] = mapped_column(default=0, server_default="0")
    last_inbound_at: Mapped[datetime | None] = mapped_column(LocalDateTime)
    created_at: Mapped[datetime] = mapped_column(LocalDateTime)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("direction IN ('in', 'out')", name="direction_valid"),
        CheckConstraint(_in("author", Author), name="author_valid"),
        CheckConstraint(
            "(direction = 'in') = (author = 'customer')", name="author_matches_direction"
        ),
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(String(3))
    author: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(LocalDateTime)


class LLMOutcome(StrEnum):
    ANSWERED = "answered"
    HANDOFF = "handoff"
    ERROR = "error"


class LLMCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        CheckConstraint(_in("outcome", LLMOutcome), name="outcome_valid"),
        Index("ix_llm_calls_conversation_id_created_at", "conversation_id", "created_at"),
        Index("ix_llm_calls_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(LocalDateTime)
    latency_ms: Mapped[int]
    outcome: Mapped[str] = mapped_column(String(16))


class ScheduleBlock(Base):
    """Bloqueio de agenda sincronizado do YAML (`closures`). Sem especialidade = clínica toda."""

    __tablename__ = "schedule_blocks"
    __table_args__ = (
        CheckConstraint("start_date <= end_date", name="period"),
        CheckConstraint("length(trim(reason)) > 0", name="reason_not_empty"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    specialty_id: Mapped[int | None] = mapped_column(ForeignKey("specialties.id"))
    start_date: Mapped[date]
    end_date: Mapped[date]
    reason: Mapped[str] = mapped_column(String(120))


class Handoff(Base):
    """Cada pedido de atendimento humano, com a primeira resposta do atendente (métricas)."""

    __tablename__ = "handoffs"
    __table_args__ = (
        CheckConstraint(_in("priority", Priority), name="priority_valid"),
        CheckConstraint(
            "first_response_at IS NULL OR first_response_at >= requested_at",
            name="response_after_request",
        ),
        Index("ix_handoffs_requested_at", "requested_at"),
        Index("ix_handoffs_conversation_id", "conversation_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    requested_at: Mapped[datetime] = mapped_column(LocalDateTime)
    first_response_at: Mapped[datetime | None] = mapped_column(LocalDateTime)
    priority: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(String(200))
