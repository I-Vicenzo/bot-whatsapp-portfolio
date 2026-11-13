"""Todos os textos do bot, em um só lugar."""

from collections.abc import Sequence
from datetime import date, datetime, time

from app.clinic import ClinicData, SpecialtyInfo
from app.db.models import Appointment

WEEKDAY_NAMES = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
WEEKDAY_SHORT = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
WEEKDAY_PLURAL = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")

MENU_OPTIONS = (
    "Agendar consulta",
    "Minhas consultas",
    "Cancelar consulta",
    "Especialidades e valores",
    "Convênios",
    "Endereço e horário de funcionamento",
    "Falar com atendente",
)

BACK_TO_MENU = "Digite *menu* para ver as opções."


# --- formatação --------------------------------------------------------------------------


def fmt_date(day: date) -> str:
    return f"{WEEKDAY_NAMES[day.weekday()]}, {day:%d/%m}"


def fmt_day_option(day: date) -> str:
    return f"{WEEKDAY_SHORT[day.weekday()]} {day:%d/%m}"


def fmt_time(value: time | datetime) -> str:
    return f"{value:%H:%M}"


def fmt_price(cents: int) -> str:
    reais = f"{cents // 100:,}".replace(",", ".")
    return f"R$ {reais},{cents % 100:02d}"


def fmt_appointment(appointment: Appointment) -> str:
    return (
        f"{appointment.specialty.name} com {appointment.specialty.professional}, "
        f"{fmt_date(appointment.starts_at.date())} às {fmt_time(appointment.starts_at)} "
        f"(paciente: {appointment.patient_name})"
    )


# --- menu e conversa ---------------------------------------------------------------------


def greeting(clinic: ClinicData) -> str:
    return (
        f"Olá! Sou o assistente virtual da *{clinic.clinic.name}*. "
        "Posso agendar, consultar ou cancelar consultas e tirar dúvidas.\n"
        "Como posso ajudar?"
    )


MENU_PROMPT = "Como posso ajudar? Escolha uma opção:"
NOT_UNDERSTOOD = "Desculpe, não entendi. Escolha uma das opções ou escreva sua dúvida:"
OFFER_HUMAN = (
    "Parece que não estou conseguindo ajudar. Se preferir, digite *atendente* para falar "
    "com a nossa equipe."
)
THANKS = "Por nada! Se precisar de algo mais, é só chamar. " + BACK_TO_MENU
FLOW_ABORTED = "Tudo bem, deixei o agendamento de lado."
SPECIALTY_GONE = "Essa especialidade não está mais disponível para agendamento."
EXPIRED = "Retomando do início, já que a conversa anterior ficou parada."

HANDOFF = (
    "Certo! Chamei alguém da nossa equipe, que vai responder por aqui em breve.\n"
    "Se mudar de ideia antes disso, digite *menu* para voltar ao assistente."
)
HANDOFF_URGENT = (
    "⚠️ *Em caso de emergência médica, ligue agora para o 192 (SAMU)* ou vá ao pronto-socorro "
    "mais próximo.\n\nAvisei nossa equipe com prioridade; alguém vai responder por aqui."
)
BACK_FROM_HANDOFF = "Tudo bem, voltei a atender por aqui."
RELEASED = "O atendimento com a equipe foi encerrado. Se precisar de algo, digite *menu*."
AI_HANDOFF_REASON = "Pergunta sem resposta nos dados da clínica"
AI_HANDOFF = (
    "Não tenho essa informação aqui, então chamei alguém da nossa equipe para te responder. "
    "Se preferir, digite *menu* para ver o que eu consigo resolver."
)


# --- informações -------------------------------------------------------------------------


def specialties_and_prices(clinic: ClinicData) -> str:
    lines = [
        f"• *{s.name}*: {s.professional}, {fmt_price(s.price_cents)}" for s in clinic.specialties
    ]
    return "Nossas especialidades e valores da consulta particular:\n\n" + "\n".join(lines)


def insurances(clinic: ClinicData) -> str:
    if not clinic.insurances:
        return "No momento atendemos apenas particular."
    names = ", ".join(clinic.insurances)
    return f"Aceitamos os convênios: {names}. Também atendemos particular."


def address(clinic: ClinicData) -> str:
    return f"📍 {clinic.clinic.address}\n📞 {clinic.clinic.phone}"


def business_hours(clinic: ClinicData) -> str:
    def describe(weekday: int) -> str:
        ranges = clinic.business_hours.for_weekday(weekday)
        if not ranges:
            return "fechado"
        return " e ".join(f"{r.start:%H:%M} às {r.end:%H:%M}" for r in ranges)

    # Agrupa dias seguidos com o mesmo horário ("Segunda a sexta: ...").
    lines: list[str] = []
    start = 0
    for weekday in range(1, 8):
        if weekday == 7 or describe(weekday) != describe(start):
            label = WEEKDAY_PLURAL[start]
            if weekday - 1 != start:
                label += f" a {WEEKDAY_PLURAL[weekday - 1].lower()}"
            lines.append(f"{label}: {describe(start)}")
            start = weekday
    return "Horário de funcionamento:\n" + "\n".join(lines)


# --- agendamento -------------------------------------------------------------------------

ASK_SPECIALTY = "Qual especialidade você procura?"
INVALID_SPECIALTY = "Não encontrei essa especialidade. Responda com o número ou o nome, ex.: *1*."


def ask_day(specialty: SpecialtyInfo) -> str:
    return (
        f"*{specialty.name}* com {specialty.professional}. Qual dia fica melhor?\n"
        "Responda com o número, ou escreva o dia (ex.: *amanhã*, *sexta*, *15/11*)."
    )


