"""Dados do negócio (clínica), carregados de `data/clinica.yaml`."""

from app.clinic.loader import ClinicDataError, load_clinic
from app.clinic.models import ClinicData, Closure, FaqEntry, SpecialtyInfo, TimeRange

__all__ = [
    "ClinicData",
    "ClinicDataError",
    "Closure",
    "FaqEntry",
    "SpecialtyInfo",
    "TimeRange",
    "load_clinic",
]
