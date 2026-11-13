"""API do painel do atendente."""

from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from app.admin.auth import (
    COOKIE_NAME,
    DEFAULT_PASSWORD,
    SESSION_MAX_AGE,
    SessionCodec,
    authenticate,
    check_origin,
    current_user,
    uses_default_password,
)
from app.admin.metrics import compute_metrics
from app.config import AppEnv, Settings
from app.conversation.service import (
    ConversationNotFound,
    ConversationService,
    DeliveryError,
    WindowClosed,
)
from app.db.models import (
    Appointment,
    AppointmentStatus,
    Conversation,
    ConversationStatus,
    Message,
    Priority,
    Specialty,
    User,
)
from app.ratelimit import RateLimiter
from app.scheduling import AppointmentNotFound, Scheduler

router = APIRouter(prefix="/api/admin", tags=["painel"])
MAX_CONVERSATIONS = 200
CurrentUser = Annotated[User, Depends(current_user)]
SameOrigin = Depends(check_origin)


# --- modelos -----------------------------------------------------------------------------


class LoginIn(BaseModel):
    username: Annotated[str, Field(min_length=1, max_length=60)]
    password: Annotated[str, Field(min_length=1, max_length=200)]


class UserOut(BaseModel):
    username: str
    name: str


class LoginInfo(BaseModel):
    demo_username: str | None
    demo_password: str | None


class LastMessage(BaseModel):
    author: str
    text: str
    created_at: datetime


class ConversationOut(BaseModel):
    id: int
    channel: str
    contact: str
    display_name: str | None
    status: str
    priority: str
    handoff_reason: str | None
    handoff_at: datetime | None
    waiting_minutes: int | None
    assigned_to: str | None
    last_message: LastMessage | None


class MessageOut(BaseModel):
    id: int
    author: str
    text: str
    created_at: datetime


class ConversationDetail(ConversationOut):
    window_open: bool
    messages: list[MessageOut]


class ReplyIn(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=2000)]


class AppointmentOut(BaseModel):
    id: int
    starts_at: datetime
    specialty: str
    professional: str
    patient_name: str
    patient_phone: str
    status: str
    channel: str


class SpecialtyOut(BaseModel):
    slug: str
    name: str


# --- helpers -----------------------------------------------------------------------------


def _session(request: Request) -> Session:
    return request.app.state.session_factory()


def _service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


def _now(request: Request) -> datetime:
    return request.app.state.clock.local_now()


