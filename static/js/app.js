/* UI logic: tabs, file picking, calling the Flask server, and rendering the report. */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const MAX_FILE = 5 * 1024 * 1024;
  /* If the page is opened with VS Code Live Server (or as a file), talk to the Flask server on port 5000. */
  const API = location.port === "5000" ? "" : "http://127.0.0.1:5000";

  const SAMPLE_CV = `Amina Khan
Junior Web Developer, Lahore

Summary
Web developer with 3 years of experience building websites and small web apps for local businesses.

Experience
Web Developer, BrightPixel Studio, 2024 - Present
- Built responsive websites with HTML, CSS and JavaScript for 12 clients.
- Developed a booking tool with Python and Flask and a MySQL database.
- Worked with Git and GitHub, and collaborated with designers in Figma.

Intern, Softwave Labs, 2023 - 2024
- Fixed bugs and wrote small features in a React front end.
- Wrote REST API calls to connect the interface to the backend.

Education
BS Computer Science, 2023

Skills
HTML, CSS, JavaScript, React, Python, Flask, MySQL, Git, Figma, teamwork, communication`;

  const SAMPLE_JD = `Full-Stack Developer

We are looking for a full-stack developer with 4+ years of experience to join our product team.

Responsibilities
- Build and maintain features across our React front end and Node.js backend.
- Design REST APIs and work with PostgreSQL databases.
- Deploy services using Docker on AWS and maintain CI/CD pipelines.
- Write unit tests and take part in code reviews in an Agile team.

Requirements
- 4+ years of experience with JavaScript or TypeScript.
- Experience with React, Node.js and SQL databases.
- Familiarity with Docker, AWS and Git.
- Strong communication and problem solving skills.`;

  const state = { mode: "upload", file: null, busy: false, ai: false };

  /* ---------- small helpers ---------- */
  function showError(msg) {
    const el = $("#error");
    el.textContent = msg || "";
    el.hidden = !msg;
  }
  function setStatus(msg) {
    $("#status-wrap").hidden = !msg;
    if (msg) $("#status-text").textContent = msg;
  }
  function updateMode() {
    const box = $("#mode");
    box.replaceChildren();
    const dot = document.createElement("span");
    dot.className = "dot" + (state.ai ? "" : " off");
    box.append(dot, state.ai ? "AI analysis is on" : "Basic analysis (AI key not set on the server)");
  }

  fetch(API + "/api/status")
    .then((r) => r.json())
    .then((d) => { state.ai = !!d.ai; updateMode(); })
    .catch(updateMode);

  /* ---------- CV input tabs ---------- */
  function setMode(next) {
    state.mode = next;
    const up = next === "upload";
    $("#tab-upload").setAttribute("aria-selected", String(up));
    $("#tab-paste").setAttribute("aria-selected", String(!up));
    $("#tab-upload").tabIndex = up ? 0 : -1;
    $("#tab-paste").tabIndex = up ? -1 : 0;
    $("#pane-upload").hidden = !up;
    $("#pane-paste").hidden = up;
  }
  $("#tab-upload").addEventListener("click", () => setMode("upload"));
  $("#tab-paste").addEventListener("click", () => setMode("paste"));
  [$("#tab-upload"), $("#tab-paste")].forEach((tab) =>
    tab.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        const next = state.mode === "upload" ? "paste" : "upload";
        setMode(next);
        $("#tab-" + next).focus();
      }
    })
  );

  /* ---------- file picking ---------- */
  const drop = $("#drop");
  const fileInput = $("#cv-file");

  function setFile(file) {
    state.file = file;
    drop.classList.toggle("has-file", !!file);
    $("#drop-title").textContent = file ? file.name : "Choose a PDF or drop it here";
    $("#drop-sub").textContent = file ? Math.max(1, Math.round(file.size / 1024)) + " KB, ready to analyze" : "Text-based PDF, up to 5 MB";
    $("#remove-file").hidden = !file;
    if (!file) fileInput.value = "";
  }
  function pickFile(file) {
    showError("");
    if (!file) return;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
      showError("Upload a PDF file, or paste the CV text instead.");
      return;
    }
    if (file.size > MAX_FILE) {
      showError("That file is larger than 5 MB. Use a smaller PDF or paste the text.");
      return;
    }
    setFile(file);
  }
  fileInput.addEventListener("change", () => pickFile(fileInput.files[0]));
  $("#remove-file").addEventListener("click", () => setFile(null));
  ["dragenter", "dragover"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); })
  );
  ["dragleave", "drop"].forEach((ev) =>
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); })
  );
  drop.addEventListener("drop", (e) => {
    const f = e.dataTransfer && e.dataTransfer.files[0];
    if (f) pickFile(f);
  });

  /* ---------- report rendering (textContent only, so model output cannot inject HTML) ---------- */
  function fillList(id, items, emptyText) {
    const ul = $(id);
    ul.replaceChildren();
    (items.length ? items : [null]).forEach((t) => {
      const li = document.createElement("li");
      if (t === null) li.className = "none";
      li.textContent = t === null ? emptyText : t;
      ul.appendChild(li);
    });
  }

  function animateScore(target) {
    const num = $("#score-num");
    const fill = $("#gauge-fill");
    const gauge = $("#gauge");
    const circ = 326.7;
    gauge.classList.toggle("high", target >= 70);
    gauge.classList.toggle("low", target < 45);
    gauge.setAttribute("aria-label", "Estimated match score: " + target + " out of 100");

    fill.style.strokeDashoffset = String(circ);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      fill.style.strokeDashoffset = String(circ * (1 - target / 100));
      num.textContent = String(target);
      return;
    }
    requestAnimationFrame(() => requestAnimationFrame(() => {
      fill.style.strokeDashoffset = String(circ * (1 - target / 100));
    }));
    const t0 = performance.now();
    (function tick(now) {
      const p = Math.min(1, (now - t0) / 1100);
      num.textContent = String(Math.round(target * (1 - Math.pow(1 - p, 3))));
      if (p < 1) requestAnimationFrame(tick);
    })(t0);
  }

  function render(r) {
    const n = $("#notice");
    n.hidden = !r.notice;
    n.textContent = r.notice || "";

    $("#summary").textContent = r.summary || "No summary was returned.";
    $("#fact-type").textContent = r.model ? r.analysis_type + " (" + r.model + ")" : r.analysis_type;
    $("#fact-kw").textContent = r.keyword_score + "% of the posting's top keywords";
    $("#fact-exp").textContent = r.experience_gap ? "See below" : "Not assessed";
    $("#gap").textContent = r.experience_gap || "No experience gap reported.";

    fillList("#matched", r.matched_skills, "None found");
    fillList("#missing", r.missing_skills, "Nothing major missing");
    fillList("#keywords", r.suggested_keywords, "No suggestions");

    const tips = $("#tips");
    tips.replaceChildren();
    (r.improvement_tips.length ? r.improvement_tips : ["No specific edits were returned. Try again."]).forEach((t) => {
      const li = document.createElement("li");
      li.textContent = t;
      tips.appendChild(li);
    });

    $("#report-wrap").hidden = false;
    animateScore(r.match_score);
    const rep = $("#report");
    rep.scrollIntoView({ behavior: "smooth", block: "start" });
    rep.focus({ preventScroll: true });
  }

  /* ---------- send to the server ---------- */
  $("#form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (state.busy) return;
    showError("");
    const btn = $("#submit");
    const jd = $("#jd-text").value.trim();

    state.busy = true;
    btn.disabled = true;
    btn.textContent = "Analyzing...";
    $("#report-wrap").hidden = true;

    try {
      const data = new FormData();
      if (state.mode === "upload") {
        if (!state.file) throw new Error("Upload your CV as a PDF, or switch to Paste text.");
        data.append("cv_file", state.file);
      } else {
        const cv = $("#cv-text").value.trim();
        if (cv.length < 100) throw new Error("Your CV looks too short. Add a few more lines of text.");
        data.append("cv_text", cv);
      }
      if (jd.length < 50) throw new Error("Paste the full job description (at least a few lines).");
      data.append("job_description", jd);

      setStatus(state.ai ? "Analyzing with the AI model. This usually takes 10 to 30 seconds..." : "Comparing skills and keywords...");
      let res;
      try {
        res = await fetch(API + "/analyze", { method: "POST", body: data });
      } catch (netErr) {
        throw new Error("Could not reach the server. Check that the app is running.");
      }
      const json = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(json.error || "Request failed. Try again.");
      setStatus("");
      render(json);
    } catch (err) {
      setStatus("");
      showError(err.message || "Something went wrong. Try again.");
    } finally {
      state.busy = false;
      btn.disabled = false;
      btn.textContent = "Analyze match";
    }
  });

  $("#sample").addEventListener("click", () => {
    showError("");
    setFile(null);
    setMode("paste");
    $("#cv-text").value = SAMPLE_CV;
    $("#jd-text").value = SAMPLE_JD;
  });

  $("#print").addEventListener("click", () => window.print());
  $("#again").addEventListener("click", () => {
    $("#report-wrap").hidden = true;
    $("#tool").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  /* ---------- theme toggle (light / dark, remembered) ---------- */
  const root = document.documentElement;
  const toggle = $("#theme-toggle");
  function syncToggle() {
    const dark = root.getAttribute("data-theme") === "dark";
    toggle.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    toggle.setAttribute("aria-pressed", String(dark));
  }
  toggle.addEventListener("click", () => {
    const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("resumefit-theme", next); } catch (e) {}
    syncToggle();
  });
  syncToggle();

  updateMode();
})();
