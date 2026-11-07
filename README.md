# Migration and sync: Asana → Jira (driven by a Google Sheet)

**English** · [Polski](README.pl.md)

> **© 2026 Tomasz Strona (GitHub: Achatchriel). All rights reserved.**
> This repository is for **private, non-commercial use only**. **Any commercial use is
> strictly prohibited** - including use in or for a company, in paid or client work and
> as part of a commercial product or service. It is not open source; redistribution
> requires the author's written permission. See [LICENSE](LICENSE).

A toolkit for moving selected projects from Asana to Jira and keeping them in sync.
**You work with it through the web panel** (`migration_app/`): a local app in the
browser from which you run every migration step, check statuses and people, edit
the configuration and browse reports. Under the hood the panel calls ordinary
Python scripts, documented in detail in part II.

**The Google Sheet is the source of truth** for which projects should exist in
Jira: the tools create and sync only the projects in the sheet's rows.

- **Part I — the panel:** one-command install, the Start tab (a new migration step by
  step), people and status mapping, copying projects, the panel views and everyday work.
- **Part II — the scripts:** documentation of every tool, options, state files,
  repair procedures and troubleshooting.

> The panel is in Polish by default - switch it to **English** with **PL / EN** at the bottom
> of the sidebar (the choice is remembered in the browser). Script output (run logs) stays in
> Polish; script messages quoted in this README are kept in the original wording (with a
> translation where helpful) so you can search for them in the console output.

---

# Part I. The panel

## Quick start

