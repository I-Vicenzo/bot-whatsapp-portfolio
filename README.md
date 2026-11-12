# Assistente virtual de clínica no WhatsApp

Bot de atendimento para uma clínica **fictícia**: informa, agenda, consulta, cancela e passa para um humano quando precisa. Núcleo determinístico (regex + máquina de estados) com IA apenas como reserva.

> Em desenvolvimento. Roteiro em [PLANO.md](PLANO.md).

## Rodar localmente

```bash
uv sync
uv run uvicorn app.main:app --reload
```

API em `http://localhost:8000/docs`.

## Testes

```bash
uv run ruff check
uv run ruff format --check
uv run pytest --cov
```

## Aviso

Todos os dados são fictícios. Não use com dados reais de saúde.

## Licença

[MIT](LICENSE)
