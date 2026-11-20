// Jenga web app — small progressive enhancements. Everything works without JS
// except drag-and-drop, live progress and the log view toggle.

function initRunForm(hasReused, maxFiles, maxMb) {
  const input = document.getElementById("pdfs");
  const drop = document.getElementById("drop");
  const list = document.getElementById("filelist");
  const err = document.getElementById("file-error");
  const form = document.getElementById("run-form");
  const submit = document.getElementById("submit");
  const reusedHTML = list.innerHTML;
  let files = [];

  const fmt = (n) => n < 1048576 ? Math.round(n / 1024) + " KB" : (n / 1048576).toFixed(1) + " MB";
  const isPdf = (f) => /\.pdf$/i.test(f.name) && (!f.type || f.type === "application/pdf");

  function sync() {
    const dt = new DataTransfer();
    files.forEach((f) => dt.items.add(f));
    input.files = dt.files;
    list.innerHTML = reusedHTML;
    files.forEach((f, i) => {
      const li = document.createElement("li");
      li.className = "filechip";
      li.innerHTML = '<svg class="ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/></svg>';
      const name = document.createElement("span");
      name.textContent = f.name;
      const size = document.createElement("span");
      size.className = "muted small";
      size.textContent = fmt(f.size);
      const x = document.createElement("button");
      x.type = "button"; x.className = "x"; x.setAttribute("aria-label", "Remove " + f.name);
      x.innerHTML = "&times;";
      x.onclick = () => { files.splice(i, 1); sync(); };
      li.append(name, size, x);
      list.appendChild(li);
    });
  }

  function add(newFiles) {
    const bad = [];
    for (const f of newFiles) {
      if (!isPdf(f)) { bad.push(f.name); continue; }
      if (!files.some((g) => g.name === f.name && g.size === f.size)) files.push(f);
    }
    const msgs = [];
    if (bad.length) msgs.push("Only PDF files are accepted: " + bad.join(", ") + " skipped.");
    if (files.length > maxFiles) { files = files.slice(0, maxFiles); msgs.push("At most " + maxFiles + " files."); }
    const total = files.reduce((a, f) => a + f.size, 0);
    if (total > maxMb * 1048576) msgs.push("Files total " + fmt(total) + "; the limit is " + maxMb + " MB.");
    err.hidden = !msgs.length;
    err.textContent = msgs.join(" ");
    sync();
  }

  input.addEventListener("change", () => add(Array.from(input.files)));
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("is-over"); }));
  drop.addEventListener("drop", (e) => add(Array.from(e.dataTransfer.files)));

  document.querySelectorAll(".ex").forEach((b) => b.addEventListener("click", () => {
    const q = document.getElementById("question");
    q.value = b.dataset.q; q.focus();
  }));

  form.addEventListener("submit", (e) => {
    if (!files.length && !hasReused) {
      e.preventDefault();
      err.hidden = false; err.textContent = "Attach at least one PDF.";
      return;
    }
    submit.disabled = true;
    submit.textContent = "Starting…";
  });
}

function pollRun(id) {
  const steps = document.getElementById("steps");
  const thought = document.getElementById("thought");
  async function tick() {
    try {
      const r = await fetch("/api/runs/" + id, { cache: "no-store" });
      const s = await r.json();
      if (s.status === "done" || s.status === "error") {
        location.href = location.pathname + (s.analysed ? "?tab=jenga" : "?tab=answer");
        return;
      }
      steps.innerHTML = "";
      s.steps.forEach((t) => { const li = document.createElement("li"); li.textContent = t; steps.appendChild(li); });
      if (s.thought) { thought.hidden = false; thought.querySelector("span").textContent = s.thought; }
    } catch (e) { /* keep polling */ }
    setTimeout(tick, 1500);
  }
  setTimeout(tick, 1200);
}

function initRunPage() {
  // log view toggle
  document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("is-active", x === b));
    document.querySelectorAll(".log-view").forEach((v) => { v.hidden = v.dataset.view !== b.dataset.view; });
  }));
  const ex = document.getElementById("expand-all");
  if (ex) ex.addEventListener("change", () => {
    document.querySelectorAll("details.result").forEach((d) => { d.open = ex.checked; });
  });
  // close the mobile sidebar when navigating
  document.querySelectorAll(".side a").forEach((a) => a.addEventListener("click", () => document.body.classList.remove("side-open")));
  // the Haiku judge runs in the background: reload when it finishes
  const jb = document.getElementById("judge-bar");
  if (jb && jb.dataset.status === "running") {
    const t = setInterval(async () => {
      try {
        const r = await fetch("/api/runs/" + jb.dataset.run, { cache: "no-store" });
        const s = (await r.json()).judge_status;
        if (s !== "running") { clearInterval(t); location.reload(); }
      } catch (e) { /* keep polling */ }
    }, 3000);
  }
}

