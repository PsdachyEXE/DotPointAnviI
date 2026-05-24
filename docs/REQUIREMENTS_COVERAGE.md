# DotPoint Requirements Coverage Matrix

Maps every requirement in `SRS2026.docx` to its implementation status in the existing Vite/React/TypeScript source at `C:\Coding\DotPoint`, as catalogued in `INVENTORY_annotated.md`.

Status legend:
- **IMPLEMENTED** — fully present in code, behaves as required
- **PARTIAL** — present but missing pieces (see Notes)
- **ABSENT** — required but not in code
- **DEFERRED** — explicitly marked as stretch or out-of-scope in the SRS
- **UNCLEAR** — requirement ambiguous; needs clarification before porting
- **VERIFY-AT-RUNTIME** — used for NFRs whose compliance cannot be judged from static inventory alone

IDs prefixed `FR-S` and `OOS-` are assigned by this matrix; the SRS lists stretch and out-of-scope items as bullets without numbering. All other IDs (`FR01–FR21`, `NFR01–NFR08`) are taken verbatim from the SRS.

---

## Functional Requirements

### Core (numbered in SRS)

| Req ID | Requirement (one-line paraphrase) | Status | Implementation reference | Notes |
|---|---|---|---|---|
| FR01 | Parse a single-line NL assessment string into a structured record. | IMPLEMENTED | Inventory §3: `SmartAssessmentInput`; §1: `chrono-node`; `src/lib/parser/` | Debounced parse on input; confidence path shared with FR17. |
| FR02 | Bulk import multiple assessments from a multi-line text block. | IMPLEMENTED | Inventory §3: `BulkAssessmentInput`; §5: `createBulkAssessments` | Per-line errors surfaced; current commit policy is user-selects-indices rather than the SRS's auto-commit HIGH/MEDIUM + auto-reject LOW. |
| FR03 | Create an assessment via a manual form with required-field validation. | IMPLEMENTED | Inventory §3: `AssessmentForm` + `form/` sub-components | All required fields (title, subject, type, weight, due date, status, reminder_days) are present. Subject sourced from a canonical list per intent. |
| FR04 | Edit an existing assessment with server-side whitelist + ownership check. | PARTIAL | Inventory §5: `updateAssessment` | Update path exists via `DataService`, but architecture is fully client-side — there is no server-side `EDITABLE_FIELDS` whitelist and no server-side ownership re-check. Anvil port must add both. |
| FR05 | Delete an assessment with confirmation and ownership check. | PARTIAL | Inventory §3: `AssessmentCard` (`showDeleteConfirm`); §5: `deleteAssessment` | Confirmation dialog present. No server-side ownership re-check. Inventory §4 notes related reminder logs are deleted from Dexie but not from Firestore. |
| FR06 | Filter list by status/subject/type with AND; default hides completed. | IMPLEMENTED | Inventory §3: `AssessmentList`, `AssessmentFilters`; §8: `useAssessmentsStore` | `viewMode: 'active' \| 'completed'` toggle present. Filter state persists in Zustand (in-memory, session only) rather than in `user_settings` as SRS specifies. |
| FR07 | Sort by due date by default; show "no assessments match" on empty. | IMPLEMENTED | Inventory §3: `AssessmentList`; §8: `sortBy` defaults to `'dueDate'` | Inventory §8 notes the code also offers `'status'` and `'createdAt'` sort keys, which are outside the SRS's `ALLOWED_SORT_KEYS` (`due_date`, `weight`, `subject`). |
| FR08 | Display the current month as a 7-column calendar grid. | IMPLEMENTED | Inventory §3: `AssessmentCalendar`, `MonthView` | Code also ships `WeekView` and `YearView` beyond the SRS's monthly requirement (tracked below as extras). |
| FR09 | Compute and display `days_remaining` for every assessment. | PARTIAL | Inventory §3: `AssessmentCard` (intent) | Computation runs client-side; SRS specifies it server-side, refreshed on every dashboard load. Behaviour is equivalent for a single client but does not match the Anvil dispatch model. |
| FR10 | Create, edit, delete, and pin markdown notes (with tags). | IMPLEMENTED | Inventory §3: `NoteList`, `NoteCard`, `NoteEditor`; §5: `createNote` / `updateNote` / `deleteNote`; §4: `Note` model | Markdown rendered via `react-markdown` (inventory §1). Pin toggle via `noteOperations.togglePin()`. |
| FR11 | Search notes by free-text query + filter by tag with AND logic. | IMPLEMENTED | Inventory §3: `NoteList` (intent) | Case-insensitive substring against title + body, ANDed with tag-filter pills. |
| FR12 | Cross-reference notes to an assessment via `linked_note_ids`. | IMPLEMENTED | Inventory §3: `LinkedNotesManager`; §4: `Assessment.linkedNoteIds` | Code stores the list on the Assessment side (SRS §4.2.1 matches; SRS §2.5 places it on the Note side — inventory flags this as internal SRS inconsistency). The MVP-only "set/clear programmatically" framing is exceeded by the existing `LinkedNotesManager` UI. |
| FR13 | Run an automated reminder check every 30 minutes. | PARTIAL | Inventory §7: `useReminders.ts` `setInterval` (30 min); extension `chrome.alarms` | Cadence matches but runs in the browser (or extension service worker), not as an Anvil Scheduled Task. The main-app checker only fires while the SPA is open. |
| FR14 | Send a reminder email at threshold with `(assessment, user, sent_date, reminder_type)` dedup. | PARTIAL | Inventory §3: `ReminderLog` model; §7: reminder checker; §9: Browser / Electron / Chrome notifications | Code dispatches browser, Electron, and Chrome notifications — no email channel. Dedup uses `(assessmentId, reminderDay)` with sentinel ints for overdue/today, not the SRS's string `reminder_type` + `sent_date` model. `cleanupOldReminderLogs` deletes logs >30 days; SRS retains them. |
| FR15 | Convert "Term X, Week Y" expressions to a calendar date using `school_terms`. | IMPLEMENTED | Inventory §3: `SchoolTermsConfig` (intent); §4: `Assessment.termInfo` | Storage (`SchoolTermConfig` on settings, `termInfo` audit string on Assessment) and resolver wiring referenced in inventory intent. The "LOW confidence when terms unconfigured" behaviour should be verified before porting. |
| FR16 | Map subject aliases to canonical subject names via `SUBJECT_ALIASES`. | IMPLEMENTED | Inventory §3: `AssessmentForm` (intent) | Inventory intent confirms manual form sources subject from `SUBJECT_ALIASES` canonical values; alias coverage (≥13 aliases per SRS) should be confirmed in parser source. |
| FR17 | Score parser output HIGH / MEDIUM / LOW with preview before commit. | PARTIAL | Inventory §3: `SmartAssessmentInput`, `MultiAssessmentPreview` | `SmartAssessmentInput` auto-applies the parse into the form (not a dedicated preview modal with confidence badge + per-field provenance + Save/Cancel as SRS specifies). `MultiAssessmentPreview` covers the multi-assessment case but not the single-line case the SRS describes. |
| FR18 | Export all user data (assessments, notes, settings) as a downloadable JSON file. | ABSENT | — | No export function found in inventory. `src/lib/dataMigration.ts` exists but is a one-time local→cloud migration, not user-facing export. |
| FR19 | Import a previously exported JSON file with schema validation + collision suffixing. | ABSENT | — | No import function found in inventory. |
| FR20 | Authenticate via Anvil Users service (email + password). | PARTIAL | Inventory §6: Auth Flow | Authentication exists but uses Firebase Auth with Google OAuth — different provider, different credential model. The "Continue without account" local-mode bypass (Inventory §6, §8) has no counterpart in the SRS, which assumes mandatory auth. |
| FR21 | Apply colour-coded urgency (overdue / today–3d / 7d / beyond). | IMPLEMENTED | Inventory §3: `AssessmentCard`, `MonthView` (intent); §10: `urgencyColors` | Colour map applied to cards and to calendar cells (highest-urgency wins per day). Inventory §10 flags `urgencyColors` is duplicated across `src/lib/utils.ts` and `src/lib/colors.ts` with different contents — exact threshold matching to the SRS bands needs verification. |

