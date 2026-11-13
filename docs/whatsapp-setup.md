# Conectar ao WhatsApp real (Cloud API)

Este guia liga o bot a um número de teste da Meta, sem custo e sem verificar empresa. São uns 20 minutos. Sem estes passos o projeto continua funcionando pelo simulador web.

> Os nomes de menus do painel da Meta mudam com frequência. Se algum item não estiver exatamente onde está descrito, procure pelo nome no painel ou na [documentação da Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started).

## O que você vai precisar

- Conta no [Meta for Developers](https://developers.facebook.com/) e um portfólio empresarial (Business) da Meta (pode ser criado no caminho).
- Um celular com WhatsApp para receber as mensagens de teste.
- Um túnel HTTPS para expor seu `localhost` ([cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/) ou [ngrok](https://ngrok.com/)).

## 1. Criar o app e o número de teste

1. Em **Meus apps → Criar app**, escolha o caso de uso de WhatsApp / tipo **Business** e vincule ao seu portfólio empresarial.
2. No app, adicione o produto **WhatsApp**. A Meta cria um **número de teste**.
3. Em **WhatsApp → Configuração da API** anote o **Phone number ID** (não é o número de telefone; é um id numérico).
4. Na mesma tela, em **Para**, cadastre até 5 números que poderão receber mensagens (a Meta envia um código de confirmação para cada um).

## 2. Gerar um token permanente

O token mostrado na tela de configuração expira em 24 horas. Para não precisar trocar todo dia:

1. Em **Configurações do negócio → Usuários → Usuários do sistema**, crie um usuário do sistema (função administrador).
2. Em **Atribuir ativos**, dê a ele acesso total ao app.
3. Clique em **Gerar token**, escolha o app e marque as permissões `whatsapp_business_messaging` e `whatsapp_business_management`.

Guarde o token só no `.env`: ele nunca vai para o repositório nem para os logs.

## 3. App Secret e token de verificação

- **App Secret**: em **Configurações do app → Básico**. É usado para validar a assinatura `X-Hub-Signature-256` de cada webhook.
- **Verify token**: uma senha que você inventa, usada só no cadastro do webhook. Gere uma aleatória:

  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

## 4. Configurar o `.env`

```bash
cp .env.example .env
```

```dotenv
WHATSAPP_TOKEN=<token do usuário do sistema>
WHATSAPP_PHONE_NUMBER_ID=<phone number id>
WHATSAPP_APP_SECRET=<app secret>
WHATSAPP_VERIFY_TOKEN=<o verify token que você gerou>
WHATSAPP_API_VERSION=v26.0
```

Com as quatro primeiras preenchidas, o WhatsApp liga (o simulador mostra **WhatsApp: ligado**). Suba o app (`docker compose up` ou `uv run uvicorn app.main:create_app --factory`).

## 5. Expor o app com HTTPS

A Meta só chama URLs HTTPS públicas. Em outro terminal:

```bash
cloudflared tunnel --url http://localhost:8000
# ou
ngrok http 8000
```

Anote a URL pública gerada (ex.: `https://abc-123.trycloudflare.com`).

## 6. Cadastrar o webhook

1. Em **WhatsApp → Configuração**, na seção **Webhook**, clique em **Editar**.
2. **URL de callback**: `https://<sua-url-publica>/webhook/whatsapp`
3. **Token de verificação**: o mesmo `WHATSAPP_VERIFY_TOKEN`.
4. Clique em **Verificar e salvar**. A Meta faz um `GET` com `hub.challenge`; o app responde o desafio se o token bater.
5. Em **Campos do webhook**, assine o campo **messages**.

## 7. Testar

Do celular cadastrado, mande "oi" para o número de teste. O bot responde com o menu. Toda a conversa também aparece no banco e, a partir da Fase 5, no painel do atendente.

## Lembrete antes da consulta (opcional)

Fora da janela de 24 h a Meta só entrega mensagens de **template aprovado**. Para ligar o lembrete:

1. Em **WhatsApp → Modelos de mensagem**, crie um modelo de categoria **Utilidade**, idioma Português (BR), com o nome `lembrete_consulta` e o corpo:

   ```text
   Olá, {{1}}! Lembrete: sua consulta de {{2}} é {{3}} às {{4}}. Se não puder vir, responda "cancelar".
   ```

2. Depois da aprovação, no `.env`: `REMINDERS_ENABLED=true` (e `WHATSAPP_REMINDER_TEMPLATE` se usar outro nome).

A cada 5 minutos o app envia o lembrete das consultas marcadas pelo WhatsApp que começam nas próximas 24 h (`REMINDER_HOURS_BEFORE`) e ainda não foram lembradas. Falhas são registradas no log e tentadas de novo no ciclo seguinte. Só uma instância do app deve ter os lembretes ligados.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| "Verificar e salvar" falha | App fora do ar, túnel parado ou `WHATSAPP_VERIFY_TOKEN` diferente do digitado no painel |
| Log `webhook com assinatura inválida` (HTTP 401) | `WHATSAPP_APP_SECRET` errado |
| Webhook nunca chega | Campo **messages** não assinado, ou URL do túnel mudou (o túnel gratuito muda a cada execução) |
| Webhook verificado, mas nenhum `POST` chega (nem status de entrega) | App não assinado na conta do WhatsApp (WABA). Assine com `curl -X POST "https://graph.facebook.com/v26.0/<WABA_ID>/subscribed_apps" -H "Authorization: Bearer <token>"`; o WABA ID é a "Identificação da conta do WhatsApp Business", na tela do número de teste |
| Erro `133010 Account not registered` no envio | Número ainda não registrado na Cloud API (status `PENDING`). Registre com `POST /<PHONE_NUMBER_ID>/register` e corpo `{"messaging_product":"whatsapp","pin":"<6 dígitos>"}`; guarde o PIN (verificação em duas etapas) |
| "O número +1 555… não está no WhatsApp" no celular | O número de teste só aparece depois de mandar a primeira mensagem: envie o template `hello_world` para o seu número e responda nessa conversa |
| Erro `131030` no envio | Destinatário não cadastrado na lista do número de teste |
| Erro `131030` com número brasileiro cadastrado | O `wa_id` pode chegar **sem o nono dígito** (ex.: `55 47 8888-7777` em vez de `55 47 98888-7777`). O bot responde para o `wa_id` exatamente como a Meta enviou; cadastre o número na lista de destinatários nesse mesmo formato |
| Erro `190` | Token expirado ou revogado: gere outro (passo 2) |
| Resposta do atendente bloqueada | Passou a janela de 24 h desde a última mensagem do cliente; fora dela a Meta só aceita templates aprovados |

## Como o app trata o webhook

- A assinatura é conferida sobre o **corpo bruto** da requisição (HMAC-SHA256 com o App Secret, comparação em tempo constante). Sem assinatura válida: HTTP 401.
- Com assinatura válida, responde **200 na hora** e processa em segundo plano. A Meta reenvia webhooks que demoram; o id de cada mensagem é único no banco, então um reenvio é descartado e nunca gera resposta duplicada.
- `statuses` (entregue, lido) são ignorados. Áudio, imagem e outros tipos recebem uma resposta padrão pedindo texto.
- Envio com timeout e novas tentativas com espera crescente em HTTP 429/5xx (respeitando `Retry-After`). Falhas vão para o log com o telefone mascarado e sem o token.

## Termos de uso

Desde janeiro de 2026 os termos do WhatsApp Business vedam chatbots de IA de uso geral. Este projeto é o atendimento de um negócio, com IA restrita aos dados da clínica, o que segue permitido. Confira os termos vigentes antes de usar em produção.
