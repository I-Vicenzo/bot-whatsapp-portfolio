"""Modelos Pydantic dos dados da clínica.

As validações aqui antecipam as regras do banco (intervalos de 30 min, horários coerentes), para
que um arquivo errado falhe na inicialização com mensagem clara, e não no primeiro agendamento.
"""

from datetime import date, time
from decimal import Decimal
from functools import cached_property
from itertools import pairwise
from typing import Annotated, Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.nlu.normalize import normalize

SLOT_MINUTES = 30
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


class TimeRange(_Model):
    """Intervalo de atendimento, escrito no YAML como `"08:00-12:00"`."""

    start: time
    end: time

    @model_validator(mode="before")
    @classmethod
    def _parse_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            start, sep, end = value.partition("-")
            if not sep:
                raise ValueError('use o formato "HH:MM-HH:MM"')
            return {"start": start.strip(), "end": end.strip()}
        return value

    @model_validator(mode="after")
    def _check(self) -> Self:
        for t in (self.start, self.end):
            if t.second or t.microsecond or t.minute % SLOT_MINUTES:
                raise ValueError(f"horários devem ser múltiplos de {SLOT_MINUTES} min: {t:%H:%M}")
        if self.start >= self.end:
            raise ValueError(f"início deve ser antes do fim: {self}")
        return self

    @property
    def open_min(self) -> int:
        return _minutes(self.start)

    @property
    def close_min(self) -> int:
        return _minutes(self.end)

    def __str__(self) -> str:
        return f"{self.start:%H:%M}-{self.end:%H:%M}"


class BusinessHours(_Model):
    monday: tuple[TimeRange, ...] = ()
    tuesday: tuple[TimeRange, ...] = ()
    wednesday: tuple[TimeRange, ...] = ()
    thursday: tuple[TimeRange, ...] = ()
    friday: tuple[TimeRange, ...] = ()
    saturday: tuple[TimeRange, ...] = ()
    sunday: tuple[TimeRange, ...] = ()

    @field_validator(*WEEKDAYS, mode="after")
    @classmethod
    def _no_overlap(cls, ranges: tuple[TimeRange, ...]) -> tuple[TimeRange, ...]:
        ordered = tuple(sorted(ranges, key=lambda r: r.start))
        for previous, current in pairwise(ordered):
            if current.start < previous.end:
                raise ValueError(f"intervalos sobrepostos: {previous} e {current}")
        return ordered

    @model_validator(mode="after")
    def _at_least_one_day(self) -> Self:
        if not any(self.for_weekday(day) for day in range(7)):
            raise ValueError("a clínica precisa abrir em pelo menos um dia da semana")
        return self

    def for_weekday(self, weekday: int) -> tuple[TimeRange, ...]:
        """Intervalos do dia (0 = segunda, como `date.weekday()`)."""
        return getattr(self, WEEKDAYS[weekday])


class ClinicInfo(_Model):
    name: NonEmptyStr
    address: NonEmptyStr
    phone: NonEmptyStr
    timezone: NonEmptyStr = "America/Sao_Paulo"

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError(f"fuso horário desconhecido: {value}") from None
        return value

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class SchedulingRules(_Model):
    min_advance_minutes: int = Field(ge=0, le=7 * 24 * 60)
    days_ahead: int = Field(ge=1, le=90, description="Último dia agendável = hoje + days_ahead")


class SpecialtyInfo(_Model):
    slug: Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
    name: NonEmptyStr
    professional: NonEmptyStr
    price: Annotated[Decimal, Field(ge=0, decimal_places=2)]
    aliases: tuple[NonEmptyStr, ...] = ()

    @property
    def price_cents(self) -> int:
        return int(self.price * 100)

    @cached_property
    def match_terms(self) -> tuple[str, ...]:
        """Termos normalizados que identificam a especialidade no texto, do mais longo ao mais
        curto (o mais específico ganha)."""
        terms = {normalize(self.name), normalize(self.slug.replace("-", " "))}
        terms.update(normalize(alias) for alias in self.aliases)
        return tuple(sorted(terms - {""}, key=len, reverse=True))


class FaqEntry(_Model):
    keywords: Annotated[tuple[NonEmptyStr, ...], Field(min_length=1)]
    answer: NonEmptyStr

    @cached_property
    def match_terms(self) -> tuple[str, ...]:
        return tuple(normalize(k) for k in self.keywords)


class Closure(_Model):
    """Dia(s) sem atendimento: feriado (clínica toda) ou férias de um profissional.

    No YAML: `date: 2026-12-25` para um dia, ou `start`/`end` para um período.
    """

    start: date
    end: date
    specialty: str | None = None  # slug; vazio = clínica inteira
    reason: NonEmptyStr

    @model_validator(mode="before")
    @classmethod
    def _single_date(cls, value: Any) -> Any:
        if isinstance(value, dict) and "date" in value:
            data = dict(value)
            day = data.pop("date")
            data.setdefault("start", day)
            data.setdefault("end", day)
            return data
        if isinstance(value, dict) and "start" in value and "end" not in value:
            return {**value, "end": value["start"]}
        return value

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end < self.start:
            raise ValueError(f"fim antes do início: {self.start} a {self.end}")
        return self

    def covers(self, day: date, specialty: str) -> bool:
        return self.start <= day <= self.end and self.specialty in (None, specialty)


class ClinicData(_Model):
    clinic: ClinicInfo
    business_hours: BusinessHours
    scheduling: SchedulingRules
    specialties: Annotated[tuple[SpecialtyInfo, ...], Field(min_length=1)]
    insurances: tuple[NonEmptyStr, ...] = ()
    faq: tuple[FaqEntry, ...] = ()
    closures: tuple[Closure, ...] = ()

    @model_validator(mode="after")
    def _unique_identifiers(self) -> Self:
        slugs = [s.slug for s in self.specialties]
        if len(slugs) != len(set(slugs)):
            raise ValueError(f"slugs de especialidade repetidos: {slugs}")
        owner: dict[str, str] = {}
        for spec in self.specialties:
            for term in spec.match_terms:
                if owner.setdefault(term, spec.slug) != spec.slug:
                    raise ValueError(
                        f'o termo "{term}" identifica duas especialidades: '
                        f"{owner[term]} e {spec.slug}"
                    )
        return self

    @model_validator(mode="after")
    def _closures_reference_specialties(self) -> Self:
        slugs = {s.slug for s in self.specialties}
        for closure in self.closures:
            if closure.specialty is not None and closure.specialty not in slugs:
                raise ValueError(f"bloqueio com especialidade desconhecida: {closure.specialty}")
        return self

    def closure_for(self, day: date, specialty: str) -> Closure | None:
        return next((c for c in self.closures if c.covers(day, specialty)), None)

    def specialty(self, slug: str) -> SpecialtyInfo | None:
        return next((s for s in self.specialties if s.slug == slug), None)
