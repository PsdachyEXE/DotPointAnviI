# DotPoint (Anvil port)

Assessment tracker for VCE students. Port of the Vite/React/TypeScript app at `C:\Coding\DotPoint` to [Anvil](https://anvil.works) (Python, fully programmatic forms, GitHub-backed app).

## Status

Foundations stage. Module files are stubbed; no business logic is implemented yet. See `docs/IMPLEMENTATION_SPEC.md` for the authoritative spec and the per-section implementation plan.

## Repository layout

```
DotPointAnviI/
  anvil.yaml              # Anvil app config (services, startup form, runtime)
  client_code/            # Fully programmatic forms (Anvil container subclasses)
    LoginForm/__init__.py
    DashboardForm/__init__.py
    AssessmentEditorForm/__init__.py
    NoteEditorForm/__init__.py
    SettingsForm/__init__.py
    ImportExportForm/__init__.py
    ParserPreviewForm/__init__.py
    common/__init__.py    # Shared client-side helpers
  server_code/            # Server modules (one per concern)
    nlp.py                # Text parser (parse_text, parse_bulk)
    assessments.py        # CRUD + bulk + export/import
    notes.py              # Note CRUD + search + settings get/update
    reminders.py          # Background email reminder dispatcher
    dashboard.py          # Aggregator for the all-in-view dashboard payload
    _constants.py         # SUBJECT_ALIASES, TYPE_KEYWORDS, URGENCY_THRESHOLDS, ...
    _auth.py              # _require_user(), _own_or_raise(row, user)
    _datetime.py          # _user_today, _user_now, _format_date_au, _urgency_band
  theme/
    parameters.yaml       # Anvil theme params (placeholder)
  docs/
    IMPLEMENTATION_SPEC.md   # Authoritative spec for the port
    MANUAL_SETUP.md          # Anvil IDE steps that can't be automated from files
    INVENTORY.md             # Source-app inventory
    INVENTORY_annotated.md   # Inventory with implementation intent
    REQUIREMENTS_COVERAGE.md # Requirements traceability matrix
    DISCREPANCIES.md         # Known inconsistencies in source documents
```

## Conventions

Per `IMPLEMENTATION_SPEC.md` §0:

- Data tables: `snake_case`, plural.
- Server modules: `snake_case.py`; functions are `snake_case`, verb-first; constants are `UPPER_SNAKE_CASE`.
- Forms: `PascalCase`, suffix `Form`; built fully programmatically (no designer YAML, no `init_components()`).
- Every `@anvil.server.callable` calls `_require_user()` first and `_own_or_raise(row, user)` before any row-scoped read or write.
- URL hashes: `#dashboard`, `#notes`, `#settings`, `#import-export`, `#login`.

## Setting up

1. Create an Anvil app and link it to this repository (see `docs/MANUAL_SETUP.md` §0).
2. Create the four data tables in the Anvil IDE per `docs/MANUAL_SETUP.md` §2.
3. Enable the Users service with the settings in `docs/MANUAL_SETUP.md` §3.
4. Add the `dateparser` Python package (`docs/MANUAL_SETUP.md` §4).
5. Configure the scheduled task and `DEV_EMAIL` app secret (`docs/MANUAL_SETUP.md` §5–6).
6. Implement modules per `docs/IMPLEMENTATION_SPEC.md` §2 and §3 in the order given by §10.

## Pending design decisions

Three open decisions in `docs/IMPLEMENTATION_SPEC.md` (top of file) affect the schema and parser. The current setup assumes recommendations A / A / C:

1. `assessments.confidence` and `assessments.source_text` — added as nullable text columns.
2. `user_settings.timezone` — added as text column, default `'Australia/Melbourne'`.
3. `reminder_logs.assessment_id` — stored as text (not an Anvil row link).
