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
  const [view, ...args] = (pathPart || "start").split("/").filter((x, i) => i === 0 || x).map(decodeURIComponent);
  S.viewToken++;
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  const main = $("#main");
  main.replaceChildren(h("p", { class: "muted" }, "Ładowanie…"));
  try {
    await (VIEWS[view] || VIEWS.start)(main, ...args);
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

// ------------------------------------------------------------------ Start (pierwsza migracja)

// Uruchamia narzędzie i pokazuje jego log w podanym kontenerze; onDone(run) po zakończeniu.
async function runInline(box, toolId, values, onDone) {
  await ensureCatalog();
  let run;
  try { run = await startTool(toolId, values); } catch (e) { toast(e.message, true); return; }
  if (!run) return;
  box.replaceChildren();
  mountLog(box, run.id, r => { if (r.status !== "running" && onDone) onDone(r); }).style.height = "28vh";
}

const START_STEPS = {
  env: ["Konfiguracja", "Dane dostępowe do Jiry, Asany i arkusza Google (zapisywane w .env, poza repo)."],
  check: ["Test połączeń", "Sprawdza konto Jiry i uprawnienia administratora, projekt i tablicę wzorcową, token Asany, dostęp do arkusza i jego kolumny."],
  sheet: ["Arkusz projektów", "Wczytuje listę projektów do migracji. Każdy wiersz potrzebuje asana_project_link."],
  people: ["Mapowanie osób", "Kto z Asany odpowiada komu w Jirze; osoby bez konta oznaczasz „nie w Jirze”."],
  statuses: ["Mapowanie statusów", "Która sekcja Asany trafia na który status Jiry i jaki status dostają zadania ukończone."],
  session: ["Sesja przeglądarki", "Kroki 2-3 (kolumny i statusy tablic) działają przez przeglądarkę - zaloguj się raz do Jiry (SSO/2FA)."],
  trial: ["Podgląd całej migracji", "Pipeline w trybie dry-run: pokazuje, co zostanie utworzone i zsynchronizowane, nic nie zapisując."],
  migrate: ["Migracja", "Pełny pipeline. Zalecane: najpierw zakładka arkusza z 1-2 projektami testowymi (GOOGLE_SHEET_RANGE)."],
  verify: ["Weryfikacja", "Zgodność statusów, przypisań i liczby zadań z Asaną."],
};

VIEWS.start = async main => {
  const st = await api("GET", "setup");
  const done = st.steps.filter(s => s.done).length;
  const card = (step, i, ...content) => h("div", { class: "card", style: step.done ? "" : (st.steps.slice(0, i).every(s => s.done) ? "border-color:var(--accent)" : "") },
    h("div", { class: "row" },
      h("span", { class: "pill " + (step.done ? "ok" : "") , style: "min-width:26px;text-align:center" }, step.done ? "✓" : String(i + 1)),
      h("h2", { style: "margin:0" }, START_STEPS[step.id][0]), h("span", { class: "spacer" }), h("small", {}, step.detail)),
    h("p", { class: "muted", style: "margin:8px 0" }, START_STEPS[step.id][1]),
    ...content);
  const S_ = Object.fromEntries(st.steps.map((s, i) => [s.id, [s, i]]));
  const chk = st.check || {};

  main.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Start migracji"), h("small", {}, `Ukończone kroki: ${done} z ${st.steps.length}. Kolejność ma znaczenie - każdy krok korzysta z poprzednich.`))),
    card(...S_.env, envForm(st)),
    card(...S_.check, checkResults(chk, st.check_fresh)),
    card(...S_.sheet, (() => {
      const box = h("div");
      return h("div", {}, h("div", { class: "row" },
        h("button", { class: S_.sheet[0].done ? "" : "primary", onclick: () => runInline(box, "sheet_load", {}, () => route()) }, S_.sheet[0].done ? "Wczytaj ponownie" : "Wczytaj arkusz"),
        h("button", { onclick: () => go("#/projects") }, "Lista projektów")), box);
    })()),
    card(...S_.people, h("button", { class: S_.people[0].done ? "" : "primary", onclick: () => go("#/people") }, "Otwórz mapowanie osób")),
    card(...S_.statuses, h("button", { class: S_.statuses[0].done ? "" : "primary", onclick: () => go("#/statusmap") }, "Otwórz mapowanie statusów")),
    card(...S_.session, h("div", { class: "row" },
      h("button", { class: S_.session[0].done ? "" : "primary", onclick: async () => { const r = await startTool("save_login", {}).catch(e => toast(e.message, true)); if (r) go("#/runs/" + r.id); } }, "Otwórz przeglądarkę do logowania"),
      h("small", {}, "Po zalogowaniu wróć do panelu i wyślij Enter w polu odpowiedzi uruchomienia."))),
    card(...S_.trial, h("button", { class: S_.trial[0].done ? "" : "primary", onclick: async () => {
      const r = await startTool("full_pipeline", { "--dry-run": true }).catch(e => toast(e.message, true)); if (r) go("#/runs/" + r.id);
    } }, "Uruchom podgląd (dry-run)")),
    card(...S_.migrate, h("div", { class: "row" },
      h("button", { class: S_.migrate[0].done ? "" : "primary", onclick: () => openTool("full_pipeline", { "--dry-run": false, "--verify": true }) }, "Pełny pipeline…"),
      h("small", {}, "Panel pokaże polecenie i poprosi o potwierdzenie przed zapisem."))),
    card(...S_.verify, h("div", { class: "row" },
      h("button", { onclick: () => go("#/check") }, "Statusy i osoby"),
      h("button", { onclick: () => openTool("compare_totals", { "--all": true }) }, "Porównanie liczby zadań…"))),
  );
};

