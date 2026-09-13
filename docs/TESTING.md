# DotPoint — Testing Evidence

Testing record for the Anvil build of the DotPoint Assessment Tracker
(VCE Software Development, Unit 4 Outcome 1). Three layers of testing were
used: **offline unit suites** against the real server modules, a
**measured evaluation-criteria run** for the parser, and **live end-to-end
journeys** in the running app. Defects found at each layer were fixed and
re-tested; the trail is recorded below.

## 1. Offline unit suites (62 suites, 948 assertions) — **in the repository**

The server modules are pure Python over Anvil's table API, so they are tested offline.
`tests/anvil_stub.py` installs a fake `anvil` package into `sys.modules` — rows, tables,
queries, transactions, users, email and secrets, backed by in-memory dictionaries — and
`tests/harness.py` synthesises a `server_code` package so the relative imports resolve.

Nothing in `server_code/` is altered, patched or mocked out: **the suites call the same
functions the live app calls.**

Run them from the repository root:

```bash
python -m tests.run_all
```

The runner prints `<n> assertions passed, <m> failed` and exits non-zero on any failure.
The assertion count below is **counted by the runner, not claimed** — reproduce it in one
command.

The suites are organised under the marking rubric's own five checks, plus the two
requirements the teacher's brief adds (guard the database too; write meaningful messages),
so each group of assertions maps to a row of `docs/VALIDATION.md`.

| File | Suites | Covers | Assertions |
|---|---|---|---|
| `test_validation.py` | 7 | The `require_*` / `safe_*` families under the rubric's own headings: existence, type, range, format, reasonableness/completeness, database reads, message quality | 180 |
| `test_constants_integrity.py` | 9 | The hand-copied client mirrors still match `_constants.py`; the subject picker offers every canonical study and never the parser-only catch-all; alias and legacy-rename integrity; the editable-field whitelist protects the audit columns; **no client module contains a hex colour**; **every `role=` used in client code has a matching stylesheet rule**; both list panels word their empty state for the reason the list is empty | 306 |
| `test_assessments.py` | 12 | Create; the SAME rule applied on all four write paths (create/update/bulk/import); reasonableness; weight formatting; FR02 partial bulk commit; ownership (NFR03) incl. cross-account reads; missing-row consistency; export/import round trip; the FR18 export contract (filename, version, `reminder_logs` exclusion); the parser audit trail's documented trim | 115 |
| `test_notes.py` | 9 | Note field bounds; missing-row consistency; ownership; search surviving a corrupt `tags` column; settings validation; school-term ordering/overlap/uniqueness (FR15); settings read guards; the two VCE program rules; account creation and email format | 78 |
| `test_nlp.py` | 8 | Parser accuracy unchanged by the new guards; unbounded "in N days"; input bounds; weight readings that are not the number typed; the three confidence bands and the `why` provenance; **Term X Week Y resolution (FR15)**; corrupt `school_terms`; corrupt `subjects` | 89 |
| `test_parser_accuracy.py` | 3 | EC-EF-01 subject identification and EC-EF-02 due-date extraction, measured over 30 inputs against their 80% targets, plus a guard on the test set's own size and variety | 69 |
| `test_reminders.py` | 7 | Due thresholds from untrusted columns; the notifications master switch; skips (completed/undated); permanent dedup (NFR02); failed-send retry; one student's bad data not stopping the run; email content | 58 |
| `test_readme.py` | 4 | The two copies of the user manual are byte-identical; every section the brief names is present; the published copy is reachable; the limits the manual quotes match `_constants` | 29 |
| `test_datetime.py` | 3 | The timezone read guard; DD MMM YYYY display (NFR08); urgency bands (FR21) | 24 |
| **Total** | **62** | | **948** |

> Counts measured on 13 September 2026 by `python -m tests.run_all`. `test_readme.py` was
> missing from this table entirely while it was registered in `run_all.py` and running,
> which is why the stated total had drifted below the measured one.

### What these suites are designed to prove

Not merely "the code runs". Each group answers a specific clause of the rubric:

- **"validates all relevant input data"** — every write path is driven with a valid record
  and then with that record spoiled one field at a time.
- **"no inconsistencies are present"** — `test_assessments.suite_consistent_across_paths`
  applies each bad value to create, update AND bulk and asserts all three refuse it. A rule
  enforced on one path only would fail here.
