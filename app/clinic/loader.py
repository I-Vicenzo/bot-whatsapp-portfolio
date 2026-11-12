"""Carga e validação do arquivo YAML da clínica."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.clinic.models import ClinicData


class ClinicDataError(Exception):
    """Arquivo da clínica ausente ou inválido. A aplicação não deve iniciar."""


def load_clinic(path: Path) -> ClinicData:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ClinicDataError(f"Arquivo de dados da clínica não encontrado: {path}") from None
    except yaml.YAMLError as exc:
        raise ClinicDataError(f"YAML inválido em {path}:\n{exc}") from None

    if not isinstance(raw, dict):
        raise ClinicDataError(f"{path}: o arquivo deve conter um mapeamento (chave: valor)")

    try:
        return ClinicData.model_validate(raw)
    except ValidationError as exc:
        problems = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc']) or '(raiz)'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ClinicDataError(f"Dados da clínica inválidos em {path}:\n{problems}") from None