function envForm(st) {
  const chk = st.check || {};
  const vals = {};
  const field = (key, label, opts = {}) => {
    const secret = opts.secret;
    const input = h("input", { type: secret ? "password" : "text", value: secret ? "" : (st.env[key] || ""),
      placeholder: secret ? (st.secrets_set[key] ? "ustawiony - wpisz, aby zmienić" : "") : (opts.placeholder || ""),
      oninput: e => { vals[key] = e.target.value.trim(); } });
    const extra = opts.extra ? opts.extra(input) : null;
    return h("div", { class: "field" }, h("span", {}, label, opts.required ? " *" : "", h("span", { class: "mono" }, "  " + key)), input, opts.help ? h("small", { class: "muted" }, opts.help) : null, extra);
  };
  const ws = ((chk.asana || {}).data || {}).workspaces || [];
  const boards = (((chk.jira || {}).data || {}).template || {}).boards || [];
  const tabs = ((chk.google || {}).data || {}).tabs || [];
  const myId = ((chk.jira || {}).data || {}).account_id;
  const pick = (input, key, value) => { input.value = value; vals[key] = value; };

  const keyArea = h("textarea", { rows: 3, placeholder: "Wklej tutaj zawartość pliku JSON z kluczem konta serwisowego Google…" });
  const keyInfo = h("div", {}, st.google_email ? h("div", { class: "notice ok", style: "margin:6px 0 0" }, "Klucz zapisany. Udostępnij arkusz Google adresowi: ",
    h("strong", { class: "mono" }, st.google_email), " ", h("button", { class: "small", onclick: () => { navigator.clipboard.writeText(st.google_email); toast("Skopiowano adres."); } }, "Kopiuj")) : null);

  const saveBtn = h("button", { class: "primary", onclick: async () => {
    saveBtn.disabled = true; saveBtn.textContent = "Zapisuję i sprawdzam…";
    try {
      if (keyArea.value.trim()) await api("POST", "setup/google-key", { key: keyArea.value.trim() });
      if (vals.GOOGLE_SHEET_ID) { const m = vals.GOOGLE_SHEET_ID.match(/\/d\/([A-Za-z0-9_-]+)/); if (m) vals.GOOGLE_SHEET_ID = m[1]; }
      if (vals.JIRA_BASE_URL) vals.JIRA_BASE_URL = vals.JIRA_BASE_URL.replace(/\/+$/, "").replace(/\/(jira|secure|browse).*$/, "");
      const changed = Object.fromEntries(Object.entries(vals).filter(([k, v]) => v !== (st.env[k] || "") && !(k.endsWith("TOKEN") && !v)));
      if (Object.keys(changed).length) await api("PUT", "env", { values: changed });
      await api("POST", "setup/check");
      route();
    } catch (e) { toast(e.message, true); saveBtn.disabled = false; saveBtn.textContent = "Zapisz i sprawdź połączenia"; }
  } }, "Zapisz i sprawdź połączenia");

  return h("div", {},
    h("div", { class: "form-grid" },
      field("JIRA_BASE_URL", "Adres Jiry", { required: true, placeholder: "https://firma.atlassian.net" }),
      field("JIRA_EMAIL", "E-mail konta Jiry (administrator)", { required: true }),
      field("JIRA_API_TOKEN", "Token API Jiry", { required: true, secret: true, help: "id.atlassian.com → Security → API tokens" }),
      field("ASANA_TOKEN", "Token Asany", { required: true, secret: true, help: "app.asana.com/0/my-apps → Personal access token" }),
      field("ASANA_WORKSPACE_GID", "Workspace Asany", { required: true, help: ws.length ? null : "Lista pojawi się po teście połączenia z tokenem.",
        extra: inp => ws.length ? h("div", { class: "row" }, ws.map(w => h("button", { class: "small", type: "button", onclick: () => pick(inp, "ASANA_WORKSPACE_GID", w.gid) }, w.name))) : null }),
      field("GOOGLE_SHEET_ID", "Arkusz Google (adres albo ID)", { required: true, placeholder: "https://docs.google.com/spreadsheets/d/…" }),
      field("GOOGLE_SHEET_RANGE", "Zakładka i kolumny", { placeholder: "Arkusz1!A:F",
        extra: inp => tabs.length ? h("div", { class: "row" }, tabs.slice(0, 8).map(t => h("button", { class: "small", type: "button", onclick: () => pick(inp, "GOOGLE_SHEET_RANGE", `${t}!A:J`) }, t))) : null }),
      field("JIRA_TEMPLATE_PROJECT_KEY", "Projekt wzorcowy (klucz)", { help: "Z niego nowe projekty dostają workflow, ekrany, pola i uprawnienia." }),
      field("JIRA_TEMPLATE_BOARD_ID", "Tablica wzorcowa (ID)", {
        extra: inp => boards.length ? h("div", { class: "row" }, boards.map(b => h("button", { class: "small", type: "button", onclick: () => pick(inp, "JIRA_TEMPLATE_BOARD_ID", String(b.id)) }, `${b.id} ${b.name}`))) : null }),
      field("DEFAULT_LEAD_ACCOUNT_ID", "Domyślny lead projektów (accountId)", {
        extra: inp => myId ? h("button", { class: "link small", type: "button", onclick: () => pick(inp, "DEFAULT_LEAD_ACCOUNT_ID", myId) }, "użyj mojego konta") : null })),
    h("div", { class: "field", style: "margin-top:12px" }, h("span", {}, "Klucz konta serwisowego Google *", h("span", { class: "mono" }, "  → service_account.json")),
      keyArea, h("small", { class: "muted" }, "Google Cloud Console → IAM → Service Accounts → Keys → Add key (JSON). Plik zostaje lokalnie (jest w .gitignore)."), keyInfo),
    h("div", { class: "row end", style: "margin-top:12px" },
      h("button", { class: "ghost", onclick: () => go("#/config/env") }, "Wszystkie zmienne…"), saveBtn));
}

function checkResults(chk, fresh) {
  const names = { jira: "Jira", asana: "Asana", google: "Google Sheets" };
  const btn = h("button", { onclick: async () => { btn.disabled = true; btn.textContent = "Sprawdzam…"; try { await api("POST", "setup/check"); route(); } catch (e) { toast(e.message, true); btn.disabled = false; btn.textContent = "Sprawdź połączenia"; } } }, "Sprawdź połączenia");
  if (!chk.checked_at) return h("div", { class: "row" }, btn);
  return h("div", {},
    h("div", { class: "grid cols-2", style: "grid-template-columns:repeat(auto-fit,minmax(260px,1fr))" },
      ["jira", "asana", "google"].map(k => {
        const r = chk[k];
        return h("div", { class: "notice " + (r.ok ? (r.warn ? "warn" : "ok") : "bad"), style: "margin:0" },
          h("strong", {}, names[k], r.ok ? (r.warn ? " - działa, ale są uwagi" : " ✓") : " - błąd"),
          h("ul", {}, r.messages.map(m => h("li", {}, m))));
      })),
    h("div", { class: "row", style: "margin-top:10px" }, h("small", {}, "Sprawdzono " + fmtTime(chk.checked_at) + (fresh ? "" : " - przed ostatnią zmianą .env")), h("span", { class: "spacer" }), btn));
}

// ------------------------------------------------------------------ Mapowanie statusów

