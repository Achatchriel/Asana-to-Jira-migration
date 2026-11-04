"use strict";

// ------------------------------------------------------------------ stan i narzędzia

const S = {
  catalog: null,
  projects: null,
  overview: null,
  selection: new Set(),
  prefill: null,      // wartości formularza przekazane przy przejściu do narzędzia
  viewToken: 0,       // zmienia się przy każdej zmianie widoku - przerywa długie odpytywanie
};

try { (JSON.parse(localStorage.getItem("selection") || "[]")).forEach(k => S.selection.add(k)); } catch (e) { /* brak */ }

function saveSelection() {
  try { localStorage.setItem("selection", JSON.stringify([...S.selection])); } catch (e) { /* brak */ }
  renderSelection();
}

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch("/api/" + path, opt);
  let data = null;
  try { data = await res.json(); } catch (e) { data = { error: "Niepoprawna odpowiedź serwera" }; }
  if (!res.ok) { const err = new Error(data.error || res.statusText); err.status = res.status; err.data = data; throw err; }
  return data;
}

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const $ = (sel, root = document) => root.querySelector(sel);
const fmtTime = t => t ? new Date(t * 1000).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" }) : "—";
const fmtDur = (a, b) => { if (!a) return ""; const s = Math.round(((b || Date.now() / 1000) - a)); return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min`; };
const fmtSize = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
const pill = (text, kind) => h("span", { class: "pill " + (kind || "") }, text);

function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), bad ? 7000 : 3500);
}

const STATUS_LABEL = { running: ["trwa", "info"], ok: ["OK", "ok"], partial: ["częściowo", "warn"], failed: ["błąd", "bad"], stopped: ["przerwane", "warn"], lost: ["utracone", "warn"] };
const runPill = st => pill(...(STATUS_LABEL[st] || [st, ""]));

function jiraLink(key) {
  const base = S.overview && S.overview.jira_base_url;
  return base ? h("a", { href: `${base}/browse/${key}`, target: "_blank", rel: "noopener" }, key) : h("span", { class: "mono" }, key);
}

// ------------------------------------------------------------------ routing

const VIEWS = {};

function go(hash) { location.hash = hash; }

async function route() {
  const [pathPart, queryPart] = location.hash.replace(/^#\/?/, "").split("?");
  S.query = new URLSearchParams(queryPart || "");
  const [view, ...args] = (pathPart || "overview").split("/").filter((x, i) => i === 0 || x).map(decodeURIComponent);
  S.viewToken++;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  const main = $("#main");
  main.replaceChildren(h("p", { class: "muted" }, "Ładowanie…"));
  try {
    await (VIEWS[view] || VIEWS.overview)(main, ...args);
  } catch (e) {
    main.replaceChildren(h("div", { class: "notice bad" }, "Błąd: " + e.message));
  }
}

document.querySelectorAll("#nav a").forEach(a => a.addEventListener("click", () => go("#/" + a.dataset.view)));
window.addEventListener("hashchange", route);

function renderSelection() {
  const list = $("#selection-list");
  list.textContent = S.selection.size ? [...S.selection].join(", ") : "brak";
  $("#selection-clear").hidden = !S.selection.size;
}
$("#selection-clear").addEventListener("click", () => { S.selection.clear(); saveSelection(); if (location.hash.startsWith("#/projects")) route(); });

async function ensureCatalog() { if (!S.catalog) S.catalog = await api("GET", "catalog"); return S.catalog; }
async function ensureProjects(refresh) {
  if (!S.projects || refresh) S.projects = await api("GET", "projects" + (refresh ? "?refresh=1" : ""));
  return S.projects;
}

function projectDatalist() {
  const dl = h("datalist", { id: "project-keys" });
  (S.projects ? S.projects.projects : []).forEach(p => dl.append(h("option", { value: p.jira_key }, p.name)));
  return dl;
}

async function refreshBadge() {
  try {
    const o = await api("GET", "overview");
    S.overview = o;
    const b = $("#running-badge");
    b.textContent = o.running;
    b.classList.toggle("hidden", !o.running);
  } catch (e) { /* serwer chwilowo niedostępny */ }
}
setInterval(refreshBadge, 5000);

// ------------------------------------------------------------------ start narzędzia

async function startTool(toolId, values) {
  try {
    return await api("POST", "runs", { tool_id: toolId, values });
  } catch (e) {
    if (e.status !== 409 || !e.data.needs_confirm) throw e;
    const tool = S.catalog.tools.find(t => t.id === toolId);
    const ok = await confirmRun(tool, e.data.argv);
    if (!ok) return null;
    return await api("POST", "runs", { tool_id: toolId, values, confirm: true });
  }
}

function confirmRun(tool, argv) {
  return new Promise(resolve => {
    const dlg = $("#confirm");
    $("#confirm-text").textContent = `„${tool.title}” zapisze zmiany${tool.browser ? " (także przez przeglądarkę)" : ""}. Upewnij się, że wcześniej sprawdziłeś podgląd.`;
    $("#confirm-cmd").textContent = ["python", tool.script, ...argv].join(" ");
    const ack = $("#confirm-ack"), btn = $("#confirm-ok");
    ack.checked = false; btn.disabled = true;
    ack.onchange = () => { btn.disabled = !ack.checked; };
    dlg.onclose = () => resolve(dlg.returnValue === "ok");
    dlg.showModal();
  });
}

// ------------------------------------------------------------------ log na żywo

function colorize(line) {
  let cls = "";
  if (/BŁĄD|ERROR|Traceback|Exception|błąd|FAIL/i.test(line)) cls = "e";
  else if (/OSTRZEŻENIE|UWAGA|WARN|pomijam/i.test(line)) cls = "w";
  else if (/\bOK\b|✓|Gotowe|zakończon|sukces/i.test(line)) cls = "o";
  else if (/^\s*(=+|-{3,}|\[\d+\/\d+\]|Krok)/.test(line)) cls = "i";
  return h("div", { class: cls }, line || " ");
}

function mountLog(container, runId, onUpdate) {
  const token = S.viewToken;
  const log = h("div", { class: "log" });
  container.append(log);
  let from = 0;
  (async function loop() {
    while (token === S.viewToken) {
      let r;
      try { r = await api("GET", `runs/${runId}?from=${from}&wait=20`); } catch (e) { await new Promise(res => setTimeout(res, 2000)); continue; }
      if (token !== S.viewToken) return;
      const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
      const frag = document.createDocumentFragment();
      r.new_lines.forEach(l => frag.append(colorize(l)));
      log.append(frag);
      from += r.new_lines.length;
      if (stick) log.scrollTop = log.scrollHeight;
      onUpdate && onUpdate(r);
      if (r.status !== "running") return;
    }
  })();
  return log;
}

// ------------------------------------------------------------------ Przegląd

VIEWS.overview = async main => {
  const [o] = await Promise.all([api("GET", "overview"), ensureCatalog()]);
  S.overview = o;
  const notices = [];
  if (!o.env_exists) notices.push(h("div", { class: "notice bad" }, "Brak pliku .env. ", h("button", { class: "link", onclick: () => go("#/config/env") }, "Uzupełnij konfigurację →")));
  else if (o.env_missing.length) notices.push(h("div", { class: "notice warn" }, "Brakujące zmienne w .env: ", o.env_missing.join(", "), ". ", h("button", { class: "link", onclick: () => go("#/config/env") }, "Uzupełnij →")));
  const oldSession = o.browser_sessions.filter(s => s.age_hours > 24);
  if (!o.browser_sessions.length) notices.push(h("div", { class: "notice warn" }, "Brak zapisanej sesji przeglądarki - kroki 2-3 (kolumny, statusy kolumn) jej wymagają. ", h("button", { class: "link", onclick: () => go("#/tools/save_login") }, "Zapisz sesję →")));
  else if (oldSession.length) notices.push(h("div", { class: "notice warn" }, `Sesja przeglądarki ma ${oldSession[0].age_hours} h - może wymagać ponownego logowania przed krokami 2-3.`));

  const stat = (value, label) => h("div", { class: "card stat" }, h("div", { class: "value" }, value), h("div", { class: "label" }, label));
  const quick = (label, onclick, cls) => h("button", { class: cls || "", onclick }, label);

  main.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Przegląd"), h("small", {}, o.root)),
      h("div", { class: "row" },
        quick("Sprawdź statusy i osoby", () => go("#/check")),
        quick("Weryfikacja liczby zadań", () => go("#/tools/count_migrated")),
        quick("Pełny pipeline…", () => go("#/tools/full_pipeline"), "primary"))),
    ...notices,
    h("div", { class: "grid cols-4" },
      stat(o.state.projects, "projektów w stanie synchronizacji"),
      stat(o.state.entries.toLocaleString("pl-PL"), "zmapowanych zadań (task_sync_state.json)"),
      stat(o.user_map_size, "osób w user_map.json"),
      stat(o.running, "trwających uruchomień")),
    h("div", { class: "grid cols-2", style: "margin-top:14px" },
      h("div", { class: "card" },
        h("h2", {}, "Ochrona projektów"),
        h("p", {}, h("strong", {}, "protected_projects.txt"), h("small", {}, " - wyłączone ze wszystkiego")),
        h("p", { class: "mono" }, o.protected.join(", ") || "—"),
        h("p", {}, h("strong", {}, "sync_exclude.txt"), h("small", {}, " - wyłączone z synchronizacji")),
        h("p", { class: "mono" }, o.excluded.length ? `${o.excluded.length}: ${o.excluded.slice(0, 40).join(", ")}${o.excluded.length > 40 ? "…" : ""}` : "—"),
        h("button", { class: "small", onclick: () => go("#/config/files/sync_exclude.txt") }, "Edytuj listy")),
      h("div", { class: "card" },
        h("h2", {}, "Środowisko"),
        h("table", {},
          h("tr", {}, h("td", {}, "Python"), h("td", { class: "mono" }, o.python)),
          h("tr", {}, h("td", {}, "Jira"), h("td", { class: "mono" }, o.jira_base_url || "—")),
          h("tr", {}, h("td", {}, "Plik stanu"), h("td", {}, `${o.state.size_mb} MB, zmieniony ${fmtTime(o.state.mtime)}`)),
          ...o.browser_sessions.map(s => h("tr", {}, h("td", {}, "Sesja przeglądarki"), h("td", {}, `${s.path} (${s.age_hours} h)`)))))),
    h("div", { class: "card", style: "margin-top:14px" },
      h("div", { class: "row" }, h("h2", {}, "Ostatnie uruchomienia"), h("span", { class: "spacer" }), h("button", { class: "small", onclick: () => go("#/runs") }, "Wszystkie")),
      o.recent_runs.length ? runsTable(o.recent_runs) : h("p", { class: "muted" }, "Jeszcze nic nie uruchomiono z panelu.")),
  );
};

function runsTable(runs) {
  return h("table", {},
    h("tr", {}, h("th", {}, "Narzędzie"), h("th", {}, "Polecenie"), h("th", {}, "Start"), h("th", {}, "Czas"), h("th", {}, "Status")),
    runs.map(r => h("tr", { class: "clickable", onclick: () => go("#/runs/" + r.id) },
      h("td", {}, r.title, r.writes ? h("span", {}, " ", pill("zapis", "warn")) : null),
      h("td", { class: "mono", style: "max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, r.command),
      h("td", {}, fmtTime(r.started)),
      h("td", {}, fmtDur(r.started, r.ended)),
      h("td", {}, runPill(r.status), r.returncode !== null && r.returncode !== 0 ? h("small", {}, ` kod ${r.returncode}`) : null))));
}

// ------------------------------------------------------------------ Projekty

VIEWS.projects = async main => {
  main.replaceChildren(h("p", { class: "muted" }, "Wczytywanie arkusza Google…"));
  const data = await ensureProjects(false);
  let filter = "all", query = "";
  const tbody = h("tbody");
  const counter = h("small");

  const FILTERS = [["all", "Wszystkie"], ["selected", "Wybrane"], ["nostate", "Bez stanu"], ["map", "Z mapą projektu"], ["excluded", "Wyłączone z sync"], ["protected", "Chronione"], ["issues", "Problemy w sprawdzeniu"]];
  const match = p => {
    if (query && !(`${p.jira_key} ${p.name}`.toLowerCase().includes(query))) return false;
    switch (filter) {
      case "selected": return S.selection.has(p.jira_key);
      case "nostate": return !p.state_entries && !p.protected;
      case "map": return p.has_map;
      case "excluded": return p.excluded;
      case "protected": return p.protected;
      case "issues": return p.check && (p.check.errors || p.check.warnings || p.check.status_bad || p.check.assignee_bad);
      default: return true;
    }
  };
  const visible = () => data.projects.filter(match);

  function draw() {
    const rows = visible();
    counter.textContent = `${rows.length} z ${data.projects.length}`;
    tbody.replaceChildren(...rows.map(p => {
      const cb = h("input", { type: "checkbox", checked: S.selection.has(p.jira_key), disabled: p.protected,
        onchange: e => { e.target.checked ? S.selection.add(p.jira_key) : S.selection.delete(p.jira_key); saveSelection(); } });
      const c = p.check;
      let checkCell = h("span", { class: "muted" }, "—");
      if (c) {
        const bad = (c.status_bad || 0) + (c.assignee_bad || 0);
        checkCell = h("span", {}, c.errors ? pill("błąd", "bad") : bad ? pill(`${bad} różnic`, "warn") : c.warnings ? pill(`${c.warnings} ostrz.`, "warn") : pill("zgodne", "ok"), h("small", {}, " " + fmtTime(c.at)));
      }
      return h("tr", {},
        h("td", {}, cb),
        h("td", {}, jiraLink(p.jira_key)),
        h("td", {}, p.name || h("span", { class: "muted" }, "—"), p.asana_link ? h("span", {}, " ", h("a", { href: p.asana_link, target: "_blank", rel: "noopener", title: "Asana" }, "↗")) : null),
        h("td", { class: "num" }, p.state_entries || h("span", { class: "muted" }, "0")),
        h("td", {}, p.has_map ? pill(p.profile ? `szablon: ${p.profile}` : "własna", "info") : h("span", { class: "muted" }, "główna")),
        h("td", {}, p.protected ? pill("chroniony", "bad") : null, " ", p.excluded ? pill("bez sync", "warn") : null),
        h("td", {}, checkCell),
        h("td", {}, p.protected ? null : h("button", { class: "small", onclick: () => go("#/check/" + p.jira_key) }, "Statusy i osoby")));
    }));
  }

  const actions = h("div", { class: "row" },
    h("button", { onclick: () => { visible().filter(p => !p.protected).forEach(p => S.selection.add(p.jira_key)); saveSelection(); draw(); } }, "Zaznacz widoczne"),
    h("button", { onclick: () => { visible().forEach(p => S.selection.delete(p.jira_key)); saveSelection(); draw(); } }, "Odznacz widoczne"),
    h("span", { class: "spacer" }),
    h("button", { onclick: () => withSelection(keys => runCheck(keys)) }, "Sprawdź statusy wybranych"),
    h("button", { onclick: () => withSelection(keys => openTool("asana_jira_sync", { "--project-keys": keys.join(",") })) }, "Synchronizacja…"),
    h("button", { class: "primary", onclick: () => withSelection(keys => openTool("full_pipeline", { "--project-keys": keys.join(",") })) }, "Pipeline dla wybranych…"));

  main.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Projekty"), h("small", {}, `Źródło: ${data.source} · wczytano ${fmtTime(data.loaded_at)}`)),
      h("button", { onclick: async e => { e.target.disabled = true; e.target.textContent = "Wczytywanie…"; await ensureProjects(true); route(); } }, "Odśwież z arkusza")),
    data.error ? h("div", { class: "notice warn" }, "Nie udało się odczytać arkusza - pokazuję dane lokalne. ", h("details", {}, h("summary", {}, "Szczegóły"), h("pre", { class: "cmd" }, data.error))) : null,
    h("div", { class: "row", style: "margin-bottom:10px" },
      h("input", { type: "search", placeholder: "Szukaj po kluczu lub nazwie…", style: "max-width:320px", oninput: e => { query = e.target.value.toLowerCase(); draw(); } }),
      h("select", { style: "max-width:220px", onchange: e => { filter = e.target.value; draw(); } }, FILTERS.map(([v, l]) => h("option", { value: v }, l))),
      counter),
    actions,
    h("div", { class: "table-wrap", style: "margin-top:10px" },
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Klucz"), h("th", {}, "Projekt Asany"), h("th", { class: "num" }, "Wpisy stanu"), h("th", {}, "Mapa sekcji"), h("th", {}, "Flagi"), h("th", {}, "Ostatnie sprawdzenie"), h("th", {}, ""))), tbody)));
  draw();
};

function withSelection(fn) {
  if (!S.selection.size) return toast("Najpierw zaznacz projekty na liście.", true);
  fn([...S.selection]);
}

function openTool(toolId, values) { S.prefill = values; go("#/tools/" + toolId); }

async function runCheck(keys) {
  await ensureCatalog();
  const run = await startTool("status_check", { pos0: keys.join(",") });
  if (run) go(`#/check/${keys.length === 1 ? keys[0] : ""}?run=${encodeURIComponent(run.id)}`);
}