**You need:** Linux or macOS with Python 3.10+, a **Jira administrator** account (Cloud)
with an [API token](https://id.atlassian.com/manage-profile/security/api-tokens), an
[Asana token](https://app.asana.com/0/my-apps), a **Google service account** (JSON key)
and a **Google Sheet** listing the projects to migrate (section 7 in part II - the
`asana_project_link` column is enough, `project_name` and `jira_key` are recommended).

```bash
git clone <repository URL> jiraSync
cd jiraSync
./start.sh                 # http://127.0.0.1:8765
```

On first run `start.sh` creates `.venv`, installs the dependencies (`requirements.txt`,
again only when it changes), then starts the panel and opens it in your browser. No
browser automation is needed: the pipeline and "Copy project" work through the API. Options:
`./start.sh --port 9000` (another port), `NO_BROWSER=1 ./start.sh` (don't open a browser),
`PYTHON=python3.12 ./start.sh` (another interpreter). The server listens on `127.0.0.1`
only. Stop it with `Ctrl+C`. Language: **PL / EN** at the bottom of the sidebar.

The panel works from an empty folder: `.env` is created from `.env.example` on the first
configuration save, maps and lists from templates. From there the **Start** tab guides you.

## The Start tab: a new migration step by step

Each step's state is detected automatically (from files and test results), so you can see
what is ready. The order matters - each step builds on the previous ones.

| # | Step | What it does | Result |
|---|---|---|---|
| 1 | **Configuration** | a form for the key variables; paste the Google service account key; the panel shows the address the sheet must be shared with | `.env`, `service_account.json` |
| 2 | **Connection test** | Jira account and admin permission, template project and board, fields required on issue creation that the script cannot fill in, Asana token and workspace (pick from a list), sheet access, tab, headers, rows without an Asana link | `migration_app/cache/setup_check.json` |
| 3 | **Project sheet** | loads the project list (with rows lacking a name - a few minutes, names are fetched from Asana) | `migration_app/cache/sheet.json` |
| 4 | **People mapping** | Asana people ↔ accounts from selected Jira groups, with suggestions; people without an account are marked "not in Jira" | `user_map.json`, `asana_users_not_in_jira.json` |
| 5 | **Status mapping** | Asana sections from all projects ↔ **statuses of the template project**; the status for tasks completed in Asana | `section_status_map.json` |
| 6 | **Preview** | the whole pipeline in dry-run mode - writes nothing | log in Runs |
| 7 | **Migration** | the full pipeline; the panel shows the command and asks for confirmation | projects and issues in Jira |
| 8 | **Verification** | status and assignee consistency, task counts compared with Asana | `migration_app/checks/` |

Step 2 also runs from the terminal: `.venv/bin/python migration_app/setup_check.py`.
Start with a sheet tab containing 1-2 test projects (`GOOGLE_SHEET_RANGE`), go through
everything, review the result in Jira and only then switch to the full list.

## People and status mapping

**People mapping** loads the members of the Asana workspace and the members of
**selected Jira groups**. Suggested matches: same e-mail, same part before `@` in another
domain, same first and last name, the pattern `first.last@JIRA_EMAIL_DOMAIN`. You can match
each person by hand (suggestions by name or e-mail) or mark them **"not in Jira"** - their
issues are created without an assignee and are not counted as differences. Start from the
current `user_map.json` or from an empty map; Asana guests are hidden by default; the panel
warns when two Asana people point to the same account. Remember: an issue can only be
assigned to a person whose project role has the **"Assignable User"** permission in the
permission scheme - mapping alone (or being the project lead) does not grant it.

**Status mapping** collects section names from all sheet projects (with project and task
counts) and the statuses of the template project (`JIRA_TEMPLATE_PROJECT_KEY`, or another
project you name). You can only choose **statuses that exist in the template**, grouped
into "to do", "in progress" and "done". Suggestions: same name, then the meaning of the
section (e.g. Fertig/Done/Abgeschlossen → the template's "done" status, In Arbeit → "in
progress", Backlog/To Do → "to do"). A section can be marked "skip" (its issues and their
subtrees are not migrated); an unmapped section stays in the workflow's initial status.
**You pick the status for tasks completed in Asana explicitly** (the `__ukonczone__` entry) -
without it the script would use `Fertig`, which your Jira may not have. Entries pointing to
a status outside the template are flagged, and step 5 is only ready without them.

Single-project maps (`section_status_map.<KEY>.json`) are edited in **Statuses and people**
or in **Configuration**.

## Copy project

A separate tab for copying an existing (company-managed) project into a new one. It is
independent of the migration: the `clone_project.py` script imports no other script, does
not use the template project and reads only Jira access from `.env` - **everything comes
from the source project**.

- **Schemes:** every scheme the source uses gets a copy named after the new project - from
  its key or its name (chosen in the tab, with a name preview), e.g. `XYZ Permission Scheme`,
  `XYZ Notification Scheme`, `XYZ Issue Security Scheme`, `XYZ Workflow Scheme`,
  `XYZ Issue Type Scheme`, `XYZ Issue Type Screen Scheme`, `XYZ Field Scheme` (on instances
  not yet migrated to Field Schemes: `XYZ Field Configuration Scheme`), `XYZ Priority Scheme`.
  Permissions that Jira does not accept when creating a scheme (e.g. Product Discovery,
  `COPY_PROJECT`) cannot be copied - the log lists them. A scheme with that name that already
  exists (e.g. after an interrupted run) is reused. The workflows, screens and field
  configurations themselves stay shared with the source (the new schemes point to them).
- **Project:** type, lead (or the one you give), URL, category and default assignee as in
  the source; the project is created with the scheme copies right away. The **description
  is not copied** - it stays empty unless you type one in the tab (`--description`).
- **Roles and access:** you search the whole Jira for people and groups and give each of them
  one or more roles (any project role in Jira) - the source project's members are not used.
  An empty list adds nobody (except the lead). The selection goes to the script as
  `--roles-file`. From the terminal: without the file nobody is added; `--roles-from-source`
  copies the source project's roles and members.
- **Boards:** a filter with the project replaced and the same sharing, the board, its columns
  and **the status-to-column mapping** as in the source (via the GreenHopper API with the
  token, no browser). Workflows are shared with the source, so statuses keep their IDs. After
  saving, the script re-reads the new board and compares every column and its statuses with
  the source (the log lists columns and statuses, or warns about a difference).
- **Also (each can be switched off):** components, versions, custom field contexts limited
  to the source project.
- **Not copied:** issues, swimlanes, quick filters and card layout.

Start with **Preview (dry-run)** - it shows the source schemes, the copy names, roles,
components and boards. "Create copy" asks for confirmation. After an interruption run it
again with "target project already exists". From the terminal:
```bash
.venv/bin/python clone_project.py --source ABC --target-key XYZ --target-name "New project" --dry-run
.venv/bin/python clone_project.py --source ABC --target-key XYZ --target-name "New project" --name-base name
```
The older `copy_project.py` (section 4a) attaches **the same** schemes as the source to the
new project and configures columns through the browser.

## Project migration (one project from an Asana link)

A tab for migrating a single project **without the Google Sheet**: you paste a link to the
Asana project and the panel creates the Jira project, maps statuses and moves the tasks.
Under the hood it runs `migrate_project.py`.

1. **Project in Asana** - "Load project" reads the name, the sections (in Asana order) and
   the number of open/completed tasks in each (`migration_app/asana_lookup.py`, read-only).
2. **New project in Jira** - key, name (defaults to the Asana name), lead ("use my account"
   fills in your accountId) and description (empty by default).
3. **Project configuration** - one of:
   - **template project** (`JIRA_TEMPLATE_PROJECT_KEY`): the same schemes as the template,
     the board gets the template board's columns and statuses (`board_columns.py`), roles
     get the developer group and administrators from `.env`;
   - **source project** (as in "Copy project"): every source scheme gets a copy named after
     the new project (from its key or name) and boards are copied with their columns -
     `clone_project.py` does this.
   "Load statuses" reads the statuses the new project will have (those of the template or
   the source project).
4. **Asana sections → Jira statuses** - every section gets its own select with **only the
   statuses of the chosen configuration**. Suggestions come from the main map (if it points
   to an existing status) or from the section's name; you can also skip a section. The map
   is saved as `section_status_map.<KEY>.json` (the previous one is backed up) and applies to
   this project only. You also choose the status for completed tasks.
5. **Roles and access** - the same editor as in "Copy project" (people and groups from the
   whole Jira, any roles). With the template, an empty list means the `.env` defaults; with
   a source project, nobody except the lead.
6. **Run** - "Preview (dry-run)" changes nothing; "Create and migrate" asks for
   confirmation, then: creates and configures the project, writes the section map, sets
   the board columns (template only), detects Epics and syncs the tasks
   (`asana_jira_sync.py --asana-link`). Options: only create and configure (no tasks), and
   "project already exists" to finish the remaining steps or re-sync later.

A project migrated this way is not in the sheet, so the full pipeline does not see it -
run further syncs from this tab. From the terminal:
```bash
.venv/bin/python migrate_project.py --asana-link "https://app.asana.com/1/<ws>/project/<gid>/board" \
    --key XYZ --config template --map-file section_map.json --dry-run
.venv/bin/python migrate_project.py --asana-link "<link>" --key XYZ --config source --source ABC --no-roles
```
`asana_jira_sync.py` and `detect_epic_candidates.py` also accept `--asana-link` (with exactly
one project key) and then skip the sheet.

## Panel views

| View | What it is for |
|---|---|
| **Start** | the steps of a new migration (above) |
| **Overview** | the whole picture on one screen: gaps in `.env`, sync state, protected and excluded projects, recent runs |
| **Projects** | projects from the sheet (without a loaded sheet: from the state file and `project_key_cache.json`), number of state entries, section map, flags, result of the last check. **Selected projects automatically fill the "Projects" fields in the tools** |
| **Tools** | 69 scripts in seven groups (table below), each with an options form. The panel shows the exact command before starting |
| **Statuses and people** | a read-only check per project: Asana sections → Jira statuses (the same rules as the sync: project map, profile, main map, `__ukonczone__`), people → Jira accounts (whether they are in `user_map.json`, active, assignable - when they lack the permission the panel shows the permission scheme and the roles granting it) and status/assignee differences in already migrated issues. A missing section mapping can be saved straight to `section_status_map.<KEY>.json`, a person can be marked "not in Jira" |
| **People mapping** / **Status mapping** | as above |
| **Project migration** | one project from an Asana link to a new Jira project, with an individual section → status map (above) |
| **Copy project** | a new project with an existing one's configuration and its own scheme copies (above) |
| **Runs** | history with full live logs; an input field for scripts that ask for confirmation; stopping a whole run (with child processes) |
| **Configuration** | editing `.env` (secrets masked, empty field = unchanged), status maps, profiles, `user_map.json`, `asana_users_not_in_jira.json`, `sync_exclude.txt`, `protected_projects.txt` and YAML schemas; files that don't exist yet are created on the first save |
| **Reports** | `logs/`, `pipeline_summaries/`, `repair_reports/` and CSV/JSON reports (CSV shown as a table) |

## Tool groups

| Group in the panel | What it contains | Described in part II |
|---|---|---|
| **Migration pipeline** | loading the sheet, the whole pipeline, creating projects, board columns and statuses, Epic candidates, sync in both directions, saving the browser session, Asana project files | sections 8, 11-12 |
| **Asana: statuses and assignees** | status and people check, status mapping sources, project sections, Asana members, user map, token | section 11 ("Configuration before the first run") |
| **Verification** | counting and comparison with Asana, duplicates, hierarchy audits, diagnostics | section 14 |
| **Repairs** | restoring links, removing duplicates, "migriert zu Jira", false Epics, state flags, batch repairs | section 15 |
| **Jira configuration** | schema from YAML, workflow layout and updates, screens, field schemes, groups on transitions | section 17 |
| **Project copies** | copy with scheme copies (`clone_project.py`), copy within the same instance and from another Jira instance | part I ("Copy project"), section 4a |
| **Other** | Epics and Stories from the scope sheet and export back, "Analysis" issues for Stories, deactivating suspended Atlassian accounts | sections 20 and 22 |

## How the panel protects data

- **Preview by default.** Tools that have a preview mode start with `--dry-run` checked;
  writing requires unchecking it or checking `--apply`.
- **Confirmation before writing.** Every run that will change data in Jira, Asana or in
  files is flagged and requires confirmation.
- **File backups.** Before every save from the panel (configuration, mappings, `.env`) the
  panel copies the file to `migration_app/backups/`.
- **Secrets stay out of the repo.** `.env`, `service_account.json` and `auth_state.json` are
  in `.gitignore`; the panel never shows token values.
- **The same safeguards as the scripts:** `protected_projects.txt`, `sync_exclude.txt`,
  project locks and atomic state writes work exactly as when run from the terminal
  (section 11).

## Everyday work in the panel

**A new migration:** the **Start** tab, steps 1-8. Before creating issues in bulk, ask a
Jira administrator to switch off notifications and automation for the new projects (Jira
sends e-mails for every issue created through the API).

**More projects in an ongoing migration:** **Projects** → select projects → **Statuses and
people** (missing sections and people) → **Tools → Pipeline** with preview, review the log
in **Runs**, then the same pipeline without preview → verification.

**Routine sync:** Tools → *Pipeline* → Epic candidates, then sync Asana → Jira (optionally
Jira → Asana, never in parallel with the first). Each step's summary is in the log and, if
configured, on Slack. How to react to the summary: section 18.

**Repairs and checks:** the *Verification* and *Repairs* groups — always report first, then
write. The order of steps when repairing projects synced by an older version is described
in sections 15i and 18.

---

# Part II. The scripts — detailed documentation

Every panel tool is an ordinary script in the main folder. The panel form matches its
command-line options, so everything below can also be run from the terminal
(`.venv/bin/python <script> ...` or after `source .venv/bin/activate`).

## Toolkit overview

A toolkit that moves selected projects from Asana to Jira and keeps them in
sync. **The Google Sheet is the source of truth** for which projects should
exist in Jira: the scripts create projects only for rows in the sheet.

What the toolkit does, step by step:

1. creates projects in Jira (`jira_sync.py`),
2. configures board columns and assigns statuses to them, through the API (`board_columns.py`),
3. detects tasks that need the Epic → Task → Sub-task hierarchy (`detect_epic_candidates.py`),
4. syncs tasks, subtasks, comments and attachments (`asana_jira_sync.py`),
5. optionally syncs changes back from Jira to Asana (`jira_asana_sync.py`).

Steps 1-4 are run with a single command by the `run_full_pipeline.py`
orchestrator (section 11a), also in parallel and with a combined Slack summary.

**The sheet contains only the projects that should be synced right now.** Projects
that are already migrated and archived in Jira are removed from it, so no tool
working off the sheet touches them. The whole working procedure is in section 18.

The toolkit also includes:

- **check and repair tools** for projects synced with older versions:
  comparison with Asana (`compare_project_totals.py`), hierarchy audit
  (`audit_hierarchy.py`), restoring lost links (`relink_state.py`),
  removing duplicates (`resolve_relink_duplicates.py`), tasks moved to Jira
  by hand (`handle_migrated_to_jira.py`) and a multi-project driver
  (`repair_batch.py`), sections 14-15,
- **copying projects from another Jira instance**, including splitting into
  several projects and pulling in new items (`copy_project_cross_instance.py`, section 4a),
- **safety mechanisms**: `sync_exclude.txt` (temporarily excludes from sync)
  and `protected_projects.txt` (permanently excludes from everything), section 11,
- **Jira configuration from a YAML file** (`build_jira_schema.py`, section 17) and
  **Epics and Stories from a scope sheet** (`create_epics_stories_from_sheet.py`,
  section 20); both tools are independent of the Asana migration,
- **the web panel** `migration_app/` (part I; panel files: section 21).

Every sheet row must have an `asana_project_link`. A row without a link (or with
a link the token cannot access) is **skipped entirely**: the project is not
created in Jira. Name-based matching has been deliberately disabled (section 7).

**Contents of part II:** 1-3 installation and configuration · 4-4a template project and copying
projects · 5-6 roles · 7 sheet format · 8-10 creating projects · 11 task sync
(including the Epic hierarchy) · 11a the whole pipeline and parallelism · 11b statuses
in columns · 12 reverse direction · 13 logs and Slack · 14 verification and cleanup ·
15 repairing hierarchy, lost links and duplicates (15f-15i) · 16 state files ·
17 Jira configuration from YAML (fields, statuses, workflows, screens, schemes) ·
18 working procedure (repair, routine, periodic) · 19 troubleshooting
(19a sync and repairs, 19b Jira configuration in practice) ·
20 Epics and Stories from the "Zakres MVP" (MVP scope) sheet · 21 panel files ·
22 other helper scripts

## 1. Installation

Simplest: `./start.sh` (part I, "Quick start"). By hand - the panel and the scripts expect
the environment in `.venv`:
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # optional: only the older browser tools
```

## 2. Configuration

**In the panel:** the Start tab, step 1 - the form saves `.env` (created from
`.env.example`), a pasted Google service account key goes to `service_account.json`, and
the panel shows the address the sheet must be shared with. Step 2 checks everything at
once (from the terminal: `.venv/bin/python migration_app/setup_check.py`).

**By hand:**

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Fill in the values in `.env`:
   - **Jira**: instance URL, e-mail, [API token](https://id.atlassian.com/manage-profile/security/api-tokens)
   - **Asana**: [Personal Access Token](https://app.asana.com/0/my-apps), workspace GID
   - **Google Sheets**: a service account (JSON key) from [Google Cloud Console](https://console.cloud.google.com/),
     the sheet ID and range (`GOOGLE_SHEET_RANGE`) — remember to **share the sheet** with the
     service account's e-mail address (as Editor if the scripts should write Jira keys).
   - recommended: a **template project** `JIRA_TEMPLATE_PROJECT_KEY` (section 4) - new
     projects get its workflow, screens and fields, and the panel takes the statuses for
     section mapping from it; with several template boards also `JIRA_TEMPLATE_BOARD_ID`.

   `.env.example` describes every variable; only the Jira, Asana and Google sections are
   required. An empty value (`X=`) is not the same as a missing variable - some scripts
   have defaults only for an absent variable, so keep unused options commented out.

   To quickly check whether `ASANA_TOKEN` actually works (regardless of
   access to a specific project — useful with `403` errors):
   ```bash
   python check_asana_token.py
   ```

### What is not in the repository

The repository contains only code and documentation. Data files are created
locally while you work and are excluded in `.gitignore`: `.env`, the Google key
(`service_account.json`), the browser session (`auth_state.json`), state and cache files (`*.json`, section 16),
`*.csv` reports, `*.xlsx` sheets, `*.txt` project lists (e.g. `sync_exclude.txt`,
`protected_projects.txt`), instance-specific YAML configurations and the
`logs/`, `locks/`, `pipeline_summaries/`, `diagnostics/`, `repair_reports/`
directories. Project keys, instance and group names in the examples (`PROJ`,
`KLUCZ` = "KEY", `twoja-instancja.atlassian.net`, `KlientX`) are placeholders —
use your own.

## 3. Board type

By default **all created projects are company-managed Kanban boards**
(classic, centrally managed by a Jira admin — creating projects of this type
requires administrative permissions). If you prefer team-managed (simpler,
configured by the team itself), `.env.example` contains a ready, commented-out
line with the matching template key.

## 4. Copying configuration from a template project

If you want new projects to have **the same workflow, screen layout and fields**
as an existing Jira project, set in `.env`:

```
JIRA_TEMPLATE_PROJECT_KEY=YOURKEY
```

For every newly created project the script will then copy from the template project:

- **workflow** (workflow scheme),
- **layout** — the screens assigned to issue types (issue type screen scheme),
- **fields** — field configuration (field configuration scheme),
- **issue types** — issue type scheme,
- permissions, notifications and security scheme (if the template project has
  custom ones — otherwise the Jira default is used).

**Limitations worth knowing:**
- Works **only for company-managed (classic) projects** — which is our
  default type anyway (see section 3).
- Requires **Jira admin permissions** (Administer Jira global permission) —
  without them Jira returns 403 when assigning the workflow/layout/fields.
- Workflow, layout and fields are assigned in a separate step **right after**
  the empty project is created — this only works while the project has no
  issues, which is always true for a freshly created project.
- If assigning any of the schemes fails (e.g. missing permissions), the script
  **does not delete** the created project — it reports a warning in the
  summary so you can fix it by hand in Jira.
- `--dry-run` does not test the configuration copy itself (the project does not
  exist yet) — it only shows that the configuration would be copied.

**Important limitation: Kanban board columns.** Jira **has no public API for
setting board columns** — the only part of the visual configuration that cannot
be copied automatically. Workflow, statuses, screens and fields will match the
template, but the new board gets Jira's default column layout (usually one
status = one column). To make recreating the template layout by hand easier, run:
```bash
python show_template_board_columns.py
```
It prints the exact column layout (names + which statuses are in which column) of
the template board — just drag the statuses into the same places on the new board
(Board → Configure → Columns), which takes a moment. With many projects, see
`browser_automation/` — Playwright automation for this step.

## 4a. Full copy of ONE project, INCLUDING boards and columns (`copy_project.py`)

> For a project copy with **its own** schemes (named after the new project), without a
> browser and without other scripts, use `clone_project.py` - the "Copy project" tab in the
> panel (part I). This section describes the older `copy_project.py`, which attaches the
> same schemes as the source.

A separate script for a different scenario than the main `jira_sync.py` (bulk
creation from the sheet) — it copies **one specific, existing** Jira project in
full, including what section 4 cannot do automatically: **all boards** of the
source project, their **column layout** and **status-to-column assignment**
(via the same internal GraphQL/GreenHopper mechanism as
`run_column_assignments.py` — see section 11b, built in directly so it does not
depend on the exact version of that file).

```bash
python copy_project.py --source ABC --target-key XYZ --target-name "New Project" \
    --lead-account-id 5b10a2844c20165700ede21 --dry-run   # preview the plan

python copy_project.py --source ABC --target-key XYZ --target-name "New Project" \
    --lead-account-id 5b10a2844c20165700ede21             # actually create
```

**Board filter sharing.** A board is visible only to people who can see its
filter. The new filter gets the same sharing and edit permissions as the source
filter, with the source project replaced by the new one (sharing with a role in
the source project becomes sharing with that role in the new project; groups and
other shares stay unchanged). In addition, the filter is always shared with the
new project, even if the source one was private. Projects copied with an older
version of the script (the team cannot see the boards) are fixed by
`fix_board_filter_permissions.py` *(script not included in this repository)*:
```bash
python fix_board_filter_permissions.py --project-keys XYZ                      # report
python fix_board_filter_permissions.py --project-keys XYZ --apply --editable   # share + edit permission
```
The warning `tablica jest przypisana do projektu ...` ("the board is assigned to
project ...") means the board will not show up in the project menu; change it in
Board settings → General → Location.

**Project lead** (`--lead-account-id`) must be a valid account ID (24 characters
or the `712020:...` format) with Jira access, otherwise Jira returns
`projectLead: You must specify a valid project lead`.

**Deliberately skips** roles/members (developers, administrators) — add them by
hand after creation, or use `DEVELOPER_GROUP_NAME`/`ADMIN_ACCOUNT_ID` in `.env`
as with regular `jira_sync.py` (sections 5–6).

Useful flags:
- **`--board-ids 123,456`** — skips the automatic (slow on a large instance)
  discovery of source boards and uses the given IDs directly. The first run
  without this flag prints the found IDs at the end — worth noting them down.
- **`--skip-project-creation`** — assumes the target project already exists
  (e.g. from a previous, interrupted attempt) and goes straight to boards/columns/
  statuses, skipping project creation and scheme assignment.

Requires a saved browser session (`auth_state.json` —
`browser_automation/save_login_session.py`), because assigning statuses to
columns uses the same unofficial internal API as `run_column_assignments.py` —
same stability caveats (section 11b). Also requires `CLOUD_ID` in `.env` (the
Jira Cloud instance ID, used by the GraphQL queries).

### Project statuses (`list_project_statuses.py`)

Prints the statuses available in a project for each issue type. Accepts a key
or any Jira link (board, backlog, settings, issue, `?selectedIssue=`):
```bash
python list_project_statuses.py PROJ3
python list_project_statuses.py "https://twoja-instancja.atlassian.net/jira/software/c/projects/PROJ3/boards/123"
```
Useful for status maps (section 11 and copying from another instance): the maps
use **status names**, not board column names.

### Removing a status from a board column (`remove_status_from_column.py`)

Moves the given status from a board column to unmapped statuses, on selected
or all boards of the projects in the sheet. Uses the same browser mechanism as
`copy_project.py` and verifies the whole board configuration after saving.
```bash
python remove_status_from_column.py                                                  # preview; by default status "Backlog" from column "Backlog"
python remove_status_from_column.py --column "Done" --status "Closed"                 # preview for another pair
python remove_status_from_column.py --column "Done" --status "Closed" --apply
python remove_status_from_column.py --board-ids 8351,8352 --apply                    # selected boards
```
Boards already changed are shown as "bez zmian" (unchanged) on a re-run, so an
interrupted run can simply be started again. Projects from
`protected_projects.txt` are skipped, also with `--board-ids`.

### When saving columns stops working

Assigning statuses to columns (`copy_project.py`, `remove_status_from_column.py`,
pipeline step 3) uses an internal GraphQL query of the Jira UI. Errors and their
causes:

| Message | Cause | What to do |
|---|---|---|
| `... -> sesja przeglądarki wygasła` (browser session expired; response `UNAUTHENTICATED`) | the session saved by `save_login_session.py` expired during a long run | `python browser_automation/save_login_session.py` and run again. `remove_status_from_column.py` stops at the first such error (`[PRZERWANO]` = aborted) |
| `Brak poprawnej odpowiedzi GraphQL po 4 próbach ... pustą treścią` (no valid GraphQL response after 4 attempts, empty body) | overload or rate limiting (e.g. several large processes at once) | wait a few minutes and retry; the script itself retries an empty read up to 4 times |
| `... Jira nie zna już skrótu zapytania` (`PersistedQueryNotFound`) | Atlassian changed the persisted query hash in a UI update | capture the new hash (below) |

**New query hash:** in the browser open any board → Board settings → Columns,
DevTools (F12) → Network, filter `ColumnsSettings`, reload the page. The request
URL `.../gateway/api/graphql/pq/<HASH>?operation=<NAME>` contains the hash and
the operation name. Put them in `.env`:
```
GRAPHQL_QUERY_HASH=<HASH>
GRAPHQL_OPERATION=<NAME>      # only if the name changed too
```
The same hash is used by `run_column_assignments.py` / `assign_statuses_to_columns.js`
(section 11b).

### Copying a project from ANOTHER Jira instance (`copy_project_cross_instance.py`)

Moves the **content** of a project from someone else's Jira instance to ours:
issues with type and hierarchy (Epic → Task → Sub-task, parents created before
children), descriptions, comments (the whole thread), attachments, reporter and
assignee, **labels**, **priority** and status according to a map or rule. **Does
not move configuration** (custom fields and their values, workflows, screens,
schemes), due dates, original dates or links between issues.

#### Access to the source

In `.env` (the target instance is the regular `JIRA_*`):
```
SOURCE_JIRA_BASE_URL=https://ich-firma.atlassian.net
SOURCE_JIRA_EMAIL=...          # the same Atlassian account, if you have access there with this e-mail
SOURCE_JIRA_API_TOKEN=...      # a regular token, NOT a "scoped" token
```
The token belongs to the Atlassian account, not to the instance, so with the same
e-mail it works in both. A separate token with a clear name is recommended, to be
deleted after the migration. Tokens expire: at startup the script checks the login
(`/myself`) and the visibility of the source project. On `HTTP 401` or a missing
project it stops immediately with `[BŁĄD] Instancja źródłowa ... odrzuca logowanie`
("source instance rejects the login"). Without this check the search would return
an empty list as for an anonymous user and look like "0 tasks in the source".

#### Target project

**Best to create it beforehand** by hand, with the "Share settings with an
existing project" option and our template project. It then gets our standard
workflow and schemes, and the script only fills it. For an **existing** project
`--target-name` and `--target-lead-account-id` are unnecessary (lead and name
stay unchanged, the lead is not added to administrators). When the project does
not exist, the script creates it with the instance's default configuration and
then both parameters are required. If the project already has our board, add
`--skip-boards`.

When setting statuses for the first time, the script builds a map of workflow
transitions once, using a few temporary issues that it deletes right away. Hence
gaps in numbering (e.g. `CJD-2` to `CJD-7`). In an active project they may trigger
automation rules.

**Target project with existing issues:** the preview shows how many source tasks
have a title identical to an existing issue in the target (e.g. moved earlier by
hand). `--skip-existing-titles` skips them together with their subtasks. Without
this option everything is copied. When copying into an active project, it is also
worth asking an administrator to pause automation temporarily, and remember that
Jira always e-mails assignees when issues are created via the API.

#### Recreating the source workflow (optional, before copying tasks)

When the target project should have **the same workflow as the source** (instead
of our standard one), recreate it in three steps before copying tasks. The account
in the source instance must be an administrator there, otherwise the workflow
cannot be read.

1. **Export the workflow** (`export_source_workflows.py`, read-only on the source) *(script not included in this repository)*:
   ```bash
   python export_source_workflows.py --source-key KEY
   python export_source_workflows.py --source-key KEY --name-prefix "KEY – " --out jira_schema_key.yaml
   ```
   This produces `jira_schema_<key>.yaml` (statuses with categories, workflows with
   transitions, ids and diagram layout, type → workflow mapping) and
   `status_map_<key>.json` (1:1 status mapping, ready for `--status-map`).
   Works for company- and team-managed projects. It **does not copy** transition
   rules (conditions, validators, post-functions), because they refer to groups
   and fields of that instance; the script prints them at the end for manual
   recreation.
2. **Workflow and scheme on our side** (section 17):
   ```bash
   python build_jira_schema.py --schema jira_schema_key.yaml --only statuses_workflow,workflow_scheme --dry-run
   python build_jira_schema.py --schema jira_schema_key.yaml --only statuses_workflow,workflow_scheme
   ```
   Check the exact spelling of statuses first: a status that differs by one
   character will be created as a new one.
3. **An empty company project** with the "KEY – Workflow Scheme" scheme attached
   (without issues Jira does not ask about status migration). Then copy the tasks
   with `--status-map status_map_<key>.json`; the script detects the existing
   project and uses it.

Skipping step 3 means `copy_project_cross_instance.py` will create the project
with the instance's default workflow.

#### Statuses

A file with a map of **status names** (not board columns) or a rule based on the
status **category** in the source; an entry from the file takes precedence:
```bash
--status-map status_map_projekt1.json      # {"Status in source": "Status on our side"}
--done-status Done --other-status Misc     # Done category -> Done, everything else -> Misc
```
Names must match character for character, including characters like `·` and `✓`.
Board columns are often named differently from the statuses in them (e.g. the
column "Review Kunde" contains the status "Customer Testing"), so check status
names with `python list_project_statuses.py KEY` or in the board column settings.
The preview prints the actual source statuses, the "target <- source" plan, and
checks that every target status exists. `(domyślny)` (default) in the plan means
a source status without a mapping. Note: adding a status to a workflow shared by
many projects changes it in all of them.

#### People

Reporter and assignee are set if the same person has an account on our side.
Matching: first `--user-map`, then the same Atlassian account ID, then e-mail.
The preview prints unmatched people with their ID in the source:
```json
{"accountId in source": "accountId on our side"}
```
**Name this map something other than `user_map.json`** (e.g. `wt_user_map.json`):
`user_map.json` is the **Asana** user map used by `asana_jira_sync.py`.
Setting the reporter requires the *Modify Reporter* permission, the assignee the
*Assignable User* permission. The creator and comment authors cannot be set via the API.

#### Origin note, labels, priorities, descriptions

- **A note (in German)** at the start of the description and a prefix on every comment:
  > *Übernommen aus ich-firma.atlassian.net · Original: WT-30 · erstellt am: 2025-03-14 · Melder: Pascal Probst · zugewiesen an: nicht zugewiesen*

  The texts are in the `ORIGIN_NOTE_TEMPLATE` and `COMMENT_PREFIX_TEMPLATE`
  constants; disable with `--no-origin-note`.
- **Labels** are moved 1:1 (spaces replaced with `_`, because Jira does not allow them).
- **Priority** by name, but only from the priorities **allowed in the target
  project** (the project's priority scheme). An unknown priority stays default and
  is listed in the summary (`Priorytety ze źródła nieznane u nas` = source
  priorities unknown on our side).
- **A description rejected by Jira** (elements pointing to the other instance) is
  first **cleaned**: mention → "@First Last", image/file → "[Anhang: name]",
  link card → plain link, emoji/status/date → text (`[INFO] Opis oczyszczony` =
  description cleaned). Only if that fails too does just the note remain.
- A field rejected by the screen or scheme (labels, priority, people) is skipped,
  and the issue is created anyway.

#### Splitting one source into several projects

`--only-statuses` / `--exclude-statuses` (**source** status names, comma-separated)
select part of the issues. The script then runs once per target project, usually
with the same list once in `--only-statuses` and once in `--exclude-statuses`.
Rules that keep the hierarchy intact:

| Element | Where it goes |
|---|---|
| Task, Bug, Story etc. | according to its **own** status |
| Sub-task | **together with its parent**, regardless of its own status |
| Epic with children | to **every** project that receives at least one of its children (no empty duplicates) |
| Epic without children | according to its own status |

When splitting, the copy state is kept separately for each `source>target` pair
(e.g. `WEB>CJD`, `WEB>PROJ4`), so the same Epic can exist in two projects.
Sub-tasks go with their parent, so the status map of the project that receives
the parent must also cover the subtask statuses from the other group (otherwise
`(domyślny)` appears in the plan).

#### Incremental copy (new items from the source)

Re-running **the same command**:
- creates only issues that are not yet in the copy state,
- sends new comments and attachments to already copied issues,
- with `--update-status` changes the status of already copied issues when it
  changed in the source (the last applied status is saved in the state, so
  subsequent runs are fast),
- with `--refresh-existing` fills in already copied issues: adds labels, sets
  priority and replaces the description **only** where it is just the note today,
- when splitting into projects, **does not duplicate** a task that moved in the
  source to a status of the other group: it stays in the project it originally
  went to, and the script lists it as `Pomijam N zadań skopiowanych już do innego
  projektu` (skipping N tasks already copied to another project). Its status is
  then not updated; you decide about moving it by hand.

The preview (`--dry-run`) distinguishes: `NOWE do utworzenia` (new, to be created,
with a list), `Już skopiowane` (already copied), `Zmiana statusu już skopiowanych`
(status change of already copied) and `Skopiowane przed --update-status` (copied
before `--update-status`; on the first use of this option the status of each of
them is checked once).

#### Permissions

Before copying issues the script adds the same groups and roles as during the
migration (from `.env`, sections 5-6). Extra groups:
`--extra-group "Group name=Role name"` (can be repeated). For an already
configured project use `--skip-access`.

#### Ready-made commands

First copy of a whole project:
```bash
python copy_project_cross_instance.py \
    --source-key WT --target-key WT --target-name "Webshop Testing" \
    --target-lead-account-id "$DEFAULT_LEAD_ACCOUNT_ID" \
    --done-status Done --other-status Misc \
    --user-map wt_user_map.json --skip-boards --dry-run
```
Splitting WEB into two existing projects (CJD and PROJ4), also for pulling in
new items later:
```bash
python copy_project_cross_instance.py \
    --source-key WEB --target-key CJD \
    --only-statuses "CJD - Backlog,CJD - On Hold,CJD - Neu / Offen,CJD - In Arbeit" \
    --status-map status_map_projekt1.json \
    --user-map wt_user_map.json --skip-boards --skip-access --update-status --dry-run

python copy_project_cross_instance.py \
    --source-key WEB --target-key PROJ4 \
    --exclude-statuses "CJD - Backlog,CJD - On Hold,CJD - Neu / Offen,CJD - In Arbeit" \
    --status-map status_map_projekt2.json \
    --user-map wt_user_map.json --skip-boards --skip-access --update-status --dry-run
```
The same commands without `--dry-run` perform the copy. They can be safely
interrupted (`Ctrl+C`) and run again; the only exception is an interruption while
the comments of one issue are being copied, in which case some of its comments
will appear twice. It is safer to interrupt at the start of the next issue (the
line `[n/N] ... -> Utworzono ...` = created).

#### Coexisting with projects synced from Asana

Issues copied from another instance have no counterpart in Asana. All tools that
compare Jira with Asana (`compare_project_totals.py`, `relink_state.py`,
`handle_migrated_to_jira.py`) **skip** them based on
`cross_instance_copy_state.json` (message `Pomijam N zgłoszeń skopiowanych
z innej instancji Jiry` = skipping N issues copied from another Jira instance),
and `resolve_relink_duplicates.py` will never delete them. That is why
`cross_instance_copy_state.json` **must not be deleted** as long as the target
project is synced with Asana. The statuses of these issues come from the source,
so do not close them with `close_children_of_done.py --source jira`.

**Alternative without the API:** in the source, export "Excel CSV (all fields)",
on our side Settings → System → External system import → CSV. Attachments are
then only links to the source instance.

## 5. Automatically adding a developer group/users

If you want a specific Jira group **and/or** specific users to be added
automatically to the "Developers" role (or another role) in every newly created
project, set in `.env`:

```
DEVELOPER_GROUP_NAME=group-name
DEVELOPER_ACCOUNT_IDS=5b10a2844c20165700ede21,5c20a2844c20165700ede99
DEVELOPER_ROLE_NAME=Developers
```

Both (the group and the user list) work **independently** — you can use one,
the other, or both at once (two separate requests to the same role).

At startup the script checks once whether such a role exists in Jira, and if so —
after every successfully created project it adds the group to that role (it adds,
it does not overwrite other actors already assigned to the role). If a role with
the given name does not exist, the script prints a warning at startup and does not
add the group to any project (so it does not try pointlessly 550 times).

Requires *Administer Projects* permission for the created projects or *Administer
Jira* — the token used in `.env` must have it, otherwise you get a per-project
warning in the summary (the project is still created, just without the group —
you will have to add it by hand).

Not sure about the exact group or role name? Run:
```bash
python list_groups.py          # all groups (optionally filtered by part of the name)
python list_project_roles.py   # all project roles
```

## 6. Automatically adding administrator(s)

Similarly, you can add **one or several specific users** (e.g. yourself and a few
team members) to the administrator role in every newly created project:

```
ADMIN_ACCOUNT_ID=your-accountId
ADMIN_ROLE_NAME=Administrators
```

Several people at once — separate with commas (one API request per project,
regardless of the number of people):
```
ADMIN_ACCOUNT_ID=5b10a2844c20165700ede21,5c20a2844c20165700ede99
```

`ADMIN_ACCOUNT_ID` is an `accountId`, not an e-mail — find it with:
```bash
python get_my_account_id.py              # for yourself
python get_group_members.py "Group name" # for many people in a group at once
```

Works exactly like the developer group (section 5): the role is checked once at
startup, added after every created project, a warning instead of a hard error if
it fails.

**In addition:** you can make the lead of **every** project (different per
project — the `lead_account_id` column in the sheet, or `DEFAULT_LEAD_ACCOUNT_ID`)
automatically get administrator permissions **in their own** project, regardless
of the global `ADMIN_ACCOUNT_ID` list above:

```
ADD_LEAD_AS_ADMIN=true
```

Both mechanisms are combined into **one** API request per project (without
duplicates, if the lead also happens to be on the `ADMIN_ACCOUNT_ID` list).

## 7. Google Sheet format

The connection test in the panel (Start, step 2) checks the headers, the number of rows
and rows without a valid Asana link. The first row contains headers (case-insensitive). **`asana_project_link` is
strictly required.** Without it (or when access to the given link fails, e.g. a
private project the token cannot access) — **the whole row is skipped, the
project is NOT created in Jira** (not just without a description — not at all).
This is deliberate: name-based matching was removed as unreliable (see
`asana_project_link` below), so without a link there is no safe way to
unambiguously link the row to the right Asana project. The script prints a
combined summary of all skipped rows at the end of the run. `project_name` can be
left empty if you provided a link — the name is fetched automatically from Asana.
The remaining columns are optional:

| project_name              | jira_key | jira_link | project_type_key | template_key | lead_account_id       | description | board_template_id | asana_project_link |
|-----------------------------|----------|-----------|--------------------|-----------------|--------------------------|--------------|--------------------|----------------------|
| Q4 Campaign                 |          |           | software           |                 | 5b10a2844c20165700ede21 |              |                    | https://app.asana.com/0/1234567890123456/list |
| Homepage redesign           |          |           | business           |                 |                          | Redesign UI  | 7276               | https://app.asana.com/0/1111111111111111/list |
| *(empty — name pulled from the link)* |  |  |    |                 |                          |              |                    | https://app.asana.com/0/9876543210987654/list |

- **`board_template_id`** — used by `bulk_configure_columns.py` (not by
  `jira_sync.py`). The ID of the template board for the COLUMN LAYOUT of this
  particular project — useful when the template project has several boards with
  different layouts (e.g. `REFERENCE2` has both an 11-column template on board 6110
  and a simpler 4-column one on boards 7276/7273/7274/7275). Empty = the default
  template is used (`JIRA_TEMPLATE_BOARD_ID` from `.env`); filled = exactly this
  board is used — **per row**, so different projects can use different templates
  in one run.

- **`asana_project_link`** — a DIRECT link to the project in Asana (copied from
  the browser address bar). This is the **only** way the whole toolkit identifies
  an Asana project — name-based matching was **deliberately removed** (there were
  duplicate project names in Asana, which led to syncing with the wrong project —
  a costly lesson from painfully long debugging). Supported formats:
  `https://app.asana.com/0/<id>/list` and
  `https://app.asana.com/1/<workspace>/project/<id>/list` (and `/board` variants
  instead of `/list`). Used by `jira_sync.py` (description enrichment),
  `asana_jira_sync.py` and `jira_asana_sync.py` (task sync in both directions).

- **`jira_key` is written back to the sheet automatically** after it is generated
  (see section 9) — so even if you leave this field empty, after the first run you
  will have the keys ready to copy (e.g. for manual column/status configuration in Jira).

- **`jira_link` (optional)** — if you add this column to the header, the script
  automatically writes a ready, clickable link to the project there too
  (`.../browse/KEY`), together with `jira_key`. Without this column everything
  works exactly the same, just without links.

- `lead_account_id` is the user's `accountId` in Jira (not an e-mail!). You can find
  it e.g. via `GET /rest/api/3/user/search?query=email@company.com`, or more simply —
  run the included `python get_my_account_id.py email@company.com`. If the row has no
  lead, `DEFAULT_LEAD_ACCOUNT_ID` from `.env` is used (if set). **Jira requires a
  valid lead when creating a project** — if both fields are empty, the script
  reports a readable error instead of sending a request that would be rejected anyway.

## 8. Running

First a preview, without creating anything:
```bash
python jira_sync.py --dry-run
```

Actually creating the projects:
```bash
python jira_sync.py
```

The script is **idempotent** — if a project with the given key already exists in
Jira, it is skipped. So it can be run many times (e.g. every time you add a new row
to the sheet) without the risk of duplicates.

In parallel, in several terminals (each process takes its part of the sheet):
```bash
python jira_sync.py --shard 1/3
python jira_sync.py --shard 2/3
python jira_sync.py --shard 3/3
```
It is more convenient to do this via the orchestrator:
`python run_full_pipeline.py --only projects --parallel 3` (section 11a).

Exit code `2` means a **partial** failure (some projects were created, some were
not), `1` means a critical error (e.g. missing `.env`). On code `2` the
orchestrator continues the next steps for the projects that succeeded.

## 9. How key generation works

For a project name without a given `jira_key`:
1. Several words → the initials of each word (e.g. "Portal Klienta B2B" → `PKB`).
2. One word → the first up to 10 characters, uppercase.
3. **At least 6 characters** — if the result is shorter, it is padded with further
   letters from the name, and as a last resort with the letter "X" (e.g. "A" → `AXXXXX`).
4. If the generated key collides with one already assigned in the same run, in the
   cache, OR with an **actually existing** project in Jira (checked live via the
   API — not only against our local cache), a number is appended
   (`PKB2`, `PKB3`, ...) and the attempt is repeated.
5. The result is saved to `project_key_cache.json` (persistent between runs)
   **and back to the sheet** (the `jira_key` column) — with one batch request at the
   end of the whole run, not in `--dry-run` mode. Requires the Google service
   account to have **Editor** permissions on the sheet, not just Viewer, and the
   `jira_key` column to already exist in the header.

If you want full control over a specific key — just type it by hand in the
`jira_key` column for that row, and the script will not overwrite it.

**The cache is keyed by the Asana GID, not by the project name.** Two different
Asana projects can have identical names (e.g. two stages of the same contract,
"Miniaudit" and "Umsetzung", named the same in the sheet) — keying by name wrongly
assigned them THE SAME Jira key, mixing their tasks in one project (a costly
lesson: the `PROJ9` case, fixed). Every unique Asana link always gets its own,
separate key.

**An important Jira limitation, independent of the key:** Jira also requires
**unique project NAMES**, not just unique keys. If two rows in the sheet have the
same name (e.g. because of an empty `project_name`, pulled automatically from an
identically named Asana project), the second one will **always** get the error
`400 A project with that name already exists`, no matter how many different keys
the script tries. Solution: type different names by hand in the `project_name`
column for such rows (overrides the automatic pull from Asana — see section 7).

**Protection against a mistake in the sheet:** at the start of every run the script
checks whether a `jira_key` **typed by hand** in the sheet appears in two rows with
**different** `asana_project_link` values — if so, the whole script stops with a
readable error before anything gets created/mixed up (see
`check_duplicate_sheet_rows.py` in section 14 to check this by hand at any time).

## 10. What the script prints at the end

- the number of created, updated (description) and skipped projects,
- errors (e.g. missing permissions, missing lead, key or name already taken),
- warnings (e.g. failed to copy one of the schemes from the template project
  or to add a role),
- the list of rows skipped because of a missing `asana_project_link` or no access
  to the Asana project (these projects were **not** created),
- the path to the mapping file (`project_key_cache.json`).

When run through `run_full_pipeline.py`, the same numbers and error details go to
the combined step summary on Slack (section 13).

## 11. Task sync Asana → Jira (`asana_jira_sync.py`)

A separate script (next to `jira_sync.py`, which only creates *projects*) — it
syncs *tasks* inside already created projects. One-way: Asana → Jira (the reverse
direction is a separate script, `jira_asana_sync.py` — see section 12).

### What it syncs

- Title (safely truncated to 255 characters — the Jira limit — with the full
  version kept at the start of the description if the original was longer),
  description (with rich formatting: links, lists, bold/italic/underline/
  strikethrough), assignee, due date — always, and only if they actually changed.
- Status — via the Asana section, mapped to a Jira status (see below). Checked ON
  EVERY run, regardless of whether other fields changed (moving between sections
  does not always update the task's timestamp in Asana).
- Comments — only real ones (without system events like "changed the due date"),
  with a `[Asana — Author, date]` prefix in the body (Jira does not allow
  impersonating the original author without extra configuration).
- Attachments — downloaded from Asana and uploaded to Jira.
- **Subtasks and hierarchy**: Asana subtasks as Sub-tasks, and for tasks with
  three levels of nesting the full Epic → Task → Sub-task structure (details in
  the subsection below).
- **Fallback assignee** — if the Asana assignee cannot be assigned in the Jira project
  (none of their roles has "Assignable User" in the permission scheme), a NEW issue is
  created without an assignee (the project lead only when the project does not allow
  unassigned issues). In ALREADY created issues the assignee is left unchanged, and the
  summary, description and due date are saved as usual.
- **Formatting fallback** — if the HTML→ADF conversion produces a structure Jira
  rejects as invalid (rare, but it happens), the script automatically retries with
  the description as plain text (without formatting) instead of losing the whole task.
- **Tags, followers and custom fields (optional)** — enabled with `--tags`, `--watchers`,
  `--custom-fields` (or `SYNC_TAGS=1`, `SYNC_WATCHERS=1`, `SYNC_CUSTOM_FIELDS=1` in `.env`,
  which also covers the pipeline). Panel: checkboxes in step 5 and in Project migration.
  - Tags become Jira labels (spaces replaced with `_`). A tag removed in Asana removes its
    label, but only a label this sync added.
  - Followers become watchers through `user_map.json`; people without an entry are skipped
    and counted in the summary. Nobody is removed from watchers.
  - Custom fields follow `custom_field_map.json`, e.g.
    `{"Priority": "priority", "Team": "customfield_10050", "_projects": {"KEY": {"Estimate": "Story Points"}}}`
    (Jira field by name or ID). Without the file a field is matched to a Jira **custom** field
    (or Priority) with the same name. Supported Jira types: single and multi select (the
    option must exist in Jira), text and paragraph, number, date, date-time, user and
    multi-user (through `user_map.json`), labels, priority. The field must be on the project's
    edit screen; a rejected value is skipped with a warning and the rest is saved.
  - The last written set is kept in the state (`extras_applied`), so an unchanged task costs
    no Jira request. To write a rejected value again after fixing Jira, change it in Asana
    or remove `extras_applied` from that task's state entry.

### Task hierarchy: Epic → Task → Sub-task

Jira has three levels (Epic, Task, Sub-task), and a Sub-task cannot have its own
subtasks. Asana allows nesting arbitrarily deep, so the script applies these rules:

| In Asana | In Jira |
|---|---|
| task without sub-subtasks | Task |
| its subtask | Sub-task under that Task |
| task whose subtask has its own subtasks (Epic candidate) | Epic |
| subtask of the candidate | Task under the Epic |
| subtask of such a Task | Sub-task |
| deeper levels (arbitrarily deep) | Sub-task under the nearest ancestor that is not a Sub-task (flattening) |

The sync descends recursively into all levels and orders the project's task list
so that a parent is always processed before its child (Asana's project list also
returns subtasks added to the project, in any order). **Older versions** had two
bugs here: they skipped subtasks from the second level down (if the intermediate
subtask was not on the project list) and created a subtask as a standalone Task
when it came before its parent on the list. Projects synced earlier are checked
and fixed by section 15e, and missing tasks are pulled in by a regular re-sync.

Epic candidates are detected **before** the sync by `detect_epic_candidates.py`,
which saves their list to `epic_candidates.json`, read automatically by
`asana_jira_sync.py`:
```bash
python detect_epic_candidates.py                           # all projects from the sheet
python detect_epic_candidates.py --project-keys KEY1,KEY2
python detect_epic_candidates.py --shard 1/3               # in parallel
python detect_epic_candidates.py --project-keys KEY --force   # rescan even though the project is already in the file
```
By default the script **resumes**: it skips projects already saved in the file.
After the structure changes in Asana or after hierarchy repairs, use `--force` for
the affected projects.

A subtask that someone **additionally added to the project** in Asana shows up on
the project's task list like a regular task. The script recognizes it (it has a
parent in the same project) and does not treat it as a top-level task. Without this
filter such a subtask could become a false Epic; section 15 describes how to fix
Epics created before the filter was introduced.

The issue type and parent are set **only on creation**. Subsequent runs update the
content and status but do not rearrange the hierarchy. Structure changes of already
migrated tasks are done by the tools in section 15.

### How to avoid duplicates on repeated runs

A local `task_sync_state.json` file (created automatically, **do not delete
between runs**) remembers for each task: its Jira key, the last known `modified_at`
from Asana, the IDs of already transferred comments and attachments, and hierarchy
flags (`is_epic`, `is_subtask`, `top_level_ancestor_key`, i.e. where new subtasks
of a task that is a Sub-task go). The flags are refreshed after hierarchy repairs by
`refresh_state_flags.py` (section 15). Tasks deleted by hand in Jira can be marked
`deleted_in_jira` and are then permanently skipped (section 14e). It is saved
**after every single task** (not only after the whole project) — safe even if the
internet goes down halfway through a run (learned the hard way). Also safe with
several parallel instances running on DIFFERENT projects at once: each process
remembers a snapshot of the state from the moment it was loaded and writes **only
the entries it changed itself** (with a file lock). **Older versions** merged the
whole project as of start time, so parallel processes deleted each other's freshly
added entries. The result: lost links and, on later syncs, whole series of
duplicates. Such projects are fixed by the procedure in sections 15f-15i.

**The write is atomic** (temporary file + `os.replace`, with an `fcntl` lock) —
resistant to race conditions at high parallelism (`--parallel` in
`run_full_pipeline.py`): reading while another process is writing will not end in
corrupted JSON. If the file still gets corrupted at some point (e.g. after `Ctrl+C`
at the wrong moment), use `repair_state_file.py` (section 14).

**Retrying on transient errors** — all requests to Asana and Jira (including the
`429` rate limit, honouring the `Retry-After` header) are retried automatically
with increasing delay, instead of aborting the whole task right away.

### Link to Asana in every issue

Every issue gets a one-time **remote link** to its task in Asana (the "Links"
section in Jira, relation "Asana", identifier `asana:task:<gid>`). The `asana_link`
flag in the state prevents adding it on every run. The link lets the team jump to
the original and is the most reliable basis for restoring links (`relink_state.py`,
the `LINK` pass, section 15f). In the summary: `Linki do Asany dodane` (links to
Asana added).

### Excluding projects: `sync_exclude.txt` and `protected_projects.txt`

| File | Affects | When to use |
|---|---|---|
| `sync_exclude.txt` | sync only (both directions, also `--project-keys` and preview) | **temporarily**, while a project is being repaired; repair tools work normally |
| `protected_projects.txt` | sync, the pipeline and **all** tools that change data, also when the key is given explicitly | **permanently**, when a project must not be touched (e.g. the team now works only in Jira) |

Format of both: one key per line, `#` = comment:
```
PROJ3   # team works in Jira, no sync and no repairs
```
Projects from `protected_projects.txt` disappear from the sheet for all scripts
(`jira_sync.get_sheet_rows()`), and tools working on the state file or on explicitly
given keys (`refresh_state_flags`, `fix_stale_resolution`, `close_children_of_done`,
`relink_state`, `resolve_relink_duplicates`, `handle_migrated_to_jira`,
`audit_hierarchy`, `remove_status_from_column`, `repair_batch`) skip them with the
message `[CHRONIONE]` (protected). Consequence: the comparison will not show such a
project either; to look at it, temporarily comment out its line.

Removing a batch of projects from `sync_exclude.txt` without touching the rest of the file:
```bash
python3 - partia.txt << 'PY'
import sys, re
part = {re.sub(r"#.*", "", l).strip().upper() for l in open(sys.argv[1], encoding="utf-8")} - {""}
lines = open("sync_exclude.txt", encoding="utf-8").read().splitlines()
keep = [l for l in lines if re.sub(r"#.*", "", l).strip().upper() not in part]
open("sync_exclude.txt", "w", encoding="utf-8").write("\n".join(keep) + "\n")
print(f"Zdjęto linii: {len(lines) - len(keep)}")
PY
```
The message counts removed **lines**: if a project was listed twice, the number
will be higher than the number of projects. Duplicate entries are removed with
`awk '{k=$1} !seen[k]++' sync_exclude.txt > tmp && mv tmp sync_exclude.txt`.

### Tasks moved to Jira by hand ("migriert zu Jira")

Teams sometimes move a task to Jira by hand and mark it in Asana with a note, e.g.
"Agenda JF (migriert zu Jira)" (also with the typo "mirgiert"). For such tasks Jira
is the source of truth, so the sync skips them **together with their whole
subtree**: it neither creates nor updates them. The same applies to entries marked
in the state with the `jira_master` flag (set by `handle_migrated_to_jira.py`,
section 15h). The pattern (a case-insensitive regular expression) can be changed in `.env`:
```
MIGRATED_TO_JIRA_PATTERN=mi[rg]{1,2}r?iert\s+zu\s+jira
```
In the summary: `prowadzone w Jirze ('migriert zu Jira')` (maintained in Jira).
Worth telling the teams: when moving a task to Jira by hand, note it in Asana.

### Configuration before the first run

1. **`asana_project_link` in the sheet** — required for every row (see section 7).
   Without it the project is skipped with a readable warning.

2. **Required fields** — your template project may have fields that are mandatory
   when creating an issue (e.g. custom "Type", "Department") that Asana does not
   have. Check them:
   ```bash
   python check_required_fields.py PROJECTKEY
   ```
   The connection test in the panel lists such fields for the template project. Fields
   with a single allowed value are filled automatically. For one field with several
   values set `DEFAULT_TYPE_FIELD_ID` and `DEFAULT_TYPE_FIELD_VALUE`; when neither variable
   is in `.env`, the script uses `customfield_10188` = `NONBILLABLE` (the "Type" field of
   the instance the toolkit was built for), and an empty value switches this off.

3. **Asana section → Jira status mapping** — Asana sections are often stages/
   milestones (e.g. "3. Kickoff"), not names matching Jira statuses directly
   ("In Progress").

   **In the panel: "Status mapping"** (part I) - sections from all sheet projects ↔ the
   template project's statuses, only statuses that exist in the template, with
   suggestions and an explicit status for completed tasks (`__ukonczone__`).

   **From the terminal** - list the sections of **one** project:
   ```bash
   python list_asana_sections.py "Exact project name in Asana"
   python list_asana_sections.py --link "https://app.asana.com/0/123456789/list"
   ```
   Or, with many projects at once — list the **unique** sections from **all** sheet
   rows in one pass (in a ready `"section: example project": "",` format to paste,
   with an automatic comparison with what you have already mapped):
   ```bash
   python list_all_asana_sections.py
   python list_all_asana_sections.py --limit 10          # test on a sample
   python list_all_asana_sections.py --save wynik.json   # save to a file
   ```
   **Important:** a key pasted this way contains an appended example project
   (after the colon) — it is only a context hint, not the real section name. Before
   saving to `section_status_map.json`, cut off the part after the **last** `": "`
   (unless the real section name itself contains a colon — then check by hand which
   colon is "yours").

   Fill in `section_status_map.json` (key = the **clean** section name, value = the
   Jira status name **or a list of statuses**, e.g. `["In progress", "Feedback
   Required"]` — useful when one section in a simple project should sensibly map to
   several more detailed statuses in the reverse direction). This table is global —
   shared between all projects and BOTH sync directions; add entries as you discover
   new sections. A section without an entry = the task keeps the default starting
   status (the script prints a list of such sections at the end of the run). The
   section name is automatically **stripped of whitespace** at the start/end before
   comparing with the map — the section `"Prio "` (with a space, as happens in Asana)
   correctly matches the `"Prio"` entry in the map.

4. **User mapping** — without it tasks are imported without an assignee. **In the panel:
   "People mapping"** (part I) - Asana people ↔ accounts from selected Jira groups, with
   suggestions; people without an account are marked "not in Jira"
   (`asana_users_not_in_jira.json`). An issue can only be assigned to a person whose
   project role has "Assignable User" in the permission scheme - mapping alone (or being
   the project lead) does not grant it; "Statuses and people" in the panel shows the scheme
   and the roles with that permission. From the terminal:
   ```bash
   python generate_user_map.py
   ```
   It matches automatically by e-mail address; if the addresses in Jira follow a
   fixed pattern (`First.Last@domain`, different from the address in Asana), the
   script also tries to build such an address from the first/last name (with a
   transliterated variant of the German characters ü/ö/ä/ß). The domain is set by
   `JIRA_EMAIL_DOMAIN` in `.env` (if unset: the domain of the instance the toolkit was built for). By default it **skips
   Asana guests** — to include them: `python generate_user_map.py --include-guests`.
   Result: `user_map.json` (**overwritten entirely** - which is why the "not in Jira" list
   lives in a separate file). People not matched automatically can be added to this
   file by hand (`"asana_user_gid": "jira_accountId"`), and the Asana gid can be
   found with `python list_asana_members.py`.

### Statuses: section map, project maps, completed tasks and subtasks

**Tasks on the project list** get a status according to their Asana section on
**every** run (not only on creation), from `section_status_map.json`. A task
**checked off** in Asana gets the final status regardless of the section.

**The final status** is the `"__ukonczone__"` entry: first from the project map, then from
the main map, and if neither has it - `Fertig` (a status of the instance the toolkit was
built for; on another Jira set `__ukonczone__` in the main map - the panel does this in
"Status mapping"). If that status does not exist in the project, the issue gets the status
from its section and the script prints a warning.

**A map for a single project:** when a project has statuses other than the standard
ones, or the same section names should lead somewhere else in it, create
`section_status_map.<KEY>.json` next to the main map (two dots, the key in uppercase,
exactly as in Jira). Its entries take precedence, but only in that project; the main
map stays untouched. The `"__ukonczone__"` ("completed") entry gives the status for
tasks checked off in Asana in that project (takes precedence over the main map; if neither has it - `Fertig`):
```json
{
  "__ukonczone__": "Live",
  "Backlog / Anfragen": "Backlog",
  "Setup": "Setup",
  "Inhalt finalisieren": "Finalize Configuration",
  "TWINT Aktivierung (Live)": "Refinement",
  "Live": "Live"
}
```
Checking whether the file is loaded (an empty dict = wrong file name):
```bash
python3 -c "import asana_jira_sync as a; print(a.project_section_overrides('PROJ'))"
```
The reverse direction (Jira → Asana) uses the same file. Disabling a project map:
rename the file (e.g. append `.wylaczona`). Before going back to the main map, make
sure the project's workflow has the statuses it points to.

**Subtasks** (all levels):

| In Asana | Status in Jira |
|---|---|
| subtask checked off | final status (`__ukonczone__` of the project, of the main map, or `Fertig`) |
| open subtask under a **checked-off** task (at any level) | final status |
| open subtask with its **own** section (also added to the project list) | according to its section (takes precedence) |
| other open subtasks | the status of the top-level task according to its section |

The last applied status is saved in the state (`status_applied`), so it is changed
only when the result changes. The first run after this change sets the statuses of
**all** subtasks of the project.

Sections without a mapping are listed at the end of the summary ("Sekcje BEZ wpisu
w section_status_map.json" = sections WITHOUT an entry). Variants with typos are
added to the map as separate keys with the same status, safely e.g. like this:
```bash
python3 -c "import json; p='section_status_map.json'; m=json.load(open(p,encoding='utf-8')); m['Whishlist (PM & Lead)']='To Clarify'; json.dump(m,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=2)"
```
A status unreachable in the workflow (no transition, e.g. from "Setup" to "Backlog")
gives a warning and `Statusy błąd` (status errors) in the summary. Solution: a
transition "from any status" in the workflow, or a manual status change.

### Run summary

| Line | Meaning |
|---|---|
| `Utworzono` / `Zaktualizowano` (created / updated) | broken down into Epics, Tasks, Sub-tasks |
| `Linki do Asany dodane` | new remote links (section above) |
| `bez zmian od ostatniego syncu` (unchanged since last sync) | the task in Asana did not change (including subtasks also on the project list, handled together with the parent) |
| `usunięte w Jirze (deleted_in_jira)` | deliberately skipped (section 14h) |
| `kandydaci na Epic do retrofit_epics.py` | the task should be an Epic but already has migrated subtasks; optionally section 15a |
| `prowadzone w Jirze ('migriert zu Jira')` | skipped with the whole subtree |
| `Zadań-podzadań z własnymi podzadaniami (spłaszczone pod przodka)` | Asana nests deeper than Jira; their subtasks go under the nearest ancestor that is not a Sub-task (formerly a separate `[INFO]` line for each such task) |
| `Statusy OK` / `Statusy błąd` | statuses set / unreachable |

### Running

```bash
python asana_jira_sync.py --dry-run --limit 1 --project-keys KEY   # preview without writing
python asana_jira_sync.py --limit 1 --project-keys KEY             # actual write, 1 project
python asana_jira_sync.py                                           # all projects from the sheet
python asana_jira_sync.py --shard 1/3                               # 1st of 3 parallel processes
python asana_jira_sync.py --project-keys KEY --tags --watchers --custom-fields  # with tags, followers, fields
```

At the end the script prints a summary broken down by type, e.g.
`Utworzono: 186 (Epiki: 6, Taski: 60, Sub-taski: 120)`, likewise for updated items,
comments, attachments, statuses and errors, plus a list of Asana sections without an
entry in `section_status_map.json`. Skipped items are broken down by reason:

| Reason | Meaning |
|---|---|
| unchanged since last sync | normal; includes subtasks also visible on the project list, handled together with the parent |
| deleted in Jira (`deleted_in_jira`) | will not be recreated; if the Jira project was created from scratch, clear its entries in the state (`clean_stale_task_state.py`) |
| Epic candidates for `retrofit_epics.py` | the task should be an Epic and its subtasks already exist (section 15a) |

`--dry-run` shows what the script **would** do (reading is safe, so even in dry-run
you see the real number of comments/attachments to transfer), without actually
creating/changing anything in Jira.

**Diagnostics:** set `DEBUG_SYNC=1` before the command to see detailed information
about every task (the exact data sent to Jira, section/status, timestamp
comparisons) — useful when something does not work as expected:
```bash
DEBUG_SYNC=1 python asana_jira_sync.py --limit 1 --project-keys KEY
```

### Files in this toolkit

| File | Role |
|---|---|
| `asana_jira_sync.py` | the main Asana → Jira sync script |
| `run_full_pipeline.py` | orchestrator of the whole pipeline, parallelism, combined summaries (section 11a) |
| `pipeline_summary.py` | shared module: records counts and error details for the orchestrator |
| `detect_epic_candidates.py` | detects Epic candidates before the sync |
| `epic_candidates.json` | **automatically created** list of Epic candidates per project |
| `copy_project_cross_instance.py` | copies a project between two Jira instances (section 4a) |
| `copy_project.py` | full copy of ONE existing Jira project, including boards/columns (section 4a) |
| `fix_board_filter_permissions.py` | shares board filters with the project when the team cannot see the boards (section 4a) *(script not included in this repository)* |
| `audit_hierarchy.py` | audits and fixes where issues sit in the hierarchy relative to the Asana tree (section 15e) |
| `compare_project_totals.py` | full Asana vs Jira project comparison, all levels, by ID (section 14a) |
| `jira_asana_sync.py` | reverse direction, Jira → Asana (section 12) |
| `list_asana_sections.py` | lists the sections of ONE Asana project (for `section_status_map.json`) |
| `list_all_asana_sections.py` | lists unique sections from ALL projects in the sheet at once, compared with those already mapped |
| `check_asana_token.py` | diagnostics: checks whether ASANA_TOKEN is valid and has access to the workspace (regardless of a specific project) |
| `section_status_map.json` | mapping: Asana section → Jira status(es) (panel: "Status mapping", or by hand) |
| `generate_user_map.py` | generates `user_map.json` (matching by e-mail) |
| `list_asana_members.py` | lists Asana workspace members/guests with their gid |
| `get_group_members.py` | lists Jira group members with their accountId |
| `user_map.json` | mapping: Asana user gid → Jira accountId (panel: "People mapping") |
| `check_required_fields.py` | diagnostics: which fields are required when creating an issue |
| `list_project_statuses.py` | diagnostics: which statuses actually exist in a given project |
| `check_task_section.py` | diagnostics: checks a task's section with two independent queries |
| `count_state_entries.py` | diagnostics: counts/shows entries in `task_sync_state.json` for a project |
| `count_migrated_tasks.py` | counts tasks in Jira (and optionally attachments), compares with the real number in Asana |
| `find_duplicate_tasks.py` | detects duplicate task titles within a project (detection only) |
| `resolve_duplicates.py` | detects AND safely deletes orphaned duplicate tasks (section 14) |
| `find_duplicate_comments_attachments.py` | detects duplicate comments/attachments INSIDE single tasks |
| `check_duplicate_sheet_rows.py` | diagnostics: Jira keys with more than one row in the sheet |
| `diagnose_jira_asana_gap.py` | diagnostics: which exact titles exist only in Jira / only in Asana |
| `trace_task_origins.py` | diagnostics: with two Asana sources under one Jira key, determines the origin of every task *(script not included in this repository)* |
| `clean_stale_task_state.py` | removes entries from `task_sync_state.json` that point to Jira issues that no longer exist |
| `repair_state_file.py` | repairs a corrupted (e.g. after `Ctrl+C`) `task_sync_state.json` |
| `repair_missing_state_entries.py` | restores missing entries in the state by matching tasks by title (section 14e) |
| `mark_deleted_as_skipped.py` | marks tasks deleted by hand in Jira as permanently skipped (section 14e) |
| `refresh_state_flags.py` | refreshes hierarchy flags in the state based on Jira (section 15) |
| `retrofit_epics.py`, `convert_to_epic.py` | rebuild the hierarchy of already migrated tasks (section 15) |
| `audit_epic_candidates.py`, `revert_false_epics.py`, `resolve_deep_chains.py` | detecting and fixing false Epics (section 15) |
| `close_children_of_done.py` | closes in Jira (optionally also in Asana) tasks left open under completed ones (section 14f) |
| `fix_stale_resolution.py` | clears a leftover Resolution in open issues (section 14g) |
| `relink_state.py` | restores lost links, detects series of duplicates (section 15f) |
| `resolve_relink_duplicates.py` | removes duplicates from the relink report, keeps the oldest copy (section 15g) |
| `handle_migrated_to_jira.py` | tidies up tasks marked "migriert zu Jira" (section 15h) |
| `repair_batch.py` | runs 15f-15h for many projects with a summary table (section 15i) |
| `remove_status_from_column.py` | removes a status from a board column (section 4a) |
| `sync_exclude.txt`, `protected_projects.txt` | temporary and permanent project exclusions (section 11) |
| `export_source_workflows.py` | exports workflows and statuses from another Jira instance to YAML and a status map (section 4a) *(script not included in this repository)* |
| `build_jira_schema.py` | Jira configuration from a YAML file (section 17) |
| `set_transition_groups.py` | who may perform a workflow transition (section 17j) |
| `parse_jira_pdfs.py`, `parse_jira_workflow.py`, `parse_jira_pptx.py`, `merge_jira_sources.py` | building the YAML file from client documentation (section 17) |
| `create_epics_stories_from_sheet.py`, `export_stories_to_sheet.py` | Epics and Stories from the scope sheet and export back (section 20) |
| `section_status_map.<KEY>.json` | status map for a single project (section 11) |
| `notify.py` | log to file + optional Slack notification |
| `start.sh` | installation (`.venv`, dependencies) and panel start |
| `board_columns.py` | board columns and status-to-column mapping through the API - pipeline steps 2-3 (section 11a) |
| `clone_project.py` | standalone project copy with scheme copies named after the new project (part I, "Copy project") |
| `migrate_project.py` | one project from an Asana link: create, configure, section map, board, tasks (part I, "Project migration") |
| `migration_app/asana_lookup.py` | read-only: name, sections and task counts of an Asana project (panel, "Project migration") |
| `LICENSE` | license: private, non-commercial use only; commercial use prohibited |
| `asana_users_not_in_jira.json` | Asana people deliberately without a Jira account (panel, "People mapping") |
| `task_sync_state.json` | **automatically created** sync state (both directions) — do not delete |
| `workflow_graph_cache.json` | **automatically created** cache of the workflow transition map — safe to delete |

## 11a. Order of steps and the whole pipeline in one command (`run_full_pipeline.py`)

The steps must run in this order, because each assumes the previous one has finished:

| Step | Key | Script | What it does |
|---|---|---|---|
| 1 | `projects` | `jira_sync.py` | creates projects from the sheet |
| 2 | `columns` | `board_columns.py` | board columns **and** the status-to-column mapping from the template board (the `board_template_id` column, otherwise `JIRA_TEMPLATE_BOARD_ID` / the template project's board), through the API - no browser |
| 3 | `statuses` | `board_columns.py --check` | read-only check that every board matches its template |
| 4 | `epics` | `detect_epic_candidates.py` | detects Epic candidates (section 11) |
| 5 | `sync` | `asana_jira_sync.py` | syncs tasks onto the ready, configured boards |

Optionally afterwards, as ongoing work requires: `python jira_asana_sync.py`
(reverse direction, section 12). It is not part of the orchestrator.

### Orchestrator

`run_full_pipeline.py` runs the scripts above as separate processes, in this order,
and stops at the first step that ends with a critical error. It does not change the
logic of any step.

```bash
python run_full_pipeline.py                            # the whole pipeline, sequentially
python run_full_pipeline.py --parallel 3               # each step as 3 parallel processes
python run_full_pipeline.py --dry-run                  # preview (step 2 shows current vs target boards, step 3 only reads)
python run_full_pipeline.py --limit 5                  # first 5 projects in the steps that support it
python run_full_pipeline.py --only sync --parallel 3   # just one step (--only takes exactly one)
python run_full_pipeline.py --skip columns --skip statuses
python run_full_pipeline.py --skip-columns             # skip steps 2-3
python run_full_pipeline.py --skip-sync                # skip steps 4-5
```

**Parallelism (`--parallel N`)** applies to each step separately: the orchestrator
starts N processes with an automatic `--shard K/N` and waits for all of them before
moving to the next step. The processes share the same Jira and Asana rate limits, so
a larger N does not speed things up proportionally. 3-5 is recommended. With
`--parallel` every output line has a process prefix (`[2/3] ...`).

**Exit codes:** `0` success, `2` partial failure (steps 1-3: the pipeline continues
for the projects that succeeded), any other code stops the pipeline. The next run
can skip finished steps with `--only` / `--skip`.

**Steps 2-3 and non-matching workflows:** a board is only written when the project's
workflow matches the template. If template statuses are missing from the project's
workflow, or the project has its own statuses on the board that the template does not
know (they would disappear from the board), the board is **skipped** with a warning
("workflow projektu nie pasuje do wzorca"). Set the right `board_template_id` in the
sheet, or write anyway with `python board_columns.py --project-keys KEY --force`. A board
that already matches the template is left untouched.

**Summaries are sent to Slack even in `--dry-run`** when `SLACK_WEBHOOK_URL` is set.

**A summary after each step** goes to Slack as one message for all processes:
status, time, summed counts and a list of errors (section 13).

## 11b. Board columns and statuses through the API (`board_columns.py`)

Jira has no **public, documented** API for board columns. The internal endpoint the Jira
UI itself uses - `PUT /rest/greenhopper/1.0/rapidviewconfig/columns` (the old
"GreenHopper" API), with `GET .../rapidviewconfig/editmodel.json` for reading - **accepts
a regular API token**, so `board_columns.py` (pipeline steps 2-3) and `clone_project.py`
set columns and the status-to-column mapping without a browser, in a single call per
board, and re-read the board afterwards to verify it.

```bash
python board_columns.py --dry-run                          # current vs target layout, writes nothing
python board_columns.py --project-keys KEY1,KEY2           # write for selected projects
python board_columns.py --check                            # read-only consistency check
python board_columns.py --project-keys KEY --template-board-id 1234   # another template
```

Statuses are matched by ID, and by name when the project's workflow lacks that ID. With
`--project-keys` the template comes from the sheet (the panel's cache), unless
`--template-board-id` is given.

**Caveat:** this is an UNOFFICIAL API - Atlassian may change it without warning. If the
write starts failing after a Jira update, the older browser tools are still in the
repository (`bulk_configure_columns.py`, `browser_automation/run_column_assignments.py`,
`assign_statuses_to_columns.js`; they need a saved browser session from
`browser_automation/save_login_session.py` and `playwright install chromium`).

## 12. Reverse sync direction (`jira_asana_sync.py`, Jira → Asana)

A separate script (not a mode of `asana_jira_sync.py`, because it iterates differently:
over the already linked pairs from `task_sync_state.json` — a file SHARED with
`asana_jira_sync.py` — not over Asana tasks).

### What it syncs

- Title, description (ADF → HTML conversion), due date, assignee (via the REVERSED
  `user_map.json`, with a fallback: if the original assignee cannot be assigned in
  Asana, the update goes through without them instead of failing).
- **Status → section** — via the REVERSED `section_status_map.json`. Since one Asana
  section can correspond to many Jira statuses (and conversely, many sections to the
  same status), the script **tries all candidates in turn** until it finds one that
  actually exists in the SPECIFIC Asana project — it does not assume the first one on
  the list always fits. Special entries (`__ukonczone__`, `__szablon__`) are skipped, and
  for extended entries (`{"status": ..., "labels": ...}`) the `status` is used.
- New comments and attachments from Jira to Asana.

**First run = starting point.** For a task the reverse direction does not know yet (no
`jira_updated` in the state, e.g. right after the Asana → Jira migration) the script only
records Jira's current state - the `updated` timestamp and the existing comments and
attachments - and changes **nothing** in Asana. From the next run on only changes made in
Jira after that point go to Asana. Without this the first run would overwrite every Asana
task and send back comments and attachments that had just come from Asana.

**No echo between directions:** `asana_jira_sync.py` records the IDs of the comments and
attachments it creates in Jira, and `jira_asana_sync.py` skips them (and the other way
round: what the reverse direction adds to Asana is not sent back to Jira). After writing to
Jira, the forward sync also refreshes the `updated` marker of tasks the reverse direction
already knows, so its own edits do not come back.

**Status → section** changes only when the issue changed in Jira since the last run, and a
task stays in its current section when that section maps to the same status (several
sections can share one status).

**Conflict resolution:** a simple comparison of the last modification timestamp in
Jira (`updated`) — **there is no full field merge** if both systems changed the same
task at the same time; the Jira state wins.

### Running

```bash
python jira_asana_sync.py --dry-run --project-keys ABC   # preview for one project
python jira_asana_sync.py --project-keys ABC              # 1st run: starting point only
python jira_asana_sync.py
python jira_asana_sync.py --project-keys XYZ --asana-link "https://app.asana.com/..."  # project outside the sheet
DEBUG_SYNC=1 python jira_asana_sync.py --limit 1   # with detailed diagnostics
```

With `--project-keys` the Asana project link is taken from the sheet (key from the
`jira_key` column or `project_key_cache.json`), so status → section works for selected
projects too - also when you pick projects in the panel. A project outside the sheet (e.g.
from the "Project migration" tab) needs `--asana-link`.

**Order when using both directions:** run them alternately (`asana_jira_sync.py`, then
`jira_asana_sync.py`), not in parallel — this avoids both overwriting each other while
running.

## 13. Additional features

- **Log to file + Slack summary** — the `notify.py` module, used by `jira_sync.py`,
  `asana_jira_sync.py` and `jira_asana_sync.py`. The log goes to
  `logs/<script>_<date-time>.log` automatically (nothing needs to be enabled).
  Slack is optional — set in `.env`:
  ```
  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
  ```
  Without it the scripts simply send nothing to Slack (no error). E-mail has not been
  implemented (it requires SMTP credentials).
- **Combined summaries in the pipeline**: when run through `run_full_pipeline.py`, the
  individual scripts do not send their own messages (they detect this by the
  `PIPELINE_RUN=1` environment variable set by the orchestrator). Instead, after each
  step one message arrives for all parallel processes:
  ```
  ⚠️ 1/5 Tworzenie projektów zakończony z częściowymi błędami
  Procesy: 3 | Czas: 4min 12s | Kody wyjścia: [0, 2, 0]
  utworzone: 15 | zaktualizowany opis: 3 | pominięte: 460 | błędy: 1 | ...

  Błędy (1):
  • DUPKEY Projekt Duplikat: 400 A project with that key already exists

  Pełny raport: logs/pipeline_projects_20260927_182054_raport.txt
  ```
  (In English: "Project creation finished with partial errors · Processes · Time ·
  Exit codes · created · description updated · skipped · errors · Full report".)
  Every number is summed separately across all processes (for task sync, among others,
  created and updated Epics, Tasks and Sub-tasks, comments, attachments). Identical
  errors are merged with a counter (`60x ... 429`). Slack shows up to 20 errors and 10
  warnings (the `SLACK_MAX_ERRORS` / `SLACK_MAX_WARNINGS` constants in
  `run_full_pipeline.py`); the full list goes to `logs/pipeline_<step>_<date>_raport.txt`.
  If a process crashed (e.g. a traceback), the message contains the last lines of its
  output. At the end of the whole pipeline a short 🏁 message arrives with the total time.

  Counts and error details are reported by `jira_sync.py`, `bulk_configure_columns.py`,
  `detect_epic_candidates.py` and `asana_jira_sync.py`. Step 3 reports status, time and
  any crash. For another script to join the summaries, it is enough to call
  `record_error(...)` / `record_warning(...)` where errors are caught and
  `write_shard_summary(...)` at the end of `main()` (the `pipeline_summary.py` module).
- **Column configuration (`bulk_configure_columns.py`)** recognizes an expired browser
  session (aborts the run with a message instead of timing out on every project), skips
  team-managed projects, picks the board belonging to the given project and reports the
  URL when Jira redirected elsewhere. On a timeout it saves an `error_<KEY>.png`
  screenshot. The wait time: `BULK_COLUMNS_TIMEOUT_MS` in `.env` (default 30000). If the
  timeout persists even with a fresh session, Jira has probably changed the page layout;
  the selectors are in the `SELECTORS` dictionary.
- **Updating existing projects** — `jira_sync.py` not only skips already existing
  projects, it also updates their **description** if it differs from the one in the
  sheet/Asana (see the summary: "Zaktualizowany opis" = description updated).

## 14. Verification and cleanup after the migration

After the main sync (section 11), especially after repeated, interrupted runs, it is
worth verifying the result and cleaning up any duplicates created by the loss/corruption
of `task_sync_state.json` during earlier attempts. The tools below are fully independent
of the main sync — they never modify `task_sync_state.json` (except
`clean_stale_task_state.py` and `repair_state_file.py`, which are explicitly meant for that).

### 14a. How many tasks are actually in Jira (`count_migrated_tasks.py`)

```bash
python count_migrated_tasks.py                     # total number of tasks + per project
python count_migrated_tasks.py --summary-only       # total only
python count_migrated_tasks.py --with-attachments   # also counts attachments (slower)
python count_migrated_tasks.py --compare-asana       # compares with the REAL number in Asana
python count_migrated_tasks.py --compare-full        # broader comparison with Asana (details: --help)
```

Counts via the official `/search/approximate-count` endpoint (one request per project —
Jira Cloud has no simpler way to count issues). `--compare-asana` independently verifies
the result through Asana and points out projects with a discrepancy (usually a sign of
remaining duplicates). **If a project has more than one row in the sheet** (see
`check_duplicate_sheet_rows.py` below) — the counts from ALL matching rows are summed, and
the fact that there are several rows is loudly flagged as needing manual verification,
not silently ignored.

**Full comparison of one project (`compare_project_totals.py`).** The tools above count
top-level tasks and direct subtasks, so they do not see gaps from the second level down
or tasks in the wrong place. This script counts all levels recursively, each task once,
and compares by the IDs in the state (duplicate titles do not confuse the result).
Read-only:
```bash
python compare_project_totals.py PROJ5
python compare_project_totals.py PROJ5,PROJ11 --details --attachments   # with content and attachment counts
python compare_project_totals.py --all --top-level                        # quick overview of the whole sheet (no subtasks)
python compare_project_totals.py --all --shard 1/3                        # full comparison of everything, in parallel (separate CSV per part)
```
Discrepancy categories: not migrated (with nesting level), in the state but missing in
Jira, marked as deleted, in Jira without a link, missing in Asana. Details in
`compare_project_totals_report.csv`. One Asana request per task, so a large project takes
a few minutes.

How to read the result:

| Pattern | Meaning | Fix |
|---|---|---|
| not migrated at level 1 = without a link (e.g. 742 and 742) | the state **lost the links**, the issues are in Jira | 15f; **do not sync** before the fix (it will create duplicates) |
| Jira has more than Asana, many without a link | repeated syncs already created **duplicates** | 15f and 15g |
| not migrated only from level 2, without a link 0 | missing deep subtasks (the old recursion bug) | 15e, then sync |
| In the state, but missing in Jira | issues deleted or moved in Jira | 14h |
| a few without a link, no counterpart in Asana | issues created by the team directly in Jira | nothing (the sync does not touch them) |

Issues copied from another Jira instance are skipped (the line `Pomijam N zgłoszeń
skopiowanych z innej instancji Jiry`), and projects from `protected_projects.txt` are not
visible at all.

### 14b. Detecting and deleting duplicate tasks (`resolve_duplicates.py`)

Duplicates appear when `task_sync_state.json` loses the entry for an already synced
task (e.g. after `Ctrl+C` at the wrong moment, before the atomic-write fix — section 11)
— the next sync run THEN creates a second copy of the same task in Jira, because it
finds no trace of the first one.

**Detection:** groups tasks within ONE project by identical title, then classifies each
group into one of three categories, cross-checking with `task_sync_state.json` and
comparing content (description, attachments, comments):

- **Safe to delete** — exactly one key of the group is currently tracked in the state
  (the rest are its orphaned copies with identical content), OR none is tracked but all
  copies have identical content (then the copy with the highest number — the newest —
  is kept).
- **To review** — the content differs in a way that cannot be resolved unambiguously
  automatically. This is the only category that needs manual attention; in practice it
  is usually a small fraction of all duplicates.
- **Confirmed different (no action)** — every key in the group has its OWN, independent
  entry in the state, i.e. by definition corresponds to a different task in Asana (e.g.
  recurring names of template items in different phases of the same rollout, or recurring
  tasks like "Weekly Statusbericht"). Purely informational.

```bash
python resolve_duplicates.py                    # all projects, report only
python resolve_duplicates.py --project KEY       # one project only
python resolve_duplicates.py --delete            # all projects, deletes (with ONE confirmation)
```

**In parallel** (scanning, the slowest part — it goes through every task in every project):
```bash
python resolve_duplicates.py --shard 1/5 &
python resolve_duplicates.py --shard 2/5 &
python resolve_duplicates.py --shard 3/5 &
python resolve_duplicates.py --shard 4/5 &
python resolve_duplicates.py --shard 5/5 &
wait
python resolve_duplicates.py --collect            # collects the results of all shards, report
python resolve_duplicates.py --collect --delete   # as above, plus deleting
```
`--delete` **does not combine** with `--shard` directly (a deliberate safety limitation —
deleting is always one final step with one confirmation, after `--collect`).

Deletion goes through Jira's official **Bulk Delete API** (up to 1000 issues per request,
asynchronous — the script polls for progress until it finishes, max. one hour; for large
batches Jira deletes issues single-threaded on its side, so it can be slow regardless of
our approach).

The full report (all three categories, untruncated) is always saved to
`duplicate_resolution_report.json`.

### 14c. Duplicate comments/attachments INSIDE a task

A separate, subtler case: a task may have only ONE Jira key (no duplicate of the whole
task) and still have the same comments/attachments added several times to THE SAME task
— because the state tracks SEPARATELY which specific comments/attachments have been synced.

```bash
python find_duplicate_comments_attachments.py --project KEY
python find_duplicate_comments_attachments.py               # all projects
```

Detection only — deletes nothing (report: `duplicate_comments_attachments_report.json`).

### 14d. Diagnostics for unusual cases

- **`check_duplicate_sheet_rows.py [KEY]`** — whether any Jira key has more than one row
  in the sheet (with different Asana links). Runs automatically at the start of
  `jira_sync.py` (section 9) — this script is the manual version to check at any time.
- **`diagnose_jira_asana_gap.py KEY`** — for one project, shows EXACTLY which task titles
  exist only in Jira (not just the number, like `--compare-asana`) — distinguishes "real
  surplus duplicates" from "a unique task whose counterpart disappeared from Asana" (e.g.
  deleted after a stage ended — this may be expected, not an error).
- **`trace_task_origins.py KEY`** *(script not included in this repository)* — when one
  Jira key has two different Asana sources in the sheet (see `check_duplicate_sheet_rows.py`),
  determines how many tasks in Jira come from which source, and whether any of them
  matches none of the currently existing tasks in either.

### 14e. Inconsistencies between the state and Jira

- **`mark_deleted_as_skipped.py`**: marks tasks deleted by hand in Jira with the
  `deleted_in_jira` flag in the state, so the sync does not try to update them (`404`
  errors) or recreate them. It treats deletion as a deliberate decision.
  `asana_jira_sync.py` also sets this flag itself when it gets a `404` while updating.
- **`repair_missing_state_entries.py`**: when a task exists in Jira but the state has no
  entry for it (which would end in a duplicate on the next sync), restores the entry by
  matching tasks by title. For bigger cases (lost links, series of duplicates) use
  `relink_state.py` and `resolve_relink_duplicates.py` (sections 15f-15g).
- **`clean_stale_task_state.py`**: the opposite case: removes entries from the state that
  point to tasks no longer in Jira.
- **`diagnose_cross_project_contamination.py`**: detects tasks that in a given project's
  state have a Jira key from **another** project.
- **`diagnose_missing_projects_in_state.py`**: projects from the sheet for which the state
  has no entries at all (e.g. the sync never ran for them).
- **`diagnose_subtask_gap.py`**, **`diagnose_attachment_gap.py`**: like
  `diagnose_jira_asana_gap.py`, but for subtasks and for attachments.
- **`diff_two_issues.py`**: compares the exact content of two Jira issues (useful for
  suspected duplicates in the "To review" category).

Options for each of them: `--help`.

### 14f. Subtasks under completed tasks (`close_children_of_done.py`)

Closes in Jira everything that is open under a **completed** task. Report only by default.

| Option | Effect |
|---|---|
| (no option) | report: open issues under completed tasks |
| `--apply` | closes them **in Jira** (final status: `__ukonczone__` from the project or main map, otherwise `Fertig`; or `--done-status`) |
| `--apply --also-asana` | additionally checks off the corresponding tasks **in Asana** (only after a successful change in Jira) |
| `--source asana` (default) | tree and "completed" according to Asana; flattening in Jira does not matter; requires the project to be in the sheet |
| `--source jira` | hierarchy and final status according to Jira |
| `--only-keys A-1,A-2` | test on selected issues |

```bash
python close_children_of_done.py --project-keys PROJ4                # report
python close_children_of_done.py --project-keys PROJ4 --apply        # Jira only
```
Since status inheritance was introduced (section 11), the sync does this itself; the tool
is mainly for checking (expected: 0 to close). Do not use `--source jira` in projects
copied from another Jira instance: their statuses come from the source.

### 14g. Struck-through keys in open issues (`fix_stale_resolution.py`)

An issue has an open status but the **Resolution** field set, so Jira shows it as closed
(struck-through key, it drops out of "unresolved" filters). This happens when an issue was
closed and then moved back (e.g. by the sync), and the workflow transition does not clear
the resolution.
```bash
python fix_stale_resolution.py --project-keys PROJ3,PROJ4           # report broken down by type
python fix_stale_resolution.py --project-keys PROJ3,PROJ4 --apply   # clean up, without notifications
```
The cleanup goes through editing the issue, so the Resolution field must be on the edit
screen of the given type (it often is not for Epics). The script checks this for each type
and only reports types without the field. An administrator can then temporarily add
Resolution to the edit screen. **The permanent fix** is in the workflow: transitions out of
final statuses should have the post-function "Update Issue Field: Resolution = None".

### 14h. Issues deleted by the team in Jira

When the team deletes tasks in Jira that still exist in Asana (e.g. cleaning up old
templates), the comparison shows them as "W stanie, ale zgłoszenia NIE MA w Jirze" (in the
state, but the issue is NOT in Jira). First check whether they were deleted (404) or moved
to another project (Jira keeps the old key as a redirect):
```bash
for k in KEY-2 KEY-519; do
  curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" "$JIRA_BASE_URL/rest/api/3/issue/$k?fields=project" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('$k ->', d.get('key', 'DELETED (404)'), d.get('fields', {}).get('project', {}).get('key', ''))"
done
```
Mark **deleted** ones in the state so the sync does not recreate them (the list from the
comparison report, each one checked again in Jira):
```bash
python3 - << 'PY'
import csv, shutil
from datetime import datetime
import asana_jira_sync as ajs
KEY = "PROJ3"
rows = [r for r in csv.DictReader(open("compare_project_totals_report.csv", encoding="utf-8"))
        if r["projekt"] == KEY and r["kategoria"] == "brak_w_jirze"]
shutil.copy2(ajs.STATE_FILE, f"task_sync_state.backup_{datetime.now():%Y%m%d_%H%M%S}_usuniete_{KEY}.json")
state = ajs.load_state(); ps = state[KEY]; marked = []
for r in rows:
    gid, key = r["asana_gid"], r["jira_key"]
    resp = ajs.request_with_retry("GET", f"{ajs.JIRA_BASE_URL}/rest/api/3/issue/{key}", auth=ajs.JIRA_AUTH, timeout=ajs.REQUEST_TIMEOUT)
    if resp.status_code == 404 and gid in ps and ps[gid].get("jira_key") == key:
        ps[gid]["deleted_in_jira"] = True
        marked.append(key)
ajs.save_state({KEY: ps})
print(f"Marked as deleted in Jira: {len(marked)} of {len(rows)}")
PY
```
The comparison will then show them under "Oznaczone jako usunięte w Jirze (celowo
pomijane)" (marked as deleted in Jira, deliberately skipped). Issues **moved** to another
project would still be updated by the sync under the old key (i.e. in the new project); if
that is not wanted, detach them (`deleted_in_jira` or `jira_master`). Worth telling the
teams: a task deleted in Jira is best also deleted in Asana, or marked "migriert zu Jira" there.

## 15. Repairing the hierarchy in already migrated projects

The hierarchy is set when issues are created, so a mistake in candidate detection or a
structure change in Asana requires a separate repair. All tools in this section have a
report mode (the default) and change Jira only with `--apply`.

### 15a. Projects migrated before Epic support (`retrofit_epics.py`)

Converts tasks that were migrated as plain Tasks into the full structure: Task → Epic, its
Sub-tasks → Tasks under the Epic, grandchildren → Sub-tasks. `asana_jira_sync.py` itself
points to this script when it hits an Epic candidate with already migrated subtasks:
```bash
python retrofit_epics.py --project-keys KEY
```
Supports `--shard`. The low-level operations (changing the type, re-parenting, Bulk Move
for types that cannot be changed by a regular edit) are in `convert_to_epic.py` and are
shared by the tools in this section.

The retrofit is **optional**: nothing is lost without it, only the structure changes
(flattened Sub-tasks become Tasks under an Epic). Since it changes the types of issues the
team may already be working on (a different view on the board and in filters, keys and
content unchanged), it is worth agreeing on the decision with the project's PM. Candidates
are easiest to collect from the sync summaries (lines `[POMINIĘTO] ... jest kandydatem na
Epic` = skipped, is an Epic candidate) and decide at the end for all projects. After the
retrofit: `refresh_state_flags.py --project-keys KEY --apply` and
`audit_hierarchy.py --project-keys KEY --full`. Changing a Sub-task to a Task uses the same
mechanism as the audit, including the required "Type" field.

### 15b. False Epics

Before the filter from section 11 was introduced, a subtask additionally added to the
project in Asana could become an Epic (e.g. `PROJ11-127`, in Asana a subtask of
`PROJ11-107`). The repair procedure, in this order:

1. **Audit** (read-only, saves `epic_candidates_audit.csv`):
   ```bash
   python audit_epic_candidates.py
   ```
2. **Re-detect candidates** for the affected projects, so the sync does not recreate the
   false Epics:
   ```bash
   python detect_epic_candidates.py --project-keys KEY1,KEY2 --force
   ```
3. **Report of what needs to be done** (state checked live in Jira, not from the CSV):
   ```bash
   python revert_false_epics.py --csv epic_candidates_audit.csv
   ```
   | Status | Meaning | Fix |
   |---|---|---|
   | `OK_JUZ_POPRAWNY` (already correct) | type and parent match | nothing |
   | `OK_SPLASZCZONE` (flattened) | the task hangs under an ancestor because the target parent is a Sub-task (after 15c) | nothing |
   | `NAPRAWIALNE_SAM_RODZIC` (fixable: parent only) | type correct, no parent (Epic) | `--apply` |
   | `NAPRAWIALNE_TYP_I_RODZIC` (fixable: type and parent) | Epic without children, parent is an Epic: change to Task | `--apply` |
   | `NAPRAWIALNE_SUBTASK_I_RODZIC` (fixable: subtask and parent) | parent is a regular task: change to Sub-task | `--apply` |
   | `WYMAGA_RECZNEJ_DECYZJI` (needs a manual decision) | Epic with children, parent is an Epic | `--apply-cascade` |
   | `WYMAGA_RECZNEJ_DECYZJI_GLEBOKA` (needs a manual decision, deep) | should be a Sub-task but has its own children (4+ levels) | step 5 |
   | `CEL_JEST_SUBTASKIEM`, `CEL_NIE_ISTNIEJE`, `KANDYDAT_NIE_ISTNIEJE` | unusual data | by hand |

   Full result: `revert_false_epics_report.csv`.
4. **Fix:**
   ```bash
   python revert_false_epics.py --csv epic_candidates_audit.csv --apply
   python revert_false_epics.py --csv epic_candidates_audit.csv --apply-cascade --only-keys KEY-1   # test on one
   python revert_false_epics.py --csv epic_candidates_audit.csv --apply-cascade
   ```
   The cascade first changes the type and parent of the task itself, and only then
   re-parents its children as Sub-tasks (the reverse order is impossible: a Sub-task
   cannot hang under an Epic). An error on one child does not block the others.
   `--dry-run` shows the operations without writing.
5. **Chains of 4+ levels** (`WYMAGA_RECZNEJ_DECYZJI_GLEBOKA`) are flattened: the task and
   all its children become Sub-tasks of the nearest stable ancestor. The script follows the
   chains itself (e.g. `92 → 97 → 106`) and also pulls in children from Jira that are not
   in the CSV:
   ```bash
   python diagnose_deep_chains.py --report revert_false_epics_report.csv   # what will be affected (read-only)
   python resolve_deep_chains.py --csv epic_candidates_audit.csv                              # plan
   python resolve_deep_chains.py --csv epic_candidates_audit.csv --apply --only-roots KEY-1   # test
   python resolve_deep_chains.py --csv epic_candidates_audit.csv --apply
   ```
   A deliberate compromise: one level of Asana grouping disappears in Jira. Content,
   comments and attachments remain.
6. **Check:** run `revert_false_epics.py --csv ...` again without `--apply`. All rows
   should have `OK_JUZ_POPRAWNY` or `OK_SPLASZCZONE`.
7. **Refresh the flags in the state** (section 15c).

### 15c. Refreshing the flags in the state (`refresh_state_flags.py`)

After any hierarchy change made directly in Jira (15a, 15b, by hand), the `is_epic` /
`is_subtask` / `top_level_ancestor_key` flags in `task_sync_state.json` are out of date.
Consequences: new subtasks from Asana go under the wrong parent or end in an error, and the
"updated" breakdown in the summary is inaccurate. The script sets the flags according to
Jira (types fetched in bulk via JQL, 100 issues per request):
```bash
python refresh_state_flags.py --project-keys KEY1,KEY2            # report
python refresh_state_flags.py --project-keys KEY1,KEY2 --apply    # write
python refresh_state_flags.py --apply                             # all projects in the state
```
Before writing it creates a `task_sync_state.backup_<date>.json` copy. It changes only these
three flags; the full list of changes is in `refresh_state_flags_report.csv`. **Do not run it
in parallel with a sync** in either direction.

After repairs, the first run of `jira_asana_sync.py` will treat all re-arranged issues as
changed (their `updated` field in Jira changed) and push their content to Asana once. The
content did not change, so this is harmless.

### 15d. Bulk Move diagnostics

Changing the type to Sub-task (or from a type that cannot be changed by a regular edit) goes
through the Bulk Move API. When moving **to the Sub-task type**, the parent must be the third
part of the mapping key, `"PROJECT,type_id,PARENT-KEY"`, not the `targetParentId` field
(otherwise Jira returns a vague `Invalid request payload`). This format is built into
`convert_to_epic.py`.

- `diagnose_subtask_types.py --project-keys KEY`: which subtask types exist in the project
  (read-only).
- `diagnose_bulk_move_variants.py --key KEY-1 --parent KEY-2`: tries several payload
  variants on one issue. **Note:** the first variant that works really moves the issue.
  Failed variants change nothing.

### 15e. Hierarchy audit and repair (`audit_hierarchy.py`)

Checks whether every migrated issue sits in Jira where the Asana tree says it should (the
same rules as in section 11). It also recognizes structures after the repairs from section
15b as correct. Projects without subtasks added to the project list are skipped with one
cheap query (`--full` forces a full check).

| Status | Meaning | Fix |
|---|---|---|
| `OK` | consistent | nothing |
| `SAMODZIELNY_TASK` (standalone task) | should be a Sub-task, is a standalone Task | `--apply` (its Sub-tasks move with it) |
| `ZLY_RODZIC_SUBTASK` (wrong parent, subtask) | Sub-task under the wrong parent | `--apply` |
| `TASK_BEZ_EPIKA` (task without Epic) | should be a Task under an Epic, has no parent | `--apply` |
| `SUBTASK_POD_EPIKIEM` (subtask under Epic) | should be a Task under the Epic, is a Sub-task under it | `--apply` |
| `WYMAGA_PRZEGLADU` (needs review) | another mismatch, e.g. a subtask pulled up to the top level in Asana | by hand (e.g. Jira: `•••` → Convert to issue) |
| `RODZIC_NIEZMIGROWANY` (parent not migrated) | the parent is not in Jira | the sync will pull it in |

Repairs run in a fixed order (first Sub-tasks under an Epic become Tasks, then Sub-tasks
are re-parented, then standalone Tasks), and the state is checked live before every change.
```bash
python audit_hierarchy.py                                          # report, projects from the sheet
python audit_hierarchy.py --project-keys A,B --apply --only-keys A-12   # test on one issue
python audit_hierarchy.py --project-keys A,B --apply
python refresh_state_flags.py --project-keys A,B --apply
python audit_hierarchy.py --project-keys A,B                       # check: all OK
```
Type changes go through a regular edit, and for old, migrated types (e.g. `Subtask` with
id 10059, whose edit is rejected) through **Bulk Move**. With Bulk Move, the required fields
of the target type (in our case the **"Type"** field, required for Tasks and Sub-tasks) are
filled in the same way as on creation: `DEFAULT_TYPE_FIELD_VALUE` from `.env` (default
`NONBILLABLE`) and fields with a single allowed value (Jira then requires
`inferFieldDefaults = false`, which the tool sets itself). The message `Bulk Move nie
zakończyło się w ciągu 60s` (Bulk Move did not finish within 60s) usually just means a slow
queue on Jira's side: the operation most often completes a moment later, and if not, the
issue will appear in the next report and another `--apply` will finish it. A long run can be
paused with `kill -STOP <PID>` and resumed with `kill -CONT <PID>` (the operation in progress
may then end up among the errors).

Report in `audit_hierarchy_report.csv` (with `--shard` each process overwrites the same
file). The repair changes the board view: re-parented issues show up under their parent
instead of as separate cards, so it is worth warning the team. We do not repair archived
projects (they are read-only and were accepted as they are).

### 15f. Restoring lost links (`relink_state.py`)

For issues that are in Jira but have no entry in the state (lost through the old
state-saving bug, section 11), it looks for the matching task in Asana. **Before the repair
the project must be in `sync_exclude.txt`**: a sync with an incomplete state would create
everything a second time.

Matching passes (in order, each working on what the previous ones did not match):

| Category | Rule | Written with |
|---|---|---|
| `LINK` | the issue has a remote link `asana:task:<gid>` | `--apply` |
| `PEWNE` (certain) | identical title, unambiguous pair, matching parent | `--apply` |
| `KOLEJNOSC` (order) | the same title repeated (e.g. "TODOs:" templates); resolved by creation order, separately for each group with an identical set of candidates (in practice: under the same parent). When there are **more** copies than tasks (repeated syncs created series of duplicates, including incomplete ones after an interrupted run), the oldest series is linked and further series go to `DUPLIKAT` | `--include-order` |
| `PODOBNE` (similar) | similar title, the parent must match | `--include-similar` |
| `TRESC` (content) | scoring: title, description, due date, assignee, completion, parent; threshold 5 points with a 1.5 lead | `--include-content` |
| `DUPLIKAT` (duplicate) | a second (further) copy of a task that already has a link | removed by 15g |
| `NADMIAROWA_KOPIA` (surplus copy) | a further copy of a task with a repeated title | removed by 15g |
| `NIEJEDNOZNACZNE` (ambiguous) | several candidates; the notes give the reason: "parent also unresolved" or "fewer copies than tasks" (then nothing is guessed) | by hand |
| `BRAK_W_ASANIE` (missing in Asana) | an issue without a counterpart (created in Jira or an old copy of a renamed task) | `--explain`, possibly 15g `--include-orphans` |
| `NIEZMIGROWANE` (not migrated) | an Asana task without an issue | the sync will pull it in |

**The "similar" rule** (tightened after wrong matches of short German titles): titles are
similar if all words of the shorter one occur in the longer one, and the longer one has at
most as many **extra** words as the shorter one has words (min. 1), or the titles are almost
identical (at least 85%).

| Pair | Result |
|---|---|
| "NB" ~ "GoLive NB" | similar (1 word added) |
| "DEV Breadcrumbs auf Collection-Pages" ~ "Breadcrumbs auf Collection-Pages" | similar |
| "Show breadcrumbs aktivieren" ~ "Show breadcrumb links aktivieren" | similar (92%) |
| "Artikel ... archiviert (mirgiert zu Jira)" ~ "Artikel ... archiviert" | similar |
| "[Produkte]" ~ "[Produkte] Produkte anlegen" | **not** (one-word heading + 2 words) |
| "Sale Bilder sind nicht lokalisiert" ~ "Newsletter Widget lokalisieren" | **not** |
| "Projekt-Beschreibung anpassen" ~ "Float Projekt anpassen" | **not** |

**Always review `PODOBNE` and `TRESC` before writing:**
```bash
grep -E "^(PODOBNE|TRESC)" relink_state_report.csv | cut -d, -f3,5,7     # Asana title, issue, Jira title / score
python relink_state.py --project-key KEY --explain KEY-509                # candidates and scoring for one issue
```

Commands:
```bash
python relink_state.py --project-key KEY                                  # report
python relink_state.py --project-key KEY --apply                          # LINK + PEWNE
python relink_state.py --project-key KEY --apply --include-order --include-similar --include-content
python relink_state.py --project-key KEY --report-file raporty/KEY.csv    # custom report file
```
After writing, every restored link gets a link to Asana, and the entry has its modification
date cleared, so the next sync refreshes the issue content from Asana. The `already_synced`
flags check comments and attachments already present in the issue, so they will not be
created a second time. At startup the tool checks the version of `asana_jira_sync.py` (an old
state-saving version = refusal), and skips issues copied from another instance and protected
projects.

### 15g. Removing duplicates from the report (`resolve_relink_duplicates.py`)

Deletes issues in the `DUPLIKAT` and `NADMIAROWA_KOPIA` categories of the latest
`relink_state.py` report. Rules:

- all copies of one task are grouped (there can be more than two),
- **the oldest** copy (by creation date) **is kept**; the state and the Asana link are moved
  to it, and the modification date is cleared (the sync will refresh its content),
- before deleting, **team work** is checked (comments and history changes by someone other
  than the sync account). A copy with work goes to `DO_DECYZJI` (to decide) and is not deleted,
- subtasks of the deleted copy that the kept issue does not have are **moved** under it before
  deletion (Bulk Move); if the move is not confirmed, the copy is not deleted,
- deletion goes from the deepest level upwards,
- it **never** deletes issues copied from another Jira instance (`POMINIĘTE` = skipped).

```bash
python resolve_relink_duplicates.py --project-key KEY                          # plan
python resolve_relink_duplicates.py --project-key KEY --apply --limit 3        # test
python resolve_relink_duplicates.py --project-key KEY --apply                  # asks for TAK (yes)
grep "^DO_DECYZJI" resolve_relink_duplicates_plan.csv | cut -d, -f3,6          # reasons for holding back
```
`--include-orphans` additionally deletes `BRAK_W_ASANIE`, but **only** when the report confirms
that every Asana task already has an issue (zero `NIEZMIGROWANE`, `NIEJEDNOZNACZNE` and unsaved
matches); otherwise it refuses. A copy that has subtasks linked to Asana goes to `DO_DECYZJI`.
`--relink-report` and `--plan-file` point to the project's files (used by 15i).

Typical reasons in `DO_DECYZJI` and what to do with them:

| Reason | What to do |
|---|---|
| comments/changes by team members | the team worked on this issue: do not delete; often this is the case from 15h |
| changes by automation or your own from tests | can be deleted by hand |
| subtasks linked to Asana | check the structure, usually 15e fixes it |

### 15h. Tasks moved to Jira by hand (`handle_migrated_to_jira.py`)

Tidies up tasks marked "migriert zu Jira" (section 11) and their subtrees. For each task it
looks for the **team's issue** (without a link, title as in Asana without the note; for
subtasks under the parent's team issue) and the **copy** from the sync (the one the state
points to):

| Action | When | Result |
|---|---|---|
| `USUŃ_KOPIĘ` (delete copy) | there is a team issue, nobody worked on the copy | copy deleted, state pointed to the team issue |
| `PRZEPNIJ` (re-point) | there is a team issue, no copy | state pointed to the team issue |
| `OZNACZ` (mark) | no separate team issue | the existing issue stays |
| `DO_DECYZJI` (to decide) | someone also worked on the copy | nothing is changed |

In all cases except `DO_DECYZJI` the entry gets the `jira_master` flag: the sync will never
create or update it again.
```bash
python handle_migrated_to_jira.py --project-key KEY            # plan
python handle_migrated_to_jira.py --project-key KEY --apply
```
The plan also lists the remaining issues without a link that are unrelated to the note. The
tool does not touch them (most often these are issues created by the team directly in Jira).

### 15i. Repairing many projects at once (`repair_batch.py`)

Runs the 15f-15h tools for a list of projects **one after another** (never in parallel on the
same project), with a separate report folder per project (`repair_reports/<KEY>/`:
`relink.csv`, `dup_plan.csv`, `migrated_plan.csv` and full logs of each step), shows the tools'
output live and ends with one table.
```bash
python repair_batch.py relink   --keys-file partia.txt                       # report + category table
python repair_batch.py relink   --keys-file partia.txt --apply --include-order --include-similar --include-content
python repair_batch.py dup      --keys-file partia.txt                       # plans + table
python repair_batch.py dup      --keys-file partia.txt --apply               # asks for TAK (yes) once
python repair_batch.py migrated --keys-file partia.txt [--apply]
python repair_batch.py relink   --keys-file partia.txt --shard 1/3           # and 2/3, 3/3 in separate terminals
```
Batch file: one key per line, `#` = comment. `--project-keys A,B` instead of a file. `--quiet`
shows just the table. Projects from `protected_projects.txt` are skipped. When split into parts,
each project goes to exactly one part, and the writing tools take a project lock; more than 3
processes at once will not speed things up (Asana and Jira rate limits).

**Running time:** the `relink` report is one Asana request per task plus one Jira request per
issue without a link (a large project: several thousand requests, a dozen or so minutes).
Writing is slower, because for each restored link it checks comments and attachments.

**Procedure for a batch** (**⏸** points: review the result before writing):
```bash
PARTIA=partia_00.txt
KEYS=$(sed 's/#.*//' "$PARTIA" | tr -d ' \t' | grep -v '^$' | paste -sd, -)
cp task_sync_state.json "task_sync_state.backup_$(date +%Y%m%d_%H%M%S)_${PARTIA%.txt}.json"

# 1. links
python repair_batch.py relink --keys-file "$PARTIA"                         # ⏸ table, review PODOBNE/TRESC
python repair_batch.py relink --keys-file "$PARTIA" --apply --include-order --include-similar --include-content
# 2. duplicates (fresh report after writing)
python repair_batch.py relink --keys-file "$PARTIA"
python repair_batch.py dup --keys-file "$PARTIA"                            # ⏸ "to decide" = 0?
python repair_batch.py dup --keys-file "$PARTIA" --apply
# 3. "migriert zu Jira"
python repair_batch.py migrated --keys-file "$PARTIA"                       # if it shows anything: --apply
# 4. structure
python refresh_state_flags.py --project-keys "$KEYS" --apply
python audit_hierarchy.py --project-keys "$KEYS" --full                     # ⏸ WYMAGA_PRZEGLADU = 0?
python audit_hierarchy.py --project-keys "$KEYS" --full --apply
python refresh_state_flags.py --project-keys "$KEYS" --apply
python fix_stale_resolution.py --project-keys "$KEYS"                       # if it shows anything: --apply
# 5. sync (remove the batch from sync_exclude.txt - script in section 11)
python detect_epic_candidates.py --project-keys "$KEYS" --force
python asana_jira_sync.py --project-keys "$KEYS"                            # ⏸ summary
# 6. check
python compare_project_totals.py "$KEYS"
python audit_hierarchy.py --project-keys "$KEYS" --full
```
The `PARTIA` and `KEYS` variables have to be set in every new terminal. With many batches,
step 5 can be run through `run_full_pipeline.py --only sync --parallel 3`: projects not yet
repaired are in `sync_exclude.txt`, so they will be skipped. Projects that only have gaps from
level 2 (no lost links) pass steps 1-3 unchanged and are fixed only by step 5.

### 15j. Manual conversion to an Epic (`convert_to_epic.py`)

```bash
python convert_to_epic.py --keys KEY-1,KEY-2 --dry-run
python convert_to_epic.py --keys KEY-1 --convert-subtasks
```

Turns the given tasks into Epics and explicitly sets *Epic Name*. When changing the type, Jira
leaves this field empty, even though it is required. With `--convert-subtasks` it also turns
the direct subtasks into Tasks under the new Epic. This is a tool for individual cases,
independent of the automatic sync.

When changing the type via Bulk Move (types that cannot be changed by a regular edit), the
required fields of the target type, including "Type", are filled in automatically (section 15e).

## 16. State and cache files (do not delete without a reason)

| File | Created by | Role | Can it be deleted |
|---|---|---|---|
| `task_sync_state.json` | `asana_jira_sync.py` and the repair tools | Asana ↔ Jira links, hierarchy flags (`is_epic`, `is_subtask`, `top_level_ancestor_key`), `status_applied`, `asana_link`, `deleted_in_jira`, `jira_master`, synced comments and attachments (both directions) | **no**: the next sync will create duplicates |
| `task_sync_state.backup_*.json` | `refresh_state_flags.py`, `relink_state.py`, `resolve_relink_duplicates.py`, `handle_migrated_to_jira.py`, by hand | state backups (the name contains the date and reason, e.g. `_relink_KEY`, `_dupl_KEY`, `_migriert_KEY`) | yes, when no longer needed |
| `sync_exclude.txt` | by hand | projects **temporarily** excluded from sync (section 11) | yes, when nothing is being repaired |
| `protected_projects.txt` | by hand | projects **permanently** excluded from sync and all data-changing tools | no, as long as it should apply |
| `section_status_map.json` | panel ("Status mapping") or by hand | main map Asana section → Jira status, with `__ukonczone__` | **no** |
| `section_status_map.<KEY>.json` | by hand | section map for one project, with `__ukonczone__` | yes, the project then goes back to the main map |
| `project_key_cache.json` | `jira_sync.py` | assigned project keys (by Asana GID) | not recommended |
| `asana_project_gids.json` | check tools | cache of GIDs of Asana projects outside the sheet | yes |
| `epic_candidates.json` | `detect_epic_candidates.py` | Epic candidates per project | yes, but it must be recreated before the sync |
| `workflow_graph_cache.json` | `asana_jira_sync.py` | workflow transition map for status changes | yes |
| `auth_state.json` | `save_login_session.py` | browser session (board columns) | yes, then you have to log in again |
| `user_map.json` | panel ("People mapping"), `generate_user_map.py` or by hand | **Asana** users → Jira accounts | **no**; do not overwrite with a map from another instance |
| `asana_users_not_in_jira.json` | panel | Asana people deliberately without a Jira account; their issues have no assignee and are not counted as differences | yes, the panel then shows them as unmapped again |
| `service_account.json` | panel (Start, pasted key) | Google service account key - **a secret** | yes, but it has to be pasted again |
| `migration_app/cache/` | panel | read results: sheet, people, sections and statuses, connection test, project-copy role selection | yes (the sheet read then takes a few minutes) |
| `migration_app/runs/`, `checks/`, `backups/` | panel | run history, "Statuses and people" results, file backups from before each save | yes |
| `wt_user_map.json` (or another name) | by hand | people from someone else's Jira instance → our accounts | yes, once incremental copying is finished |
| `status_map_projekt1.json`, `status_map_projekt2.json` | by hand | status maps for copying from another instance | no, as long as new items are being pulled in |
| `cross_instance_copy_state.json` | `copy_project_cross_instance.py` | source → target links (keys `SOURCE` or `SOURCE>TARGET`), `status_applied` | **no**, as long as the copy should be resumable, and especially as long as the target project is synced with Asana (the tools from sections 14-15 skip these issues based on it) |
| `repair_reports/<KEY>/` | `repair_batch.py` | reports, plans and logs of a project's repair steps | yes, after the repair is finished |
| `relink_state_report.csv`, `resolve_relink_duplicates_plan.csv`, `handle_migrated_to_jira_plan.csv` | tools 15f-15h (run without `repair_batch.py`) | the latest report/plan; `resolve_relink_duplicates.py` reads the relink report | yes, but the relink report must be refreshed before `dup` |
| `audit_hierarchy_report.csv`, `compare_project_totals_report.csv`, `refresh_state_flags_report.csv` | check tools | reports | yes |
| `projekty_all.txt`, `pilot.txt`, `partia_*.txt` | by hand | lists of projects to repair in batches | yes |
| `jira_schema_state.json` | `build_jira_schema.py` | name → ID mapping of configuration objects (preview only; the script checks Jira anyway) | yes |
| `jira_schema_<key>.yaml`, `status_map_<key>.json` | `export_source_workflows.py` | source workflow and 1:1 status map | yes, after the workflow is recreated and the tasks are copied |
| `epics_stories_state.json` | `create_epics_stories_from_sheet.py` | created Epics and Stories | yes; it **must** be deleted after deleting test issues in Jira |
| `pipeline_summaries/`, `logs/` | scripts | summaries and logs | yes |
| `__pycache__/` | Python | compiled modules | yes, always (Python rebuilds it) |

## 17. Jira configuration from a YAML file (`build_jira_schema.py`)

Builds the configuration of a company-managed project from a declarative YAML file: custom
fields, issue types, statuses, workflows, screens and all the schemes. Example of a full
configuration: `jira_schema_klient.yaml` (a client's configuration, not published; a starting
point for your own: the `jira_schema.yaml` template) (a recreation of the KlientX configuration).

```bash
python build_jira_schema.py --schema jira_schema_klient.yaml --dry-run
python build_jira_schema.py --schema jira_schema_klient.yaml
python build_jira_schema.py --schema jira_schema_klient.yaml --only statuses_workflow
python build_jira_schema.py --schema jira_schema_klient.yaml --include-incomplete
```

Requires the **Administer Jira** permission. The script is **idempotent**: an object with the
given name that already exists is reused instead of creating a duplicate. It saves the name → ID
mapping in `jira_schema_state.json`, but this file is only a preview: the script checks the
state in Jira on every run anyway. The log goes to `logs/`, errors and warnings to Slack (if
configured).

**Where the YAML file comes from:** from client documentation via the parsers
`parse_jira_pdfs.py`, `parse_jira_workflow.py`, `parse_jira_pptx.py` and `merge_jira_sources.py`
(e.g. `jira_schema_klient.yaml`: 47 fields, 6 screens, 46 statuses, 7 workflows), or from another
Jira instance via `export_source_workflows.py` (section 4a, copying from another instance).

Workflows marked in the YAML as `incomplete: true` (e.g. missing transitions) are skipped unless
you pass `--include-incomplete`.

**Known pitfalls:** statuses in older instances have numeric IDs, while `statusReference` in the
workflow definition must be a UUID, so the script generates it locally and points to the existing
status by `id`. The status category is taken from Jira, not from the YAML, and project-scoped
statuses are skipped when building global workflows.

### 17a. Steps (`--only`)

Executed in this order. `--only` takes one step or several separated by commas, e.g.
`--only screens,screen_scheme,issue_type_screen_scheme` — the execution order is always the one
in the table anyway:

| Step | What it does | What it does with an existing object |
|---|---|---|
| `custom_fields` | custom fields, for lists also the context and options | adds missing options |
| `issue_types` | missing **global** issue types | does not change it |
| `issue_type_scheme` | issue type scheme (in the UI: *Work type scheme*) | adds missing types |
| `statuses_workflow` | statuses and workflows | **does not change** the workflow |
| `screens` | screens, tabs, fields (section 17h) | adds missing tabs and fields, sets the tab order; removes nothing |
| `screen_scheme` | screen schemes (a screen per operation) | does not change it |
| `issue_type_screen_scheme` | issue type → screen scheme (*Work type screen scheme*) | adds missing mappings |
| `workflow_scheme` | issue type → workflow | adds missing mappings |
| `apply` | attaches the ITSS and workflow scheme to the projects in `apply_to_projects` | — |
| `workflow_layout` | recalculates the diagram layout of existing workflows (section 17f); **on request only**, not in a full run | changes status positions only |
| `workflow_update` | brings existing workflows to the state in the YAML: transitions, layout, START (section 17g); **on request only** | adds/changes/removes transitions, adds statuses, keeps rules |

Because mappings are added, the configuration can be built in stages: e.g. when a workflow
or screen scheme for another type is created later, re-running the step adds it to the
existing scheme. This works as long as the workflow scheme is not attached to a project.
After that Jira requires a draft and a status migration, which has to be done in the UI.

### 17b. YAML file format

```yaml
prefix: ''                        # optional prefix for the names of all objects

custom_fields:
- name: Wycena [MD]
  type: float
- name: System
  type: multiselect
  options: [eCommerce]

issue_types:                      # only types outside the Jira standard
- name: Deployment
  type: standard                  # or: subtask

issue_type_scheme:
  name: eCommerce Sync – Work Type Scheme
  default: Task
  issue_types: [Epic, Story, Task, Bug, Sub-Task, Dev-Task, Deployment]

statuses:
- name: Nowy
  category: TODO                  # TODO / IN_PROGRESS / DONE

workflows:                        # also supported: 'workflow:' and '_pozostale_workflow:'
- name: Workflow-Story
  initial_status: Nowy            # status after the issue is created
  incomplete: false               # true = skipped without --include-incomplete
  statuses: [Nowy, W trakcie analizy (PL), Done]
  start_layout: [80, 40]          # optional: position of the START point
  layout:                         # optional: top-left corner of each status
    Nowy: [200, 100]              # (missing = layout calculated automatically)
    W trakcie analizy (PL): [200, 210]
    Done: [200, 320]
  transitions:
  - name: Do analizy
    id: '511'                     # optional: transition id (e.g. from KlientX)
    from: [Nowy]                  # a list of statuses or ANY (global transition)
    to: W trakcie analizy (PL)

screens:
- name: Story Create Screen
  tabs:                           # order = order of tabs in Jira (17h)
  - name: Zakładka główna
    fields: [Podsumowanie, Opis, Wycena [MD], customfield_11977]   # field name or ID

screen_schemes:
- name: Story Screen Scheme
  default: Story Edit Screen      # required
  create: Story Create Screen     # optional: create / edit / view

issue_type_screen_scheme:
  name: eCommerce Sync – Work Type Screen Scheme
  default_screen_scheme: Task Screen Scheme   # for types without their own row
  mappings:
  - issue_types: [Story]
    screen_scheme: Story Screen Scheme

workflow_scheme:
  name: eCommerce Sync – Workflow Scheme
  default_workflow: Workflow-Task  # optional; without it the system 'jira' workflow
  mappings:
  - issue_types: [Task, Sub-Task, Dev-Task]
    workflow: Workflow-Task

apply_to_projects: []             # project keys for the 'apply' step
```

(The example names are Polish: "Nowy" = New, "W trakcie analizy" = In analysis, "Do analizy"
= To analysis, "Zakładka główna" = Main tab, "Podsumowanie" = Summary, "Opis" = Description,
"Wycena [MD]" = Estimate [man-days].)

Field types: `textfield`, `textarea`, `select`, `multiselect`, `datepicker` (alias `date`),
`datetime`, `float`, `userpicker` (alias `user`), `multiuserpicker`, `labels`, `url`. The
`issuelink` and `issuelist` types are in the map, but Jira Cloud rejects creating such fields
via the API.

### 17c. Name matching

- **Fields on screens** are looked up by name (case-insensitive), and system fields also by
  their Polish UI names ("Podsumowanie", "Opis", "Osoba przypisana", "Termin", "Wersje do
  poprawienia"…), so a schema in Polish also works on an account set to English. Jira
  Software fields (Sprint, Epic Name, Epic Link, Target start/end…) are recognized by type.
- **A field can also be given by ID** (`customfield_12083`) — it then hits exactly that
  field, even if Jira has several with the same name. Whether a field is on a tab is also
  checked by ID, so fields whose name the API returns in English are not added a second time.
- **"Wersja bazowa" (base version) is a custom text field in KlientX**, not the system Fix
  Version/s — the script no longer translates this name to `fixVersions`.
- **Issue types** are compared without hyphens and spaces: `Sub-Task` covers both `Sub-task`
  and the legacy `Subtask` (10059). This is deliberate: migrated tasks use the legacy type,
  and Jira will not attach a scheme without a type that existing issues have.
- **Statuses and types from team-managed projects are skipped.** They may have the same name
  as global ones. Confirmed: there are two "Done" statuses (global 10001 and project 10233),
  and a global workflow cannot see the project one (error "Status does not exist or is not
  visible to the workflow"). Names pointing to several objects are marked in the log as
  `[UWAGA]` (warning).
- **The category of an existing status** is taken from Jira, not from the YAML. A mismatch is
  reported in the log.

### 17d. Workflow rules and limitations

- **`initial_status`** gives the status after an issue is created. Without it the script takes
  the first status from the list and warns, because the order in the YAML rarely matches the
  diagram (in Workflow-Story the first one was `Closed`).
- **Transition names may repeat** (KlientX has e.g. three "Need more info" in Deployment and
  two "Gotowe" (Ready) in Story). The earlier assumption that they must be unique was wrong —
  the real cause of that error was the "Done" status from a team-managed project.
- **`from: ANY`** is a global transition from any status **to** `to`. A global loop "any status
  → the same status" (e.g. "Przypisz Analityka" = Assign Analyst, with a post-function) cannot
  be expressed this way. Configure it by hand in the workflow editor.
- The virtual start point from the diagrams (`Początek`, `Start`) is skipped, because the script
  generates the INITIAL transition.
- **The `statuses_workflow` step does not change an existing workflow** (it only creates missing
  ones). Existing ones are updated by `workflow_update` (section 17g), and the layout alone by
  `workflow_layout` (section 17f).
- **The diagram layout** is taken from `layout` in the YAML (in `jira_schema_klient.yaml` these
  are the positions from the KlientX diagrams, so the diagrams look 1:1). Without `layout` it is
  calculated automatically, vertically as in KlientX: START and the initial status at the top,
  further rows by distance from it in the transition graph, statuses in a row placed side by side
  so that the arrows cross less. Chains longer than 16 rows wrap to a lane on the right. Global
  transitions (`ANY`) do not affect the layout.

### 17e. Attaching to projects

The `apply` step assigns the ITSS and the workflow scheme, but the endpoint for assigning a
workflow scheme works **only for projects without issues**. Attach migrated projects by hand in
the project settings, in this order:
1. **Work type scheme** (issue types),
2. **Work type screen scheme** (screens),
3. **Workflow scheme**. Jira will ask here which new status each old status of each type goes to.

The full order for a new project on the eCommerce Sync configuration:

```bash
python build_jira_schema.py --schema jira_schema_klient.yaml --dry-run       # preview of everything
python build_jira_schema.py --schema jira_schema_klient.yaml                 # fields, types, workflows, screens, schemes
# attach the schemes to the project (apply step or by hand, as above)
python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project KEY   # section 17i
python set_transition_groups.py --dry-run && python set_transition_groups.py                # section 17j
```

After any later change to the screens: `--only screens` (possibly with `custom_fields`), and then
`associate_fields_to_field_scheme.py` again.

### 17f. Diagram layout of existing workflows (`--only workflow_layout`)

Workflows created by an older version of the script have all statuses in one row, and the START
point is sometimes at the other end of the diagram. This step recalculates the layout with the
same algorithm and puts START above the initial status. It takes the transition graph from Jira,
not from the YAML, so it also takes into account transitions added by hand in the editor. It
changes **only the status positions**: statuses, transitions and rules are sent back unchanged.

```bash
# preview: columns + Jira validation, nothing is saved
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_layout \
    --workflow Workflow-Epic --dry-run
# save for one workflow
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_layout \
    --workflow Workflow-Epic
# all workflows from the YAML
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_layout
```

The step is not part of a full run (without `--only`), so as not to overwrite a layout corrected
by hand in the editor. If the workflow has a `layout` section in the YAML (as all workflows in
`jira_schema_klient.yaml` do — positions read from the KlientX diagrams), the step uses it and the
diagram looks as in KlientX; without `layout` the layout is calculated automatically.

**Arrow ports.** Jira does not document which `fromPort`/`toPort` number means which edge of the
status rectangle, so arrows created via the API leave from random sides.
`diagnose_workflow_ports.py` (read-only) works out this numbering from workflows drawn by hand in
the editor:
```bash
python diagnose_workflow_ports.py
python diagnose_workflow_ports.py --limit 300
```
Put the result in `LAYOUT_PORTS` in `build_jira_schema.py` (empty by default — ports are then not
set) and run `workflow_layout` or `workflow_update` again.

### 17g. Updating existing workflows (`--only workflow_update`)

Brings workflows that already exist in Jira to the state in the YAML: transitions (names, sources,
targets, ids), the diagram layout and the START point. It prints which arrows are added (`+`) and
which disappear (`-`).

```bash
# preview of the changes + Jira validation, nothing is saved
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_update \
    --workflow Workflow-Epic --dry-run
# save
python build_jira_schema.py --schema jira_schema_klient.yaml --only workflow_update
```

- Statuses are matched by name (case-insensitive). Missing ones are **added** to the workflow.
- **It does not remove statuses**: that would require moving issues. If the YAML does not contain
  a status present in the workflow, the workflow is skipped with a message.
- **Rules** (actions, conditions, validators) of an existing transition with the same name and
  target are carried over, so configuration added by hand in the editor does not disappear.
- It is not part of a full run — run it deliberately, first with `--dry-run`.

The workflows in `jira_schema_klient.yaml` were recreated from PDFs of the KlientX editor (Text and
Diagram views). The names of the global transitions of Bug ("To Do", "Anulowane" = Cancelled),
Story ("Anulowane") and Task ("On hold", "Rejected") are assumed, because Jira does not show them
on the diagram — to be confirmed by KlientX. The Text view was complete only for Epic, Acceptance,
Question and Deployment; partial for Bug and Story, and missing for Task — the rest was read from
the diagram. Global loops with a post-function (e.g. "Przypisz Analityka") are skipped — configure
them by hand.

### 17h. Screens: fields by ID, tab order, removing fields

**Field by ID.** In a tab's field list you can give the field ID (`customfield_11977`) instead of
the name. Useful when Jira has several fields with the same name (e.g. two "Environment.") and you
need to point to a specific one. A comment after `#` is ignored:

```yaml
- name: System Project Deployment Edit/View Issue Screen-KlientX(v1)
  tabs:
  - name: Zakładka główna
    fields:
    - Podsumowanie
    - customfield_11977   # Data wdrożenia (deployment date)
```

**Tab order.** The `screens` step sets the tabs in the order from the YAML. The first tab is the
one Jira shows right after opening an issue, so fields like Summary and Description should be on
the first one. Jira appends new tabs at the end, so after adding the missing ones the script moves
them to the right places. Tabs not in the YAML stay at the end. In `jira_schema_klient.yaml` the
first one is "Zakładka główna" (Acceptance: "General", Question: "Field Tab"). The KlientX PDF
export listed the tabs alphabetically, which put the description in the last one.

**The default tab of a new screen.** Jira creates every new screen with an empty "Field Tab" tab.
The script renames it to the first tab from the YAML, unless the YAML itself expects "Field Tab"
(as on the Question screen).

**A field can be on a screen only once.** If you move a field in the YAML to another tab of the
same screen, the script will not move it (it only adds missing ones). Drag it by hand in the
screen editor.

**`--dry-run` saves nothing on existing screens.** An older version of the script added tabs and
fields to existing screens in preview mode. If you ran `screens` with `--dry-run` before this fix,
the changes may already have been applied.

**Removing fields from screens (`remove_field_from_screens.py`).** `build_jira_schema.py` only
adds. A separate script removes a field. The field and its values in issues stay; it only
disappears from the screens:

```bash
python remove_field_from_screens.py --field "Nr zgłoszenia u Dostawcy" --schema jira_schema_klient.yaml --dry-run
python remove_field_from_screens.py --field "Nr zgłoszenia u Dostawcy" --schema jira_schema_klient.yaml
python remove_field_from_screens.py --field customfield_11972 --all-screens --dry-run   # all screens in Jira
```

`--schema` limits itself to the screens from the YAML (recommended), `--all-screens` goes through
all screens of the instance, including other projects. With several fields of the same name the
script refuses and asks for the ID. Remember to remove the field from the YAML too, otherwise the
next `--only screens` will add it back.

### 17i. Field schemes — the field is on the screen but cannot be seen

Jira Cloud is replacing "field configurations" with a new mechanism, **field schemes**. It is
already enabled on the FIRMA instance. A field that is on a screen but **is not associated with
the project's field scheme** is invisible and cannot be edited — also via the API and automation.
Creating fields and screens in `build_jira_schema.py` **does not create** this association.
Symptom: in "Find your field" (⋯ in the issue → Find your field) the entry "Field scheme
association: The field … is not included in the 'Default Field Scheme'".

`associate_fields_to_field_scheme.py` collects the IDs of all custom fields from the screens in
the YAML and adds the missing ones to the project's field scheme. It also removes a restriction of
the field to selected work types, if it finds one:

```bash
python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project PROJ --dry-run
python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project PROJ
python associate_fields_to_field_scheme.py --project PROJ --fields customfield_11977,customfield_11973
python associate_fields_to_field_scheme.py --schema jira_schema_klient.yaml --project PROJ \
    --dry-run --show-raw customfield_11977          # raw entry of the field in the field scheme
```

- **Run it after every time a field is added to screens** (`--only screens`), for every project
  that uses the eCommerce Sync schemes.
- FIRMA projects usually share a common **"Default Field Scheme"**. The added fields become
  available in them, but show up only where they are on the screens.
- The field schemes API is new (request format according to
  [RFC-105](https://community.developer.atlassian.com/t/rfc-105-new-field-scheme-model-apis-for-field-configuration-backup-restoration-and-management/94206)).
  On a write error the script prints Jira's full response.

**Diagnostics: `diagnose_field_on_screen.py`** (read-only). For one field it checks in turn:
whether it exists (and whether there are no duplicate names), the field context, which screens it
is on, which screens the project actually uses for the given type (create/edit/view), whether the
field is editable in a specific issue, the field configuration, and a table of all fields of the
edit screen with the columns "editable / in field configuration / context / type":

```bash
python diagnose_field_on_screen.py --field customfield_11977 --issue PROJ-11
python diagnose_field_on_screen.py --field "Wersja wdrożenia" --project PROJ --issue-type Deployment
```

Order of checks when a field cannot be seen in an issue:
1. whether the field exists and has a context covering the project and type (section 2 of the output),
2. whether it is on a screen the project uses for this type (section 4),
3. whether it is in the project's field scheme ("Find your field" or `--show-raw`),
4. whether it is simply empty — Jira hides empty fields under "More fields".

### 17j. Who may perform a transition — groups (`set_transition_groups.py`)

Creates groups and sets the "Restrict transition" condition
(`system:restrict-issue-transition`) with a list of groups on workflow transitions. The
configuration is in `transition_groups.yaml` (the file is not in the repository — create it
following the pattern):

```yaml
groups:                               # alias from the slide -> group in Jira
  SX-Dev:    {name: "SX-Dev"}         # created if it does not exist
  PL-Admins: {name: "PL-Admins"}
  Admins:    {name: "Backbone-superuser", existing: true}   # only checked

always_allowed: [Admins]              # added to EVERY condition

propagate:                            # the same rules in other workflows
- from: Workflow-Deployment
  to_workflows: [Workflow-Story, Workflow-Task, Workflow-Bug, Workflow-Epic]

workflows:
  Workflow-Deployment:
  - {transition: "Deploy to TEST", to: "Deploying (Test)", groups: [Admins]}
```

```bash
python set_transition_groups.py --dry-run                 # preview + Jira validation
python set_transition_groups.py
python set_transition_groups.py --workflow Workflow-Deployment
```

- A transition is identified by **name and target status** (case-insensitive), because names
  repeat (3× "Need more info").
- **`propagate`** applies the rules to transitions with the same name and target in other
  workflows; a missing transition is not an error. Note: the source status is not compared, e.g.
  "Deployed To Test" in Bug/Task (from Done) gets the groups of "Deployed to TEST" in Deployment
  (from Deploying (Test)).
- The previous group condition is **replaced**, other transition conditions stay — the script can
  be run many times. `build_jira_schema.py --only workflow_update` does not delete these
  conditions (it carries over the rules of transitions with the same name and target).
- **New groups are empty.** Before you save the conditions, add people to the groups — otherwise
  only `always_allowed` (Backbone-superuser) can perform the transitions.
- Jira validation in `--dry-run` works only once the groups already exist.
- Source of the groups: slide 13 of the KlientX v1.5 presentation (Deployment only). Deliberately
  skipped: Create (it does not accept conditions — creation is governed by the permission scheme),
  "Auto." transitions (performed by automation) and transitions without groups on the slide.
  Abbreviations were expanded: "An" → PL-An, "PM" without a prefix → SX-PM + PL-PM, "SX, PL" →
  SX-Dev, SX-PM, PL-PM, PL-An. Correct them in the YAML if you read them differently.

## 18. Working procedure

### A new migration

The **Start** tab in the panel (part I). Start with a sheet tab containing 1-2 test
projects (`GOOGLE_SHEET_RANGE`), ask a Jira administrator to switch off notifications and
automation for the new projects before creating issues in bulk, and after the migration
check the projects in "Statuses and people" and with the task count comparison (14a).

### Rules

- **Report first, then write.** Every data-changing tool has a report mode; `--apply` only after
  reviewing the result (especially `PODOBNE`, `TRESC`, `DO_DECYZJI`, `WYMAGA_PRZEGLADU`).
- **A copy of the state** before every bigger operation:
  `cp task_sync_state.json "task_sync_state.backup_$(date +%Y%m%d_%H%M%S)_<reason>.json"`.
- **Never two processes on the same project.** Different projects in parallel are safe (project
  locks, the state saves only changed entries), but at most 3 processes at once.
- **Sync only after repairing** a project with lost links; until then the project is in
  `sync_exclude.txt`.
- Projects where the team now works only in Jira are in `protected_projects.txt`.
- The sheet contains the projects to sync; all steps work on the sheet by default.

### Repairing projects synced with an older version

Diagnosis: `python compare_project_totals.py --all --top-level` (fast) or the full
`--all --shard K/N`. Patterns and their meaning: section 14a.

**Step 0, once:**
```bash
cp task_sync_state.json "task_sync_state.backup_$(date +%Y%m%d_%H%M%S)_przed_naprawa_wszystkich.json"
python3 -c "import jira_sync as c; print('\n'.join(sorted({r.jira_key for r in c.get_sheet_rows() if r.jira_key})))" > projekty_all.txt
cat projekty_all.txt >> sync_exclude.txt          # no project is synced until it is repaired
```
In addition, ask the Jira administrator to **turn off notifications** (e.g. an empty notification
scheme for the projects) and pause automation while missing issues are being pulled in, because
Jira sends e-mails on every creation via the API.

**A pilot batch** with different cases (a large project with lost links, a small one, only missing
deep levels, Jira > Asana), then the rest in batches of about 50 projects:
```bash
grep -v -x -f pilot.txt projekty_all.txt | split -l 50 -d - partia_
```
Each batch goes through steps 1-6 of section 15i. Urgent projects can be put into a separate file
and taken through the same steps out of order.

### Routinely, on every sync

```bash
python run_full_pipeline.py --only epics --parallel 3
python run_full_pipeline.py --only sync --parallel 3
```
One project: `python asana_jira_sync.py --project-keys KEY` (before a new project or after a
structure change in Asana also `python detect_epic_candidates.py --project-keys KEY --force`). A
new project with a different workflow: first `section_status_map.<KEY>.json` (section 11), a
`--dry-run` preview, and a check of the list of sections without a mapping.

Reacting to the summary (console or Slack):

| Signal | What to do |
|---|---|
| `Sekcje BEZ wpisu w section_status_map.json` | add the section to the main map or the project map (also variants with typos) |
| `Statusy błąd` > 0 | the warning in the log points to the issue and the missing transition; workflow or manual change |
| `kandydaci na Epic do retrofit_epics.py` > 0 | optionally a retrofit (15a), after agreeing with the PM |
| `pominięte usunięte w jirze` grows for no reason | section 14h |
| `[CHRONIONE] Pomijam ...` | expected for projects in `protected_projects.txt` |
| `Utworzono` much higher than the number of new tasks in Asana | **stop**; possibly lost links (section 14a) |
| connection errors (`Temporary failure in name resolution`) | section 19 |

### Periodically (e.g. once a month)

```bash
python compare_project_totals.py --all --top-level      # quick completeness overview
python audit_hierarchy.py                                # structure changes made in Asana
python fix_stale_resolution.py --project-keys ...        # struck-through keys
```
The sync does not rearrange the hierarchy of already migrated issues (type and parent are set on
creation), so structure changes in Asana are caught by the audit.

### Pulling in new items from another Jira instance

The same command as for the first copy, with `--update-status` (section 4a, "Incremental copy");
first `--dry-run` for each target project.

### Always

After manual hierarchy changes in Jira or after any repair tool:
`python refresh_state_flags.py --project-keys ... --apply`.

### Processes

Running scripts: `pgrep -af python`. Pause and resume: `kill -STOP <PID>` / `kill -CONT <PID>`
(or `Ctrl+Z` and `fg`); an operation in progress at the moment of pausing may exceed its timeout
after resuming and end up among the errors. Stop: `kill <PID>` or `Ctrl+C` (the state write is
atomic; re-running the same command skips what is done). After stopping `run_full_pipeline.py`,
check that no shard processes are left.

## 19. Troubleshooting

### 19a. Sync, repairs, copying

| Symptom | Cause | What to do |
|---|---|---|
| `Temporary failure in name resolution`, series of `[PONAWIAM n/4]` (retrying) | a network or DNS outage on your side | check `ping -c 3 app.asana.com`; for a longer outage abort, and when the network is back run the same command again (finished items are skipped), at the end `compare_project_totals.py` |
| `copy_project_cross_instance.py`: `0 zadań` (0 tasks) or `odrzuca logowanie: HTTP 401` | the token for the source instance expired or was revoked, the account lost access, the instance changed its address | a new token (regular, not "scoped") in `SOURCE_JIRA_API_TOKEN`; check: `curl -u "$SOURCE_JIRA_EMAIL:$SOURCE_JIRA_API_TOKEN" "$SOURCE_JIRA_BASE_URL/rest/api/3/myself"` |
| `AttributeError: module 'jira_sync' has no attribute ...` or a report with identical numbers despite a new version | the folder has an old version of the file (the downloaded new one was saved as `file (1).py`) or a stale `__pycache__` | `ls -la name*.py`, move the new version to the right name, `rm -rf __pycache__`; replace files from one package together |
| `repair_batch.py: error: podaj --project-keys albo --keys-file` | empty `$PARTIA` variable (new terminal) | set `PARTIA` and `KEYS` again |
| `GRUPA:: nie znaleziono polecenia` (command not found) on `source .env` | a value with a space, without quotes | `DEVELOPER_GROUP_NAME="MOJA GRUPA: Staff"` |
| `sesja przeglądarki wygasła` / `UNAUTHENTICATED` / empty GraphQL response | session, overload, changed query hash | section 4a, "When saving columns stops working" |
| `Bulk Move nie zakończyło się w ciągu 60s` | slow queue on Jira's side | another `--apply` will finish the missing ones (section 15e) |
| `For issue X Type is required` | the required "Type" field when changing the type | handled automatically by `convert_to_epic.py`; if it comes back, check `DEFAULT_TYPE_FIELD_ID`/`DEFAULT_TYPE_FIELD_VALUE` in `.env` |
| `The priority selected is invalid` when copying | a priority outside the project's priority scheme | handled automatically (the field is skipped, the rest is saved) |
| a struck-through key in an open issue | a leftover Resolution field | section 14g |
| hundreds of e-mails/notifications about deletions and changes | issues are created from the sync account, which is the reporter | personal notification settings in the Jira profile; for bulk creation, notifications turned off by an administrator |
| the number in `Zdjęto z wykluczeń` (removed from exclusions) is higher than the number of projects | duplicate entries in `sync_exclude.txt` | harmless; deduplication: section 11 |
| the sync preview shows nothing for a project | the project is in `sync_exclude.txt` or `protected_projects.txt` | `grep KEY sync_exclude.txt protected_projects.txt` |
| `./start.sh`: `Address already in use` | port 8765 is taken (e.g. a running panel) | open the running panel or `./start.sh --port 9000` |
| an older browser tool doesn't start the browser | Chromium not installed or missing system libraries (Linux) | `.venv/bin/python -m playwright install chromium`, then if needed `sudo .venv/bin/python -m playwright install-deps chromium` |
| step 2: "workflow projektu nie pasuje do wzorca" (workflow does not match the template) | the board's project uses statuses the template board does not have | set `board_template_id` in the sheet or use `board_columns.py --force` (section 11a) |
| panel: empty project list | the sheet is loaded only on request | Start → step 3 or Projects → "Refresh from sheet" |
| connection test: no access to the sheet (403) | the sheet is not shared with the service account | share it with the address shown in step 1 |
| issues without an assignee although the person is in `user_map.json` | the person's role lacks "Assignable User" in the permission scheme | "Statuses and people" → People (scheme and roles with the permission) |
| checked-off issues don't get the final status | no `__ukonczone__`, and the project has no `Fertig` status | set the status in "Status mapping" |
| project copy: "pominięto uprawnienia…" (permissions skipped) | Jira does not accept some permissions when creating a scheme (Product Discovery, `COPY_PROJECT`) | add them by hand in the copy's scheme if needed |

### 19b. Jira configuration in practice: known problems and solutions

**The field is on the screen but cannot be seen / edited.** See section 17i (field scheme) — the
most common cause on FIRMA. Quick test in the UI: ⋯ in the issue → **Find your field**.

**A Story's name cannot be edited.** A Story has two screens: Create and Edit (edit and view). The
Edit screen from the KlientX export had neither Summary nor Description. In
`jira_schema_klient.yaml` a "Zakładka główna" (main tab) was added to it (16.10.2026) — running
`--only screens` is enough. The same cause made the Story Components field invisible.

**Changing the type Task → Story from the type icon does not work** ("You cannot edit this work
type in this space"). Task and Story have different workflows, and changing the type from the icon
works only with a shared workflow. Use **⋯ → Move**, keep the project, choose the type and map the
status. Many issues at once: search → **Bulk change → Move**. The key, comments and history remain.

**The "Work item created" automation does not work on some issues.** Issues **moved** from another
project (Move, e.g. RED-28 → PROJ-450) do not fire the "created" event. For fields set
automatically (e.g. Key_txt = `{{issue.key}}`) add:
- a rule with the **Work item moved** trigger (target project) and the same action — it always
  overwrites, because the key changes on a move,
- or a **Scheduled** rule with the JQL `project = PROJ AND "Key_txt[Short text]" is EMPTY` (or
  `cf[12083] is EMPTY`) and **without** "Only include work items that have changed since the last
  time…". It catches every issue with an empty field, regardless of how it got into the project,
  and also fills in old ones.

Diagnosis: the rule's audit log (⋯ → Audit log). No entry = the rule did not run (scope or the way
the issue was created), an entry with an error = the field (field scheme), "Success" but the field
is empty = something overwrote the value (check the issue History, e.g. the Backbone sync).

**Changing the project key (PROJT → PROJ).** Automation rules are tied to the project, not the
key — they keep working. In the scripts use the new key (`--project PROJ`); old issue keys redirect
in the UI. State files saved under the old key (e.g. `epics_stories_state.json`) will start from
scratch, but the scripts look for existing issues in Jira anyway, so they do not create duplicates.
Fields with a saved key (Key_txt) may contain the old key.

**Backbone sync — "Analityk u Klienta" (client-side analyst) = `Bacbone-Issue-Sync (${user.emailAddress})`.**
This is an unsubstituted template in the Backbone mapping; to be fixed in the sync configuration,
not in Jira.

## 20. Epics and Stories from the "Zakres MVP" (MVP scope) sheet

### 20a. Creating (`create_epics_stories_from_sheet.py`)

Source: an `.xlsx` file (Google Sheets: File → Download → Microsoft Excel).

| Tab | Column | Goes to |
|---|---|---|
| Lista Epiców (Epic list) | D | Epic title (when empty or a formula without a value: B + " - " + C) |
| Lista Epiców | E | Epic description |
| Biznesowy zakres MVP (business MVP scope) | D (Funkcja = Feature) | Story title |
| Biznesowy zakres MVP | F (Opis kontekstowy = context description) | Story description |
| Biznesowy zakres MVP | G (Uwagi = Notes) | appended to the description as "Uwagi: …" (with an empty F the description = just the notes) |
| Biznesowy zakres MVP | E (Status: MVP / POST-MVP) | Story **component** (the Components field) |
| Biznesowy zakres MVP | B + " - " + C | parent Epic (matched to column D of "Lista Epiców") |

```bash
python create_epics_stories_from_sheet.py --file "Zakres MVP.xlsx" --project PROJ --dry-run
python create_epics_stories_from_sheet.py --file "Zakres MVP.xlsx" --project PROJ --limit 5   # test
python create_epics_stories_from_sheet.py --file "Zakres MVP.xlsx" --project PROJ
```

| Option | Effect |
|---|---|
| `--limit N` | test: the first N Stories and only their Epics; a full run then finishes the rest without duplicates |
| `--status MVP` | only Stories with this status |
| `--only-epics` | Epics only |
| `--no-status-component` | no components from the status |
| `--strip-epic-components` | removes MVP/POST-MVP from existing Epics (set by an older version of the script) |
| `--epic-sheet`, `--story-sheet`, `--story-type` | other tab names / Story type |

**Rules:**
- **Story → Epic matching** ignores case, extra spaces and the kind of dash. When the full title
  does not match, the script matches by the area number ("2.") and the Epic name (column C) — this
  is how it handles "2. Hub ofertowy / Backoffice (panel administracyjny)" vs "2. Hub ofertowy /
  Backoffice". Pairs matched this way are printed in an `[INFO]` block. The Epic title always comes
  from column D of "Lista Epiców".
- **Stories without an Epic** (B + C matches nothing) are skipped and listed at the start — usually
  a typo in the sheet.
- **An empty description in the sheet = an empty description in Jira** (the field is not sent).
- **Only Stories get components.** The script creates missing components (MVP, POST-MVP) in the project.
- **Verification after writing:** after creating, the script reads the issue back and, if Jira
  dropped the description or component, adds it with a separate update. For existing issues it fills
  in an empty description and a missing component (it does not overwrite a filled description). A
  rejection by Jira is printed with the full message.
- **The "Epic Name" field**, if Jira requires it on creation, gets the Epic title.
- **Duplicates:** before creating, the script looks in the project for an Epic with the same title
  and a Story with the same title under that Epic. It saves the state in `epics_stories_state.json`.
  After deleting test issues in Jira, delete this file too, otherwise the script will consider them
  existing.

### 20b. Exporting back to the sheet (`export_stories_to_sheet.py`)

Fetches Stories from Jira and writes them to a "Jira" tab with the columns
**Klucz (Key) | Epik (klucz) (Epic key) | Epik (Epic) | Tytuł (Title) | Opis (Description)** (keys are
links, the description as plain text, rows sorted by key):

```bash
# a new tab in a COPY of the sheet: "<file> - eksport Jira.xlsx" (the original untouched)
python export_stories_to_sheet.py --project PROJ --file "Zakres MVP.xlsx"
# a separate file
python export_stories_to_sheet.py --project PROJ --out stories_PROJ.xlsx
# a custom filter
python export_stories_to_sheet.py --project PROJ --jql 'project = PROJ AND component = MVP'
```

## 21. Panel files (`migration_app/`)

Using the panel is described in part I. Files:

| Path | What it is |
|---|---|
| `../start.sh` | installation (`.venv`, dependencies) and panel start; `panel.sh` just calls it |
| `server.py` | HTTP server and API |
| `catalog.py` | tool catalogue: groups, form parameters, when a tool writes |
| `runner.py` | subprocesses, logs, input for script prompts, stopping |
| `asana_check.py` | status and assignment check (can also be run by hand) |
| `sheet_projects.py` | reads projects from the sheet into `cache/sheet.json` |
| `setup_check.py` | connection and configuration tests (Start, step 2) |
| `people_source.py` | people from Asana and Jira groups with suggested matches |
| `status_source.py` | Asana sections and template project statuses for mapping |
| `jira_lookup.py` | roles, people and groups for the "Copy project" tab |
| `cache/` | read results (sheet, people, sections, tests) — not in the repository |
| `static/` | the user interface (HTML, JS, CSS); `i18n.js` - English version (dictionary and PL/EN switch) |
| `runs/` | run history (metadata and log) — not in the repository |
| `checks/` | results of status and people checks — not in the repository |
| `backups/` | copies of files from before every save in the panel — not in the repository |

**A new script in the panel** is added with one entry in the `TOOLS` list in
`catalog.py`: the group, the script path, the form parameters (kinds described at the
top of the file: `bool`, `str`/`int`, `keys`, `choice`, `pos`, `repeat`) and the
`writes` field (`never`, `always`, `unless:--dry-run`, `with:--apply`), which decides
whether the panel asks for confirmation.

## 22. Other helper scripts

| Script | What it is for |
|---|---|
| `add_admin_to_projects.py` | adds one person (accountId) to the Administrator role in existing projects |
| `add_group_to_projects.py` | adds a group to one or more roles in existing projects |
| `assign_category_to_projects.py` | assigns an existing project category to the given projects |
| `copy_permission_scheme.py` | copies a permission scheme under a new name and optionally attaches it to a project |
| `empty_notification.py` | attaches the given notification scheme to a list of projects (keys in `RAW_PROJECT_KEYS`, credentials from the `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` environment variables) |
| `atlassian_suspend_to_deactivate.py` | deactivates accounts with the "suspended" status in the Atlassian organization (Lifecycle API) |
| `backfill_jira_links.py` | one-off: fills in the `jira_link` column in the sheet for rows with a `jira_key` |
| `list_sheet_tabs.py` | prints the exact tab names of the Google Sheet |
| `check_jira_project_access.py` | checks whether an e-mail and API token have read access in Jira |
| `check_project_level_attachments.py` | detects files attached to a project in Asana (not to tasks), which the sync does not move |
| `migrate_project_level_attachments.py` | moves those files to Jira as attachments of a "Project resources" container issue |
| `bulk_configure_columns_simple.py` | column configuration variant for a simple 4-column layout |
| `configure_columns_via_cli.py` | column configuration via the Jira Command Line Interface (Appfire) instead of the browser |
| `move_all_statuses_to_new.py` | moves all statuses of a board to a single "New In" column |
| `create_analysis_for_stories.py` | creates a linked "Analysis" issue for every Story |
| `diagnose_field_config.py`, `diagnose_transition.py` | diagnostics: raw field configuration scheme response and the transitions available for an issue |
| `project_lock.py` | per-project lock module for parallel runs (used by other scripts) |
