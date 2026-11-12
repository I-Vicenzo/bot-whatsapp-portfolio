# Assistente virtual de clínica no WhatsApp

Bot de atendimento para uma clínica **fictícia**: informa, agenda, consulta, cancela e passa para um humano quando precisa. Núcleo determinístico (regex + máquina de estados) com IA apenas como reserva.

> Em desenvolvimento. Roteiro em [PLANO.md](PLANO.md).

## Rodar com Docker (sem configurar nada)

```bash
git clone https://github.com/I-Vicenzo/bot-whatsapp-portfolio.git
cd bot-whatsapp-portfolio
docker compose up
```

Simulador em `http://localhost:8000`, API documentada em `http://localhost:8000/docs`. Sem `.env`, sobe em modo demo com consultas de exemplo; WhatsApp e IA ficam desligados e sinalizados na tela.

## Rodar sem Docker

```bash
uv sync
uv run uvicorn app.main:create_app --factory --reload
```

O banco SQLite é criado e migrado sozinho em `var/app.db`; os dados da clínica (fictícios) ficam em [data/clinica.yaml](data/clinica.yaml).

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