// ------------------------------------------------------------------ Narzędzia

VIEWS.tools = async (main, toolId) => {
  const cat = await ensureCatalog();
  ensureProjects(false).catch(() => null);
  const tool = cat.tools.find(t => t.id === toolId) || null;

  const list = h("div", { class: "tool-list" },
    cat.groups.map(g => [
      h("div", { class: "tool-group" }, g.title),
      cat.tools.filter(t => t.group === g.id).map(t => h("div", { class: "tool-item" + (tool && t.id === tool.id ? " active" : ""), onclick: () => go("#/tools/" + t.id) },
        h("span", {}, t.title),
        t.writes === "never" ? h("span", { class: "w muted" }, "odczyt") : t.writes === "always" ? h("span", { class: "w", style: "color:var(--bad)" }, "zapis") : h("span", { class: "w muted" }, "podgląd")))]));

  const body = h("div");
  main.replaceChildren(h("div", { class: "page-head" }, h("h1", {}, "Narzędzia")), h("div", { class: "tools-layout" }, list, body));
  if (!tool) {
    body.append(h("div", { class: "card" }, h("h2", {}, "Wybierz narzędzie z listy"),
      h("p", { class: "muted" }, "Każde narzędzie to istniejący skrypt jiraSync. Panel składa polecenie z formularza, pokazuje je przed uruchomieniem i wymaga potwierdzenia, gdy uruchomienie zmienia dane. Zaczynaj od podglądu (dry-run) albo trybu raportu.")));
    return;
  }
  body.append(toolForm(tool));
};

