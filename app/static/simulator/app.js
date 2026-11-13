"use strict";

// Simulador: cada aba é um cliente com um número fictício próprio (sessionStorage).
// Conteúdo vindo do servidor é inserido só com textContent (nunca innerHTML).

const POLL_MS = 3000;
const AUTHOR_LABEL = { agent: "Atendente", ai: "IA" };
const SOURCE_LABEL = {
  rule: "regra",
  faq: "FAQ",
  ai: "IA",
  not_understood: "não entendido",
  silent: "silêncio (com humano)",
};
const STATUS_LABEL = { bot: "bot", waiting_human: "aguardando atendente", human: "com atendente" };

const els = {
  messages: document.getElementById("messages"),
  options: document.getElementById("options"),
  composer: document.getElementById("composer"),
  input: document.getElementById("input"),
  send: document.getElementById("send"),
  contact: document.getElementById("contact"),
  contactHint: document.getElementById("contact-hint"),
  newContact: document.getElementById("new-contact"),
  scenarios: document.querySelectorAll("[data-say]"),
  reset: document.getElementById("reset"),
  clinicName: document.getElementById("clinic-name"),
  phoneTitle: document.getElementById("phone-title"),
  chipWhatsapp: document.getElementById("chip-whatsapp"),
  chipAi: document.getElementById("chip-ai"),
  dbg: {
    state: document.getElementById("dbg-state"),
    intent: document.getElementById("dbg-intent"),
    source: document.getElementById("dbg-source"),
    status: document.getElementById("dbg-status"),
  },
};

// `epoch` muda a cada troca de cliente ou reinício: respostas atrasadas de antes são descartadas.
const state = { contact: "", lastId: 0, busy: false, loading: false, polling: false, epoch: 0, pollTimer: null };
const CONTACT_HINT = "Só números, de 8 a 15 dígitos.";
const NETWORK_ERROR = "Sem conexão com o servidor. Tente de novo.";

// --- armazenamento por aba (pode falhar em modo privado) -----------------------------------

function loadContact() {
  try {
    return sessionStorage.getItem("sim-contact");
  } catch {
    return null;
  }
}

function saveContact(value) {
  try {
    sessionStorage.setItem("sim-contact", value);
  } catch {
    /* sem armazenamento: segue só em memória */
  }
}

function randomContact() {
  const digits = Array.from({ length: 8 }, () => Math.floor(Math.random() * 10)).join("");
  return `55009${digits}`; // DDD 00: claramente fictício
}

// --- renderização segura -------------------------------------------------------------------

// Converte *negrito* do WhatsApp em <strong>, criando nós de texto.
function appendFormatted(parent, text) {
  const pattern = /\*([^*\n]+)\*/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(last, match.index)));
    const strong = document.createElement("strong");
    strong.textContent = match[1];
    parent.append(strong);
    last = match.index + match[0].length;
  }
  parent.append(document.createTextNode(text.slice(last)));
}

// Só rola até o fim se o usuário já está perto dele (ou se a mensagem é dele mesmo).
function addMessage(author, text, { scroll = false } = {}) {
  const box = els.messages;
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const bubble = document.createElement("div");
  bubble.className = `msg ${author}`;
  if (AUTHOR_LABEL[author]) {
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = AUTHOR_LABEL[author];
    bubble.append(who);
  }
  appendFormatted(bubble, text);
  box.append(bubble);
  if (scroll || nearBottom || author === "customer") box.scrollTop = box.scrollHeight;
}

function addSystemNote(text) {
  addMessage("system", text);
}

function setOptions(reply) {
  els.options.replaceChildren();
  if (!reply || !reply.options.length) return;
  reply.options.forEach((label, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    // Opções numeradas aceitam o número; as demais (horários) aceitam o próprio texto.
    // O balão mostra o rótulo; o bot recebe o número, como no WhatsApp.
    button.addEventListener("click", () =>
      send(reply.numbered ? String(index + 1) : label, label),
    );
    els.options.append(button);
  });
}

function setDebug(debug) {
  const show = (el, value) => {
    el.textContent = value ?? "—";
  };
  show(els.dbg.state, debug?.state);
  show(els.dbg.intent, debug?.intent);
  show(els.dbg.source, debug ? SOURCE_LABEL[debug.source] ?? debug.source : null);
  show(els.dbg.status, debug ? STATUS_LABEL[debug.status] ?? debug.status : null);
}