// Propozycje biorą WYŁĄCZNIE statusy projektu wzorcowego: najpierw ta sama nazwa, potem słowa
// kluczowe sekcji (DE/EN/PL) → status wzorca z tej samej kategorii (do zrobienia / w toku / zakończone).
const STATUS_HINTS = [
  // Kolejność słów = priorytet przy wyborze statusu wzorca (np. „Done” przed „Closed”).
  { cat: "done", words: ["done", "fertig", "erledigt", "abgeschlossen", "completed", "complete", "resolved", "zrobione", "gotowe", "zakonczone", "closed"] },
  { cat: "indeterminate", words: ["in arbeit", "in progress", "doing", "wip", "in bearbeitung", "umsetzung", "w trakcie", "w realizacji"] },
  { cat: "new", words: ["to do", "todo", "new", "backlog", "open", "offen", "neu", "to dos", "inbox", "ideen", "ideas", "eingang", "no section", "untitled section", "do zrobienia"] },
];
const normS = s => (s || "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

// Status wzorca reprezentujący kategorię: taki, którego nazwa sama jest słowem kluczowym
// tej kategorii (np. „Done”, „To Do”), a gdy brak - pierwszy status tej kategorii we wzorcu.
function categoryStatus(cat, statuses) {
  const hint = STATUS_HINTS.find(x => x.cat === cat);
  const ofCat = statuses.filter(s => s.category === cat);
  for (const w of hint.words) { const hit = ofCat.find(s => normS(s.name) === w); if (hit) return hit; }
  return ofCat[0] || null;
}

function suggestStatus(section, statuses) {
  const n = normS(section);
  const exact = statuses.find(s => normS(s.name) === n);
  if (exact) return { status: exact.name, reason: "ta sama nazwa" };
  const padded = ` ${n} `;
  for (const hint of STATUS_HINTS) {
    if (hint.words.some(w => padded.includes(` ${w} `))) {
      const st = categoryStatus(hint.cat, statuses);
      if (st) return { status: st.name, reason: { done: "zakończone", indeterminate: "w toku", new: "do zrobienia" }[hint.cat] };
    }
  }
  return null;
}

VIEWS.statusmap = async main => {
  let data = await api("GET", "statusmap");
  const loadBox = h("div");
  const projIn = h("input", { type: "text", placeholder: "domyślnie projekt wzorcowy", style: "max-width:220px" });
  const loadBtn = h("button", { class: data.source ? "" : "primary", onclick: () => runInline(loadBox, "status_source", { "--project": projIn.value.trim().toUpperCase() }, () => route()) },
    data.source ? "Wczytaj ponownie" : "Wczytaj sekcje i statusy");
  const body = h("div");
  main.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Mapowanie statusów"),
      h("small", {}, "Sekcje Asany ze wszystkich projektów arkusza → statusy Jiry. Wynik: section_status_map.json (mapa główna; mapy pojedynczych projektów - w „Statusy i osoby”)."))),
    h("div", { class: "card" }, h("h2", {}, "1. Źródło"),
      data.source ? h("p", {}, `Statusy z projektu ${data.source.status_project} (${data.source.statuses.length}), sekcje z ${data.source.projects_scanned} projektów (${data.source.sections.length} różnych nazw). Wczytano ${fmtTime(data.source.loaded_at)}.`,
        data.source.errors.length ? h("span", { style: "color:var(--warn)" }, ` Błędy w ${data.source.errors.length} projektach.`) : null) :
        h("p", { class: "muted" }, "Wymaga wczytanego arkusza (Start → krok 3). Przy kilkuset projektach trwa 2-3 minuty."),
      h("div", { class: "row" }, h("span", {}, "Projekt ze statusami:"), projIn, h("span", { class: "spacer" }), loadBtn), loadBox),
    body);
  if (!data.source) return;

  const src = data.source;
  const statuses = src.statuses;
  const sug = Object.fromEntries(src.sections.map(s => [s.name, suggestStatus(s.name, statuses)]));
  let work = {};
  const SKIP = "__pomin__";
  const valueOf = v => v && typeof v === "object" ? (v.pomin ? SKIP : (v.status || "")) : (Array.isArray(v) ? v[0] : (v || ""));
  const init = mode => {
    work = {};
    [...src.sections.map(s => s.name), "__ukonczone__"].forEach(n => { work[n] = mode === "current" ? valueOf(data.map[n]) : ""; });
    // Propozycja dla ukończonych: status „zakończony” wzorca. Zapisywana jawnie - bez wpisu skrypt
    // synchronizacji użyłby „Fertig”, którego w innej Jirze może nie być.
    if (!work.__ukonczone__ || !statuses.some(s => s.name === work.__ukonczone__)) { const d = categoryStatus("done", statuses); work.__ukonczone__ = d ? d.name : ""; }
    keep.checked = mode === "current"; draw();
  };
  let filter = "todo", q = "", minProjects = 1;
  const tbody = h("tbody");
  const summary = h("div", { class: "row", style: "margin:10px 0" });
  const keep = h("input", { type: "checkbox", checked: true });
  const doneSel = h("select", { style: "max-width:240px", onchange: e => { work.__ukonczone__ = e.target.value; } });
  const known = new Set(statuses.map(x => x.name));
  const invalid = v => v && v !== SKIP && !known.has(v);
  const opts = cur => [h("option", { value: "" }, "(bez mapowania - status startowy)"),
    invalid(cur) ? h("option", { value: cur, selected: true }, `⚠ ${cur} - nie ma tego statusu we wzorcu`) : null,
    ...["new", "indeterminate", "done"].map(cat => h("optgroup", { label: { new: "Do zrobienia", indeterminate: "W toku", done: "Zakończone" }[cat] },
      statuses.filter(s => s.category === cat).map(s => h("option", { value: s.name, selected: s.name === cur }, s.name)))),
    h("option", { value: SKIP, selected: cur === SKIP }, "— pomiń: nie migruj zadań z tej sekcji —")];

  function draw() {
    doneSel.replaceChildren(...statuses.map(s => h("option", { value: s.name, selected: s.name === work.__ukonczone__ }, `${s.name}${s.category === "done" ? "" : " (nie jest „zakończony”)"}`)));
    if (!work.__ukonczone__) doneSel.prepend(h("option", { value: "", selected: true }, "(wybierz status)"));
    const secs = src.sections.filter(s => s.projects.length >= minProjects);
    const cnt = f => secs.filter(s => f(work[s.name])).length;
    summary.replaceChildren(pill(`${cnt(v => v && v !== SKIP)} zmapowanych`, "ok"), pill(`${cnt(v => v === SKIP)} pomijanych`, "info"),
      pill(`${cnt(v => !v)} bez mapowania`, cnt(v => !v) ? "warn" : "ok"),
      cnt(invalid) ? pill(`${cnt(invalid)} ze statusem spoza wzorca`, "bad") : null, h("small", {}, `z ${secs.length} sekcji (min. ${minProjects} projektów)`));
    const rows = secs.filter(s => {
      const v = work[s.name];
      if (q && !normS(s.name).includes(normS(q))) return false;
      return filter === "all" || (filter === "invalid" && invalid(v)) || (filter === "todo" && !v) || (filter === "mapped" && v && v !== SKIP) || (filter === "skip" && v === SKIP) || (filter === "suggested" && !v && sug[s.name]);
    });
    tbody.replaceChildren(...rows.slice(0, 400).map(s => {
      const g = sug[s.name];
      const sel = h("select", { style: "max-width:260px", onchange: e => { work[s.name] = e.target.value; draw(); } }, opts(work[s.name]));
      return h("tr", {},
        h("td", {}, s.name), h("td", { class: "num", title: s.projects.slice(0, 30).join(", ") }, s.projects.length), h("td", { class: "num" }, s.tasks),
        h("td", {}, sel),
        h("td", {}, g && g.status !== work[s.name] ? h("button", { class: "small", title: g.reason, onclick: () => { work[s.name] = g.status; draw(); } }, "← " + g.status, h("small", { class: "muted" }, ` (${g.reason})`)) : h("span", { class: "muted" }, g ? "✓" : "—")));
    }), rows.length > 400 ? h("tr", {}, h("td", { colspan: 5, class: "muted" }, `Pokazano 400 z ${rows.length} - zawęź filtr albo podnieś próg projektów.`)) : null);
  }

  const save = async () => {
    if (!work.__ukonczone__) return toast("Wybierz status dla zadań ukończonych w Asanie.", true);
    const bad = Object.entries(work).filter(([, v]) => invalid(v));
    if (bad.length && !confirm(`${bad.length} wpisów wskazuje status, którego nie ma w projekcie ${src.status_project} (np. „${bad[0][1]}”). Synchronizacja zostawi takie zadania w statusie startowym. Zapisać mimo to?`)) return;
    const entries = {};
    Object.entries(work).forEach(([k, v]) => { if (v === SKIP) entries[k] = { pomin: true }; else if (v) entries[k] = v; });
    if (!confirm(`Zapisać ${Object.keys(entries).length} wpisów do section_status_map.json?` + (keep.checked ? "" : "\nWpisy sekcji spoza wczytanej listy zostaną usunięte."))) return;
    try {
      const r = await api("PUT", "statusmap", { entries, keep_unlisted: keep.checked });
      toast(`Zapisano: ${r.entries} wpisów (+${r.added}, −${r.removed}, zmienionych ${r.changed}).`);
      data = await api("GET", "statusmap");
    } catch (e) { toast(e.message, true); }
  };

  body.replaceChildren(h("div", { class: "card" },
    h("h2", {}, "2. Przypisz statusy"),
    h("div", { class: "row" }, h("span", {}, "Start:"),
      h("label", { class: "check" }, h("input", { type: "radio", name: "smode", checked: true, onchange: () => init("current") }), `od obecnej mapy (${Object.keys(data.map).length} wpisów)`),
      h("label", { class: "check" }, h("input", { type: "radio", name: "smode", onchange: () => init("fresh") }), "od pustej mapy (świeża migracja)")),
    h("div", { class: "notice", style: "margin-top:10px" }, h("div", { class: "row" }, h("strong", {}, "Zadania ukończone w Asanie →"), doneSel,
      h("small", {}, "Ma pierwszeństwo przed sekcją (wyjątki ustawisz w mapie projektu: „nad_ukonczonym”)."))),
    summary,
    h("div", { class: "row", style: "margin-bottom:10px" },
      h("select", { style: "max-width:200px", onchange: e => { filter = e.target.value; draw(); } },
        [["todo", "Bez mapowania"], ["invalid", "Status spoza wzorca"], ["suggested", "Z propozycją"], ["mapped", "Zmapowane"], ["skip", "Pomijane"], ["all", "Wszystkie"]].map(([v, l]) => h("option", { value: v }, l))),
      h("input", { type: "search", placeholder: "Szukaj sekcji…", style: "max-width:220px", oninput: e => { q = e.target.value; draw(); } }),
      h("label", { class: "check" }, "min. projektów:", h("input", { type: "number", min: 1, value: 1, style: "width:70px", oninput: e => { minProjects = Math.max(1, +e.target.value || 1); draw(); } })),
      h("span", { class: "spacer" }),
      h("button", { onclick: () => { let n = 0; src.sections.forEach(s => { if (!work[s.name] && sug[s.name]) { work[s.name] = sug[s.name].status; n++; } }); toast(`Przyjęto ${n} propozycji.`); draw(); } }, "Przyjmij wszystkie propozycje")),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, "Sekcja Asany"), h("th", { class: "num" }, "Projektów"), h("th", { class: "num" }, "Zadań"), h("th", {}, "Status w Jirze"), h("th", {}, "Propozycja"))), tbody)),
    h("p", { class: "muted", style: "margin-top:8px" }, "Sekcja bez mapowania = zadania zostają w statusie startowym workflow (to nie błąd). „Pomiń” = zadania z tej sekcji, z podzadaniami, nie są migrowane."),
    h("div", { class: "row", style: "margin-top:12px" }, h("label", { class: "check" }, keep, "zachowaj wpisy sekcji spoza wczytanej listy"), h("span", { class: "spacer" }),
      h("button", { class: "primary", onclick: save }, "Zapisz mapowanie"))));
  init("current");
};

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
  main.replaceChildren(h("p", { class: "muted" }, "Wczytywanie arkusza Google… Pierwszy odczyt może potrwać kilka minut (nazwy projektów są pobierane z Asany), potem panel korzysta z cache."));
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
      h("button", { onclick: async e => { e.target.disabled = true; e.target.textContent = "Wczytywanie… (do kilku minut)"; await ensureProjects(true); route(); } }, "Odśwież z arkusza")),
    !data.sheet_loaded && !data.error && !data.projects.length ? h("div", { class: "notice" }, "Arkusz nie został jeszcze wczytany. ",
      h("button", { class: "small", onclick: async () => { try { const r = await startTool("sheet_load", {}); if (r) go("#/runs/" + r.id); } catch (e) { toast(e.message, true); } } }, "Wczytaj arkusz")) : null,
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
    `Pominięte: ${c.jira_master} prowadzonych w Jirze („migriert zu Jira”), ${c.skipped} z sekcji „pomiń”, ${c.status_unmapped} bez mapowania statusu, ${c.missing_in_jira} usuniętych w Jirze. Nieprzypisanych w Asanie: ${res.unassigned_tasks}`
    + (c.assignee_default ? ` (${c.assignee_default} z nich ma w Jirze domyślnie leada projektu${res.project_lead ? " – " + res.project_lead : ""}, liczone jako zgodne).` : ".")
    + (c.assignee_not_in_jira ? ` Zadania osób „nie w Jirze”: ${c.assignee_not_in_jira} (nie liczone jako różnice).` : "")
    + (c.assignee_unassignable_empty ? ` Bez przypisanego, bo osoby nie da się przypisać: ${c.assignee_unassignable_empty} (zgodne).` : "")
    + (c.assignee_kept ? ` Osoby nie da się przypisać, w Jirze zostaje obecny przypisany: ${c.assignee_kept} (zaakceptowane).` : "")));

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
      h("tr", {}, h("th", {}, "Osoba w Asanie"), h("th", { class: "num" }, "Zadań"), h("th", { class: "num" }, "Otwartych"), h("th", {}, "Konto w Jirze (user_map.json)"), h("th", {}, "Stan"), h("th", {}, "")),
      res.people.map(p => h("tr", {},
        h("td", {}, p.name || "—", h("small", { style: "display:block" }, [p.email, p.gid].filter(Boolean).join(" · "))),
        h("td", { class: "num" }, p.tasks), h("td", { class: "num" }, p.open),
        h("td", { title: p.jira_account_id || "" }, p.jira_account_id ? (p.jira_name || p.jira_account_id) : h("span", { class: "muted" }, "—")),
        h("td", {}, p.problem ? pill(p.problem, "bad") : p.not_in_jira ? pill("nie w Jirze", "info") : pill("OK", "ok")),
        h("td", {}, personSkipButton(p)))))),
    res.people.some(p => p.assignable === false && !p.not_in_jira) ? h("div", { class: "notice warn", style: "margin-top:10px" },
      "„Bez uprawnienia do przypisania” = mapowanie jest poprawne, ale Jira nie pozwala przypisać tego konta w projekcie: żadna rola tej osoby w projekcie (także przez grupę) nie ma uprawnienia „Assignable User” w schemacie uprawnień. Bycie leadem go nie daje. ",
      res.assignable_permission
        ? [`W schemacie „${res.assignable_permission.scheme}” (id ${res.assignable_permission.scheme_id}) przypisanie dają: `, h("strong", {}, res.assignable_permission.holders.map(x => x.name).join(", ")), ". Rozwiązanie: dodać uprawnienie roli, w której są te osoby (zmienia wszystkie projekty z tym schematem), albo dodać osoby/grupę do jednej z ról powyżej w tym projekcie. Do tego czasu nowe zadania takich osób powstają bez przypisanego, a już utworzone zachowują obecnego przypisanego - jedno i drugie nie jest liczone jako różnica."]
        : "Nie udało się odczytać schematu uprawnień (wymaga uprawnień administratora Jiry).") : null,
    h("p", { class: "muted", style: "margin-top:8px" }, "„Nie w Jirze” = osoba świadomie bez konta (lista w asana_users_not_in_jira.json, wspólna dla wszystkich projektów). Jej zadania trafiają do Jiry bez przypisanego (Jira wstawia leada) i nie są liczone jako różnice. Oznaczenie usuwa osobę z user_map.json."),
    h("div", { class: "row", style: "margin-top:10px" },
      h("button", { class: "small", onclick: () => go("#/config/files/asana_users_not_in_jira.json") }, "Lista „nie w Jirze”"),
      h("button", { class: "small", onclick: () => go("#/config/files/user_map.json") }, "Edytuj user_map.json"),
      h("button", { class: "small", onclick: () => go("#/tools/user_map") }, "Generuj user_map (po e-mailu)…"),
      h("button", { class: "small", onclick: () => go("#/tools/asana_members") }, "Lista osób w Asanie…")));
}

