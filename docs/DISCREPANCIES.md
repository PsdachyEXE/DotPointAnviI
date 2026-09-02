# DotPoint — Design vs Code Discrepancy Log

Comparing the existing TypeScript/React codebase (per `INVENTORY.md`) against the design intent of `SoftwareDesign_FinalDraft2026.docx` (the Anvil/Python rebuild target). Every row is a place where the two disagree on behaviour, data shape, or design rule — not naming or refactoring differences.

> **Read this first.** The numbered table below was written *before* the Anvil port, and
> its Recommendation column records what the port was advised to do — not what it did.
> Where the shipped app departs from that advice it does so deliberately; those departures
> are recorded in **"Decisions taken in the Anvil build"** at the end of this document.
> Consult that section for the behaviour of the running software.

Severity scale:
- **BLOCKER** — affects core behaviour, must be resolved before the Anvil port
- **NOTABLE** — design drift; needs a decision for the port
- **COSMETIC** — naming, wording, minor

Recommendation column states which side should govern the rebuild — and why, in one sentence.

---

## Discrepancies

| # | Component/Feature | What the doc says | What the code does | Severity | Recommendation |
|---|---|---|---|---|---|
| 1 | `Assessment.type` enum | `{SAC, SAT, Test, Assignment, Practical, Other}` — six canonical values keyed off `TYPE_KEYWORDS` and used in the manual-entry dropdown (§2.3 C, §4.2.1) | `'project' \| 'exam' \| 'sac' \| 'sat' \| 'homework' \| 'other'` — six values but a different set: `project`/`exam`/`homework` exist in code but not the doc, while `Test`/`Assignment`/`Practical` exist in the doc but not the code | BLOCKER | Doc governs — `TYPE_KEYWORDS`, the manual dropdown, every existing row's `type` value, and the data dictionary all align with the doc's enum; a one-shot migration of existing rows is cheaper than rewriting the parser table. |
| 2 | `Assessment.status` enum | `{Not started, In progress, Complete}` — three states explicitly chosen so urgency-colour and filter behaviour stay meaningful (§2.3 G, §4.2.1) | `'not-started' \| 'in-progress' \| 'submitted' \| 'completed'` — four states; `'submitted'` is an extra state the doc does not anticipate | BLOCKER | Doc governs — the reminder dispatcher filters on `status != 'Complete'`, the show-completed toggle is binary, and EC-UX-05 specifies a single-dropdown status change; the fourth `'submitted'` state has no role in either path. |
| 3 | `Assessment.source_text` field | `str \| None`. Raw input string from the NLP strip or bulk-import line. Set by parser; `None` on manual entries. Used as the audit trail for parser-produced rows (§3.2, §4.2.1) | Field does not exist on the `Assessment` interface (`src/types/database.ts:11-26`). Parser audit trail is impossible to reconstruct from current data | BLOCKER | Doc governs — without `source_text` the parser preview surface, the FR17 audit trail, and the "show original sentence" element of the preview modal (§2.2 D) cannot exist. |
| 4 | `Assessment.confidence` field | `'HIGH' \| 'MEDIUM' \| 'LOW' \| None`. Set by parser at `parse_text`; `None` on manual-entry rows. FR17 — surfaced in preview, stored for audit. Preserved across edits (§3.2, §4.2.1, §3.3.10) | Field does not exist on the `Assessment` interface. Confidence is computed transiently in `SmartAssessmentInput`/`BulkAssessmentInput` and discarded on save | BLOCKER | Doc governs — bulk-import auto-commit policy, parser-audit preservation, and the confidence pill on the bulk dialog's results table (§2.4 C) all require this to be persisted. |
| 5 | `UserSettings.timezone` field | IANA timezone string, default `'Australia/Melbourne'`. Required for `today = datetime.now(tz).date()` math in both the reminder dispatcher and `get_dashboard_data` (server is UTC) (§3.3.2, §3.3.3, §4.2.3, §6 contingency on server-vs-user timezone) | No `timezone` field on `AppSettings` (`src/types/database.ts:36-49`). Date math runs against the browser/Electron local clock with no explicit IANA awareness | BLOCKER | Doc governs — without a per-user timezone the server-side scheduled task cannot compute "today" correctly, and a UTC server would fire reminders against the wrong calendar day for AEST users. |
| 6 | Notes body is plain text | "Plain text only; no rich-text per SRS constraint." Field name is `body` (§4.2.2). No markdown rendering anywhere | `Note.content` is described as "Markdown" in code comments; rendered through `react-markdown` + `remark-gfm` in `NoteCard.tsx`; `NoteEditor.tsx` has a `showPreview` toggle for live markdown preview | BLOCKER | Doc governs — the SRS constraint is explicit, and removing rich-text simplifies the Anvil-side editor, search (substring against body), and export round-trip (FR18/FR19). Existing markdown notes survive as raw text without loss. |
| 7 | Default `reminder_days` | `[7, 2]` — two reminder windows, sourced from survey Q7 (§4.2.3, §3.2 UserSettings example) | Example in inventory data model shows `[7, 3, 1]` (line 290 of `INVENTORY.md`); the actual default lives in code defaults rather than the doc-specified pair | NOTABLE | Doc governs — Q7 of the data collection is the documented evidence, and the per-assessment override pattern (Follow-up Q11) means a user can still pick `[7, 3, 1]` for high-stakes items if they want. |
| 8 | Reminder day options offered to the user | Fixed pill set `14 / 7 / 3 / 2 / 1` on both the manual form (§2.3 I) and the Settings defaults (§2.6 C) | `ReminderManager` accepts arbitrary integer input via a `newReminderDay: string` text field (line 168), so any day count is possible | NOTABLE | Doc governs — a fixed pill set removes a class of bad input (e.g. negative days, zero, 365) and matches the parser's expectations; the Anvil port should ship the fixed set. |
| 9 | `ReminderLog` dedup model | Dedup key is `(assessment_id, user_id, reminder_type)` where `reminder_type` is a string `'{N}-day'`. `sent_date` is stored but **not** in the key. Allows missed-window catch-up without re-firing. Row written only on email-send success (§3.3.2, §4.2.4) | `ReminderLog` has `reminderDay: number` (with sentinel values: `0` = due today, `-1` = overdue) and `firedAt: Date`; dedup logic in `src/lib/reminders.ts` is not fully visible from inventory but the schema admits date-based and sentinel-keyed semantics. Has a `dismissed: boolean` field with no doc equivalent | BLOCKER | Doc governs — the dedup rules drive correctness of EC-EF-05 (no duplicates) and the missed-window recovery in the contingency table; the current schema cannot represent the doc's permanent dedup key without a migration. |
| 10 | `ReminderLog.dismissed` field | No such field; reminders are events, not user-actionable items, because the channel is email (§4.2.4) | `ReminderLog.dismissed: boolean` exists; presumably surfaced through some UI for in-app browser notifications | NOTABLE | Doc governs — email reminders are not dismissible, so the field becomes dead weight in the Anvil port; drop it. |
| 11 | `UserSettings.last_active` field | `datetime`, written on every authenticated server call. Used for diagnostic dashboards (§4.2.3) | No equivalent. The closest thing is Firebase's internal session tracking, which is not user-visible | NOTABLE | Doc governs — easy to add server-side in an Anvil `@anvil.server.callable` decorator; cheap operational signal. |
| 12 | Notification channel | Email only, via Anvil's scheduled task + email relay. Explicit non-goal: "No push notifications" (§6.1) | Three channels: browser Notification API (`src/lib/notifications.ts`), Electron native notifications (`electron/main.ts:60-70`), Chrome extension notifications (`extension/src/background/service-worker.ts:128-134`). No email channel at all | BLOCKER | Doc governs — the dispatcher in the Anvil rebuild has no client-side context to fire a browser notification from anyway (it runs as a background task on the server). The behavioural model is fundamentally email-driven. |
| 13 | Authentication provider | Anvil's built-in Users service with email + password. No password reset link on the school network (§2.7) | Firebase Auth with Google provider only. `signInWithPopup`/`signInWithCredential`. No email-password path exists | NOTABLE | Doc governs — the rebuild platform is Anvil, and Anvil Users delivers the session lifecycle and login form (EC-SEC-04, EC-SEC-05) the doc explicitly relies on. |
| 14 | "Continue without account" / local mode | Not in scope. Single-user authenticated app. Every server callable's first action is `anvil.users.get_user()` and raises `PermissionError` if `None` (every pseudocode block in §3.3) | `localStorage.dotpoint_local_mode = 'true'` bypasses the auth gate entirely; the app runs fully offline against Dexie (`src/App.tsx:76`, `LoginPage.tsx:9`) | NOTABLE | Doc governs — no equivalent surface exists in the Anvil model and the data-collection scope is single authenticated user; drop the local mode in the port. |
| 15 | Theme | "Theme selector deferred to Stage 6 stretch (SRS scope: 'if-possible'). The header is rendered so the section is reserved on the page, but no controls ship in MVP." (§2.6 A) | Full dark/light theme support: `AppSettings.theme: 'dark' \| 'light'`, `useTheme` hook, theme switcher in Settings | NOTABLE | Code governs — the feature exists, works, and removing it would be a user-visible regression for the existing offline users; keep it but ship without it on a v1 release if scope pressure demands. Document the deviation. |
| 16 | Subject input | Subject must be one of `SUBJECT_ALIASES.values()` (canonical, post-alias). Manual form is a dropdown sourced from those canonical values to avoid free-text entries that would break the alias lookup (§2.3 B, §4.2.1) | `AssessmentForm` has both a `subject` and a `customSubject` state variable (line 119), allowing free-text entry that bypasses any alias table | NOTABLE | Doc governs — free-text subjects defeat the parser, defeat the colour mapping per subject, and defeat any future per-subject aggregation; enforce the canonical-only path in the port. |
| 17 | Single-row parser preview UX | Dedicated **confidence-badged modal** before any DB write. Shows a HIGH/MEDIUM/LOW pill, per-field provenance ("Due date: 23 May 2026 (from 'Friday week 5')"), editable inputs, the original sentence in a greyed box, and Save/Cancel. FR17 is "the single most important error-prevention surface in the app." (§2.2) | `SmartAssessmentInput` debounces and auto-applies parsed fields into the surrounding `AssessmentForm`. The form itself acts as the preview surface; no confidence pill, no per-field provenance, no original-sentence display | NOTABLE | Doc governs — FR17's "preview before commit" guarantee is technically satisfied (the user must click Save), but the confidence badge and provenance are explicit EC-UX criteria (EC-UX-04, EC-UX-07) and surface them in the port. |
| 18 | Bulk import commit policy | HIGH and MEDIUM rows **auto-commit** inside one per-batch Anvil Transaction; LOW rows are **auto-rejected** with the missing-fields reason rendered inline. No per-row confirmation. All-or-nothing transaction (§3.3.8) | `BulkAssessmentInput` shows a preview with `selectedAssessments: Set<number>` — the user manually selects which rows to commit; LOW rows are not auto-rejected by the workflow | NOTABLE | Doc governs — manual selection per row defeats the time-saving purpose of bulk import (Data Collection Q5 was the source); the HIGH/MEDIUM threshold is the safety net in place of per-row confirmation. |
| 19 | Calendar views | Month view only. Cells coloured by highest-urgency item due that day; today is ringed; legend below (§2.1 D, §2.1 E, §3.3.3) | Three views: `MonthView`, `WeekView`, `YearView`, each as a separate component | NOTABLE | Doc governs for the MVP — Week and Year views are not in the doc's scope, but they're working features today; suggest shipping month-only for the Anvil v1 to match the design contract, then adding Week/Year as a Stage 6 stretch if time permits. |
| 20 | Sort options | `ALLOWED_SORT_KEYS = {'due_date', 'weight', 'subject'}` — three keys, enforced server-side (§3.3.3 `get_dashboard_data`) | `sortBy: 'dueDate' \| 'subject' \| 'status' \| 'createdAt'` (Zustand store) — `'status'` and `'createdAt'` are not in the doc's whitelist; `'weight'` is missing from code | NOTABLE | Doc governs — the whitelist is part of the NFR04 input-validation contract and `'weight'` is a meaningful sort for high-stakes-first workflows; drop `'status'` and `'createdAt'`, add `'weight'`. |
| 21 | Note autosave debounce | 300ms ("Server clock on every save (debounced 300ms)") (§4.2.2 `updated_at`) | 500ms (`NoteEditor.tsx` line 44 of inventory: "auto-save debounced 500ms") | COSMETIC | Doc governs — 300ms is a UX-tuned figure; trivial to honour in the port. |
| 22 | Notes — `linked_note_ids` direction | §2.5 says it's an Anvil `simpleObject` list on the **Note** record; §4.2.1 lists it on the **Assessment** record. The doc contradicts itself | Code stores `linkedNoteIds` on the **Assessment** side only (`src/types/database.ts`) | NOTABLE | Code governs — assessment-side storage is more useful for the dashboard's "show notes linked to this assessment" surface; resolve the doc's internal inconsistency by following the §4.2.1 data dictionary, which is the authoritative artefact. |
| 23 | Reminder log cleanup | No log-deletion behaviour described. Logs are permanent; the dedup key relies on permanence for "never re-fire" semantics (§3.3.2) | `cleanupOldReminderLogs()` runs on initial load and deletes logs older than 30 days (`useReminders.ts:28`) | NOTABLE | Doc governs — deleting logs older than 30 days would allow an old `(assessment, user, reminder_type)` to re-fire on the next dispatcher run, violating EC-EF-05. Drop the cleanup. |
| 24 | Dashboard data delivery | Single `get_dashboard_data(filters)` server call returns one composite payload (`assessments`, `calendar_grid`, `upcoming`, `applied_filters`, `generated_at`) for NFR01's <2-second budget (§3.3.3, EC-EFF-05) | Each page (`/`, `/assessments`, `/calendar`, `/settings`) is a separate route hitting separate Dexie live queries; no composite payload exists | NOTABLE | Doc governs — the composite payload is the design's load-budget mitigation, and the Anvil port collapses the four routes into one three-panel dashboard anyway (§2.1). The "separate routes" pattern dies with the rewrite. |
| 25 | Field-whitelist on update | `EDITABLE_FIELDS` set server-side; unknown keys silently dropped; `confidence` and `source_text` deliberately excluded so the parser audit trail survives edits (§3.3.4, EC-SEC-03) | `DataService.updateAssessment(id, data: Partial<Assessment>)` accepts any subset of the Assessment interface; no whitelist enforcement; relies on TypeScript types client-side, which can be bypassed | NOTABLE | Doc governs — server-side whitelisting is an EC-SEC-03 criterion and is cheap to add in Anvil; the audit-preservation choice (excluding `confidence`/`source_text`) is a behavioural rule the code does not implement. |
| 26 | JSON export / JSON import | `export_user_data` and `import_user_data` are deferred-surface callables (FR18/FR19); import validates each row against `ASSESSMENT_SCHEMA`/`NOTE_SCHEMA` and runs inside one Anvil Transaction; conflicts on `assessment_id` get a numeric-suffix rename (§3.3.7, EC-EF-08, EC-SEC-06) | No equivalent in `DataService`. `dataMigration.ts` handles Dexie-to-Firestore migration only — not user-driven JSON portability | NOTABLE | Doc governs — these are first-class evaluation criteria (EC-EF-08 round-trip, EC-SEC-06 schema validation) and have no current implementation to port. |
| 27 | Multi-platform builds (Electron, Chrome Extension) | Web-only. The doc explicitly scopes to "an Anvil (Python) web application" and "No mobile build" (§1, §6.1) | Three build targets: Vite SPA, Electron desktop (Win/macOS/Linux), Chrome Extension (MV3) | NOTABLE | Doc governs — the Anvil platform doesn't support extension or Electron packaging anyway; the rebuild is a clean break, and the extension's Firestore-direct write pattern (`QuickAddAssessment`, `QuickAddNote`) has no Anvil equivalent. |
| 28 | Command palette (Cmd+K) | Not described. The doc's navigation model is the persistent top bar plus the three-panel dashboard (§2.1 F) | `CommandPalette` component opens on Cmd+K, exposes navigation, create, and open-item commands (`src/components/ui/CommandPalette.tsx`) | NOTABLE | Code governs (for future Stage 6) — this is a power-user feature that exists in the code but is out of scope for the Anvil v1; drop it in the port and document as a stretch goal. |

