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
  newContact: document.getElementById("new-contact"),
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

const state = { contact: "", lastId: 0, busy: false, pollTimer: null };

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

function addMessage(author, text) {
  const bubble = document.createElement("div");
  bubble.className = `msg ${author}`;
  if (AUTHOR_LABEL[author]) {
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = AUTHOR_LABEL[author];
    bubble.append(who);
  }
  appendFormatted(bubble, text);
  els.messages.append(bubble);
  els.messages.scrollTop = els.messages.scrollHeight;
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
    button.addEventListener("click", () => send(reply.numbered ? String(index + 1) : label));
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
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `Erro ${response.status}`;
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

async function send(text) {
  const message = text.trim();
  if (!message || state.busy) return;
  state.busy = true;
  els.send.disabled = true;
  els.options.replaceChildren();
  addMessage("customer", message);
  try {
    const result = await api("/api/simulator/messages", {
      method: "POST",
      body: JSON.stringify({ contact: state.contact, text: message }),
    });
    for (const reply of result.replies) addMessage("bot", reply.text);
    setOptions(result.replies.at(-1));
    setDebug(result.debug);
    state.lastId = Math.max(state.lastId, result.last_message_id);
  } catch (error) {
    addSystemNote(error.message);
  } finally {
    state.busy = false;
    els.send.disabled = false;
    els.input.focus();
  }
}

// Respostas do atendente (painel) chegam por polling.
async function poll() {
  if (state.busy || document.hidden) return;
  try {
    const messages = await api(
      `/api/simulator/messages?contact=${state.contact}&after=${state.lastId}`,
    );
    for (const message of messages) {
      if (message.author !== "customer") addMessage(message.author, message.text);
      state.lastId = Math.max(state.lastId, message.id);
    }
  } catch {
    /* tenta de novo no próximo ciclo */
  }
}

async function loadHistory() {
  els.messages.replaceChildren();
  els.options.replaceChildren();
  setDebug(null);
  state.lastId = 0;
  try {
    const messages = await api(`/api/simulator/messages?contact=${state.contact}&after=0`);
    for (const message of messages) {
      addMessage(message.author, message.text);
      state.lastId = message.id;
    }
  } catch (error) {
    addSystemNote(error.message);
  }
  if (!els.messages.childElementCount) {
    addSystemNote("Diga “oi” ou use um cenário pronto ao lado.");
  }
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

function useContact(value) {
  state.contact = value;
  els.contact.value = value;
  saveContact(value);
  loadHistory();
}

// --- eventos -------------------------------------------------------------------------------

els.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = els.input.value;
  els.input.value = "";
  send(text);
});

document.querySelectorAll("[data-say]").forEach((button) => {
  button.addEventListener("click", () => send(button.dataset.say));
});

els.contact.addEventListener("change", () => {
  if (els.contact.checkValidity()) useContact(els.contact.value);
});

els.newContact.addEventListener("click", () => useContact(randomContact()));

els.reset.addEventListener("click", async () => {
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
