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
        location.href = location.pathname + (s.analysed ? "?tab=trust" : "?tab=answer");
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
  // size the report iframe to its content (same origin)
  const frame = document.getElementById("report-frame");
  if (frame) {
    const fit = () => {
      try { frame.style.height = frame.contentDocument.documentElement.scrollHeight + 4 + "px"; } catch (e) {}
    };
    frame.addEventListener("load", () => { fit(); setTimeout(fit, 300); });
    window.addEventListener("resize", () => setTimeout(fit, 100));
  }
  // close the mobile sidebar when navigating
  document.querySelectorAll(".side a").forEach((a) => a.addEventListener("click", () => document.body.classList.remove("side-open")));
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