---

## What this log deliberately does NOT include

Per the rules, the following were considered and excluded:
- `snake_case` vs `camelCase` field naming (`assessment_id` vs `id`, `user_id` vs `userId`, `term_info` vs `termInfo`, `linked_note_ids` vs `linkedNoteIds`, `is_pinned` vs `isPinned`, etc.) — naming-only, doesn't affect behaviour.
- `body` (doc) vs `content` (code) for the note text field — naming-only.
- ID format (`'a_' + uuid4[:8]` vs `crypto.randomUUID()`) — both produce unique strings; format change is part of the platform translation.
- The doc's code snippets vs the codebase's actual TypeScript — the doc snippets are forward-looking Python pseudocode for the Anvil rebuild, not commentary on the existing code, so the "doc snippet vs code mismatch" case doesn't apply here.
- Code-level ambiguities in `INVENTORY.md` §10 (`auth = getAuth(app)` bug, duplicate `setSelectedTag`, `subjectColors` exported twice, etc.) — the design doc does not address these and they are code-hygiene issues that get resolved by the rewrite anyway.

---

## Severity tally

| Severity | Count | Row #s |
|---|---|---|
| **BLOCKER** | 8 | 1, 2, 3, 4, 5, 6, 9, 12 |
| **NOTABLE** | 19 | 7, 8, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20, 22, 23, 24, 25, 26, 27, 28 |
| **COSMETIC** | 1 | 21 |
| **Total** | 28 | |