function personSkipButton(p) {
  const skip = !p.not_in_jira;
  const btn = h("button", { class: "small", onclick: async () => {
    if (skip && p.jira_account_id && !confirm(`${p.name} ma wpis w user_map.json (${p.jira_name || p.jira_account_id}). Oznaczenie usunie ten wpis - synchronizacja przestanie przypisywać tej osobie zadania. Kontynuować?`)) return;
    try {
      const r = await api("POST", "people-skip", { gid: p.gid, skip, name: p.name, email: p.email });
      toast(skip ? `${p.name}: nie w Jirze${r.removed_from_user_map ? " (usunięto z user_map.json)" : ""}. Sprawdź ponownie, żeby przeliczyć różnice.` : `${p.name}: zdjęto oznaczenie.`);
      btn.disabled = true;
      btn.textContent = "Zapisano ✓";
    } catch (e) { toast(e.message, true); }
  } }, skip ? "Nie w Jirze" : "Cofnij");
  return btn;
}

function diffTable(res) {
  if (!res.mismatches.length) return h("div", { class: "notice ok" }, "Wszystkie zmigrowane zadania mają w Jirze oczekiwany status i przypisanego.");
  let kind = "", q = "";
  const tbody = h("tbody");
  const draw = () => tbody.replaceChildren(...res.mismatches
    .filter(m => (!kind || m.kind === kind) && (!q || `${m.jira_key} ${m.title} ${m.section}`.toLowerCase().includes(q)))
    .map(m => h("tr", {},
      h("td", {}, jiraLink(m.jira_key)), h("td", {}, m.title, m.completed ? h("span", {}, " ", pill("ukończone")) : null),
      h("td", {}, h("small", {}, m.section || "—")), h("td", {}, pill(m.kind, ["brak w Jirze", "brak uprawnienia w Jirze", "problem z mapowaniem"].includes(m.kind) ? "bad" : "warn")),
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

// ------------------------------------------------------------------ Mapowanie osób

VIEWS.people = async main => {
  let st = await api("GET", "people");
  const groupsBox = h("div", { class: "card" });
  const body = h("div");
  main.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, "Mapowanie osób"), h("small", {}, "Osoby z Asany → konta Jiry z wybranych grup. Wynik: user_map.json i asana_users_not_in_jira.json."))),
    groupsBox, body);

  // --- wybór grup Jiry
  const chosen = new Set(st.source ? st.source.groups : []);
  const groupList = h("div", { style: "max-height:180px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:6px;margin:8px 0" });
  const chosenLabel = h("small", {});
  let allGroups = [];
  const drawGroups = q => {
    chosenLabel.textContent = chosen.size ? "Wybrane: " + [...chosen].join(", ") : "Nie wybrano grup.";
    groupList.replaceChildren(...allGroups.filter(g => !q || g.name.toLowerCase().includes(q) || chosen.has(g.name)).slice(0, 300).map(g =>
      h("label", { class: "check", style: "padding:2px 0" }, h("input", { type: "checkbox", checked: chosen.has(g.name),
        onchange: e => { e.target.checked ? chosen.add(g.name) : chosen.delete(g.name); drawGroups(q); } }), g.name)));
  };
  const loadBtn = h("button", { class: "primary", onclick: async () => {
    if (!chosen.size) return toast("Wybierz przynajmniej jedną grupę Jiry.", true);
    loadBtn.disabled = true; loadBtn.textContent = "Wczytywanie…";
    try { st = await api("POST", "people/load", { groups: [...chosen] }); drawMapping(); toast("Wczytano osoby z Asany i Jiry."); }
    catch (e) { toast(e.message, true); }
    loadBtn.disabled = false; loadBtn.textContent = "Wczytaj osoby";
  } }, "Wczytaj osoby");
  groupsBox.append(
    h("h2", {}, "1. Grupy Jiry, z których wybierasz konta"),
    h("input", { type: "search", placeholder: "Filtruj grupy…", style: "max-width:320px", oninput: e => drawGroups(e.target.value.toLowerCase()) }),
    groupList, h("div", { class: "row" }, chosenLabel, h("span", { class: "spacer" }),
      st.source ? h("small", {}, "Ostatnio wczytano " + fmtTime(st.source.loaded_at)) : null, loadBtn));
  api("GET", "people/groups").then(r => { allGroups = r.groups; drawGroups(""); }).catch(e => groupList.replaceChildren(h("span", { class: "muted" }, "Nie udało się pobrać grup: " + e.message)));
  drawGroups("");

  // --- mapowanie
  function drawMapping() {
    const src = st.source;
    if (!src) { body.replaceChildren(h("div", { class: "card muted" }, "Wybierz grupy Jiry i kliknij „Wczytaj osoby”.")); return; }
    const jiraById = Object.fromEntries(src.jira.map(u => [u.account_id, u]));
    const label = acc => { const u = jiraById[acc]; return u ? `${u.name}${u.email ? " — " + u.email : ""}` : acc ? `(spoza wybranych grup) ${acc}` : ""; };
    const byLabel = Object.fromEntries(src.jira.map(u => [label(u.account_id), u.account_id]));
    const asanaByGid = Object.fromEntries(src.asana.map(a => [a.gid, a]));
    let work = {}, mode = "current";

    const init = m => {
      mode = m; work = {};
      src.asana.forEach(a => {
        work[a.gid] = m === "current"
          ? { acc: st.user_map[a.gid] || "", skip: !!st.not_in_jira[a.gid] && !st.user_map[a.gid] }
          : { acc: "", skip: false };
      });
      keepUnlisted.checked = m === "current";
      draw();
    };

    let filter = "todo", q = "", showGuests = false;
    const tbody = h("tbody");
    const summary = h("div", { class: "row", style: "margin:10px 0" });
    const keepUnlisted = h("input", { type: "checkbox", checked: true });
    const dl = h("datalist", { id: "jira-people" }, src.jira.map(u => h("option", { value: label(u.account_id) })));
    const guests = src.asana.filter(a => a.guest).length;

    const usage = () => { const m = {}; Object.entries(work).forEach(([g, w]) => { if (w.acc) (m[w.acc] = m[w.acc] || []).push(g); }); return m; };
    const status = (a, w) => {
      if (w.skip) return ["nie w Jirze", "info"];
      if (w.acc) return [st.user_map[a.gid] === w.acc && mode === "current" ? "zmapowany" : "zmapowany (zmiana)", "ok"];
      if (src.suggestions[a.gid]) return ["jest propozycja", "warn"];
      return ["do decyzji", "bad"];
    };
    const matches = (a, w) => {
      if (!showGuests && a.guest && !w.acc && !w.skip) return false;
      if (q && !`${a.name} ${a.email} ${label(w.acc)}`.toLowerCase().includes(q)) return false;
      switch (filter) {
        case "todo": return !w.acc && !w.skip;
        case "suggested": return !w.acc && !w.skip && !!src.suggestions[a.gid];
        case "mapped": return !!w.acc;
        case "skip": return w.skip;
        case "changed": return (w.acc || "") !== (st.user_map[a.gid] || "") || w.skip !== (!!st.not_in_jira[a.gid] && !st.user_map[a.gid]);
        default: return true;
      }
    };

    function draw() {
      const use = usage();
      const rows = src.asana.filter(a => matches(a, work[a.gid]));
      const all = src.asana.filter(a => showGuests || !a.guest || work[a.gid].acc || work[a.gid].skip);
      const cnt = f => all.filter(a => f(work[a.gid])).length;
      summary.replaceChildren(
        pill(`${cnt(w => !!w.acc)} zmapowanych`, "ok"), pill(`${cnt(w => w.skip)} nie w Jirze`, "info"),
        pill(`${cnt(w => !w.acc && !w.skip)} do decyzji`, cnt(w => !w.acc && !w.skip) ? "bad" : "ok"),
        h("small", {}, `· ${src.jira.length} kont w grupach Jiry, nieużytych: ${src.jira.filter(u => !use[u.account_id]).length}`));
      tbody.replaceChildren(...rows.slice(0, 500).map(a => {
        const w = work[a.gid];
        const sug = src.suggestions[a.gid];
        const input = h("input", { type: "text", list: "jira-people", value: label(w.acc), placeholder: "wpisz imię lub e-mail…", disabled: w.skip,
          onchange: e => {
            const v = e.target.value.trim();
            if (!v) { w.acc = ""; draw(); return; }
            if (byLabel[v]) { w.acc = byLabel[v]; draw(); return; }
            toast("Wybierz osobę z listy podpowiedzi.", true); e.target.value = label(w.acc);
          } });
        const dup = w.acc && use[w.acc] && use[w.acc].length > 1
          ? h("small", { style: "display:block;color:var(--warn)" }, "to samo konto także: " + use[w.acc].filter(g => g !== a.gid).map(g => asanaByGid[g] ? asanaByGid[g].name : g).join(", ")) : null;
        return h("tr", {},
          h("td", {}, a.name || "—", a.guest ? h("span", {}, " ", pill("gość")) : null,
            h("small", { style: "display:block" }, a.email || "brak e-maila")),
          h("td", { style: "min-width:280px" }, input, dup),
          h("td", {}, sug && sug.account_id !== w.acc && !w.skip
            ? h("button", { class: "small", title: "Dopasowanie: " + sug.reason, onclick: () => { w.acc = sug.account_id; draw(); } }, "← " + (jiraById[sug.account_id] || {}).name, h("small", { class: "muted" }, ` (${sug.reason})`))
            : h("span", { class: "muted" }, sug && sug.account_id === w.acc ? `✓ ${sug.reason}` : "—")),
          h("td", {}, h("label", { class: "check" }, h("input", { type: "checkbox", checked: w.skip, onchange: e => { w.skip = e.target.checked; if (w.skip) w.acc = ""; draw(); } }), "nie w Jirze")),
          h("td", {}, pill(...status(a, w))));
      }), rows.length > 500 ? h("tr", {}, h("td", { colspan: 5, class: "muted" }, `Pokazano 500 z ${rows.length} - zawęź filtr.`)) : null);
    }

    const acceptAll = () => {
      let n = 0;
      src.asana.forEach(a => { const w = work[a.gid]; const s = src.suggestions[a.gid];
        if (s && !w.acc && !w.skip && (showGuests || !a.guest)) { w.acc = s.account_id; n++; } });
      toast(`Przyjęto ${n} propozycji.`); draw();
    };
    const skipVisibleTodo = () => {
      const rows = src.asana.filter(a => matches(a, work[a.gid]) && !work[a.gid].acc && !work[a.gid].skip);
      if (!rows.length) return toast("Brak widocznych osób do decyzji.");
      if (!confirm(`Oznaczyć ${rows.length} widocznych, niezmapowanych osób jako „nie w Jirze”?`)) return;
      rows.forEach(a => { work[a.gid].skip = true; }); draw();
    };
    const save = async () => {
      const mapping = {}, notIn = {};
      Object.entries(work).forEach(([g, w]) => { if (w.acc) mapping[g] = w.acc; else if (w.skip) notIn[g] = { name: asanaByGid[g].name, email: asanaByGid[g].email }; });
      const msg = `Zapisać ${Object.keys(mapping).length} mapowań do user_map.json i ${Object.keys(notIn).length} osób „nie w Jirze”?` +
        (keepUnlisted.checked ? "\nWpisy osób spoza wczytanej listy zostaną zachowane." : "\nPliki zostaną zastąpione (świeża mapa) - kopie trafią do migration_app/backups/.");
      if (!confirm(msg)) return;
      try {
        const r = await api("PUT", "people/mapping", { mapping, not_in_jira: notIn, keep_unlisted: keepUnlisted.checked });
        toast(`Zapisano: user_map.json ${r.user_map} wpisów (+${r.added}, −${r.removed}, zmienionych ${r.changed}), nie w Jirze: ${r.not_in_jira}.`);
        st = await api("GET", "people"); mode = "current"; draw();
      } catch (e) { toast(e.message, true); }
    };

    const FILTERS = [["todo", "Do decyzji"], ["suggested", "Z propozycją"], ["mapped", "Zmapowane"], ["skip", "Nie w Jirze"], ["changed", "Zmienione (niezapisane)"], ["all", "Wszystkie"]];
    body.replaceChildren(dl, h("div", { class: "card" },
      h("h2", {}, "2. Dopasuj osoby"),
      !src.jira_emails_visible ? h("div", { class: "notice warn" }, "Jira nie zwraca adresów e-mail (widoczność kont) - propozycje opierają się tylko na imieniu i nazwisku.") : null,
      h("div", { class: "row" },
        h("span", {}, "Start:"),
        h("label", { class: "check" }, h("input", { type: "radio", name: "pmode", checked: true, onchange: () => init("current") }), `od obecnego user_map.json (${Object.keys(st.user_map).length})`),
        h("label", { class: "check" }, h("input", { type: "radio", name: "pmode", onchange: () => init("fresh") }), "od pustej mapy (świeża migracja)")),
      summary,
      h("div", { class: "row", style: "margin-bottom:10px" },
        h("select", { style: "max-width:220px", onchange: e => { filter = e.target.value; draw(); } }, FILTERS.map(([v, l]) => h("option", { value: v }, l))),
        h("input", { type: "search", placeholder: "Szukaj osoby…", style: "max-width:240px", oninput: e => { q = e.target.value.toLowerCase(); draw(); } }),
        h("label", { class: "check" }, h("input", { type: "checkbox", onchange: e => { showGuests = e.target.checked; draw(); } }), `pokaż gości Asany (${guests})`),
        h("span", { class: "spacer" }),
        h("button", { onclick: acceptAll }, "Przyjmij wszystkie propozycje"),
        h("button", { onclick: skipVisibleTodo }, "Widoczne „do decyzji” → nie w Jirze")),
      h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, h("th", {}, "Osoba w Asanie"), h("th", {}, "Konto w Jirze"), h("th", {}, "Propozycja"), h("th", {}, ""), h("th", {}, "Stan"))), tbody)),
      h("div", { class: "row", style: "margin-top:12px" },
        h("label", { class: "check" }, keepUnlisted, "zachowaj wpisy osób spoza wczytanej listy"),
        h("span", { class: "spacer" }),
        h("button", { class: "primary", onclick: save }, "Zapisz mapowanie"))));
    init("current");
  }
  drawMapping();
};