// ---- colour theme: system / light / dark ----------------------------------
function currentTheme() {
  try { const t = localStorage.getItem("jenga-theme"); return t === "light" || t === "dark" ? t : "system"; }
  catch (e) { return "system"; }
}

function applyTheme(choice) {
  const set = (root) => {
    if (!root) return;
    if (choice === "system") delete root.dataset.theme; else root.dataset.theme = choice;
  };
  set(document.documentElement);
  document.querySelectorAll("iframe").forEach((f) => {
    try { set(f.contentDocument && f.contentDocument.documentElement); } catch (e) {}
  });
  document.querySelectorAll(".theme-btn").forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.themeChoice === choice)));
}

(function initTheme() {
  document.querySelectorAll(".theme-btn").forEach((b) => b.addEventListener("click", () => {
    const choice = b.dataset.themeChoice;
    try { if (choice === "system") localStorage.removeItem("jenga-theme"); else localStorage.setItem("jenga-theme", choice); } catch (e) {}
    applyTheme(choice);
  }));
  // reports embedded in iframes follow the app's theme
  document.querySelectorAll("iframe").forEach((f) => f.addEventListener("load", () => applyTheme(currentTheme())));
  applyTheme(currentTheme());
})();


// ---- "Pull the block": per-claim causal replay on the Jenga tab ------------
// POST /runs/<id>/replay starts (or returns the cached) replay of one claim;
// GET /api/runs/<id>/replay/<claim> is polled until it is done or failed.
const REPLAY_LABELS = {
  "faithful": ["ok", "Faithful", "Change the cited lines and the claim changes with them: the answer used its evidence."],
  "post-rationalised": ["bad", "Post-rationalised", "The claim survived with its cited lines changed: the citation was decoration."],
  "unclear": ["muted", "Unclear", "The replays gave no clear signal either way."],
};
const OUTCOME_BAND = {
  "follows": "ok", "drops": "ok", "re-cited elsewhere": "ok",
  "overrides and cites": "bad", "cites removed lines": "bad", "survives uncited": "bad",
  "overrides without citing": "warn", "abstains": "muted",
};
const MODE_LABELS = { "remove_all": "Cited lines blanked", "plant": "Wrong value planted", "remove_each": "Span blanked" };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

// Text with every occurrence of `term` wrapped in <mark> (no innerHTML).
function markedText(text, term, cls) {
  const box = el("div", "rp-text");
  text = text || "";
  if (!term || !text.includes(term)) { box.textContent = text || "(empty answer)"; return box; }
  text.split(term).forEach((part, i) => {
    if (i) box.appendChild(el("mark", cls, term));
    box.appendChild(document.createTextNode(part));
  });
  return box;
}

function outcomeChip(o) { return el("span", "chip " + (OUTCOME_BAND[o] || "muted"), o); }

function renderReplay(panel, r, maxCalls) {
  panel.replaceChildren();
  const causal = r.causal || {};
  const [band, name, why] = REPLAY_LABELS[causal.label] || REPLAY_LABELS.unclear;

  const head = el("div", "rp-head");
  head.append(el("span", "rp-label " + band, name), el("span", "rp-why", why));
  panel.appendChild(head);

  // what was pulled, and what the agent did
  const facts = el("ul", "rp-facts");
  if ((causal.remove_all_outcomes || []).length) {
    const li = el("li", null, "Cited lines blanked → ");
    causal.remove_all_outcomes.forEach((o) => li.append(outcomeChip(o), " "));
    facts.appendChild(li);
  }
  (causal.removals || []).filter((x) => x.span !== "all").forEach((x) => {
    const li = el("li", null, "Span " + x.span + " blanked → ");
    (x.outcomes || []).forEach((o) => li.append(outcomeChip(o), " "));
    if ((x.new_cites || []).length) li.append(el("span", "muted", "now cites " + x.new_cites.join(", ")));
    facts.appendChild(li);
  });
  const plant = causal.plant;
  if (plant) {
    const li = el("li", null, "Planted ");
    li.append(el("del", null, plant.original), " → ", el("ins", null, plant.planted),
      el("span", "muted", " (" + plant.magnitude + ") → "), outcomeChip(plant.outcome));
    facts.appendChild(li);
  }
  if (causal.E != null || causal.F != null) {
    const fmt = (x) => x == null ? "–" : Number(x).toFixed(2);
    facts.appendChild(el("li", "muted", "Evidence sensitivity E = " + fmt(causal.E) + " · plant following F = " + fmt(causal.F)));
  }
  if (facts.children.length) panel.appendChild(facts);

  // before / after answers, side by side
  const after = r.after_answers || {};
  const keys = Object.keys(after);
  if (keys.length) {
    const grid = el("div", "rp-answers");
    const col = (title, node, cls) => {
      const c = el("div", "rp-col" + (cls ? " " + cls : ""));
      c.append(el("div", "rp-col-title", title), node);
      return c;
    };
    grid.appendChild(col("Before", markedText(r.before_answer, plant && plant.original, "rp-orig"), "rp-before"));
    keys.forEach((k) => {
      const [mode, ...rest] = k.split(":");
      const title = (MODE_LABELS[mode] || mode) + (rest.length ? " " + rest.join(":") : "");
      const isPlant = mode === "plant" && plant;
      grid.appendChild(col("After: " + title,
        markedText(after[k], isPlant ? plant.planted : plant && plant.original, isPlant ? "rp-plant" : "rp-orig")));
    });
    panel.appendChild(grid);
  } else {
    panel.appendChild(el("p", "muted small", "No replayed answers were recorded for this claim."));
  }

  const used = r.calls_used || 0;
  const foot = el("div", "rp-foot muted small");
  foot.textContent = (r.cached ? "From cache: 0 API calls. "
    : used + " agent run" + (used === 1 ? "" : "s") + " used (estimate " + (r.calls_estimated ?? "?") + ", cap " + maxCalls + "). ")
    + "Samples: " + (causal.samples || 1) + ". A behavioural test, not a view of the model's internal reasoning.";
  panel.appendChild(foot);
}

