"""Validação do cabeçalho X-Hub-Signature-256 enviado pela Meta."""

import hashlib
import hmac

PREFIX = "sha256="


def sign(body: bytes, app_secret: str) -> str:
    digest = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return PREFIX + digest


def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    """HMAC-SHA256 do corpo **bruto** com o App Secret, comparado em tempo constante."""
    if not header or not header.startswith(PREFIX):
        return False
    return hmac.compare_digest(sign(body, app_secret), header)