// ------------------------------------------------------------------ Kopia projektu

// Wybór osób i grup dla ról nowego projektu: start od ról źródła, z możliwością
// odznaczenia, dodania osób/grup z wyszukiwarki i dodania innych ról.
function rolesEditor(sourceKey) {
  let data = null, none = false;
  const body = h("div");
  const card = h("div", { class: "card" });

  const addSearch = role => {
    const kind = h("select", { style: "max-width:110px" }, h("option", { value: "users" }, "osoba"), h("option", { value: "groups" }, "grupa"));
    const q = h("input", { type: "text", placeholder: "imię, e-mail albo nazwa grupy…", style: "max-width:240px" });
    const results = h("div", { class: "row", style: "margin-top:4px" });
    const search = async () => {
      if (q.value.trim().length < 2) return toast("Wpisz co najmniej 2 znaki.", true);
      results.replaceChildren(h("small", { class: "muted" }, "Szukam…"));
      try {
        const found = await api("GET", `jira/${kind.value}?q=${encodeURIComponent(q.value.trim())}`);
        const list = kind.value === "users" ? role.users : role.groups;
        const idKey = kind.value === "users" ? "accountId" : "groupId";
        results.replaceChildren(...(found.length ? found.map(x => h("button", { class: "small", title: x.email || "",
          onclick: () => { if (!list.some(y => y[idKey] === x[idKey])) list.push({ [idKey]: x[idKey], name: x.name, on: true }); draw(); } },
          "+ " + x.name + (x.active === false ? " (nieaktywne)" : ""))) : [h("small", { class: "muted" }, "Nic nie znaleziono.")]));
      } catch (e) { results.replaceChildren(h("small", { style: "color:var(--bad)" }, e.message)); }
    };
    q.addEventListener("keydown", e => { if (e.key === "Enter") search(); });
    return h("div", {}, h("div", { class: "row" }, kind, q, h("button", { class: "small", onclick: search }, "Szukaj")), results);
  };

  const chip = (item, label) => h("label", { class: "check", style: "margin-right:12px" },
    h("input", { type: "checkbox", checked: item.on, onchange: e => { item.on = e.target.checked; } }), label);

  function draw() {
    if (!data) {
      body.replaceChildren(h("p", { class: "muted" }, none ? "Nikt nie zostanie dodany do ról nowego projektu (poza leadem)."
        : "Wczytaj role projektu źródłowego - osoby i grupy będą wstępnie zaznaczone, możesz je odznaczyć albo dodać inne."));
      return;
    }
    const shown = data.rows.filter(r => r.users.length || r.groups.length || r.added);
    const free = data.all_roles.filter(r => !shown.some(x => x.id === r.id));
    const roleSel = h("select", { style: "max-width:260px" }, h("option", { value: "" }, "+ dodaj rolę…"), free.map(r => h("option", { value: r.id }, r.name)));
    roleSel.onchange = () => {
      const r = data.all_roles.find(x => x.id === roleSel.value);
      if (!r) return;
      const row = data.rows.find(x => x.id === r.id);
      if (row) row.added = true; else data.rows.push({ id: r.id, name: r.name, users: [], groups: [], added: true });
      draw();
    };
    body.replaceChildren(
      ...shown.map(r => h("div", { style: "border-top:1px solid var(--border);padding:10px 0" },
        h("strong", {}, r.name),
        h("div", { style: "margin:6px 0" },
          r.groups.map(g => chip(g, "👥 " + g.name)), r.users.map(u => chip(u, u.name || u.accountId)),
          !r.users.length && !r.groups.length ? h("small", { class: "muted" }, "nikogo - dodaj poniżej") : null),
        addSearch(r))),
      h("div", { class: "row", style: "border-top:1px solid var(--border);padding-top:10px" }, roleSel));
  }

  const loadBtn = h("button", { onclick: async () => {
    const key = sourceKey();
    if (!key) return toast("Najpierw podaj projekt źródłowy.", true);
    loadBtn.disabled = true; loadBtn.textContent = "Wczytuję…";
    try {
      const d = await api("GET", `jira/roles?project=${encodeURIComponent(key)}`);
      data = { all_roles: d.all_roles, rows: d.roles.map(r => ({ id: r.id, name: r.name,
        users: r.users.map(u => ({ ...u, on: true })), groups: r.groups.map(g => ({ ...g, on: true })) })) };
      none = false; noneBox.checked = false;
      draw();
    } catch (e) { toast(e.message, true); }
    loadBtn.disabled = false; loadBtn.textContent = data ? "Wczytaj ponownie" : "Wczytaj role źródła";
  } }, "Wczytaj role źródła");
  const noneBox = h("input", { type: "checkbox", onchange: e => { none = e.target.checked; if (none) data = null; draw(); } });

  card.append(h("div", { class: "row" }, h("h2", { style: "margin:0" }, "Role i dostęp"), h("span", { class: "spacer" }),
      h("label", { class: "check" }, noneBox, "nikogo nie dodawaj"), loadBtn),
    h("p", { class: "muted", style: "margin:8px 0" }, "Kogo i z jaką rolą dodać do nowego projektu. Uwaga: przypisywać zadania można tylko osobom z roli, która w schemacie uprawnień ma „Assignable User”."),
    body);
  draw();
  return {
    card, none: () => none, loaded: () => !!data,
    selection: () => (data ? data.rows : []).map(r => ({ id: r.id, name: r.name,
      users: r.users.filter(u => u.on).map(u => u.accountId), groups: r.groups.filter(g => g.on).map(g => g.groupId) })),
  };
}