- **"reasonableness and completeness"** — records where every field is individually valid
  and the record is still wrong: a start date after the due date, a mistyped year, a term
  that runs backwards, a bulk line missing three fields.
- **"as well as from the database"** — every `safe_*` helper is driven with the values an
  Anvil `simpleObject` column can actually hold after a console edit (a scalar, a dict,
  `None`, a part-corrupt list) and asserted never to raise.
- **"meaningful warning/error messages"** — `suite_messages` takes a representative
  rejection from every family and asserts mechanically that the text starts with a capital,
  ends as a sentence, and leaks no developer term (`None`, `isinstance`, `ValueError`,
  `traceback`).
- **Security (NFR03)** — a second account is created and asserted unable to read, edit or
  delete the first account's rows, and to receive an empty list rather than a refusal.

### Regression locks

Every defect fixed in the validation pass has a test that **fails without its fix**:

| Defect | Locked by |
|---|---|
| An unresolvable stored timezone took the whole app down | `test_datetime.suite_timezone_read_guard` |
| Settings could show reminders OFF while the dispatcher emailed | `test_reminders.suite_notifications_switch` |
| A scalar `reminder_days` skipped a student's whole run | `test_reminders.suite_thresholds` |
| `"in 99999999999 days"` raised `OverflowError` | `test_nlp.suite_unbounded_day_counts` |
| Bulk add was all-or-nothing, contradicting FR02 | `test_assessments.suite_bulk_partial_commit` |
| Bulk rejections reported the wrong line number | `test_nlp.suite_input_bounds` |
| `get_*` raised where `delete_*` returned `False` | `test_assessments.suite_missing_row_consistency`, `test_notes.suite_note_missing_row_consistency` |
| Reminder offsets had no upper bound on write or read | `test_validation.suite_range`, `test_reminders.suite_thresholds` |
| The client subject-group mirrors had drifted from the server | `test_constants_integrity.suite_subject_group_mirrors` |

> **Note on the previous figure.** Earlier revisions of this document cited *14 suites,
> 974 assertions* with the harness "kept outside the repo". Those scripts were lost with
> the working directory they lived in, and an assessor could not have run or read them.
> The suite above was rebuilt from scratch and **committed**, so the claim is now
> reproducible. The assertion count is lower and the coverage is different — it is
> concentrated on validation, which is what criterion 7.3 is marked on.

## 2. Evaluation-criteria measurement (EC-EF-01 / EC-EF-02)

A 30-input test set is run against the real `nlp.py` with school terms configured,
covering all four date forms EC-EF-02 names (weekday, DD/MM including a date that must
roll into next year, month names, Term X Week Y with and without a weekday offset) plus
`tomorrow` and `in N days`, across nine studies.

The set lives at **`tests/test_parser_accuracy.py`** and both percentages are recomputed
on every run, so this table can no longer drift from the measurement. To reproduce the
input-by-input table:

```bash
python -m tests.test_parser_accuracy
```

| Criterion | Target | Result (measured 13 Sep 2026) |
|---|---|---|
| EC-EF-01 subject identification | ≥ 80% | **30/30 = 100%** |
| EC-EF-02 due-date extraction | ≥ 80% | **30/30 = 100%** |

> **Provenance of these figures.** The same 30/30 was recorded in July 2026 against a
> different set that lived in a session scratchpad and was lost with it. The sentences
> above are NEW and the July run is not reproducible; only the September measurement is
> evidence. Expected dates in the set are computed from the student's meaning by helpers
> in the test file, never read back from `parse_text`, or every assertion would be true
> by construction.

## 3. Live end-to-end journeys (debug runtime, fresh test account)

Performed in the running app with a dedicated test account
(`claude.tester@dotpoint.dev`), i.e. a true new-user path:

1. Sign-up → settings row lazily created with defaults (7+2 days, reminders on).
2. Settings → **Load VIC 2026 term dates** preset → Save → hint banner clears.
3. Parse `Methods SAC2 due term 3 week 5 worth 25%` → preview: **HIGH** badge,
   provenance lines (`"term 3 week 5" → 10 Aug 2026` etc.) → Save.
4. Card renders with subject/type chips, `parsed · HIGH` audit tag, urgency
   colour + "(in 18 days) · 25% of grade"; appears in Upcoming and as a
   coloured, clickable calendar day.