function toolForm(tool) {
  const values = {};
  const prefill = S.prefill || {};
  S.prefill = null;
  const cmd = h("pre", { class: "cmd" }, "…");
  const writesNote = h("div");
  let timer;

  const update = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      try {
        const r = await api("POST", "preview", { tool_id: tool.id, values });
        cmd.textContent = r.command;
        writesNote.replaceChildren(r.writes ? h("div", { class: "notice warn" }, "To uruchomienie ZMIENI dane - panel poprosi o potwierdzenie.") : h("div", { class: "notice ok" }, "Tylko odczyt / podgląd - nic nie zostanie zmienione."));
      } catch (e) {
        cmd.textContent = e.message;
        writesNote.replaceChildren();
      }
    }, 150);
  };

  const fields = tool.params.map(p => {
    const name = p.name;
    let input;
    let initial = prefill[name];
    if (initial === undefined && p.kind === "keys" && !p.single && S.selection.size) initial = [...S.selection].join(",");
    if (initial === undefined && p.kind === "pos" && /keys?/.test(p.kind_hint || "") && S.selection.size) initial = p.kind_hint === "key" ? [...S.selection][0] : [...S.selection].join(",");
    if (p.kind === "bool") {
      values[name] = initial !== undefined ? !!initial : !!p.default;
      input = h("input", { type: "checkbox", checked: values[name], onchange: e => { values[name] = e.target.checked; update(); } });
      return h("label", { class: "check", style: "grid-column: span 1" }, input, h("span", {}, p.label, h("small", { class: "mono" }, p.flag ? "  " + p.flag : "")));
    }
    values[name] = initial !== undefined ? initial : "";
    const set = e => { values[name] = e.target.value; update(); };
    if (p.kind === "choice" || p.options) {
      input = h("select", { onchange: set }, (p.options.includes("") ? p.options : ["", ...p.options]).map(o => h("option", { value: o, selected: o === values[name] }, o || "(domyślnie)")));
    } else if (p.kind === "repeat") {
      input = h("textarea", { rows: 3, placeholder: p.placeholder || "", oninput: set }, values[name]);
    } else {
      input = h("input", { type: p.kind === "int" ? "number" : "text", value: values[name], placeholder: p.placeholder || "", oninput: set,
        list: (p.kind === "keys" || /key/.test(p.kind_hint || "")) ? "project-keys" : null });
    }
    const extra = (p.kind === "keys" && !p.single) || p.kind_hint === "keys"
      ? h("button", { class: "link small", type: "button", onclick: () => { if (!S.selection.size) return toast("Brak wybranych projektów.", true); input.value = [...S.selection].join(","); values[name] = input.value; update(); } }, "wstaw wybrane projekty")
      : null;
    return h("div", { class: "field", style: p.kind === "repeat" || p.kind === "keys" && !p.single ? "grid-column: 1 / -1" : null },
      h("span", {}, p.label, p.required ? " *" : "", p.flag ? h("span", { class: "mono" }, "  " + p.flag) : ""), input, extra);
  });

  const runBtn = h("button", { class: "primary", onclick: async () => {
    runBtn.disabled = true;
    try {
      const run = await startTool(tool.id, values);
      if (run) go("#/runs/" + run.id);
    } catch (e) { toast(e.message, true); }
    runBtn.disabled = false;
  } }, "Uruchom");

  update();
  return h("div", {},
    projectDatalist(),
    h("div", { class: "card" },
      h("div", { class: "row" }, h("h2", {}, tool.title), h("span", { class: "spacer" }), h("span", { class: "mono muted" }, tool.script)),
      h("p", {}, tool.desc),
      tool.browser ? h("div", { class: "notice" }, "Narzędzie używa przeglądarki (Playwright) - potrzebna aktualna sesja logowania (narzędzie „Zapis sesji przeglądarki”).") : null,
      tool.params.length ? h("div", { class: "form-grid", style: "margin-top:12px" }, fields) : h("p", { class: "muted" }, "Bez parametrów.")),
    h("div", { class: "card" },
      h("h3", {}, "Polecenie"), cmd, h("div", { style: "margin-top:10px" }, writesNote),
      h("div", { class: "row end" }, runBtn)));
}

