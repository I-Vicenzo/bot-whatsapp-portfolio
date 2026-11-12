"""Erros de domínio da agenda. O bot transforma cada um em uma mensagem amigável."""

from sqlalchemy.exc import IntegrityError


class SchedulingError(Exception):
    """Base dos erros de agenda."""


class SlotTaken(SchedulingError):
    """Horário já reservado para a especialidade (inclusive por disputa simultânea)."""


class PatientAlreadyBooked(SchedulingError):
    """O paciente (telefone) já tem consulta confirmada nesse horário."""


class OutsideBusinessHours(SchedulingError):
    """Fora do horário de funcionamento."""


class InvalidSlot(SchedulingError):
    """Horário fora da grade de 30 minutos."""


class TooSoon(SchedulingError):
    """Antes da antecedência mínima."""


class TooFarAhead(SchedulingError):
    """Além da janela de agendamento."""


class SpecialtyNotFound(SchedulingError):
    """Especialidade inexistente ou inativa."""


class AppointmentNotFound(SchedulingError):
    """Consulta inexistente, de outro paciente, já cancelada ou passada."""


# Mensagens do SQLite -> erro de domínio. Índices únicos aparecem pelas colunas; CHECKs e
# triggers, pelo nome/mensagem definidos na migração.
_DB_ERRORS: tuple[tuple[str, type[SchedulingError]], ...] = (
    ("appointments.specialty_id, appointments.starts_at", SlotTaken),
    ("uq_appointments_specialty_slot", SlotTaken),
    ("appointments.patient_phone, appointments.starts_at", PatientAlreadyBooked),
    ("uq_appointments_patient_slot", PatientAlreadyBooked),
    ("outside_business_hours", OutsideBusinessHours),
    ("too_soon", TooSoon),
    ("ck_appointments_starts_at_slot", InvalidSlot),
    ("ck_appointments_starts_at_format", InvalidSlot),
)


def translate_integrity_error(exc: IntegrityError) -> SchedulingError | None:
    message = str(exc.orig)
    for marker, error in _DB_ERRORS:
        if marker in message:
            return error(message)
    return None
