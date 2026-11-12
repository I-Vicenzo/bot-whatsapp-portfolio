Extras pós-MVP (Fase 7). Dados continuam 100% fictícios.

### Novidades

- **WhatsApp:** opções como botões e listas interativas; áudios transcritos (Whisper via Groq) e tratados como texto; lembrete 24 h antes da consulta por template aprovado
- **Agenda:** bloqueios por feriado ou férias de profissional, declarados no YAML e garantidos por trigger no banco
- **LGPD:** comando "apagar meus dados", com confirmação (cancela consultas futuras, anonimiza o histórico e apaga a conversa)
- **Painel:** aba de métricas (resolvidas pelo bot, pedidos de atendente, tempo até a primeira resposta, uso da IA)
- **PostgreSQL:** as mesmas regras de agenda em PL/pgSQL; `docker compose -f docker-compose.yml -f docker-compose.postgres.yml up`
- **IA local:** Ollama sem chave, com `docker compose -f docker-compose.yml -f docker-compose.ollama.yml up`
- **Qualidade:** testes E2E no navegador (Playwright) e suíte inteira também no PostgreSQL, os dois no CI
