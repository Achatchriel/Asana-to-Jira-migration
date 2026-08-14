# Sync: Google Sheets (selected Asana projects) → Jira

The script creates Jira projects **only for entries listed in the Google Sheet** —
the sheet is the source of truth for what should exist in Jira.

Additionally, the script fetches (via `asana_project_link`) the corresponding
project in Asana to enrich the new Jira project's description with notes from
Asana. If a given sheet row has no link, the project is still created — just
without an enriched description (see section 7 — name-based matching has been
deliberately disabled).

## 1. Installation

```bash
pip install -r requirements.txt
```

## 2. Configuration

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Fill in the values in `.env`:
   - **Jira**: instance URL, email, [API token](https://id.atlassian.com/manage-profile/security/api-tokens)
   - **Asana**: [Personal Access Token](https://app.asana.com/0/my-apps), workspace GID
   - **Google Sheets**: service account (JSON key) from [Google Cloud Console](https://console.cloud.google.com/)
     — remember to **share the sheet** with the service account's email address.

## 3. Board type

By default **all created projects are company-managed Kanban boards**
(classic, centrally managed by a Jira admin — requires admin permissions to
create this project type). If you prefer team-managed (simpler, self-configured
by the team), `.env.example` has a ready, commented-out line with the
appropriate template key.

## 4. Copying configuration from a template project

If you want new projects to have **the same workflow, screen layout, and
fields** as an existing, chosen Jira project — set in `.env`:

```
JIRA_TEMPLATE_PROJECT_KEY=YOURKEY
```

The script will then copy from the template project, for every newly created
project:

- **workflow** (workflow scheme),
- **layout** — screen scheme assigned to issue types (issue type screen scheme),
- **fields** — field configuration (field configuration scheme),
- **issue types** — issue type scheme,
- permissions, notifications, and security scheme (if the template project has
  custom ones — otherwise Jira's default is used).

**Limitations worth knowing about:**
- Works **only for company-managed (classic) projects** — which is our
  default type anyway (see section 3).
- Requires **Jira administrator permissions** (Administer Jira global
  permission) — without them Jira returns a 403 error when trying to assign
  the workflow/layout/fields.
- Workflow, layout, and fields are assigned in a separate step, **right
  after** creating the empty project — this only works as long as the
  project has no issues yet, which is always true for a freshly created
  project.
- If assigning any of the schemes fails (e.g. missing permissions), the
  script **does not delete** the already-created project — it reports this
  as a warning in the summary so you can fix it manually in Jira.
- `--dry-run` mode does not test the configuration copying itself (since the
  project doesn't exist yet) — it only shows that the configuration would be
  copied.

**Important limitation: Kanban board columns.** Jira **does not expose a
public API for setting board columns** — this is the one piece of visual
configuration that cannot be copied automatically. Workflow, statuses,
screens, and fields will be identical to the template, but the new board
will get Jira's default column layout (usually one status = one column). To
help manually recreate the template's layout, run:
```bash
python show_template_board_columns.py
```
It prints the exact column layout (names + which statuses are in which
column) from the template board — you just drag statuses into the same
places on the new board (Board → Configure → Columns), which takes a
moment. For a large number of projects, also see `browser_automation/` —
Playwright automation for this step.

## 5. Automatically adding a developer group/users

If you want a specific Jira group **and/or** specific users to be
automatically added to the "Developers" role (or another role) in every
newly created project, set in `.env`:

```
DEVELOPER_GROUP_NAME=group-name
DEVELOPER_ACCOUNT_IDS=5b10a2844c20165700ede21,5c20a2844c20165700ede99
DEVELOPER_ROLE_NAME=Developers
```

Both (the group and the user list) work **independently** — you can use
one, the other, or both at once (two separate requests to the same role).

The script checks once at startup whether such a role exists in Jira, and
if so — after each successful project creation, adds this group to that
role (adds, doesn't overwrite other actors already assigned to the role).
If a role with the given name doesn't exist, the script prints a warning
at startup and doesn't add the group to any project (so it doesn't
pointlessly try 550 times).

Requires *Administer Projects* permission for the created projects or
*Administer Jira* — the token used in `.env` must have it, otherwise
you'll get a per-project warning in the summary (the project is still
created, just without the group added — you'll need to add it manually).

Not sure about the exact group or role name? Run:
```bash
python list_groups.py          # all groups (optionally with a name fragment)
python list_project_roles.py   # all project roles
```

## 6. Automatically adding administrator(s)

Similarly, you can add **one or several specific users** (e.g. yourself and
a few team members) to the administrator role in every newly created
project:

```
ADMIN_ACCOUNT_ID=your-accountId
ADMIN_ROLE_NAME=Administrators
```

Several people at once — comma-separated (one API request per project,
regardless of how many people):
```
ADMIN_ACCOUNT_ID=5b10a2844c20165700ede21,5c20a2844c20165700ede99
```

`ADMIN_ACCOUNT_ID` is an `accountId`, not an email — find it with:
```bash
python get_my_account_id.py              # for yourself
python get_group_members.py "Group name" # for many people from a group at once
```

Works exactly like the developer group (section 5): the role is checked
once at startup, added after each project creation, warning instead of a
hard failure if it doesn't work.

## 7. Google Sheet format

The first row is the header (case-insensitive). **`asana_project_link` is
required** to get an enriched description (from Asana notes) and for
`asana_jira_sync.py`/`jira_asana_sync.py` to work — without it the project
is still created in Jira, but without a description and without the
ability to sync tasks for that row. `project_name` can be left blank if
you provided the link — the name will be fetched automatically from
Asana. The remaining columns are optional:

| project_name              | jira_key | project_type_key | template_key | lead_account_id       | description | board_template_id | asana_project_link |
|-----------------------------|----------|--------------------|-----------------|--------------------------|--------------|--------------------|----------------------|
| Q4 Campaign                 |          | software           |                 | 5b10a2844c20165700ede21 |              |                    | https://app.asana.com/0/1234567890123456/list |
| Homepage redesign            |          | business           |                 |                          | Redesign UI  | 7276               | https://app.asana.com/0/1111111111111111/list |
| *(blank — name pulled from link)* |  |    |                 |                          |              |                    | https://app.asana.com/0/9876543210987654/list |

- **`board_template_id`** — used ONLY by `bulk_configure_columns_simple.py`
  (not by `bulk_configure_columns.py` or `jira_sync.py`). The template board
  ID for the COLUMN LAYOUT of this specific project — useful when the
  template project has several boards with different layouts (e.g.
  `REFERENCE2` has both an 11-column layout on board 6110 and a simpler
  4-column one on boards 7276/7273/7274/7275). Blank = the row is handled
  by `bulk_configure_columns.py` (default template, `JIRA_TEMPLATE_BOARD_ID`
  from `.env`); filled in = the row is handled exclusively by
  `bulk_configure_columns_simple.py`.

- **`asana_project_link`** — a DIRECT link to the project in Asana (copied
  from the browser's address bar). This is the **only** way to identify an
  Asana project across the whole toolkit — name-based matching has been
  **deliberately removed** (there were duplicate project names in Asana,
  which led to syncing with the wrong project — a costly lesson from a
  painfully long debugging session). Supported formats:
  `https://app.asana.com/0/<id>/list` and
  `https://app.asana.com/1/<workspace>/project/<id>/list` (and `/board`
  variants instead of `/list`). Used by `jira_sync.py` (description
  enrichment), `asana_jira_sync.py`, and `jira_asana_sync.py` (task sync in
  both directions).

- **`jira_key` is written back to the sheet automatically** after being
  generated (see section 9) — so even leaving this field blank, after the
  first run you'll have ready-made keys for quick copying (e.g. for manual
  column/status configuration in Jira).

- `lead_account_id` is the Jira user's `accountId` (not an email!). Find it
  e.g. via `GET /rest/api/3/user/search?query=email@company.com`, or more
  simply — run the included `python get_my_account_id.py email@company.com`.
  If a row has no lead, `DEFAULT_LEAD_ACCOUNT_ID` from `.env` is used (if
  set). **Jira requires a valid lead when creating a project** — if both
  fields are blank, the script reports this as a clear error instead of
  sending a request that would be rejected anyway.

## 8. Running it

First, a preview, without creating anything:
```bash
python jira_sync.py --dry-run
```

Actually creating the projects:
```bash
python jira_sync.py
```

The script is **idempotent** — if a project with a given key already
exists in Jira, it's skipped. You can therefore run it repeatedly (e.g.
every time you add a new row to the sheet) without risking duplicates.

## 9. How key generation works

For a project name without a given `jira_key`:
1. Several words → initials of each word (e.g. "Client Portal B2B" → `CPB`).
2. One word → first up to 10 characters, uppercase.
3. **Minimum 6 characters** — if the result is shorter, it's padded with
   further letters from the name, and as a last resort with the letter "X"
   (e.g. "A" → `AXXXXX`).
4. If the generated key collides with one already assigned in the same run,
   in the cache, OR with a project that **actually exists** in Jira
   (checked live via the API — not just against our local cache), a number
   is appended (`CPB2`, `CPB3`, ...) and the attempt repeats.
5. The result is saved to `project_key_cache.json` (persistent between runs)
   **and written back to the sheet** (`jira_key` column) — one bulk request
   at the end of the whole run, not in `--dry-run` mode. Requires the
   Google service account to have **Editor** permissions on the sheet, not
   just Viewer, and the `jira_key` column to already exist in the header.

If you want full control over a specific key — just type it manually in
the `jira_key` column for that row, and the script won't overwrite it.

## 10. What the script prints at the end

- number of created and skipped projects,
- errors (e.g. missing permissions, missing lead, taken key),
- sheet rows without a match in Asana (the project is still created, just
  without a description),
- path to the name → key mapping file.

## 11. Task synchronization Asana → Jira (`asana_jira_sync.py`)

A separate script (alongside `jira_sync.py`, which only creates *projects*)
— synchronizes *tasks* within already-created projects. One direction only:
Asana → Jira (the reverse direction is a separate script,
`jira_asana_sync.py` — see section 12).

### What it synchronizes

- Title (safely truncated to 255 characters — Jira's limit — with the full
  version kept at the start of the description if the original was
  longer), description (with rich formatting: links, lists,
  bold/italic/underline/strikethrough), assignee, due date — always, and
  only if they actually changed.
- Status — via the Asana section, mapped to a Jira status (see below).
  Checked on EVERY run, regardless of whether other fields changed (moving
  between sections doesn't always update the task's timestamp in Asana).
- Comments — only real ones (no system events like "changed due date"),
  with an `[Asana — Author, date]` prefix in the body (Jira doesn't allow
  impersonating the original author without additional configuration).
- Attachments — downloaded from Asana and uploaded to Jira.
- **Assignee fallback** — if the original assignee from Asana can't be
  assigned in the given Jira project (e.g. not a member of it), the script
  automatically tries the lead of THAT SPECIFIC project instead of giving
  up.

### How it avoids duplicates on repeated runs

A local `task_sync_state.json` file (created automatically, **don't delete
between runs**) remembers, for each task: its Jira key, the last known
`modified_at` from Asana, and the IDs of already-transferred comments and
attachments. Saved **after every single task** (not only after the whole
project) — safe even if the internet drops mid-run (learned the hard way
in practice). Also safe with several parallel instances running on
DIFFERENT projects at once (file locking + merging instead of overwriting
the whole thing).

### Setup before the first run

1. **`asana_project_link` in the sheet** — required for every row (see
   section 7). Without it, the project is skipped with a clear warning.

2. **Required fields** — your template project may have fields required
   when creating a task (e.g. custom "Type", "Department") that Asana
   doesn't have. Check them:
   ```bash
   python check_required_fields.py PROJECTKEY
   ```
   Fields with a single allowed value are filled in automatically. The
   "Type" field has a default value of `NONBILLABLE` set (change it in
   `.env`: `DEFAULT_TYPE_FIELD_VALUE=...`, or `DEFAULT_TYPE_FIELD_ID=...`
   if it's a different field than `customfield_10188`).

3. **Section → status mapping** — Asana sections tend to be stages/
   milestones (e.g. "3. Kickoff"), not names matching Jira statuses
   directly ("In Progress"). List the project's sections:
   ```bash
   python list_asana_sections.py "Exact project name in Asana"
   python list_asana_sections.py --link "https://app.asana.com/0/123456789/list"
   ```
   Fill in `section_status_map.json` (key = section name, value = Jira
   status name **or a list of statuses**, e.g.
   `["In progress", "Feedback Required"]` — useful when one section in a
   simple project should reasonably correspond to several more granular
   statuses on the reverse direction). This table is global — shared
   across all projects and BOTH sync directions; add entries as you
   discover new sections. A section without an entry = the task stays at
   its default starting status (the script prints a list of such sections
   at the end of the run).

4. **User mapping** — without this, tasks are imported with no assignee:
   ```bash
   python generate_user_map.py
   ```
   Matches automatically by email address; if Jira addresses follow a
   fixed pattern (`FirstName.LastName@domain`, different from the Asana
   address), the script also tries building such an address from the
   first/last name (with a transliterated variant of German characters
   ü/ö/ä/ß). Domain set via `JIRA_EMAIL_DOMAIN` in `.env` (default
   `your-company.com`). By default **skips guests** in Asana — to include
   them: `python generate_user_map.py --include-guests`. Output:
   `user_map.json`. You can manually add unmatched people to this file
   (`"asana_user_gid": "jira_accountId"`), or find an Asana gid via
   `python list_asana_members.py`.

### Running it

```bash
python asana_jira_sync.py --dry-run --limit 1 --project-keys KEY   # preview, no writes
python asana_jira_sync.py --limit 1 --project-keys KEY             # actual write, 1 project
python asana_jira_sync.py                                           # all projects from the sheet
```

`--dry-run` shows what the script **would** do (reading is safe, so even
in dry-run you'll see the real number of comments/attachments to be
transferred), without actually creating/changing anything in Jira.

**Diagnostics:** set `DEBUG_SYNC=1` before the command to see detailed
information about each task (exact data sent to Jira, section/status,
timestamp comparisons) — useful when something isn't working as expected:
```bash
DEBUG_SYNC=1 python asana_jira_sync.py --limit 1 --project-keys KEY
```

### Files in this package

| File | Role |
|---|---|
| `asana_jira_sync.py` | main sync script, Asana → Jira |
| `jira_asana_sync.py` | reverse direction, Jira → Asana (section 12) |
| `list_asana_sections.py` | lists an Asana project's sections (for `section_status_map.json`) |
| `section_status_map.json` | mapping: Asana section → Jira status(es) (filled in manually) |
| `generate_user_map.py` | generates `user_map.json` (matched by email) |
| `list_asana_members.py` | lists Asana workspace members/guests with their gid |
| `get_group_members.py` | lists Jira group members with their accountId |
| `user_map.json` | mapping: Asana user gid → Jira accountId |
| `check_required_fields.py` | diagnostics: which fields are required when creating a task |
| `list_project_statuses.py` | diagnostics: which statuses actually exist in a given project |
| `check_task_section.py` | diagnostics: checks a task's section via two independent queries |
| `count_state_entries.py` | diagnostics: counts/shows entries in `task_sync_state.json` for a project |
| `notify.py` | file logging + optional Slack notification |
| `task_sync_state.json` | **automatically created** sync state (both directions) — don't delete |
| `workflow_graph_cache.json` | **automatically created** workflow-transition-graph cache — safe to delete |

## 11a. Correct order of steps (IMPORTANT)

Statuses are assigned to board columns **manually** (drag-and-drop
automation turned out to be too unreliable in this Jira version — see
`browser_automation/README.md`). This forces a specific order — task
synchronization (`asana_jira_sync.py`) should NOT be run right after
creating a project, because the board isn't ready yet:

1. `python jira_sync.py` — create the project(s).
2. `python bulk_configure_columns.py` (for rows without `board_template_id`)
   **and/or** `python bulk_configure_columns_simple.py` (for rows with
   `board_template_id` filled in) — create the columns.
3. **You manually** drag statuses into the correct columns in Jira (a
   cheat-sheet is printed at the end of step 2).
4. Only now: `python asana_jira_sync.py` — task syncing makes sense, since
   tasks land on an already-ready, correctly configured board.
5. Optionally, as ongoing work happens: `python jira_asana_sync.py` — the
   reverse direction, carrying changes from Jira to Asana (see section 12).

## 12. Reverse sync direction (`jira_asana_sync.py`, Jira → Asana)

A separate script (not a mode within `asana_jira_sync.py`, because it
iterates differently: over already-linked pairs from `task_sync_state.json`
— a file SHARED with `asana_jira_sync.py` — not over Asana tasks).

### What it synchronizes

- Title, description (ADF → HTML conversion), due date, assignee (via a
  REVERSED `user_map.json`, with a fallback: if the original assignee
  can't be assigned in Asana, the update goes through without them instead
  of failing).
- **Status → section** — via a REVERSED `section_status_map.json`. Since
  one Asana section can correspond to several Jira statuses (and vice
  versa, several sections to the same status), the script **tries all
  candidates in order** until it finds one that actually exists in the
  SPECIFIC Asana project — it doesn't assume the first one on the list
  always fits.
- New comments and attachments from Jira to Asana.

**Conflict resolution:** a simple comparison of the last-modified
timestamp in Jira (`updated`) — **there's no full field merging** if both
systems changed the same task at the same time; Jira's state wins.

### Running it

```bash
python jira_asana_sync.py --dry-run --limit 1   # preview, without --project-keys (see below)
python jira_asana_sync.py --limit 1
python jira_asana_sync.py
DEBUG_SYNC=1 python jira_asana_sync.py --limit 1   # with detailed diagnostics
```

**Important about `--project-keys`:** if you provide specific keys with
this flag, the script has no way to get `asana_project_link`/the project
name from the sheet for those keys — status → section sync **won't work**
in this mode (you'll get a clear warning). To test on 1 project, use
`--limit 1` **without** `--project-keys` — then the script takes the first
row from the sheet along with its link.

**Order when using both directions:** run them alternately
(`asana_jira_sync.py`, then `jira_asana_sync.py`), not in parallel — this
avoids a situation where both overwrite each other mid-run.

## 13. Additional features

- **File logging + Slack summary** — the `notify.py` module, used by
  `jira_sync.py`, `asana_jira_sync.py`, and `jira_asana_sync.py`. Logs go
  to `logs/<script>_<date-time>.log` automatically (nothing to enable).
  Slack is optional — set in `.env`:
  ```
  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
  ```
  Without this, the scripts simply don't send anything to Slack (no
  error). Email hasn't been implemented (requires SMTP details) — let me
  know if you need it.
- **Updating existing projects** — `jira_sync.py` doesn't just skip
  already-existing projects, it updates their **description** if it
  differs from the sheet/Asana (see the summary: "Description updated").

Let me know if you'd like to add or change anything else in any of these
mechanisms.