---

## Decisions taken in the Anvil build (added 2026-09-02)

Everything above compares the **original TypeScript/React app** against the design
document, and was written *before* the Anvil port. It is a record of what the port had to
decide, not of what it decided. Several rows recommend "Doc governs" where the shipped
Anvil app in fact does something else — deliberately, and for reasons recorded below.

This section closes that gap, so the register and the running code no longer contradict
each other.

| Ref | Row(s) above | What the Anvil build actually does | Why |
|---|---|---|---|
| A-1 | 1 | `type` = `sac, sat, exam, project, homework, other` — **lowercase, and the code's value set**, not the doc's `{SAC, SAT, Test, Assignment, Practical, Other}`. Defined once as `_constants.VALID_TYPES`. | The recommendation to let the doc govern assumed a one-shot migration was cheap. By the time the port was live it no longer was: the values are persisted in every stored row, mirrored in both client forms, keyed in `TYPE_KEYWORDS` (which drives the parser), and written into every export file a student may still hold. Changing them would invalidate all of that to gain a cosmetic match. **The design document's §4.2.1 should be corrected to the lowercase set**, not the code. |
| A-2 | 2 | `status` = `not_started, in_progress, completed` — three states, lowercase. The fourth `submitted` state from the old app was dropped, which is what the doc asked for. | The doc's *intent* (three states, binary show-completed toggle, single-dropdown change per EC-UX-05) is honoured exactly; only the casing differs, and for the same reason as A-1. |
| A-3 | 3, 4 | `source_text` and `confidence` both exist as nullable text columns and are **excluded from `EDITABLE_FIELDS_ASSESSMENT`**. | Doc governs, as recommended. Excluding them from the whitelist is what makes the FR17 audit trail survive an edit — the student can correct a parsed field without erasing the record of how it was parsed. |
| A-4 | 5 | `user_settings.timezone` exists, defaults to `Australia/Melbourne`, and is validated against the real IANA database on write and guarded on read. | Doc governs, as recommended. The read guard was added later: a stored value the tz database cannot resolve used to raise inside `_user_now()`, which every screen calls. |
| A-5 | 6 | Note content is **plain text**, and the editor says so. The column is named `content`, not the doc's `body`. | The plain-text decision stands, but the *reason* recorded above is wrong and has been corrected in the code comments: SRS FR10 actually describes markdown notes. Plain text is a **build decision** — it keeps the editor, the substring search and the export round-trip simple — not an SRS constraint. The column name `content` was chosen at schema-creation time and renaming a live Anvil column is a manual console migration for no behavioural gain. |
| A-6 | 8 | The reminder-day pill set is fixed at `14 / 7 / 3 / 2 / 1`, and arbitrary integers are additionally bounded server-side to `1..365` with at most six per assessment. | Doc governs, as recommended, plus a server bound the doc did not ask for. The bound was not optional: an unbounded value such as `999999` satisfied every other check and made the assessment permanently "due soon", emailing the student about it on the first scheduler tick. |
| A-7 | — | **`create_bulk_assessments` commits the valid lines and reports the rest.** It was previously all-or-nothing. | This is a **behaviour change made to match the project's own SRS**. FR02 reads: "Lines that fail validation are reported back to the user with the line number and the reason. Valid lines still commit so a single bad line does not block the rest." The shipped code did the opposite. The design document's §3.3.6 pseudocode also shows per-line commit with a `continue`, so both source documents agreed and only the code disagreed. Locked by `tests/test_assessments.suite_bulk_partial_commit`. |
| A-8 | — | `user_settings.school_terms` accepts **both** the doc's `start`/`end` key names and the code's `start_date`/`end_date`, normalising to the latter on write. | SAT 5 §4.2.3 documents `start`/`end`; the validator enforced `start_date`/`end_date`. A hand-authored or doc-conformant file would have been rejected with a message quoting a key name the reader could not find in the design document. Accepting both costs one normalisation step and removes the contradiction. |
| A-9 | — | Data-table column names diverge from the data dictionary in three places: `notes.content` (doc: `body`), `user_settings.default_reminder_days` (doc: `reminder_days`), and `user_settings.school_year` (not in the doc at all). Rows are identified by Anvil's own row ids and a `user` link column rather than the doc's `assessment_id` / `user_id` text keys. | Anvil supplies row identity and referential links natively, so re-implementing string keys would have added columns that duplicate what the platform already guarantees. The remaining three name differences are cosmetic and were not worth a live column migration. **Recorded here so the difference is a documented decision rather than an unexplained inconsistency.** |

