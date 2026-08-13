# Synchronization: Google Sheets (selected Asana projects) → Jira

The script creates projects in Jira **only for items present in the Google Sheet** — 
the spreadsheet serves as the single source of truth regarding what should be created in Jira.

Additionally, the script fetches a list of projects from Asana to enrich the new Jira project's description with Asana notes (if a name match is found). If a given row from the spreadsheet has no counterpart in Asana, the project will still be created — simply without a description.

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
     — remember to **share the spreadsheet** with the service account's email address.

## 3. Board Type

By default, **all created projects are company-managed Kanban boards**
(classic, managed centrally by a Jira admin — requires administrator permissions to create projects of this type). If you prefer team-managed projects (simpler, configured independently by the team), `.env.example` includes a ready-to-use, commented-out line with the corresponding template key.

## 4. Copying Configuration from a Reference Project

If you want new projects to have **the same workflow, screen layout, and fields** as an existing reference project in Jira, set the following in `.env`:

```
JIRA_TEMPLATE_PROJECT_KEY=YOURKEY
```

The script will then copy the following from the reference project for every newly created project:

- **workflow** (workflow scheme),
- **layout** — screen scheme assigned to issue types (issue type screen scheme),
- **fields** — field configuration scheme,
- **issue types** — issue type scheme,
- permissions, notifications, and issue security scheme (if the reference project uses custom ones — otherwise, Jira's defaults will be used).

**Limitations to keep in mind:**
- Works **only for company-managed (classic) projects** — which is our default type anyway (see section 3).
- Requires **Jira administrator permissions** (*Administer Jira* global permission) — without them, Jira will return a 403 error when attempting to assign the workflow/layout/fields.
- Workflow, layout, and fields are assigned in a separate step **immediately after** creating the empty project — this only works as long as the project contains no issues, which is always true for a freshly created project.
- If assigning any scheme fails (e.g., due to missing permissions), the script **does not delete** the created project — it reports this as a warning in the summary so you can fix it manually in Jira.
- `--dry-run` mode does not test the configuration copying itself (since the project does not exist yet) — it only shows that the configuration would be copied.

**Important limitation: Kanban board columns.** Jira **does not provide a public API for setting board columns** — this is the only visual configuration element that cannot be copied automatically. The workflow, statuses, screens, and fields will be identical to the template, but the new board will receive Jira's default column layout (usually one status = one column). To make it easy to recreate the layout manually from the template, run:
```bash
python show_template_board_columns.py
```
This will print the exact column layout (names + which statuses belong to which column) from the template board — simply drag and drop the statuses into the same places on the new board (Board → Configure → Columns), which takes just a moment. For a large number of projects, see also `browser_automation/` — Playwright automation for this step.

## 5. Automatic Developer Group Assignment

If you want a specific Jira group to be automatically added to the "Developers" role (or another role) in every newly created project, set the following in `.env`:

```
DEVELOPER_GROUP_NAME=group-name
DEVELOPER_ROLE_NAME=Developers
```

The script checks once at startup whether such a role exists in Jira. If it does, after each successful project creation, it adds the group to that role (it appends, without overwriting any other actors already assigned to the role). If no role with the given name exists, the script prints a warning at startup and skips adding the group to any project (to avoid making 550 pointless API calls).

Requires *Administer Projects* permission for the created projects or *Administer Jira* — the token used in `.env` must have these permissions; otherwise, you will get a per-project warning in the summary (the project will still be created, just without the added group — you will need to add it manually).

Not sure about the exact group or role name? Run:
```bash
python list_groups.py          # all groups (optionally filtered by substring)
python list_project_roles.py   # all project roles
```

## 6. Automatic Administrator Assignment

Similarly, you can add a **specific user** (e.g., yourself) to the administrator role in every newly created project:

```
ADMIN_ACCOUNT_ID=your-accountId
ADMIN_ROLE_NAME=Administrators
```

`ADMIN_ACCOUNT_ID` is the `accountId`, not an email address — you can find it using:
```bash
python get_my_account_id.py
```

Works identically to the developer group (section 5): checks the role once at startup, adds it after each project creation, and logs a warning instead of a hard error if it fails.

## 7. Google Sheet Format

The first row contains headers (case-insensitive). **Only the `project_name` column is required.** The rest are optional:

| project_name               | jira_key | project_type_key | template_key | lead_account_id        | description | board_template_id | asana_project_link |
|-----------------------------|----------|--------------------|-----------------|--------------------------|--------------|--------------------|----------------------|
| Q4 Campaign                 |          | software           |                 | 5b10a2844c20165700ede21 |              |                    | https://app.asana.com/0/1234567890123456/list |
| Homepage Redesign           |          | business           |                 |                          | UI Redesign  | 7276               |                      |

- **`board_template_id`** — used ONLY by `bulk_configure_columns.py` (not by `jira_sync.py`). The reference board ID for the COLUMN LAYOUT of this specific project — useful when the template project has several boards with different layouts (e.g., `REFERENCE2` has both an 11-column template on board 6110 and a simpler 4-column one on boards 7276/7273/7274/7275). If blank, the default template (`JIRA_TEMPLATE_BOARD_ID` from `.env`) will be used.

- **`asana_project_link`** — optional DIRECT link to the project in Asana (copied from the browser address bar). If provided, it is used INSTEAD of matching by name — much more reliable as it avoids typos, duplicate names, and project name changes in Asana. Supported formats: `https://app.asana.com/0/<id>/list` and `https://app.asana.com/1/<workspace>/project/<id>/list` (and `/board` variants instead of `/list`). If blank, name-based matching is used as before. Used by `jira_sync.py` (description enrichment) and `asana_jira_sync.py` (task synchronization).

- **`jira_key` is now optional.** If left blank (or if the column is omitted entirely), the script automatically generates a key based on the project name (e.g., "Q4 Campaign" → `QC`, "Homepage Redesign" → `HR`). If you prefer to control keys manually, simply enter them in this column.
- Generated keys are saved to a local file `project_key_cache.json` (path configurable via `PROJECT_KEY_CACHE_FILE` in `.env`), ensuring **the same project name always gets the same key** across subsequent script runs — no need to worry about consistency.
- `lead_account_id` is the user's `accountId` in Jira (not an email!). You can find it via `GET /rest/api/3/user/search?query=email@company.com`, or more simply — run the included `python get_my_account_id.py email@company.com`. If a row lacks a lead, `DEFAULT_LEAD_ACCOUNT_ID` from `.env` will be used (if set). **Jira requires a valid lead when creating a project** — if both fields are empty, the script will report a clear error instead of sending a request that would be rejected anyway.

## 8. Running the Script

First, run a preview without creating anything:
```bash
python jira_sync.py --dry-run
```

Actual project creation:
```bash
python jira_sync.py
```

The script is **idempotent** — if a project with a given key already exists in Jira, it will be skipped. You can run it multiple times (e.g., whenever you add a new row to the sheet) without risk of duplicates.

## 9. How Key Generation Works

For a project name without a specified `jira_key`:
1. Multiple words → initials of each word (e.g., "B2B Customer Portal" → `BCP`).
2. Single word → first up to 10 characters in uppercase.
3. If the generated key conflicts with one already assigned in the same run (or in the cache), a number is appended (`BCP2`, `BCP3`, ...).
4. The result is written to `project_key_cache.json` to remain persistent across runs.

If you want full control over a specific key, simply enter it manually in the `jira_key` column for that row, and the script will not overwrite it.

## 10. Summary Output

At the end of execution, the script displays:
- Number of created and skipped projects,
- Errors (e.g., missing permissions, missing lead, taken key),
- Sheet rows without a match in Asana (the project is still created, just without a description),
- Path to the name-to-key mapping file.

## 11. Task Synchronization: Asana → Jira (`asana_jira_sync.py`)

A standalone script (alongside `jira_sync.py`, which only creates *projects*) — synchronizes *tasks* inside already created projects. One-way: Asana → Jira.

### What it synchronizes

- Title, description, assignee, due date — always.
- Status — via Asana sections mapped to Jira statuses (see below).
- Comments — real ones only (excluding system events like "changed due date"), prefixed with `[Asana — Author, date]` in the body (Jira does not allow impersonating original authors without extra configuration).
- Attachments — downloaded from Asana and uploaded to Jira.

### Preventing duplicates on repeated runs

The local `task_sync_state.json` file (created automatically, **do not delete between runs**) keeps track of each task: its Jira key, latest known `modified_at` from Asana, and IDs of already migrated comments and attachments. Saved after each project (not just at the end), making it safe if interrupted midway.

### Configuration before first run

1. **Required fields** — Your template project might have mandatory fields during issue creation (e.g., custom "Type", "Department") that Asana doesn't have. Check them:
   ```bash
   python check_required_fields.py PROJECTKEY
   ```
   Fields with only one allowed value are populated automatically. The "Type" field has a default value set to `NONBILLABLE` (change in `.env`: `DEFAULT_TYPE_FIELD_VALUE=...`, or `DEFAULT_TYPE_FIELD_ID=...` if it uses a field other than `customfield_10188`).

2. **Asana section → Jira status mapping** — Asana sections are sometimes stages/milestones (e.g., "3. Kickoff") rather than names mapping directly to Jira statuses ("In Progress"). List the project sections:
   ```bash
   python list_asana_sections.py "Exact Asana Project Name"
   ```
   Populate `section_status_map.json` (key = section name, value = Jira status name). This table is global — shared across all projects; add entries as you discover new sections. Unmapped sections = task stays at the default initial status (the script prints a list of such sections at the end of the run).

3. **User mapping** — Without this, tasks import unassigned:
   ```bash
   python generate_user_map.py
   ```
   Matches automatically by email address; if Jira emails follow a strict pattern (`First.Last@domain`, differing from Asana emails), the script also attempts to construct such an address from first/last names (handling transliteration for German characters ü/ö/ä/ß). Domain set via `JIRA_EMAIL_DOMAIN` in `.env` (default: `your-company.com`). By default, it **skips Asana guests** — to include them: `python generate_user_map.py --include-guests`.
   Output: `user_map.json`. Unmatched users can be added manually to this file (`"asana_user_gid": "jira_accountId"`).

### Running execution

```bash
python asana_jira_sync.py --dry-run --limit 1 --project-keys KEY   # preview without saving
python asana_jira_sync.py --limit 1 --project-keys KEY              # actual run, 1 project
python asana_jira_sync.py                                           # all projects from sheet
```

`--dry-run` shows what the script **would** do (read actions are safe, so even in dry-run you will see the actual number of comments/attachments to transfer), without creating or modifying anything in Jira.

### Conscious omissions for now (Phase 3)

- Reverse direction Jira → Asana (currently Asana → Jira only).
- Rich formatting conversion from Asana (links, lists, bold) — descriptions and comments land as plain text.

### Files in this package

| File | Role |
|---|---|
| `asana_jira_sync.py` | Main synchronization script |
| `list_asana_sections.py` | Lists sections of an Asana project (for `section_status_map.json`) |
| `section_status_map.json` | Mapping: Asana section → Jira status (populated manually) |
| `generate_user_map.py` | Generates `user_map.json` (email matching) |
| `user_map.json` | Mapping: Asana user GID → Jira accountId |
| `check_required_fields.py` | Diagnostic tool: required fields for task creation |
| `task_sync_state.json` | **Automatically generated** sync state — do not delete |

## 11a. Proper Execution Order (IMPORTANT)

Statuses are assigned to board columns **manually** (drag-and-drop automation proved too unreliable in this Jira version — see `browser_automation/README.md`). This dictates a specific sequence — task sync (`asana_jira_sync.py`) should **NOT** be executed immediately after project creation, as the board is not ready yet:

1. `python jira_sync.py` — create project(s).
2. `python bulk_configure_columns.py` (or `..._simple.py`) — create columns.
3. **You manually** drag and drop statuses into correct columns in Jira (cheat sheet printed at the end of step 2).
4. Only now: `python asana_jira_sync.py` — task sync makes sense now, as tasks will land on a fully prepared, correctly configured board.

## 12. Additional Features (Implemented)

Everything listed in the previous README version as a "possible extension" has now been implemented:

- **Rich Formatting from Asana** (links, lists, bold/italic/underline/strikethrough) — converted to ADF (description/comments in Jira) and back to HTML (during reverse sync). See `html_to_adf`/`adf_to_html` in `asana_jira_sync.py`.
- **Updating Existing Projects** — `jira_sync.py` not only skips existing projects, but updates their **description** if it differs from the one in the sheet/Asana (see summary: "Updated description").
- **File Logging + Slack Summary** — new `notify.py` module, used by `jira_sync.py` and `asana_jira_sync.py`. Logs automatically go to `logs/<script>_<datetime>.log` (no setup required). Slack is optional — set in `.env`:
  ```
  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
  ```
  Without this, scripts simply skip sending Slack messages (without throwing an error). Email notification was not implemented (requires SMTP details) — let me know if needed, and I can add it similarly.
- **Reverse Synchronization (Jira → Asana)** — separate script `jira_asana_sync.py` (not a mode in `asana_jira_sync.py`, as it iterates differently: over already mapped pairs from `task_sync_state.json`, not Asana tasks). Synchronizes title, description, due date, assignee (via inverted `user_map.json`), and new comments/attachments from Jira to Asana. Conflict resolution: simple timestamp comparison of last modification in Jira — **no full field merging** if both systems modified the same task simultaneously. Status is NOT synced backwards (Asana sections and Jira workflow statuses are different concepts, lacking a natural 1:1 reverse mapping).
  ```bash
  python jira_asana_sync.py --dry-run --limit 1 --project-keys KEY
  python jira_asana_sync.py
  ```
  **Order when using both directions:** run alternately (`asana_jira_sync.py`, then `jira_asana_sync.py`), not in parallel — to prevent them from overwriting each other during execution.

I tried Playwright to move statuses to columns, but unfortunately without better results.  
Let me know if you would like to add or change anything else in any of these mechanisms!
