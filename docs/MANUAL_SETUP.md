# Manual Setup — Anvil IDE Configuration

This document captures every step that must be performed inside the **Anvil web IDE** (https://anvil.works) because it cannot be reliably performed by editing files in this repository.

The source of truth for table shapes and auth behaviour is `IMPLEMENTATION_SPEC.md` §1 and §5. This document is the operational checklist for translating those sections into IDE actions.

Three pending decisions in the spec affect schema and are flagged inline below: Decision 1 (`assessments.confidence` / `source_text`), Decision 2 (`user_settings.timezone`), Decision 3 (`reminder_logs.assessment_id`). The spec currently assumes A / A / C; tables below follow those assumptions.

---

## 0. Create the app and link to this repository

1. Sign in at https://anvil.works → **New Blank App** → choose the *Material Design 3* theme (any theme works; the spec does not pin one).
2. App name: **DotPoint**.
3. In the app's gear menu → **Share App** → **Share via Source Control** → **Connect to GitHub** → select this repository (`DotPointAnviI`).
4. Anvil will create or overwrite `anvil.yaml`. The starter `anvil.yaml` committed in this repo is a best-effort scaffold; if Anvil's IDE-generated version differs, **accept the IDE's version** — it has internal IDs the IDE manages.

---

## 1. Enable the Tables service

1. Sidebar → **Services** → **+ Add Service** → **Data Tables**. Already added by `anvil.yaml`; verify it shows.
2. Click **Data Tables** in the sidebar to open the table editor.

### Anvil-specific notes that affect the table definitions below

- **All Anvil columns are nullable at the storage layer.** The "Required" and "Default" columns in `IMPLEMENTATION_SPEC.md` §1 are *application invariants enforced by server functions* (`assessments.create_assessment`, `notes.create_note`, etc.), not column attributes you set in the IDE. Configure only **name**, **type**, and (where marked) **indexed**.
- **`simpleObject` columns** store any JSON-serialisable value: `dict`, `list`, `str`, `int`, `float`, `bool`, `None`. The spec's typing details (e.g. `list[int]`) are enforced in code, not the IDE.
- **`link to row` columns** are configured by choosing target table from a dropdown after picking column type "Linked column → Single row".
- **Indexes** are toggled per-column under the column's overflow menu (`⋯` → **Index this column**). Indexes speed up `app_tables.<t>.get(col=val)` and `search(col=val)`.
- **Permissions**: for every table, set the row's *Form Server* permission to **Can search, edit, delete** and *Client* permission to **No access**. All data access must go through `@anvil.server.callable` functions per §5.

---

## 2. Data tables (create each one)

### 2.1 `assessments`

Create a new table named `assessments`. Add the following columns in this order:

| # | Column name | Type | Indexed | Notes |
|---|-------------|------|---------|-------|
| 1 | `title` | Text | no | App enforces max 200 chars |
| 2 | `subject` | Text | **yes** | App restricts to `SUBJECT_ALIASES.values()` |
| 3 | `type` | Text | **yes** | App enum: `'sac' \| 'sat' \| 'exam' \| 'project' \| 'homework' \| 'other'` |
| 4 | `due_date` | Date | **yes** | |
| 5 | `start_date` | Date | no | |
| 6 | `weight` | Number | no | App restricts to `0.0 ≤ w ≤ 100.0` |
| 7 | `status` | Text | **yes** | App enum: `'not_started' \| 'in_progress' \| 'completed'`; app default `'not_started'` |
| 8 | `description` | Text | no | |
| 9 | `reminder_days` | Simple Object | no | App stores `list[int]`; default `[7, 2]` |
| 10 | `linked_note_ids` | Simple Object | no | App stores `list[str]` of `notes.get_id()` values; default `[]` |
| 11 | `term_info` | Text | no | Audit string e.g. `"Term 1, Week 4B"` |
| 12 | `confidence` | Text | no | **Decision 1.** App enum: `'HIGH' \| 'MEDIUM' \| 'LOW' \| None` |
| 13 | `source_text` | Text | no | **Decision 1.** Raw parser input; `None` for manual entries |
| 14 | `user` | Linked column → Single row → **users** table | **yes** | Set on insert; never edited |
| 15 | `created_at` | Date and Time | **yes** | Set on insert (UTC) |
| 16 | `updated_at` | Date and Time | **yes** | Reset on every update (UTC) |

Permissions: **Form Server: full**, **Client: none**.

### 2.2 `notes`

| # | Column name | Type | Indexed | Notes |
|---|-------------|------|---------|-------|
| 1 | `title` | Text | no | App enforces max 200 chars |
| 2 | `content` | Text | no | Markdown; rendered client-side |
| 3 | `tags` | Simple Object | no | App stores `list[str]`; default `[]` |
| 4 | `is_pinned` | True/False | **yes** | App default `False` |
| 5 | `user` | Linked column → Single row → **users** | **yes** | |
| 6 | `created_at` | Date and Time | **yes** | UTC |
| 7 | `updated_at` | Date and Time | **yes** | UTC |

Permissions: **Form Server: full**, **Client: none**.

### 2.3 `user_settings`

One row per user; singleton-per-user enforced in code.

| # | Column name | Type | Indexed | Notes |
|---|-------------|------|---------|-------|
| 1 | `user` | Linked column → Single row → **users** | **yes** | Logical uniqueness enforced by `_get_or_create_settings(user)` |
| 2 | `theme` | Text | no | App enum: `'light' \| 'dark'`; default `'dark'` |
| 3 | `default_reminder_days` | Simple Object | no | `list[int]`; default `[7, 2]` |
| 4 | `notifications_enabled` | True/False | **yes** | App default `True`; master gate for reminder emails |
| 5 | `school_year` | Number | no | Integer e.g. `2026` |
| 6 | `school_terms` | Simple Object | no | `list[dict]`: `{term:int, start_date:'YYYY-MM-DD', end_date:'YYYY-MM-DD'}`; default `[]` |
| 7 | `timezone` | Text | no | **Decision 2.** IANA name; default `'Australia/Melbourne'` |

Permissions: **Form Server: full**, **Client: none**.

### 2.4 `reminder_logs`

Insert-only; dedup key is the logical tuple `(assessment_id, user, reminder_type)`.

| # | Column name | Type | Indexed | Notes |
|---|-------------|------|---------|-------|
| 1 | `assessment_id` | Text | **yes** | **Decision 3.** Stores `assessment_row.get_id()`; NOT a row link |
| 2 | `user` | Linked column → Single row → **users** | **yes** | |
| 3 | `sent_date` | Date | **yes** | User-local date when email sent |
| 4 | `reminder_type` | Text | **yes** | Format `'{N}_day'`, e.g. `'7_day'`, `'2_day'` |

Permissions: **Form Server: full**, **Client: none**.

---

## 3. Users service (per §5)

1. Sidebar → **Services** → **+ Add Service** → **Users**. Already added by `anvil.yaml`; verify it shows.
2. Click **Users** in the sidebar → open the service settings panel and confirm:
   - **Allow users to log in with email + password**: ON
   - **Allow users to sign up**: ON
   - **Allow users to stay logged in (remember me)**: ON, **7 days**
   - **Allow users to reset their password by email**: ON
   - **Require email confirmation**: OFF (the spec does not require it)
   - **Require MFA**: OFF
   - **Require secure passwords**: ON
   - **Lock out after N failed attempts**: 10
3. **Do NOT add custom columns to the `users` table.** Per §5, no extra columns; per-user data lives in `user_settings`, `assessments`, `notes`, `reminder_logs`.

The `users` table will be auto-created by enabling the Users service; it provides `email`, `password_hash`, `enabled`, `confirmed_email`, `last_login`, `signed_up`.

---

## 4. Python packages (per §7)

1. Sidebar → **Settings** → **Python versions and packages**.
2. Confirm runtime is **Python 3.10 (Full Python)** (matches `anvil.yaml`).
3. Under **Packages**, add: **`dateparser`** (latest). Apply.

`dateparser` is the only third-party Python dependency. No external APIs.

---

## 5. Scheduled tasks (per §2, `reminders.py`)

`reminders.run_reminder_check` is decorated `@anvil.server.background_task` and runs every 30 minutes (Anvil's documented minimum).

1. Sidebar → **Scheduled Tasks** → **+ Add Scheduled Task**.
2. Name: `run_reminder_check`.
3. Server function: `run_reminder_check` (from `reminders.py`).
4. Schedule: **Every 30 minutes**.
5. Save.

*Note:* this task will fail to register until `reminders.py` actually defines `run_reminder_check`. Add the schedule entry after Workflow 5B implements the function.

---

## 6. App Secrets (per §2, `trigger_reminder_check_now`)

For the dev-only manual reminder trigger:

1. Sidebar → **App Secrets** → **+ Add Secret**.
2. Name: `DEV_EMAIL`.
3. Value: the developer's email address (the one logged in as during testing).

Without this secret, `trigger_reminder_check_now` will raise `PermissionError("dev only")` for everyone.

---

## 7. Verification checklist

After all of the above:

- [ ] Four data tables exist: `assessments`, `notes`, `user_settings`, `reminder_logs`.
- [ ] Each table has the columns in the right order, with the right types, with the indexes marked.
- [ ] Each table's Client permission is **No access**; Form Server permission is full.
- [ ] Users service enabled; settings match §3 above; `users` table has no custom columns.
- [ ] `dateparser` listed in Python packages.
- [ ] Scheduled task `run_reminder_check` configured (deferred until Workflow 5B).
- [ ] `DEV_EMAIL` secret set.
- [ ] `anvil.yaml` startup form is `LoginForm`.
