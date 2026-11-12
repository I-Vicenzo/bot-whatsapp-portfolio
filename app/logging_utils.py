"""Utilitários de log: dados pessoais nunca aparecem completos."""


def mask_phone(phone: str) -> str:
    """'5547999990001' -> '5547*****0001'."""
    if len(phone) <= 8:
        return "*" * len(phone)
    return phone[:4] + "*" * (len(phone) - 8) + phone[-4:]
