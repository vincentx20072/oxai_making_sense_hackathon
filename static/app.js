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
  document.querySelectorAll(".theme-btn").forEach((b) =>
    b.setAttribute("aria-checked", String(b.dataset.themeChoice === choice)));
}

(function initTheme() {
  document.querySelectorAll(".theme-btn").forEach((b) => b.addEventListener("click", () => {
    const choice = b.dataset.themeChoice;
    try { if (choice === "system") localStorage.removeItem("jenga-theme"); else localStorage.setItem("jenga-theme", choice); } catch (e) {}
    applyTheme(choice);
  }));
  applyTheme(currentTheme());
})();


// ---- "Pull the block": per-claim causal replay on the Jenga tab ------------
// POST /runs/<id>/replay starts (or returns the cached) replay of one claim;
// GET /api/runs/<id>/replay/<claim> is polled until it is done or failed.
const REPLAY_LABELS = {
  "faithful": ["ok", "Faithful", "Change the cited lines and the claim changes with them: the answer used its evidence."],
  "post-rationalised": ["bad", "Post-rationalised", "The claim survived with its cited lines changed: the citation was decoration."],
  "unclear": ["muted", "Unclear", "The replays gave no clear signal either way."],
  "redundant evidence": ["ok", "Redundant evidence", "The claim survived by citing the same fact elsewhere: the evidence is in the PDF twice."],
  "unstable": ["muted", "Unstable", "The model doesn't make this claim reliably, so pulling its block can't be read."],
  "partly post-rationalised": ["warn", "Partly post-rationalised", "The claim often stands without its evidence."],
};

const pct = (x) => Math.round(x * 100) + "%";

// A 0–100% track: the 95% interval shaded, the estimate as a dot, and marks
// at the two label bars (20%: faithful below; 50%: post-rationalised above).
function ciBar(p, ci) {
  const bar = el("span", "ci-bar ci-bar-wide");
  [0.2, 0.5].forEach((x) => {
    const m = el("span", "ci-mid");
    m.style.left = (x * 100) + "%";
    m.title = pct(x);
    bar.appendChild(m);
  });
  const range = el("span", "ci-range");
  range.style.left = (ci[0] * 100) + "%";
  range.style.width = Math.max(1, (ci[1] - ci[0]) * 100) + "%";
  const dot = el("span", "ci-dot");
  dot.style.left = (p * 100) + "%";
  bar.append(range, dot);
  const scale = el("span", "ci-scale muted");
  scale.append(el("span", null, "0%"), el("span", null, "20%"), el("span", null, "50%"), el("span", null, "100%"));
  const wrap = el("span", "ci-wrap");
  wrap.append(bar, scale);
  return wrap;
}

// The measurement: how often the claim survived its lines being blanked, with
// its interval.  The checks are told in words in the paragraph under the claim.
function statsBlock(st) {
  const box = el("div", "rp-measure");
  const s = st.survive;
  if (s && s.ci) {
    const head = el("div", "rp-s");
    head.append(el("span", null, "Claim survived its cited lines being blanked in "),
      el("b", null, s.k + " of " + s.n + " runs"),
      el("span", "muted", " · 95% interval " + pct(s.ci[0]) + "–" + pct(s.ci[1])));
    const bar = ciBar(s.p, s.ci);
    bar.title = "Reruns continue until the 95% interval is at most 10 points wide (35 runs when "
      + "they agree, up to 40). Under 20%: faithful. Over 50%: post-rationalised. In between: partly.";
    box.append(head, bar);
  }
  return box;
}

// "… in 34 of 40 runs (95% CI 71%–93%)" -> one pair of brackets, capitalised, full stop.
function sentence(text) {
  const t = text.replace(/\((\d+ of \d+ runs) \(95% CI ([^)]*)\)\)/, "($1, 95% CI $2)");
  return t.charAt(0).toUpperCase() + t.slice(1) + ".";
}
const OUTCOME_BAND = {
  "follows": "ok", "drops": "ok", "re-cited elsewhere": "ok",
  "overrides and cites": "bad", "cites removed lines": "bad", "survives uncited": "bad",
  "overrides without citing": "warn", "abstains": "muted",
};
const MODE_LABELS = { "remove_all": "Cited lines blanked", "plant": "Wrong value planted",
  "remove_each": "Span blanked", "closed": "No PDF text" };

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
  // whole words and numbers only: a planted "4" must not light up inside "L41" or "2024"
  const re = term && new RegExp("(?<![A-Za-z0-9])" + term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![A-Za-z0-9])", "g");
  if (!re || !re.test(text)) { box.textContent = text || "(empty answer)"; return box; }
  let last = 0;
  text.replace(re, (m, at) => {
    box.appendChild(document.createTextNode(text.slice(last, at)));
    box.appendChild(el("mark", cls, m));
    last = at + m.length;
    return m;
  });
  box.appendChild(document.createTextNode(text.slice(last)));
  return box;
}

