# DotPoint — Testing Evidence

Testing record for the Anvil build of the DotPoint Assessment Tracker
(VCE Software Development, Unit 4 Outcome 1). Three layers of testing were
used: **offline unit suites** against the real server modules, a
**measured evaluation-criteria run** for the parser, and **live end-to-end
journeys** in the running app. Defects found at each layer were fixed and
re-tested; the trail is recorded below.

## 1. Offline unit suites (14 suites, 974 assertions)

The server modules are pure Python over Anvil's table API, so they are tested
offline by stubbing `anvil.*` in `sys.modules` and loading `server_code/` as a
package (harness scripts kept outside the repo; a `run_all.py` runner
executes the set and exits non-zero if anything fails). All suites pass.

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
| exams (§13) | Timetable integrity (catalog subjects, in-period weekday dates, valid times), verified date spot-checks, English guarantee, days-remaining/urgency decoration, next-exam selection, month bucketing with string keys | 33 |
| auth/ownership | `_require_user`, the ownership guard, and a source sweep asserting **every** `@anvil.server.callable` resolves the user before touching data | 86 |
| constants-integrity (§14) | The hand-copied client mirrors still match `_constants.py`; **no client module contains a hex colour**; **every `role=` used in client code has a matching rule in the stylesheet**; both palettes define a colour token for every urgency band | 57 |

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

## 3c. Live journeys — UI overhaul (spec §14)

Performed in the published app as `claude.tester@dotpoint.dev`, in **both
themes**, after each push (the GitHub webhook auto-syncs Anvil):

| # | Checked | Result |
|---|---|---|
| 1 | Login screen + credential dialog | Wordmark / tagline / pitch on the role type scale; dialog rebuilt on `make_field`; affirmative button accent, Cancel secondary |
| 2 | Top bar | Active tab underlined on all five routes; Sign out pushed to the right edge; survives both themes |
| 3 | Dashboard hierarchy | parse bar → filters → next-exam strip → three panels; urgency moved from a `●` glyph to the card's left edge |
| 4 | **Calendar** | Real 7-column grid, evenly sized cells, today ring, per-day count badge, `▲` exam marker; all 42 cells present, Sunday no longer clipped |
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