// ------------------------------------------------------------------ Uruchomienia

VIEWS.runs = async (main, runId) => {
  const { runs } = await api("GET", "runs");
  if (!runId) {
    main.replaceChildren(
      h("div", { class: "page-head" }, h("h1", {}, "Uruchomienia"), h("small", {}, "Historia zapisywana w migration_app/runs/")),
      runs.length ? h("div", { class: "table-wrap" }, runsTable(runs)) : h("div", { class: "card muted" }, "Brak uruchomień."));
    return;
  }
  const meta = runs.find(r => r.id === runId);
  if (!meta) throw new Error("Nie ma takiego uruchomienia.");
  const statusEl = h("span", {}, runPill(meta.status));
  const durEl = h("small", {});
  const input = h("input", { type: "text", placeholder: "Odpowiedź dla skryptu (np. t / tak / Enter) …", style: "max-width:380px" });
  const sendBtn = h("button", { onclick: async () => { try { await api("POST", `runs/${runId}/input`, { text: input.value }); input.value = ""; } catch (e) { toast(e.message, true); } } }, "Wyślij");
  input.addEventListener("keydown", e => { if (e.key === "Enter") sendBtn.click(); });
  const stopBtn = h("button", { class: "danger", onclick: async () => { if (confirm("Przerwać to uruchomienie?")) await api("POST", `runs/${runId}/stop`); } }, "Przerwij");
  const delBtn = h("button", { class: "ghost", onclick: async () => { if (!confirm("Usunąć wpis i log z historii?")) return; await api("DELETE", `runs/${runId}`); go("#/runs"); } }, "Usuń z historii");
  const inputRow = h("div", { class: "row", style: "margin-top:10px" }, input, sendBtn, h("span", { class: "spacer" }), stopBtn);
  const followUp = h("div");
  const logBox = h("div", { style: "margin-top:12px" });

  main.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("button", { class: "link", onclick: () => go("#/runs") }, "← Uruchomienia"), h("h1", {}, meta.title)),
      h("div", { class: "row" }, statusEl, durEl, delBtn)),
    h("div", { class: "card" },
      h("pre", { class: "cmd" }, meta.command),
      h("div", { class: "row", style: "margin-top:8px" }, h("small", {}, "Start: " + fmtTime(meta.started)), meta.writes ? pill("zapis", "warn") : pill("odczyt / podgląd"),
        h("span", { class: "spacer" }), h("button", { class: "small", onclick: () => openTool(meta.tool_id, null) }, "Uruchom ponownie…")),
      inputRow, logBox, followUp));

  mountLog(logBox, runId, r => {
    statusEl.replaceChildren(runPill(r.status));
    durEl.textContent = fmtDur(r.started, r.ended) + (r.returncode !== null ? ` · kod ${r.returncode}` : "");
    const running = r.status === "running";
    inputRow.classList.toggle("hidden", !running);
    delBtn.disabled = running;
    if (!running && r.tool_id === "status_check") {
      const keys = (r.argv[0] || "").split(",").filter(Boolean);
      followUp.replaceChildren(h("div", { class: "row", style: "margin-top:12px" }, "Wyniki: ", keys.map(k => h("button", { class: "small", onclick: () => go("#/check/" + k) }, k))));
    }
  });
};

