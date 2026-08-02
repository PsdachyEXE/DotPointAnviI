# DotPoint Assessment Tracker — Implementation Spec (Anvil Port)

Single source of truth for porting `C:\Coding\DotPoint` (Vite/React/TypeScript) to Anvil (Python, fully programmatic). Authoritative for Claude Code; sections are self-contained.

---

## Decisions Needed

The upstream documents are silent or in tension on the following. Each blocks a definite spec entry; the recommended choice is shown but not yet adopted.

1. **`assessments.confidence` and `assessments.source_text` columns** — FR17 audit fields.
   - Solution Analysis Assessment data dictionary (12 fields) lists neither. FR17 + EC-UX-07 require showing the confidence badge on a previously-parsed record and explaining how each parsed value was inferred, both of which break after the first edit if the parser output is not persisted.
   - Options: (A) Add both as nullable `text` columns, set by `nlp.parse_text`, `None` for manual entries, excluded from `EDITABLE_FIELDS`. (B) Omit; badge shows once at parse time and is lost.
   - **Recommend (A).** Cost: two columns. Benefit: FR17 audit survives edits.

2. **`user_settings.timezone` column** — date-math correctness.
   - Anvil server is UTC. `(due_date - today).days` and the reminder window check both depend on which date "today" is. Solution Analysis user_settings dictionary (6 fields) does not include a timezone column.
   - Options: (A) Add `timezone: text`, default `'Australia/Melbourne'`; one UTC→local conversion at the top of `nlp.parse_text`, `reminders.run_reminder_check`, `dashboard.get_dashboard_data`. (B) Hardcode `'Australia/Melbourne'` as a module constant. (C) Hardcode `'Australia/Darwin'`.
   - **Recommend (A).** Cost: one column + one helper. Benefit: correct for any AU timezone without redeploy.

3. **`reminder_logs.assessment` link semantics on assessment delete** — log retention vs. row-ref integrity.
   - Solution Analysis dictionary specifies the column as "Row ref / Anvil row reference". Doc intent (inventory §3.3.2, §6) requires `reminder_logs` to be retained permanently for audit. Anvil's link-to-row column with `link_action='restrict'` (default) blocks the assessment delete; `link_action='cascade'` destroys the audit trail; neither matches both constraints.
   - Options: (A) Link-to-row with `cascade`; lose audit for deleted assessments. (B) Link-to-row with `restrict`; require app-code cleanup before assessment delete. (C) Store `assessment_id: text` (string FK, no Anvil link); audit survives, no resolution helper needed because `assessment_id` is the dedup key, not a navigation target.
   - **Recommend (C).** The audit value of `reminder_logs` is the existence of the row keyed by `(assessment_id, user, reminder_type)`. The only consumer that ever resolves `assessment_id → row` is `delete_assessment`, which is precisely the case where the row no longer exists. Choosing (C) costs the Solution Analysis a one-line dictionary correction.

Until these three are resolved, the spec below assumes (A), (A), (C). All three are flagged inline at their points of use.

---

## 0. Glossary & Conventions

### Naming
- **Data tables**: `snake_case`, plural. `assessments`, `notes`, `user_settings`, `reminder_logs`.
- **Server module files**: `snake_case.py`. One module per concern: `nlp.py`, `assessments.py`, `notes.py`, `reminders.py`, `dashboard.py`.
- **Server functions**: `snake_case`, verb-first. `parse_text`, `create_assessment`, `list_assessments`.
- **Forms**: `PascalCase`, suffix `Form`. `DashboardForm`, `AssessmentEditorForm`, `NoteEditorForm`, `SettingsForm`, `LoginForm`, `ImportExportForm`.
- **Form instance variables**: prefix `self._` for private state, no prefix for components added to the layout.
- **Constants**: `UPPER_SNAKE_CASE` at module top. `SUBJECT_ALIASES`, `TYPE_KEYWORDS`, `EDITABLE_FIELDS`.

### Anvil project layout (GitHub-backed)
```
DotPoint/
  anvil.yaml              # app config; references startup form and scheduled tasks
  client_code/
    DashboardForm/__init__.py
    AssessmentEditorForm/__init__.py
    NoteEditorForm/__init__.py
    SettingsForm/__init__.py
    LoginForm/__init__.py
    ImportExportForm/__init__.py
    ParserPreviewForm/__init__.py
    common/__init__.py     # shared helpers: format_date_au, urgency_colour, etc.
  server_code/
    nlp.py
    assessments.py
    notes.py
    reminders.py
    dashboard.py
    _constants.py          # SUBJECT_ALIASES, TYPE_KEYWORDS, STATUS_KEYWORDS, URGENCY_THRESHOLDS
    _auth.py               # _require_user(), _own_or_raise(row)
    _datetime.py           # _user_today(user_settings_row), _user_now(user_settings_row)
  theme/
    parameters.yaml
```

### "Fully programmatic" forms (no visual designer)
Every form is a Python class that subclasses an Anvil container (typically `ColumnPanel` or `HtmlPanel`) and builds its child tree in `__init__`. The form is not paired with a `.yaml` template. Pattern:

```python
from anvil import ColumnPanel, Label, TextBox, Button
import anvil.server

class DashboardForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        # build UI here by instantiating components and self.add_component(...)
```

No `self.init_components()` call — that helper is for designer-backed forms.

### Type hints in server functions
Every `@anvil.server.callable` function declares argument and return types using Python 3.10+ syntax. Return types use `dict`, `list[dict]`, `anvil.tables.Row`, `bool`, `int`, `str`, `None`, or unions. Anvil rows are typed as `anvil.tables.Row`.

### Common helpers (defined once, referenced throughout)
- `_require_user() -> anvil.tables.Row`: returns the logged-in user row or raises `anvil.users.AuthenticationFailed`. Defined in `server_code/_auth.py`.
- `_own_or_raise(row, user) -> None`: raises `PermissionError("Not your record")` if `row['user'] != user`.
- `_user_today(user_settings_row) -> datetime.date`: returns today's date in the user's timezone. Defined in `server_code/_datetime.py`.
- `_user_now(user_settings_row) -> datetime.datetime`: timezone-aware now in user-local time.
- `_format_date_au(d: date) -> str`: returns `DD MMM YYYY` (NFR08). Used in both server (email bodies, JSON export) and client (display).
- `_urgency_colour(days_remaining: int) -> str`: returns one of `'overdue' | 'today' | 'soon' | 'distant'` per FR21 thresholds.

### URL hash scheme
- Routing via `anvil.set_url_hash(hash, set_in_history=True)` and `anvil.get_url_hash()`.
- Hash format: `'#dashboard'`, `'#notes'`, `'#settings'`, `'#import-export'`, `'#login'`. No parameterised routes; opening a record uses an in-memory form parameter, not a URL.

### Error classes (raised by server functions; caught by client)
- `anvil.users.AuthenticationFailed` — not logged in.
- `PermissionError` — logged in but trying to access another user's row.
- `ValueError` — input validation failure (weight out of range, invalid enum, etc.).
- `anvil.tables.TableError` — Anvil row not found / type mismatch (Anvil-raised, not authored here).

## 1. Data Tables

> **Indexes deferred** — Data Table Indexes are an Anvil Business-only feature. All `indexed` annotations have been removed. Re-evaluate if any table exceeds ~10,000 rows in production; DotPoint is not expected to approach this threshold.

### Table: `assessments`

| Column | Anvil type | Required | Default | Notes |
|---|---|---|---|---|
| `title` | text | yes | — | Max 200 chars (validated server-side) |
| `subject` | text | yes | — | Must be a value from `SUBJECT_ALIASES.values()` |
| `type` | text | yes | — | Enum: `'sac' \| 'sat' \| 'exam' \| 'project' \| 'homework' \| 'other'` |
| `due_date` | date | yes | — | |
| `start_date` | date | no | None | |
| `weight` | number | no | None | Float, 0.0 ≤ weight ≤ 100.0 |
| `status` | text | yes | `'not_started'` | Enum: `'not_started' \| 'in_progress' \| 'completed'` |
| `description` | text | no | None | |
| `reminder_days` | simpleObject | yes | `[7, 2]` | `list[int]`; each value > 0 |
| `linked_note_ids` | simpleObject | yes | `[]` | `list[str]`; each value is a note row ID (Anvil `get_id()`) |
| `term_info` | text | no | None | Audit string e.g. `"Term 1, Week 4B"` |
| `confidence` | text | no | None | **Pending Decision 1.** Enum: `'HIGH' \| 'MEDIUM' \| 'LOW' \| None` |
| `source_text` | text | no | None | **Pending Decision 1.** Raw parser input; `None` for manual entries |
| `user` | link to row (Users) | yes | `anvil.users.get_user()` | Set on insert; never edited |
| `created_at` | datetime | yes | server now (UTC) | Set on insert |
| `updated_at` | datetime | yes | server now (UTC) | Reset on every update |

- **Relations**: `user` → built-in Users table; `linked_note_ids` → `notes.<row id>` (resolved app-side, no Anvil link).
- **Access**: Form Server only. No client-side `app_tables` access. All reads/writes via server functions in `assessments.py` and `dashboard.py`.
- **Migration**: None. App is new in Anvil; the existing TypeScript/IndexedDB data is not imported (offline-only mode is dropped per REQUIREMENTS_COVERAGE).

### Table: `notes`

| Column | Anvil type | Required | Default | Notes |
|---|---|---|---|---|
| `title` | text | yes | — | Max 200 chars |
| `content` | text | yes | `''` | Markdown; rendered with markdown library client-side |
| `tags` | simpleObject | yes | `[]` | `list[str]`; case-preserved, comparisons case-insensitive |
| `is_pinned` | bool | yes | `False` | |
| `user` | link to row (Users) | yes | `anvil.users.get_user()` | |
| `created_at` | datetime | yes | server now (UTC) | |
| `updated_at` | datetime | yes | server now (UTC) | |

- **Note on `content`**: Solution Analysis specifies markdown (UC5 data dictionary). Inventory §3 NoteEditor intent annotation claims "plain-text only per SRS constraint" — this contradicts the Solution Analysis and the existing code (which uses `react-markdown`). Solution Analysis is canonical per the spec mandate; markdown stands. Field name is `content`, not `body`.
- **Access**: Form Server only. All access via `notes.py`.
- **Migration**: None.

### Table: `user_settings`

| Column | Anvil type | Required | Default | Notes |
|---|---|---|---|---|
| `user` | link to row (Users) | yes | — | One row per user; created on first login. **Uniqueness was previously enforced by a unique index; now enforced exclusively by the `_get_or_create_settings(user)` helper — see callout below.** |
| `theme` | text | yes | `'dark'` | Enum: `'light' \| 'dark'`. No UI control in MVP (column exists for FR-S01 stretch) |
| `default_reminder_days` | simpleObject | yes | `[7, 2]` | `list[int]` |
| `notifications_enabled` | bool | yes | `True` | Master gate for `reminders.run_reminder_check` |
| `school_year` | number | no | None | Integer, e.g. `2026` |
| `school_terms` | simpleObject | no | `[]` | `list[dict]`: each `{'term': int, 'start_date': 'YYYY-MM-DD', 'end_date': 'YYYY-MM-DD'}` |
| `timezone` | text | yes | `'Australia/Melbourne'` | **Pending Decision 2.** IANA name |

- **Singleton per user**: enforced by `_get_or_create_settings(user)` helper in `notes.py` (or wherever first needed); never two rows for the same user.
- **Access**: Form Server only.
- **Migration**: None. A row is created on first login by the `LoginForm` post-login hook.

### Table: `reminder_logs`

| Column | Anvil type | Required | Default | Notes |
|---|---|---|---|---|
| `assessment_id` | text | yes | — | **Pending Decision 3.** Stores `assessment_row.get_id()`; not a link-to-row |
| `user` | link to row (Users) | yes | — | |
| `sent_date` | date | yes | — | User-local date the email was sent |
| `reminder_type` | text | yes | — | Format: `'{N}_day'`, e.g. `'7_day'`, `'2_day'` |