### Corrections APPLIED to the design document (2 September 2026)

The five items below were previously listed here as changes the design document needed.
They have now been **made**, in
`SAT/SAT 5/SoftwareDesign FinalDraft2026.docx`, and verified: a script compares every
field name in the four §4.2 dictionaries against `anvil.yaml`'s `db_schema` and reports
all four as matching (assessments 16/16, notes 7/7, user_settings 8/8,
reminder_logs 4/4).

1. §4.2.1 — `type` and `status` corrected to the lowercase value sets (A-1, A-2).
2. §4.2.2 — `body` corrected to `content`, with the plain-text decision re-attributed
   honestly as a build decision rather than an SRS constraint (A-5).
3. §4.2.3 — `reminder_days` corrected to `default_reminder_days`; `theme`,
   `school_year` and `subjects` documented for the first time (A-9).
4. §4.2.1 / §4.2.2 / §4.2.4 — the designed `assessment_id` / `note_id` / `log_id` /
   `user_id` string keys replaced by Anvil's own row ids and the `user` link column,
   which is what was actually built (A-9).
5. §4.2.4 — `log_id` and `sent_at` removed (they were never created); `reminder_type`
   corrected to the underscore spelling `'7_day'` that is actually stored.

**The change is declared, not slipped in.** A dated revision note now sits directly
under the §4.2 heading explaining that the dictionaries were corrected against the
software as built, sorting the changes into the three kinds above, and pointing back to
this file for the reasoning. Individual corrected rows carry a `CORRECTED:` or `ADDED:`
prefix in their description so a reader can see exactly what moved.

The original is preserved alongside it as
`SoftwareDesign FinalDraft2026.BACKUP-2026-09-02.docx`.