// ------------------------------------------------------------------ Statusy i osoby

VIEWS.check = async (main, keyArg) => {
  await ensureCatalog();
  const key = (keyArg || "").toUpperCase();
  const runParam = S.query.get("run");
  await ensureProjects(false).catch(() => null);

  const keyInput = h("input", { type: "text", list: "project-keys", value: key || [...S.selection].join(","), placeholder: "Klucz(e) projektu, np. ABC albo ABC,DEF", style: "max-width:340px" });
  const checkBtn = h("button", { class: "primary", onclick: async () => {
    const keys = keyInput.value.toUpperCase().split(",").map(s => s.trim()).filter(Boolean);
    if (!keys.length) return toast("Podaj klucz projektu.", true);
    try { await runCheck(keys); } catch (e) { toast(e.message, true); }
  } }, "Sprawdź teraz");
  const head = h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Statusy i osoby"), h("small", {}, "Sekcje Asany → statusy Jiry, przypisani → konta Jiry, zgodność zmigrowanych zadań. Tylko odczyt.")),
    h("div", { class: "row" }, projectDatalist(), keyInput, checkBtn));
  const body = h("div");
  main.replaceChildren(head, body);

  if (runParam) {
    const box = h("div", { class: "card" }, h("h3", {}, "Sprawdzanie…"));
    body.append(box);
    mountLog(box, runParam, r => {
      if (r.status !== "running") {
        const keys = (r.argv[0] || "").split(",").filter(Boolean);
        if (keys.length === 1) { go("#/check/" + keys[0]); return; }
        box.append(h("div", { class: "row", style: "margin-top:10px" }, "Wyniki: ", keys.map(k => h("button", { class: "small", onclick: () => go("#/check/" + k) }, k))));
      }
    }).style.height = "30vh";
    return;
  }

  if (!key) {
    const { checks } = await api("GET", "checks");
    body.append(checks.length ? h("div", { class: "table-wrap" }, h("table", {},
      h("tr", {}, h("th", {}, "Projekt"), h("th", {}, "Asana"), h("th", {}, "Sprawdzono"), h("th", { class: "num" }, "Zadań"), h("th", { class: "num" }, "Różnice statusu"), h("th", { class: "num" }, "Różnice przypisania"), h("th", {}, "Uwagi")),
      checks.sort((a, b) => b.checked_at - a.checked_at).map(c => h("tr", { class: "clickable", onclick: () => go("#/check/" + c.jira_key) },
        h("td", { class: "mono" }, c.jira_key), h("td", {}, c.asana_name || "—"), h("td", {}, fmtTime(c.checked_at)),
        h("td", { class: "num" }, (c.counts || {}).tasks ?? "—"),
        h("td", { class: "num" }, numPill((c.counts || {}).status_bad)), h("td", { class: "num" }, numPill((c.counts || {}).assignee_bad)),
        h("td", {}, c.errors && c.errors.length ? pill("błąd", "bad") : c.warnings && c.warnings.length ? pill(`${c.warnings.length} ostrzeżeń`, "warn") : pill("OK", "ok"))))))
      : h("div", { class: "card muted" }, "Brak wyników. Podaj klucz projektu i kliknij „Sprawdź teraz” (albo zaznacz projekty na liście Projekty)."));
    return;
  }

  let res;
  try { res = await api("GET", "checks/" + key); } catch (e) {
    body.append(h("div", { class: "card" }, h("p", {}, `Projekt ${key} nie był jeszcze sprawdzany.`), h("button", { class: "primary", onclick: () => runCheck([key]) }, "Sprawdź " + key)));
    return;
  }
  body.append(checkResult(res));
};

const numPill = n => n === undefined || n === null ? "—" : n ? pill(n, "warn") : pill("0", "ok");