def _conversation_rows(
    session: Session, *where: object, latest_first: bool = False, limit: int | None = None
) -> list[tuple]:
    # Subconsulta correlacionada: usa o índice (conversation_id, id) de `messages` por
    # conversa, em vez de agrupar a tabela inteira de mensagens a cada atualização do painel.
    last_id = (
        select(func.max(Message.id))
        .where(Message.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )
    last = aliased(Message)
    query = (
        select(Conversation, last, User.name)
        .select_from(Conversation)
        .outerjoin(last, last.id == last_id)
        .outerjoin(User, User.id == Conversation.assigned_to)
        .where(*where)  # type: ignore[arg-type]
    )
    if latest_first:
        query = query.order_by(last.id.desc().nulls_last(), Conversation.id.desc())
    if limit is not None:
        query = query.limit(limit)
    return list(session.execute(query).all())


def _to_out(row: tuple, now: datetime) -> ConversationOut:
    conversation, last, assigned_name = row
    waiting = None
    if conversation.status == ConversationStatus.WAITING_HUMAN and conversation.handoff_at:
        waiting = int((now - conversation.handoff_at).total_seconds() // 60)
    return ConversationOut(
        id=conversation.id,
        channel=conversation.channel,
        contact=conversation.contact,
        display_name=conversation.display_name,
        status=conversation.status,
        priority=conversation.priority,
        handoff_reason=conversation.handoff_reason,
        handoff_at=conversation.handoff_at,
        waiting_minutes=waiting,
        assigned_to=assigned_name,
        last_message=LastMessage(author=last.author, text=last.text, created_at=last.created_at)
        if last
        else None,
    )


def _order(items: list[ConversationOut]) -> list[ConversationOut]:
    """Fila primeiro (urgentes, depois quem espera há mais tempo); o resto por atividade."""
    waiting = [i for i in items if i.status == ConversationStatus.WAITING_HUMAN]
    others = [i for i in items if i.status != ConversationStatus.WAITING_HUMAN]
    waiting.sort(
        key=lambda i: (i.priority != Priority.URGENT, i.handoff_at is None, i.handoff_at or 0)
    )
    others.sort(
        key=lambda i: i.last_message.created_at.isoformat() if i.last_message else "", reverse=True
    )
    return waiting + others


# --- sessão ------------------------------------------------------------------------------


@router.get("/login-info", summary="Credenciais de demonstração (só no modo demo)")
def login_info(request: Request) -> LoginInfo:
    settings: Settings = request.app.state.settings
    if settings.app_env is AppEnv.DEV and uses_default_password(settings):
        return LoginInfo(demo_username=settings.admin_username, demo_password=DEFAULT_PASSWORD)
    if settings.public_demo and settings.admin_password is not None:
        # Demo pública: o login é parte da vitrine (o app recusa iniciar com WhatsApp ligado).
        return LoginInfo(
            demo_username=settings.admin_username,
            demo_password=settings.admin_password.get_secret_value(),
        )
    return LoginInfo(demo_username=None, demo_password=None)


@router.post("/login", dependencies=[SameOrigin])
def login(body: LoginIn, request: Request, response: Response) -> UserOut:
    limiter: RateLimiter = request.app.state.login_limiter
    if not limiter.allow(request.client.host if request.client else "unknown"):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Muitas tentativas. Aguarde um minuto."
        )
    with _session(request) as session:
        user = authenticate(session, body.username, body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário ou senha inválidos.")
    codec: SessionCodec = request.app.state.session_codec
    settings: Settings = request.app.state.settings
    response.set_cookie(
        COOKIE_NAME,
        codec.dumps(user.id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=settings.app_env is AppEnv.PRODUCTION,
        path="/",
    )
    return UserOut(username=user.username, name=user.name)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[SameOrigin])
def logout(response: Response) -> Response:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut(username=user.username, name=user.name)


# --- conversas ---------------------------------------------------------------------------


@router.get("/conversations", summary="Fila e lista de conversas")
def list_conversations(
    request: Request,
    _user: CurrentUser,
    status_filter: Annotated[ConversationStatus | None, Query(alias="status")] = None,
) -> list[ConversationOut]:
    where = [Conversation.status == status_filter] if status_filter else []
    now = _now(request)
    waiting = Conversation.status == ConversationStatus.WAITING_HUMAN
    with _session(request) as session:
        # A fila vem inteira (é curta e precisa da ordem de prioridade); o resto, só as mais
        # recentes, com ORDER BY e LIMIT no banco.
        rows = _conversation_rows(session, *where, waiting, limit=MAX_CONVERSATIONS)
        rows += _conversation_rows(
            session,
            *where,
            ~waiting,
            latest_first=True,
            limit=max(MAX_CONVERSATIONS - len(rows), 0),
        )
        items = [_to_out(row, now) for row in rows]
    return _order(items)[:MAX_CONVERSATIONS]


@router.get("/conversations/{conversation_id}")
def get_conversation(
    conversation_id: int, request: Request, _user: CurrentUser
) -> ConversationDetail:
    with _session(request) as session:
        rows = _conversation_rows(session, Conversation.id == conversation_id)
        if not rows:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada.")
        messages = session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(300)
        ).all()
        summary = _to_out(rows[0], _now(request))
        return ConversationDetail(
            **summary.model_dump(),
            window_open=_service(request).window_open(rows[0][0]),
            messages=[
                MessageOut(id=m.id, author=m.author, text=m.text, created_at=m.created_at)
                for m in reversed(messages)
            ],
        )


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada.")


@router.post(
    "/conversations/{conversation_id}/assign",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[SameOrigin],
)
def assign(conversation_id: int, request: Request, user: CurrentUser) -> Response:
    try:
        _service(request).assign(conversation_id, user.id)
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/reply", dependencies=[SameOrigin])
def reply(conversation_id: int, body: ReplyIn, request: Request, user: CurrentUser) -> MessageOut:
    try:
        message = _service(request).agent_reply(conversation_id, user.id, body.text.strip())
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc
    except WindowClosed as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Passaram 24 h desde a última mensagem do cliente: o WhatsApp só permite "
            "mensagens de template aprovado nesse caso.",
        ) from exc
    except DeliveryError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "O WhatsApp não aceitou a mensagem. Tente de novo."
        ) from exc
    return MessageOut(
        id=message.id, author=message.author, text=message.text, created_at=message.created_at
    )


