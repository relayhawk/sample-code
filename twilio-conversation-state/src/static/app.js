/*
 * Conversation State dashboard.
 *
 * Live view and replay are the same thing: a call is an ordered list of events,
 * and the page renders "the call as of event N". Live mode keeps N pinned to the
 * newest event; replay lets you move N with the timeline, the arrow keys, or the
 * call recording.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const DEFAULT_STEPS = [
    { key: "first_name", label: "First name" },
    { key: "last_name", label: "Last name" },
    { key: "email", label: "Email address" },
    { key: "phone", label: "Phone number" },
    { key: "review", label: "Review" },
    { key: "complete", label: "Complete" },
  ];
  const ENDED = new Set(["completed", "busy", "failed", "no-answer", "canceled"]);

  const S = {
    config: null,
    calls: new Map(), // call_sid -> call row
    latestSnapshot: new Map(), // call_sid -> newest snapshot seen (for the sidebar)
    selected: null,
    call: null,
    events: [],
    idx: -1,
    follow: true, // pinned to the newest event
    playing: false,
    timer: null,
    showRaw: false,
    prevValues: {},
    hasAudio: false,
    pending: [], // live events that arrive while the selected call is still loading
  };

  // ---------------------------------------------------------------- helpers

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function el(tag, cls, html) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html !== undefined) node.innerHTML = html;
    return node;
  }

  async function api(path, options) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
    return response.json();
  }

  function formatPhone(number) {
    const m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(number || "");
    return m ? `(${m[1]}) ${m[2]}-${m[3]}` : number || "";
  }

  function formatClock(ms) {
    const total = Math.max(0, Math.floor(ms / 1000));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  function formatOffset(ms) {
    const s = Math.max(0, ms) / 1000;
    return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
  }

  function timeAgo(iso) {
    const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return new Date(iso).toLocaleDateString();
  }

  const isLive = (call) => call && call.status === "live";

  // Values are stored normalized (phones in E.164); show them the way people read them.
  const displayValue = (field) => (field.key === "phone" && field.value ? formatPhone(field.value) : field.value || "");

  function callName(call, snapshot) {
    const snap = snapshot || call?.final_state;
    if (snap) {
      const value = (key) => snap.fields.find((f) => f.key === key)?.value;
      const name = [value("first_name"), value("last_name")].filter(Boolean).join(" ");
      if (name) return name;
    }
    const other = call?.direction === "outbound" ? call?.to_number : call?.from_number;
    return formatPhone(other) || "Unknown caller";
  }

  function stepLabel(snapshot) {
    if (!snapshot) return "";
    if (snapshot.completed) return "Complete";
    return snapshot.steps.find((s) => s.key === snapshot.step)?.label || snapshot.step;
  }

  // ---------------------------------------------------------------- sidebar

  function renderCallList() {
    const list = $("callList");
    const calls = [...S.calls.values()].sort((a, b) => b.started_at.localeCompare(a.started_at));
    $("callCount").textContent = calls.length;
    list.innerHTML = "";
    if (!calls.length) {
      list.append(el("li", "none", "No calls yet."));
      return;
    }
    for (const call of calls) {
      const snapshot = S.latestSnapshot.get(call.call_sid) || call.final_state;
      const completed = snapshot?.completed;
      const item = el("li", `call-item${isLive(call) ? " live" : ""}${completed ? " complete" : ""}${call.call_sid === S.selected ? " selected" : ""}`);
      const where = isLive(call) ? `<span class="ci-step">${esc(stepLabel(snapshot) || "Connecting")}</span>` :
        `<span>${completed ? "Collected" : esc(call.status)}</span>`;
      const duration = call.duration_sec ? `<span>${formatClock(call.duration_sec * 1000)}</span>` : "";
      item.innerHTML = `
        <span class="ci-dot"></span>
        <span class="ci-name">${esc(callName(call, snapshot))}</span>
        <span class="ci-time">${esc(timeAgo(call.started_at))}</span>
        <span class="ci-sub">${where}${duration}<span>${esc(call.direction || "")}</span></span>`;
      item.addEventListener("click", () => select(call.call_sid, true));
      list.append(item);
    }
  }

  // ---------------------------------------------------------------- selection

  async function select(callSid, push) {
    stop();
    S.selected = callSid;
    S.call = null;
    S.pending = [];
    if (push) history.pushState({}, "", `/calls/${callSid}`);
    renderCallList();
    let payload;
    try {
      payload = await api(`/api/calls/${encodeURIComponent(callSid)}`);
    } catch (error) {
      console.error(error);
      return;
    }
    if (S.selected !== callSid) return;
    S.call = payload.call;
    S.calls.set(callSid, payload.call);
    S.events = payload.events;
    const lastSeq = S.events.length ? S.events[S.events.length - 1].seq : 0;
    S.events.push(...S.pending.filter((e) => e.seq > lastSeq));
    S.pending = [];
    S.follow = isLive(S.call);
    // Finished calls open at the end so the whole result is visible; step back from there.
    S.idx = S.events.length - 1;
    S.prevValues = {};
    $("emptyState").hidden = true;
    $("callView").hidden = false;
    $("player").hidden = false;
    setupAudio();
    render();
  }

  function setupAudio() {
    const audio = $("audio");
    S.hasAudio = false;
    audio.removeAttribute("src");
    audio.load();
    if (S.call?.recording_sid && !isLive(S.call)) {
      audio.src = `/api/calls/${encodeURIComponent(S.call.call_sid)}/recording`;
      $("audioState").textContent = "Loading recording…";
    } else {
      $("audioState").textContent = isLive(S.call) ? "" : "No recording";
    }
  }

  // ---------------------------------------------------------------- rendering

  function snapshotAt(index) {
    for (let i = index; i >= 0; i--) if (S.events[i].kind === "snapshot") return S.events[i].data;
    return null;
  }

  function render() {
    if (!S.call) return;
    const snapshot = snapshotAt(S.idx);
    renderHeader(snapshot);
    renderFlow(snapshot);
    renderTranscript();
    renderFields(snapshot);
    renderDirective(snapshot);
    renderEvent();
    renderPlayer();
  }

  function renderHeader(snapshot) {
    const call = S.call;
    $("callTitle").textContent = callName(call, snapshot);
    const status = $("callStatus");
    const final = call.final_state?.completed;
    status.className = `pill ${isLive(call) ? "live" : final ? "done" : ENDED.has(call.status) && call.status !== "completed" ? "failed" : ""}`;
    status.textContent = isLive(call) ? "Live" : final ? "Collected" : call.status;
    $("callFrom").textContent = `${call.direction === "outbound" ? "To" : "From"} ${formatPhone(call.direction === "outbound" ? call.to_number : call.from_number) || "unknown"}`;
    $("callStarted").textContent = new Date(call.started_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    $("callSid").textContent = call.call_sid;
    renderDuration();
  }

  function renderDuration() {
    if (!S.call) return;
    const start = new Date(S.call.started_at).getTime();
    const end = S.call.ended_at ? new Date(S.call.ended_at).getTime() : Date.now();
    $("callDuration").textContent = S.call.duration_sec ? formatClock(S.call.duration_sec * 1000) : formatClock(end - start);
  }

  function renderFlow(snapshot) {
    const rail = $("flowRail");
    const steps = snapshot?.steps || DEFAULT_STEPS;
    rail.innerHTML = "";
    steps.forEach((step, i) => {
      const field = snapshot?.fields.find((f) => f.key === step.key);
      let state = "pending";
      let value = "";
      if (snapshot) {
        if (field) {
          const done = field.needs_confirmation ? field.status === "confirmed" : field.status !== "empty";
          state = done ? "done" : snapshot.step === step.key ? "current" : "pending";
          value = displayValue(field);
        } else if (step.key === "review") {
          state = snapshot.summary_confirmed ? "done" : snapshot.step === "review" ? "current" : "pending";
          value = snapshot.summary_confirmed ? "Confirmed" : snapshot.pending_readback === "summary" ? "Awaiting yes" : "";
        } else if (step.key === "complete") {
          state = snapshot.completed ? "current final" : "pending";
          value = snapshot.completed ? "Done" : "";
        }
      }
      const visits = snapshot ? snapshot.visited.filter((k) => k === step.key).length : 0;
      const node = el("li", `step ${state}`);
      const mark = state === "done" || state.includes("final") ? "✓" : String(i + 1);
      node.innerHTML = `
        ${visits > 1 ? `<span class="step-badge" title="Revisited">×${visits}</span>` : ""}
        <span class="step-node">${mark}</span>
        <span class="step-label">${esc(step.label)}</span>
        <span class="step-value" title="${esc(value)}">${esc(value)}</span>`;
      rail.append(node);
    });
  }

  function renderTranscript() {
    const box = $("transcript");
    const visible = S.events.slice(0, S.idx + 1);
    // Which agent line did each interrupt cut off?
    const heardFor = new Map();
    let lastAgent = -1;
    visible.forEach((event, i) => {
      if (event.kind === "transcript" && event.data.role === "agent") lastAgent = i;
      if (event.kind === "interrupt" && lastAgent >= 0) heardFor.set(lastAgent, event.data.heard || "");
    });
    // Attach LLM timing to the agent line it produced.
    const timingFor = new Map();
    visible.forEach((event, i) => {
      if (event.kind === "llm" && event.data.first_token_ms != null) {
        for (let j = i - 1; j >= 0 && j >= i - 3; j--) {
          const prev = visible[j];
          if (prev.kind === "transcript" && prev.data.source === "llm") { timingFor.set(j, event.data); break; }
        }
      }
    });

    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    box.innerHTML = "";
    let current = null;
    visible.forEach((event, i) => {
      const node = transcriptNode(event, heardFor.get(i), timingFor.get(i));
      if (!node) return;
      node.dataset.idx = i;
      node.addEventListener("click", () => seek(Number(node.dataset.idx)));
      box.append(node);
      current = node;
    });
    // In replay, outline the latest visible line (state-only events have no line of their own).
    if (current && !S.follow) current.classList.add("current-event");
    if (!box.children.length) box.append(el("div", "tr-empty", "Waiting for the caller…"));
    if (S.follow) {
      if (nearBottom || isLive(S.call)) box.scrollTop = box.scrollHeight;
    } else if (current) {
      // Scroll only the transcript, never the page.
      box.scrollTop = current.offsetTop - box.clientHeight / 2 + current.offsetHeight / 2;
    }
  }

  function transcriptNode(event, heard, timing) {
    const d = event.data;
    switch (event.kind) {
      case "transcript": {
        if (d.role === "caller") {
          const node = el("div", "msg caller");
          node.innerHTML = `<div class="msg-who">Caller</div><div class="bubble">${esc(d.text)}</div>`;
          return node;
        }
        const source = d.source || "llm";
        const node = el("div", `msg agent ${source}`);
        let body = esc(d.text);
        if (heard !== undefined) {
          body = d.text.startsWith(heard)
            ? `${esc(heard)}<span class="unheard">${esc(d.text.slice(heard.length))}</span>`
            : `<span class="unheard">${esc(d.text)}</span>`;
        }
        const label = { llm: "LLM", engine: "Engine", greeting: "Greeting" }[source] || source;
        const time = timing ? `<span class="timing">${timing.first_token_ms} ms to first token</span>` : "";
        node.innerHTML = `<div class="msg-who">Agent <span class="src src-${esc(source)}">${esc(label)}</span>${time}</div>
          <div class="bubble">${body}</div>
          ${heard !== undefined ? `<div class="cut">Caller interrupted${heard ? "" : " before it played"}</div>` : ""}`;
        return node;
      }
      case "tool": {
        const node = el("div", "chip-row");
        const args = Object.entries(d.arguments || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ");
        node.innerHTML = `<span class="chip tool${d.ok ? "" : " fail"}" title="${esc(d.message)}">
          <b>${esc(d.name)}</b><span class="args">(${esc(args)})</span>${d.ok ? "✓" : "✕"}</span>
          <span class="timing">${esc(d.message)}</span>`;
        return node;
      }
      case "error": {
        const node = el("div", "chip-row");
        node.innerHTML = `<span class="chip err">Error in ${esc(d.where)}: ${esc(d.message)}</span>`;
        return node;
      }
      default: {
        const label = systemLabel(event);
        if (!label) return null;
        const node = el("div", "chip-row");
        node.innerHTML = `<span class="chip sys">${esc(label)}</span>`;
        return node;
      }
    }
  }

  function systemLabel(event) {
    const d = event.data;
    switch (event.kind) {
      case "call.started": return `Call ${d.direction === "inbound" ? "from" : "to"} ${formatPhone(d.direction === "inbound" ? d.from : d.to)}`;
      case "session.setup": return "ConversationRelay connected";
      case "recording.started": return "Recording started";
      case "dtmf": return `Keypad: ${d.digit}`;
      case "session.end": return "Engine ended the session";
      case "session.ended": return `ConversationRelay session ${d.status || "ended"}`;
      case "twilio.status": return ENDED.has(d.status) ? `Call ${d.status}${d.duration_sec ? ` · ${d.duration_sec}s` : ""}` : null;
      case "socket.closed": return "Socket closed";
      default: return null;
    }
  }

  function renderFields(snapshot) {
    const body = $("fieldRows");
    body.innerHTML = "";
    $("turnLabel").textContent = snapshot ? `Turn ${snapshot.turn}` : "";
    const fields = snapshot?.fields || [];
    if (!fields.length) {
      body.innerHTML = `<tr><td colspan="3" class="muted">Nothing yet.</td></tr>`;
      return;
    }
    for (const f of fields) {
      const row = el("tr");
      const awaiting = snapshot.pending_readback === f.key;
      const status = awaiting ? "awaiting" : f.status;
      const statusText = awaiting ? "Awaiting yes" : f.status;
      const tries = [f.attempts > 1 ? `${f.attempts} attempts` : "", f.rejections ? `${f.rejections} rejected` : ""].filter(Boolean).join(" · ");
      row.innerHTML = `
        <td class="label">${esc(f.label)}</td>
        <td class="value${f.value ? "" : " empty"}" title="${esc(f.value || "")}">${esc(displayValue(f) || "—")}${tries ? `<span class="attempts">${esc(tries)}</span>` : ""}</td>
        <td><span class="status ${esc(status)}">${esc(statusText)}</span></td>`;
      if (S.prevValues[f.key] !== undefined && S.prevValues[f.key] !== `${f.value}|${status}`) row.classList.add("flash");
      S.prevValues[f.key] = `${f.value}|${status}`;
      body.append(row);
    }
  }

  function renderDirective(snapshot) {
    const box = $("directive");
    if (!snapshot) {
      box.innerHTML = `<span class="muted">The engine starts once ConversationRelay connects.</span>`;
      return;
    }
    const d = snapshot.directive;
    if (snapshot.completed) {
      box.innerHTML = `<div class="d-kind say">Engine speaks, then hangs up</div><blockquote>${esc(d.say)}</blockquote>`;
      return;
    }
    let html = d.say
      ? `<div class="d-kind say">Engine speaks verbatim</div><blockquote>${esc(d.say)}</blockquote>`
      : `<div class="d-kind llm">Instruction to the LLM</div><div>${esc(d.instruction)}</div>`;
    if (snapshot.pending_readback) {
      const target = snapshot.pending_readback === "summary" ? "the summary" :
        snapshot.fields.find((f) => f.key === snapshot.pending_readback)?.label;
      html += `<div class="d-wait">Waiting for the caller to confirm ${esc(target)}</div>`;
    }
    box.innerHTML = html;
  }

  function renderEvent() {
    const event = S.events[S.idx];
    const box = $("eventDetail");
    if (!event) {
      box.innerHTML = `<span class="muted">No events yet.</span>`;
      $("eventRaw").textContent = "";
      return;
    }
    const rows = [["Event", `#${event.seq} ${event.kind}`], ["At", formatOffset(event.t_ms)]];
    const d = event.data;
    if (event.kind === "snapshot") rows.push(["Step", `${d.step} (${d.reason})`]);
    if (event.kind === "transcript") rows.push(["Said", `${d.role}${d.source ? ` / ${d.source}` : ""}`]);
    if (event.kind === "tool") rows.push(["Result", d.message]);
    if (event.kind === "llm") {
      rows.push(["Pass", `${d.pass} (tool_choice=${d.tool_choice})`]);
      rows.push(["Timing", `${d.first_token_ms ?? "—"} ms first token · ${d.total_ms} ms total`]);
      if (d.tool_calls?.length) rows.push(["Tools", d.tool_calls.map((t) => t.name).join(", ")]);
    }
    if (event.kind === "interrupt") rows.push(["Heard", d.heard || "(nothing)"]);
    box.innerHTML = `<dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
    $("eventRaw").textContent = JSON.stringify(event, null, 2);
    $("eventRaw").hidden = !S.showRaw;
  }

  function tickClass(event) {
    const d = event.data;
    if (event.kind === "transcript") return d.role === "caller" ? "caller" : d.source === "engine" ? "engine" : "llm";
    if (event.kind === "tool") return "tool";
    if (event.kind === "error") return "error";
    return "";
  }

  let renderedTicks = 0;
  function renderPlayer() {
    const n = S.events.length;
    const scrub = $("scrub");
    scrub.max = Math.max(0, n - 1);
    scrub.value = Math.max(0, S.idx);
    const now = S.events[S.idx];
    const end = S.events[n - 1];
    $("posLabel").textContent = `${S.idx + 1} / ${n}  ·  ${formatOffset(now?.t_ms || 0)} / ${formatOffset(end?.t_ms || 0)}`;
    $("btnPrev").disabled = $("btnFirst").disabled = S.idx <= 0;
    $("btnNext").disabled = S.idx >= n - 1;
    $("btnLive").hidden = !isLive(S.call);
    $("btnLive").classList.toggle("on", S.follow);
    $("btnPlay").textContent = S.playing ? "❚❚" : "▶";
    $("btnPlay").setAttribute("aria-label", S.playing ? "Pause" : "Play");
    $("btnPlay").disabled = isLive(S.call) && S.follow;

    if (renderedTicks !== n) {
      // Ticks are spaced by real time, so long pauses in the call show up as gaps.
      const ticks = $("ticks");
      ticks.innerHTML = "";
      const span = Math.max(1, end?.t_ms || 1);
      for (const event of S.events) {
        const cls = tickClass(event);
        if (!cls) continue;
        const tick = el("span", `tick ${cls}`);
        tick.style.left = `${(event.t_ms / span) * 100}%`;
        ticks.append(tick);
      }
      renderedTicks = n;
    }
  }

  // ---------------------------------------------------------------- navigation

  function seek(index, fromAudio) {
    if (!S.events.length) return;
    S.idx = Math.min(S.events.length - 1, Math.max(0, index));
    S.follow = isLive(S.call) && S.idx === S.events.length - 1;
    if (!fromAudio && S.hasAudio) {
      const audio = $("audio");
      const offset = S.call.recording_offset_ms || 0;
      audio.currentTime = Math.max(0, (S.events[S.idx].t_ms - offset) / 1000);
    }
    render();
  }

  function goLive() {
    stop();
    S.follow = true;
    S.idx = S.events.length - 1;
    render();
  }

  function play() {
    if (!S.events.length) return;
    if (S.idx >= S.events.length - 1) seek(0);
    S.playing = true;
    S.follow = false;
    if (S.hasAudio) {
      const audio = $("audio");
      audio.playbackRate = Number($("speed").value);
      audio.play().catch(() => { S.hasAudio = false; play(); });
    } else {
      scheduleNext();
    }
    renderPlayer();
  }

  function scheduleNext() {
    clearTimeout(S.timer);
    if (!S.playing) return;
    if (S.idx >= S.events.length - 1) { stop(); return; }
    // Wait the real gap between events (capped so long silences don't stall the replay).
    const gap = Math.min(2500, Math.max(120, S.events[S.idx + 1].t_ms - S.events[S.idx].t_ms));
    S.timer = setTimeout(() => { seek(S.idx + 1, true); scheduleNext(); }, gap / Number($("speed").value));
  }

  function stop() {
    S.playing = false;
    clearTimeout(S.timer);
    $("audio").pause();
    if (S.call) renderPlayer();
  }

  // ---------------------------------------------------------------- live socket

  function connectSocket() {
    const state = $("socketState");
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ui/ws`);
    ws.onopen = () => {
      state.className = "socket ok";
      state.querySelector(".socket-label").textContent = "Live";
      connectSocket.delay = 1000;
      refreshCalls(); // catch up on anything missed while disconnected
    };
    ws.onclose = () => {
      state.className = "socket bad";
      state.querySelector(".socket-label").textContent = "Reconnecting";
      setTimeout(connectSocket, connectSocket.delay);
      connectSocket.delay = Math.min(15000, (connectSocket.delay || 1000) * 2);
    };
    ws.onmessage = (message) => onLive(JSON.parse(message.data));
  }

  function onLive(message) {
    if (message.type === "call") {
      const call = message.call;
      S.calls.set(call.call_sid, call);
      if (call.call_sid === S.selected && S.call) {
        const wasLive = isLive(S.call);
        S.call = call;
        if (wasLive && !isLive(call)) { S.follow = false; setupAudio(); }
        else if (!S.hasAudio && call.recording_sid && !isLive(call)) setupAudio();
        render();
      } else if (!S.selected && isLive(call)) {
        select(call.call_sid, true); // auto-open the first live call
      }
      renderCallList();
      return;
    }
    if (message.type !== "event") return;
    const event = message.event;
    if (event.kind === "snapshot") {
      S.latestSnapshot.set(event.call_sid, event.data);
      renderCallList();
    }
    if (event.call_sid !== S.selected) return;
    if (!S.call) { S.pending.push(event); return; }
    if (S.events.length && event.seq <= S.events[S.events.length - 1].seq) return;
    S.events.push(event);
    if (S.follow) S.idx = S.events.length - 1;
    render();
  }

  async function refreshCalls() {
    try {
      const calls = await api("/api/calls");
      for (const call of calls) S.calls.set(call.call_sid, call);
      renderCallList();
      // A live call may have moved on while we were disconnected; a replay keeps its position.
      if (S.selected && isLive(S.call)) await select(S.selected, false);
    } catch (error) {
      console.error(error);
    }
  }

  // ---------------------------------------------------------------- wiring

  function wire() {
    $("btnFirst").onclick = () => { stop(); seek(0); };
    $("btnPrev").onclick = () => { stop(); seek(S.idx - 1); };
    $("btnNext").onclick = () => { stop(); seek(S.idx + 1); };
    $("btnPlay").onclick = () => (S.playing ? stop() : play());
    $("btnLive").onclick = goLive;
    $("scrub").oninput = (e) => { stop(); seek(Number(e.target.value)); };
    $("speed").onchange = () => { if (S.hasAudio) $("audio").playbackRate = Number($("speed").value); };
    $("toggleRaw").onclick = () => {
      S.showRaw = !S.showRaw;
      $("toggleRaw").textContent = S.showRaw ? "Hide JSON" : "Show JSON";
      $("eventRaw").hidden = !S.showRaw;
    };

    const audio = $("audio");
    audio.addEventListener("loadedmetadata", () => {
      S.hasAudio = true;
      $("audioState").textContent = `Recording · ${formatClock(audio.duration * 1000)}`;
    });
    audio.addEventListener("error", () => {
      if (!audio.getAttribute("src")) return;
      S.hasAudio = false;
      $("audioState").textContent = "Recording unavailable";
    });
    audio.addEventListener("timeupdate", () => {
      if (!S.playing || !S.hasAudio) return;
      const t = audio.currentTime * 1000 + (S.call.recording_offset_ms || 0);
      let index = 0;
      while (index + 1 < S.events.length && S.events[index + 1].t_ms <= t) index++;
      if (index !== S.idx) seek(index, true);
    });
    audio.addEventListener("ended", stop);

    document.addEventListener("keydown", (e) => {
      if (!S.call || ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return;
      if (e.key === "ArrowLeft") { stop(); seek(S.idx - 1); e.preventDefault(); }
      else if (e.key === "ArrowRight") { stop(); seek(S.idx + 1); e.preventDefault(); }
      else if (e.key === "Home") { stop(); seek(0); e.preventDefault(); }
      else if (e.key === "End") { isLive(S.call) ? goLive() : (stop(), seek(S.events.length - 1)); e.preventDefault(); }
      else if (e.key === " ") { S.playing ? stop() : play(); e.preventDefault(); }
    });

    $("callMe").addEventListener("submit", async (e) => {
      e.preventDefault();
      const button = e.target.querySelector("button");
      button.disabled = true;
      try {
        const { call_sid } = await api("/api/calls/outbound", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to: $("callMeTo").value }),
        });
        await refreshCalls();
        select(call_sid, true);
      } catch (error) {
        alert(`Could not place the call: ${error.message}`);
      } finally {
        button.disabled = false;
      }
    });

    window.addEventListener("popstate", () => {
      const match = /^\/calls\/([^/]+)/.exec(location.pathname);
      if (match) select(decodeURIComponent(match[1]), false);
    });

    setInterval(() => {
      if (isLive(S.call)) renderDuration();
      renderCallList(); // keeps "2m ago" fresh
    }, 1000 * 15);
    setInterval(() => { if (isLive(S.call)) renderDuration(); }, 1000);
  }

  async function init() {
    wire();
    try {
      S.config = await api("/api/config");
      $("company").textContent = S.config.company;
      $("voiceUrl").textContent = S.config.voice_url;
      if (S.config.phone_number) {
        $("dialInfo").hidden = false;
        $("dialNumber").textContent = formatPhone(S.config.phone_number);
        $("dialNumber").href = `tel:${S.config.phone_number}`;
      }
      $("callMe").hidden = !S.config.outbound_enabled;
    } catch (error) {
      console.error(error);
    }
    await refreshCalls();
    // Seed sidebar names for calls that are still in progress.
    for (const call of S.calls.values()) {
      if (!isLive(call)) continue;
      api(`/api/calls/${encodeURIComponent(call.call_sid)}`).then(({ events }) => {
        const snap = [...events].reverse().find((e) => e.kind === "snapshot");
        if (snap) { S.latestSnapshot.set(call.call_sid, snap.data); renderCallList(); }
      }).catch(() => {});
    }
    const match = /^\/calls\/([^/]+)/.exec(location.pathname);
    if (match) await select(decodeURIComponent(match[1]), false);
    connectSocket();
  }

  init();
})();
