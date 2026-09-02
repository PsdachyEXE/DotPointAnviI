# DotPoint — Validation reference

Every input the application accepts, and exactly what is checked before it is
used. This document is the evidence for **SAT criterion 7.3**, whose Very High
descriptor is:

> Validates all relevant input data and checks the reasonableness and
> completeness of all input data. No inconsistencies are present.

and whose named checks are **existence, type, range, format** and
**reasonableness/completeness**. The teacher's brief adds two requirements that
this document also has to answer:

> guard all inputs from the UI, **as well as from the database**, with proper
> validation and **meaningful warning/error messages**.

Those five checks are the column headings below, and the two extra requirements
are §2 and §5.

---

## 1. The architecture: two families, one rule each

All validation lives in `server_code/_validation.py`, in two deliberately
different families. Which family a check belongs to is decided by one question:
**is there a person present who can fix this?**

| | `require_*` | `safe_*` |
|---|---|---|
| Guards | data **arriving** — a form submit, a server argument, an imported file | data **leaving the database** — a row this app wrote earlier |
| On bad input | **raises** `ValueError` | **never raises** — degrades to a documented default |
| Message | a sentence written for the student | none; the app carries on |
| Why | someone is at the keyboard and can correct it, so stopping is helpful | nobody is present, and refusing to render is worse than degrading |

This split is the answer to the "as well as from the database" half of the
brief. A single shared module also means one rule per concept applied
identically on every path, which is what "no inconsistencies are present" asks
for: before it existed, `weight` was range-checked on create but the same field
could arrive unchecked through three other routes.

### Why the database counts as an input

Anvil Data Tables are not a closed system:

- `simpleObject` columns (`reminder_days`, `linked_note_ids`, `tags`,
  `school_terms`, `subjects`) accept **any** JSON, so a value can be a scalar,
  a dict, or a list with three good entries and one bad one.
- Any row can be hand-edited in the Anvil Data Tables console, which bypasses
  every validator above.
- Rows written by an older version of the app may hold values the current enum
  no longer contains, or may be missing a column added later.

