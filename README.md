# DotPoint (Anvil port)

Assessment tracker for VCE students. Port of the Vite/React/TypeScript app at `C:\Coding\DotPoint` to [Anvil](https://anvil.works) (Python, fully programmatic forms, GitHub-backed app).

## Status

**Feature-complete and live-tested.** All spec §10 slices are implemented:
auth + settings (incl. one-click VIC 2026 term preset), assessments CRUD,
the regex/lookup NLP parser (parse → confidence-badged preview → save),
bulk import (atomic), the three-panel dashboard (list + calendar + upcoming,
filters/sort, clickable urgent days, inline card status changes), notes
(CRUD/search/tags/pinning) with assessment linking, the 30-minute email
reminder dispatcher (scheduled task registered), and JSON export/import.

Post-MVP slices (spec §11–§13): **subject onboarding** — every account locks
in its VCE studies (≥1 maths, English group guaranteed per the VCAA rule)
which then drive the editor dropdown, dashboard filter and parser alias
priority; a deliberate **change-subjects flow** and a **light/dark theme
picker** in Settings; a **token-based design system** where every colour and
size is a CSS variable in `anvil.yaml` and no form hardcodes one (§14); and the
**VCE 2026 exam timetable** view (official VCAA dates, per-student papers,
countdown chip + calendar exam markers).

Parser accuracy measured at **30/30 subjects and 30/30 due dates** against the
EC-EF-01/02 test set (target ≥80%). See `docs/TESTING.md` for the full
testing evidence and the defect → fix → re-test trail.

## Repository layout

```
DotPointAnviI/
  anvil.yaml              # Anvil app config: services, startup form, runtime, data-table
                          # schema, scheduled task, and the design-token stylesheet
  client_code/            # Fully programmatic forms (Anvil container subclasses)
    Main/__init__.py             # Startup form: hash router + auth and onboarding gates
    LoginForm/__init__.py        # Sign in / create an account
    OnboardingForm/__init__.py   # Mandatory post-signup subject selection (§11)
    DashboardForm/__init__.py    # List + calendar + next-30-days, filters and sort
    AssessmentEditorForm/__init__.py  # Four-mode modal: create/edit/preview/bulk
    NotesForm/__init__.py        # Notes list, search, tag filter, pinning
    NoteEditorForm/__init__.py   # Note create/edit modal
    ExamsForm/__init__.py        # VCE 2026 written-exam timetable (§13)
    SettingsForm/__init__.py     # Terms, reminders, timezone, theme, change subjects
    ImportExportForm/__init__.py # JSON export and import (FR18/FR19)
    common/__init__.py           # Shared helpers + the UI kit (top bar, cards, chips,
                                 # fields, empty states, toasts, SubjectPicker, theme)
  server_code/            # Server modules (one per concern)
    README.txt            # USER MANUAL and legal notice — the client-facing document
    nlp.py                # Text parser (parse_text, parse_bulk)
    assessments.py        # CRUD + bulk + export/import
    notes.py              # Note CRUD + search + settings/subjects + authentication
    reminders.py          # Background email reminder dispatcher
    dashboard.py          # Aggregator for the all-in-one dashboard payload
    exams.py              # VCE 2026 exam timetable constants + callable (§13)
    _validation.py        # Shared input checks: require_* (raise) and safe_* (degrade)
    _constants.py         # SUBJECT_GROUPS/ALIASES, enums, field bounds, thresholds
    _auth.py              # _require_user(), _own_or_raise(row, user)
    _datetime.py          # _user_today, _user_now, _format_date_au, _urgency_band
  tests/                  # Offline suites — a fake anvil package lets the UNMODIFIED
                          # server modules run on a plain interpreter (python -m tests.run_all)
  theme/
    parameters.yaml       # Anvil theme params (placeholder)
  docs/
    IMPLEMENTATION_SPEC.md   # Authoritative spec for the port
    VALIDATION.md            # Field-by-field validation reference (criterion 7.3)
    MANUAL_SETUP.md          # Anvil IDE steps that can't be automated from files
    TESTING.md               # Testing evidence: suites, EC accuracy, live journeys
    INVENTORY.md             # Source-app inventory
    INVENTORY_annotated.md   # Inventory with implementation intent
    REQUIREMENTS_COVERAGE.md # Requirements traceability matrix
    DISCREPANCIES.md         # Known inconsistencies in source documents
```

## Conventions

Per `IMPLEMENTATION_SPEC.md` §0:

- Data tables: `snake_case`, plural. Columns `snake_case`; booleans read as predicates (`is_pinned`).
- Server modules: `snake_case.py`; functions are `snake_case`, verb-first; constants are `UPPER_SNAKE_CASE`; module-private names take a single leading underscore.
- Forms: `PascalCase`, suffix `Form`; built fully programmatically (no designer YAML, no `init_components()`).
- Form attributes: `self._` for everything the form keeps, including the interface controls it reads back (`self._title_tb`, `self._subject_dd`). Controls are type-suffixed; event handlers are `_on_<thing>_<event>`.
- Local variables say what they hold (`validated_fields`, not `out`; `assessment_type`, not `a_type`).
- No f-strings anywhere — `%`-formatting only.
- Every `@anvil.server.callable` calls `_require_user()` first and `_own_or_raise(row, user)` before any row-scoped read or write. The two pre-authentication callables (`create_account`, `sign_in_with_email`) are the deliberate exceptions.
- Every value entering the app goes through a `require_*` check; every value read back out of the database goes through a `safe_*` guard (`server_code/_validation.py`, `docs/VALIDATION.md`).
- URL hashes: `#dashboard`, `#notes`, `#exams`, `#settings`, `#import-export`, `#login`, `#onboarding`.

## Setting up

1. Create an Anvil app and link it to this repository (see `docs/MANUAL_SETUP.md` §0).
2. Create the four data tables in the Anvil IDE per `docs/MANUAL_SETUP.md` §2.
3. Enable the Users service with the settings in `docs/MANUAL_SETUP.md` §3.
4. Add the `dateparser` Python package (`docs/MANUAL_SETUP.md` §4).
5. Configure the scheduled task and `DEV_EMAIL` app secret (`docs/MANUAL_SETUP.md` §5–6).
6. Implement modules per `docs/IMPLEMENTATION_SPEC.md` §2 and §3 in the order given by §10.

## Design decisions (resolved)

The three decisions flagged at the top of `docs/IMPLEMENTATION_SPEC.md` were all resolved
in favour of the recommended option and are implemented:

1. `assessments.confidence` and `assessments.source_text` exist as nullable text columns,
   set by the parser, `None` for manual entries, and excluded from the editable-field
   whitelist so the parser audit trail survives an edit.
2. `user_settings.timezone` exists as a text column defaulting to `Australia/Melbourne`,
   and every "today" calculation goes through it.
3. `reminder_logs.assessment_id` is stored as text rather than an Anvil row link, so the
   audit record survives the deletion of the assessment it refers to.

Known divergences from the design documents are logged in `docs/DISCREPANCIES.md`.

## Testing

```bash
python -m tests.run_all
```

`tests/` installs a fake `anvil` package into `sys.modules`, so the **unmodified** server
modules import and run on a normal Python interpreter against in-memory tables. Nothing
is mocked out — the suites call the same functions the live app calls.

The suites are organised under the marking rubric's own headings (existence, type, range,
format, reasonableness/completeness, database reads, message quality) and each of the
defects fixed in the validation pass has a test that fails without its fix. See
`docs/VALIDATION.md` for the field-by-field reference and `docs/TESTING.md` for the full
evidence trail.
