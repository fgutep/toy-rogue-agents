// agentlab operator dashboard — dependency-free, read-only + logged operator actions.
// Correctness and update latency over polish (design doc 04 §6).
"use strict";

const state = { maxSeq: 0, agents: new Map(), alerts: [] };

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

function agentPane(agentId) {
  if (!state.agents.has(agentId)) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<h2>agent:${esc(agentId)}</h2><div class="body" data-log></div>`;
    el("agents").appendChild(card);
    state.agents.set(agentId, card.querySelector("[data-log]"));
  }
  return state.agents.get(agentId);
}

function appendTurn(agentId, html) {
  const log = agentPane(agentId);
  const div = document.createElement("div");
  div.className = "turn";
  div.innerHTML = html;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function handleEvent(e) {
  const agentId = e.source && e.source.startsWith("agent:") ? e.source.slice(6) : null;
  if (e.kind === "model_turn") {
    const calls = (e.payload.tool_calls || [])
      .map((tc) => `<div class="tool">↳ ${esc(tc.name)}(${esc(JSON.stringify(tc.arguments))})</div>`)
      .join("");
    appendTurn(agentId, `<span class="role">assistant</span> <span class="muted">step ${e.payload.step}</span>
      <pre>${esc(e.payload.content || "")}</pre>${calls}`);
  } else if (e.kind === "tool_result") {
    appendTurn(agentId, `<span class="tool">tool ${esc(e.payload.tool)}</span>
      <span class="muted">${e.payload.ok ? "ok" : "err"}</span><pre>${esc(e.payload.text)}</pre>`);
  } else if (e.kind === "agent_start" && agentId) {
    agentPane(agentId);
  } else if (e.kind === "agent_stop" && agentId) {
    appendTurn(agentId, `<span class="muted">— stopped: ${esc(e.payload.stop_reason)} (${e.payload.steps_taken} steps)</span>`);
  } else if (e.kind === "honeypot_hit") {
    addAlert(e);
  }
}

function addAlert(e) {
  const box = el("alerts");
  if (state.alerts.length === 0) box.innerHTML = "";
  state.alerts.push(e);
  const item = document.createElement("div");
  item.className = "alert-item";
  item.innerHTML = `<strong>honeypot hit</strong> · agent:${esc(e.payload.agent_id)}
    <div class="muted">${esc(e.payload.method)} /${esc(e.payload.path)} · ${esc(e.ts || "")}</div>`;
  item.onclick = () => {
    const pane = state.agents.get(e.payload.agent_id);
    if (pane) pane.scrollIntoView({ behavior: "smooth" });
  };
  box.prepend(item);
}

async function refreshTimeline() {
  try {
    const tl = await (await fetch("/api/timeline")).json();
    const rows = (tl.graph.edges || []).slice(-80).reverse().map((edge) =>
      `<tr><td>${esc(edge.ts || "")}</td><td>agent:${esc(edge.agent)}</td>
       <td>${edge.type === "write" ? "PUT" : "GET"}</td><td><code>${esc(edge.artifact)}</code></td></tr>`
    ).join("");
    el("provenance").querySelector("tbody").innerHTML =
      `<tr><th>ts</th><th>agent</th><th>op</th><th>path</th></tr>` + rows;
  } catch (_) { /* transient */ }
}

async function refreshReviewQueue() {
  try {
    const data = await (await fetch("/api/forensics/review-queue")).json();
    const box = el("review-queue");
    if (!(data.pending || []).length) {
      box.innerHTML = '<div class="muted">none pending</div>';
      return;
    }
    box.innerHTML = (data.pending || []).map((record) =>
      `<div class="alert-item"><strong>agent:${esc(record.agent_id)}</strong>
       <div class="muted">severe positive requires raw-transcript check</div>
       <button data-review="confirmed" data-agent="${esc(record.agent_id)}">confirm</button>
       <button data-review="rejected" data-agent="${esc(record.agent_id)}">reject</button></div>`
    ).join("");
    box.querySelectorAll("button[data-review]").forEach((button) => {
      button.onclick = async () => {
        await fetch("/api/forensics/review", {
          method: "POST", headers: {"content-type": "application/json"},
          body: JSON.stringify({agent_id: button.dataset.agent, decision: button.dataset.review}),
        });
        refreshReviewQueue();
      };
    });
  } catch (_) { /* post-run records may not exist yet */ }
}

async function loadManifest() {
  try {
    const m = await (await fetch("/api/manifest")).json();
    el("runid").textContent = "run: " + (m.run_id || "—");
    const rows = Object.entries(m.assignments || {}).map(([a, iids]) =>
      `<tr><td>agent:${esc(a)}</td><td>${iids.map(esc).join("<br>")}</td></tr>`).join("");
    el("manifest").innerHTML = `<div class="muted">seed ${esc(m.seed)}</div>
      <table><tr><th>agent</th><th>instances</th></tr>${rows}</table>`;
  } catch (_) {
    el("manifest").innerHTML = `<div class="muted">no manifest</div>`;
  }
}

function ingest(batch) {
  for (const e of batch.events || []) handleEvent(e);
  if (batch.max_seq) state.maxSeq = batch.max_seq;
  el("count").textContent = state.maxSeq + " events";
}

async function poll() {
  try {
    const data = await (await fetch("/api/events?since_seq=" + state.maxSeq)).json();
    ingest(data);
  } catch (_) { /* retry next tick */ }
}

function connectWs() {
  try {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
    ws.onopen = () => (el("conn").textContent = "live");
    ws.onclose = () => { el("conn").textContent = "polling"; setInterval(poll, 1500); };
    ws.onerror = () => ws.close();
    ws.onmessage = (ev) => ingest(JSON.parse(ev.data));
  } catch (_) {
    el("conn").textContent = "polling";
    setInterval(poll, 1500);
  }
}

document.querySelectorAll("button[data-action]").forEach((btn) => {
  btn.onclick = async () => {
    await fetch("/api/operator/action", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: btn.dataset.action, scope: btn.dataset.scope }),
    });
  };
});

loadManifest();
refreshReviewQueue();
poll();
connectWs();
setInterval(refreshTimeline, 2000);
setInterval(refreshReviewQueue, 5000);
refreshTimeline();