// --- API -----------------------------------------------------------------------------------

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
    throw new Error(NETWORK_ERROR); // o navegador só diz "Failed to fetch"
  }
  if (!response.ok) {
    let detail = `O servidor não conseguiu responder (erro ${response.status}). Tente de novo.`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* corpo não é JSON */
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

// Enquanto uma mensagem está em envio, os atalhos (cenários, opções, reiniciar) ficam desabilitados.
function setBusy(busy) {
  state.busy = busy;
  els.send.disabled = busy;
  els.reset.disabled = busy;
  els.scenarios.forEach((button) => {
    button.disabled = busy;
  });
  els.options.querySelectorAll("button").forEach((button) => {
    button.disabled = busy;
  });
}

function messagesUrl(after) {
  const params = new URLSearchParams({ contact: state.contact, after: String(after) });
  return `/api/simulator/messages?${params}`;
}

// Devolve true se a mensagem foi entregue ao bot.
async function send(text, shown = text) {
  const message = text.trim();
  if (!message || state.busy || state.loading) return false;
  const epoch = state.epoch;
  setBusy(true);
  els.options.replaceChildren();
  addMessage("customer", shown.trim());
  try {
    const result = await api("/api/simulator/messages", {
      method: "POST",
      body: JSON.stringify({ contact: state.contact, text: message }),
    });
    if (epoch !== state.epoch) return true; // cliente trocado no meio do envio
    const author = result.debug?.source === "ai" ? "ai" : "bot";
    for (const reply of result.replies) addMessage(author, reply.text);
    setOptions(result.replies.at(-1));
    setDebug(result.debug);
    state.lastId = Math.max(state.lastId, result.last_message_id);
    return true;
  } catch (error) {
    if (epoch === state.epoch) addSystemNote(error.message);
    return false;
  } finally {
    setBusy(false);
    els.input.focus();
  }
}

// Respostas do atendente (painel) chegam por polling.
async function poll() {
  if (state.busy || state.loading || state.polling || document.hidden) return;
  const epoch = state.epoch;
  state.polling = true;
  try {
    const messages = await api(messagesUrl(state.lastId));
    if (epoch !== state.epoch) return; // resposta de outro cliente ou de antes do reinício
    for (const message of messages) {
      if (message.id <= state.lastId) continue;
      if (message.author !== "customer") addMessage(message.author, message.text);
      state.lastId = message.id;
    }
  } catch {
    /* tenta de novo no próximo ciclo */
  } finally {
    state.polling = false;
  }
}

async function loadHistory() {
  const epoch = ++state.epoch;
  els.messages.replaceChildren();
  els.options.replaceChildren();
  setDebug(null);
  state.lastId = 0;
  state.loading = true;
  // O histórico carregado de uma vez não deve ser lido inteiro pelo leitor de tela.
  els.messages.setAttribute("aria-live", "off");
  try {
    const messages = await api(messagesUrl(0));
    if (epoch !== state.epoch) return;
    for (const message of messages) {
      addMessage(message.author, message.text);
      state.lastId = Math.max(state.lastId, message.id);
    }
  } catch (error) {
    if (epoch !== state.epoch) return;
    addSystemNote(error.message);
  } finally {
    if (epoch === state.epoch) {
      state.loading = false;
      els.messages.setAttribute("aria-live", "polite");
    }
  }
  if (!els.messages.childElementCount) {
    addSystemNote("Diga “oi” ou use um cenário pronto ao lado.");
  }
  els.messages.scrollTop = els.messages.scrollHeight;
}

async function loadStatus() {
  try {
    const status = await api("/api/status");
    els.clinicName.textContent = `${status.clinic_name}: assistente virtual`;
    els.phoneTitle.textContent = status.clinic_name;
    document.title = `Simulador · ${status.clinic_name}`;
    const chip = (el, label, on) => {
      el.textContent = `${label}: ${on ? "ligado" : "desligado"}`;
      el.classList.toggle("on", on);
    };
    chip(els.chipWhatsapp, "WhatsApp", status.whatsapp_enabled);
    chip(els.chipAi, "IA", status.ai_enabled);
  } catch {
    /* status é só informativo */
  }
}

function setContactError(invalid) {
  els.contact.setAttribute("aria-invalid", String(invalid));
  els.contactHint.textContent = invalid ? `Número inválido. ${CONTACT_HINT}` : CONTACT_HINT;
  els.contactHint.classList.toggle("error", invalid);
}

function useContact(value) {
  state.contact = value;
  els.contact.value = value;
  setContactError(false);
  saveContact(value);
  loadHistory();
}

// --- eventos -------------------------------------------------------------------------------

els.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy || state.loading) return; // mantém o texto digitado
  const text = els.input.value;
  els.input.value = "";
  const sent = await send(text);
  if (!sent && !els.input.value) els.input.value = text; // falhou: devolve o texto
});

els.scenarios.forEach((button) => {
  button.addEventListener("click", () => send(button.dataset.say));
});

els.contact.addEventListener("change", () => {
  const valid = els.contact.checkValidity();
  setContactError(!valid);
  if (valid) useContact(els.contact.value);
});

els.contact.addEventListener("input", () => {
  if (els.contact.checkValidity()) setContactError(false);
});

els.newContact.addEventListener("click", () => useContact(randomContact()));

els.reset.addEventListener("click", async () => {
  if (!window.confirm("Reiniciar a conversa? O histórico deste cliente será apagado.")) return;
  try {
    await api("/api/simulator/reset", {
      method: "POST",
      body: JSON.stringify({ contact: state.contact }),
    });
  } catch (error) {
    addSystemNote(error.message);
    return;
  }
  loadHistory();
});

loadStatus();
useContact(loadContact() || randomContact());
state.pollTimer = setInterval(poll, POLL_MS);