function checkResult(res) {
  const wrap = h("div");
  const c = res.counts || {};
  wrap.append(h("div", { class: "row", style: "margin-bottom:12px" },
    h("h2", { style: "margin:0" }, jiraLink(res.jira_key), " · ", res.asana_name || ""),
    res.asana_link ? h("a", { href: res.asana_link, target: "_blank", rel: "noopener" }, "Asana ↗") : null,
    h("span", { class: "spacer" }), h("small", {}, "Sprawdzono " + fmtTime(res.checked_at)),
    h("button", { class: "small", onclick: () => runCheck([res.jira_key]) }, "Sprawdź ponownie")));

  if (res.errors && res.errors.length) wrap.append(h("div", { class: "notice bad" }, h("strong", {}, "Błędy"), h("ul", {}, res.errors.map(e => h("li", {}, e)))));
  if (res.warnings && res.warnings.length) wrap.append(h("div", { class: "notice warn" }, h("ul", {}, res.warnings.map(e => h("li", {}, e)))));
  if (!res.sections) return wrap;

  const stat = (value, label, kind) => h("div", { class: "card stat" }, h("div", { class: "value", style: kind ? `color:var(--${kind})` : "" }, value), h("div", { class: "label" }, label));
  wrap.append(h("div", { class: "grid cols-4" },
    stat(c.tasks, "zadań w projekcie Asany"),
    stat(`${c.migrated}`, `zmigrowanych · ${c.not_migrated} jeszcze nie`),
    stat(c.status_bad, `różnic statusu · ${c.status_ok} zgodnych`, c.status_bad ? "warn" : "ok"),
    stat(c.assignee_bad, `różnic przypisania · ${c.assignee_ok} zgodnych`, c.assignee_bad ? "warn" : "ok")));
  wrap.append(h("p", { class: "muted", style: "margin-top:8px" },
    `Pominięte: ${c.jira_master} prowadzonych w Jirze („migriert zu Jira”), ${c.skipped} z sekcji „pomiń”, ${c.status_unmapped} bez mapowania statusu, ${c.missing_in_jira} usuniętych w Jirze. Nieprzypisanych w Asanie: ${res.unassigned_tasks}.`));

  const tabs = [["sections", `Sekcje (${res.sections.length})`], ["people", `Osoby (${res.people.length})`], ["diff", `Różnice (${res.mismatches.length})`]];
  const tabBar = h("div", { class: "tabs" });
  const panel = h("div");
  const show = id => {
    tabBar.querySelectorAll("button").forEach(b => b.classList.toggle("active", b.dataset.id === id));
    panel.replaceChildren(id === "sections" ? sectionsTable(res) : id === "people" ? peopleTable(res) : diffTable(res));
  };
  tabs.forEach(([id, label]) => tabBar.append(h("button", { "data-id": id, onclick: () => show(id) }, label)));
  wrap.append(h("div", { class: "card", style: "margin-top:14px" }, tabBar, panel));
  show(res.mismatches.length ? "diff" : "sections");
  return wrap;
}

function sectionsTable(res) {
  const d = res.done_status;
  const statusOpts = ["", ...res.jira_statuses];
  const info = h("div", { class: "notice" + (d.exists ? "" : " warn") },
    `Ukończone w Asanie → „${d.name}” (${d.source})`, d.exists ? " ✓" : " - takiego statusu nie ma w projekcie!",
    res.map.profile ? ` · mapa projektu korzysta z szablonu „${res.map.profile}”` : res.map.project_file ? " · projekt ma własną mapę" : " · projekt używa tylko mapy głównej");
  const rows = res.sections.map(s => {
    let statusCell;
    if (s.skip) statusCell = pill("pomijana (nie migruje)", "");
    else if (!s.status) statusCell = pill("brak mapowania", (s.open || s.completed) ? "warn" : "");
    else statusCell = pill(s.status, s.status_exists ? "ok" : "bad");
    const extras = [s.labels.length ? `etykiety: ${s.labels.join(", ")}` : "", s.components.length ? `komponenty: ${s.components.join(", ")}` : "",
      s.resolution ? `rozwiązanie: ${s.resolution}` : "", s.over_completed ? "ważniejsza niż odhaczenie" : ""].filter(Boolean).join(" · ");
    const sel = h("select", { style: "max-width:200px" }, statusOpts.map(o => h("option", { value: o, selected: o === (s.status || "") && s.source === "projekt" }, o || "(usuń wpis projektu)")));
    const save = h("button", { class: "small", onclick: async () => {
      try {
        await api("POST", "status-map/" + res.jira_key, { section: s.name, value: sel.value || null });
        toast(`Zapisano w section_status_map.${res.jira_key}.json. Sprawdź ponownie, żeby zobaczyć efekt.`);
        save.textContent = "Zapisano ✓";
      } catch (e) { toast(e.message, true); }
    } }, "Zapisz");
    return h("tr", {},
      h("td", {}, s.name, extras ? h("small", { style: "display:block" }, extras) : null),
      h("td", { class: "num" }, s.open), h("td", { class: "num" }, s.completed),
      h("td", {}, statusCell), h("td", {}, s.source ? h("small", {}, s.source) : h("span", { class: "muted" }, "—")),
      h("td", {}, h("div", { class: "row", style: "flex-wrap:nowrap" }, sel, save)));
  });
  return h("div", {}, info,
    h("div", { class: "table-wrap" }, h("table", {},
      h("tr", {}, h("th", {}, "Sekcja Asany"), h("th", { class: "num" }, "Otwarte"), h("th", { class: "num" }, "Ukończone"), h("th", {}, "Status w Jirze"), h("th", {}, "Źródło wpisu"), h("th", {}, "Mapa projektu")),
      rows)),
    h("p", { class: "muted", style: "margin-top:8px" }, "„Zapisz” zmienia tylko mapę tego projektu (section_status_map.<KLUCZ>.json, z kopią w migration_app/backups/). Mapa główna i szablony - w Konfiguracji. Rozszerzone wpisy (etykiety, rozwiązanie, pomiń) edytuj w pliku."),
    h("div", { class: "row" },
      h("button", { class: "small", onclick: () => go(`#/config/files/section_status_map.${res.jira_key}.json`) }, "Edytuj mapę projektu"),
      h("button", { class: "small", onclick: () => openTool("asana_jira_sync", { "--project-keys": res.jira_key, "--dry-run": true }) }, "Synchronizacja tego projektu (podgląd)…")));
}