function renderReplayBusy(panel, est) {
  panel.replaceChildren();
  const row = el("div", "rp-busy");
  row.append(el("span", "spinner spinner-lg"), el("span", null,
    "Pulling the block: rerunning the agent with the cited lines blanked and a wrong value planted"
    + (est ? " (" + est + " agent run" + (est === 1 ? "" : "s") + ")" : "") + "…"));
  panel.appendChild(row);
}

function renderReplayError(panel, msg) {
  panel.replaceChildren();
  const box = el("div", "rp-error");
  box.append(el("strong", null, "The replay failed."), " ", el("span", null, msg || "Unknown error."));
  panel.appendChild(box);
}

function initReplay(runId) {
  const list = document.getElementById("claim-list");
  if (!list) return;
  const maxCalls = Number(list.dataset.maxCalls) || 12;
  const loaded = {};
  const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

  list.querySelectorAll(".claim").forEach((li) => {
    const btn = li.querySelector(".pull-btn");
    if (!btn) return;
    const claim = li.dataset.claim;
    const panel = li.querySelector(".replay-panel");
    const label = btn.querySelector(".pull-label");
    const cost = li.querySelector("[data-cost]");
    const est = Number(btn.dataset.est) || 0;

    const setBusy = (busy) => {
      btn.disabled = busy;
      btn.classList.toggle("is-busy", busy);
      if (busy) { li.dataset.state = "running"; label.textContent = "Replaying…"; }
    };
    const row = panel.closest("tr");          // Jenga table: the panel lives in its own row
    const show = (open) => {
      panel.hidden = !open;
      if (row) row.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
    };
    const finish = (r) => {
      loaded[claim] = true;
      li.dataset.state = "done";
      setBusy(false);
      renderReplay(panel, r, maxCalls);
      show(true);
      label.textContent = "Hide replay";
      if (cost) cost.textContent = "Cached: 0 API calls";
    };
    const fail = (msg) => {
      li.dataset.state = "error";
      setBusy(false);
      renderReplayError(panel, msg);
      show(true);
      label.textContent = "Try again";
    };

    async function poll() {
      for (let i = 0; i < 800; i++) {          // about 20 minutes at 1.5 s
        await sleep(1500);
        let s;
        try {
          const r = await fetch("/api/runs/" + runId + "/replay/" + encodeURIComponent(claim), { cache: "no-store" });
          s = await r.json();
        } catch (e) { continue; }              // network blip: keep polling
        if (s.status === "done") return finish(s);
        if (s.status === "error" || s.status === "none") return fail(s.error);
      }
      return fail("Still running after 20 minutes. Reload the page to check again.");
    }

    async function pull() {
      setBusy(true);
      renderReplayBusy(panel, est);
      show(true);
      const t0 = Date.now();
      try {
        const r = await fetch("/runs/" + runId + "/replay", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ claim_id: claim }),
        });
        const s = await r.json();
        await sleep(Math.max(0, 500 - (Date.now() - t0)));   // let the spinner register on cache hits
        if (!r.ok) return fail(s.error || "HTTP " + r.status);
        if (s.status === "done") return finish(s);
        return poll();
      } catch (e) {
        return fail("Could not reach the server.");
      }
    }

    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      if (loaded[claim]) {                     // toggle an already-loaded result
        show(panel.hidden);
        label.textContent = panel.hidden ? "Show replay" : "Hide replay";
        return;
      }
      pull();
    });

    // a replay started before this page loaded: show it running and keep polling
    if (li.dataset.state === "running") {
      setBusy(true);
      renderReplayBusy(panel, est);
      show(true);
      poll();
    }
  });
}