### Stretch (assigned IDs; SRS lists these unnumbered under "Stretch")

| Req ID | Requirement (one-line paraphrase) | Status | Implementation reference | Notes |
|---|---|---|---|---|
| FR-S01 | Material 3 dark/light theme toggle implemented in CSS. | DEFERRED | Inventory §1: Tailwind; `useTheme` hook (§10 ambiguity #6) | Code ships a light/dark theme already, written in Tailwind not Material 3. SRS classifies as "may be cut". Inventory §10 ambiguity #6 notes `useTheme.setTheme` bypasses `dataService` and may not sync to cloud. |
| FR-S02 | Per-subject colour-coding on assessment cards and calendar cells. | DEFERRED | Inventory §10 ambiguity #8: `subjectColors` in `src/lib/colors.ts` | Already implemented in code (canonical version covers Biology, Chemistry, Physics, Economics, Psychology). Duplicate `subjectColors` export in `src/lib/utils.ts` (inventory §10) is a cleanup item. |
| FR-S03 | UI for selecting and viewing notes linked to an assessment. | DEFERRED | Inventory §3: `LinkedNotesManager` | UI already exists (used inside `AssessmentForm`). SRS treats UI as stretch while the MVP requires only programmatic set/clear; code exceeds MVP here. |
| FR-S04 | Advanced NLP date parsing beyond keywords ("in 3 days", "next week Tuesday"). | DEFERRED | Inventory §1: `chrono-node` | `chrono-node` 2.7 supports these expressions; whether they're wired into the parser pipeline (vs. only the keyword path) needs source-level confirmation before porting. |
| FR-S05 | Markdown editor toolbar (bold, italic, lists). | DEFERRED | Inventory §3: `NoteEditor` (`showPreview` state) | Editor offers a markdown preview toggle but no formatting toolbar; raw markdown only. |

### Out-of-scope (assigned IDs; SRS lists these unnumbered under "Outside scope")

| Req ID | Requirement (one-line paraphrase) | Status | Implementation reference | Notes |
|---|---|---|---|---|
| OOS-01 | Mobile app wrapper or PWA. | DEFERRED | — | SRS: "Web-only. No offline mode." Current code has Electron desktop + Chrome extension build targets (not a mobile or PWA wrapper). |
| OOS-02 | Grade prediction from completed assessment scores. | DEFERRED | — | Not in code. |
| OOS-03 | Teacher or parent dashboard with read-only access. | DEFERRED | — | SRS notes Will explicitly does not want this (interview, 14 Feb). |
| OOS-04 | LMS (Canvas) integration. | DEFERRED | — | SRS: Canvas does not expose a student-accessible API. |
| OOS-05 | Collaboration features (shared assessments, note sharing). | DEFERRED | — | Not in code. |
| OOS-06 | Recurring assessments. | DEFERRED | — | Each assessment record is a single instance in the current schema. |
| OOS-07 | Multi-language support. | DEFERRED | — | English only in UI strings. |

---

## Non-Functional Requirements

| Req ID | Requirement (one-line paraphrase) | Status | Implementation reference | Notes |
|---|---|---|---|---|
| NFR01 | Dashboard initial render under 2 s for ~100 assessments + 50 notes on Will's laptop. | VERIFY-AT-RUNTIME | — | Cannot be judged from inventory. Evidence: Lighthouse / Chrome DevTools profile on the target HP laptop with a 100-assessment + 50-note seeded dataset, measured from route mount to last paint. |
| NFR02 | No reminder delivered more than once per `(assessment, user, threshold, day)`. | PARTIAL | Inventory §3: `ReminderLog` model; §7: reminder checker | Dedup exists but uses `(assessmentId, reminderDay)` with sentinel-int reminderDays (overdue = -1, today = 0). SRS specifies a `reminder_type` string key plus an explicit `sent_date` column. Behaviourally similar but the schema differs and must be re-modelled for the Anvil port. |
| NFR03 | Every Data Table query scoped to `current_user`. | VERIFY-AT-RUNTIME | Inventory §5: Firestore paths `users/{userId}/...` | Path-scoped Firestore writes are present, but cross-user read isolation depends on Firestore security rules that are not part of the inventory. Evidence: open `firestore.rules`, confirm a `request.auth.uid == userId` check on every collection, and exercise with a second test account. Anvil port replaces this with server-side `row.user_id == current_user.user_id` checks (per NFR04 in inventory §5 intent). |
| NFR04 | Parser usable on inputs of ~4 informational tokens (subject + due_date + type detected). | VERIFY-AT-RUNTIME | Inventory §3: `SmartAssessmentInput`; `src/lib/parser/` | Evidence: run the parser against a 30-input test set drawn from Will's actual phrasing (per SRS) and measure the rate of HIGH-confidence outputs. |
| NFR05 | Runs on Chrome 120+ on Windows 11, no install, no admin rights. | VERIFY-AT-RUNTIME | Inventory §1: Vite SPA build target | Web SPA inherently satisfies "no install / no admin". Evidence: open the deployed URL in Chrome 120 on the school HP laptop and exercise core flows; no Electron/extension install is required for the web path. |
| NFR06 | Server modules separated by concern (`nlp.py`, `assessments.py`, `notes.py`, `reminders.py`, `dashboard.py`). | ABSENT | — | Architecture is fully client-side; there are no Python server modules. Inventory §5: "There are no server-side API routes." The Anvil port creates these from scratch. |
| NFR07 | Reaches backend via outbound HTTPS to `*.anvil.app` on TCP 443. | ABSENT | Inventory §1: Firebase services | Current backend is Firebase (`firestore.googleapis.com`, `firebaseauth.googleapis.com`), not `anvil.app`. The network requirement is satisfied in shape (HTTPS/443) but not in destination. |
| NFR08 | Every user-facing date renders as `DD MMM YYYY` regardless of browser locale. | VERIFY-AT-RUNTIME | Inventory §1: `date-fns` 2.30 | `date-fns` supports the format; need to confirm every date-rendering surface (`AssessmentCard`, calendar tooltips, detail modal, reminder text) uses it. SRS specifies server-side formatting; current code formats client-side. |

---

## Code features without a matching requirement

These exist in the current code but have no corresponding SRS requirement. Each needs a decision before the Anvil port.

| Inventory reference | What it does | Keep / Drop / Formalise (recommend) |
|---|---|---|
| Inventory §1 / §3 Extension Components / §9 Chrome APIs | Chrome Extension (MV3) — popup `QuickAddAssessment` / `QuickAddNote`, service worker reminder alarm + badge counter, `chrome.identity` OAuth, direct Firestore writes. | Drop. SRS Operating Environment specifies a single Anvil app published to one URL; the extension is an extra surface area with its own auth and write paths. |
| Inventory §1 / §3 Electron / §9 Electron IPC | Electron desktop build target (portable Windows, DMG, AppImage/deb) with native notifications via IPC. | Drop. SRS Scope Constraints state "Web-only. No offline mode." Anvil is browser-only. |
| Inventory §6 / §8 `dotpoint_local_mode` localStorage flag | "Continue without account" bypass — runs the app fully offline with no Firebase auth. | Drop. SRS requires Anvil Users authentication (FR20); offline-only operation is excluded by Scope Constraints. |
| Inventory §1 / §4 Dexie indexes / §5 Dexie-first writes | Dexie/IndexedDB local persistence with cloud mirror via `DataService`. | Drop. SRS Scope Constraints: "IndexedDB and localStorage are not used." Anvil Data Tables become the single source of truth. |
| Inventory §1 / §5 / §6 Firebase | Firebase Auth (Google OAuth only) + Cloud Firestore + `enableIndexedDbPersistence`. | Drop. Replaced by Anvil Users service (FR20) and Anvil Data Tables (NFR07). |
| Inventory §5: `DataService` `setUser` → `migrateLocalDataToCloud()` | One-time migration of Dexie data to Firestore on first sign-in. | Drop. Specific to the current dual-write architecture; no analog under Anvil's single-store model. |
| Inventory §5: Firestore `onSnapshot` listeners on assessments / notes / reminderLogs / settings | Real-time cloud-to-local sync via Firestore subscriptions. | Drop. SRS Anvil callable model does not include real-time push; the doc design refreshes on action (and on the 30-minute reminder tick). |
| Inventory §3: `CommandPalette` (`Cmd+K`) | Cross-cutting palette for navigate / create / open commands. | Drop (or Formalise if user-facing palette is wanted). SRS does not mention it; UX requirement set is otherwise satisfied by the sidebar nav. |
| Inventory §3: `WeekView`, `YearView` calendar components | Week and year calendar views in addition to the month grid. | Drop. SRS FR08 specifies a monthly 7-column grid only. |
| Inventory §3: `NoteEditor` debounced 500 ms autosave | Auto-saves notes 500 ms after the last keystroke for existing notes. | Formalise. Useful UX behaviour worth carrying into the Anvil port, but it should be added as a new requirement (e.g. FR10a) with the debounce explicitly specified (inventory §3 intent notes the doc value is 300 ms, not 500 ms). |
| Inventory §7: `cleanupOldReminderLogs` (30-day TTL) | Deletes reminder log rows older than 30 days on app load. | Drop. SRS reminder design retains logs permanently; cleanup would defeat the audit value of `reminder_logs`. |
| Inventory §9: Browser / Electron / Chrome native notifications | Three parallel notification channels (`Notification` API, Electron IPC, `chrome.notifications`). | Drop. SRS FR14 specifies email only via `anvil.email.send()`; FR21 dashboard colour is the always-visible visual reminder (Follow-up Q7). |
| Inventory §7: Extension `updateBadge` alarm + `chrome.action.setBadgeText` | 5-minute alarm that recomputes upcoming-due count and renders as a browser-action badge. | Drop. Tied to the Chrome extension. |
| Inventory §7: Notification-permission polling (60 s `setInterval`) | Polls `Notification.permission` and updates internal state. | Drop. Tied to browser notifications, which themselves are dropped. |

---

## Summary

**Numbered SRS requirements (FR01–FR21, NFR01–NFR08): 29 total**

| Status | Count | IDs |
|---|---|---|
| IMPLEMENTED | 12 | FR01, FR02, FR03, FR06, FR07, FR08, FR10, FR11, FR12, FR15, FR16, FR21 |
| PARTIAL | 8 | FR04, FR05, FR09, FR13, FR14, FR17, FR20, NFR02 |
| ABSENT | 4 | FR18, FR19, NFR06, NFR07 |
| VERIFY-AT-RUNTIME | 5 | NFR01, NFR03, NFR04, NFR05, NFR08 |
| UNCLEAR | 0 | — |
| DEFERRED (numbered) | 0 | — |

**Additional scope items tracked with assigned IDs**

| Group | Count | Status |
|---|---|---|
| Stretch (FR-S01–FR-S05) | 5 | All DEFERRED (SRS classifies as "may be cut") |
| Out-of-scope (OOS-01–OOS-07) | 7 | All DEFERRED (SRS explicitly excludes) |

**Grand total tracked: 41**

### ABSENT requirements (full text)

- **FR18** — Export all of the user's assessment and note data as a downloadable JSON file (filename `dotpoint-export-YYYY-MM-DD.json`, excludes `reminder_logs`).
- **FR19** — Import a previously exported JSON file; validate against the expected schema before any write; collision-suffix records whose titles match existing rows.
- **NFR06** — Server modules separated by concern (`nlp.py`, `assessments.py`, `notes.py`, `reminders.py`, `dashboard.py`), each exposing only its `@anvil.server.callable` functions.
- **NFR07** — Application reaches its backend via standard outbound HTTPS to `*.anvil.app` on TCP 443.

### UNCLEAR requirements (full text)

None. Every numbered FR/NFR could be classified from the inventory + SRS pair, though several PARTIAL items (notably FR15, FR16, FR21) warrant source-level confirmation of specific thresholds, alias coverage, or resolver behaviour before porting.
