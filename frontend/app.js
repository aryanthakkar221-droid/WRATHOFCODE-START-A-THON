/* KnowFlow frontend: vanilla JS single-page app, served by the FastAPI backend.
 * Routes (hash): #/record  #/ask  #/practice  #/play/<card>  #/meetings  #/dashboard  #/wallet  #/m/<machine>  #/card/<id>  #/login
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

  // Guided questions depend on experience: 5+ years = expert, below = newer worker.
  const EXPERT_YEARS = 5;
  const QUESTIONS_EXPERT = [
    "What was difficult today?",
    "How did you fix it?",
    "What should the next person watch out for?",
  ];
  const QUESTIONS_NEWER = [
    "What was difficult or unclear today?",
    "What did you try, and did it work?",
    "Who helped you, and what did they tell you?",
  ];
  function userYears() {
    const me = state.experts.find((x) => x.name === state.user);
    return me ? me.years_experience : null;
  }
  function isExpert() {
    const y = userYears();
    return y != null && y >= EXPERT_YEARS;
  }
  function questionsFor() { return isExpert() ? QUESTIONS_EXPERT : QUESTIONS_NEWER; }

  const view = document.getElementById("view");
  const toastEl = document.getElementById("toast");
  const modal = document.getElementById("modal");

  // ------------------------------------------------------------ helpers
  // Lucide icons, inlined from icons.js (works offline). ic("mic"), ic("check", "lg")
  const ICONS = window.KF_ICONS || {};
  function ic(name, cls = "") {
    return `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ICONS.plus || ""}</svg>`;
  }
  const MACHINE_ICONS = { "packaging-machine": "package", "machine-1": "cog", "press-2": "hammer", compressor: "wind" };
  function machineIcon(id, cls = "") { return ic(MACHINE_ICONS[id] || "factory", cls); }
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
    return `<span class="avatar ${cls}" aria-hidden="true">${esc(initials(name))}</span>`;
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
  function toast(msg, icon) {
    toastEl.innerHTML = (icon ? ic(icon) : "") + `<span>${esc(msg)}</span>`;
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
      ? `<span class="badge ok">${ic("badge-check")}Approved by ${esc(c.approved_by || c.author)}</span>`
      : `<span class="badge draft">Draft · waiting for approval</span>`;
    const conf = Math.round((c.confidence || 0.5) * 100);
    const votes = c.worked_count + c.failed_count;
    return `
    <article class="kcard ${c.outdated ? "outdated" : ""}" data-card="${c.id}">
      <div class="kcard-head">
        <div class="badges">
          ${c.machine ? `<a class="badge machine" href="#/m/${esc(c.machine_id)}">${machineIcon(c.machine_id)}${esc(c.machine)}</a>` : ""}
          ${kindCls || c.kind !== "fix" ? `<span class="badge ${kindCls}">${kindLabel}</span>` : ""}
          ${c.source === "meeting" ? `<span class="badge meeting">From meeting</span>` : ""}
          ${c.shared_by ? `<span class="badge shared">${ic("message-square")}Tip from ${esc(c.author)}, shared by ${esc(c.shared_by)}</span>` : ""}
          ${c.failed_count > c.worked_count ? `<span class="badge bad">${ic("alert-triangle")}Often didn't work</span>` : c.outdated ? `<span class="badge bad">${ic("alert-triangle")}Outdated</span>` : ""}
        </div>
        <div class="badges">${status}</div>
      </div>
      ${opts.titleFromProblem && c.problem ? `<div class="kcard-title">${esc(c.problem)}</div>` : ""}
      <dl>
        ${rows.map((r) => `<div class="field-row ${r[2]}"><dt>${r[0]}</dt><dd>${esc(r[1])}</dd></div>`).join("")}
      </dl>
      ${opts.showTranscript && c.transcript ? `<div class="transcript"><div class="eyebrow">Transcript</div>“${esc(c.transcript)}”</div>` : ""}
      <div class="author">
        ${avatar(c.author)}
        <div class="author-meta grow">
          <b>${esc(c.author)}</b>
          <small>${c.author_years_experience ? c.author_years_experience + " years experience · " : ""}${fmtDate(c.date)}${c.last_confirmed ? " · last confirmed " + ago(c.last_confirmed) : ""}</small>
        </div>
        ${c.status === "approved" ? `<div class="worked" title="Worked for ${c.worked_count} ${c.worked_count === 1 ? "person" : "people"}">
          <span class="big">${c.worked_count}</span><span class="lbl">${c.worked_count ? `Worked for ${c.worked_count === 1 ? "1 person" : c.worked_count + " people"}` : votes ? "Not confirmed yet" : "No votes yet"}</span>
          ${c.failed_count ? `<span class="fail">Didn't work for ${c.failed_count}</span>` : ""}
          <div class="confidence"><i style="width:${conf}%"></i></div></div>` : ""}
      </div>
      ${opts.feedback && c.status === "approved" ? `
      <div class="feedback">
        <span class="label">Did this work?</span>
        <button class="btn small" data-fb="1" data-id="${c.id}">${ic("check")}Worked</button>
        <button class="btn small" data-fb="0" data-id="${c.id}">${ic("x")}Didn't work</button>
      </div>` : ""}
      ${opts.approve && c.status !== "approved" ? `
      <div class="card-actions">
        ${c.shared_by && c.author !== state.user
          ? `<span class="waiting">${ic("hourglass")}Waiting for ${esc(c.author)} to approve</span>`
          : `<button class="btn primary" data-approve="${c.id}">${ic("check")}Approve</button>`}
        <button class="btn" data-edit="${c.id}">${ic("pencil")}Edit</button>
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
        const c = await api(`/cards/${id}/feedback`, { method: "POST", json: { worked: fb.dataset.fb === "1", voter: state.user } });
        replaceCard(c, { feedback: false, titleFromProblem: !!fb.closest("[data-title]") });
        if (c.already_voted) toast("You already rated this tip");
        else if (c.tokens) rewardToast(c.tokens);
        else toast(fb.dataset.fb === "1" ? "Marked as a fix that worked" : "Noted. The expert will see it didn't work", fb.dataset.fb === "1" ? "check" : "x");
      } catch (err) { toast(err.message); }
      return;
    }
    const ap = e.target.closest("[data-approve]");
    if (ap) {
      ap.disabled = true;
      try {
        const c = await api(`/cards/${ap.dataset.approve}/approve`, { method: "POST", json: { expert: state.user } });
        replaceCard(c, { showTranscript: false });
        if (c.tokens) rewardToast(c.tokens, "Card approved. The recording is deleted");
        else toast("Card approved. The recording is deleted", "badge-check");
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
        toast("Draft discarded");
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
        toast("Saved", "check");
      } catch (err) { toast(err.message); }
    };
  }

  // ------------------------------------------------------------ user picker
  function renderWho() {
    document.getElementById("who").innerHTML = `${avatar(state.user, "sm")}<span>${esc(state.user)}</span>` +
      (state.balance != null ? `<span class="who-bal" id="whoBal" title="Your tokens">${ic("coins")}${state.balance}</span>` : "") +
      `<span class="who-chev" aria-hidden="true">${ic("chevron-right")}</span>`;
  }
  async function refreshBalance() {
    try { state.balance = (await api("/wallet/" + encodeURIComponent(state.user))).balance; } catch (e) { state.balance = null; }
    renderWho();
  }
  // "+10 tokens · Herr Müller · Your fix worked for Ahmed" slides in from the top.
  function rewardToast(t, extra) {
    const who = t.person === state.user ? "" : `${t.person} · `;
    toastEl.classList.add("reward");
    toast(`${extra ? extra + " · " : ""}+${t.amount} tokens · ${who}${t.reason}`, "coins");
    setTimeout(() => toastEl.classList.remove("reward"), 2800);
    refreshBalance();
    const b = document.getElementById("whoBal");
    if (b) { b.classList.remove("pop"); void b.offsetWidth; b.classList.add("pop"); }
  }
  document.getElementById("who").addEventListener("click", async () => {
    if (!state.experts.length) state.experts = await api("/experts").catch(() => []);
    modal.innerHTML = `
      <h2>Who is using KnowFlow?</h2>
      ${(() => { const me = state.experts.find((x) => x.name === state.user); return `<p class="sub">Signed in as <b>${esc(state.user)}</b>${me && me.email ? ` · ${esc(me.email)}` : ""}</p>`; })()}
      <p class="sub">Your name goes on the knowledge you share.</p>
      <div class="who-list">
        ${state.experts.map((x) => `
          <button type="button" data-name="${esc(x.name)}" class="${x.name === state.user ? "on" : ""}">
            ${avatar(x.name)}<span><b>${esc(x.name)}</b><br>${x.email ? `<small class="muted">${esc(x.email)}</small><br>` : ""}<small class="muted">${x.years_experience} years experience${x.retires_in_months != null ? " · retires in " + x.retires_in_months + " months" : ""}</small></span>
          </button>`).join("")}
      </div>
      <form method="dialog" class="row" style="margin-top:12px">
        <input type="text" id="newName" placeholder="Or type your name" class="grow" style="flex:1">
        <button class="btn primary" value="new">OK</button>
      </form>
      <a class="btn ghost block" href="#/login" id="signOut" style="margin-top:8px">${ic("log-out")}Sign out</a>`;
    modal.showModal();
    modal.querySelectorAll("[data-name]").forEach((b) => b.addEventListener("click", () => { setUser(b.dataset.name); modal.close(); }));
    document.getElementById("signOut").addEventListener("click", () => { store.set("signed_in", ""); modal.close(); });
    modal.onclose = () => {
      const v = (modal.querySelector("#newName") || {}).value;
      if (modal.returnValue === "new" && v && v.trim()) setUser(v.trim());
    };
  });
  function setUser(name) {
    state.user = name;
    store.set("user", name);
    state.balance = null;
    renderWho();
    refreshBalance();
    refreshInviteDot();
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
      stream,
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
  const MIC_SVG = ic("mic");
  const STOP_SVG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`;

  // Voice level bars: real microphone level (Web Audio), random fallback if unavailable.
  function levelBars(stream, box) {
    const bars = [...box.children];
    let an = null, data = null, ac = null;
    try {
      ac = new (window.AudioContext || window.webkitAudioContext)();
      an = ac.createAnalyser(); an.fftSize = 128;
      ac.createMediaStreamSource(stream).connect(an);
      data = new Uint8Array(an.frequencyBinCount);
    } catch (_) { an = null; }
    const tick = () => {
      if (!document.body.contains(box) || !box.parentElement.classList.contains("on")) { if (ac) ac.close().catch(() => {}); return; }
      if (an) an.getByteFrequencyData(data);
      bars.forEach((b, i) => {
        const d = Math.abs(i - (bars.length - 1) / 2) / (bars.length / 2);   // 0 in the middle, 1 at the edges
        const v = an ? Math.min(1, (data[Math.floor(d * data.length * 0.45)] / 255) * (1.25 - d * 0.6)) : Math.random() * 0.8;
        b.style.height = Math.max(12, Math.round(v * 100)) + "%";
      });
      setTimeout(() => requestAnimationFrame(tick), 70);
    };
    tick();
  }

  // ------------------------------------------------------------ screen: record
  let activeRecorder = null;

  async function screenRecord() {
    const QUESTIONS = questionsFor();
    render(`
      <h1>Log your shift</h1>
      <p class="sub">Tap the mic and talk for about 2 minutes. KnowFlow writes the card for you.</p>
      <div id="loop"></div>
      <div class="stack">
        <div><label class="field">I'm speaking</label>${langChips()}</div>
      </div>
      <div class="record-hero" id="hero">
        <div class="question-card" id="qcard">
          <div class="q-step" id="qstep">Question 1 of 3</div>
          <p class="q-text" id="qtext">${esc(QUESTIONS[0])}</p>
          <div class="q-dots">${QUESTIONS.map((_, i) => `<i class="${i === 0 ? "on" : ""}"></i>`).join("")}</div>
        </div>
        <div class="voice" id="voice">
          <button class="mic" id="mic" type="button" aria-label="Start recording">${MIC_SVG}</button>
          <span class="voice-time" id="vtime">00:00</span>
          <div class="voice-bars" id="vbars" aria-hidden="true">${"<i></i>".repeat(48)}</div>
          <div class="rec-status" id="recStatus">Tap to speak</div>
        </div>
        <div class="row" style="margin-top:14px;justify-content:center">
          <button class="btn" id="nextQ" type="button" hidden>Next question${ic("arrow-right")}</button>
          <button class="btn primary" id="done" type="button" hidden>${ic("check")}Done</button>
        </div>
        <button class="btn ghost" id="typeInstead" type="button" style="margin-top:6px">${ic("keyboard")}Type instead</button>
      </div>
      <a class="btn block" href="#/meetings" id="toMeetings" style="margin-top:18px">${ic("users")}Record a meeting</a>
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
        <div class="loop-note" style="margin-bottom:14px">${ic("circle-help")}<span><b>A colleague asked:</b> “${esc(q.question)}”<br>
        <span class="small muted">If you know the answer, include it in today's log.</span></span></div>`;
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
          $("vtime").textContent = mmss(s);
        });
      } catch (e) {
        toast("Microphone blocked. You can type instead.");
        showType();
        return;
      }
      mic.classList.add("rec");
      mic.innerHTML = `<span class="spin-sq" aria-hidden="true"></span>`;
      $("voice").classList.add("on");
      levelBars(activeRecorder.stream, $("vbars"));
      mic.setAttribute("aria-label", "Stop recording");
      status.innerHTML = `<span class="dot"></span> Listening…`;
      $("typeInstead").hidden = true;
      done.hidden = false;
      setQ(0);
    }
    async function finish() {
      if (!activeRecorder) return;
      const r = activeRecorder;
      activeRecorder = null;
      const v = document.getElementById("voice"); if (v) v.classList.remove("on");
      const { blob, ext, seconds } = await r.stop();
      if (seconds < 2) {
        toast("That was very short. Try again and talk a little longer");
        return screenRecord();
      }
      const form = new FormData();
      form.append("author", state.user);
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
      if (t.length < 10) return toast("Write a little more first");
      const form = new FormData();
      form.append("author", state.user);
      form.append("text", t);
      submit(form);
    });

    async function submit(form) {
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
          <h2>${res.tip_from ? "Tip passed on" : "Your card is ready"}</h2>
          <p class="sub">${res.tip_from
            ? `This tip is from ${esc(res.tip_from)}. It stays a draft until ${esc(res.tip_from)} approves it.`
            : "Check it, then approve it so the team can use it."}</p>
          ${res.open_question_id ? `<div class="loop-note" style="margin-bottom:12px">${ic("circle-help")}<span><b>Sent to the experts.</b> Nobody could help today, so your question is now on the experts' list. They will see it in their next shift log.</span></div>` : ""}
          ${detectedHTML(res)}
          <div id="resultCard">${cardHTML(res.card, { approve: true, showTranscript: true })}</div>
          <button class="btn ghost block" id="again" style="margin-top:12px">${ic("mic")}Log another</button>`;
        $("again").addEventListener("click", screenRecord);
        wireDetected(res);
      } catch (e) {
        $("result").innerHTML = `<div class="empty"><span class="ei">${ic("mic-off")}</span>${esc(e.message)}</div>
          <button class="btn block" id="retry" style="margin-top:12px">Try again</button>`;
        $("retry").addEventListener("click", screenRecord);
      }
    }
  }

  // Machine is detected from what was said (or the shift plan); the expert can correct it.
  function detectedHTML(res) {
    const c = res.card;
    const how = { said: "from what you said", "shift plan": "from your shift plan", chosen: "" }[res.machine_source] || "";
    const opts = (res.machines || []).map((m) => `<option ${m === c.machine ? "selected" : ""}>${esc(m)}</option>`).join("");
    return `
      <div class="detected" id="detected">
        <span class="det-ico">${machineIcon(c.machine_id, "lg")}</span>
        <span class="det-text">Detected: <b id="detName">${esc(c.machine || "No machine yet")}</b>
          ${how ? `<small class="muted">${how}</small>` : ""}</span>
        <button class="btn small" id="detChange" type="button">${ic("pencil")}Change</button>
        <div class="det-pick" id="detPick" hidden>
          <select id="detSelect" aria-label="Machine">${c.machine ? "" : "<option value=\"\">Pick a machine</option>"}${opts}</select>
          <button class="btn small primary" id="detSave" type="button">${ic("check")}Save</button>
        </div>
      </div>`;
  }
  function wireDetected(res) {
    const $ = (id) => document.getElementById(id);
    $("detChange").addEventListener("click", () => { $("detPick").hidden = false; $("detChange").hidden = true; $("detSelect").focus(); });
    $("detSave").addEventListener("click", async () => {
      const m = $("detSelect").value;
      if (!m) return toast("Pick a machine first");
      try {
        const card = await api("/cards/" + res.card.id, { method: "PATCH", json: { machine: m } });
        res.card = card;
        $("detName").textContent = card.machine;
        document.querySelector("#detected .det-ico").innerHTML = machineIcon(card.machine_id, "lg");
        $("detPick").hidden = true; $("detChange").hidden = false;
        $("resultCard").innerHTML = cardHTML(card, { approve: true, showTranscript: true });
        toast("Saved to " + card.machine, "check");
        refreshMachines();
      } catch (e) { toast(e.message); }
    });
  }

  // ------------------------------------------------------------ screen: ask
  const SUGGESTIONS = [
    "The Packaging Machine jams on Monday morning. What do I do?",
    "Machine 1 stops with alarm E-217",
    "Press 2 parts come out the wrong size",
    "The compressor gets hot and switches off",
  ];

  function screenAsk(preset) {
    render(`
      <h1>Ask KnowFlow</h1>
      <p class="sub">Ask in your own words, in any language. Answers come from real entries by your colleagues.</p>
      <form class="promptbox" id="askForm">
        <textarea id="q" rows="1" placeholder="Ask anything about your machines…" autocomplete="off" enterkeyhint="send" aria-label="Your question"></textarea>
        <div class="pb-row">
          <span class="grow"></span>
          <button type="button" class="pb-icon" id="askMic" aria-label="Speak your question" title="Speak your question">${ic("mic")}</button>
          <button type="submit" class="pb-send" id="askSend" aria-label="Ask" title="Ask" disabled>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 18.75V5.25M18.75 12 12 5.25 5.25 12"/></svg></button>
        </div>
      </form>
      <div class="chips" style="margin-top:12px">${["The packaging machine jams, what do I do?", "Press 2 parts are not accurate", "Compressor pressure drops at night"]
        .map((s) => `<button class="chip" type="button" data-s="${esc(s)}">${esc(s)}</button>`).join("")}</div>
      <div id="answer"></div>`);
    const q = document.getElementById("q"), send = document.getElementById("askSend");
    const grow = () => { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 200) + "px"; send.disabled = !q.value.trim(); };
    q.addEventListener("input", grow);
    q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); document.getElementById("askForm").requestSubmit(); } });
    document.querySelector(".promptbox").addEventListener("click", (e) => { if (e.target.closest(".promptbox") && !e.target.closest("button")) q.focus(); });
    // mic: browser speech recognition fills the box (hidden where the browser has none)
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition, micB = document.getElementById("askMic");
    if (!SR) micB.hidden = true; else micB.addEventListener("click", () => {
      const r = new SR(); r.lang = state.lang === "de" ? "de-DE" : state.lang === "en" ? "en-US" : navigator.language || "de-DE"; r.interimResults = true;
      micB.classList.add("on");
      r.onresult = (ev) => { q.value = [...ev.results].map((x) => x[0].transcript).join(" "); grow(); };
      r.onend = () => { micB.classList.remove("on"); if (q.value.trim()) document.getElementById("askForm").requestSubmit(); };
      r.onerror = () => { micB.classList.remove("on"); toast("Microphone not available here"); };
      r.start();
    });
    const go = async (text) => {
      if (!text.trim()) return;
      q.value = text; grow();
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
    if (r.general) {
      return `<div class="answer general"><div class="eyebrow">${ic("message-square")}General AI answer · not checked by your experts</div>
        <p class="answer-summary">${esc(r.summary)}</p></div>`;
    }
    if (!r.answered) {
      return `<div class="answer none">
        <div class="eyebrow">${ic("file-question")}No answer yet</div>
        <p class="answer-summary">No approved card found yet. Your question has been sent to the experts.</p>
      </div>`;
    }
    return `<div class="answer">
      <div class="eyebrow">${ic("badge-check")}From approved cards</div>
      <p class="answer-summary">${esc(r.summary)}</p>
      <ol class="steps">${r.steps.map((s) => `
        <li>${esc(s.text)}<br>
          <span class="source">${avatar(s.source.author, "sm")} from ${esc(s.source.author)}, ${fmtDate(s.source.date)}${s.source.years_experience ? " · " + s.source.years_experience + " yrs" : ""}</span>
        </li>`).join("")}</ol>
      ${r.warning ? `<div class="warnbox">${ic("alert-triangle")}<span>${esc(r.warning)}</span></div>` : ""}
    </div>
    <div class="section-label">Source cards</div>
    <div class="stack" data-title>${r.sources.map((c) => cardHTML(c, { feedback: true, titleFromProblem: true })).join("")}</div>`;
  }

  // ------------------------------------------------------------ screen: meetings
  async function screenMeetings() {
    render(`
      <h1>Meetings</h1>
      <p class="sub">Keep the know-how from team meetings: decisions and why, problems solved, lessons, action items.</p>
      <div id="pendingConsent"></div>
      <div id="meet" class="stack"></div>
      <h2>Past meetings</h2>
      <div id="past" class="stack"><div class="skeleton"></div></div>`);
    meetingSetup();
    loadPastMeetings();
    loadPendingConsent();
  }

  // Meeting invites for the selected person (top of Dashboard) + red dot on the Dashboard tab.
  async function refreshInviteDot() {
    let n = 0;
    try { n = (await api("/meetings/consent?pending_for=" + encodeURIComponent(state.user))).length; } catch (_) {}
    document.querySelectorAll('#tabs a[data-tab="dashboard"], .side-nav a[data-side="dashboard"]').forEach((t) => t.classList.toggle("has-dot", n > 0));
    return n;
  }
  async function loadInvites() {
    const el = document.getElementById("invites");
    if (!el) return;
    let list = [];
    try { list = await api("/meetings/consent?pending_for=" + encodeURIComponent(state.user)); } catch (_) {}
    refreshInviteDot();
    el.innerHTML = list.map((c) => `
      <div class="loop-note invite" style="margin-bottom:14px">${ic("mail")}<span class="grow"><b>${esc(c.organiser)} invited you to “${esc(c.title)}”. Agree to be recorded?</b><br>
        <span class="small muted">Participants: ${esc(c.participants.join(", "))}</span>
        <span class="row" style="margin-top:10px">
          <button class="btn small primary" data-inv-yes="${c.id}" type="button">${ic("check")}I agree</button>
          <button class="btn small" data-inv-no="${c.id}" type="button">Decline</button></span></span></div>`).join("");
    el.querySelectorAll("[data-inv-yes],[data-inv-no]").forEach((b) => b.addEventListener("click", async () => {
      const yes = !!b.dataset.invYes;
      try {
        await api(`/meetings/consent/${b.dataset.invYes || b.dataset.invNo}/answer`, { method: "POST", json: { name: state.user, agree: yes, by: state.user } });
        toast(yes ? "You agreed to be recorded" : "You declined. The meeting can't be recorded", yes ? "check" : "x");
        loadInvites();
      } catch (e) { toast(e.message); }
    }));
  }

  async function loadPendingConsent() {
    const el = document.getElementById("pendingConsent");
    if (!el) return;
    let list = [];
    try { list = await api("/meetings/consent?pending_for=" + encodeURIComponent(state.user)); } catch (_) {}
    el.innerHTML = list.map((c) => `
      <div class="loop-note" style="margin-bottom:14px">${ic("users")}<span class="grow"><b>${esc(c.organiser)} wants to record “${esc(c.title)}”.</b><br>
        <span class="small muted">Participants: ${esc(c.participants.join(", "))}</span>
        <span class="row" style="margin-top:10px">
          <button class="btn small primary" data-own-agree="${c.id}" type="button">${ic("check")}I agree to be recorded</button>
          <button class="btn small" data-own-refuse="${c.id}" type="button">No</button></span></span></div>`).join("");
    el.querySelectorAll("[data-own-agree],[data-own-refuse]").forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.ownAgree || b.dataset.ownRefuse;
      try {
        await api(`/meetings/consent/${id}/answer`, { method: "POST", json: { name: state.user, agree: !!b.dataset.ownAgree, by: state.user } });
        toast(b.dataset.ownAgree ? "You agreed to be recorded" : "You said no. The meeting won't be recorded", b.dataset.ownAgree ? "check" : "x");
        loadPendingConsent();
      } catch (e) { toast(e.message); }
    }));
  }

  // Consent: the organiser lists the participants; each person answers for themselves,
  // on their own device or on this device after it is handed to them. One "no" blocks it.
  let consentReq = null, consentPoll = null;
  const hhmm = (iso) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

  function meetingSetup() {
    consentReq = null; clearInterval(consentPoll);
    const box = document.getElementById("meet");
    box.innerHTML = `
      <div class="stack">
        <div><label class="field" for="mtitle">Meeting title</label>
          <input type="text" id="mtitle" placeholder="e.g. Line 2 weekly" value="Line 2 weekly"></div>
        <div><label class="field" for="memail">Invite participants by email</label>
          <div class="chipbox" id="chipbox"><input type="email" id="memail" list="peopleEmails" placeholder="name@knowflow-demo.de, press Enter" autocomplete="off" enterkeyhint="done"></div>
          <datalist id="peopleEmails">${state.experts.filter((x) => x.email).map((x) => `<option value="${esc(x.email)}">${esc(x.name)}</option>`).join("")}</datalist>
          <p class="small muted" id="emailMsg" style="margin:6px 0 0">You are included as the organiser. No real emails are sent.</p>
          <input type="hidden" id="mpeople" value="${esc(state.user)}"></div>
        <div><label class="field">Language spoken</label>${langChips()}</div>
        <div class="consent" id="consentBox">
          <p style="margin:0 0 12px"><b style="font-weight:500">Everyone must agree before recording.</b><br>
            <small class="muted">Each person confirms for themselves. Required by law in Germany. Audio is never stored, only the extracted knowledge.</small></p>
          <button class="btn primary block" id="askConsent" type="button">${ic("mail")}Send invites</button>
        </div>
        <div class="row">
          <button class="btn primary grow" id="mrec" disabled>${ic("mic")}Record</button>
          <button class="btn grow" id="mtype" disabled>${ic("keyboard")}Paste notes</button>
        </div>
      </div>`;
    // email chips: Enter / comma adds; known emails map to people, unknown ones are flagged
    const emails = [], emailIn = document.getElementById("memail"), msg = document.getElementById("emailMsg");
    const byEmail = (e) => state.experts.find((x) => (x.email || "").toLowerCase() === e.toLowerCase());
    const drawChips = () => {
      document.getElementById("chipbox").querySelectorAll(".echip").forEach((x) => x.remove());
      emailIn.insertAdjacentHTML("beforebegin", emails.map((e, i) => { const p = byEmail(e);
        return `<span class="echip ${p ? "" : "unknown"}" title="${p ? esc(p.name) : "Not a KnowFlow user yet"}">${esc(e)}${p ? "" : ` · Not a KnowFlow user yet`}
          <button type="button" data-rm="${i}" aria-label="Remove ${esc(e)}">${ic("x")}</button></span>`; }).join(""));
      document.getElementById("chipbox").querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => { emails.splice(+b.dataset.rm, 1); drawChips(); }));
      const me = state.user, names = emails.map(byEmail).filter(Boolean).map((p) => p.name);
      document.getElementById("mpeople").value = [me, ...names.filter((n) => n !== me)].join(", ");
    };
    const addEmail = () => {
      const v = emailIn.value.trim().replace(/,$/, "");
      if (!v) return;
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) { msg.textContent = "That is not a valid email address."; msg.className = "small login-err"; return; }
      if (!emails.some((e) => e.toLowerCase() === v.toLowerCase())) emails.push(v);
      emailIn.value = ""; msg.className = "small muted"; msg.textContent = byEmail(v) ? `Added ${byEmail(v).name}.` : `${v}: Not a KnowFlow user yet`;
      drawChips();
    };
    emailIn.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addEmail(); } });
    emailIn.addEventListener("change", () => { if (byEmail(emailIn.value.trim())) addEmail(); });
    document.getElementById("chipbox").addEventListener("click", () => emailIn.focus());
    document.getElementById("askConsent").addEventListener("click", async () => {
      addEmail();
      if (emails.some((e) => !byEmail(e))) return toast("Remove emails that are not KnowFlow users yet");
      const people = document.getElementById("mpeople").value.split(",").map((x) => x.trim()).filter(Boolean);
      if (people.length < 2) return toast("Invite at least one person by email");
      try {
        consentReq = await api("/meetings/consent", { method: "POST", json: {
          title: document.getElementById("mtitle").value.trim() || "Team meeting", organiser: state.user, participants: people } });
        store.set("consent_" + state.user, String(consentReq.id));
        showConsent();
      } catch (e) { toast(e.message); }
    });
    // an open request from before (e.g. after switching people to answer) comes back
    const saved = store.get("consent_" + state.user);
    if (saved) api("/meetings/consent/" + saved).then((c) => {
      if (c.status === "used" || !document.getElementById("consentBox")) return store.set("consent_" + state.user, "");
      consentReq = c; document.getElementById("mtitle").value = c.title; showConsent();
    }).catch(() => store.set("consent_" + state.user, ""));
    function showConsent() {
      document.getElementById("mtitle").disabled = emailIn.disabled = true;
      document.getElementById("chipbox").classList.add("locked");
      renderConsent();
      clearInterval(consentPoll);
      consentPoll = setInterval(async () => {
        if (!document.getElementById("consentBox") || !consentReq) return clearInterval(consentPoll);
        try { const fresh = await api("/meetings/consent/" + consentReq.id); if (JSON.stringify(fresh.answers) !== JSON.stringify(consentReq.answers)) { consentReq = fresh; renderConsent(); } } catch (_) {}
      }, 2000);
    }
    document.getElementById("mrec").addEventListener("click", meetingRecord);
    document.getElementById("mtype").addEventListener("click", meetingType);
  }

  function renderConsent() {
    const c = consentReq, box = document.getElementById("consentBox");
    if (!c || !box) return;
    const ready = c.status === "ready", no = c.status === "declined";
    box.innerHTML = `
      <div class="row-between"><span class="eyebrow" style="margin:0">Consent · ${esc(c.title)}</span>
        <span class="tag ${ready ? "recurring" : no ? "high" : "warn"}">${ready ? "Everyone agreed" : no ? "Not allowed" : "Waiting"}</span></div>
      <div class="list consent-list">${c.participants.map((p) => {
        const a = c.answers[p], x = state.experts.find((e) => e.name === p), mail = x && x.email ? x.email : p;
        return `<div class="list-item">${avatar(p)}<span class="grow"><b style="font-weight:500">${esc(mail)}</b><br>
          <small class="muted">${esc(p)} · ${a ? (a.agreed ? `Agreed ${hhmm(a.at)}` : `Declined ${hhmm(a.at)}`) : `Invited ${hhmm(c.created)}`}</small></span>
          ${a ? `<span class="tag ${a.agreed ? "recurring" : "high"}">${a.agreed ? "Agreed" : "Declined"}</span>`
            : p === state.user && !no ? `
            <button class="btn small primary" data-agree="${esc(p)}" type="button">${ic("check")}I agree</button>
            <button class="btn small" data-refuse="${esc(p)}" type="button" aria-label="Decline">Decline</button>`
            : `<span class="tag warn">Invited</span>`}</div>`;
      }).join("")}</div>
      ${no ? `<p class="login-err" style="margin-top:12px">${esc(c.declined_by.join(", "))} declined. This meeting can't be recorded.</p>
        <button class="btn block" id="consentRestart" type="button" style="margin-top:8px">Start over</button>`
        : ready ? "" : `<p class="small muted" style="margin:12px 0 0">Each invited person answers on their own Dashboard. Recording stays locked until everyone agreed.</p>`}`;
    document.getElementById("mrec").disabled = document.getElementById("mtype").disabled = !ready;
    box.querySelectorAll("[data-agree],[data-refuse]").forEach((b) => b.addEventListener("click", () =>
      consentAnswer(b.dataset.agree || b.dataset.refuse, !!b.dataset.agree)));
    const rs = document.getElementById("consentRestart"); if (rs) rs.addEventListener("click", () => { store.set("consent_" + state.user, ""); meetingSetup(); });
  }

  // Own name: answer directly. Someone else's name: hand the device over, that person confirms.
  function consentAnswer(name, agree) {
    const send = async (shared) => {
      try {
        consentReq = await api(`/meetings/consent/${consentReq.id}/answer`, { method: "POST",
          json: { name, agree, by: state.user, shared_device: shared } });
        renderConsent();
      } catch (e) { toast(e.message); }
    };
    if (name === state.user) return send(false);
    modal.innerHTML = `
      <div class="eyebrow">${ic("users")}Hand the device to ${esc(name)}</div>
      <h2>${esc(name)}, do you agree that this meeting is recorded?</h2>
      <p class="sub">Only ${esc(name)} taps below. Nobody can agree for someone else.</p>
      <form method="dialog" class="stack">
        <button class="btn primary block" value="yes" id="handYes">${ic("check")}I am ${esc(name)} and I agree to be recorded</button>
        <button class="btn block" value="no" id="handNo">I am ${esc(name)} and I say no</button>
        <button class="btn ghost block" value="cancel">Cancel</button>
      </form>`;
    modal.showModal();
    modal.onclose = () => { if (modal.returnValue === "yes") send(true); else if (modal.returnValue === "no") { agree = false; send(true); } };
  }

  function meetingMeta() {
    return {
      title: (consentReq && consentReq.title) || document.getElementById("mtitle").value.trim() || "Team meeting",
      people: document.getElementById("mpeople").value.trim(),
      consent: consentReq && consentReq.id,
    };
  }

  async function meetingRecord() {
    if (!canRecord()) { toast("No microphone here (needs HTTPS). Paste notes instead"); return meetingType(); }
    const meta = meetingMeta();
    const box = document.getElementById("meet");
    let rec;
    try {
      rec = await startRecorder((s) => { const t = document.getElementById("mtime"); if (t) t.textContent = mmss(s); });
    } catch (e) { return toast("Microphone blocked"); }
    activeRecorder = rec;
    box.innerHTML = `
      <div class="rec-bar live" role="status"><span class="dot"></span> Recording · <span id="mtime">00:00</span> <span class="grow"></span>${esc(meta.title)}</div>
      <div class="say-aloud">${ic("message-square")}<span><span class="eyebrow" style="margin:0 0 4px">Say this out loud now</span>
        <b>“Everyone agrees this meeting is being recorded.”</b></span></div>
      <p class="sub center" style="margin-top:12px">The red bar stays visible the whole time you record.</p>
      <button class="btn primary block" id="mstop">${ic("square")}Stop and extract knowledge</button>
      <button class="btn ghost block" id="mcancel">Cancel</button>`;
    document.getElementById("mcancel").addEventListener("click", () => { rec.cancel(); activeRecorder = null; meetingSetup(); });
    document.getElementById("mstop").addEventListener("click", async () => {
      activeRecorder = null;
      const { blob, ext } = await rec.stop();
      const form = new FormData();
      form.append("title", meta.title);
      form.append("participants", meta.people);
      form.append("consent_request", meta.consent);
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
      if (t.length < 20) return toast("Paste a bit more text first");
      const form = new FormData();
      form.append("title", meta.title);
      form.append("participants", meta.people);
      form.append("consent_request", meta.consent);
      form.append("text", t);
      meetingSubmit(form);
    });
  }

  async function meetingSubmit(form) {
    const box = document.getElementById("meet");
    box.innerHTML = `<div class="thinking"><div class="spinner"></div><b>AI is pulling out decisions and lessons…</b></div>`;
    try {
      const r = await api("/meetings", { method: "POST", form });
      store.set("consent_" + state.user, "");
      box.innerHTML = meetingResultHTML(r) + `
        ${r.cards.length ? `<button class="btn primary block" id="addAll">${ic("check")}Add ${r.cards.length} cards to knowledge base</button>` : ""}
        <button class="btn ghost block" id="newMeet">New meeting</button>`;
      const addAll = document.getElementById("addAll");
      if (addAll) addAll.addEventListener("click", async () => {
        addAll.disabled = true;
        try {
          for (const c of r.cards) await api(`/cards/${c.id}/approve`, { method: "POST", json: { expert: state.user } });
          addAll.innerHTML = ic("check") + "Added to knowledge base";
          toast("Meeting knowledge is now searchable", "check");
          refreshMachines();
        } catch (e) { toast(e.message); addAll.disabled = false; }
      });
      document.getElementById("newMeet").addEventListener("click", meetingSetup);
      loadPastMeetings();
    } catch (e) {
      box.innerHTML = `<div class="empty"><span class="ei">${ic("mic-off")}</span>${esc(e.message)}</div>`;
      setTimeout(meetingSetup, 3000);
    }
  }

  function meetingResultHTML(r) {
    const sec = (icon, title, items, fmt) => items && items.length ? `
      <div class="result-sec"><h3>${ic(icon)} ${title}</h3><ul>${items.map((x) => `<li>${fmt(x)}</li>`).join("")}</ul></div>` : "";
    return `<div class="stack">
      ${r.summary ? `<p class="answer-summary">${esc(r.summary)}</p>` : ""}
      ${sec("circle-check", "Decisions", r.decisions, (d) => `<b>${esc(d.decision)}</b>${d.reason ? `<br><span class="muted">Why: ${esc(d.reason)}</span>` : ""}`)}
      ${sec("wrench", "Problems and solutions", r.problems, (p) => `<b>${esc(p.problem)}</b>${p.solution ? `<br><span class="muted">Fix:</span> ${esc(p.solution)}` : ""}${p.machine ? ` <span class="badge machine">${esc(p.machine)}</span>` : ""}`)}
      ${sec("lightbulb", "Lessons learned", r.lessons_learned, (l) => esc(l))}
      ${sec("list-checks", "Action items", r.action_items, (a) => `${esc(a.task)}${a.owner ? ` · <b>${esc(a.owner)}</b>` : ""}${a.due ? ` · due ${esc(a.due)}` : ""}`)}
    </div>`;
  }

  async function loadPastMeetings() {
    const el = document.getElementById("past");
    if (!el) return;
    try {
      const list = await api("/meetings");
      el.innerHTML = list.length ? list.map((m) => `
        <details class="result-sec"><summary><b>${esc(m.title)}</b> <span class="muted small">· ${fmtDate(m.date)} · ${esc(m.participants.join(", "))}</span></summary>
          ${(m.consent_log || []).length ? `<div class="consent-log"><span class="eyebrow" style="margin:0">Consent log</span>
            ${m.consent_log.map((x) => `<span>${esc(x.name)} agreed ${fmtDate(x.agreed_at)} ${hhmm(x.agreed_at)} · ${esc(x.on)}</span>`).join("")}</div>` : ""}
          <div style="margin-top:10px">${meetingResultHTML(m)}</div></details>`).join("")
        : `<div class="empty"><span class="ei">${ic("users")}</span>No meetings yet. Record your next team meeting to keep what was decided and why.</div>`;
    } catch (e) { el.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------ screen: dashboard
  const ALERT_META = {
    recurring_problem: { icon: "repeat", title: "Keeps happening", tag: ["Recurring", "recurring"] },
    knowledge_risk: { icon: "user-round", title: "Only one person knows", tag: ["High risk", "high"] },
    disputed_fix: { icon: "thumbs-down", title: "Fix often fails", tag: ["Check fix", "warn"] },
  };
  function alertRow(a, withTitle = true) {
    const m = ALERT_META[a.type] || { icon: "alert-triangle", title: "Alert", tag: ["Alert", "warn"] };
    const tag = a.type === "knowledge_risk" && a.severity !== "high" ? ["Watch", "warn"] : m.tag;
    const n = a.count || (a.card_ids || []).length || 1;
    return `
      <a class="alert ${a.severity}" href="${a.machine_id ? "#/m/" + esc(a.machine_id) : "#/dashboard"}">
        <span class="ai">${ic(m.icon)}</span>
        <span class="alert-body">${withTitle ? `<b>${m.title}</b>` : ""}<span class="alert-msg">${esc(a.message)}</span><br>
          <span class="tag ${tag[1]}">${tag[0]}</span></span>
        <span class="count" title="${n} cards">${n}</span>
      </a>`;
  }

  // ---- small inline-SVG charts (no library, works offline). Hover shows a tooltip from data-tip.
  const DOTS = `<svg viewBox="0 0 24 24" class="i" aria-hidden="true"><circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/></svg>`;
  function barChart(weeks) {
    const W = 560, H = 210, L = 28, B = 26, T = 10, max = Math.max(2, ...weeks.map((w) => Math.max(w.rec, w.one)));
    const top = Math.ceil(max / 2) * 2, step = (W - L) / weeks.length, y = (v) => T + (H - T - B) * (1 - v / top);
    const grid = [0, top / 2, top].map((v) => `<line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" class="gl"/><text x="${L - 8}" y="${y(v) + 4}" class="ax" text-anchor="end">${v}</text>`).join("");
    const bars = weeks.map((w, k) => {
      const x = L + k * step + step / 2;
      const bar = (v, dx, cls, lbl) => { const h = Math.max(v ? 4 : 0, (H - T - B) * v / top); return v ? `<rect x="${x + dx - 5}" y="${y(0) - h}" width="10" height="${h}" rx="5" class="${cls}" data-tip="${esc(w.label)} · ${lbl}: ${v}"/>` : ""; };
      return bar(w.rec, -7, "b-rec", "Recurring") + bar(w.one, 7, "b-one", "One-off") + `<text x="${x}" y="${H - 6}" class="ax" text-anchor="middle">${esc(w.label)}</text>`;
    }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="Problems logged per week">${grid}${bars}</svg>`;
  }
  function donut(parts, center, sub) {
    const total = parts.reduce((a, p) => a + p.v, 0) || 1, R = 42, C = 2 * Math.PI * R;
    let off = 0;
    const segs = parts.filter((p) => p.v).map((p) => { const len = C * p.v / total; const el = `<circle r="${R}" cx="60" cy="60" fill="none" stroke="${p.color}" stroke-width="14" stroke-dasharray="${Math.max(len - 2, 0.1)} ${C}" stroke-dashoffset="${-off}" stroke-linecap="butt" data-tip="${esc(p.label)}: ${p.v}"/>`; off += len; return el; }).join("");
    return `<svg viewBox="0 0 120 120" class="donut" role="img" aria-label="${esc(parts.map((p) => p.label + " " + p.v).join(", "))}">
      <circle r="${R}" cx="60" cy="60" fill="none" stroke="#F1ECF8" stroke-width="14"/><g transform="rotate(-90 60 60)">${segs}</g>
      <text x="60" y="${sub ? 60 : 66}" text-anchor="middle" class="d-num">${esc(center)}</text>${sub ? `<text x="60" y="78" text-anchor="middle" class="d-sub">${esc(sub)}</text>` : ""}</svg>`;
  }
  function sparkline(days) {
    // line = 5-day average of tokens per day (smooth); tooltips and the label show the real day values
    const avg = days.map((_, k) => { const w = days.slice(Math.max(0, k - 2), k + 3); return w.reduce((a, d) => a + d.tokens, 0) / w.length; });
    const W = 300, H = 130, P = 10, max = Math.max(1, ...avg), n = Math.max(days.length - 1, 1);
    const pts = avg.map((v, k) => [P + (W - 2 * P) * k / n, H - P - (H - 2 * P) * v / max]);
    let path = `M${pts[0][0]},${pts[0][1]}`;
    for (let k = 1; k < pts.length; k++) {   // smooth curve (Catmull-Rom -> Bezier)
      const p0 = pts[k - 2] || pts[k - 1], p1 = pts[k - 1], p2 = pts[k], p3 = pts[k + 1] || p2;
      path += ` C${p1[0] + (p2[0] - p0[0]) / 6},${p1[1] + (p2[1] - p0[1]) / 6} ${p2[0] - (p3[0] - p1[0]) / 6},${p2[1] - (p3[1] - p1[1]) / 6} ${p2[0]},${p2[1]}`;
    }
    const best = days.reduce((b, d, k) => (d.tokens > days[b].tokens || (d.tokens === days[b].tokens && avg[k] > avg[b]) ? k : b), 0), bp = pts[best];
    const lbl = `+${days[best].tokens} · ${fmtDate(days[best].date)}`;
    return `<svg viewBox="0 0 ${W} ${H + 26}" class="spark" role="img" aria-label="Tokens per day this month">
      <path d="${path}" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round" opacity=".95"/>
      ${pts.map((p, k) => `<circle cx="${p[0]}" cy="${p[1]}" r="7" fill="transparent" data-tip="${esc(fmtDate(days[k].date))}: ${days[k].tokens} tokens"/>`).join("")}
      <circle cx="${bp[0]}" cy="${bp[1]}" r="5" fill="#fff" stroke="rgba(255,255,255,.35)" stroke-width="6"/>
      <g transform="translate(${Math.min(Math.max(bp[0], 46), W - 46)},${Math.max(bp[1] - 18, 10)})"><rect x="-44" y="-12" width="88" height="22" rx="11" fill="#fff"/>
      <text text-anchor="middle" y="3" class="sp-lbl">${esc(lbl)}</text></g></svg>`;
  }
  const tipEl = document.createElement("div");
  tipEl.className = "chart-tip"; tipEl.setAttribute("role", "tooltip"); document.body.appendChild(tipEl);
  document.addEventListener("mousemove", (e) => {
    const t = e.target.closest && e.target.closest("[data-tip]");
    if (!t) { tipEl.style.opacity = 0; return; }
    tipEl.textContent = t.dataset.tip; tipEl.style.opacity = 1;
    tipEl.style.left = Math.min(e.clientX + 12, innerWidth - tipEl.offsetWidth - 8) + "px"; tipEl.style.top = (e.clientY - 36) + "px";
  });

  async function screenDashboard() {
    render(`<h1>Dashboard</h1><p class="sub">Where knowledge is at risk, and what keeps coming back.</p>
      <div class="kpis">${'<div class="kpi"><div class="skeleton" style="height:40px;width:50%"></div><div class="skeleton" style="margin-top:12px"></div></div>'.repeat(4)}</div>`);
    try {
      const [stats, alerts, risk, drafts, openQ, approved, daily] = await Promise.all([
        api("/stats"), api("/alerts"), api("/risk-map"), api("/entries?status=draft"), api("/open-questions"),
        api("/entries?status=approved"), api("/tokens/daily").catch(() => ({ month_total: 0, days: [] })),
      ]);
      const weekAgo = Date.now() - 7 * 864e5;
      const newThisWeek = approved.filter((c) => new Date(c.approved_at || c.date) >= weekAgo).length;
      const atRisk = risk.filter((r) => r.risk === "high");
      const group = (t) => alerts.filter((a) => a.type === t);
      const list = (items, empty) => items.length
        ? `<div class="list">${items.map((a) => alertRow(a, false)).join("")}</div>`
        : `<div class="empty"><span class="ei">${ic("circle-check")}</span>${empty}</div>`;

      // problems per week (last 8 weeks, Monday start): recurring = cards in a "keeps happening" alert
      const recIds = new Set(group("recurring_problem").flatMap((a) => a.card_ids || []));
      const monday = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
      const thisWeek = monday(new Date());
      const weeks = Array.from({ length: 8 }, (_, k) => { const start = new Date(thisWeek); start.setDate(start.getDate() - 7 * (7 - k));
        return { start, label: start.toLocaleDateString("en-GB", { day: "numeric", month: "short" }), rec: 0, one: 0 }; });
      [...approved, ...drafts].forEach((c) => {
        const k = weeks.findIndex((w, idx) => { const end = weeks[idx + 1] ? weeks[idx + 1].start : new Date(8.64e15); return new Date(c.date) >= w.start && new Date(c.date) < end; });
        if (k >= 0) weeks[k][recIds.has(c.id) ? "rec" : "one"]++;
      });
      const pct = Math.round(100 * approved.length / Math.max(approved.length + drafts.length, 1));
      const cov = { low: 0, medium: 0, high: 0 }; risk.forEach((r) => cov[r.risk]++);

      // who knows which machine: approved cards per person per machine
      const people = [...new Set(approved.map((c) => c.author))].sort((a, b) =>
        approved.filter((c) => c.author === b).length - approved.filter((c) => c.author === a).length);
      const cnt = (mid, p) => approved.filter((c) => c.machine_id === mid && c.author === p).length;
      const shade = (n) => "s" + Math.min(n, 4);
      const lvl = { high: ["At risk", "high"], medium: ["Watch", "warn"], low: ["Safe", "recurring"] };
      const kpi = (tint, icon, num, lbl, trend, cls = "") => `
        <div class="kpi dk ${cls}"><span class="k-ico ${tint}">${ic(icon)}</span>
          <span class="k-body"><span class="num">${num}</span><span class="lbl">${lbl}</span><span class="trend">${trend}</span></span>
          <span class="k-more" aria-hidden="true">${DOTS}</span></div>`;
      const card = (title, body, extra = "") => `<section class="dcard ${extra}"><div class="dhead"><h2>${title}</h2></div>${body}</section>`;
      const one = group("knowledge_risk");

      render(`
        <div class="dash-head"><div><h1>Dashboard</h1>
          <p class="sub">Where knowledge is at risk, and what keeps coming back.</p></div></div>
        <div id="invites"></div>

        <div class="kpis stats dash-kpis">
          ${kpi("t-violet", "badge-check", stats.cards_approved, "Approved cards", `+${newThisWeek} this week`)}
          ${kpi("t-pink", "circle-check", stats.confirmed_fixes, "Fixes that worked", `across ${approved.filter((c) => c.worked_count).length} cards`)}
          ${kpi("t-orange", "circle-help", stats.open_questions, "Open questions", "waiting for an expert")}
          ${kpi("t-red", "shield-alert", atRisk.length, "Machines at risk", `of ${risk.length} machines`, "risk-kpi")}
        </div>

        <div class="dgrid r2">
          <section class="dcard span2">
            <div class="dhead"><h2>Problems over time</h2><span class="fake-select">Show by weeks ${ic("chevron-right")}</span></div>
            <div class="chart-row">
              <div class="chart-main">${barChart(weeks)}
                <div class="legend"><span><i style="background:#7B3FE4"></i>Recurring</span><span><i style="background:#CDB8F5"></i>One-off</span></div></div>
              <div class="chart-side"><h3>Approved vs. waiting</h3>
                ${donut([{ v: approved.length, color: "#7B3FE4", label: "Approved" }, { v: drafts.length, color: "#F0A6E8", label: "Waiting" }], pct + "%", "approved")}
                <div class="legend"><span><i style="background:#7B3FE4"></i>Approved ${approved.length}</span><span><i style="background:#F0A6E8"></i>Waiting ${drafts.length}</span></div></div>
            </div>
          </section>
          <section class="dcard">
            <div class="dhead"><h2>Knowledge coverage</h2></div>
            <div class="cov">${donut([{ v: cov.low, color: "#7B3FE4", label: "Safe" }, { v: cov.medium, color: "#F5B66B", label: "Watch" }, { v: cov.high, color: "#D92D20", label: "At risk" }], String(risk.length), "machines")}</div>
            <div class="legend col"><span><i style="background:#7B3FE4"></i>Safe<b>${cov.low}</b></span><span><i style="background:#F5B66B"></i>Watch<b>${cov.medium}</b></span><span><i style="background:#D92D20"></i>At risk<b>${cov.high}</b></span></div>
          </section>
        </div>

        <div class="dgrid r3">
          ${card("Only one person knows", one.length ? `<div class="list flat">${one.map((a) => `
            <a class="alert ${a.severity}" href="#/m/${esc(a.machine_id)}">${avatar(a.expert, "sm")}
              <span class="one-body"><b>${esc(a.machine)}</b><span class="sr-only">${esc(a.message)}</span>
                <span class="tag ${a.severity === "high" ? "high" : "warn"}">${a.severity === "high" ? "High risk" : "Watch"}</span></span>
              <span class="count" title="${(a.card_ids || []).length} cards">${(a.card_ids || []).length}</span></a>`).join("")}</div>`
            : `<div class="empty"><span class="ei">${ic("circle-check")}</span>Every machine has more than one expert.</div>`)}
          <section class="dcard"><div class="dhead"><h2>Who knows which machine</h2></div>
            <div class="heat-wrap">
              <div class="risk-grid heat compact" role="table" aria-label="Approved cards per person and machine">
                <div class="hrow hhead" role="row"><span class="hcell hname" role="columnheader">Machine</span>${people.map((p) =>
                  `<span class="hcell" role="columnheader"><span class="person" title="${esc(p)}">${avatar(p, "sm")}</span></span>`).join("")}</div>
                ${risk.map((r) => `
                <div class="hrow" role="row">
                  <span class="hcell hname" role="rowheader"><a class="risk ${r.risk}" href="#/m/${esc(r.machine_id)}">
                    <b>${esc(r.machine)}</b><span class="tag ${lvl[r.risk][1]} lvl">${lvl[r.risk][0]}</span></a></span>
                  ${people.map((p) => { const n = cnt(r.machine_id, p); return `<span class="hcell" role="cell"><span class="shade ${shade(n)}" data-tip="${esc(p)}: ${n} cards">${n || "·"}</span></span>`; }).join("")}
                </div>`).join("")}
              </div>
            </div>
          </section>
          <section class="dcard hero-card">
            <div class="dhead"><span class="eyebrow">Tokens earned this month</span><span class="k-more" aria-hidden="true">${DOTS}</span></div>
            <div class="hero-num">${daily.month_total}</div>
            <p class="hero-sub">${(daily.month_total / 10).toFixed(2).replace(".", ",")} € for tips that helped colleagues</p>
            ${daily.days.length ? sparkline(daily.days) : ""}
          </section>
        </div>

        <div class="dgrid r4">
          ${card("Keeps happening", list(group("recurring_problem"), "Nothing keeps coming back right now."))}
          ${card("Fix often fails", list(group("disputed_fix"), "No fix is failing right now."))}
          <section class="dcard"><div class="dhead"><h2>Waiting for approval <span class="count">${drafts.length}</span></h2></div>
            <div class="stack">${drafts.length ? drafts.map((c) => cardHTML(c, { approve: true, showTranscript: true, titleFromProblem: true })).join("")
              : `<div class="empty"><span class="ei">${ic("inbox")}</span>No drafts. New shift logs show up here until an expert approves them.</div>`}</div></section>
          <section class="dcard"><div class="dhead"><h2>Questions without an answer <span class="count">${openQ.length}</span></h2></div>
            ${openQ.length ? `<div class="list flat">${openQ.map((q) => `
              <div class="list-item"><span class="li-ico">${ic("circle-help")}</span><span class="grow">${esc(q.question)}<br>
                <small class="muted">${q.asked_by ? esc(q.asked_by) + " · " : ""}${ago(q.date)}</small></span></div>`).join("")}</div>`
              : `<div class="empty"><span class="ei">${ic("circle-check")}</span>Every question has an answer.</div>`}</section>
        </div>

        <section class="dcard"><div class="dhead"><h2>Machines</h2></div>
          <div class="list flat" id="machineList">${state.machines.map((m) => `
            <a class="list-item" href="#/m/${esc(m.machine_id)}"><span class="li-ico">${machineIcon(m.machine_id)}</span>
              <span class="grow"><b style="font-weight:500">${esc(m.machine)}</b><br><small class="muted">${m.cards} cards · ${m.experts} ${m.experts === 1 ? "expert" : "experts"}</small></span>
              <span class="muted-i">${ic("chevron-right")}</span></a>`).join("")}</div></section>

        <div class="no-print" style="margin-top:24px">
          <button class="btn ghost block" id="reset">${ic("rotate-ccw")}Reset demo data</button>
        </div>`);
      document.getElementById("reset").addEventListener("click", async () => {
        if (!confirm("Reset all data to the demo set?")) return;
        try { await api("/admin/reset-demo", { method: "POST" }); await refreshMachines(); toast("Demo data reset", "rotate-ccw"); screenDashboard(); refreshBalance(); }
        catch (e) { toast(e.message); }
      });
      loadInvites();
    } catch (e) {
      render(`<h1>Dashboard</h1><div class="empty"><span class="ei">${ic("file-question")}</span>${esc(e.message)}</div>`);
    }
  }

  // ------------------------------------------------------------ screen: machine page
  async function screenMachine(id) {
    render(`<div class="machine-hero"><div class="eyebrow">Machine</div><h1><span class="hero-ico">${machineIcon(id, "lg")}</span>…</h1><p>Loading the experts' answers…</p></div><div class="stack"><div class="skeleton"></div><div class="skeleton" style="width:70%"></div></div>`);
    try {
      const m = await api("/machine/" + encodeURIComponent(id));
      render(`
        <div class="machine-hero">
          <div class="eyebrow">Machine · ${m.cards.length} approved tips</div>
          <h1><span class="hero-ico">${machineIcon(m.machine_id, "lg")}</span>${esc(m.machine)}</h1>
          <p>${m.cards.length} tips from ${esc(m.experts.join(", "))}</p>
        </div>
        ${m.alerts.length ? `<div class="list machine-alerts">${m.alerts.map((a) => alertRow(a, false)).join("")}</div>` : ""}
        <div class="row" style="margin-bottom:14px">
          <a class="btn grow" href="#/ask">${ic("search")}Ask about this machine</a>
        </div>
        <div class="stack" data-title>${m.cards.map((c) => cardHTML(c, { feedback: true, titleFromProblem: true })).join("")}</div>`);
    } catch (e) {
      render(`<div class="empty"><span class="ei">${ic("file-question")}</span>${esc(e.message)}<br><br><a class="btn" href="#/record">${ic("mic")}Log the first entry</a></div>`);
    }
  }

  async function screenCard(id) {
    try {
      const c = await api("/cards/" + id);
      render(`<div data-title>${cardHTML(c, { feedback: true, approve: true, showTranscript: true, titleFromProblem: true })}</div>`);
    } catch (e) { render(`<div class="empty">${esc(e.message)}</div>`); }
  }

  // ------------------------------------------------------------ screen: fix-it challenge
  // A tiny training game built from a real expert card. Nothing about the player is stored.
  let lastChallenge = null;

  async function screenChallenge(machine, opts = {}) {
    render(`
      <div class="game-hero"><div class="eyebrow">${ic("gamepad")}Fix-it Challenge</div><p>Can you fix it like the experts?</p></div>
      <div class="thinking"><div class="spinner"></div><b>AI is building your challenge…</b></div>`);
    let g;
    try {
      g = await api("/challenge", { method: "POST", json: {
        machine: machine || undefined,
        card_id: opts.card_id ? +opts.card_id : undefined,
        exclude: opts.exclude || [],
      } });
    } catch (e) {
      return render(`<div class="empty"><span class="ei">${ic("gamepad")}</span>${esc(e.message)}</div>`);
    }
    lastChallenge = g;
    const L = "ABCD";
    render(`
      <div class="game-hero">
        <div class="eyebrow">${ic("gamepad")}Fix-it Challenge · ${esc(g.machine || "")}</div>
        <p>${esc(g.situation)}</p>
      </div>
      <div class="game-q">${esc(g.question)}</div>
      <div class="options" id="opts">${g.options.map((o, i) => `
        <button class="option" type="button" data-i="${i}"><span class="letter">${L[i]}</span>
          <span>${esc(o.text)}<span class="consequence ${o.correct ? "ok" : "no"}" hidden>${ic(o.correct ? "check" : "x")}<span>${esc(o.consequence)}</span></span></span>
        </button>`).join("")}</div>
      <div id="after"></div>`);
    const box = document.getElementById("opts");
    box.addEventListener("click", (e) => {
      const b = e.target.closest(".option");
      if (!b || box.classList.contains("done")) return;
      box.classList.add("done");
      const pick = g.options[+b.dataset.i];
      savePractice(g.card_id, pick.correct ? g.badge : null);
      box.querySelectorAll(".option").forEach((el) => {
        const o = g.options[+el.dataset.i];
        if (o.correct) el.classList.add("right");
        else if (el === b) el.classList.add("wrong");
        else el.classList.add("dim");
        if (o.correct || el === b) el.querySelector(".consequence").hidden = false;
      });
      document.getElementById("after").innerHTML = `
        <div class="explain">
          <div class="who-says">${avatar(g.expert)} <span>${esc(g.expert)}'s approved tip${g.expert_years ? ` <span class="muted small" style="font-weight:500">· ${g.expert_years} years</span>` : ""}</span></div>
          <blockquote>“${esc(g.expert_tip)}”</blockquote>
          <div class="small muted" style="margin-top:6px">From ${esc(g.expert)}'s card, ${fmtDate(g.tip_date)} · approved by ${esc(g.approved_by || g.expert)}</div>
          <div class="why"><div class="eyebrow">Why it works</div>${esc(g.explanation)}</div>
        </div>
        ${pick.correct ? `
        <div class="badge-earned"><span class="medal">${ic("award")}</span><b>${esc(g.badge)}</b><br>
          <span class="small muted">You know what ${esc(g.expert)} knows about the ${esc(g.machine || "machine")}.</span></div>` : `
        <div class="answer none" style="margin-top:14px"><b>Not quite, and that's how we learn.</b><br>
          <span class="small">Now you know the trick. Try another one.</span></div>`}
        <div class="row" style="margin-top:14px">
          <button class="btn primary grow" id="nextGame">${ic("gamepad")}Another challenge</button>
          <a class="btn grow" href="#/practice">All challenges</a>
        </div>`;
      document.getElementById("after").scrollIntoView({ behavior: "smooth", block: "start" });
      document.getElementById("nextGame").addEventListener("click", () => {
        const done = Object.keys(practice().done).map(Number);
        screenChallenge("", { exclude: [...new Set([g.card_id, ...done])] });
      });
    });
  }

  // ------------------------------------------------------------ screen: practice
  // Progress is kept only on this device (localStorage), never sent to the server.
  function practice() {
    try { return JSON.parse(store.get("practice_" + state.user) || "") || { done: {} }; } catch (_) { return { done: {} }; }
  }
  function savePractice(cardId, badge) {
    const p = practice();
    p.done[cardId] = badge || p.done[cardId] || "";
    store.set("practice_" + state.user, JSON.stringify(p));
  }
  const MACHINE_ORDER = ["packaging-machine", "machine-1", "press-2", "compressor"];

  async function screenPractice() {
    render(`<h1>Practice</h1><p class="sub">Short challenges built from approved fixes.</p>
      <div class="stack"><div class="skeleton" style="height:90px"></div><div class="skeleton" style="height:90px"></div></div>`);
    let cards;
    try { cards = (await api("/entries?status=approved")).filter((c) => c.machine_id && c.solution); }
    catch (e) { return render(`<h1>Practice</h1><div class="empty"><span class="ei">${ic("gamepad")}</span>${esc(e.message)}</div>`); }
    const p = practice();
    const doneIds = cards.filter((c) => c.id in p.done);
    const badges = [...new Set(doneIds.map((c) => p.done[c.id]).filter(Boolean))];
    const rank = (id) => { const i = MACHINE_ORDER.indexOf(id); return i < 0 ? 99 : i; };
    const groups = {};
    cards.forEach((c) => (groups[c.machine_id] = groups[c.machine_id] || []).push(c));
    const ids = Object.keys(groups).sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    const pct = cards.length ? Math.round((doneIds.length / cards.length) * 100) : 0;
    render(`
      <h1>Practice</h1>
      <p class="sub">Short challenges built from approved fixes. Learn what the experts know, one situation at a time.</p>
      <section class="panel practice-progress">
        <div class="row-between"><span class="eyebrow" style="margin:0">Progress</span><span id="practiceDone">Challenges done: ${doneIds.length} of ${cards.length}</span></div>
        <div class="confidence wallet-bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pct}%"></i></div>
        ${badges.length ? `<div class="badges" style="margin-top:6px">${badges.map((b) => `<span class="badge">${ic("award")}${esc(b)}</span>`).join("")}</div>`
          : `<small class="muted">Get one right to earn your first badge.</small>`}
      </section>
      ${ids.map((id) => `
        <div class="section-label">${machineIcon(id)}${esc(groups[id][0].machine)}</div>
        <div class="stack">${groups[id].map((c) => `
          <article class="practice-card${c.id in p.done ? " done" : ""}">
            <div class="grow">
              <div class="eyebrow">${esc(c.machine)}${c.id in p.done ? ` · ${p.done[c.id] ? "Badge earned" : "Done"}` : ""}</div>
              <b>${esc(c.problem)}</b>
              <small class="muted">4 answers · 1 min</small>
            </div>
            <a class="btn ${c.id in p.done ? "" : "primary"} small" href="#/play/${c.id}" data-play="${c.id}">${c.id in p.done ? "Again" : "Start"}</a>
          </article>`).join("")}</div>`).join("")}`);
  }

  // ------------------------------------------------------------ screen: wallet
  async function screenWallet() {
    render(`<h1>Wallet</h1><div class="skeleton" style="height:140px"></div>`);
    let w;
    try { w = await api("/wallet/" + encodeURIComponent(state.user)); }
    catch (e) { return render(`<div class="empty"><span class="ei">${ic("wallet")}</span>${esc(e.message)}</div>`); }
    state.balance = w.balance; renderWho();
    const pct = Math.min(100, Math.round((w.month_eur / w.monthly_cap_eur) * 100));
    const eur = (n) => n.toFixed(2).replace(".", ",") + " €";
    render(`
      <h1>Wallet</h1>
      <p class="sub">Tokens for knowledge that helps others. ${w.tokens_per_eur} tokens = 1 €.</p>
      <section class="wallet-hero">
        <div class="eyebrow">Balance</div>
        <div class="wallet-bal"><span class="coin">${ic("coins")}</span><b id="walletBal">${w.balance}</b><span class="muted">tokens</span></div>
        <div class="wallet-eur">= ${eur(w.balance_eur)}</div>
      </section>
      <section class="panel">
        <div class="row-between"><span class="eyebrow" style="margin:0">This month</span><span class="muted">${eur(w.month_eur)} of ${w.monthly_cap_eur} €</span></div>
        <div class="confidence wallet-bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pct}%"></i></div>
        <small class="muted">Up to ${w.monthly_cap_eur} € a month (tax-free limit) · max ${w.daily_cap} tokens a day · today: ${w.today}</small>
      </section>
      <button class="btn primary block" id="redeem" type="button">${ic("qr-code")}Redeem in canteen</button>
      <div class="section-label">Recent earnings</div>
      ${w.events.length ? `<div class="earn-list">${w.events.map((e) => `
        <div class="earn"><b class="earn-amt">+${e.amount}</b><span class="grow">${esc(e.reason)}</span><small class="muted">${fmtDate(e.date)}</small></div>`).join("")}</div>`
        : `<div class="empty"><span class="ei">${ic("coins")}</span>No tokens yet. When your card is approved you get 5, and 10 each time your fix works for a colleague.</div>`}
      <p class="small muted" style="margin-top:14px">Tokens reward tips that help, not how much you record. No tokens for drafts, for "Didn't work", or for rating your own card.</p>`);
    document.getElementById("redeem").addEventListener("click", async () => {
      try {
        const v = await api("/wallet/" + encodeURIComponent(state.user) + "/voucher", { method: "POST" });
        modal.innerHTML = `
          <h2>Canteen voucher</h2>
          <p class="sub">Show this at the canteen till. Demo only, no real payment.</p>
          <div class="voucher">${v.qr_svg}<b>${eur(v.eur)}</b><small class="muted">${esc(v.person)} · ${esc(v.code)}</small></div>
          <form method="dialog"><button class="btn primary block">Done</button></form>`;
        modal.showModal();
      } catch (e) { toast(e.message); }
    });
  }

  // ------------------------------------------------------------ screen: login (demo)
  // Demo sign-in to show the login flow. No real accounts or passwords are stored:
  // the email picks the person, any password works.
  const emailFor = (name) => slug(String(name).replace(/^(Herr|Frau)\s+/i, "")).replace(/-/g, ".") + "@knowflow.demo";
  let mapAnim = null;

  function screenLogin() {
    const people = state.experts.length ? state.experts : [{ name: "Herr Müller" }, { name: "Ahmed" }];
    render(`
      <div class="login">
        <section class="login-card">
          <div class="login-art" aria-hidden="true">
            <canvas id="dotmap"></canvas>
            <div class="login-art-text">
              <span class="login-mark">${ic("arrow-right", "lg")}</span>
              <img class="logo-img" src="logo.png" alt="" style="height:44px;margin:0 auto 8px"><h2>KnowFlow</h2>
              <p>Sign in to share what you fixed today and find what your colleagues already know.</p>
            </div>
          </div>
          <div class="login-form">
            <div class="eyebrow">KnowFlow · Shift log</div>
            <h1>Welcome back</h1>
            <p class="sub">Sign in to your account</p>
            <form id="loginForm" class="stack" novalidate>
              <div>
                <label class="field" for="lemail">Email</label>
                <div class="input-ico">${ic("mail")}<input type="email" id="lemail" autocomplete="username" placeholder="Enter your email address" required></div>
              </div>
              <div>
                <label class="field" for="lpass">Password</label>
                <div class="input-ico">${ic("lock")}<input type="password" id="lpass" autocomplete="current-password" placeholder="Enter your password" required>
                  <button type="button" class="eye" id="leye" aria-label="Show password">${ic("eye")}</button></div>
              </div>
              <p class="login-err" id="lerr" role="alert" hidden></p>
              <button class="btn primary block" id="lsubmit" type="submit">Sign in${ic("arrow-right")}</button>
            </form>
            <div class="login-or"><span>Demo accounts</span></div>
            <div class="chips">${people.map((x) => `<button type="button" class="chip" data-login="${esc(x.name)}">${avatar(x.name, "sm")}&nbsp;${esc(x.name)}</button>`).join("")}</div>
            <p class="small muted" style="margin-top:14px">Demo only: no real accounts yet. Any password works.</p>
          </div>
        </section>
      </div>`);
    const email = document.getElementById("lemail"), pass = document.getElementById("lpass"), err = document.getElementById("lerr");
    document.getElementById("leye").addEventListener("click", (e) => {
      const show = pass.type === "password";
      pass.type = show ? "text" : "password";
      e.currentTarget.innerHTML = ic(show ? "eye-off" : "eye");
      e.currentTarget.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
    view.querySelectorAll("[data-login]").forEach((b) => b.addEventListener("click", () => {
      email.value = emailFor(b.dataset.login); pass.value = "demo"; err.hidden = true;
    }));
    document.getElementById("loginForm").addEventListener("submit", (e) => {
      e.preventDefault();
      const who = people.find((x) => emailFor(x.name) === email.value.trim().toLowerCase());
      err.hidden = false;
      if (!who) { err.textContent = "No account with this email. Pick a demo account below."; return email.focus(); }
      if (!pass.value) { err.textContent = "Enter your password."; return pass.focus(); }
      err.hidden = true;
      store.set("signed_in", "1");
      state.user = who.name; store.set("user", who.name); state.balance = null;
      renderWho(); refreshBalance();
      toast("Signed in as " + who.name, "check");
      location.hash = "#/record";
    });
    startDotMap(document.getElementById("dotmap"));
  }

  // Animated dot field with "knowledge routes" between colleagues (canvas, no library).
  function startDotMap(canvas) {
    if (mapAnim) cancelAnimationFrame(mapAnim);
    if (!canvas || !canvas.parentElement.offsetWidth) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.parentElement.offsetWidth, H = canvas.parentElement.offsetHeight;
    canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = W + "px"; canvas.style.height = H + "px";
    ctx.scale(dpr, dpr);
    const dots = [];
    for (let x = 8; x < W; x += 14) for (let y = 8; y < H; y += 14) {
      const d = Math.hypot((x - W / 2) / W, (y - H / 2) / H);
      if (Math.random() > 0.25 && d < 0.62) dots.push({ x, y, o: (0.75 - d) * (Math.random() * 0.5 + 0.35) });
    }
    const P = (fx, fy) => ({ x: W * fx, y: H * fy });
    const routes = [[P(.22, .25), P(.62, .18), 0], [P(.62, .18), P(.8, .5), 2], [P(.15, .62), P(.5, .78), 1], [P(.82, .3), P(.4, .55), .5], [P(.4, .55), P(.2, .4), 3]];
    const still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    let t0 = performance.now();
    const frame = (now) => {
      if (!document.body.contains(canvas)) return;
      const t = still ? 99 : (now - t0) / 1000;
      ctx.clearRect(0, 0, W, H);
      for (const d of dots) { ctx.beginPath(); ctx.arc(d.x, d.y, 1.1, 0, 7); ctx.fillStyle = `rgba(123,63,228,${d.o})`; ctx.fill(); }
      for (const [a, b, delay] of routes) {
        const k = Math.max(0, Math.min((t - delay) / 3, 1));
        if (!k) continue;
        const x = a.x + (b.x - a.x) * k, y = a.y + (b.y - a.y) * k;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(x, y); ctx.strokeStyle = "rgba(123,63,228,.8)"; ctx.lineWidth = 1.5; ctx.stroke();
        for (const [px, py, rad, col] of [[a.x, a.y, 3, "#7B3FE4"], [x, y, 7, "rgba(192,75,224,.3)"], [x, y, 3, "#C04BE0"]]) {
          ctx.beginPath(); ctx.arc(px, py, rad, 0, 7); ctx.fillStyle = col; ctx.fill();
        }
        if (k === 1) { ctx.beginPath(); ctx.arc(b.x, b.y, 3, 0, 7); ctx.fillStyle = "#7B3FE4"; ctx.fill(); }
      }
      if (t > 14) t0 = now;
      if (!still) mapAnim = requestAnimationFrame(frame);
    };
    mapAnim = requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------ router
  function route() {
    if (activeRecorder) { activeRecorder.cancel(); activeRecorder = null; }
    const [, name, arg] = (location.hash || "#/record").split("/");
    const tab = { play: "practice", challenge: "practice", meetings: "record" }[name] || name;
    document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === tab));
    document.querySelectorAll(".side-nav a").forEach((a) => a.classList.toggle("active", a.dataset.side === tab));
    document.body.dataset.screen = name || "record";
    document.body.classList.toggle("login-mode", name === "login");
    if (name !== "login" && mapAnim) { cancelAnimationFrame(mapAnim); mapAnim = null; }
    window.scrollTo(0, 0);
    switch (name) {
      case "ask": return screenAsk(arg ? decodeURIComponent(arg) : "");
      case "meetings": return screenMeetings();
      case "dashboard": return screenDashboard();
      case "wallet": return screenWallet();
      case "login": return screenLogin();
      case "m": return screenMachine(decodeURIComponent(arg || ""));
      case "card": return screenCard(arg);
      case "practice": return screenPractice();
      case "play": return screenChallenge("", { card_id: arg });
      case "challenge": return screenChallenge(arg ? decodeURIComponent(arg) : "");
      default: return screenRecord();
    }
  }

  document.querySelectorAll("[data-icon]").forEach((el) => (el.innerHTML = ic(el.dataset.icon)));
  window.addEventListener("hashchange", route);
  renderWho();
  refreshBalance();
  Promise.all([refreshMachines(), api("/experts").then((x) => (state.experts = x)).catch(() => {})]).finally(route);
  refreshInviteDot(); setInterval(refreshInviteDot, 5000);
  // laptop top bar: "Ask KnowFlow..." opens Ask with that question
  document.getElementById("topSearch").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = document.getElementById("topQ").value.trim();
    if (!v) return;
    document.getElementById("topQ").value = "";
    location.hash = "#/ask/" + encodeURIComponent(v);
  });
})();