5. Inline status change on the card (EC-UX-05) → persists across refresh.
6. Notes: create + tag (×2) + pin → pinned-first card, tag filter auto-grows.
7. Edit assessment → linked-notes search → link note (FR12) → Save.
8. Bulk add 3 lines → per-line confidence (HIGH/HIGH/MEDIUM), Term-Week line
   resolved to 31 Aug 2026 → "Created 3 assessment(s)". (Recorded at the time as
   "atomic"; defect 27 later replaced that with per-line commit to match FR02, so
   the wording here has been corrected rather than left contradicting the fix.)
9. Filters (subject auto-populated), sort by weight/due date, day-click popup,
   Import/Export page render, sign-out/sign-in cycle.

## 3b. Live journeys — subjects / theme / exams slices (spec §11–§13)

Performed in the published app as `claude.tester@dotpoint.dev` after the
`subjects` column migration:

1. Sign-in → router forces `#onboarding` ("What subjects do you do?", full
   grouped catalog, rendered in the account's stored dark theme).
2. Rules: empty selection blocked; no-maths selection blocked with the maths
   message; no-English selection raises the confirm dialog and locks in with
   **English auto-added** (chips + dashboard filter show it).
3. Parser priority: `maths sac friday` → preview `matched 'maths' →
   Mathematical Methods` (single-locked-maths remap) → saved.
4. Exams (#exams): countdown banner + per-paper cards sorted by date with
   correct VCAA times; adding Literature via Settings immediately added its
   29 Oct paper. Dashboard: next-exam chip; purple ▲ markers on 27/30 Oct;
   day popup lists "VCE exam: English — Written examination".
5. Settings: theme Dark→Light flips the whole palette instantly and persists
   across a fresh session/login; Change subjects… (confirm → prefilled picker
   → save) updates chips, filter and exams.
6. Regression sweep: inline card status persists; sort by weight; notes
   create/pin/tag/search; bulk add 3 lines (Term-4-Week-2 resolved, locked
   `lit` → Literature, fallback `spesh` → Specialist Mathematics) created
   per-line (see defect 27); export JSON (8 assessments / 2 notes / settings incl. locked
   subjects) → re-import renamed all 8 duplicates.

## 3c. Live journeys — UI overhaul (spec §14)

Performed in the published app as `claude.tester@dotpoint.dev`, in **both
themes**, after each push (the GitHub webhook auto-syncs Anvil):

| # | Checked | Result |
|---|---|---|
| 1 | Login screen + credential dialog | Wordmark / tagline / pitch on the role type scale; dialog rebuilt on `make_field`; affirmative button accent, Cancel secondary |
| 2 | Top bar | Active tab underlined on all five routes; Sign out pushed to the right edge; survives both themes |
| 3 | Dashboard hierarchy | parse bar → filters → next-exam strip → three panels; urgency moved from a `●` glyph to the card's left edge |
| 4 | **Calendar** | Real 7-column grid, evenly sized cells, today ring, per-day count badge, `▲` exam marker; every cell of the grid present, Sunday no longer clipped (the grid is 4, 5 or 6 rows depending on the month — 42 cells is the six-row case, not a constant) |
| 5 | Calendar interaction | Only days with content are links (3 of 42 in August); day popup lists exams and assessments |
| 6 | Parse → preview → save | `Physics SAC3 due term 3 week 9 worth 30%` → HIGH chip, provenance shown as field hints (`matched "term 3 week 9" → 07 Sep 2026`) → saved; list went 16 → 17 |
| 7 | Bulk add | Three lines → HIGH / MEDIUM / LOW chips, LOW row auto-unticked with a "LOW confidence" reason chip; `12/09` → 12 Sep 2026, `term 4 week 2` → 12 Oct 2026 |
| 8 | Notes + note editor | Tags render as individual chips; editor uses labelled fields with hints |
| 9 | Exams | Next-exam banner plus a list card per paper, countdown chip driven by the urgency band |
| 10 | Settings | Four grouped cards; reminder days as toggle pills; the four term rows aligned |
| 11 | **Subject picker** | 56 studies as toggle pills, group headers with live counts, running "6 subjects selected" total |
| 12 | Import & export | Two grouped cards, one-line explanations |
| 13 | **Theme** | Dark ↔ light flips the whole app instantly and persists across a new session; urgency, confidence and exam colours stay recognisable in both |
| 14 | **Toasts (defect 13)** | Four rapid saves → stack capped at 3, bottom-right (cannot cover the nav), all gone after the timeout |
| 15 | **Browser Back** | Dashboard → Notes → Exams → Back lands on `#notes` with the right active tab and exactly **one** rendered top bar (no double render) |
| 16 | **Mobile** | 768px and 375px: the three panels stack, the calendar fits (48px cells at 375px), no horizontal overflow, top bar 63px instead of 169px |

## 3d. Live journeys — validation and documentation pass (criterion 7)

Performed 2 September 2026 in the **published** app
(https://honored-willing-tea.anvil.app) as `claude.tester@dotpoint.dev`, immediately
after the GitHub push auto-synced into Anvil. Anvil form code is runtime-checked, not
compile-checked, so the new `make_field(required=True)` argument, the `set_field_error`
channel and the `role='fielderror'` stylesheet rule could only be proven this way.

| # | Journey | Result |
|---|---|---|
| 1 | **No regression on load.** Dashboard after sign-in compared against the screenshot taken before any change | Identical: 16 assessments shown, same filter/sort bar, September 2026 calendar with the 2nd ringed and a count badge on the 7th, "Physics SAC3 · 07 Sep 2026" in Next 30 Days, next-exam chip "English — Written examination · in 55 days" |
| 2 | **Required markers render.** Sign-in dialog, and Add assessment | "Email \*" / "Password \*"; "Title \*", "Subject \*", "Due date \*" marked, and "Start date (optional)" correctly NOT marked |
| 3 | **Field hints appear before the student types** | "Up to 200 characters." under Title; "A percentage between 0 and 100." under Weight — previously the cap was only discovered by failing |
| 4 | **Submit is blocked with empty required fields (SRS FR03)** | Save produced no server call. Three messages, each in red beneath its own field: "Title is required.", "Choose a subject.", "Due date is required." |
| 5 | **The message describes the right problem** | An unselected subject reads "Choose a subject." — the server's old text was "invalid subject", which describes a different fault |
| 6 | **Messages are re-evaluated, not stale** | Filling only the Title and pressing Save again cleared the Title message while leaving the Subject and Due date messages in place |
| 7 | **Range check fires client-side, quoting the value** | Weight 150 → "Weight (%) must be between 0 and 100 (you entered 150)." beneath the Weight box, with no round trip |
| 8 | **Errors appear beside the field, not in a toast (SRS FR04)** | All four messages rendered inline via the new `fielderror` role; the toast stack stayed empty |
| 9 | **The flagship parser workflow is unbroken** | "Methods SAC2 due Friday week 5 worth 25%" → preview with a HIGH badge, Title "Methods SAC2", Subject Mathematical Methods, Type SAC, Due date 04 Sep 2026, each with its provenance line ("matched \"friday\" → 04 Sep 2026"). The new required markers coexist with a parsed preview without blocking it |
| 10 | **`_validation` module synced** | Visible in the Anvil IDE Server Code list; every screen that calls it renders |

The risk this pass was watching for was that a pre-submit check would block
`mode='preview'`, since that mode exists precisely so a student can hand-correct a
LOW-confidence parse. Journey 9 confirms it does not.

## 4. Defects found → fixed → re-tested

| # | Found by | Defect | Fix | Re-test |
|---|---|---|---|---|
| 1 | Offline parser run | Title extraction left orphan digits ("2 5") after stripping matched spans | Keep subject/type words; strip date/weight/week phrases + orphan numbers | parser suite |
| 2 | Offline parser run | `SAT term 2 week 3` read the type "SAT" as Saturday (weekday offset) | Weekday must follow `term N` in the week-phrase regex | parser suite |
| 3 | Adversarial audit | Bare `sat` in the free weekday matcher collided with the SAT type — fabricated/overrode due dates at HIGH confidence | Excluded `sat` abbreviation from that matcher ('saturday' still parses) | `test_parser_accuracy` row 14 ("softdev SAT due 12 November") |
| 4 | Adversarial audit | Wrong modal close event `x-close` (Anvil uses `x-close-alert`) — every dialog Save/Cancel was a silent no-op | Renamed at all 5 raise sites | live journey |
| 5 | Adversarial audit | Reminder email counted down from the threshold, not actual days remaining | `_build_email` takes `days_remaining`; "due today/in 1 day/in N days" | reminders suite |
| 6 | Adversarial audit | Bare DD/MM dates in the past were not rolled to next year (inconsistent with month-name path) | Roll forward when no explicit year given | `test_parser_accuracy` row 13 ("bio sac due 1/3" → next year) |
| 7 | Live run | `zoneinfo` missing on Anvil's Full-Python-3 runtime (pre-3.9) — signup crashed | `_get_tz()` compat: zoneinfo, else pytz | `test_datetime.suite_timezone_read_guard` (zoneinfo path only — pytz is unreachable where zoneinfo exists) + live |
| 8 | Live run | Anvil cannot serialize int dict keys — dashboard crashed once a calendar day had assessments | Calendar day keys stringified server-side | live only — `dashboard.py` has NO offline suite (see §6) |
| 9 | Live run | White-on-background calendar cell rendered invisible in the runtime theme | Urgency carried in coloured bold "● N" text | live |
| 10 | Live run | "in 1 days" pluralization on cards | day/days switch | live |
| 11 | Live run | Success toasts never auto-dismissed; the stack covered the action bar and swallowed clicks | `timeout=4` on all 31 notifications | live |
| 12 | Live sweep (task logs) | Reminder emails never sent: every `run_reminder_check` pass died on `ServiceNotAdded` — the Anvil **Email service** was never added to the app, so the 30-min task COMPLETED while `anvil.email.send` raised before any send/log; `reminder_logs` stayed empty and no inbox ever received mail | Email service added to `anvil.yaml` services | next scheduled run: error gone, **8 reminder_logs rows written** (7_day + 2_day for the test user's due-soon assessments) — send path proven |
| 13 | Live sweep | Some success toasts still failed to auto-dismiss despite `timeout=4` and stacked over the top bar, where they swallowed nav clicks | Single `common.toast()` helper: it keeps a reference to every live toast (an unreferenced `Notification` was being collected before its own timer ran), dismisses it on its own timer and caps the stack at 3; the stylesheet moves the stack bottom-right so a stuck toast can never cover the nav | Live: four rapid saves → 3 shown bottom-right, 0 left after the timeout |
| 14 | UI overhaul, live | Every dialog crashed with `TypeError: Spacer got unexpected keyword argument(s): 'role'`. `make_divider()` was built on a `Spacer`, one of the few Anvil components that rejects `role`, so it raised at construction — taking the login screen, both editor dialogs and onboarding with it | Rebuilt `make_divider()` on an empty `ColumnPanel` | Live: login, note editor and parser preview all render |
| 15 | UI overhaul, live | The calendar rendered as a column of slivers. Anvil's `.flow-panel-gutter` carries −15px side margins (so a flex row can bleed into its container's padding) and `.flow-panel-item` is sized for flex, so under `display:grid` the grid overflowed its column and no cell stretched | Cancel the gutter margins and force the items to fill their grid column | Live: 42 evenly sized cells, Sunday no longer clipped |
| 16 | UI overhaul, live | Every *coloured* list card rendered at roughly double height — the rule neutralising Anvil's per-component margins matched `.anvil-role-listcard`, but the cards carry `.anvil-role-listcard-overdue` and friends | Attribute-substring selectors, plus one deliberate gap between stacked rows | Live: cards on every screen |
| 17 | UI overhaul, live | Rows never lined up with the section header above them: Anvil adds a 15px side margin to each `.flow-panel-item` on top of the CSS `gap`, giving ~38px between chips and indenting the first one | Cancel both, so `gap` alone decides horizontal spacing | Live: picker pills and section headers share a left edge |
| 18 | UI overhaul, adversarial review | `navigate()` had been changed to rely on the browser raising `hashchange`, but the new listener ignores events raised while a dialog fades out — so signing in or finishing onboarding could change the URL and leave the page untouched, stranding the student on the onboarding gate (which has no top bar) | `navigate()` sets the hash and re-enters the router unconditionally; the listener handles only Back/Forward and pasted links, and ignores the echo of the app's own hash writes (which also removed a double render and its duplicate round-trip) | Live: nav clicks, sign-in and Back each route exactly once |
| 19 | UI overhaul, live | The dashboard's parse box never widened, and both dialog buttons rendered as the primary action | A `TextBox` puts its role class on the `<input>` itself (there is no wrapper), and every `alert()` button is an identical `.btn-default` in its own wrapper so `:first-child` matched both | Live: 420px input; Sign in accent, Cancel secondary |
| 20 | UI overhaul, design review | `--dp-soon` had been set to the exact accent hex, so a "soon" chip and an accent chip were the same colour; `chip-ok` painted green text on a grey fill | Restored the band's own blue; added `--dp-ok-soft` to both palettes | Visual check in both themes |
| 21 | Final adversarial review | Accessibility and dead CSS: white text on the accent measured **2.7:1** in the dark palette (fails AA, and got worse on hover); six of nine chip tones fell just under 4.5:1 in light; two rules were silently dead because a later `!important` beat them on importance rather than specificity ("Sign out" no longer pushed right; a `make_field` inside a `make_card` lost its own spacing) | Added a themed `--dp-on-accent`; darkened the light-mode hues without changing the red-orange-blue-grey ramp; `!important` on the two overridden rules; chip tone colour no longer depends on source order | Live, both themes: primary button now dark-on-blue in dark mode, Sign out flush to the right edge |
| 22 | Final adversarial review | Two latent faults the live sweep had not hit: the router recorded its route only at render time, so a `hashchange` delivered while `get_settings()` suspended the client could look like a Back press and build the screen twice; and if `get_settings()` failed on page load, "Change subjects…" opened the picker empty and saving it would overwrite the student's real locked subjects | Claim the route before the blocking call; re-read subjects before offering the picker and refuse if still unknown | Reasoned from the code and re-tested live (sign-in renders once; nav, Back and dialogs all route once) |
| 23 | Criterion 7 audit + offline suite | **A stored timezone the tz database cannot resolve took the whole app down.** `_user_now()` passed `user_settings.timezone` straight into `ZoneInfo()`/`pytz.timezone()` with no guard. Every screen calls that function, so one bad cell — reachable by a Data Tables console edit, or an import whose settings patch was swallowed — made the app unusable, *including the Settings page that is the only way to correct the value* | `_datetime._safe_timezone()` resolves the stored name and falls back to `Australia/Melbourne` when it cannot; `_user_now` also tolerates the column not existing yet | `tests/test_datetime.py` drives seven damaged values (a plausible typo, `None`, a number, a list, a pre-migration row that raises on lookup) and asserts none raises, while a valid `Australia/Perth` is still honoured |
| 24 | Criterion 7 audit + offline suite | **The app could tell a student reminders were OFF and keep emailing them.** `user_settings.notifications_enabled` was read two ways: `bool()` on the Settings screen (so `None` drew the switch off) and `is False` in the dispatcher (so `None` meant keep sending) | Both readers now use `_validation.safe_bool(..., default=False)` — failing closed, because an unsent reminder is a nuisance and an unwanted one is worse | `tests/test_reminders.py` sets the column to `None`, `'yes'`, `0`, `1` and `''` and asserts nothing is sent in any case |
| 25 | Criterion 7 audit + offline suite | **One corrupt column silently skipped a student's entire remaining run.** A hand-edited `reminder_days` holding `7` instead of `[7]` raised `TypeError` inside `_process_user`, which the per-user handler counted and moved on from — abandoning every assessment after it | `_get_due_thresholds` sanitises through `safe_list(..., is_positive_int)`; a corrupt column yields no thresholds instead of raising | Offline: five corrupt shapes assert no raise; a part-corrupt `[7, 'x', None, 2]` still fires both good thresholds |
| 26 | Criterion 7 audit + offline suite | **`"in 99999999999 days"` crashed the parser.** The `(\d+)` capture was unbounded and went straight into `timedelta`, which raises `OverflowError` past ~999,999,999 days | The captured count is bounded to the same five-year horizon the validator uses; past it the phrase is treated as "not a date" and falls through, rather than raising or fabricating a date decades away | `tests/test_nlp.py` parses four hostile counts without raising, and asserts `"in 10 days"` still resolves |
| 27 | Criterion 7 audit | **Bulk add contradicted the project's own SRS.** `create_bulk_assessments` was all-or-nothing, but FR02 reads "Valid lines still commit so a single bad line does not block the rest" — and the design document's §3.3.6 pseudocode agrees. Both source documents said one thing and the code did the opposite | Per-line commit: valid records are inserted, invalid ones returned with their index and reason | `tests/test_assessments.py` sends a three-line batch with one bad line and asserts exactly the two good rows exist |
| 28 | Criterion 7 audit | **Bulk rejections pointed at the wrong line of the student's paste.** The message printed the index in the ticked-only records list as if it were the source line number; with any unticked line the two diverge | `parse_bulk` carries a real `line_index` through from the original paste | Offline: a paste with a blank middle line asserts indices `[0, 2]`, i.e. numbering that reflects the original text |
| 29 | Criterion 7 audit | **Editing an assessment could silently rewrite it.** A stored `type` or `status` outside the current enum was assigned to a DropDown that did not offer it; the control fell back to its first item and `_build_payload` wrote that back on save. The same method already defended `subject`, so this was an internal inconsistency as well as a defect | Membership is checked before assigning; an unrecognised stored value selects nothing and reports itself to the student rather than being substituted | Offline: `safe_choice` asserted to pin an off-enum value to the default; live: an edit round-trip preserves type and status |
| 30 | Criterion 7 audit | **`get_*` raised where `delete_*` returned `False`** for the identical missing-row condition, inside the same module — a quotable breach of 7.3's "no inconsistencies are present" | All by-id paths raise the same student-facing sentence | Offline: the three assessment paths and the three note paths are asserted to raise, and to raise the *same* message |
| 31 | Criterion 7 audit | **Reminder offsets had no upper bound.** `[999999]` passed every check, and made an assessment permanently "due soon" — emailing the student about all of them on the first tick. The read guard accepted it too, so a stored value bypassed the new write rule | `MIN_/MAX_REMINDER_DAY` bounds on write, and one shared `is_valid_reminder_day` predicate used by the three modules that read the column | Offline: rejected on write; a stored `999999` is dropped on read |
| 32 | Offline suite (tripwire) | **The client's copies of the server subject groups had drifted**, under a comment saying "keep in sync" | Mirrors corrected; `tests/test_constants_integrity.py` now reads the client forms with `ast` and asserts equality, and separately asserts the picker offers every canonical study and never the parser-only catch-all | The suite fails if either drifts again |
| 33 | Criterion 7 audit | **Raw developer strings were shown to students at 23 sites**, six of them a bare `toast_error(str(e))` — so leaving the due date empty displayed the literal text `invalid date: None` | Server messages rewritten as sentences addressed to the student; `common.friendly_error()` is now the single place that decides what is fit to show, replacing anything that does not read as a written sentence | Offline: every family's rejection asserted to be sentence-shaped and free of developer terms; live: journeys 4–7 in §3d |
| 34 | SAT 8 alpha testing (breakpoints) | **Every fixture that claimed to test Term X Week Y (FR15) missed it entirely.** `_try_parse_week_phrase` requires a literal `term N` token and returns at its first guard without one, so "Methods SAC2 due Friday week 5 worth 25%" resolved its date from the WEEKDAY. `suite_corrupt_school_terms` therefore drove seven damaged column shapes through a sentence that never reads the column: 15 assertions that passed without touching the code they named and could not fail. `_stored_terms`, `_is_well_formed_term` and `_iso_to_date` had no coverage at all | Fixtures now carry a real `term N` token; `suite_term_week_dates` added for FR15 itself (Monday of the requested week, a second term, the weekday offset, provenance, and all three fall-through cases); `suite_corrupt_school_terms` now asserts the degradation is OBSERVABLE (`due_date` is None), not merely that nothing raised | Red/green transcripts and the `pdb` session that diagnosed it: `docs/evidence/sat8/` |
| 35 | SAT 8 alpha testing | **Two percentages were read as a different number from the one typed.** `_extract_weight` matches from the first digit, so `-5%` stored 5, `1e5%` stored 5, and `.5%` stored 5 — all at HIGH confidence, with a provenance line quoting the same bad capture | The character before the digits is inspected; a minus sign, a decimal point, or an `e` that itself follows a digit rejects the candidate. `re.finditer` replaces `re.search` so a rejection means "keep looking" rather than "no weight here" | `test_nlp.suite_weight_reading` (three misreads, the keep-looking rule, seven forms that already worked); live: TC-PRE-04 |
| 36 | SAT 8 alpha testing (code read) | **An edit that sent no reminder days re-armed the ones the student had turned off.** `_validate_field` passed `reminder_days=None` to the shared payload validator, which correctly reads None on a CREATE as "not supplied, use the defaults" — but an update carries only the keys the student changed, so the key being present is the statement that it was set. `[]` became `[7, 2]` | The one editable column whose None differs on an edit is settled in `_validate_field` before the shared rule sees it | `test_assessments.suite_empty_reminder_days`; latent (the shipped editor always sends a list), recorded as such |
| 37 | SAT 8 alpha testing (live) | **A list emptied by FILTERS was described as a list never filled.** Filtering 17 assessments to Literature + Exam produced "Nothing here yet / Type an assessment above…", which describes the account rather than the filters and reads as data loss. `NotesForm` had drawn this distinction since it was written; `DashboardForm` had not | `_filters_are_narrowed()` reads the same sentinels `_build_filters` reads; a second empty state says "No assessments match" and offers a `Clear filters` button | `test_constants_integrity.suite_empty_states_name_their_cause` (both panels); live: TC-PRE-07, failed then re-tested after the fix |
| 38 | SAT 8 alpha testing (App Sessions log) | **Scheduled runs are occasionally lost to the platform.** Filtering the session log to errors over 18 Aug – 13 Sep 2026 (~1,250 runs) shows five failures, all `anvil.server.RuntimeUnavailableError` — four "Server runtime failed to start", one "Downlink disconnected". `run_reminder_check` never executed on those ticks | No code change: this is Anvil's runtime, not the app. The design already absorbs it — thresholds test `days_remaining <= d` rather than `== d`, and the dedup key carries no date, so the next successful run sends what the missed one would have, and nothing twice | Recorded rather than fixed. **No exception raised by application code appears anywhere in that window** |

Config/platform issues resolved en route, none of them application defects: the Users
service missing its `user_table` binding; an unavailable `python310-full` base-image
pin; the database schema migration (`users` table server permission); and the GitHub
token re-authorisation. Listed after the table rather than inside it, where the
paragraph used to sit and split the markdown in half.

## 4b. What the offline suites do NOT cover

Stated plainly, because a suite's silence is easy to read as coverage.

- **`dashboard.py` (109 statements) and `exams.py` (69) have no offline suite at all** and
  are imported by no test file. That is FR08's calendar grid, half of FR09, the whole exam
  timetable, and `get_dashboard_data` — the busiest callable in the app. Every test case
  for those in the SAT 8 tables is therefore a live journey, and is labelled as one.
- **No client form executes in any test.** `client_code` pulls in Anvil's UI components,
  which do not exist outside the browser, so the three client tripwires
  (`suite_no_falsy_empty_reads`, `suite_empty_states_name_their_cause`,
  `suite_no_client_colours`) read the source with `ast` or string matching instead of
  running it. They can prove a shape is present; they cannot prove a button works.
- **The `pytz` branch of `_get_tz` never runs offline**, because `zoneinfo` exists on the
  local interpreter. Only the deployed runtime exercises it.
- **`dateparser` is not installed in the harness**, so step 7 of the date chain (the
  free-form fallback) is offline-unreachable. Any test of a free-form date must be live.
- **`notes.sign_in_with_email` is called by no test**, so FR20's anti-enumeration message
  is asserted only by reading it. The stub's password handling is fake, so nothing about
  real Anvil authentication is proven offline in any case.

## 5. Security spot-checks

- Every `@anvil.server.callable` resolves `_require_user()` first; all queries
  scoped by the `user` link column (NFR03 / EC-SEC-01 pattern, uniform across
  modules — verified by review). A second account IS created and asserted unable to
  read, edit or delete another account's rows, offline, in
  `test_assessments.suite_ownership` and `test_notes.suite_note_ownership`; what
  remains future work is repeating that probe against the PUBLISHED app with two real
  Anvil accounts.
- Edits/deletes re-check ownership (`_own_or_raise`) and whitelist editable
  fields (EC-SEC-02/03) — covered by validation suites.
- Import validates every row against the schema before any write, inside a
  transaction (EC-SEC-06) — covered by the export/import suite.