def no_days(specialty: SpecialtyInfo, days_ahead: int) -> str:
    return (
        f"Não há horários livres de {specialty.name} nos próximos {days_ahead} dias. "
        "Digite *atendente* para falar com a nossa equipe ou *menu* para voltar."
    )


INVALID_DAY = (
    "Não entendi a data. Responda com o número da lista ou escreva, ex.: *sexta* ou *15/11*."
)


def day_unavailable(day: date, reason: str) -> str:
    return f"Não tenho horários em {fmt_date(day)}: {reason}. Escolha outro dia:"


REASON_PAST = "essa data já passou"
REASON_CLOSED = "a clínica não abre nesse dia"
REASON_TOO_FAR = "a agenda ainda não está aberta para essa data"
REASON_FULL = "todos os horários estão ocupados"


def reason_closure(reason: str) -> str:
    return f"não há atendimento nesse dia ({reason})"


def ask_time(specialty: SpecialtyInfo, day: date, slots: Sequence[datetime]) -> str:
    morning = [fmt_time(s) for s in slots if s.hour < 12]
    afternoon = [fmt_time(s) for s in slots if s.hour >= 12]
    groups = []
    if morning:
        groups.append("🌅 Manhã: " + ", ".join(morning))
    if afternoon:
        groups.append("🌇 Tarde: " + ", ".join(afternoon))
    return (
        f"Horários livres de {specialty.name} em {fmt_date(day)}:\n\n"
        + "\n".join(groups)
        + "\n\nQual horário? Escreva a hora (ex.: *14h* ou *14:30*) ou *outro dia*."
    )


INVALID_TIME = "Não entendi o horário. Escreva a hora, ex.: *9h*, *14h* ou *14:30*."


def time_unavailable(value: time) -> str:
    return f"O horário {fmt_time(value)} não está disponível nesse dia."


ASK_NAME = "Qual o *nome completo* do paciente?"
INVALID_NAME = "Por favor, informe nome e sobrenome do paciente, só com letras. Ex.: *Maria Silva*."


def confirm_booking(specialty: SpecialtyInfo, starts_at: datetime, patient_name: str) -> str:
    return (
        "Confira os dados da consulta:\n\n"
        f"• Especialidade: {specialty.name}\n"
        f"• Profissional: {specialty.professional}\n"
        f"• Data: {fmt_date(starts_at.date())}\n"
        f"• Horário: {fmt_time(starts_at)}\n"
        f"• Paciente: {patient_name}\n"
        f"• Valor particular: {fmt_price(specialty.price_cents)}\n\n"
        "Posso confirmar? (*sim* ou *não*)"
    )


CONFIRM_YES_NO = "Responda *sim* para confirmar ou *não* para desistir."


def booked(appointment: Appointment, clinic: ClinicData) -> str:
    return (
        f"✅ Consulta confirmada!\n\n{fmt_appointment(appointment)}.\n\n"
        f"📍 {clinic.clinic.address}\n"
        "Chegue com 15 minutos de antecedência. Para cancelar, é só escrever *cancelar*."
    )


BOOKING_DECLINED = "Tudo bem, não agendei nada. " + BACK_TO_MENU
SLOT_TAKEN = "Poxa, esse horário acabou de ser reservado por outra pessoa."
PATIENT_BUSY = "Você já tem uma consulta confirmada nesse mesmo horário."
SLOT_NO_LONGER_VALID = "Esse horário não está mais disponível."


# --- consultas e cancelamento ------------------------------------------------------------

NO_APPOINTMENTS = "Você não tem consultas agendadas. Quer marcar? Digite *agendar*."
# Não confirma nem nega que a pessoa seja paciente da clínica.
THIRD_PARTY_PRIVACY = (
    "Por privacidade, não passo informações sobre consultas de outras pessoas. "
    "Aqui eu mostro só as consultas marcadas por este número: digite *minhas consultas* "
    "para ver as suas."
)


def upcoming(appointments: Sequence[Appointment]) -> str:
    lines = "\n".join(f"• {fmt_appointment(a)}" for a in appointments)
    title = "Sua próxima consulta:" if len(appointments) == 1 else "Suas próximas consultas:"
    return f"{title}\n\n{lines}\n\n{BACK_TO_MENU}"


ASK_WHICH_TO_CANCEL = "Qual consulta você quer cancelar?"
INVALID_CANCEL_OPTION = "Responda com o número da consulta que quer cancelar."


def confirm_cancel(appointment: Appointment) -> str:
    return f"Cancelar esta consulta?\n\n{fmt_appointment(appointment)}\n\n(*sim* ou *não*)"


ERASE_CONFIRM = (
    "Posso apagar seus dados? Isso vai:\n\n"
    "• cancelar suas consultas futuras;\n"
    "• remover seu nome e telefone do histórico de consultas;\n"
    "• apagar esta conversa.\n\n"
    "Não dá para desfazer. Confirma? (*sim* ou *não*)"
)
ERASED = "Pronto: seus dados foram apagados. Se voltar a falar com a gente, começamos do zero."
ERASE_KEPT = "Tudo bem, não apaguei nada. " + BACK_TO_MENU
ERASED_PATIENT_NAME = "Removido (LGPD)"

CANCELLED = "Consulta cancelada. O horário foi liberado. " + BACK_TO_MENU
CANCEL_KEPT = "Tudo bem, sua consulta continua marcada. " + BACK_TO_MENU
CANCEL_NOT_FOUND = "Não encontrei essa consulta: ela pode já ter sido cancelada. " + BACK_TO_MENU


# --- canais ------------------------------------------------------------------------------

UNSUPPORTED_MESSAGE = (
    "Por enquanto só consigo ler mensagens de texto. Pode escrever sua dúvida? " + BACK_TO_MENU
)
