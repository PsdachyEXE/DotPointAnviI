# DotPoint — Testing Evidence

Testing record for the Anvil build of the DotPoint Assessment Tracker
(VCE Software Development, Unit 4 Outcome 1). Three layers of testing were
used: **offline unit suites** against the real server modules, a
**measured evaluation-criteria run** for the parser, and **live end-to-end
journeys** in the running app. Defects found at each layer were fixed and
re-tested; the trail is recorded below.

## 1. Offline unit suites (~200 assertions)

The server modules are pure Python over Anvil's table API, so they are tested
offline by stubbing `anvil.*` in `sys.modules` and loading `server_code/` as a
package (harness scripts kept outside the repo). All suites pass.

| Suite | Covers | Assertions |
|---|---|---|
| parser | Subject/type/weight/date matchers, Term-X-Week-Y resolution, title extraction, confidence tiers across 11 realistic sentences | 11 cases |
| server-validation | `_urgency_band` bands (FR21), AU date formatting (NFR08), month bounds, date coercion, `_validate_assessment_payload` happy path + 9 rejection paths | 28 |
| dashboard | Calendar bucketing, per-day highest-urgency colour, month parsing, string-keyed serialization rule | 12 |
| bulk | `create_bulk_assessments`: all-valid inserts, atomic rejection (no partial writes), empty input | 10 |
| notes | Note validation (title/tags/pin), pinned-first search ordering, case-insensitive tag + substring query filters, `toggle_pin`, delete-with-unlink | 17 |
| reminders | Reminder-window thresholds (incl. overdue exclusion), email subject/body content, dispatch + permanent dedup, notifications-off gate | 20 |
| export/import | JSON round-trip shape, schema rejection, collision renames | (suite) |
| fixes-regression | Regression locks for the four audited defects (below) | 11 |
| tz-compat | zoneinfo AND pytz backends: module imports, tz-aware now, junk-timezone rejection | 10 |
| subjects (§11) | Catalog integrity (aliases cover the catalog, no type-keyword collisions), `set_subjects` rules (maths required, English auto-add, dedupe, unknown/oversize rejection, whitelist lock), parser prioritisation + single-maths remap + fallback, new aliases | 29 |
| exams (§13) | Timetable integrity (catalog subjects, in-period weekday dates, valid times), verified date spot-checks, English guarantee, days-remaining/urgency decoration, next-exam selection, month bucketing with string keys | 22 |

## 2. Evaluation-criteria measurement (EC-EF-01 / EC-EF-02)

A 30-input test set was run against the real `nlp.py` with school terms
configured, covering: DD/MM (+year, +future-rolling), weekday names,
Term-X-Week-Y (+weekday offsets), tomorrow/today/"in N days", month-name dates,
all 11 subject aliases, and graceful no-subject/no-date degradation.

| Criterion | Target | Result |
|---|---|---|
| EC-EF-01 subject identification | ≥ 80% | **30/30 = 100%** |
| EC-EF-02 due-date extraction | ≥ 80% | **30/30 = 100%** |

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
   resolved to 31 Aug 2026 → "Created 3 assessment(s)" (atomic).
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
   atomically; export JSON (8 assessments / 2 notes / settings incl. locked
   subjects) → re-import renamed all 8 duplicates.

## 4. Defects found → fixed → re-tested

| # | Found by | Defect | Fix | Re-test |
|---|---|---|---|---|
| 1 | Offline parser run | Title extraction left orphan digits ("2 5") after stripping matched spans | Keep subject/type words; strip date/weight/week phrases + orphan numbers | parser suite |
| 2 | Offline parser run | `SAT term 2 week 3` read the type "SAT" as Saturday (weekday offset) | Weekday must follow `term N` in the week-phrase regex | parser suite |
| 3 | Adversarial audit | Bare `sat` in the free weekday matcher collided with the SAT type — fabricated/overrode due dates at HIGH confidence | Excluded `sat` abbreviation from that matcher ('saturday' still parses) | fixes suite + EC set |
| 4 | Adversarial audit | Wrong modal close event `x-close` (Anvil uses `x-close-alert`) — every dialog Save/Cancel was a silent no-op | Renamed at all 5 raise sites | live journey |
| 5 | Adversarial audit | Reminder email counted down from the threshold, not actual days remaining | `_build_email` takes `days_remaining`; "due today/in 1 day/in N days" | reminders suite |
| 6 | Adversarial audit | Bare DD/MM dates in the past were not rolled to next year (inconsistent with month-name path) | Roll forward when no explicit year given | fixes suite + EC set |
| 7 | Live run | `zoneinfo` missing on Anvil's Full-Python-3 runtime (pre-3.9) — signup crashed | `_get_tz()` compat: zoneinfo, else pytz | tz-compat suite (both paths) + live |
| 8 | Live run | Anvil cannot serialize int dict keys — dashboard crashed once a calendar day had assessments | Calendar day keys stringified server-side | dashboard suite + live |
| 9 | Live run | White-on-background calendar cell rendered invisible in the runtime theme | Urgency carried in coloured bold "● N" text | live |
| 10 | Live run | "in 1 days" pluralization on cards | day/days switch | live |
| 11 | Live run | Success toasts never auto-dismissed; the stack covered the action bar and swallowed clicks | `timeout=4` on all 31 notifications | live |
| 12 | Live sweep (task logs) | Reminder emails never sent: every `run_reminder_check` pass died on `ServiceNotAdded` — the Anvil **Email service** was never added to the app, so the 30-min task COMPLETED while `anvil.email.send` raised before any send/log; `reminder_logs` stayed empty and no inbox ever received mail | Email service added to `anvil.yaml` services | task logs after next scheduled run |
| 13 | Live sweep | Some success toasts still fail to auto-dismiss despite `timeout=4` and can stack over the top bar (each has a manual ×; error-style toasts dismiss fine) | OPEN — needs Anvil Notification-stacking investigation | — |

Config/platform issues resolved en route: Users service missing
`user_table` binding; unavailable `python310-full` base-image pin; database
schema migration (`users` table server permission); GitHub token re-auth.

## 5. Security spot-checks

- Every `@anvil.server.callable` resolves `_require_user()` first; all queries
  scoped by the `user` link column (NFR03 / EC-SEC-01 pattern, uniform across
  modules — verified by review; a second-account probe is future work).
- Edits/deletes re-check ownership (`_own_or_raise`) and whitelist editable
  fields (EC-SEC-02/03) — covered by validation suites.
- Import validates every row against the schema before any write, inside a
  transaction (EC-SEC-06) — covered by the export/import suite.