- **Dedup key (logical, enforced in app code, not by Anvil)**: `(assessment_id, user, reminder_type)`. `sent_date` is stored for audit but is not part of the key.
- **Insert-only**: rows are never updated or deleted. No 30-day TTL cleanup (the current code's `cleanupOldReminderLogs` is dropped — see §8).
- **Access**: Form Server only. Written exclusively by `reminders.run_reminder_check`.
- **Migration**: None.

---

## Note on `user_settings` uniqueness

The `user_settings.user` column originally had `indexed: yes (unique)`. The "unique" part was doing real work — it was a database-level guarantee that you can never have two settings rows for the same user, even if your app code has a race condition or a bug. Without it, you're relying entirely on `_get_or_create_settings(user)` being correct, atomic, and the only code path that ever inserts into `user_settings`.

For a single-user app this is fine in practice. Required mitigations:

1. Make `_get_or_create_settings(user)` the **only** function that ever calls `app_tables.user_settings.add_row(...)`. No other code path inserts. Enforce this by convention and a code-review pass.
2. Inside that helper, do `search → if exists return → else add_row`. Be aware Anvil server modules don't give you true transactional isolation across this read-then-write, so two concurrent first-logins for the same user could theoretically both pass the search and both insert. Risk is negligible for this use case, but acknowledged here.
3. Add a server-side sanity-check function (e.g. `assert_settings_integrity()`) that searches for duplicate `user_settings` rows and logs if any exist. Run manually during dev as cheap insurance.


## 2. Server Modules

### `server_code/_constants.py`

Module-level immutable constants. No functions, no callables.

```
SUBJECT_ALIASES: dict[str, str]
    Maps lowercased alias → canonical subject name.
    Must cover at least: 'math', 'maths', 'methods', 'spec' / 'specialist',
    'further', 'eng', 'english', 'chem', 'bio', 'phys', 'swd' / 'software',
    'geo' / 'geography', 'pe' / 'phys ed'.
    Canonical names used in the UI: 'Mathematics', 'English', 'Software Development',
    'Geography', 'Physical Education', 'Chemistry', 'Biology', 'Physics',
    'Mathematical Methods', 'Specialist Mathematics', 'Further Mathematics'.

TYPE_KEYWORDS: dict[str, list[str]]
    Maps canonical type → trigger keywords (lowercased).
    Required keys: 'sac', 'sat', 'exam', 'project', 'homework', 'other'.
    Example: 'sac' → ['sac', 'school assessed coursework'].
    'other' is the fallback; never appears as a keyword match — assigned when no other
    keyword fires.

STATUS_KEYWORDS: dict[str, list[str]]
    Maps canonical status → trigger keywords.
    Keys: 'not_started', 'in_progress', 'completed'.

URGENCY_THRESHOLDS: list[tuple[int, str]]
    Ordered descending by threshold; first match wins.
    Concrete values per FR21:
        [(-1, 'overdue'),    # days_remaining < 0
         ( 3, 'today'),      # 0 <= days_remaining <= 3
         ( 7, 'soon'),       # 4 <= days_remaining <= 7
         (9999, 'distant')]  # days_remaining > 7
    Consumed by both client (card border, calendar fill) and server (dashboard payload).

ALLOWED_FILTER_KEYS: set[str] = {'subjects', 'types', 'statuses', 'show_completed', 'sort_by', 'month'}
ALLOWED_SORT_KEYS: set[str] = {'due_date', 'weight', 'subject'}

EDITABLE_FIELDS_ASSESSMENT: tuple[str, ...] = (
    'title', 'subject', 'type', 'due_date', 'start_date', 'weight',
    'status', 'description', 'reminder_days', 'linked_note_ids', 'term_info'
)
# Note: 'confidence', 'source_text', 'user', 'created_at' are NOT editable.

EDITABLE_FIELDS_NOTE: tuple[str, ...] = (
    'title', 'content', 'tags', 'is_pinned'
)
```

### `server_code/_auth.py`

```python
import anvil.users
import anvil.tables as tables
from anvil.tables import app_tables
```

#### `_require_user() -> anvil.tables.Row`
- Decorator: none (helper, not a callable).
- Behaviour:
  1. Call `anvil.users.get_user(allow_remembered=True)`.
  2. If `None`, raise `anvil.users.AuthenticationFailed("Login required")`.
  3. Return the user row.
- Permissions: called as first line of every `@anvil.server.callable` in `assessments.py`, `notes.py`, `dashboard.py`, and `nlp.py`.

#### `_own_or_raise(row: anvil.tables.Row, user: anvil.tables.Row) -> None`
- Behaviour:
  1. If `row['user'] != user`, raise `PermissionError("Not your record")`.
  2. Return `None`.
- Permissions: called by every update/delete server function after fetching a row by ID.

### `server_code/_datetime.py`

```python
import datetime
from zoneinfo import ZoneInfo  # stdlib, Python 3.9+
```

#### `_user_today(user_settings_row: anvil.tables.Row) -> datetime.date`
- Behaviour:
  1. Get `tz_name = user_settings_row['timezone']` (default `'Australia/Melbourne'` if column missing).
  2. Return `datetime.datetime.now(ZoneInfo(tz_name)).date()`.
- **Pending Decision 2.** If decision is (B) or (C), replace step 1 with a module constant.

#### `_user_now(user_settings_row) -> datetime.datetime`
- Returns a tz-aware datetime in user-local time. Use for logging and email "sent at" stamps.

#### `_format_date_au(d: datetime.date) -> str`
- Behaviour: return `d.strftime('%d %b %Y')` — yields e.g. `'15 Mar 2026'`. Locale-independent because `%b` returns the English month abbreviation under the standard C locale, which Anvil's Python runtime defaults to.
- Used by: email bodies (`reminders.py`), JSON export (`assessments.py`), and via `anvil.server.callable` if the client requests pre-formatted strings.

#### `_urgency_band(days_remaining: int) -> str`
- Behaviour: walk `URGENCY_THRESHOLDS` in order; return the first `colour` for which `days_remaining <= threshold`.

---

### `server_code/nlp.py`

Pure parser logic. No table writes from this module — `parse_text` returns a dict that the client previews and then passes to `assessments.create_assessment`.

#### `parse_text(s: str) -> dict`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. Call `user = _require_user()`.
  2. Fetch the user's `user_settings` row (used only for timezone in date resolution).
  3. Compute `today = _user_today(settings)`.
  4. Call `_match_subject(s, user_subjects) -> (str | None, str | None)` — collects every `SUBJECT_ALIASES` hit (case-folded, word-boundary regex) with its position, then ranks: contained shorter hits lose to longer phrases, unambiguous aliases beat `AMBIGUOUS_BARE_ALIASES`, the student's locked subjects (§11) beat non-locked, earliest mention wins. With exactly one locked maths study, a surviving bare `maths` hit maps to that study.
  5. Call `_match_type(s) -> str | None` — regex against `TYPE_KEYWORDS`; default `'other'` if no fire.
  6. Call `_extract_date(s, today, settings) -> tuple[date | None, str | None]` — ordered regex chain: DD/MM, weekday names, `"Term X Week Y"` (uses `settings['school_terms']`; returns `(None, None)` and downgrades confidence if `school_terms` is empty), `"tomorrow"`, `"today"`, `"in N days"`, month-name dates. The second element is the original term-phrase string for the `term_info` audit field.
  7. Call `_extract_weight(s) -> float | None` — regex `\d+(?:\.\d+)?\s*(%|percent)`.
  8. Call `_extract_title(s, matched_spans) -> str` — the residual after removing matched spans; trimmed.
  9. Assemble a dict with keys `title, subject, type, due_date, weight, term_info` plus per-field provenance: `{'fields': {...}, 'why': {'due_date': 'matched "next Friday" → 2026-03-20', ...}}`.
  10. Call `_score(parsed_dict) -> str` — counts detected fields among `{subject, type, due_date, weight}`; ≥4 → `'HIGH'`, 2–3 → `'MEDIUM'`, <2 → `'LOW'`.
  11. Return `{'fields': {...}, 'why': {...}, 'confidence': 'HIGH'|'MEDIUM'|'LOW', 'source_text': s}`.
- Errors: `anvil.users.AuthenticationFailed` if not logged in. Otherwise tolerates any input; returns `'LOW'` rather than raising on parse failure.
- External calls: none. Uses `dateparser` package as a fallback inside `_extract_date` for free-form English dates that the regex chain misses.
- Permissions: any logged-in user.

#### `parse_bulk(s: str) -> list[dict]`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. Split `s` on newlines; drop empty/whitespace-only lines.
  3. For each line, call the same internal helpers as `parse_text` (refactor `parse_text` to call a shared `_parse_one(line, today, settings)` so this re-uses logic).
  4. Return a `list[dict]`, each element with the same shape as `parse_text`'s return plus an integer `'line_index'`.
- Errors: same as `parse_text`.
- Permissions: any logged-in user.

#### Private helpers (not callables)
- `_match_subject(s, user_subjects) -> tuple[str | None, str | None]`
- `_match_type(s) -> str | None`
- `_extract_date(s, today, settings) -> tuple[date | None, str | None]`
- `_extract_weight(s) -> float | None`
- `_extract_title(s, matched_spans) -> str`
- `_score(parsed) -> str`
- `_try_parse_week_phrase(s, settings) -> tuple[date | None, str | None]` — `"Term 1 Week 4"` resolution; returns `(None, None)` if `settings['school_terms']` is empty (FR15 LOW-confidence-on-missing-config rule).

---

### `server_code/assessments.py`

```python
import anvil.server
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
import datetime, json
from ._auth import _require_user, _own_or_raise
from ._datetime import _user_today, _format_date_au
from ._constants import (
    ALLOWED_FILTER_KEYS, ALLOWED_SORT_KEYS,
    EDITABLE_FIELDS_ASSESSMENT, SUBJECT_ALIASES
)
```

#### `create_assessment(record: dict) -> str`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. Validate `record`:
     - `title`: non-empty str, length ≤ 200; else `ValueError("title required")`.
     - `subject`: must be in `set(SUBJECT_ALIASES.values())`; else `ValueError("invalid subject")`.
     - `type`: must be in `{'sac', 'sat', 'exam', 'project', 'homework', 'other'}`; else `ValueError`.
     - `due_date`: coerce to `datetime.date` (accept ISO string or `date`); else `ValueError`.
     - `weight`: if present, coerce to `float`; reject if outside `0.0 <= w <= 100.0`.
     - `status`: must be in `{'not_started', 'in_progress', 'completed'}`; default `'not_started'`.
     - `reminder_days`: must be a list of positive ints; default `[7, 2]`.
     - `linked_note_ids`: must be a list of str (note row IDs); default `[]`. Each id must resolve to a note owned by `user` (call `app_tables.notes.get_by_id(nid)` and `_own_or_raise`); reject the whole insert on any failure with `ValueError("invalid linked_note_ids")`.
     - `confidence`: must be `None` or one of `'HIGH', 'MEDIUM', 'LOW'`. **Pending Decision 1.**
     - `source_text`: text or `None`. **Pending Decision 1.**
     - `term_info`: text or `None`.
  3. `now = datetime.datetime.now(datetime.timezone.utc)`.
  4. Build the row payload: validated values + `user=user`, `created_at=now`, `updated_at=now`.
  5. `row = app_tables.assessments.add_row(**payload)`.
  6. Return `row.get_id()` (Anvil row ID, str).
- Errors: `AuthenticationFailed`, `ValueError`, `PermissionError` (from `linked_note_ids` ownership check).
- External calls: none.
- Permissions: any logged-in user.

#### `create_bulk_assessments(records: list[dict]) -> dict`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. Open `with tables.Transaction():` (Anvil's atomic write context).
  3. For each `record`, validate using the same checks as `create_assessment` (refactor into a shared `_validate_assessment_payload(record, user)` helper).
  4. If any validation fails, abort the transaction (raise inside the `with` block); return `{'inserted': 0, 'rejected': [...]}` where `rejected` contains `{'index': i, 'reason': str}` per failed item.
  5. On success: insert all rows; commit transaction.
  6. Return `{'inserted': len(records), 'ids': [row.get_id() for row in inserted]}`.
- **Commit policy** (per inventory intent for FR02): the caller (`AssessmentEditorForm.bulk_import_action`) pre-filters out `LOW`-confidence parses before invoking; the server treats every received row as auto-commit. The all-or-nothing transaction protects against partial writes.
- Errors: `AuthenticationFailed`, `ValueError` (raised inside the transaction triggers rollback).
- Permissions: any logged-in user.

#### `update_assessment(row_id: str, fields: dict) -> dict`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. `row = app_tables.assessments.get_by_id(row_id)`; if `None`, raise `ValueError("not found")`.
  3. `_own_or_raise(row, user)`.
  4. Filter `fields` to only keys in `EDITABLE_FIELDS_ASSESSMENT`; silently drop unknown keys (FR04 / EC-SEC-03).
  5. Re-validate each retained field using the same rules as `create_assessment`'s validation block (refactor a shared `_validate_field(key, value, user)` helper).
  6. Set `fields['updated_at'] = datetime.datetime.now(datetime.timezone.utc)`.
  7. `row.update(**fields)`.
  8. Return a dict mirror of the updated row (use a `_row_to_dict(row)` helper so the client never receives a live `Row` object).
- Errors: `AuthenticationFailed`, `PermissionError`, `ValueError`.
- External calls: none.
- Permissions: any logged-in user; row ownership enforced inline.

#### `delete_assessment(row_id: str) -> bool`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. `row = app_tables.assessments.get_by_id(row_id)`; if `None`, return `False`.
  3. `_own_or_raise(row, user)`.
  4. **Pending Decision 3 (C).** Reminder logs are not cascade-deleted; they retain `assessment_id` referencing the now-deleted row for audit purposes.
  5. `row.delete()`.
  6. Return `True`.
- Errors: `AuthenticationFailed`, `PermissionError`.
- Permissions: any logged-in user; ownership enforced inline.

#### `list_assessments(filters: dict | None = None, sort: dict | None = None) -> list[dict]`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. `filters = filters or {}`, `sort = sort or {'by': 'due_date', 'direction': 'asc'}`.
  3. Filter `filters` to keys in `ALLOWED_FILTER_KEYS`; drop the rest silently (NFR04).
  4. Build a `q.all_of(...)` query:
     - Always: `user=user`.
     - If `filters.get('subjects')` (non-empty list): `q.any_of(*[q.equal_to('subject', s) for s in filters['subjects']])`.
     - If `filters.get('types')`: same shape on `type`.
     - If `filters.get('statuses')`: same shape on `status`.
     - If `filters.get('show_completed') == False` (default): exclude `status='completed'` (`q.not_equal('status', 'completed')`).
     - If `filters.get('month')` (form `'YYYY-MM'`): bound `due_date` to the first and last days of that month inclusive (`q.between(...)`).
  5. Validate `sort['by']` against `ALLOWED_SORT_KEYS`; default to `'due_date'` on miss.
  6. Validate `sort['direction']` in `{'asc', 'desc'}`; default `'asc'`.
  7. Execute `rows = app_tables.assessments.search(tables.order_by(sort['by'], ascending=(sort['direction'] == 'asc')), <filters>)`.
  8. Convert each row to a dict via `_row_to_dict`; compute and attach `days_remaining` (from `_user_today`) and `urgency_band`.
  9. Return the list.
- Errors: `AuthenticationFailed`.
- Permissions: any logged-in user; results are user-scoped by step 4.

#### `export_user_data() -> anvil.BlobMedia`
- Decorator: `@anvil.server.callable`
- Behaviour (FR18):
  1. `user = _require_user()`.
  2. Collect: all `assessments` and all `notes` owned by `user`; the user's `user_settings` row.
  3. Exclude: `reminder_logs` (per FR18 spec); `user.get_id()` (export is portable across accounts).
  4. Convert datetimes to ISO 8601 strings; convert dates to `'YYYY-MM-DD'`.
  5. Build payload `{'version': 1, 'exported_at': '...', 'assessments': [...], 'notes': [...], 'settings': {...}}`.
  6. JSON-encode (`json.dumps(payload, indent=2)`); encode to bytes.
  7. `today_str = _user_today(settings).strftime('%Y-%m-%d')`.
  8. Return `anvil.BlobMedia('application/json', json_bytes, name=f'dotpoint-export-{today_str}.json')`.
- Errors: `AuthenticationFailed`.
- External calls: none.
- Permissions: any logged-in user.

#### `import_user_data(blob: anvil.Media) -> dict`
- Decorator: `@anvil.server.callable`
- Behaviour (FR19):
  1. `user = _require_user()`.
  2. Read `blob.get_bytes()`, decode as UTF-8, `json.loads`.
  3. Validate the parsed structure:
     - Top-level keys `'version'` (must equal `1`), `'assessments'` (list), `'notes'` (list), `'settings'` (dict). Reject the entire import on any structural failure with `ValueError("invalid export format")`.
     - Each assessment dict validated against `_validate_assessment_payload` rules.
     - Each note dict validated against the note insert rules from `notes.create_note`.
  4. **Collision handling**: for each incoming record, check if a row exists with the same `title` and `user`. If yes, append `' (imported YYYY-MM-DD HH:MM)'` to the title before insert.
  5. **Transaction**: wrap all inserts in `with tables.Transaction()`.
  6. Notes are inserted first; the mapping `{old_id: new_id}` is built. Then assessments are inserted with their `linked_note_ids` remapped via the dict.
  7. Update `user_settings` for the current user using `_update_settings` (whitelist-filtered, see `notes.py`).
  8. Return `{'notes_inserted': N, 'assessments_inserted': M, 'renamed': [...titles...]}`.
- Errors: `AuthenticationFailed`, `ValueError`.
- External calls: none.
- Permissions: any logged-in user.

#### Private helpers
- `_row_to_dict(row) -> dict` — converts an Anvil row to a plain dict, including `'id': row.get_id()`, converting dates to ISO strings.
- `_validate_assessment_payload(record, user) -> dict` — shared by `create_assessment`, `create_bulk_assessments`, `import_user_data`. Returns the validated payload or raises `ValueError`.

---

### `server_code/notes.py`

```python
import anvil.server, datetime
import anvil.tables as tables
from anvil.tables import app_tables
from ._auth import _require_user, _own_or_raise
from ._constants import EDITABLE_FIELDS_NOTE
```

#### `create_note(record: dict) -> str`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. Validate `record`: `title` non-empty ≤ 200; `content` str (may be empty); `tags` list of str, deduplicated; `is_pinned` bool (default `False`).
  3. `now = datetime.datetime.now(datetime.timezone.utc)`.
  4. `row = app_tables.notes.add_row(title=..., content=..., tags=..., is_pinned=..., user=user, created_at=now, updated_at=now)`.
  5. Return `row.get_id()`.
- Errors: `AuthenticationFailed`, `ValueError`.
- Permissions: any logged-in user.

#### `update_note(row_id: str, fields: dict) -> dict`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. `row = app_tables.notes.get_by_id(row_id)`; raise `ValueError` if `None`.
  3. `_own_or_raise(row, user)`.
  4. Filter `fields` to `EDITABLE_FIELDS_NOTE`.
  5. Re-validate retained fields.
  6. `fields['updated_at'] = datetime.datetime.now(datetime.timezone.utc)`.
  7. `row.update(**fields)`.
  8. Return `_note_row_to_dict(row)`.
- Errors: `AuthenticationFailed`, `PermissionError`, `ValueError`.
- Permissions: any logged-in user.

#### `delete_note(row_id: str) -> bool`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. `row = app_tables.notes.get_by_id(row_id)`; return `False` if `None`.
  3. `_own_or_raise(row, user)`.
  4. **Linked-note cleanup**: find any `assessments` rows owned by `user` whose `linked_note_ids` contains `row_id`; remove `row_id` from the list and call `row.update(linked_note_ids=...)`. Do this inside a `with tables.Transaction()`.
  5. `row.delete()`.
  6. Return `True`.
- Errors: `AuthenticationFailed`, `PermissionError`.
- Permissions: any logged-in user.

#### `toggle_pin(row_id: str) -> bool`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. `row = app_tables.notes.get_by_id(row_id)`; raise `ValueError` if `None`.
  3. `_own_or_raise(row, user)`.
  4. `new_value = not row['is_pinned']`.
  5. `row.update(is_pinned=new_value, updated_at=<utc now>)`.
  6. Return `new_value`.
- Errors: `AuthenticationFailed`, `PermissionError`, `ValueError`.
- Permissions: any logged-in user.

#### `search_notes(query: str | None = None, tag: str | None = None, pinned_only: bool = False) -> list[dict]`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. Fetch all notes owned by `user`, ordered by `is_pinned desc, updated_at desc` (pinned-first, then recency — FR10 / FR11).
  3. Apply `query` filter: case-insensitive substring against `title + ' ' + content`.
  4. Apply `tag` filter: include only notes whose `tags` list contains a case-insensitive equal of `tag`.
  5. Apply `pinned_only` filter.
  6. Combine all filters with AND (FR11).
  7. Return `[_note_row_to_dict(r) for r in result]`.
- Errors: `AuthenticationFailed`.
- Permissions: any logged-in user.

#### `get_settings() -> dict`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. `row = app_tables.user_settings.get(user=user)`; if `None`, call `_create_default_settings(user)` and return that.
  3. Return `_settings_row_to_dict(row)`.
- Errors: `AuthenticationFailed`.
- Permissions: any logged-in user.

#### `update_settings(fields: dict) -> dict`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. Fetch or create the settings row.
  3. Whitelist `fields` against `{'theme', 'default_reminder_days', 'notifications_enabled', 'school_year', 'school_terms', 'timezone'}`.
  4. Validate: `theme` in `{'light','dark'}`; `default_reminder_days` is `list[int]` all positive; `school_terms` is a list of `{term:int, start_date:str, end_date:str}`; `timezone` is a valid IANA name (try `ZoneInfo(value)`; raise on failure).
  5. `row.update(**fields)`.
  6. Return `_settings_row_to_dict(row)`.
- Errors: `AuthenticationFailed`, `ValueError`.
- Permissions: any logged-in user.

#### Private helpers
- `_create_default_settings(user) -> Row` — used by `get_settings` first-call path and the post-login hook in `LoginForm`.
- `_note_row_to_dict(row) -> dict`
- `_settings_row_to_dict(row) -> dict`

---

### `server_code/reminders.py`

```python
import anvil.server, anvil.email, datetime
import anvil.tables as tables
from anvil.tables import app_tables
from ._datetime import _user_today, _format_date_au
```

#### `run_reminder_check() -> dict`
- Decorator: `@anvil.server.background_task`
- Scheduling: configured in `anvil.yaml` as a scheduled task running every **30 minutes** (Anvil's documented minimum interval).
- Behaviour:
  1. `users_iter = app_tables.users.search()` — every Anvil user.
  2. Initialise counters `sent = 0`, `errors = 0`.
  3. For each `user`:
     a. `settings = app_tables.user_settings.get(user=user)`. If `None` or `settings['notifications_enabled'] is False`: continue.
     b. `today = _user_today(settings)`.
     c. `assessments = app_tables.assessments.search(user=user)`, filter to `status != 'completed'`.
     d. For each `assessment`:
        i. `days_remaining = (assessment['due_date'] - today).days`.
        ii. For each `d` in `assessment['reminder_days']`:
            - If `days_remaining > d`: skip (window not yet open).
            - If `days_remaining < 0` and `d > 0`: skip (overdue — the doc treats overdue as out of scope for emailed reminders; the dashboard colour band carries this signal).
            - `reminder_type = f'{d}_day'`.
            - **Dedup check**: `existing = app_tables.reminder_logs.get(assessment_id=assessment.get_id(), user=user, reminder_type=reminder_type)`. If non-`None`: skip.
            - Build email subject and body (see §6).
            - Try `anvil.email.send(to=user['email'], from_address='reminders@<app>.anvil.app', subject=..., text=..., html=...)`.
            - If send raises `anvil.email.SendFailure`: increment `errors`, log to server console, continue (the next 30-minute run retries).
            - On success: `app_tables.reminder_logs.add_row(assessment_id=assessment.get_id(), user=user, sent_date=today, reminder_type=reminder_type)`; increment `sent`.
  4. Return `{'sent': sent, 'errors': errors, 'run_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}`.
- Errors: caught per-user inside a `try/except Exception` so one user's failure doesn't halt the run.
- External calls: `anvil.email.send` (see §6).
- Permissions: scheduled task only — not exposed as `@anvil.server.callable`.

#### `trigger_reminder_check_now() -> dict`
- Decorator: `@anvil.server.callable`
- Behaviour:
  1. `user = _require_user()`.
  2. Optional gating: only allow if `user['email']` matches a dev-mode env value (set in Anvil App Secrets as `DEV_EMAIL`); else raise `PermissionError("dev only")`.
  3. Call `run_reminder_check()` inline.
  4. Return its result dict.
- Purpose: lets the developer manually fire the dispatcher during testing without waiting for the 30-min tick.
- Errors: `AuthenticationFailed`, `PermissionError`.
- Permissions: dev only.

---

### `server_code/dashboard.py`

```python
import anvil.server, datetime, calendar
import anvil.tables as tables
from anvil.tables import app_tables
from ._auth import _require_user
from ._datetime import _user_today, _urgency_band
from ._constants import URGENCY_THRESHOLDS
```

#### `get_dashboard_data(month: str | None = None, filters: dict | None = None, sort: dict | None = None) -> dict`
- Decorator: `@anvil.server.callable`
- Behaviour (NFR01 single round-trip):
  1. `user = _require_user()`.
  2. `settings = app_tables.user_settings.get(user=user)`.
  3. `today = _user_today(settings)`.
  4. **Assessment list panel**: call `assessments.list_assessments(filters, sort)` (or inline its logic to avoid a round-trip through `@anvil.server.callable`; refactor `_list_assessments_impl(user, settings, filters, sort)` as the shared core). Return `assessment_list`.
  5. **Calendar grid panel**:
     - If `month` is `None`: use `today.year`, `today.month`. Otherwise parse `'YYYY-MM'`.
     - `grid = calendar.monthcalendar(year, month)` — `list[list[int]]`, 6×7.
     - For each assessment owned by `user` with `due_date` inside this month: bucket by day → `day_buckets: dict[int, list[dict]]`.
     - For each day, compute the cell colour: walk its assessments, pick the highest-urgency band (lowest threshold in `URGENCY_THRESHOLDS` that fires for any of them).
     - Return `{'year': year, 'month': month, 'weeks': grid, 'day_buckets': day_buckets, 'cell_colours': {day_int: band_str}}`.
  6. **Upcoming sidebar**:
     - Filter `user`'s assessments to `status != 'completed'` and `0 <= days_remaining <= 30`.
     - Sort by `due_date` ascending.
     - Return `upcoming: list[dict]`, each with the same shape as `assessment_list` entries.
  7. **Subject list** (for filter dropdown): the set of distinct `subject` values across the user's assessments.
  8. Combine and return:
     ```
     {
       'today': today.isoformat(),
       'assessment_list': [...],
       'calendar': {...},
       'upcoming': [...],
       'subjects': [...],
       'settings': _settings_row_to_dict(settings),
     }
     ```
- Errors: `AuthenticationFailed`.
- External calls: none.
- Permissions: any logged-in user.

---

## 3. Client Forms

Every form is built fully programmatically. The pattern below is shared by all forms; subsequent sections only describe the layout tree, event handlers, state, and navigation.

```python
from anvil import *
import anvil.server, anvil.users

class FormName(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'
        # ... build UI ...
```

### Form: `LoginForm`
- Purpose: gate the app behind Anvil Users authentication (FR20).
- Layout: a single `ColumnPanel` containing:
  - `Label(text='DotPoint', role='display-1')`
  - `Label(text='Assessment Tracker', role='display-2')`
  - A `Button(text='Sign in')` whose `click` handler calls `anvil.users.login_with_form(allow_remembered=True, allow_signup=True)`. Anvil's built-in form is used for login + signup (it satisfies the email-and-password FR20 requirement without custom UI).
- Event handlers:
  - `_on_sign_in_click(self, **event_args)`: call `anvil.users.login_with_form(...)`; on success, call `anvil.server.call('get_settings')` to ensure the settings row exists (the first call lazily creates it via `_create_default_settings`); then `open_form('DashboardForm')`.
- Form state: none.
- Navigation: on successful login → `DashboardForm`. No other transitions.

### Form: `DashboardForm`
- Purpose: the all-in-view three-panel dashboard (FR06, FR07, FR08, FR09, FR21).
- Layout: outer `ColumnPanel` containing, in order:
  1. **Top bar** (`FlowPanel`, full width): `Label(text='DotPoint', role='heading')`, spacer, `Link(text='Notes', tag='notes')`, `Link(text='Settings', tag='settings')`, `Link(text='Import/Export', tag='import-export')`, `Button(text='Sign out', role='secondary')`. The three links route via `set_url_hash` and `open_form`.
  2. **NLP input bar** (`FlowPanel`): `TextBox(placeholder='Type assessment, e.g. "Maths SAC next Friday 25%"', width='100%')`, `Button(text='Parse')`. Pressing Enter or clicking Parse calls `anvil.server.call('parse_text', input_value)` and opens `ParserPreviewForm` as an alert with the result.
  3. **Filter row** (`FlowPanel`): three `DropDown`s (Subject, Status, Type), one `CheckBox(text='Show completed')`, one `Button(text='Bulk add', role='secondary')`. Changes to any control re-fire `_refresh()`.
  4. **Body** (`GridPanel`, 3 columns, widths 5/4/3):
     - **Left panel — assessment list**: a `ColumnPanel` (`self.list_panel`) that `_refresh()` populates with one `AssessmentCard` (a custom `ColumnPanel` subclass built in this same form file as a nested class, or factored to `client_code/common/AssessmentCard.py`) per assessment.
     - **Centre panel — calendar grid**: a `ColumnPanel` (`self.calendar_panel`) holding a `GridPanel` (6×7) of `Label`s for day numbers; each cell's `background` is set from `cell_colours`. Header row: weekday names Mon–Sun.
     - **Right panel — upcoming sidebar**: a `ColumnPanel` (`self.upcoming_panel`) of compact rows grouped by date (`Label(text='Mon 15 Mar')` headers + child rows).
- Event handlers (`_refresh` is the central one):
  - `_refresh(self)`: build the `filters` dict from the four filter controls; build `sort` from session state; call `anvil.server.call('get_dashboard_data', month=self._current_month, filters=filters, sort=self._sort)`; repopulate the three body panels.
  - `_on_parse_click(self)`: call `parse_text` with the input bar's text; pass the result to `alert(ParserPreviewForm(parsed=result), large=True)`; on save (the alert returns the assessment id), call `_refresh`.
  - `_on_bulk_add_click(self)`: open `AssessmentEditorForm(mode='bulk')` as an alert.
  - `_on_card_status_change(self, assessment_id, new_status)`: bound at `AssessmentCard` construction time; calls `anvil.server.call('update_assessment', assessment_id, {'status': new_status})`; on success, `_refresh()`.
  - `_on_card_click(self, assessment)`: opens `AssessmentEditorForm(mode='edit', assessment_id=...)` as an alert.
  - `_on_card_delete_click(self, assessment_id)`: `if confirm('Delete this assessment?'): anvil.server.call('delete_assessment', assessment_id); self._refresh()`.
  - `_on_month_change(self, direction)`: increment/decrement `self._current_month`; `_refresh`.
- Form state (instance variables, initialised in `__init__`):
  - `self._current_month: str` — `'YYYY-MM'`; init to current month.
  - `self._sort: dict` — `{'by': 'due_date', 'direction': 'asc'}`; persisted in `anvil.user_cache.user['ui_sort']` if cookies allowed, else session-only.
  - `self._filters_subject, _filters_status, _filters_type` — current dropdown values.
  - `self._show_completed: bool` — checkbox state.
- Navigation: `open_form('NotesForm')`, `open_form('SettingsForm')`, `open_form('ImportExportForm')`. Editor and parser preview open as alerts, not new forms.
- **First-paint sequence**: `__init__` builds the static layout, then calls `self._refresh()` once. The card click / delete handlers are bound after `_refresh` repopulates the list panel.

### Form: `AssessmentEditorForm`
- Purpose: manual create (UC2), edit (UC3), parser-preview-confirm landing (UC1 commit), and bulk add (UC1.1). One form, multiple modes via `mode` constructor param.
- Constructor signature: `def __init__(self, mode='create', assessment_id=None, prefill=None, **properties)`.
  - `mode='create'`: empty form, button labelled "Save".
  - `mode='edit'`: load existing assessment by ID, prefill all fields, button labelled "Save". Server call: `anvil.server.call('list_assessments', filters={'_id': assessment_id})` — actually use a dedicated `get_assessment(id)` server function, **add this to `assessments.py`**:
    ```
    @anvil.server.callable
    def get_assessment(row_id: str) -> dict:
        user = _require_user()
        row = app_tables.assessments.get_by_id(row_id)
        if row is None: raise ValueError("not found")
        _own_or_raise(row, user)
        return _row_to_dict(row)
    ```
  - `mode='preview'`: `prefill` is a `parse_text` result dict; show confidence badge, per-field "why" annotations.
  - `mode='bulk'`: large `TextArea` for paste, "Parse" button, then `MultiPreview` panel.
- Layout (`mode in {'create','edit','preview'}`):
  - **Header row**: `Label(text=<mode-specific>)`, in `'preview'` mode a coloured confidence badge `Label` (`role='confidence-high/medium/low'`, styled via theme).
  - **Form body** (single `ColumnPanel`, vertical):
    - `TextBox` for `title` — required, max length 200.
    - `DropDown` for `subject` — items from `SUBJECT_ALIASES.values()` (fetched once via `anvil.server.call` to a `get_subjects()` callable, or hardcoded — recommend hardcode via a client-side import of a generated `_subjects.py`).
    - `DropDown` for `type` — six static items.
    - `DatePicker` for `due_date` — format `'DD MMM YYYY'` (NFR08).
    - `DatePicker` for `start_date` — optional.
    - `TextBox` for `weight` — numeric input; client-side bound `0–100`.
    - `DropDown` for `status` — three items.
    - **Reminder pills** (`FlowPanel`): five `CheckBox`es labelled `14, 7, 3, 2, 1` days; default-checked per `settings.default_reminder_days`.
    - **Linked notes manager** (`FlowPanel`): a search `TextBox` + result list; selected note titles render as pills with an X. Resolves via `anvil.server.call('search_notes', query=...)`.
    - `TextArea` for `description` — optional.
    - In `'preview'` mode: under each field, a small `Label` with the parser "why" string (e.g. `'matched "next Friday" → 20 Mar 2026'`), styled grey.
  - **Footer row**: `Button(text='Cancel', role='ghost')` left, `Button(text='Save', role='primary')` right.
- Layout (`mode='bulk'`):
  - Top: `TextArea(placeholder='Paste assessments, one per line', height='200px')`.
  - Below: `Button(text='Parse all')`.
  - Below that: `self._multi_panel: ColumnPanel` that fills with one preview row per parsed line. Each row shows a confidence pill + the inferred fields + a `CheckBox(checked=<confidence != 'LOW'>)`. LOW rows are unchecked-by-default and show their `why` string.
  - Footer: `Button(text='Create selected', role='primary')` — gathers checked rows, calls `anvil.server.call('create_bulk_assessments', records=[...])`.
- Event handlers:
  - `_on_save_click(self)`: build a payload from the form fields; in `'create'`/`'preview'` mode call `create_assessment`; in `'edit'` mode call `update_assessment`. On success, `raise_event('x-close')` so the parent's `alert(...)` returns.
  - `_on_cancel_click(self)`: same close event, return `None`.
  - `_on_bulk_parse_click(self)`: call `parse_bulk` with the textarea content; rebuild `self._multi_panel`.
  - `_on_bulk_create_click(self)`: gather checked rows; call `create_bulk_assessments`; close.
- Form state: one instance variable per form field (or use a dict `self._fields`); `self._mode`, `self._assessment_id`, `self._prefill`, `self._multi_results`.
- Navigation: opened as an alert from `DashboardForm`; closes via the alert close event.

### Form: `NotesForm`
- Purpose: notes panel (FR10, FR11, FR-S03).
- Layout:
  - **Top bar**: same as `DashboardForm` top bar minus the NLP input.
  - **Filter row** (`FlowPanel`): search `TextBox`, tag-filter `DropDown` (populated from the union of all tags across the user's notes), `Button(text='New note', role='primary')`.
  - **Body**:
    - `Label(text='Pinned')` (only if pinned notes exist).
    - A `GridPanel(columns=2)` of pinned note cards.
    - `Label(text='All notes')`.
    - A `GridPanel(columns=3)` of all-notes cards.
  - **`NoteCard`** (factor to `client_code/common/NoteCard.py`): `Card` containing `Label(title)`, tag pills, a markdown-rendered preview of the first 200 chars of `content`, plus pin/edit/delete icon buttons.
- Event handlers:
  - `_refresh(self)`: `anvil.server.call('search_notes', query=self._search, tag=self._tag, pinned_only=False)`; populate the two grids by partitioning `is_pinned`.
  - `_on_new_note_click`: `alert(NoteEditorForm(mode='create'), large=True)`; on close, `_refresh`.
  - `_on_card_edit_click(note_id)`: `alert(NoteEditorForm(mode='edit', note_id=note_id), large=True)`; on close, `_refresh`.
  - `_on_card_pin_click(note_id)`: `anvil.server.call('toggle_pin', note_id)`; `_refresh`.
  - `_on_card_delete_click(note_id)`: `if confirm(...): anvil.server.call('delete_note', note_id); _refresh`.
- Form state: `self._search`, `self._tag`.
- Navigation: opens `NoteEditorForm` as an alert.

### Form: `NoteEditorForm`
- Purpose: create / edit a single note.
- Constructor: `def __init__(self, mode='create', note_id=None, **properties)`.
- Layout:
  - `TextBox` for `title`.
  - `TextArea` for `content` (height 400px) — plain text edit; markdown is rendered only in the preview pane.
  - Tag manager: `TextBox` for new tag + add button; existing tags render as pills with X.
  - `CheckBox(text='Pin this note')`.
  - Footer: `Button(text='Cancel')`, `Button(text='Save')`.
- Event handlers:
  - `_on_save_click`: in create mode call `create_note`; in edit mode call `update_note`; close.
  - **Autosave**: in edit mode only, a 300ms debounce on `content`/`title`/`tags` changes calls `update_note` silently (no toast, no close). Implement using `anvil.Timer(interval=0.3)` reset on each change.
- Form state: `self._mode`, `self._note_id`, `self._dirty`, `self._save_timer`.
- Navigation: opened as an alert; closes via close event.

### Form: `ParserPreviewForm`
- Purpose: anchor the parser preview modal (FR17, EC-UX-04, EC-UX-07).
- Effectively a thin wrapper that constructs `AssessmentEditorForm(mode='preview', prefill=parsed)` and returns it.
- Or: drop this form entirely; `DashboardForm._on_parse_click` opens `AssessmentEditorForm(mode='preview', ...)` directly. **Drop `ParserPreviewForm`.** (Removed from §0 layout listing — Claude Code should not create this file.)

### Form: `SettingsForm`
- Purpose: configure default reminder days, school year, school terms, notifications enabled, timezone (Decision 2).
- Layout:
  - **Top bar**: same as `NotesForm`.
  - **Body** (single `ColumnPanel`):
    - `Label(text='Reminders', role='heading')`.
    - Five checkboxes for default reminder days (14, 7, 3, 2, 1).
    - `CheckBox(text='Enable email reminders')`.
    - `Label(text='School terms', role='heading')`.
    - For each of 4 terms: two `DatePicker`s (start, end).
    - `TextBox` for `school_year`.
    - `Label(text='Timezone', role='heading')`.
    - `DropDown` for `timezone` — items: a static list of IANA Australian zones (`Australia/Sydney`, `Australia/Melbourne`, `Australia/Brisbane`, `Australia/Perth`, `Australia/Darwin`, `Australia/Adelaide`, `Australia/Hobart`).
    - `Label(text='Theme', role='heading')` + a placeholder `Label('Theme control coming in a future release.')` — column exists but no UI control per MVP scope (inventory intent on `AppSettings`).
    - `Button(text='Save', role='primary')`.
- Event handlers:
  - `_on_save_click`: build the fields dict; call `update_settings`; toast success.
- Form state: one instance variable per control.
- Navigation: returns to `DashboardForm` via top-bar link.

### Form: `ImportExportForm`
- Purpose: FR18 / FR19.
- Layout:
  - `Label(text='Export', role='heading')`.
  - `Button(text='Download my data')` → calls `export_user_data`, receives a `BlobMedia`, calls `anvil.media.download(blob)`.
  - `Label(text='Import', role='heading')`.
  - `FileLoader(file_types='.json', multiple=False)` → on change, store the file in `self._upload`.
  - `Button(text='Import')` → on click, call `import_user_data(self._upload)`; show the returned summary in a `Label`.
- Event handlers:
  - `_on_download_click`, `_on_import_click`.
- Form state: `self._upload`.
- Navigation: top-bar link back to `DashboardForm`.

---

## 4. Routing / Navigation

- Routing mechanism: a thin custom hash router in `client_code/Main/__init__.py`, set as the app's Startup Form in `anvil.yaml`.
- `Main` (subclass `ColumnPanel`) reads `anvil.get_url_hash()` on init and on hash-change events:

```
hash -> form:
  ''               -> DashboardForm  (if logged in) else LoginForm
  'dashboard'      -> DashboardForm
  'notes'          -> NotesForm
  'settings'       -> SettingsForm
  'import-export'  -> ImportExportForm
  'login'          -> LoginForm
```

- Implementation:
  1. `Main.__init__` calls `_route_to_current()`.
  2. `_route_to_current()`:
     - If `anvil.users.get_user(allow_remembered=True) is None` and hash != `'login'`: `anvil.set_url_hash('login', set_in_history=False)`; render `LoginForm` inside `self` (call `self.clear()`, `self.add_component(LoginForm())`).
     - Otherwise map hash → form and render.
  3. Subscribe to `anvil.event_loop` hash-change via `anvil.set_url_hash` — Anvil emits a `hash_changed` event on the open form when the URL hash changes. Simpler: rely on `open_form('Main')` calls from child forms (each child form's nav link calls `set_url_hash` and `open_form('Main')`).
- Initial route: `''` → `DashboardForm` after login, `LoginForm` before.
- Post-login route: `DashboardForm` (`open_form('Main')` after `login_with_form` returns).
- Deep-linkable routes: all four top-level routes are deep-linkable via the hash. No parameterised routes — opening a specific assessment uses an in-memory alert, not a URL.

---

## 5. Authentication

- Mechanism: Anvil Users service (`anvil.users`), email + password.
- Custom form: no. `anvil.users.login_with_form(allow_remembered=True, allow_signup=True)` is used directly (it's a function call, not a designer-backed form, so it doesn't violate the fully-programmatic constraint).
- Required user properties beyond Anvil defaults: none. Anvil's built-in `users` table provides `email`, `password_hash`, `enabled`, `confirmed_email`, `last_login`, `signed_up`. No extra columns are added to the `users` table; per-user data lives in `user_settings`, `assessments`, `notes`, and `reminder_logs`.
- Permission model: row-level. Every server function calls `_require_user()` and `_own_or_raise(row, user)` before reading or writing. No roles.
- Login flow:
  1. `LoginForm` button calls `anvil.users.login_with_form(allow_remembered=True, allow_signup=True)`.
  2. On success, the form calls `anvil.server.call('get_settings')` which lazily creates the user's `user_settings` row if absent.
  3. `open_form('Main')` — Main's router sees a logged-in user and renders `DashboardForm`.
- Signup flow: same as login; Anvil's built-in form has a "sign up" tab when `allow_signup=True`.
- Password reset flow: built into Anvil's login form. Enable in the Anvil app's Users service config: "Allow users to reset their password by email".
- Logout: `anvil.users.logout()` from the top-bar "Sign out" button on every form; followed by `open_form('Main')` (the router sends the user back to `LoginForm`).

---

## 6. Email

Only one email template. Trigger is `reminders.run_reminder_check`.

### Template: assessment reminder
- Purpose: notify the user that an assessment is due in N days (FR14).
- Trigger: inside `run_reminder_check`, once per `(assessment, user, reminder_type)` not already in `reminder_logs`.
- Recipient: `user['email']`.
- From address: `'reminders@<app-slug>.anvil.app'` (the app's default outbound address; Anvil routes from this with no DNS setup).
- Subject: `f'Reminder: {assessment["title"]} due in {d} day{"s" if d != 1 else ""}'`.
- Text body:
  ```
  Hi,

  This is a reminder that the following assessment is coming up:

    {title}
    Subject: {subject}
    Due:     {_format_date_au(due_date)}  (in {d} day(s))
    Type:    {type}
    Weight:  {weight}%   ← only if weight is not None

  Open DotPoint to update your status or notes:
  https://<app-url>/#dashboard

  — DotPoint
  ```
- HTML body: same content, wrapped in a minimal HTML shell with `<h2>` for the title, `<dl>` for the field block, and an anchor for the link. Inline styles only.
- Sending mechanism: `anvil.email.send(to=..., from_address=..., subject=..., text=..., html=...)` from `reminders.run_reminder_check`. On `anvil.email.SendFailure`, log and continue without writing a `reminder_logs` row, so the next 30-minute tick retries.
- App URL: read from `anvil.app.environment.app_origin` or hardcoded as a constant `APP_BASE_URL` in `_constants.py`; the URL is for the user's link to log in, not for any server-to-server call.

---

## 7. Third-Party Integrations

| Service / library | Purpose | Called from | Credentials |
|---|---|---|---|
| `dateparser` (PyPI package) | Free-form English date parsing fallback inside `nlp._extract_date` | `server_code/nlp.py` | None (pure library). Added to the Anvil app's Python package list via the IDE's Settings → Python packages. |

No external APIs. Firebase, Firestore, Chrome Identity, Chrome Notifications, Browser Notifications, and Electron IPC are all dropped (see §8).

---

## 8. Anvil Translation Decisions

One row per source feature listed in `INVENTORY_annotated.md` / `REQUIREMENTS_COVERAGE.md`. Notes follow the table for MODIFIED, ALTERNATIVE, and DROPPED rows.

| Source feature | Inventory ref | Anvil translation | Translation type |
|---|---|---|---|
| HashRouter SPA routes (`/`, `/assessments`, `/calendar`, `/settings`) | Inv §2 | Custom hash router in `Main` form; routes `''`, `'dashboard'`, `'notes'`, `'settings'`, `'import-export'`, `'login'` | MODIFIED |
| Dexie/IndexedDB local persistence | Inv §1, §4, §5 | Anvil Data Tables (no local store) | ALTERNATIVE |
| Cloud Firestore | Inv §1, §5, §9 | Anvil Data Tables | ALTERNATIVE |
| Firebase Auth (Google OAuth) | Inv §1, §6 | Anvil Users (email + password) | ALTERNATIVE |
| `localStorage.dotpoint_local_mode` "Continue without account" | Inv §6, §8 | — (no offline mode) | DROPPED |
| Zustand stores (filter/sort UI state) | Inv §8 | Instance variables on `DashboardForm` and `NotesForm`; persisted per session via `anvil.user_cache` if cookies enabled | MODIFIED |
| `setInterval` 30-min reminder checker in browser | Inv §7 | `@anvil.server.background_task run_reminder_check` scheduled every 30 min | ALTERNATIVE |
| Browser Notifications API (Notification, Notification.permission) | Inv §9 | Email via `anvil.email.send` (FR14) | ALTERNATIVE |
| Electron native notifications via IPC | Inv §9 | — | DROPPED |
| Chrome Extension popup (`QuickAddAssessment`, `QuickAddNote`) | Inv §3 | — | DROPPED |
| Chrome Extension service worker (`chrome.alarms`, badge counter, direct Firestore writes) | Inv §7, §9 | — | DROPPED |
| `chrome.identity.getAuthToken` extension OAuth | Inv §9 | — | DROPPED |
| `chrono-node` (TypeScript NLP date parser) | Inv §1, §9 | `dateparser` (Python equivalent), used in `nlp._extract_date` | ALTERNATIVE |
| `date-fns` 2.30 | Inv §1 | `datetime` + `_format_date_au` helper for NFR08 | ALTERNATIVE |
| `framer-motion` animations | Inv §1 | — (Anvil components animate on hover/focus via theme CSS; no scripted animations) | DROPPED |
| `react-hot-toast` toasts | Inv §1 | Anvil `Notification(message).show()` | ALTERNATIVE |
| `react-markdown` + `remark-gfm` | Inv §1 | Render markdown client-side using the `markdown2` Python package via a server callable `render_markdown(s) -> str` that returns HTML, then place the HTML inside a `RichText(content_type='html')` component. (`RichText` accepts HTML.) | ALTERNATIVE |
| `lucide-react` icons | Inv §1 | Anvil's built-in icon set (FontAwesome via `Button(icon='fa:plus')`) | ALTERNATIVE |
| `react-router-dom` 6 | Inv §1 | Hash router in `Main` form (above) | ALTERNATIVE |
| `<HashRouter>` deep links | Inv §2 | `set_url_hash` + `Main` router | DIRECT |
| `SmartAssessmentInput` debounced parse | Inv §3 | Synchronous: user clicks "Parse" button (or presses Enter); no debounce. Result opens `AssessmentEditorForm(mode='preview')` as an alert | MODIFIED |
| `AssessmentList` client-side filter/sort | Inv §3 | Server-side: `list_assessments(filters, sort)` returns the already-filtered, already-sorted list | MODIFIED |
| `AssessmentCard` inline status dropdown | Inv §3 | `DropDown` component on each card row; `change` event handler calls `update_assessment` and re-fires `_refresh` | DIRECT |
| `AssessmentForm` (15 state variables, 4 modes) | Inv §3 | `AssessmentEditorForm` with `mode` parameter (`'create' \| 'edit' \| 'preview' \| 'bulk'`) | DIRECT |
| `BulkAssessmentInput` user-selects-indices commit | Inv §3 | Same UI shape; commit policy: HIGH/MEDIUM default-checked, LOW default-unchecked; user can override; all-or-nothing transaction in `create_bulk_assessments` | MODIFIED |
| `MultiAssessmentPreview` | Inv §3 | Folded into `AssessmentEditorForm(mode='bulk')` | DIRECT |
| `AssessmentCalendar` with `WeekView` and `YearView` | Inv §3 | Month view only; built inline in `DashboardForm` centre panel using `calendar.monthcalendar` | MODIFIED |
| `NoteList` pinned-first + filter | Inv §3 | `NotesForm` with server-side filter via `search_notes` | DIRECT |
| `NoteEditor` 500ms debounced autosave | Inv §3 | Same UX, 300ms debounce (per doc), uses `anvil.Timer` | MODIFIED |
| `SchoolTermsConfig` | Inv §3 | `SettingsForm` body section | DIRECT |
| `CommandPalette` (Cmd+K) | Inv §3 | — | DROPPED |
| `ConfirmDialog` | Inv §3 | `anvil.confirm(message)` built-in | ALTERNATIVE |
| `ErrorBoundary` | Inv §3 | Per-callable `try/except` on the client side; `Notification.show()` for user-facing errors | ALTERNATIVE |
| `Modal`, `Badge`, `Button`, `Card`, `Input`, `Header`, `Sidebar`, `MobileNav` | Inv §3 | Anvil's built-in container/widget set | DIRECT |
| `useReminders` 60-second permission poller | Inv §7 | — | DROPPED |
| `cleanupOldReminderLogs` (30-day TTL) | Inv §7 | — | DROPPED |
| `useAuth` Firebase listener | Inv §6 | `anvil.users.get_user(allow_remembered=True)` checked per route | ALTERNATIVE |
| `dataService.setUser` + `migrateLocalDataToCloud` | Inv §5 | — | DROPPED |
| `onSnapshot` Firestore real-time listeners | Inv §5 | Manual refresh on action + 30-min scheduler tick (no push channel) | ALTERNATIVE |
| `DataService.createBulkAssessments` Firestore `writeBatch` | Inv §5 | `create_bulk_assessments` inside `with tables.Transaction()` | DIRECT |
| `subjectColors` (duplicated between `utils.ts` and `colors.ts`) | Inv §10 #8 | Single `_constants.py` constant `SUBJECT_COLOURS: dict[str, str]` consumed by both server (calendar cell colours) and client (card border) | MODIFIED |
| `urgencyColors` (duplicated) | Inv §10 #10 | Single `URGENCY_THRESHOLDS` constant in `_constants.py` | MODIFIED |
| `useTheme` direct-Dexie write bug | Inv §10 #6 | — (theme has no UI control in MVP; column exists but unused) | DROPPED |
| Extension hardcoded `https://dotpoint.vercel.app` | Inv §10 #4 | — | DROPPED |
| `auth = getAuth(app)` typo in `firebase.ts` | Inv §10 #2 | — (Firebase removed entirely) | DROPPED |
| FR18 export | REQ-COV row FR18 | `assessments.export_user_data` callable; client `ImportExportForm` triggers download | ALTERNATIVE |
| FR19 import | REQ-COV row FR19 | `assessments.import_user_data` callable; client `ImportExportForm` provides FileLoader | ALTERNATIVE |
| Reminder dedup key `(assessmentId, reminderDay)` with sentinel ints | REQ-COV row FR14 / NFR02 | `reminder_logs(assessment_id, user, reminder_type, sent_date)`; logical key `(assessment_id, user, reminder_type)` | MODIFIED |
| Days-remaining computed client-side | REQ-COV row FR09 | Computed server-side in `dashboard.get_dashboard_data` and `list_assessments` | MODIFIED |
| `EDITABLE_FIELDS` whitelist | REQ-COV row FR04 | `EDITABLE_FIELDS_ASSESSMENT` / `EDITABLE_FIELDS_NOTE` constants enforced in `update_assessment` / `update_note` | DIRECT |

### Translation notes

**HashRouter SPA routes → custom hash router**: The TypeScript app uses `react-router-dom` with 4 routes mapped to 4 page components. Anvil has no built-in router; `anvil.set_url_hash` plus a manual dispatch in `Main` provides equivalent shareable URLs. The route count drops from 4 to 6 because two non-route UIs in the source (LoginPage, modals) become navigable hashes (`login`, `import-export`), making the auth gate explicit.

**Dexie/IndexedDB → Anvil Data Tables**: The source has a dual-write architecture (Dexie first, Firestore mirror). Per REQUIREMENTS_COVERAGE: "IndexedDB and localStorage are not used" (SRS Scope Constraints). Anvil Data Tables become the single store of truth; the optimistic-local-then-cloud pattern collapses into a single server round-trip per action.

**Cloud Firestore → Anvil Data Tables**: Equivalent CRUD coverage; no native real-time push (see `onSnapshot` row below). Schema is the four normalised tables in §1, not nested Firestore subcollections under `users/{uid}/...` — Anvil's row-level access is enforced in app code (`_own_or_raise`) instead of by security rules.

**Firebase Auth → Anvil Users**: FR20 mandates Anvil Users / email + password. Google OAuth disappears; "Continue without account" disappears (see next row). User identity is the row in Anvil's built-in `users` table, accessed via `anvil.users.get_user()`. No `user.uid` indirection — the row itself is the identity.

**`localStorage.dotpoint_local_mode` → dropped**: SRS Scope Constraints: "Web-only. No offline mode." The "Continue without account" bypass that the source code supports has no SRS counterpart and must be removed.

**Zustand stores → form-instance variables**: Three Zustand stores collapse into per-form instance variables because Anvil forms are themselves stateful objects and there are no cross-form filter contexts in the spec'd UI. Filter/sort persistence across reloads is downgraded to session-only — the SRS calls predictability across a single session sufficient.

**`setInterval` reminder checker → Anvil scheduled task**: The source code's 30-minute `setInterval` only fires while the SPA tab is open. Anvil's scheduled task runs regardless of any client session, which is the entire point of moving reminders server-side. Anvil's minimum scheduler interval is 30 minutes, so the cadence is preserved exactly.

**Browser Notifications → email**: SRS FR14 specifies email only ("No push notifications. Email only…"). The three native notification channels in the source code (Browser, Electron, Chrome) all collapse into one email send. The doc's reasoning, paraphrased: the dispatcher is already using Anvil's scheduled task + email relay, so adding a second channel doubles the surface area without doubling the value. The always-visible `days_remaining` colour on each card (FR21) is the in-app visual reminder.

**Electron, Chrome Extension, Chrome OAuth, Chrome alarms, Chrome notifications → dropped**: SRS Scope Constraints state "Web-only" and "single Anvil app published to one URL". All extension and desktop build targets are removed.

**`chrono-node` → `dateparser`**: Both are NLP date libraries with comparable coverage of English date phrases. `dateparser` is a maintained PyPI package available in Anvil's runtime; `chrono-node` is JS-only.

**`date-fns` → stdlib `datetime`**: `_format_date_au` is a 1-line wrapper around `strftime('%d %b %Y')`. Stdlib only, no PyPI dependency.

**`framer-motion` → dropped**: Anvil's component set does not expose hooks for scripted animations. Transitions are limited to CSS in `theme/parameters.yaml`. No requirement demands scripted animation; FR21's colour change is instant.

**`react-hot-toast` → `Notification.show`**: Anvil's built-in `Notification` widget delivers the same "transient confirmation message" UX. The source code uses toasts on save and on parse; the port preserves the same trigger points.

**`react-markdown` + `remark-gfm` → server-side render**: Anvil has no client-side markdown widget. The cleanest port renders markdown to HTML on the server (`markdown2` package, fast and deterministic) and puts the HTML in a `RichText` component. Editing remains plain-text in `NoteEditorForm`; rendering happens only in `NotesForm` card previews and in note detail views.

**`lucide-react` icons → Anvil's FontAwesome**: Anvil ships with FontAwesome icons accessible via `icon='fa:plus'` etc. on `Button`, `Link`, and `Label` components. Icon names in the source code map 1:1 (or near-1:1) to FontAwesome names; substitute on a per-component basis.

**`SmartAssessmentInput` debounced parse → click-to-parse**: The source code re-parses on every keystroke (debounced 300ms). In Anvil, each `parse_text` call is a network round-trip, so debounced live parsing would spam the server. Synchronous click-to-parse is the obvious downgrade and matches the doc's Figure 6 mockup (parser preview is anchored to the input, opened on submit, not on type).

**`AssessmentList` client-side filter/sort → server-side**: The source filters and sorts in the browser over a Dexie live query. In Anvil, the data lives on the server; filter and sort happen in `list_assessments` to avoid shipping the full row set to the client. Filter keys are restricted to `ALLOWED_FILTER_KEYS` per NFR04.

**`BulkAssessmentInput` commit policy**: The source code requires the user to click each row to select it. The doc's spec'd policy (HIGH/MEDIUM auto-checked, LOW auto-unchecked, user can override) is a strict UX improvement and is what the port adopts. The all-or-nothing transaction in `create_bulk_assessments` is new behaviour.

**`WeekView` and `YearView` → dropped**: SRS FR08 specifies a monthly grid only. The week and year views are not in any FR; they're optional polish that costs `dashboard.py` complexity for no requirement gain.

**`NoteEditor` 500ms autosave → 300ms**: Doc value (inventory §3 NoteEditor intent) is 300ms; the source code has 500ms. Re-aligning to the doc.

**`CommandPalette` (Cmd+K) → dropped**: Not in any FR. The sidebar nav + hash routes satisfy the navigation requirements.

**`ConfirmDialog` → `anvil.confirm`**: Anvil's built-in alert system has a confirm dialog (`anvil.confirm(message)` returns True/False). Equivalent UX, no custom component needed.

**`ErrorBoundary` → per-callable try/except**: Anvil has no React-style error boundary. Each client-side `anvil.server.call` is wrapped in `try/except` with `Notification.show(str(e), style='danger')` for user-facing errors. Unexpected exceptions are surfaced via Anvil's built-in error-handling banner.

**`useReminders` 60-second poller → dropped**: The poller exists only to detect Browser Notification permission state. Browser notifications are dropped, so the poller is moot.

**`cleanupOldReminderLogs` (30-day TTL) → dropped**: The doc requires permanent log retention for audit. The source's cleanup is unnecessary and would defeat the audit value.

**`dataService.setUser` + `migrateLocalDataToCloud` → dropped**: Specific to the dual-write architecture's first-login migration. Anvil has a single store, so there is nothing to migrate.

**`onSnapshot` Firestore real-time listeners → action-driven refresh**: Anvil callables are request/response, not push. The dashboard refreshes on user action (create / edit / delete / filter change) and on the 30-minute scheduler tick. No background polling. For a single-user app where the user is the sole writer, this is functionally equivalent to push.

**`subjectColors` and `urgencyColors` duplicates → single source**: Inventory flagged both as duplicated. Port consolidates each into one `_constants.py` constant consumed by both server (for calendar cell colour calculation) and client (for card border CSS).

**`useTheme` Dexie-bypass bug → dropped**: Theme has no UI control in MVP. The bug disappears with the feature.

**Browser/Chrome/Electron hardcoded URLs and the Firebase typo → dropped**: All Firebase-derived code is removed.

**FR18 / FR19 → server-side**: Export builds a `BlobMedia` server-side and returns it for download. Import takes an `anvil.Media` (uploaded file), validates, and inserts inside a transaction. Both behaviours did not exist in the source code.

**Reminder dedup → new schema**: The source uses `(assessmentId, reminderDay)` with sentinel ints (`-1` = overdue, `0` = today). The port uses `(assessment_id, user, reminder_type)` with reminder_type a string `'{N}_day'`. Migration: none — the port starts fresh.

**Days-remaining → server-side**: Moves the computation to `dashboard.get_dashboard_data` and `list_assessments`, returned alongside each assessment dict.

---

## 9. Known Anvil Limitations Affecting This Project

| Limitation | Affects §8 entries |
|---|---|
| Anvil's runtime is stateless between callables — no module-level mutable state can be relied on across requests | Logic design choice (flat procedural over service classes); private parser helpers are pure functions |
| Anvil scheduled tasks have a 30-minute minimum interval | `setInterval` reminder checker → scheduled task |
| Anvil callables are request/response; no built-in real-time push to clients | `onSnapshot` Firestore listeners → action-driven refresh |
| Anvil Data Tables expose no cross-table foreign keys; relations are either link-to-row columns (cascade or restrict) or `simpleObject` lists of row IDs | `linked_note_ids` as `simpleObject` list on `assessments`; `reminder_logs.assessment_id` as text (Decision 3) |
| Anvil server runs in UTC; client browsers may be in any timezone | `user_settings.timezone` (Decision 2); `_user_today` helper |
| Anvil's `users` table is fixed-schema; extra per-user data must live in a separate table | `user_settings` table exists separately from `users` |
| Anvil has no client-side markdown renderer | Server-side `render_markdown` callable feeding a `RichText` component |
| Anvil's component set has no React-style error boundary | Per-callable try/except + `Notification.show` |
| Anvil's email relay can rate-limit or fail; the SDK raises `anvil.email.SendFailure` | `run_reminder_check` writes `reminder_logs` only on send success, so a failed send retries on the next tick |
| Anvil's "fully programmatic" mode means each form is a Python class subclassing a container; there is no template file | All forms in §3 build their layout in `__init__` |
| Anvil's hash routing is a single string; no built-in route parameters | All routes in §4 are static; record IDs are passed as alert form parameters, not URL params |

---

## 10. Implementation Order

Vertical-slice ordering: build the foundations, then complete one end-to-end feature before starting the next. Risky unknowns are pulled forward.

1. **Anvil project bootstrap + Auth + Settings** (§0, §1 `user_settings`, §2 `_auth.py`, §2 `_datetime.py`, §2 `notes.get_settings` / `notes.update_settings`, §3 `LoginForm`, §3 `SettingsForm`, §4, §5)
   - Resolves Decision 2 (timezone) and validates the routing skeleton.
2. **Assessments CRUD end-to-end** (§1 `assessments`, §2 `_constants.py`, §2 `assessments.create_assessment` / `update_assessment` / `delete_assessment` / `get_assessment` / `list_assessments`, §3 `AssessmentEditorForm` in `create` and `edit` modes, partial `DashboardForm` with just the list panel)
   - First moment a user can create, edit, and delete an assessment. Resolves Decision 1 (`confidence` / `source_text` columns).
3. **Dashboard combined payload** (§2 `dashboard.get_dashboard_data`, §3 full `DashboardForm` with calendar grid + upcoming sidebar)
   - Validates NFR01 (single round-trip dashboard render).
4. **NLP parser end-to-end** (§2 `nlp.parse_text`, §3 `AssessmentEditorForm` in `preview` mode wired to `DashboardForm._on_parse_click`)
   - First moment FR01 / FR17 work end-to-end. Parser is risky enough to pull forward of bulk and notes.
5. **Bulk import** (§2 `nlp.parse_bulk`, §2 `assessments.create_bulk_assessments`, §3 `AssessmentEditorForm` in `bulk` mode)
   - Adds FR02 on top of the working parser.
6. **Notes end-to-end** (§1 `notes`, §2 `notes.create_note` / `update_note` / `delete_note` / `toggle_pin` / `search_notes`, §3 `NotesForm`, §3 `NoteEditorForm`, server-side `render_markdown`)
   - FR10 / FR11 / FR-S03.
7. **Linked notes manager** (§3 `AssessmentEditorForm` linked-notes section, ownership-checked resolution in `create_assessment` / `update_assessment`)
   - FR12.
8. **Reminder dispatcher + scheduled task config** (§1 `reminder_logs`, §2 `reminders.run_reminder_check`, §2 `reminders.trigger_reminder_check_now`, §6 email template, `anvil.yaml` scheduled-task entry)
   - FR13 / FR14 / NFR02. Resolves Decision 3 (`reminder_logs.assessment_id` semantics).
9. **Export / Import** (§2 `assessments.export_user_data`, §2 `assessments.import_user_data`, §3 `ImportExportForm`)
   - FR18 / FR19.
10. **NFR pass** (server-side date formatting NFR08, log ownership audit NFR03, parser test set NFR04, dashboard render budget NFR01)

---

## 11. Subject Onboarding (post-MVP slice, 2026-07)

**Goal:** every account locks in its actual VCE studies, and those studies then
drive the whole app (editor dropdown, dashboard filter, parser alias priority,
exam timetable).

**Data:** `user_settings.subjects` (simpleObject; list of canonical subject
strings, or null = not onboarded). Written ONLY by `notes.set_subjects` — the
key is deliberately excluded from the `update_settings` whitelist so the
validation rules cannot be bypassed.

**Catalog:** `_constants.SUBJECT_GROUPS` / `CANONICAL_SUBJECTS` — the ~46
commonly-taken VCAA studies grouped by learning area (source cited in the
module). Every catalog entry has at least its own lowercased name in
`SUBJECT_ALIASES`, so `assessments._validate_assessment_payload` accepts the
whole catalog. `Further Mathematics` was renamed `General Mathematics` (VCAA
2023); `further*` aliases retained. The generic `Mathematics` catch-all stays
alias-only (not in the picker).

**Rules (`notes._clean_subjects`):**
- every entry must be a catalog subject; dedupe + strip; max 12;
- **>= 1 mathematics study** (`MATHS_GROUP`) — a DotPoint client mandate
  (VCAA does not require maths; the client does);
- **English group always present** (`ENGLISH_GROUP` = English / EAL / English
  Language / Literature, per the VCAA English requirement): if none selected,
  `'English'` is appended automatically. The client warns first (confirm
  dialog) so the auto-add is never a surprise.

**Flow:** `Main` router gates any logged-in user whose settings carry no
subjects into `OnboardingForm` (route `#onboarding`), whatever hash they hit.
The form renders `common.SubjectPicker` (grouped checkboxes over
`get_subject_catalog`), pre-checks the maths/English rules client-side, calls
`set_subjects`, seeds the session cache, and routes to the dashboard. Changing
subjects later is a deliberate Settings flow (§12).

**Session cache:** `common.get_session_settings()` caches one `get_settings`
round-trip per browser session; the router reads it on every navigation for
the gate + theme. All settings writers push the server response back via
`set_session_settings`; sign-out and login clear it.

**Parser:** `nlp._match_subject(text, user_subjects)` collects every alias hit
with its position and ranks them: longer phrases beat contained tokens,
unambiguous aliases beat `AMBIGUOUS_BARE_ALIASES` (ordinary words like
'health'/'business'), locked subjects beat non-locked, earliest mention wins.
Bare `math/maths/mathematics` maps onto the student's single locked maths
study when unambiguous.

## 12. Settings: Change Subjects + Theme (post-MVP slice, 2026-07)

- **Change subjects:** chips show the locked list; `Change subjects…` opens a
  confirm dialog (consequences spelled out), then the shared `SubjectPicker`
  prefilled, then `set_subjects` re-runs the same server-side rules.
- **Theme:** `user_settings.theme` (`'light'` default | `'dark'`) now has a
  dropdown. `common.apply_theme` toggles `body.dotpoint-dark`; the whole
  palette is CSS variables in `anvil.yaml native_deps.head_html` (light values
  on `:root`, dark overrides under `body.dotpoint-dark`). The router applies
  the theme from the session cache on every navigation, so it survives
  reloads without a per-route server call.

## 13. VCE Exam Timetable 2026 (post-MVP slice, 2026-07)

- **Data:** `exams.EXAM_TIMETABLE_2026` — written-exam sessions (date, start,
  end, paper) keyed by canonical subject, transcribed from the official VCAA
  "2026 VCE examination timetable" (URL cited in the module; retrieved
  2026-07-23/24; every entry independently re-verified against the same
  page). Covers the whole catalog except `NO_WRITTEN_EXAM` (Applied
  Computing, Extended Investigation, Art Creative Practice — no written exam
  on the VCAA timetable, reported as `no_exam_subjects`); any future
  uncovered subject is reported as `not_covered`, so a data gap is never
  presented as "no exam". Music (one picker subject over four VCAA studies)
  carries stream labels on each paper.
- **Server:** `exams.get_exam_timetable()` → the student's papers (locked
  subjects; English guaranteed via `_exam_subjects` even for legacy rows),
  each decorated with `days_remaining` + the shared urgency band (`done` once
  past), sorted by date; plus `next_exam` and `no_exam_subjects`.
- **Client:** `ExamsForm` (route `#exams`, top-bar link) — countdown banner,
  a card per paper, source link. `DashboardForm` overlays `calendar.exam_days`
  (purple `▲` markers, exams included in the day popup) and shows a next-exam
  countdown chip linking to the Exams view.

---

## 14. Design system (UI overhaul, 2026-08)

Every feature was finished and live before this slice; nothing here changes what
the app *does*. The problem it solves is that the app looked like a working
prototype: colours were written as hex literals inside the forms (the muted grey
`#9aa0a6` appeared 20+ times), the same visual idea — a section heading, an
empty list, a status tag — was expressed differently on each screen, and because
a hex value in Python cannot change with the theme, half the app did not adapt
when the student switched to dark mode.

The fix is a two-layer separation that the rest of this section describes:
**the stylesheet decides how things look; the forms only say what things are.**

### 14.1 Principles

1. One source of truth. Every colour, size, radius, space and shadow is a CSS
   custom property in `anvil.yaml` → `native_deps.head_html`.
2. Forms name meaning, not appearance. A form writes `role='t-overdue'`, never
   `foreground='#d64550'`. No client module contains a hex colour; an offline
   suite asserts that (§14.7).
3. Both themes, always. Dark mode is one class on `<body>`; because every colour
   is a variable, one declaration block re-skins the whole app.
4. Compose, don't repeat. Small builders in `client_code/common` mean a form
   body reads as composition rather than a wall of styling arguments.
5. Semantic colour survives. Urgency bands, parser confidence and the VCE exam
   marker keep their meaning in both palettes, and never signal by colour alone.
6. Minimalism is subtraction. Fewer borders, more whitespace, one clear
   hierarchy per screen: page title → section header → content.

### 14.2 Token layer

`:root` declares the light palette; `body.dotpoint-dark` re-declares only the
colour tokens. Spacing, type scale and radii are shared, so the two themes can
never drift in layout.

| Group | Tokens |
|---|---|
| Spacing (4px base) | `--dp-s1` … `--dp-s7` (4, 8, 12, 16, 24, 32, 48px) |
| Radii | `--dp-r-sm`, `--dp-r-md`, `--dp-r-lg`, `--dp-r-pill` |
| Type scale | `--dp-fs-display`, `-page`, `-card`, `-body`, `-cap`, `-micro` |
| Surfaces | `--dp-bg`, `--dp-surface`, `--dp-surface-2`, `--dp-border`, `--dp-border-strong` |
| Text | `--dp-heading`, `--dp-text`, `--dp-muted`, `--dp-faint` |
| Brand | `--dp-accent`, `-hover`, `-soft`, `--dp-on-accent`, `--dp-focus` |
| Urgency (FR21) | `--dp-overdue`, `--dp-duetoday`, `--dp-soon`, `--dp-distant` (+ `-soft` fills) |
| Confidence (FR17) | `--dp-ok`, `--dp-warn`, `--dp-bad` |
| Exams (§13) | `--dp-exam`, `--dp-exam-soft` |
| Elevation | `--dp-shadow`, `--dp-shadow-lg` |

The dark palette **lightens** the semantic hues rather than reusing them
(`--dp-overdue` is `#c8384a` on white and `#ff8b98` on slate): the same red
would be unreadable on a dark surface, but a lighter tint of it is still
recognisably "overdue".

`--dp-on-accent` exists because the accent itself flips lightness between the
palettes: white on the light theme's accent is 4.9:1, but white on the dark
theme's lighter accent is only 2.7:1, which fails WCAG AA. Freezing the text
colour would have left every primary button, every selected subject pill and
the calendar's "today" ring washed out in dark mode, so the text colour is a
token too. For the same reason the light-mode semantic hues are darker than
they look like they need to be: they are read at 11px on a pale tint, where
4.5:1 is the bar.

`server_code/_constants.URGENCY_COLOURS` was **deleted** in this slice. The
server's job is to say *which* band an assessment is in; how a band looks is the
client's. The band name is now the whole contract across that boundary.

### 14.3 Role vocabulary

Anvil turns `role='card'` into the DOM class `.anvil-role-card`, so every rule
is written against `.anvil-role-<name>`.

| Group | Roles |
|---|---|
| Type | `display`, `pagetitle`, `pagehead`, `sectionhead`, `cardtitle`, `muted`, `caption`, `micro` |
| Tone | `t-overdue`, `t-duetoday`, `t-soon`, `t-distant`, `t-exam`, `t-ok`, `t-warn`, `t-bad`, `t-accent` |
| Layout | `page`, `panel`, `row`, `toolbar`, `field`, `divider`, `dashgrid` |
| Surfaces | `card`, `listcard` (+ `-overdue`/`-duetoday`/`-soon`/`-distant`), `banner`, `empty`, `authcard` |
| Navigation | `topbar`, `brand`, `navitem`, `navitem-active` |
| Buttons | `primary`, `secondary`, `ghost`, `danger`, `iconbtn` |
| Chips | `chip` (+ `-accent`/`-exam`/`-ok`/`-warn`/`-bad`/ the four urgency suffixes) |
| Inputs | `bigfield`, `pill` |
| Calendar | `calgrid`, `calhead`, `calcell` (+ urgency suffixes), `calcell-blank`, `calnum`, `calnum-now`, `calcount`, `calexam` |

The server's urgency band `today` is mapped to the tone name `duetoday`, because
the calendar separately needs "is today" styling and the two must not collide.
`common.band_role(band, prefix)` does that mapping in one place.

### 14.4 The component kit (`client_code/common`)

| Builder | Returns |
|---|---|
| `make_page(*c)` | the centred content column every signed-in screen sits in |
| `make_page_title(title, subtitle=None)` | the h1 block of a screen |
| `make_section_header(title, hint=None)` | small tracked label that opens a section |
| `make_card(*c)` | a surface panel |
| `make_list_card(band=None)` | a list row whose left edge carries the urgency band |
| `make_banner(*c)` | a quiet full-width strip (tips, next-exam countdown) |
| `make_row(*c)` / `make_toolbar(*c)` | a wrapping row of components / of controls |
| `make_chip(text, tone=None)` / `make_band_chip(text, band)` | a rounded tag |
| `make_field(label, component, hint=None)` | a labelled form control |
| `make_empty_state(title, hint, action_text, action_click)` | what a panel shows when it has nothing |
| `make_divider()` | a hairline rule |
| `toast` / `toast_error` / `toast_warn` | the app's only transient-message call sites |
| `from_iso` / `fmt_date` / `to_iso` / `MONTHS_ABBR` | shared date formatting (Skulpt has no usable `strftime`) |
| `SubjectPicker` | the grouped pill multi-select (§11) |

### 14.5 What Anvil's markup forced

These are the non-obvious constraints the implementation had to work around.
Each was measured against the running app, not assumed.

- A **FlowPanel** renders `.flow-panel > .flow-panel-gutter > .flow-panel-item`,
  and the gutter is the flex container. Overriding it to `display:grid` is what
  makes a real calendar possible.
- That gutter carries **−15px side margins** and each item a **15px side
  margin** (Anvil's own row spacing). Both must be cancelled wherever the
  stylesheet sets its own `gap`, or items sit ~38px apart and the first one is
  indented out of line with the heading above it.
- A **GridPanel** emits `col-xs-N`, `col-sm-N`, `col-md-N` *and* `col-lg-N` for
  the same number, so Bootstrap's own responsive stacking can never fire.
  Stacking has to be a media query that overrides width and float (§14.6).
- A **Label**'s text node is `.label-text`; a **Link**'s is `.link-text`. A rule
  written for one silently misses the other.
- A **TextBox** puts its role class on the `<input>` itself — there is no
  wrapper — so `.anvil-role-bigfield input` matches nothing.
- **Spacer does not accept `role`** and raises at construction if given one.
  `make_divider()` is therefore built from an empty ColumnPanel.
- A **CheckBox** renders `div.checkbox > label > input + span`. Hiding the input
  and styling the sibling `<span>` turns it into a toggle pill with no
  JavaScript — this is how the reminder options and the subject picker work.

### 14.6 Three behavioural fixes carried by this slice

**Toasts (defect 13).** Anvil Notifications are bootstrap-notify elements fixed
at `top:20px`, i.e. directly over the top bar, and success toasts were observed
failing to auto-dismiss — a stuck toast then swallowed clicks on the navigation.
`common.toast()` is now the single call site: it keeps a reference to every live
toast and dismisses it on its own timer, caps the visible stack at three, and
the stylesheet moves the stack to the bottom-right so a stuck toast can never
cover the nav again. `toast_warn` is kept distinct from `toast_error` because an
empty text box is not a failure and colouring routine validation red teaches the
student to ignore red.

**Browser navigation.** `Main` now installs a `hashchange` listener (once per
session), so Back, Forward and a pasted `#notes` link re-route without a full
reload. `common.navigate()` still sets the hash *and* re-enters the router
directly: relying on the event alone would make every navigation depend on it
firing, and the listener deliberately ignores events raised while a dialog is
fading out — which is exactly when signing in and finishing onboarding navigate.
The listener compares the new hash against the route the router last drew and
ignores the echo of the app's own writes, so nothing renders twice (NFR01: one
round-trip per screen).

**Mobile.** Below 900px the dashboard's three panels each take full width;
below 640px the top bar stays a single scrolling row instead of wrapping onto
three lines (which cost 169px of a 812px-tall phone screen before any content).

### 14.7 Keeping it honest

Two offline assertions guard the system, because both failure modes are silent:

- **No client module may contain a hex colour literal.** That is the exact
  defect this slice removed, and it would reappear one convenient `foreground=`
  at a time.
- **Every `role=` used in client code must have a matching rule in the
  stylesheet.** A role with no rule renders as unstyled default Anvil — it looks
  like a layout bug, not like a typo.

Both live in the constants-integrity suite (docs/TESTING.md §1), which also
still checks the hand-copied enum mirrors.

---

## Coverage check

Cross-reference of `REQUIREMENTS_COVERAGE.md` IMPLEMENTED + PARTIAL requirements to their spec locations:

| Req ID | Spec location |
|---|---|
| FR01 | §2 `nlp.parse_text`; §3 `DashboardForm._on_parse_click` + `AssessmentEditorForm(mode='preview')` |
| FR02 | §2 `nlp.parse_bulk` + `assessments.create_bulk_assessments`; §3 `AssessmentEditorForm(mode='bulk')` |
| FR03 | §2 `assessments.create_assessment`; §3 `AssessmentEditorForm(mode='create')` |
| FR04 | §2 `assessments.update_assessment` + `EDITABLE_FIELDS_ASSESSMENT`; §3 `AssessmentEditorForm(mode='edit')` |
| FR05 | §2 `assessments.delete_assessment`; §3 `DashboardForm._on_card_delete_click` + `anvil.confirm` |
| FR06 | §2 `assessments.list_assessments` filter logic with `ALLOWED_FILTER_KEYS`; §3 `DashboardForm` filter row |
| FR07 | §2 `assessments.list_assessments` sort logic (default `due_date asc`); empty-state labels in `DashboardForm` |
| FR08 | §2 `dashboard.get_dashboard_data` calendar block; §3 `DashboardForm` centre panel |
| FR09 | §2 `dashboard.get_dashboard_data` + `list_assessments` attach `days_remaining` |
| FR10 | §1 `notes`; §2 `notes.create_note` / `update_note` / `delete_note` / `toggle_pin`; §3 `NotesForm` + `NoteEditorForm` |
| FR11 | §2 `notes.search_notes`; §3 `NotesForm` filter row |
| FR12 | §1 `assessments.linked_note_ids`; §3 `AssessmentEditorForm` linked-notes manager |
| FR13 | §2 `reminders.run_reminder_check` (scheduled @ 30 min) |
| FR14 | §2 `reminders.run_reminder_check`; §6 email template |
| FR15 | §2 `nlp._try_parse_week_phrase`; §1 `user_settings.school_terms` |
| FR16 | §2 `_constants.SUBJECT_ALIASES`; §2 `nlp._match_subject` |
| FR17 | §2 `nlp.parse_text` returns `confidence` + `why`; §3 `AssessmentEditorForm(mode='preview')` |
| FR18 | §2 `assessments.export_user_data`; §3 `ImportExportForm` |
| FR19 | §2 `assessments.import_user_data`; §3 `ImportExportForm` |
| FR20 | §5 Anvil Users; §3 `LoginForm` |
| FR21 | §2 `_constants.URGENCY_THRESHOLDS`; §2 `_datetime._urgency_band`; §3 `DashboardForm` card border + calendar cell colour |
| NFR01 | §2 `dashboard.get_dashboard_data` single round-trip |
| NFR02 | §1 `reminder_logs` dedup key; §2 `reminders.run_reminder_check` dedup check |
| NFR03 | §2 `_auth._require_user` + `_own_or_raise`; §1 every table has `user` column; every server function filters by `user` |
| NFR04 | §2 `assessments.list_assessments` whitelists `ALLOWED_FILTER_KEYS` / `ALLOWED_SORT_KEYS` |
| NFR05 | Anvil-hosted web app; no install required (architectural) |
| NFR06 | §2 module split: `nlp.py`, `assessments.py`, `notes.py`, `reminders.py`, `dashboard.py` |
| NFR07 | Anvil-hosted; `*.anvil.app` over TCP 443 (architectural) |
| NFR08 | §2 `_datetime._format_date_au`; consumed by server (emails, exports) and client (display) |

No requirement is unaddressed.

---

## Totals

- **Data tables: 4** (`assessments`, `notes`, `user_settings`, `reminder_logs`).
- **Server functions (`@anvil.server.callable` + `@anvil.server.background_task`): 25**
  - `nlp.py`: `parse_text`, `parse_bulk` — 2
  - `assessments.py`: `create_assessment`, `create_bulk_assessments`, `update_assessment`, `delete_assessment`, `list_assessments`, `get_assessment`, `export_user_data`, `import_user_data` — 8
  - `notes.py`: `create_note`, `update_note`, `delete_note`, `toggle_pin`, `search_notes`, `get_settings`, `update_settings`, `get_subject_catalog` (§11), `set_subjects` (§11), `create_account` (§5 workaround), `sign_in_with_email` (§5 workaround) — 11
  - `reminders.py`: `run_reminder_check` (background_task), `trigger_reminder_check_now` (callable) — 2
  - `dashboard.py`: `get_dashboard_data` — 1
  - `exams.py`: `get_exam_timetable` (§13) — 1
  - Server-side `render_markdown` helper (§8 markdown row) is not counted; implement as a private function imported by the forms that need it, or as a callable if cross-form caching becomes useful.
- **Client forms: 10** — `Main`, `LoginForm`, `DashboardForm`, `AssessmentEditorForm`, `NotesForm`, `NoteEditorForm`, `SettingsForm`, `ImportExportForm`, `OnboardingForm` (§11), `ExamsForm` (§13). (`ParserPreviewForm` was folded into `AssessmentEditorForm(mode='preview')` in §3.)
- **DROPPED features (§8): 13** — `localStorage.dotpoint_local_mode`, Electron native notifications, Chrome Extension popup, Chrome Extension service worker, `chrome.identity` OAuth, `framer-motion`, `CommandPalette`, `useReminders` 60-second poller, `cleanupOldReminderLogs`, `dataService.setUser` + `migrateLocalDataToCloud`, `useTheme` Dexie-bypass bug, extension hardcoded Vercel URL, `firebase.ts` `auth = getAuth(app)` typo.
- **Decisions Needed: 3** (listed at top — `confidence`/`source_text` columns, `timezone` column, `reminder_logs.assessment_id` semantics).
