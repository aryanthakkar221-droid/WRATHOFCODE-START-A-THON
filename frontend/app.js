/* KnowFlow frontend: vanilla JS single-page app, served by the FastAPI backend.
 * Routes (hash): #/record  #/ask  #/meetings  #/dashboard  #/m/<machine>  #/sign/<machine>  #/card/<id>
 * API base: same origin by default; override with ?api=https://host (remembered).
 */
// Safe localStorage: private mode or blocked storage must never break the app.
const store = {
  get(k) { try { return localStorage.getItem("kf_" + k); } catch (_) { return null; } },
  set(k, v) { try { localStorage.setItem("kf_" + k, v); } catch (_) {} },
};

(() => {
  "use strict";

  // ------------------------------------------------------------ config & state
  const qs = new URLSearchParams(location.search);
  if (qs.get("api")) store.set("api", qs.get("api").replace(/\/$/, ""));
  const API = store.get("api") || "";

  const state = {
    user: store.get("user") || "Herr Müller",
    experts: [],
    machines: [],
    lang: store.get("lang") || "",  // "" = auto-detect (German, English, ...)
  };

  const LANGS = [["", "Auto"], ["de", "Deutsch"], ["en", "English"]];
  function langChips() {
    return `<div class="chips lang" role="group" aria-label="Spoken language">${LANGS.map(([v, l]) =>
      `<button type="button" class="chip ${state.lang === v ? "on" : ""}" data-lang="${v}">${l}</button>`).join("")}</div>`;
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-lang]");
    if (!b) return;
    state.lang = b.dataset.lang;
    store.set("lang", state.lang);
    document.querySelectorAll("[data-lang]").forEach((x) => x.classList.toggle("on", x.dataset.lang === state.lang));
  });

  const QUESTIONS = [
    "What was difficult today?",
    "What did you solve, and how?",
    "What should the next person watch out for?",
  ];

  const view = document.getElementById("view");
  const toastEl = document.getElementById("toast");
  const modal = document.getElementById("modal");

  // ------------------------------------------------------------ helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function initials(name) {
    const parts = String(name || "?").replace(/^(Herr|Frau)\s+/i, "").split(/\s+/);
    return (parts[0][0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
  }
  const AVATAR_COLORS = ["#3f7d20", "#8a5a2b", "#2f6690", "#9c3d54", "#6b5b95", "#b7791f", "#2c7a7b"];
  function avatarColor(name) {
    let h = 0;
    for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return AVATAR_COLORS[h % AVATAR_COLORS.length];
  }
  function avatar(name, cls = "") {
    return `<span class="avatar ${cls}" style="background:${avatarColor(name)}" aria-hidden="true">${esc(initials(name))}</span>`;
  }
  function fmtDate(iso, withYear) {
    if (!iso) return "";
    const d = new Date(iso);
    const opts = { day: "numeric", month: "short" };
    if (withYear || d.getFullYear() !== new Date().getFullYear()) opts.year = "numeric";
    return d.toLocaleDateString("en-GB", opts);
  }
  function ago(iso) {
    if (!iso) return "";
    const days = Math.floor((Date.now() - new Date(iso)) / 864e5);
    if (days <= 0) return "today";
    if (days === 1) return "yesterday";
    if (days < 30) return days + " days ago";
    return fmtDate(iso);
  }
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => toastEl.classList.remove("show"), 2600);
  }
  function render(html) {
    view.innerHTML = html;
    view.classList.remove("view-enter");
    void view.offsetWidth;
    view.classList.add("view-enter");
  }
  function slug(s) {
    return String(s || "").toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
      .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }

  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.json !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.json);
    } else if (opts.form) {
      init.body = opts.form;
    }
    let res;
    try {
      res = await fetch(API + path, init);
    } catch (e) {
      throw new Error("Can't reach the KnowFlow server. Check the Wi-Fi.");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Request failed (" + res.status + ")");
    return data;
  }

  // ------------------------------------------------------------ knowledge card
  const KIND = { fix: ["Fix", ""], mistake: ["What went wrong", "mistake"], decision: ["Decision", "meeting"], lesson: ["Lesson", "meeting"] };

  function cardHTML(c, opts = {}) {
    const [kindLabel, kindCls] = KIND[c.kind] || ["Fix", ""];
    const rows = [
      ["Problem", c.problem, "problem"],
      ["Cause", c.cause, "cause"],
      ["Solution", c.solution, "solution"],
      ["Watch out", c.warning, "warning"],
    ].filter((r) => r[1] && !(opts.titleFromProblem && r[2] === "problem"));
    const status = c.status === "approved"
      ? `<span class="badge ok">✓ Approved${c.approved_by && c.approved_by !== c.author ? " by " + esc(c.approved_by) : ""}</span>`
      : `<span class="badge draft">Draft · waiting for approval</span>`;
    const conf = Math.round((c.confidence || 0.5) * 100);
    const votes = c.worked_count + c.failed_count;
    return `
    <article class="kcard ${c.outdated ? "outdated" : ""}" data-card="${c.id}">
      <div class="kcard-head">
        <div class="badges">
          ${c.machine ? `<a class="badge machine" href="#/m/${esc(c.machine_id)}">⚙️ ${esc(c.machine)}</a>` : ""}
          ${kindCls || c.kind !== "fix" ? `<span class="badge ${kindCls}">${kindLabel}</span>` : ""}
          ${c.source === "meeting" ? `<span class="badge meeting">From meeting</span>` : ""}
          ${c.outdated ? `<span class="badge bad">⚠ Outdated</span>` : ""}
        </div>
        <div class="badges">${status}</div>
      </div>
      ${opts.titleFromProblem && c.problem ? `<div class="kcard-title">${esc(c.problem)}</div>` : ""}
      <dl>
        ${rows.map((r) => `<div class="field-row ${r[2]}"><dt>${r[0]}</dt><dd>${esc(r[1])}</dd></div>`).join("")}
      </dl>
      ${opts.showTranscript && c.transcript ? `<div class="transcript">“${esc(c.transcript)}”</div>` : ""}
      <div class="author">
        ${avatar(c.author)}
        <div class="author-meta grow">
          <b>${esc(c.author)}</b>
          <small>${c.author_years_experience ? c.author_years_experience + " years experience · " : ""}${fmtDate(c.date)}${c.last_confirmed ? " · last confirmed " + ago(c.last_confirmed) : ""}</small>
        </div>
        ${c.status === "approved" ? `<div class="small muted" title="Share of people who said it worked">
          ${votes ? `${c.worked_count}/${votes} worked` : "No votes yet"}
          <div class="confidence"><i style="width:${conf}%"></i></div></div>` : ""}
      </div>
      ${opts.feedback && c.status === "approved" ? `
      <div class="feedback">
        <span class="label">Did this work?</span>
        <button class="btn small" data-fb="1" data-id="${c.id}">👍 Yes</button>
        <button class="btn small" data-fb="0" data-id="${c.id}">👎 No</button>
      </div>` : ""}
      ${opts.approve && c.status !== "approved" ? `
      <div class="card-actions">
        <button class="btn primary" data-approve="${c.id}">✓ Approve</button>
        <button class="btn" data-edit="${c.id}">✎ Edit</button>
        <button class="btn danger" data-reject="${c.id}">Discard</button>
      </div>` : ""}
    </article>`;
  }

  // Delegated card actions (feedback, approve, edit, discard) work on every screen.
  view.addEventListener("click", async (e) => {
    const fb = e.target.closest("[data-fb]");
    if (fb) {
      const id = fb.dataset.id;
      fb.parentElement.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        const c = await api(`/cards/${id}/feedback`, { method: "POST", json: { worked: fb.dataset.fb === "1" } });
        replaceCard(c, { feedback: false, titleFromProblem: !!fb.closest("[data-title]") });
        toast(fb.dataset.fb === "1" ? "Thanks! Marked as proven fix." : "Thanks. The expert will see it didn't work.");
      } catch (err) { toast(err.message); }
      return;
    }
    const ap = e.target.closest("[data-approve]");
    if (ap) {
      ap.disabled = true;
      try {
        const c = await api(`/cards/${ap.dataset.approve}/approve`, { method: "POST", json: { expert: state.user } });
        replaceCard(c, { showTranscript: false });
        toast("Approved. It's live, and the recording is deleted.");
        refreshMachines();
      } catch (err) { toast(err.message); ap.disabled = false; }
      return;
    }
    const rj = e.target.closest("[data-reject]");
    if (rj) {
      if (!confirm("Discard this draft? It will be deleted.")) return;
      try {
        await api(`/cards/${rj.dataset.reject}`, { method: "DELETE" });
        const el = view.querySelector(`[data-card="${rj.dataset.reject}"]`);
        if (el) el.remove();
        toast("Draft discarded.");
      } catch (err) { toast(err.message); }
      return;
    }
    const ed = e.target.closest("[data-edit]");
    if (ed) openEdit(ed.dataset.edit);
  });

  function replaceCard(c, opts) {
    const el = view.querySelector(`[data-card="${c.id}"]`);
    if (!el) return;
    const tmp = document.createElement("div");
    tmp.innerHTML = cardHTML(c, opts);
    const fresh = tmp.firstElementChild;
    fresh.style.animation = "none";
    el.replaceWith(fresh);
  }

  async function openEdit(id) {
    const c = await api(`/cards/${id}`);
    modal.innerHTML = `
      <h2>Edit card</h2>
      <form method="dialog" class="stack" id="editForm">
        ${["machine", "problem", "cause", "solution", "warning"].map((f) => `
          <div><label class="field" for="e_${f}">${f[0].toUpperCase() + f.slice(1)}</label>
          <textarea id="e_${f}" name="${f}" rows="2" style="min-height:64px">${esc(c[f] || "")}</textarea></div>`).join("")}
        <div><label class="field" for="e_tags">Tags (comma separated)</label>
          <input type="text" id="e_tags" name="tags" value="${esc((c.tags || []).join(", "))}"></div>
        <div class="row"><button class="btn ghost grow" value="cancel">Cancel</button>
          <button class="btn primary grow" value="save">Save</button></div>
      </form>`;
    modal.showModal();
    modal.onclose = async () => {
      if (modal.returnValue !== "save") return;
      const f = new FormData(modal.querySelector("form"));
      const body = {};
      for (const [k, v] of f.entries()) {
        if (k === "tags") body.tags = v.split(",").map((t) => t.trim()).filter(Boolean);
        else if (v.trim()) body[k] = v.trim();
      }
      try {
        const upd = await api(`/cards/${id}`, { method: "PATCH", json: body });
        replaceCard(upd, { approve: true, showTranscript: true });
        toast("Saved.");
      } catch (err) { toast(err.message); }
    };
  }

  // ------------------------------------------------------------ user picker
  function renderWho() {
    document.getElementById("who").innerHTML = `${avatar(state.user, "sm")}<span>${esc(state.user)}</span>`;
  }
  document.getElementById("who").addEventListener("click", async () => {
    if (!state.experts.length) state.experts = await api("/experts").catch(() => []);
    modal.innerHTML = `
      <h2>Who is using KnowFlow?</h2>
      <p class="sub">Your name goes on the knowledge you share.</p>
      <div class="who-list">
        ${state.experts.map((x) => `
          <button type="button" data-name="${esc(x.name)}" class="${x.name === state.user ? "on" : ""}">
            ${avatar(x.name)}<span><b>${esc(x.name)}</b><br><small class="muted">${x.years_experience} years experience${x.retires_in_months != null ? " · retires in " + x.retires_in_months + " months" : ""}</small></span>
          </button>`).join("")}
      </div>
      <form method="dialog" class="row" style="margin-top:12px">
        <input type="text" id="newName" placeholder="Or type your name" class="grow" style="flex:1">
        <button class="btn primary" value="new">OK</button>
      </form>`;
    modal.showModal();
    modal.querySelectorAll("[data-name]").forEach((b) => b.addEventListener("click", () => { setUser(b.dataset.name); modal.close(); }));
    modal.onclose = () => {
      const v = (modal.querySelector("#newName") || {}).value;
      if (modal.returnValue === "new" && v && v.trim()) setUser(v.trim());
    };
  });
  function setUser(name) {
    state.user = name;
    store.set("user", name);
    renderWho();
    route();
  }

  async function refreshMachines() {
    state.machines = await api("/machines").catch(() => state.machines);
  }

  // ------------------------------------------------------------ recorder
  function pickMime() {
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"];
    if (!window.MediaRecorder) return null;
    return types.find((t) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || "";
  }
  function canRecord() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  }
  async function startRecorder(onTick) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = pickMime();
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    const t0 = Date.now();
    const timer = setInterval(() => onTick(Math.floor((Date.now() - t0) / 1000)), 250);
    rec.start(250);
    return {
      stop: () => new Promise((resolve) => {
        rec.onstop = () => {
          clearInterval(timer);
          stream.getTracks().forEach((t) => t.stop());
          const type = rec.mimeType || "audio/webm";
          const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
          resolve({ blob: new Blob(chunks, { type }), ext, seconds: Math.round((Date.now() - t0) / 1000) });
        };
        rec.stop();
      }),
      cancel: () => { clearInterval(timer); try { rec.stop(); } catch (_) {} stream.getTracks().forEach((t) => t.stop()); },
    };
  }
  const mmss = (s) => String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
  const MIC_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z"/></svg>`;
  const STOP_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`;

  // ------------------------------------------------------------ screen: record
  let activeRecorder = null;

  async function screenRecord() {
    const machineOpts = state.machines.map((m) => m.machine);
    const last = store.get("machine") || machineOpts[0] || "";
    render(`
      <h1>Log your shift</h1>
      <p class="sub">Tap the mic and talk for about 2 minutes. KnowFlow writes the card for you.</p>
      <div id="loop"></div>
      <div class="stack">
        <div>
          <label class="field" for="machine">Machine</label>
          <input type="text" id="machine" list="machineList" value="${esc(last)}" placeholder="e.g. CNC 3" autocomplete="off">
          <datalist id="machineList">${machineOpts.map((m) => `<option value="${esc(m)}">`).join("")}</datalist>
        </div>
        <div><label class="field">I'm speaking</label>${langChips()}</div>
      </div>
      <div class="record-hero" id="hero">
        <div class="question-card" id="qcard">
          <div class="q-step" id="qstep">Question 1 of 3</div>
          <p class="q-text" id="qtext">${esc(QUESTIONS[0])}</p>
          <div class="q-dots">${QUESTIONS.map((_, i) => `<i class="${i === 0 ? "on" : ""}"></i>`).join("")}</div>
        </div>
        <button class="mic" id="mic" type="button" aria-label="Start recording">${MIC_SVG}</button>
        <div class="rec-status" id="recStatus"><span class="muted">Tap to start</span></div>
        <div class="row" style="margin-top:14px;justify-content:center">
          <button class="btn" id="nextQ" type="button" hidden>Next question →</button>
          <button class="btn primary" id="done" type="button" hidden>✓ Done</button>
        </div>
        <button class="btn ghost" id="typeInstead" type="button" style="margin-top:6px">⌨️ Type instead</button>
      </div>
      <div id="typeBox" hidden class="stack">
        <textarea id="noteText" placeholder="What was difficult today? What did you solve, and how? What should the next person watch out for?"></textarea>
        <button class="btn primary block" id="sendText" type="button">Create card</button>
      </div>
      <div id="result"></div>
    `);

    // Closed loop: questions nobody could answer, for machines this person knows.
    api("/open-questions?expert=" + encodeURIComponent(state.user)).then((qsList) => {
      if (!qsList.length) return;
      const q = qsList[0];
      document.getElementById("loop").innerHTML = `
        <div class="loop-note" style="margin-bottom:14px">❓ <b>A colleague asked:</b> “${esc(q.question)}”<br>
        <span class="small">If you know the answer, tell it in today's log.</span></div>`;
    }).catch(() => {});

    const $ = (id) => document.getElementById(id);
    const mic = $("mic"), status = $("recStatus"), nextQ = $("nextQ"), done = $("done");
    let qi = 0;
    const setQ = (i) => {
      qi = i;
      $("qstep").textContent = `Question ${i + 1} of ${QUESTIONS.length}`;
      $("qtext").textContent = QUESTIONS[i];
      document.querySelectorAll(".q-dots i").forEach((d, k) => d.classList.toggle("on", k <= i));
      nextQ.hidden = !activeRecorder || i >= QUESTIONS.length - 1;
    };

    async function begin() {
      if (!canRecord()) {
        toast("No microphone here (needs HTTPS). Type your note instead.");
        showType();
        return;
      }
      try {
        activeRecorder = await startRecorder((s) => {
          status.innerHTML = `<span class="dot"></span> Recording ${mmss(s)}`;
        });
      } catch (e) {
        toast("Microphone blocked. You can type instead.");
        showType();
        return;
      }
      mic.classList.add("rec");
      mic.innerHTML = STOP_SVG;
      mic.setAttribute("aria-label", "Stop recording");
      status.innerHTML = `<span class="dot"></span> Recording 00:00`;
      $("typeInstead").hidden = true;
      done.hidden = false;
      setQ(0);
    }
    async function finish() {
      if (!activeRecorder) return;
      const r = activeRecorder;
      activeRecorder = null;
      const { blob, ext, seconds } = await r.stop();
      if (seconds < 2) {
        toast("That was very short. Try again and talk a bit longer.");
        return screenRecord();
      }
      const form = new FormData();
      form.append("author", state.user);
      const m = $("machine").value.trim();
      if (m) form.append("machine", m);
      if (state.lang) form.append("language", state.lang);
      form.append("audio", blob, "shift." + ext);
      submit(form);
    }
    function showType() {
      $("hero").hidden = true;
      $("typeBox").hidden = false;
      $("noteText").focus();
    }

    mic.addEventListener("click", () => (activeRecorder ? finish() : begin()));
    done.addEventListener("click", finish);
    nextQ.addEventListener("click", () => setQ(Math.min(qi + 1, QUESTIONS.length - 1)));
    $("typeInstead").addEventListener("click", showType);
    $("sendText").addEventListener("click", () => {
      const t = $("noteText").value.trim();
      if (t.length < 10) return toast("Write a little more first.");
      const form = new FormData();
      form.append("author", state.user);
      const m = $("machine").value.trim();
      if (m) form.append("machine", m);
      form.append("text", t);
      submit(form);
    });

    async function submit(form) {
      const m = $("machine").value.trim();
      if (m) store.set("machine", m);
      $("hero").hidden = true;
      $("typeBox").hidden = true;
      $("result").innerHTML = `
        <div class="thinking"><div class="spinner"></div>
          <b>AI is writing your card…</b>
          <div style="width:100%;max-width:420px" class="stack"><div class="skeleton"></div><div class="skeleton" style="width:80%"></div><div class="skeleton" style="width:60%"></div></div>
        </div>`;
      try {
        const res = await api("/entries", { method: "POST", form });
        $("result").innerHTML = `
          <h2>Your card is ready</h2>
          <p class="sub">Check it, then approve it so the team can use it.</p>
          ${cardHTML(res.card, { approve: true, showTranscript: true })}
          <button class="btn ghost block" id="again" style="margin-top:12px">🎙️ Log another</button>`;
        $("again").addEventListener("click", screenRecord);
      } catch (e) {
        $("result").innerHTML = `<div class="empty"><span class="ei">😕</span>${esc(e.message)}</div>
          <button class="btn block" id="retry" style="margin-top:12px">Try again</button>`;
        $("retry").addEventListener("click", screenRecord);
      }
    }
  }

  // ------------------------------------------------------------ screen: ask
  const SUGGESTIONS = [
    "CNC 3 makes a noise when starting, what do I do?",
    "Packaging line film keeps jamming",
    "Alarm E-217 on CNC 1",
    "Press 2 parts out of tolerance",
  ];

  function screenAsk(preset) {
    render(`
      <h1>Ask the experts</h1>
      <p class="sub">Ask in your own words, in any language. Answers come from real entries by your colleagues.</p>
      <form class="searchbar" id="askForm">
        <input type="search" id="q" placeholder="e.g. packaging line jams, what do I do?" autocomplete="off" enterkeyhint="search">
        <button class="btn primary" aria-label="Ask">Ask</button>
      </form>
      <div class="chips" style="margin-top:12px">${SUGGESTIONS.map((s) => `<button class="chip" type="button" data-s="${esc(s)}">${esc(s)}</button>`).join("")}</div>
      <div id="answer"></div>`);
    const q = document.getElementById("q");
    const go = async (text) => {
      if (!text.trim()) return;
      q.value = text;
      const box = document.getElementById("answer");
      box.innerHTML = `<div class="answer"><div class="stack"><div class="skeleton" style="width:70%"></div><div class="skeleton"></div><div class="skeleton" style="width:85%"></div></div></div>`;
      try {
        const r = await api("/ask", { method: "POST", json: { question: text, asked_by: state.user } });
        box.innerHTML = answerHTML(r);
      } catch (e) {
        box.innerHTML = `<div class="answer none">${esc(e.message)}</div>`;
      }
    };
    document.getElementById("askForm").addEventListener("submit", (e) => { e.preventDefault(); go(q.value); });
    view.querySelectorAll("[data-s]").forEach((c) => c.addEventListener("click", () => go(c.dataset.s)));
    if (preset) go(preset); else q.focus();
  }

  function answerHTML(r) {
    if (!r.answered) {
      return `<div class="answer none">
        <p class="answer-summary">🤔 ${esc(r.summary)}</p>
        ${r.suggest_ask ? `<p>Best person to ask: <b>${esc(r.suggest_ask)}</b></p>` : ""}
        <p class="small muted">We sent your question to the experts. It becomes their next question in the shift log.</p>
      </div>`;
    }
    return `<div class="answer">
      <p class="answer-summary">${esc(r.summary)}</p>
      <ol class="steps">${r.steps.map((s) => `
        <li>${esc(s.text)}<br>
          <span class="source">${avatar(s.source.author, "sm")} from ${esc(s.source.author)}, ${fmtDate(s.source.date)}${s.source.years_experience ? " · " + s.source.years_experience + " yrs" : ""}</span>
        </li>`).join("")}</ol>
      ${r.warning ? `<div class="warnbox">⚠️ ${esc(r.warning)}</div>` : ""}
    </div>
    <h2>Source cards</h2>
    <div class="stack" data-title>${r.sources.map((c) => cardHTML(c, { feedback: true, titleFromProblem: true })).join("")}</div>`;
  }

  // ------------------------------------------------------------ screen: meetings
  async function screenMeetings() {
    render(`
      <h1>Meetings</h1>
      <p class="sub">Keep the know-how from team meetings: decisions and why, problems solved, lessons, action items.</p>
      <div id="meet" class="stack"></div>
      <h2>Past meetings</h2>
      <div id="past" class="stack"><div class="skeleton"></div></div>`);
    meetingSetup();
    loadPastMeetings();
  }

  function meetingSetup() {
    const box = document.getElementById("meet");
    box.innerHTML = `
      <div class="stack">
        <div><label class="field" for="mtitle">Meeting title</label>
          <input type="text" id="mtitle" placeholder="e.g. Line 2 weekly" value="Line 2 weekly"></div>
        <div><label class="field" for="mpeople">Participants</label>
          <input type="text" id="mpeople" placeholder="Names, separated by commas" value="${esc(state.user)}"></div>
        <div><label class="field">Language spoken</label>${langChips()}</div>
        <div class="consent">
          <label><input type="checkbox" id="consent">
            <span>Everyone in this meeting agrees to the recording.<br>
            <small class="muted" style="font-weight:400">Required by law in Germany. Audio is never stored, only the extracted knowledge.</small></span></label>
        </div>
        <div class="row">
          <button class="btn primary grow" id="mrec" disabled>🔴 Record</button>
          <button class="btn grow" id="mtype" disabled>⌨️ Paste notes</button>
        </div>
      </div>`;
    const consent = document.getElementById("consent");
    consent.addEventListener("change", () => {
      document.getElementById("mrec").disabled = !consent.checked;
      document.getElementById("mtype").disabled = !consent.checked;
    });
    document.getElementById("mrec").addEventListener("click", meetingRecord);
    document.getElementById("mtype").addEventListener("click", meetingType);
  }

  function meetingMeta() {
    return {
      title: document.getElementById("mtitle").value.trim() || "Team meeting",
      people: document.getElementById("mpeople").value.trim(),
    };
  }

  async function meetingRecord() {
    if (!canRecord()) { toast("No microphone here (needs HTTPS). Paste notes instead."); return meetingType(); }
    const meta = meetingMeta();
    const box = document.getElementById("meet");
    let rec;
    try {
      rec = await startRecorder((s) => { const t = document.getElementById("mtime"); if (t) t.textContent = mmss(s); });
    } catch (e) { return toast("Microphone blocked."); }
    activeRecorder = rec;
    box.innerHTML = `
      <div class="rec-bar"><span class="dot"></span> Recording · <span id="mtime">00:00</span> <span class="grow"></span>${esc(meta.title)}</div>
      <p class="sub center">Everyone can see this red indicator while recording.</p>
      <button class="btn primary block" id="mstop">■ Stop and extract knowledge</button>
      <button class="btn ghost block" id="mcancel">Cancel</button>`;
    document.getElementById("mcancel").addEventListener("click", () => { rec.cancel(); activeRecorder = null; meetingSetup(); });
    document.getElementById("mstop").addEventListener("click", async () => {
      activeRecorder = null;
      const { blob, ext } = await rec.stop();
      const form = new FormData();
      form.append("title", meta.title);
      form.append("participants", meta.people);
      form.append("consent", "true");
      if (state.lang) form.append("language", state.lang);
      form.append("audio", blob, "meeting." + ext);
      meetingSubmit(form);
    });
  }

  function meetingType() {
    const meta = meetingMeta();
    const box = document.getElementById("meet");
    box.innerHTML = `
      <textarea id="mtext" placeholder="Paste the meeting notes or transcript…"></textarea>
      <button class="btn primary block" id="msend">Extract knowledge</button>
      <button class="btn ghost block" id="mback">Back</button>`;
    document.getElementById("mback").addEventListener("click", meetingSetup);
    document.getElementById("msend").addEventListener("click", () => {
      const t = document.getElementById("mtext").value.trim();
      if (t.length < 20) return toast("Paste a bit more text first.");
      const form = new FormData();
      form.append("title", meta.title);
      form.append("participants", meta.people);
      form.append("consent", "true");
      form.append("text", t);
      meetingSubmit(form);
    });
  }

  async function meetingSubmit(form) {
    const box = document.getElementById("meet");
    box.innerHTML = `<div class="thinking"><div class="spinner"></div><b>AI is pulling out decisions and lessons…</b></div>`;
    try {
      const r = await api("/meetings", { method: "POST", form });
      box.innerHTML = meetingResultHTML(r) + `
        ${r.cards.length ? `<button class="btn primary block" id="addAll">✓ Add ${r.cards.length} cards to knowledge base</button>` : ""}
        <button class="btn ghost block" id="newMeet">New meeting</button>`;
      const addAll = document.getElementById("addAll");
      if (addAll) addAll.addEventListener("click", async () => {
        addAll.disabled = true;
        try {
          for (const c of r.cards) await api(`/cards/${c.id}/approve`, { method: "POST", json: { expert: state.user } });
          addAll.textContent = "✓ Added to knowledge base";
          toast("Meeting knowledge is now searchable.");
          refreshMachines();
        } catch (e) { toast(e.message); addAll.disabled = false; }
      });
      document.getElementById("newMeet").addEventListener("click", meetingSetup);
      loadPastMeetings();
    } catch (e) {
      box.innerHTML = `<div class="empty"><span class="ei">😕</span>${esc(e.message)}</div>`;
      setTimeout(meetingSetup, 3000);
    }
  }

  function meetingResultHTML(r) {
    const sec = (icon, title, items, fmt) => items && items.length ? `
      <div class="result-sec"><h3>${icon} ${title}</h3><ul>${items.map((x) => `<li>${fmt(x)}</li>`).join("")}</ul></div>` : "";
    return `<div class="stack">
      ${r.summary ? `<p class="answer-summary">${esc(r.summary)}</p>` : ""}
      ${sec("✅", "Decisions", r.decisions, (d) => `<b>${esc(d.decision)}</b>${d.reason ? `<br><span class="muted">Why: ${esc(d.reason)}</span>` : ""}`)}
      ${sec("🔧", "Problems and solutions", r.problems, (p) => `<b>${esc(p.problem)}</b>${p.solution ? `<br>→ ${esc(p.solution)}` : ""}${p.machine ? ` <span class="badge machine">${esc(p.machine)}</span>` : ""}`)}
      ${sec("💡", "Lessons learned", r.lessons_learned, (l) => esc(l))}
      ${sec("📌", "Action items", r.action_items, (a) => `${esc(a.task)}${a.owner ? ` · <b>${esc(a.owner)}</b>` : ""}${a.due ? ` · due ${esc(a.due)}` : ""}`)}
    </div>`;
  }

  async function loadPastMeetings() {
    const el = document.getElementById("past");
    if (!el) return;
    try {
      const list = await api("/meetings");
      el.innerHTML = list.length ? list.map((m) => `
        <details class="result-sec"><summary><b>${esc(m.title)}</b> <span class="muted small">· ${fmtDate(m.date)} · ${esc(m.participants.join(", "))}</span></summary>
          <div style="margin-top:10px">${meetingResultHTML(m)}</div></details>`).join("")
        : `<div class="empty"><span class="ei">👥</span>No meetings yet. Record your next team meeting to keep what was decided and why.</div>`;
    } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------ screen: dashboard
  async function screenDashboard() {
    render(`<h1>Dashboard</h1><p class="sub">Where knowledge is at risk, and what keeps coming back.</p><div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>`);
    try {
      const [stats, alerts, risk, drafts, openQ] = await Promise.all([
        api("/stats"), api("/alerts"), api("/risk-map"), api("/entries?status=draft"), api("/open-questions"),
      ]);
      const icon = { recurring_problem: "🔁", knowledge_risk: "👤", disputed_fix: "👎" };
      const title = { recurring_problem: "Recurring problem", knowledge_risk: "Knowledge risk", disputed_fix: "Disputed fix" };
      render(`
        <h1>Dashboard</h1>
        <p class="sub">Where knowledge is at risk, and what keeps coming back.</p>
        <div class="stats">
          <div class="stat"><b>${stats.cards_approved}</b><span>knowledge cards</span></div>
          <div class="stat"><b>${stats.experts}</b><span>experts sharing</span></div>
          <div class="stat"><b>${stats.confirmed_fixes}</b><span>times a fix worked</span></div>
          <div class="stat"><b>${stats.open_questions}</b><span>open questions</span></div>
        </div>

        <h2>Alerts</h2>
        <div class="stack">${alerts.length ? alerts.map((a) => `
          <a class="alert ${a.severity}" href="${a.machine_id ? "#/m/" + esc(a.machine_id) : "#/dashboard"}">
            <span class="ai">${icon[a.type] || "⚠️"}</span>
            <span><b>${title[a.type] || "Alert"}</b>${esc(a.message)}</span>
          </a>`).join("") : `<div class="empty"><span class="ei">✅</span>No alerts right now.</div>`}</div>

        <h2>Knowledge-risk map</h2>
        <p class="sub small">Machines coloured by how many people know them. Red: only experts retiring within a year.</p>
        <div class="risk-grid">${risk.map((r) => `
          <a class="risk ${r.risk}" href="#/m/${esc(r.machine_id)}">
            <span class="lvl">${r.risk} risk</span>
            <b>${esc(r.machine)}</b>
            <span class="small">${r.people_who_know_it} ${r.people_who_know_it === 1 ? "person knows" : "people know"} it</span>
            <span class="small muted">${r.experts_retiring_soon.length ? "Retiring: " + esc(r.experts_retiring_soon.join(", ")) : esc(r.experts.join(", "))}</span>
          </a>`).join("")}</div>

        <h2>Waiting for approval <span class="badge draft">${drafts.length}</span></h2>
        <div class="stack">${drafts.length ? drafts.map((c) => cardHTML(c, { approve: true, showTranscript: true, titleFromProblem: true })).join("")
          : `<div class="empty"><span class="ei">📝</span>No drafts. New shift logs show up here until an expert approves them.</div>`}</div>

        <h2>Open questions</h2>
        <div class="stack">${openQ.length ? openQ.map((q) => `
          <div class="list-item"><span style="font-size:22px">❓</span><span class="grow">${esc(q.question)}<br>
            <small class="muted">${q.asked_by ? esc(q.asked_by) + " · " : ""}${ago(q.date)}</small></span></div>`).join("")
          : `<div class="empty"><span class="ei">🙌</span>Every question has an answer.</div>`}</div>

        <h2>Machine QR codes</h2>
        <p class="sub small">Print one for each machine. Scanning it opens the experts' fixes for that machine.</p>
        <div class="stack">${state.machines.map((m) => `
          <a class="list-item" href="#/sign/${esc(m.machine_id)}"><span style="font-size:22px">⚙️</span>
            <span class="grow"><b>${esc(m.machine)}</b><br><small class="muted">${m.cards} cards · ${m.experts} ${m.experts === 1 ? "expert" : "experts"}</small></span>
            <span class="btn small">QR</span></a>`).join("")}</div>

        <div class="no-print" style="margin-top:28px">
          <button class="btn ghost block" id="reset">↺ Reset demo data</button>
        </div>`);
      document.getElementById("reset").addEventListener("click", async () => {
        if (!confirm("Reset all data to the demo set?")) return;
        try { await api("/admin/reset-demo", { method: "POST" }); await refreshMachines(); toast("Demo data reset."); screenDashboard(); }
        catch (e) { toast(e.message); }
      });
    } catch (e) {
      render(`<h1>Dashboard</h1><div class="empty"><span class="ei">😕</span>${esc(e.message)}</div>`);
    }
  }

  // ------------------------------------------------------------ screen: machine (QR target)
  async function screenMachine(id) {
    render(`<div class="machine-hero"><h1>⚙️ …</h1><p>Loading the experts' answers…</p></div><div class="stack"><div class="skeleton"></div></div>`);
    try {
      const m = await api("/machine/" + encodeURIComponent(id));
      render(`
        <div class="machine-hero">
          <h1>⚙️ ${esc(m.machine)}</h1>
          <p>${m.cards.length} proven answers from ${esc(m.experts.join(", "))}</p>
        </div>
        ${m.alerts.map((a) => `<div class="alert ${a.severity}" style="margin-bottom:10px"><span class="ai">${a.type === "recurring_problem" ? "🔁" : a.type === "knowledge_risk" ? "👤" : "👎"}</span><span>${esc(a.message)}</span></div>`).join("")}
        <a class="btn block" href="#/ask" style="margin-bottom:14px">🔎 Ask a question about this machine</a>
        <div class="stack" data-title>${m.cards.map((c) => cardHTML(c, { feedback: true, titleFromProblem: true })).join("")}</div>`);
    } catch (e) {
      render(`<div class="empty"><span class="ei">⚙️</span>${esc(e.message)}<br><br><a class="btn" href="#/record">Log the first entry</a></div>`);
    }
  }

  function screenSign(id) {
    const m = state.machines.find((x) => x.machine_id === id) || { machine: id };
    render(`
      <div class="sign">
        <div class="muted" style="font-weight:700;letter-spacing:.08em">KNOWFLOW · SCAN FOR EXPERT FIXES</div>
        <h1>${esc(m.machine)}</h1>
        <img src="${API}/machine/${encodeURIComponent(id)}/qr.svg" alt="QR code for ${esc(m.machine)}">
        <p class="muted">Scan with your phone camera to see how colleagues solved problems on this machine.</p>
      </div>
      <div class="row no-print" style="margin-top:14px">
        <button class="btn primary grow" onclick="window.print()">🖨️ Print sign</button>
        <a class="btn grow" href="#/m/${esc(id)}">Open page</a>
      </div>`);
  }

  async function screenCard(id) {
    try {
      const c = await api("/cards/" + id);
      render(`<div data-title>${cardHTML(c, { feedback: true, approve: true, showTranscript: true, titleFromProblem: true })}</div>`);
    } catch (e) { render(`<div class="empty">${esc(e.message)}</div>`); }
  }

  // ------------------------------------------------------------ router
  function route() {
    if (activeRecorder) { activeRecorder.cancel(); activeRecorder = null; }
    const [, name, arg] = (location.hash || "#/record").split("/");
    document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
    window.scrollTo(0, 0);
    switch (name) {
      case "ask": return screenAsk(arg ? decodeURIComponent(arg) : "");
      case "meetings": return screenMeetings();
      case "dashboard": return screenDashboard();
      case "m": return screenMachine(decodeURIComponent(arg || ""));
      case "sign": return screenSign(decodeURIComponent(arg || ""));
      case "card": return screenCard(arg);
      default: return screenRecord();
    }
  }

  window.addEventListener("hashchange", route);
  renderWho();
  refreshMachines().finally(route);
})();

