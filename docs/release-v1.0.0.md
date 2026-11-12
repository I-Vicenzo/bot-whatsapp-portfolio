Primeira versão completa do assistente virtual de clínica (dados 100% fictícios).

**Rodar:** `git clone`, `cd bot-whatsapp-portfolio`, `docker compose up` → simulador em http://localhost:8000 e painel em http://localhost:8000/admin.

### O que tem

- Agendar, consultar e cancelar consultas por conversa em português (regex + máquina de estados)
- Regras de agenda garantidas no banco: grade de 30 min, sem horário duplicado, horário de funcionamento e antecedência mínima (CHECK, índices únicos parciais e triggers)
- WhatsApp Cloud API: webhook com assinatura validada, deduplicação, envio com retry
- IA de reserva (Groq/OpenAI/Ollama) só com dados da clínica, com limites de uso e encaminhamento para humano
- Painel do atendente: fila com urgentes, respostas pelo canal certo, janela de 24 h do WhatsApp, agenda
- Simulador web com o mesmo motor de produção e painel "por dentro do bot"
- Mais de 400 testes, CI e Docker com usuário não-root

### Versões anteriores

`v0.1.0` núcleo · `v0.2.0` API e simulador · `v0.3.0` WhatsApp · `v0.4.0` IA · `v0.5.0` painel
