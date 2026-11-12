"use strict";

// Painel do atendente. Conteúdo vindo de clientes é inserido só com textContent.

const POLL_MS = 4000;
const AUTHOR = { customer: "Cliente", bot: "Bot", ai: "IA", agent: "Atendente" };
const STATUS = { bot: "com o bot", waiting_human: "aguardando", human: "em atendimento" };
const CHANNEL = { whatsapp: "WhatsApp", simulator: "Simulador", admin: "Painel" };
const WEEKDAYS = ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"];

const $ = (id) => document.getElementById(id);
const state = { tab: "queue", selected: null, timer: null, specialtiesLoaded: false };

// --- utilidades -----------------------------------------------------------------------------

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "className") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  node.append(...children.filter((c) => c != null));
  return node;
}

// *negrito* do WhatsApp -> <strong>, só com nós de texto.
function appendFormatted(parent, text) {
  let last = 0;
  for (const match of text.matchAll(/\*([^*\n]+)\*/g)) {
    parent.append(document.createTextNode(text.slice(last, match.index)));
    parent.append(el("strong", { text: match[1] }));
    last = match.index + match[0].length;
  }
  parent.append(document.createTextNode(text.slice(last)));
}

function fmtDateTime(iso) {
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, "0");
  return `${WEEKDAYS[d.getDay()]} ${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtWaiting(minutes) {
  if (minutes == null) return null;
  if (minutes < 60) return `esperando há ${minutes} min`;
  return `esperando há ${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  if (response.status === 401 && !path.endsWith("/login")) {
    showLogin();
    throw new Error("Sessão expirada.");
  }
  if (!response.ok) {
    let detail = `Erro ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* sem corpo JSON */
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

// --- login ----------------------------------------------------------------------------------

async function showLogin() {
  clearInterval(state.timer);
  $("app-view").hidden = true;
  $("login-view").hidden = false;
  try {
    const info = await api("/api/admin/login-info");
    if (info.demo_username) {
      const hint = $("demo-credentials");
      hint.textContent = `Modo demo: usuário "${info.demo_username}", senha "${info.demo_password}".`;
      hint.hidden = false;
    }
  } catch {
    /* informativo */
  }
  $("username").focus();
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    const user = await api("/api/admin/login", {
      method: "POST",
      body: JSON.stringify({ username: $("username").value, password: $("password").value }),
    });
    $("password").value = "";
    startApp(user);
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

$("logout").addEventListener("click", async () => {
  try {
    await api("/api/admin/logout", { method: "POST" });
  } finally {
    showLogin();
  }
});

function startApp(user) {
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  $("user-name").textContent = user.name;
  selectTab(state.tab);
  clearInterval(state.timer);
  state.timer = setInterval(refresh, POLL_MS);
}

// --- abas -----------------------------------------------------------------------------------

document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => selectTab(button.dataset.tab));
});

function selectTab(tab) {
  state.tab = tab;
  document.querySelectorAll("[data-tab]").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  });
  const appointments = tab === "appointments";
  $("conversations-view").hidden = appointments;
  $("appointments-view").hidden = !appointments;
  refresh();
}

function refresh() {
  if (document.hidden) return;
  if (state.tab === "appointments") loadAppointments();
  else loadConversations();
  updateQueueCount();
}

// --- conversas ------------------------------------------------------------------------------

async function updateQueueCount() {
  try {
    const queue = await api("/api/admin/conversations?status=waiting_human");
    const badge = $("queue-count");
    badge.textContent = String(queue.length);
    badge.classList.toggle("zero", queue.length === 0);
  } catch {
    /* tenta no próximo ciclo */
  }
}

async function loadConversations() {
  const query = state.tab === "queue" ? "?status=waiting_human" : "";
  let items;
  try {
    items = await api(`/api/admin/conversations${query}`);
  } catch {
    return;
  }
  const list = $("conversation-list");
  list.replaceChildren(...items.map(renderListItem));
  $("list-empty").hidden = items.length > 0;
  if (state.selected) loadDetail(state.selected, { quiet: true });
}

function renderListItem(item) {
  const tags = el("div", { className: "tags" });
  if (item.priority === "urgent") tags.append(el("span", { className: "tag urgent", text: "URGENTE" }));
  tags.append(el("span", { className: "tag", text: STATUS[item.status] ?? item.status }));
  tags.append(el("span", { className: "tag", text: CHANNEL[item.channel] ?? item.channel }));
  const waiting = fmtWaiting(item.waiting_minutes);
  if (waiting) tags.append(el("span", { className: "tag", text: waiting }));

  const last = item.last_message;
  const button = el(
    "button",
    {
      type: "button",
      "data-id": String(item.id),
      "aria-current": String(item.id === state.selected),
      onclick: () => loadDetail(item.id),
    },
    el(
      "div",
      { className: "row-top" },
      el("span", { className: "name", text: item.display_name || item.contact }),
      last ? el("span", { className: "meta", text: fmtDateTime(last.created_at) }) : null,
    ),
    el("div", {
      className: "preview",
      text: last ? `${AUTHOR[last.author] ?? last.author}: ${last.text}` : "",
    }),
    tags,
  );
  return el("li", {}, button);
}

async function loadDetail(id, { quiet = false } = {}) {
  let detail;
  try {
    detail = await api(`/api/admin/conversations/${id}`);
  } catch (error) {
    if (!quiet) $("reply-error").textContent = error.message;
    return;
  }
  const changed = state.selected !== id;
  state.selected = id;
  document.querySelectorAll("#conversation-list button").forEach((b) => {
    b.setAttribute("aria-current", String(b.dataset.id === String(id)));
  });

  $("detail-empty").hidden = true;
  $("detail").hidden = false;
  $("detail-title").textContent = detail.display_name || detail.contact;
  const meta = [
    CHANNEL[detail.channel] ?? detail.channel,
    detail.contact,
    STATUS[detail.status] ?? detail.status,
    detail.assigned_to ? `com ${detail.assigned_to}` : null,
    detail.handoff_reason,
  ].filter(Boolean);
  $("detail-meta").textContent = meta.join(" · ");
  $("assign").disabled = detail.status === "human";
  $("release").disabled = detail.status === "bot";
  $("window-warning").hidden = detail.window_open;
  $("reply-text").disabled = !detail.window_open;

  const box = $("messages");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.replaceChildren(
    ...detail.messages.map((m) => {
      const bubble = el("div", { className: `msg ${m.author}` });
      bubble.append(el("span", { className: "who", text: `${AUTHOR[m.author] ?? m.author} · ${fmtDateTime(m.created_at)}` }));
      appendFormatted(bubble, m.text);
      return bubble;
    }),
  );
  if (changed || atBottom) box.scrollTop = box.scrollHeight;
}

async function conversationAction(action) {
  if (!state.selected) return;
  $("reply-error").textContent = "";
  try {
    await api(`/api/admin/conversations/${state.selected}/${action}`, { method: "POST" });
  } catch (error) {
    $("reply-error").textContent = error.message;
  }
  refresh();
}

$("assign").addEventListener("click", () => conversationAction("assign"));
$("release").addEventListener("click", () => conversationAction("release"));

$("reply-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("reply-text").value.trim();
  if (!text || !state.selected) return;
  $("reply-error").textContent = "";
  try {
    await api(`/api/admin/conversations/${state.selected}/reply`, {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    $("reply-text").value = "";
  } catch (error) {
    $("reply-error").textContent = error.message;
  }
  refresh();
});

// --- agendamentos ---------------------------------------------------------------------------

async function loadSpecialties() {
  if (state.specialtiesLoaded) return;
  try {
    const specialties = await api("/api/admin/specialties");
    $("f-specialty").append(...specialties.map((s) => el("option", { value: s.slug, text: s.name })));
    state.specialtiesLoaded = true;
  } catch {
    /* tenta de novo depois */
  }
}

async function loadAppointments() {
  loadSpecialties();
  const params = new URLSearchParams();
  if ($("f-date").value) params.set("date", $("f-date").value);
  if ($("f-specialty").value) params.set("specialty", $("f-specialty").value);
  if ($("f-status").value) params.set("status", $("f-status").value);
  let rows;
  try {
    rows = await api(`/api/admin/appointments?${params}`);
  } catch {
    return;
  }
  $("appointment-rows").replaceChildren(
    ...rows.map((a) => {
      const cancel =
        a.status === "confirmed"
          ? el("button", {
              type: "button",
              text: "Cancelar",
              onclick: () => cancelAppointment(a),
            })
          : null;
      return el(
        "tr",
        { className: a.status },
        el("td", { text: fmtDateTime(a.starts_at) }),
        el("td", { text: `${a.specialty} (${a.professional})` }),
        el("td", { text: a.patient_name }),
        el("td", { text: a.patient_phone }),
        el("td", { text: CHANNEL[a.channel] ?? a.channel }),
        el("td", { text: a.status === "confirmed" ? "Confirmada" : "Cancelada" }),
        el("td", {}, cancel),
      );
    }),
  );
  $("appointments-empty").hidden = rows.length > 0;
}

async function cancelAppointment(appointment) {
  const label = `${appointment.patient_name}, ${fmtDateTime(appointment.starts_at)}`;
  if (!window.confirm(`Cancelar a consulta de ${label}?`)) return;
  try {
    await api(`/api/admin/appointments/${appointment.id}/cancel`, { method: "POST" });
  } catch (error) {
    window.alert(error.message);
  }
  loadAppointments();
}

$("filters").addEventListener("change", loadAppointments);

// --- início ---------------------------------------------------------------------------------

api("/api/admin/me")
  .then(startApp)
  .catch(() => showLogin());