function peopleTable(res) {
  return h("div", {},
    h("div", { class: "table-wrap" }, h("table", {},
      h("tr", {}, h("th", {}, "Osoba w Asanie"), h("th", { class: "num" }, "Zadań"), h("th", { class: "num" }, "Otwartych"), h("th", {}, "Konto w Jirze (user_map.json)"), h("th", {}, "Stan")),
      res.people.map(p => h("tr", {},
        h("td", {}, p.name || "—", h("small", { style: "display:block" }, [p.email, p.gid].filter(Boolean).join(" · "))),
        h("td", { class: "num" }, p.tasks), h("td", { class: "num" }, p.open),
        h("td", {}, p.jira_account_id ? [p.jira_name || "?", h("small", { class: "mono", style: "display:block" }, p.jira_account_id)] : h("span", { class: "muted" }, "—")),
        h("td", {}, p.problem ? pill(p.problem, "bad") : pill("OK", "ok")))))),
    h("div", { class: "row", style: "margin-top:10px" },
      h("button", { class: "small", onclick: () => go("#/config/files/user_map.json") }, "Edytuj user_map.json"),
      h("button", { class: "small", onclick: () => go("#/tools/user_map") }, "Generuj user_map (po e-mailu)…"),
      h("button", { class: "small", onclick: () => go("#/tools/asana_members") }, "Lista osób w Asanie…")));
}

function diffTable(res) {
  if (!res.mismatches.length) return h("div", { class: "notice ok" }, "Wszystkie zmigrowane zadania mają w Jirze oczekiwany status i przypisanego.");
  let kind = "", q = "";
  const tbody = h("tbody");
  const draw = () => tbody.replaceChildren(...res.mismatches
    .filter(m => (!kind || m.kind === kind) && (!q || `${m.jira_key} ${m.title} ${m.section}`.toLowerCase().includes(q)))
    .map(m => h("tr", {},
      h("td", {}, jiraLink(m.jira_key)), h("td", {}, m.title, m.completed ? h("span", {}, " ", pill("ukończone")) : null),
      h("td", {}, h("small", {}, m.section || "—")), h("td", {}, pill(m.kind, m.kind === "brak w Jirze" ? "bad" : "warn")),
      h("td", {}, m.expected || "—"), h("td", {}, m.actual || "—"))));
  const kinds = [...new Set(res.mismatches.map(m => m.kind))];
  const out = h("div", {},
    h("div", { class: "row", style: "margin-bottom:10px" },
      h("select", { style: "max-width:200px", onchange: e => { kind = e.target.value; draw(); } }, h("option", { value: "" }, "Wszystkie rodzaje"), kinds.map(k => h("option", { value: k }, `${k} (${res.mismatches.filter(m => m.kind === k).length})`))),
      h("input", { type: "search", placeholder: "Szukaj…", style: "max-width:260px", oninput: e => { q = e.target.value.toLowerCase(); draw(); } }),
      h("span", { class: "spacer" }),
      h("button", { class: "small", onclick: () => openTool("asana_jira_sync", { "--project-keys": res.jira_key, "--dry-run": true }) }, "Synchronizacja (podgląd)…"),
      h("button", { class: "small", onclick: () => openTool("compare_totals", { pos0: res.jira_key, "--details": true }) }, "Pełne porównanie…")),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Zgłoszenie"), h("th", {}, "Zadanie"), h("th", {}, "Sekcja Asany"), h("th", {}, "Rodzaj"), h("th", {}, "Oczekiwane (wg Asany)"), h("th", {}, "W Jirze"))),
      tbody)));
  draw();
  return out;
}

// ------------------------------------------------------------------ Konfiguracja

