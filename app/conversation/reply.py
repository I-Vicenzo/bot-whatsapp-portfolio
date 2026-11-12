"""Saída estruturada do motor, independente de canal."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Reply:
    """Uma mensagem do bot.

    `options` viram botões no simulador. Se `numbered`, também aparecem como lista numerada no
    texto (WhatsApp) e o cliente pode responder com o número. Se não, o texto já descreve as
    opções e elas servem só de atalho (ex.: horários, que o cliente digita como "14h").
    """

    text: str
    options: tuple[str, ...] = ()
    numbered: bool = True

    def render(self) -> str:
        """Texto puro para canais sem botões (WhatsApp)."""
        if not self.options or not self.numbered:
            return self.text
        listing = "\n".join(f"*{i}.* {option}" for i, option in enumerate(self.options, 1))
        return f"{self.text}\n\n{listing}"