const SCHEME_LABELS = ["Permission Scheme", "Notification Scheme", "Issue Security Scheme", "Workflow Scheme",
  "Issue Type Scheme", "Issue Type Screen Scheme", "Field Scheme", "Priority Scheme"];

VIEWS.copy = async main => {
  await ensureCatalog();
  const v = { "--name-base": "key" };
  const input = (name, label, opts = {}) => h("div", { class: "field" }, h("span", {}, label, opts.required ? " *" : ""),
    h("input", { type: "text", placeholder: opts.placeholder || "", oninput: e => { v[name] = e.target.value.trim(); if (opts.upper) { e.target.value = e.target.value.toUpperCase(); v[name] = e.target.value.trim(); } draw(); } }),
    opts.help ? h("small", { class: "muted" }, opts.help) : null);
  const check = (name, label) => h("label", { class: "check" }, h("input", { type: "checkbox", onchange: e => { v[name] = e.target.checked; } }), label);
  const names = h("ul", { class: "mono", style: "margin:6px 0 0;padding-left:18px;font-size:12.5px" });
  const draw = () => {
    const base = v["--name-base"] === "name" ? (v["--target-name"] || "Nazwa projektu") : (v["--target-key"] || "KLUCZ");
    names.replaceChildren(...SCHEME_LABELS.map(l => h("li", {}, `${base} ${l}`)));
  };
  const logBox = h("div", { style: "margin-top:12px" });
  const roles = rolesEditor(() => (v["--source"] || "").toUpperCase());
  const run = dry => async () => {
    for (const [k, l] of [["--source", "projekt źródłowy"], ["--target-key", "klucz nowego projektu"], ["--target-name", "nazwę nowego projektu"]])
      if (!v[k]) return toast(`Podaj ${l}.`, true);
    const values = { ...v, "--source": (v["--source"] || "").toUpperCase(), "--dry-run": dry, "--no-roles": roles.none() };
    delete values["--roles-file"];
    if (!roles.none()) {
      if (roles.loaded()) {
        try {
          const r = await api("POST", "clone/roles", { target_key: v["--target-key"], roles: roles.selection() });
          values["--roles-file"] = r.path;
        } catch (e) { return toast(e.message, true); }
      } else if (!dry) {
        return toast("Wczytaj role projektu źródłowego i wybierz, kogo dodać (albo zaznacz „nikogo nie dodawaj”).", true);
      }
    }
    await runInline(logBox, "clone_project", values);
  };
  draw();
  main.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Kopia projektu"),
      h("small", {}, "Nowy projekt z konfiguracją projektu źródłowego - każdy schemat dostaje własną kopię z nazwą nowego projektu. Nie korzysta z projektu wzorcowego ani ustawień z .env (poza dostępem do Jiry)."))),
    h("div", { class: "card" },
      h("h2", {}, "Projekty"),
      h("div", { class: "form-grid" },
        input("--source", "Projekt źródłowy (klucz)", { required: true, upper: true, placeholder: "ABC" }),
        input("--target-key", "Klucz nowego projektu", { required: true, upper: true, placeholder: "XYZ", help: "2-10 znaków: wielkie litery i cyfry, na początku litera." }),
        input("--target-name", "Nazwa nowego projektu", { required: true }),
        input("--lead-account-id", "Lead (accountId)", { help: "Puste = lead projektu źródłowego." }),
        input("--description", "Opis", { help: "Puste = opis projektu źródłowego." }))),
    h("div", { class: "card" },
      h("h2", {}, "Nazwy kopii schematów"),
      h("div", { class: "row" },
        h("label", { class: "check" }, h("input", { type: "radio", name: "nb", checked: true, onchange: () => { v["--name-base"] = "key"; draw(); } }), "od klucza nowego projektu"),
        h("label", { class: "check" }, h("input", { type: "radio", name: "nb", onchange: () => { v["--name-base"] = "name"; draw(); } }), "od nazwy nowego projektu")),
      names,
      h("p", { class: "muted", style: "margin-top:8px" }, "Kopiowane są tylko schematy, których źródło używa (np. bez schematu bezpieczeństwa nie powstaje jego kopia). W instancjach sprzed migracji na Field Schemes zamiast „Field Scheme” powstaje „Field Configuration Scheme”. Uprawnień, których Jira nie przyjmuje przy tworzeniu schematu (np. Product Discovery, COPY_PROJECT), nie da się skopiować - log je wymienia. Schemat o takiej nazwie, który już istnieje, jest używany ponownie. Same workflow, ekrany i konfiguracje pól pozostają wspólne ze źródłem - wskazują na nie nowe schematy.")),
    roles.card,
    h("div", { class: "card" },
      h("h2", {}, "Co jeszcze skopiować"),
      h("p", { class: "muted" }, "Domyślnie wszystko: ustawienia projektu (kategoria, URL, domyślne przypisanie), komponenty, wersje, konteksty pól niestandardowych ograniczone do źródła oraz tablice - filtr z tymi samymi udostępnieniami, kolumny i przypisanie statusów do kolumn jak w źródle (przez API, bez przeglądarki; po zapisie skrypt sprawdza, czy każda kolumna ma te same statusy)."),
      h("div", { class: "form-grid" },
        check("--no-versions", "bez wersji"), check("--no-components", "bez komponentów"),
        check("--no-field-contexts", "bez kontekstów pól (szybciej - pomija przegląd wszystkich pól)"), check("--no-boards", "bez tablic"),
        check("--existing-target", "projekt docelowy już istnieje (dokończenie przerwanej kopii)")),
      h("div", { style: "margin-top:10px" }, input("--board-ids", "ID tablic źródła", { help: "Puste = tablice przypisane do projektu źródłowego." }))),
    h("div", { class: "card" },
      h("div", { class: "row" }, h("h2", { style: "margin:0" }, "Uruchomienie"), h("span", { class: "spacer" }),
        h("button", { onclick: run(true) }, "Podgląd (dry-run)"),
        h("button", { class: "primary", onclick: run(false) }, "Utwórz kopię")),
      h("p", { class: "muted", style: "margin:8px 0 0" }, "Zacznij od podglądu - pokazuje schematy źródła, nazwy kopii, role, komponenty i tablice. „Utwórz kopię” poprosi o potwierdzenie."),
      logBox));
};

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
    files.map(f => h("div", { class: "tool-item" + (f.path === path ? " active" : ""), onclick: () => go("#/config/files/" + f.path) }, h("span", { class: "mono", style: "font-size:12.5px" }, f.path), f.missing ? h("span", { class: "w muted" }, "nowy") : null)),
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
  const isNewMap = f.missing && /^section_status_map\..+\.json$/.test(path);
  const ta = h("textarea", { class: "editor", spellcheck: false }, f.content || (isNewMap ? '{\n  "__ukonczone__": "Done"\n}\n' : ""));
  const status = h("small");
  const validate = () => {
    if (!isJson) return true;
    try { JSON.parse(ta.value); status.textContent = "JSON poprawny"; status.style.color = "var(--ok)"; return true; } catch (e) { status.textContent = "Błąd JSON: " + e.message; status.style.color = "var(--bad)"; return false; }
  };
  ta.addEventListener("input", validate);
  ta.addEventListener("keydown", e => { if (e.key === "Tab") { e.preventDefault(); ta.setRangeText("  ", ta.selectionStart, ta.selectionEnd, "end"); } });
  editorBox.append(h("div", { class: "card" },
    h("div", { class: "row", style: "margin-bottom:8px" }, h("h2", { class: "mono", style: "margin:0" }, path), f.missing ? pill("nowy plik - powstanie przy zapisie", "info") : null, h("span", { class: "spacer" }), status),
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