VIEWS.config = async (main, tab, ...rest) => {
  tab = tab || "env";
  const tabBar = h("div", { class: "tabs" },
    h("button", { class: tab === "env" ? "active" : "", onclick: () => go("#/config/env") }, "Zmienne .env"),
    h("button", { class: tab === "files" ? "active" : "", onclick: () => go("#/config/files") }, "Pliki: mapy, listy, schematy"));
  const body = h("div");
  main.replaceChildren(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Konfiguracja"), h("small", {}, "Przed każdym zapisem kopia trafia do migration_app/backups/"))), tabBar, body);
  if (tab === "env") return envEditor(body);
  return filesEditor(body, rest.join("/"));
};

async function envEditor(body) {
  const env = await api("GET", "env");
  const changed = {};
  const rows = env.entries.map(e => {
    const input = h("input", { type: e.secret ? "password" : "text", value: e.secret ? "" : e.value,
      placeholder: e.secret ? (e.set ? `ustawione (${e.value}) - wpisz, aby zmienić` : "nieustawione") : "",
      oninput: ev => { changed[e.key] = ev.target.value; } });
    return h("div", { class: "env-row" },
      h("div", { class: "k" }, e.key, " ", e.set ? null : pill("puste", "")),
      h("div", {}, input, e.help ? h("small", { class: "muted" }, e.help) : null));
  });
  const keyIn = h("input", { type: "text", placeholder: "NOWA_ZMIENNA" });
  const valIn = h("input", { type: "text", placeholder: "wartość" });
  body.append(
    env.exists ? null : h("div", { class: "notice warn" }, "Pliku .env jeszcze nie ma - zostanie utworzony przy zapisie."),
    h("div", { class: "card" }, rows,
      h("div", { class: "env-row" }, keyIn, valIn),
      h("div", { class: "row end", style: "margin-top:12px" }, h("button", { class: "primary", onclick: async () => {
        if (keyIn.value.trim()) changed[keyIn.value.trim()] = valIn.value;
        if (!Object.keys(changed).length) return toast("Nic nie zmieniono.");
        try { const r = await api("PUT", "env", { values: changed }); toast("Zapisano: " + r.saved.join(", ")); route(); } catch (e) { toast(e.message, true); }
      } }, "Zapisz .env"))));
}

async function filesEditor(body, path) {
  const { files } = await api("GET", "files");
  const list = h("div", { class: "tool-list" },
    h("div", { class: "tool-group" }, "Pliki"),
    files.map(f => h("div", { class: "tool-item" + (f.path === path ? " active" : ""), onclick: () => go("#/config/files/" + f.path) }, h("span", { class: "mono", style: "font-size:12.5px" }, f.path))),
    h("div", { class: "tool-group" }, "Nowa mapa projektu"),
    (() => {
      const k = h("input", { type: "text", placeholder: "KLUCZ", list: "project-keys" });
      return h("div", { class: "row", style: "padding:4px 8px" }, projectDatalist(), k,
        h("button", { class: "small", onclick: () => k.value.trim() && go(`#/config/files/section_status_map.${k.value.trim().toUpperCase()}.json`) }, "Otwórz"));
    })());
  const editorBox = h("div");
  body.append(h("div", { class: "tools-layout" }, list, editorBox));
  if (!path) { editorBox.append(h("div", { class: "card muted" }, "Wybierz plik z listy.")); return; }

  const f = await api("GET", "files?path=" + encodeURIComponent(path));
  const isJson = path.endsWith(".json");
  const isNewMap = !f.content && /^section_status_map\..+\.json$/.test(path);
  const ta = h("textarea", { class: "editor", spellcheck: false }, f.content || (isNewMap ? '{\n  "__ukonczone__": "Done"\n}\n' : ""));
  const status = h("small");
  const validate = () => {
    if (!isJson) return true;
    try { JSON.parse(ta.value); status.textContent = "JSON poprawny"; status.style.color = "var(--ok)"; return true; } catch (e) { status.textContent = "Błąd JSON: " + e.message; status.style.color = "var(--bad)"; return false; }
  };
  ta.addEventListener("input", validate);
  ta.addEventListener("keydown", e => { if (e.key === "Tab") { e.preventDefault(); ta.setRangeText("  ", ta.selectionStart, ta.selectionEnd, "end"); } });
  editorBox.append(h("div", { class: "card" },
    h("div", { class: "row", style: "margin-bottom:8px" }, h("h2", { class: "mono", style: "margin:0" }, path), isNewMap ? pill("nowy plik", "info") : null, h("span", { class: "spacer" }), status),
    path === "sync_exclude.txt" || path === "protected_projects.txt" ? h("p", { class: "muted" }, "Jeden klucz w linii, # = komentarz.") : null,
    /section_status_map\..+\.json$/.test(path) ? h("p", { class: "muted" }, 'Wpisy: "Sekcja": "Status" albo {"status", "labels", "components", "resolution", "nad_ukonczonym", "pomin"}. "__ukonczone__" = status dla ukończonych, "__szablon__" = nazwa szablonu z section_status_profiles/.') : null,
    ta,
    h("div", { class: "row end", style: "margin-top:10px" },
      isJson ? h("button", { onclick: () => { try { ta.value = JSON.stringify(JSON.parse(ta.value), null, 2) + "\n"; validate(); } catch (e) { validate(); } } }, "Formatuj JSON") : null,
      h("button", { class: "primary", onclick: async () => {
        if (!validate()) return toast("Popraw JSON przed zapisem.", true);
        try { await api("PUT", "files", { path, content: ta.value }); toast("Zapisano " + path); } catch (e) { toast(e.message, true); }
      } }, "Zapisz"))));
  validate();
}

// ------------------------------------------------------------------ Raporty

VIEWS.reports = async (main, ...pathParts) => {
  const path = pathParts.join("/");
  const { reports } = await api("GET", "reports");
  let q = "";
  const tbody = h("tbody");
  const draw = () => tbody.replaceChildren(...reports.filter(r => !q || r.path.toLowerCase().includes(q)).slice(0, 400).map(r =>
    h("tr", { class: "clickable" + (r.path === path ? " active" : ""), onclick: () => go("#/reports/" + r.path) },
      h("td", { class: "mono", style: "font-size:12.5px" }, r.path), h("td", { class: "num" }, fmtSize(r.size)), h("td", {}, fmtTime(r.mtime)))));
  const viewer = h("div");
  main.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Raporty i logi"), h("small", {}, "logs/, pipeline_summaries/, repair_reports/, raporty CSV/JSON"))),
    h("div", { class: "tools-layout", style: "grid-template-columns: minmax(300px, 420px) 1fr" },
      h("div", {},
        h("input", { type: "search", placeholder: "Filtruj…", style: "margin-bottom:8px", oninput: e => { q = e.target.value.toLowerCase(); draw(); } }),
        h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Plik"), h("th", { class: "num" }, "Rozmiar"), h("th", {}, "Zmieniony"))), tbody))),
      viewer));
  draw();
  if (!path) { viewer.append(h("div", { class: "card muted" }, "Wybierz plik.")); return; }
  const r = await api("GET", "reports?path=" + encodeURIComponent(path));
  let content;
  if (path.endsWith(".csv")) content = csvTable(r.content);
  else if (path.endsWith(".json")) { try { content = h("pre", { class: "log" }, JSON.stringify(JSON.parse(r.content), null, 2)); } catch (e) { content = h("pre", { class: "log" }, r.content); } }
  else { content = h("div", { class: "log" }); r.content.split("\n").forEach(l => content.append(colorize(l))); }
  viewer.append(h("div", { class: "card" },
    h("div", { class: "row", style: "margin-bottom:8px" }, h("h2", { class: "mono", style: "margin:0" }, path), h("span", { class: "spacer" }), h("small", {}, fmtSize(r.size), r.truncated ? " · pokazano koniec pliku" : "")),
    content));
};

function parseCsv(text) {
  const firstLine = text.split("\n", 1)[0];
  const delim = (firstLine.match(/;/g) || []).length > (firstLine.match(/,/g) || []).length ? ";" : firstLine.includes("\t") && !firstLine.includes(",") ? "\t" : ",";
  const rows = [];
  let row = [], cell = "", inQ = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQ) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') inQ = false;
      else cell += ch;
    } else if (ch === '"') inQ = true;
    else if (ch === delim) { row.push(cell); cell = ""; }
    else if (ch === "\n") { row.push(cell.replace(/\r$/, "")); rows.push(row); row = []; cell = ""; }
    else cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

function csvTable(text) {
  const rows = parseCsv(text.replace(/^﻿/, ""));
  if (!rows.length) return h("p", { class: "muted" }, "Pusty plik.");
  const [head, ...data] = rows;
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, head.map(c => h("th", {}, c)))),
    h("tbody", {}, data.slice(0, 3000).map(r => h("tr", {}, r.map(c => h("td", {}, c)))))),
    data.length > 3000 ? h("p", { class: "muted" }, `Pokazano 3000 z ${data.length} wierszy.`) : null);
}

// ------------------------------------------------------------------ start

renderSelection();
refreshBadge().then(route);