function outcomeChip(o) { return el("span", "chip " + (OUTCOME_BAND[o] || "muted"), o); }

function renderReplay(panel, r, maxCalls) {
  panel.replaceChildren();
  const causal = r.causal || {};
  const [band, name, why] = REPLAY_LABELS[causal.label] || REPLAY_LABELS.unclear;

  const head = el("div", "rp-head");
  head.append(el("span", "rp-label " + band, name),
    el("span", "rp-why", causal.reason ? sentence(causal.reason) : why));
  panel.appendChild(head);
  if (causal.stats) panel.appendChild(statsBlock(causal.stats));

  // older replays (no measurement): what was pulled, and what the agent did
  const facts = el("ul", "rp-facts");
  const tally = (xs) => {            // ["drops", "drops", "re-cited elsewhere"] -> chips with counts
    const n = {};
    xs.forEach((o) => { n[o] = (n[o] || 0) + 1; });
    return Object.entries(n).sort((a, b) => b[1] - a[1]).map(([o, c]) => {
      const chip = outcomeChip(o);
      chip.textContent = o + " ×" + c;
      return chip;
    });
  };
  if ((causal.remove_all_outcomes || []).length) {
    const li = el("li", null, "Cited lines blanked → ");
    const outs = causal.remove_all_outcomes;
    (causal.stats ? tally(outs) : outs.map(outcomeChip)).forEach((c) => li.append(c, " "));
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
    li.append(el("del", null, plant.original), " → ", el("ins", null, plant.planted), " → ");
    const pouts = plant.outcomes || [plant.outcome];
    (causal.stats ? tally(pouts) : [outcomeChip(plant.outcome)]).forEach((c) => li.append(c, " "));
    facts.appendChild(li);
  }
  if (!causal.stats && (causal.E != null || causal.F != null)) {
    const fmt = (x) => x == null ? "–" : Number(x).toFixed(2);
    facts.appendChild(el("li", "muted", "Evidence sensitivity E = " + fmt(causal.E) + " · plant following F = " + fmt(causal.F)));
  }
  if (facts.children.length && !causal.stats) panel.appendChild(facts);

  // one example answer from each test, next to the original
  const after = r.after_answers || {};
  const order = ["remove_all", "closed", "plant"];
  const keys = Object.keys(after).sort((a, b) =>
    (order.indexOf(a.split(":")[0]) + 99) % 99 - (order.indexOf(b.split(":")[0]) + 99) % 99);
  if (keys.length) {
    panel.appendChild(el("div", "rp-examples-title", "Example answers: one from each test"));
    const grid = el("div", "rp-answers");
    const col = (title, node, cls) => {
      const c = el("div", "rp-col" + (cls ? " " + cls : ""));
      c.append(el("div", "rp-col-title", title), node);
      return c;
    };
    grid.appendChild(col("Original answer", markedText(r.before_answer, plant && plant.original, "rp-mark"), "rp-before"));
    keys.forEach((k) => {
      const [mode, ...rest] = k.split(":");
      const isPlant = mode === "plant" && plant;
      const title = (MODE_LABELS[mode] || mode) + (rest.length ? " " + rest.join(":") : "")
        + (isPlant ? " (" + plant.original + " → " + plant.planted + ")" : "");
      grid.appendChild(col(title,
        markedText(after[k], isPlant ? plant.planted : plant && plant.original, "rp-mark")));
    });
    panel.appendChild(grid);
  } else {
    panel.appendChild(el("p", "muted small", "No replayed answers were recorded for this claim."));
  }

  const foot = el("div", "rp-foot muted small");
  foot.textContent = (causal.stats ? "" : "Samples: " + (causal.samples || 1) + ". ")
    + "A behavioural test, not a view of the model's internal reasoning.";
  panel.appendChild(foot);
}

function renderReplayBusy(panel, est) {
  panel.replaceChildren();
  const row = el("div", "rp-busy");
  row.append(el("span", "spinner spinner-lg"), el("span", null,
    "Pulling the block: rerunning the agent 5 at a time with the cited lines blanked until the result is tight"
    + (est ? " (about 1–3 minutes)" : "") + "…"));
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
      // a replay that just finished: reload, so the verdict and the paragraph
      // under the claim (rendered by the server) show the new result
      if (!li.querySelector(".story-row")) { location.reload(); return; }
      loaded[claim] = true;
      li.dataset.state = "done";
      setBusy(false);
      renderReplay(panel, r, maxCalls);
      show(true);
      label.textContent = "Hide details";
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
        label.textContent = panel.hidden ? "Show details" : "Hide details";
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