@router.post(
    "/conversations/{conversation_id}/release",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[SameOrigin],
)
def release(conversation_id: int, request: Request, _user: CurrentUser) -> Response:
    try:
        _service(request).release(conversation_id)
    except ConversationNotFound as exc:
        raise _not_found(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- agenda ------------------------------------------------------------------------------


@router.get("/specialties")
def list_specialties(request: Request, _user: CurrentUser) -> list[SpecialtyOut]:
    with _session(request) as session:
        rows = session.scalars(select(Specialty).order_by(Specialty.name)).all()
        return [SpecialtyOut(slug=s.slug, name=s.name) for s in rows]


@router.get("/appointments")
def list_appointments(
    request: Request,
    _user: CurrentUser,
    day: Annotated[date | None, Query(alias="date")] = None,
    specialty: str | None = None,
    status_filter: Annotated[AppointmentStatus | None, Query(alias="status")] = None,
) -> list[AppointmentOut]:
    query = select(Appointment).join(Specialty)
    if day is not None:
        start = datetime.combine(day, datetime.min.time())
        query = query.where(
            Appointment.starts_at >= start, Appointment.starts_at < start + timedelta(days=1)
        )
    else:
        today = datetime.combine(_now(request).date(), datetime.min.time())
        query = query.where(Appointment.starts_at >= today)
    if specialty:
        query = query.where(Specialty.slug == specialty)
    if status_filter:
        query = query.where(Appointment.status == status_filter)
    query = query.order_by(
        Appointment.starts_at,
        case((Appointment.status == AppointmentStatus.CONFIRMED, 0), else_=1),
    ).limit(300)
    with _session(request) as session:
        return [
            AppointmentOut(
                id=a.id,
                starts_at=a.starts_at,
                specialty=a.specialty.name,
                professional=a.specialty.professional,
                patient_name=a.patient_name,
                patient_phone=a.patient_phone,
                status=a.status,
                channel=a.channel,
            )
            for a in session.scalars(query)
        ]


@router.post(
    "/appointments/{appointment_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[SameOrigin],
)
def cancel_appointment(appointment_id: int, request: Request, _user: CurrentUser) -> Response:
    scheduler: Scheduler = request.app.state.scheduler
    try:
        with request.app.state.session_factory.begin() as session:
            scheduler.cancel_by_staff(session, appointment_id)
    except AppointmentNotFound as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Consulta não encontrada ou já cancelada."
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- métricas ----------------------------------------------------------------------------


class MetricsOut(BaseModel):
    days: int
    conversations: int
    resolved_by_bot: int
    resolved_rate: float | None
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


@router.get("/metrics", summary="Indicadores do atendimento nos últimos N dias")
def metrics(
    request: Request,
    _user: CurrentUser,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> MetricsOut:
    end = _now(request) + timedelta(seconds=1)
    start = datetime.combine(end.date() - timedelta(days=days - 1), datetime.min.time())
    with _session(request) as session:
        result = compute_metrics(session, start, end)
    data = {k: v for k, v in result.__dict__.items() if k not in ("start", "end")}
    return MetricsOut(days=days, resolved_rate=result.resolved_rate, **data)