The design document anticipated exactly one of these (§6: *"Anvil simpleObject
list_of_dicts (school_terms) corrupted by hand-edit in the Data Tables
console"*). Every column with the same exposure is now guarded the same way.

---

## 2. Field-by-field table

`✓` enforced · `—` not applicable to this field · **bold** = the check that
most commonly fires.

### 2.1 Assessments

| Field | Existence | Type | Range | Format | Reasonableness | Message shown |
|---|---|---|---|---|---|---|
| Title | ✓ | ✓ text | ✓ ≤ 200 chars | — | — | "Title is required." / "Title is too long — keep it to 200 characters or fewer (currently 240)." |
| Subject | ✓ | ✓ text | ✓ must be one of the student's chosen studies | — | legacy VCAA renames coerced first, so old rows stay editable | "That is not a valid subject. Choose one of: …" |
| Type | ✓ | ✓ | ✓ one of `sac, sat, exam, project, homework, other` | — | — | "That is not a valid type. Choose one of: …" |
| **Due date** | **✓** | ✓ date | — | **✓ real calendar date** | **✓ within ±5 years of today** | "Due date must be a real date in the form YYYY-MM-DD." / "Due date is more than five years away (01 May 2062). Check the year." |
| Start date | optional | ✓ date | — | ✓ | **✓ must not be after the due date** | "Start date cannot be after Due date. Check the two dates." |
| **Weight (%)** | optional | ✓ number, **not bool** | **✓ 0–100** | ✓ stored to 2 dp | — | "Weight (%) must be between 0 and 100 (you entered 150)." |
| Status | defaulted | ✓ | ✓ one of `not_started, in_progress, completed` | — | — | "That is not a valid status. Choose one of: …" |
| Description | optional | ✓ text | ✓ ≤ 2000 chars | — | — | "Description is too long — …" |
| Reminder days | defaulted | ✓ list of ints, **not bool** | ✓ each 1–365, ≤ 6 entries | — | — | "Reminder day must be between 1 and 365 (you entered 999999)." |
| Linked note ids | optional | ✓ list | — | — | ✓ each must be a note the student owns | "One of the linked notes could not be found." |
| Source text | optional | ✓ text | ✓ ≤ 500 chars | — | — | — |

**The bool trap.** In Python `bool` subclasses `int`, so `isinstance(True, int)`
is `True`. Without an explicit guard a stored `[True]` in `reminder_days` reads
as the number `1` and fires a "due tomorrow" email nobody asked for. Every
numeric check rejects `bool` explicitly, and `tests/test_validation.py` asserts
it.

### 2.2 Notes

| Field | Existence | Type | Range | Format | Reasonableness |
|---|---|---|---|---|---|
| Title | ✓ | ✓ text | ✓ ≤ 200 chars | — | — |
| Content | optional | ✓ text | ✓ ≤ 20 000 chars | — | — |
| Tags | optional | ✓ list of text | ✓ ≤ 20 tags, each ≤ 40 chars | — | — |
| Pinned | defaulted | ✓ bool | — | — | — |

### 2.3 User settings

| Field | Existence | Type | Range | Format | Reasonableness |
|---|---|---|---|---|---|
| Default reminder days | ✓ | ✓ list of ints | ✓ each 1–365, ≤ 6 | — | — |
| Notifications enabled | ✓ | ✓ bool | — | — | — |
| **Timezone** | ✓ | ✓ text | — | **✓ resolved against the real IANA tz database** | — |
| Theme | ✓ | ✓ | ✓ light or dark | — | — |
| School year | ✓ | ✓ int | ✓ 2000–2100 | — | — |
| **School terms** | ✓ | ✓ list of dicts | ✓ term 1–4 | ✓ each date `YYYY-MM-DD` | **✓ start before end, no two terms overlapping, term numbers unique** |
| Subjects | ✓ | ✓ list of text | ✓ ≤ 12 | — | ✓ ≥ 1 maths; English added if none chosen |

**Why the timezone format check has to hit the tz database.** A pattern match
accepts `Australia/Melbourn`, which is fatal. The only honest check is to ask
the timezone backend to resolve the name.

**Why term ordering is a reasonableness check, not a format one.** Each date is
individually a perfectly valid date. It is only the *relationship* between them
that is wrong — and a backwards term makes `nlp._try_parse_week_phrase`'s
`start <= due <= end` test unsatisfiable, so every "Term X Week Y" phrase
silently stops resolving with no error anywhere. Nothing visibly breaks, which
is what makes it the app's most confusing failure mode.

### 2.4 Account

| Field | Existence | Type | Range | Format | Reasonableness |
|---|---|---|---|---|---|
| **Email address** | ✓ | ✓ text | ✓ ≤ 254 chars | **✓ one `@`, text either side, a dot in the domain** | — |
| Password | ✓ | ✓ text | ✓ minimum length | — | — |

The account **is** the email address, and there is no "confirm password" box
and no in-app password reset — so a typo at sign-up creates an account the
student can never get back into. That is why format is checked here and not
merely left to the platform.

### 2.5 Import file (FR19)

| Stage | Check |
|---|---|
| File | extension, size cap, decodes as UTF-8 — all client-side, before upload |
| Envelope | parses as JSON; has the expected top-level keys; version recognised |
| Each row | validated against the same `_validate_assessment_payload` / note validator as a manual create |
| Whole file | written inside one transaction — an invalid file writes **nothing** |
| Horizon | the ±5-year due-date check is **deliberately skipped** on import, so a legitimate old export still restores |

---

## 3. Reasonableness and completeness

Field-by-field validation cannot catch a record where every field is
individually valid and the record is wrong *as a whole*. These are the checks
that look at more than one value at a time.

| Check | What it catches | Where |
|---|---|---|
| `require_not_after(start, due)` | a start date after the due date | assessments |
| `require_not_after(term start, term end)` | a term that runs backwards | settings |
| term overlap / uniqueness | Term 2 starting before Term 1 ends | settings |
| `require_within_horizon(due, today)` | a mistyped year — `2062` for `2026` | assessments (not import) |
| `require_complete_record(...)` | a bulk line missing several fields — reported in **one** message, not one per submit | bulk add, import |
| guarded `days_remaining` | a calculation result that would render as "due in 13,000 days" | dashboard |
| ownership re-check | a record that is valid but is not the caller's | every row-by-id path |

The last one is a completeness check in the rubric's sense: the input is a
record id, and a valid id belonging to somebody else is an *incomplete*
authorisation, not a malformed one.

---

## 4. Guarding what comes back out of the database

Every one of these is a value the app could genuinely meet, and each used to be
trusted.

| Column | What could be there | What happens now |
|---|---|---|
| `user_settings.timezone` | `'Australia/Melbourn'` after a console edit | falls back to `Australia/Melbourne` |
| `user_settings.notifications_enabled` | `None` on a row written before the column | reads as **off** (fails closed) |
| `user_settings.school_terms` | a scalar, a dict, a list with one bad term | bad elements dropped, good ones kept |
| `user_settings.default_reminder_days` | `7` instead of `[7]`; `[True]`; `[0]` | non-positive-int elements dropped |
| `user_settings.subjects` | a name that is not a VCE study | dropped |
| `assessments.status` / `.type` | a legacy Title-Case `'Complete'` | pinned to the default; never silently rewritten |
| `assessments.weight` | `250`, stored before the range rule existed | treated as absent |
| `assessments.reminder_days` | any JSON | sanitised per element |
| `notes.tags` | a non-string tag | dropped |
| any date column | an unparseable string | treated as absent |

### The three failures this prevented

1. **`_user_now()` took the whole app down.** An unresolvable stored timezone
   was passed straight into `ZoneInfo()`. Every screen calls this function, so
   one bad cell made the app unusable — *including the Settings page that was
   the only way to correct the value*.
2. **The app could say reminders were off while sending them.** The Settings
   screen read `notifications_enabled` with `bool()` (so `None` → "off") and
   the dispatcher read it with `is False` (so `None` → "keep sending"). Both
   now use `safe_bool`, defaulting to off.
3. **A corrupt column silently skipped a whole student's reminders.** A scalar
   in `reminder_days` raised `TypeError` inside the per-user handler, which
   counted it and moved to the next student — abandoning every remaining
   assessment for that one.

---

## 5. Message quality

The rubric asks for *meaningful* warning and error messages. Every message the
student can see follows four rules:

1. **Names the field as the interface labels it** — "Due date", never
   `due_date`.
2. **Says what is wrong and what to do** — "Weight (%) must be between 0 and
   100 (you entered 150)", not "invalid weight".
3. **Quotes the offending value back** where that helps them spot the typo.
4. **Is a sentence** — capital letter, full stop, no Python type names.

`tests/test_validation.py` asserts rules 1–4 mechanically across a
representative rejection from every family, including that no message leaks a
developer term (`None`, `isinstance`, `ValueError`, `traceback`, …).

### Where the message appears

- **Beside the field** it belongs to, via `common.set_field_error()` —
  this is what SRS **FR04** asks for ("a server error that the form surfaces
  beside the offending field").
- A toast is used only for whole-form or whole-operation outcomes.

### `friendly_error()`

Every form used to do `toast_error(str(e))`, so a student who left the due date
empty was shown the literal text `invalid date: None`. `common.friendly_error()`
is now the single place that decides what a student sees: a message the app
wrote for a person passes through unchanged; anything else — a network drop, a
platform error, a bug — is replaced with a plain fallback.

---

## 6. Client and server

| | Client | Server |
|---|---|---|
| Role | fast, friendly first pass | **the authority** |
| Runs | on submit | on every write, from every path |
| Can be skipped? | yes (a crafted request) | no |

Client checks are **additive**. No server check was removed or weakened to make
room for one, and no client check is relied on for correctness — the tests in
`tests/` exercise the server validators directly, with no browser involved.

Where both sides check the same field they use the same bound from
`server_code/_constants.py`, and `tests/test_constants_integrity.py` asserts
that the client's mirrored copies still match the server's — because the client
cannot import server modules, several tables are duplicated into the forms, and
a comment saying "keep in sync" is not a mechanism. That suite exists because
the two copies had in fact drifted.

---

## 7. Verifying it

```bash
python -m tests.run_all
```

`tests/` contains a fake `anvil` package that lets the **unmodified** server
modules run on a plain interpreter, backed by in-memory tables. Nothing is
mocked out: the suites call the same functions the live app calls.

The suites are organised under the rubric's own headings — `existence`, `type`,
`range`, `format`, `reasonableness/completeness`, `database reads`, and
`message quality` — so each assertion maps to a line of this document.

See `docs/TESTING.md` for the full testing evidence, including the live
end-to-end journeys and the defect → fix → re-test trail.
