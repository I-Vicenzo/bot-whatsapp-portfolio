"""Autenticação do painel: senha com Argon2 e sessão em cookie assinado.

CSRF: cookie SameSite=Strict, corpo JSON obrigatório e checagem do cabeçalho Origin nas
requisições que alteram estado.
"""

import logging
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import Clock
from app.config import Settings
from app.db.models import User

logger = logging.getLogger(__name__)

COOKIE_NAME = "admin_session"
SESSION_MAX_AGE = 12 * 60 * 60
DEFAULT_PASSWORD = "admin"  # noqa: S105 - só fora de produção, exibida na tela de login

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


class SessionCodec:
    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt="admin-session")

    def dumps(self, user_id: int) -> str:
        return self._serializer.dumps({"uid": user_id})

    def loads(self, token: str) -> int | None:
        try:
            data = self._serializer.loads(token, max_age=SESSION_MAX_AGE)
        except BadSignature:
            return None
        uid = data.get("uid") if isinstance(data, dict) else None
        return uid if isinstance(uid, int) else None


def initial_password(settings: Settings) -> str:
    if settings.admin_password is not None:
        return settings.admin_password.get_secret_value()
    return DEFAULT_PASSWORD


def uses_default_password(settings: Settings) -> bool:
    return settings.admin_password is None


def ensure_initial_user(session: Session, settings: Settings, clock: Clock) -> None:
    """Cria o primeiro usuário do painel (ADMIN_USERNAME/ADMIN_PASSWORD) se não houver
    nenhum. Depois disso, mudar as variáveis não altera usuários existentes."""
    if session.scalar(select(func.count(User.id))):
        return
    session.add(
        User(
            username=settings.admin_username,
            password_hash=hash_password(initial_password(settings)),
            name="Atendente",
            created_at=clock.local_now(),
        )
    )
    session.flush()
    logger.info("Usuário inicial do painel criado: %s", settings.admin_username)


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        verify_password(hash_password("x"), password)  # tempo parecido com usuário existente
        return None
    return user if verify_password(user.password_hash, password) else None


def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE_NAME)
    codec: SessionCodec = request.app.state.session_codec
    user_id = codec.loads(token) if token else None
    if user_id is not None:
        with request.app.state.session_factory() as session:
            user = session.get(User, user_id)
            if user is not None:
                return user
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Faça login.")


def check_origin(request: Request) -> None:
    """Bloqueia requisições de outra origem (defesa extra contra CSRF)."""
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Origem não permitida.")
