"""Dados do negócio (clínica), carregados de `data/clinica.yaml`."""

from app.clinic.loader import ClinicDataError, load_clinic
from app.clinic.models import ClinicData, FaqEntry, SpecialtyInfo, TimeRange

__all__ = [
    "ClinicData",
    "ClinicDataError",
    "FaqEntry",
    "SpecialtyInfo",
    "TimeRange",
    "load_clinic",
]
