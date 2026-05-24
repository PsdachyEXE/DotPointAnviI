# DotPoint Source Inventory

Generated from the Vite/React/TypeScript source at `C:\Coding\DotPoint`.

---

## 1. Stack & Runtime

### Framework & Language
- **Framework:** React 18 (SPA, client-rendered via `HashRouter`)
- **Language:** TypeScript 5.3+
- **Bundler:** Vite 5
- **Styling:** Tailwind CSS 3.3
- **Animations:** Framer Motion 10.16

### Key Dependencies
| Dependency | Purpose |
|---|---|
| `react-router-dom` 6.20 | Client-side routing (HashRouter) |
| `zustand` 4.4 | Global state management |
| `dexie` 3.2.4 / `dexie-react-hooks` 1.1.7 | IndexedDB wrapper (local persistence) |
| `firebase` 12.9.0 | Auth (Google), Firestore (cloud sync) |
| `date-fns` 2.30 | Date manipulation |
| `chrono-node` 2.7 | Natural-language date parsing |
| `react-hot-toast` 2.4.1 | Toast notifications |
| `react-markdown` 9 / `remark-gfm` 4 | Markdown rendering in notes |
| `lucide-react` 0.292 | Icon library |

### Build/Deploy Targets
1. **Web (Vite SPA)** - `npm run build` -> `dist/` (`vite.config.ts:14`)
2. **Electron desktop** - `npm run electron:build:win|mac|linux` -> `release/` (`package.json:20-25`)
   - Main process: `electron/main.ts`
   - Preload: `electron/preload.ts`
   - Targets: portable (Win x64), DMG (macOS x64+arm64), AppImage/deb (Linux)
3. **Chrome Extension (MV3)** - `npm run ext:build` -> `dist-extension/` (`extension/vite.config.ts`)
   - Service worker: `extension/src/background/service-worker.ts`
   - Popup: `extension/src/popup/`

### Environment Variables Referenced in Code
| Variable | File:Line |
|---|---|
| `VITE_FIREBASE_API_KEY` | `src/lib/firebase.ts:6`, `extension/src/lib/firebase.ts:6`, `extension/src/background/service-worker.ts:17` |
| `VITE_FIREBASE_AUTH_DOMAIN` | `src/lib/firebase.ts:7`, `extension/src/lib/firebase.ts:7`, `extension/src/background/service-worker.ts:18` |
| `VITE_FIREBASE_PROJECT_ID` | `src/lib/firebase.ts:8`, `extension/src/lib/firebase.ts:8`, `extension/src/background/service-worker.ts:19` |
| `VITE_FIREBASE_STORAGE_BUCKET` | `src/lib/firebase.ts:9`, `extension/src/lib/firebase.ts:9`, `extension/src/background/service-worker.ts:20` |
| `VITE_FIREBASE_MESSAGING_SENDER_ID` | `src/lib/firebase.ts:10`, `extension/src/lib/firebase.ts:10`, `extension/src/background/service-worker.ts:21` |
| `VITE_FIREBASE_APP_ID` | `src/lib/firebase.ts:11`, `extension/src/lib/firebase.ts:11`, `extension/src/background/service-worker.ts:22` |
| `NODE_ENV` / `app.isPackaged` | `electron/main.ts:15` (Electron dev detection) |

### Third-Party Services
| Service | Purpose | Where |
|---|---|---|
| Firebase Auth (Google provider) | User authentication | `src/lib/firebase.ts`, `src/hooks/useAuth.ts` |
| Cloud Firestore | Cloud data sync | `src/lib/dataService.ts`, `src/lib/dataMigration.ts` |
| Chrome Identity API | Extension OAuth | `extension/src/lib/auth.ts:10` |
| Browser Notification API | Reminders | `src/lib/notifications.ts` |
| Electron Notification API | Native reminders | `electron/main.ts:60-70`, `electron/preload.ts:7` |
| Chrome Alarms API | Extension scheduled tasks | `extension/src/background/service-worker.ts:31-32` |
| Chrome Notifications API | Extension reminders | `extension/src/background/service-worker.ts:128-134` |

---

## 2. Routes / Pages

All routes are client-side via `HashRouter` (`src/App.tsx:96-106`). There is no server-side routing.

| Path | File | Auth required? | Data fetched | Side effects on load |
|---|---|---|---|---|
| `/` | `src/pages/NotesPage.tsx` | Conditional (see note) | `useNotes()` via Dexie live query (`src/hooks/useNotes.ts:8`) | None |
| `/assessments` | `src/pages/AssessmentsPage.tsx` | Conditional | `useAssessments()` via Dexie live query (`src/hooks/useAssessments.ts:8`) | None |
| `/calendar` | `src/pages/CalendarPage.tsx` | Conditional | `useAssessments()` via Dexie live query | None |
| `/settings` | `src/pages/SettingsPage.tsx` | Conditional | `db.settings.get('settings')` via `useLiveQuery` (`src/pages/SettingsPage.tsx:18`) | None |

**Auth gate** (`src/App.tsx:92-93`): If Firebase is configured and no user is signed in and `localStorage.dotpoint_local_mode` is not `'true'`, `LoginPage` is shown instead of routes. All routes share the same gate; there is no per-route auth check.

**Login page** (`src/components/auth/LoginPage.tsx`): Not a route; rendered conditionally in `AppContent`. Offers Google sign-in or "Continue without account" (sets `localStorage.dotpoint_local_mode = 'true'`).

---

## 3. Components

### Layout Components

**Layout** - `src/components/layout/Layout.tsx`
- Props: none (uses `<Outlet />`)
- State: none
- Side effects: none
- Children: `Sidebar`, `Header`, `MobileNav`, `<Outlet />`

**Header** - `src/components/layout/Header.tsx`
- Props: `{ title?: string }`
- State: `showUserMenu: boolean` (line 13)
- Side effects: `useEffect` closes menu on outside click (line 16), dispatches `Cmd+K` to open CommandPalette (line 24)
- Uses: `useUpcomingRemindersCount(7)`, `useAuthContext()`

**Sidebar** - `src/components/layout/Sidebar.tsx`
- Pure presentational nav links with icons. No state.

**MobileNav** - `src/components/layout/MobileNav.tsx`
- Pure presentational bottom nav. No state.

### Assessment Components

**AssessmentList** - `src/components/assessments/AssessmentList.tsx`
- Props: none
- State: `viewMode: 'active' | 'completed'` (line 23)
- Side effects: none (reads from Zustand store + Dexie live query via `useAssessments()`)
- Children: `AssessmentCard`, `AssessmentFilters`, `AssessmentForm`, `EmptyState` (local)
- Logic: client-side filtering/sorting by search, subject, status, type, sort field/direction (lines 26-85)

**AssessmentCard** - `src/components/assessments/AssessmentCard.tsx`
- Props: `{ assessment: Assessment }`
- State: `showDeleteConfirm: boolean`, `isDeleting: boolean` (lines 23-24)
- Side effects: calls `assessmentOperations.delete()` and `assessmentOperations.updateStatus()` (lines 30-46)
- Children: `ConfirmDialog`, `Badge`

**AssessmentForm** - `src/components/assessments/AssessmentForm.tsx`
- Props: `{ assessmentId?: string; onClose: () => void }`
- State: 15 state variables — title, subject, customSubject, type, dueDate, startDate, weight, status, linkedNoteIds, reminderDays, description, isSaving, smartInputFields, isMultiMode, multiAssessments, selectedIndices, isCreatingMultiple, activeTab (lines 50-73)
- Side effects: `useEffect` loads existing assessment or default settings (line 81), calls `assessmentOperations.create/update/createBulk` (lines 114-340)
- Children: `SmartAssessmentInput`, `MultiAssessmentPreview`, `BulkAssessmentInput`, `TypeSelector`, `StatusSelector`, `SubjectSelector`, `ReminderManager`, `LinkedNotesManager`, `DescriptionField`, `Input`, `Button`

**AssessmentFilters** - `src/components/assessments/AssessmentFilters.tsx`
- Props: none (reads from Zustand store)
- State: none (all state in `useAssessmentsStore`)
- Children: none (renders filter buttons inline)

**AssessmentCalendar** - `src/components/assessments/AssessmentCalendar.tsx`
- Props: none
- State: `currentDate: Date`, `viewMode: 'week' | 'month' | 'year'`, `selectedAssessment: Assessment | null` (lines 34-36)
- Children: `WeekView`, `MonthView`, `YearView`, `AssessmentDetailModal`, `AssessmentForm`

**AssessmentDetailModal** - `src/components/assessments/AssessmentDetailModal.tsx`
- Props: `{ assessment: Assessment; onClose: () => void }`
- State: none
- Side effects: calls `assessmentOperations.updateStatus()` (line 36)
- Children: `Modal`, `Button`, `Badge`

**SmartAssessmentInput** - `src/components/assessments/SmartAssessmentInput.tsx`
- Props: `{ onApply: (parsed: ParseResult['parsed']) => void; onMultipleDetected?: (...) => void; isExpanded?: boolean }`
- State: `input: string`, `parseResult`, `multiParseResult`, `isExpanded`, `isParsing`, `isMultiMode`, `textareaRows` (lines 40-46)
- Side effects: debounced parse via `useEffect` (line 125), auto-applies parsed result, shows toast (line 116)

**BulkAssessmentInput** - `src/components/assessments/BulkAssessmentInput.tsx`
- Props: `{ onAddAssessments: (assessments: MultiPartAssessment[]) => void; defaultSubject?: string | null; alwaysExpanded?: boolean }`
- State: `input`, `parseResult`, `isExpanded`, `isParsing`, `selectedAssessments: Set<number>`, `editingIndex` (lines 39-44)
- Side effects: debounced parse via `useEffect` (line 66)
- Children: `AssessmentPreviewCard` (local)

**MultiAssessmentPreview** - `src/components/assessments/MultiAssessmentPreview.tsx`
- Props: `{ assessments, allSubjects, onUpdateAssessment, onToggleSelect, onSelectAll, onDeselectAll, onCreateSelected, onCancel, selectedIndices, isCreating }`
- State: `expandedIndex: number | null` (line 51)
- Children: `AssessmentItem` (local)

### Form Sub-Components (under `src/components/assessments/form/`)

All are presentational with props-driven state. Barrel-exported from `form/index.ts`.

| Component | File | Props |
|---|---|---|
| `TypeSelector` | `form/TypeSelector.tsx` | `{ value: AssessmentType; onChange: (type) => void }` |
| `StatusSelector` | `form/StatusSelector.tsx` | `{ value: AssessmentStatus; onChange: (status) => void }` |
| `SubjectSelector` | `form/SubjectSelector.tsx` | `{ value: string; customValue: string; subjects: string[]; onChange; onCustomChange }` |
| `ReminderManager` | `form/ReminderManager.tsx` | `{ reminderDays: number[]; onChange: (days) => void }` |
| `LinkedNotesManager` | `form/LinkedNotesManager.tsx` | `{ notes: Note[]; linkedNoteIds: string[]; onLinkNote; onUnlinkNote }` |
| `DescriptionField` | `form/DescriptionField.tsx` | `{ value: string; onChange: (value) => void }` |

`ReminderManager` has local state: `newReminderDay: string` (line 13).

### Note Components

**NoteList** - `src/components/notes/NoteList.tsx`
- Props: none
- State: none (reads from Zustand store + Dexie live queries)
- Logic: client-side filtering by search, tag, pinned (lines 22-44)
- Children: `NoteCard`, `NoteFilters`, `NoteEditor`, `EmptyState` (local)

**NoteCard** - `src/components/notes/NoteCard.tsx`
- Props: `{ note: Note }`
- State: `isExpanded: boolean` (line 17)
- Side effects: calls `noteOperations.delete()`, `noteOperations.togglePin()` (lines 21-29)
- Children: renders `ReactMarkdown` for content

**NoteEditor** - `src/components/notes/NoteEditor.tsx`
- Props: `{ noteId?: string; onClose: () => void }`
- State: `title`, `content`, `tags: string[]`, `newTag`, `showPreview`, `isSaving` (lines 21-26)
- Side effects: auto-save debounced 500ms for existing notes (`useEffect` line 44), `useEffect` loads existing note (line 29)

**NoteFilters** - `src/components/notes/NoteFilters.tsx`
- Props: none (reads from Zustand store)
- State: none

### Calendar View Components

**MonthView** - `src/components/calendar/MonthView.tsx`
- Props: `{ currentDate: Date; assessments: Assessment[]; onAssessmentClick; onDateClick }`
- State: none (derived via `useMemo`)

**WeekView** - `src/components/calendar/WeekView.tsx`
- Props: `{ currentDate: Date; assessments: Assessment[]; onAssessmentClick }`
- State: none
- Children: `AssessmentChip` (local)

**YearView** - `src/components/calendar/YearView.tsx`
- Props: `{ currentDate: Date; assessments: Assessment[]; onAssessmentClick; onMonthClick }`
- State: none
- Children: `MiniMonthCalendar` (local)

### Settings Components

**SchoolTermsConfig** - `src/components/settings/SchoolTermsConfig.tsx`
- Props: `{ currentTerms: SchoolTermConfig[] | undefined; onSave: (terms) => void }`
- State: `terms: SchoolTermConfig[]`, `hasChanges: boolean` (lines 17-18)
- Side effects: calls `setCustomSchoolTerms()` on mount and save (lines 20-24, 38)

### UI Components

**CommandPalette** - `src/components/ui/CommandPalette.tsx`
- Props: none
- State: `isOpen`, `query`, `selectedIndex` (lines 29-31)
- Side effects: keyboard listener for `Cmd+K` (line 154), scrolls selected into view (line 200)
- Uses: `useNavigate()`, `useNotes()`, `useAssessments()`, both Zustand stores
- Provides: navigation, create, and open-item commands

**ConfirmDialog** - `src/components/ui/ConfirmDialog.tsx` - Presentational dialog wrapper.

**ErrorBoundary** - `src/components/ui/ErrorBoundary.tsx` - Class component `componentDidCatch` error boundary.

**Modal** - `src/components/ui/Modal.tsx` - Presentational modal with backdrop.

**Badge**, **Button**, **Card**, **Input** - Pure presentational UI primitives with no state or logic.

### Extension Components

**Extension App** - `extension/src/popup/App.tsx`
- State: `user: User | null`, `loading: boolean`, `activeTab: 'assessment' | 'note'` (lines 11-13)
- Side effects: `onExtensionAuthStateChanged` listener (line 15)
- Children: `QuickAddAssessment`, `QuickAddNote`, `AuthStatus`

**AuthStatus** - `extension/src/popup/components/AuthStatus.tsx`
- Props: `{ user: User | null }`
- State: `signingIn: boolean`, `error: string` (lines 10-11)
- Side effects: calls `signInWithChromeIdentity()` or `extensionSignOut()` (lines 13-23, 51)

**QuickAddAssessment** - `extension/src/popup/components/QuickAddAssessment.tsx`
- Props: `{ userId: string }`
- State: `input`, `title`, `subject`, `type`, `dueDate`, `weight`, `saving`, `success` (lines 16-23)
- Side effects: debounced smart parse `useEffect` (line 26), writes directly to Firestore (lines 51-89)

**QuickAddNote** - `extension/src/popup/components/QuickAddNote.tsx`
- Props: `{ userId: string }`
- State: `title`, `content`, `tags`, `pageTitle`, `pageUrl`, `saving`, `success` (lines 11-17)
- Side effects: reads current tab info via `chrome.tabs.query` (line 21), writes directly to Firestore (lines 40-73)

---

## 4. Data Model

Source of truth: TypeScript interfaces in `src/types/database.ts`. Persisted in IndexedDB via Dexie and optionally synced to Cloud Firestore.

### Note
```typescript
// src/types/database.ts:1-9
{
  id: string;           // UUID (crypto.randomUUID)
  title: string;
  content: string;      // Markdown
  tags: string[];
  isPinned: boolean;
  createdAt: Date;
  updatedAt: Date;
}
```
**Dexie indexes** (`src/lib/db.ts:13`): `id, createdAt, updatedAt, isPinned, *tags`
**Firestore path**: `users/{userId}/notes/{id}`

### Assessment
```typescript
// src/types/database.ts:11-26
{
  id: string;
  title: string;
  subject: string;
  type: 'project' | 'exam' | 'sac' | 'sat' | 'homework' | 'other';
  dueDate: Date;
  startDate?: Date;
  weight?: number;          // Percentage 0-100
  status: 'not-started' | 'in-progress' | 'submitted' | 'completed';
  linkedNoteIds: string[];  // References to Note.id
  reminderDays: number[];   // e.g. [7, 3, 1]
  description?: string;
  termInfo?: string;        // e.g. "Term 1, Week 4B"
  createdAt: Date;
  updatedAt: Date;
}
```
**Dexie indexes** (`src/lib/db.ts:14`): `id, dueDate, subject, status, createdAt`
**Firestore path**: `users/{userId}/assessments/{id}`

### ReminderLog
```typescript
// src/types/database.ts:28-34
{
  id: string;
  assessmentId: string;   // References Assessment.id
  reminderDay: number;    // Which reminder fired (7, 3, 1, 0 = due today, -1 = overdue)
  firedAt: Date;
  dismissed: boolean;
}
```
**Dexie indexes** (`src/lib/db.ts:15`): `id, assessmentId, firedAt`
**Firestore path**: `users/{userId}/reminderLogs/{id}`

### AppSettings
```typescript
// src/types/database.ts:36-49
{
  id: 'settings';               // Singleton
  theme: 'dark' | 'light';
  defaultReminderDays: number[];
  notificationsEnabled: boolean;
  schoolYear?: number;
  schoolTerms?: SchoolTermConfig[];
}
```
**Dexie indexes** (`src/lib/db.ts:16`): `id`
**Firestore path**: `users/{userId}/profile/settings`

### SchoolTermConfig
```typescript
// src/types/database.ts:36-40
{
  term: number;
  startDate: string;   // ISO date string
  endDate: string;     // ISO date string
}
```
Not stored independently; nested inside `AppSettings.schoolTerms`.

### Relations
- `Assessment.linkedNoteIds[]` -> `Note.id` (many-to-many, stored as array on assessment side)
- `ReminderLog.assessmentId` -> `Assessment.id` (many-to-one)
- No foreign key constraints enforced; app-level only
- On assessment delete, associated reminder logs are deleted from Dexie (`src/lib/dataService.ts:210`) but NOT from Firestore (`src/lib/dataService.ts:214`)

---

## 5. Server Functions / API Routes

**There are no server-side API routes.** The app is fully client-side. All data operations go through `DataService` (`src/lib/dataService.ts`) which writes to Dexie (IndexedDB) first, then optionally to Cloud Firestore.

### DataService Methods (client-side, `src/lib/dataService.ts`)

| Method | Inputs | Output | Auth check | External calls |
|---|---|---|---|---|
| `setUser(userId)` | `userId: string \| null` | `void` | Sets internal userId | Calls `migrateLocalDataToCloud()` on first sign-in (line 34) |
| `createAssessment(data)` | `Omit<Assessment, 'id'\|'createdAt'\|'updatedAt'>` | `string` (id) | If `userId` set, writes to Firestore | Dexie `.add()`, Firestore `setDoc()` (line 165) |
| `createBulkAssessments(items)` | `Array<Omit<Assessment, ...>>` | `string[]` (ids) | Same | Dexie `.bulkAdd()`, Firestore `writeBatch()` (lines 180-189) |
| `updateAssessment(id, data)` | `id, Partial<Assessment>` | `void` | Same | Dexie `.update()`, Firestore `updateDoc()` (line 204) |
| `deleteAssessment(id)` | `id: string` | `void` | Same | Dexie `.delete()` + delete related logs, Firestore `deleteDoc()` (lines 209-215) |
| `createNote(data)` | `Omit<Note, 'id'\|'createdAt'\|'updatedAt'>` | `string` | Same | Dexie `.add()`, Firestore `setDoc()` |
| `updateNote(id, data)` | `id, Partial<Note>` | `void` | Same | Dexie `.update()`, Firestore `updateDoc()` |
| `deleteNote(id)` | `id: string` | `void` | Same | Dexie `.delete()`, Firestore `deleteDoc()` |
| `updateSettings(updates)` | `Partial<AppSettings>` | `void` | Same | Dexie `.update()`, Firestore `setDoc(merge: true)` |
| `createReminderLog(data)` | `Omit<ReminderLog, 'id'>` | `string` | Same | Dexie `.add()`, Firestore `setDoc()` |

### Firestore Listeners (real-time sync, `src/lib/dataService.ts:49-148`)

When a user is signed in, `DataService` opens `onSnapshot` listeners on:
- `users/{userId}/assessments` (ordered by `updatedAt desc`)
- `users/{userId}/notes` (ordered by `updatedAt desc`)
- `users/{userId}/reminderLogs`
- `users/{userId}/profile/settings` (single document)

Changes from Firestore are written into Dexie, keeping local and cloud in sync.

### Extension Direct Firestore Writes

The Chrome extension bypasses `DataService` and writes directly to Firestore:
- `QuickAddAssessment`: `extension/src/popup/components/QuickAddAssessment.tsx:73-74`
- `QuickAddNote`: `extension/src/popup/components/QuickAddNote.tsx:61-62`
- Service worker reminder logging: `extension/src/background/service-worker.ts:170-176`

---

## 6. Auth Flow

### Provider & Library
- **Provider:** Google (only)
- **Library:** Firebase Auth (`firebase/auth`)
- **Session storage:** Firebase handles session persistence internally (IndexedDB-based)

### Login Path
1. `LoginPage` (`src/components/auth/LoginPage.tsx`) renders on app load if Firebase is configured and no user is signed in
2. "Sign in with Google" button calls `signInWithPopup(auth, googleProvider)` (`src/hooks/useAuth.ts:33`)
3. "Continue without account" sets `localStorage.dotpoint_local_mode = 'true'` and reloads (`src/components/auth/LoginPage.tsx:9-10`)
4. On successful sign-in, `onAuthStateChanged` fires (`src/hooks/useAuth.ts:23`), user state propagates via `AuthContext`
5. `AppContent` calls `dataService.setUser(user.uid)` which triggers data migration and starts Firestore listeners (`src/App.tsx:81`)

### Logout Path
- `signOut(auth)` called from `useAuth.logout()` (`src/hooks/useAuth.ts:41`)
- Available from Header user menu (`src/components/layout/Header.tsx:70`) and Settings page (`src/pages/SettingsPage.tsx:134`)
- `dataService.setUser(null)` is called reactively via `useEffect` in `AppContent` (`src/App.tsx:83`)

### Password Reset
None. Google-only auth.

### How Protected Routes Enforce Auth
- **Single gate in `AppContent`** (`src/App.tsx:92-93`): `if (!user && !isLocalMode && isFirebaseConfigured) return <LoginPage />`
- No middleware, no per-route guards
- If Firebase is not configured (`isFirebaseConfigured = false`), the app runs fully offline without any auth gate

### Extension Auth
- Uses `chrome.identity.getAuthToken()` for Google OAuth token, then `signInWithCredential()` (`extension/src/lib/auth.ts:10-42`)
- Retry logic if token is expired (line 22)
- Auth state watched via `onAuthStateChanged` in popup (`extension/src/popup/App.tsx:16`) and service worker (`extension/src/background/service-worker.ts:205`)

---

## 7. Background / Async Work

### Main App (Browser/Electron)
- **Reminder checker:** `setInterval` every 30 minutes (`src/hooks/useReminders.ts:7,32-36`)
  - Calls `checkReminders()` (`src/lib/reminders.ts:7-76`) which queries Dexie for incomplete assessments, checks if reminders should fire based on `reminderDays` config, and sends notifications
  - Also runs `cleanupOldReminderLogs()` on initial load (`src/hooks/useReminders.ts:28`) - deletes logs older than 30 days
- **Notification permission poller:** `setInterval` every 60 seconds checks browser permission state (`src/hooks/useReminders.ts:48-49`)

### Chrome Extension Service Worker (`extension/src/background/service-worker.ts`)
- **`checkReminders` alarm:** every 30 minutes (`line 31`). Queries Firestore directly for incomplete assessments, fires `chrome.notifications.create()`, logs reminders to Firestore
- **`updateBadge` alarm:** every 5 minutes (`line 32`). Queries Firestore for assessments due within 3 days, updates badge count
- **`onInstalled` listener:** runs `updateBadgeCount()` on extension install (`line 43-45`)
- **Notification click handler:** opens `https://dotpoint.vercel.app/#/assessments` (`line 199`)

### Cron Jobs / Queues / Webhooks
None found.

---

## 8. Client-Side State

### Global State Library: Zustand

Three stores, all in-memory (no Zustand persistence middleware):

**`useAssessmentsStore`** (`src/stores/useAssessmentsStore.ts`)
| State | Type | Initial |
|---|---|---|
| `searchQuery` | `string` | `''` |
| `selectedSubject` | `string \| null` | `null` |
| `selectedStatus` | `AssessmentStatus \| null` | `null` |
| `selectedType` | `AssessmentType \| null` | `null` |
| `sortBy` | `'dueDate' \| 'subject' \| 'status' \| 'createdAt'` | `'dueDate'` |
| `sortDirection` | `'asc' \| 'desc'` | `'asc'` |
| `editingAssessmentId` | `string \| null` | `null` |
| `isCreating` | `boolean` | `false` |

**`useNotesStore`** (`src/stores/useNotesStore.ts`)
| State | Type | Initial |
|---|---|---|
| `searchQuery` | `string` | `''` |
| `selectedTag` | `string \| null` | `null` |
| `showPinnedOnly` | `boolean` | `false` |
| `editingNoteId` | `string \| null` | `null` |
| `isCreating` | `boolean` | `false` |

**`useSettingsStore`** (`src/stores/useSettingsStore.ts`)
| State | Type | Initial |
|---|---|---|
| `isLoading` | `boolean` | `false` |

Actions in `useSettingsStore` call `dataService.updateSettings()` which writes to Dexie and Firestore.

### What Lives in Global State vs Local
- **Global (Zustand):** UI filter/sort state, currently-editing/creating IDs
- **Local (component state):** form field values, expanded/collapsed toggles, modal visibility, animation state
- **Persistent (Dexie/IndexedDB):** all entity data (notes, assessments, reminder logs, settings)

### Persistence (localStorage, cookies)
| Key | Where | Purpose |
|---|---|---|
| `dotpoint_local_mode` | `src/App.tsx:76`, `src/components/auth/LoginPage.tsx:9`, `src/pages/SettingsPage.tsx:42`, `src/components/layout/Header.tsx:41` | Bypasses auth gate for offline-only mode |

No cookies used. Firebase Auth uses IndexedDB for session tokens internally.

---

## 9. External Integrations

### Firebase Auth (Google Sign-In)
- **Service:** Firebase Authentication
- **What's called:** `signInWithPopup`, `onAuthStateChanged`, `signOut` (main app); `signInWithCredential` via `chrome.identity.getAuthToken` (extension)
- **Where:** `src/hooks/useAuth.ts`, `src/lib/firebase.ts`, `extension/src/lib/auth.ts`
- **Data in:** Google OAuth token
- **Data out:** Firebase `User` object (uid, email, displayName, photoURL)

### Cloud Firestore
- **Service:** Firebase Firestore
- **What's called:** `setDoc`, `updateDoc`, `deleteDoc`, `onSnapshot`, `getDocs`, `writeBatch`, `getDoc`
- **Where:** `src/lib/dataService.ts`, `src/lib/dataMigration.ts`, `extension/src/background/service-worker.ts`, `extension/src/popup/components/QuickAddAssessment.tsx`, `extension/src/popup/components/QuickAddNote.tsx`
- **Data in/out:** All entity CRUD (assessments, notes, reminder logs, settings) serialized with Timestamp conversion (`src/lib/firestore-utils.ts`)
- **Offline persistence:** enabled via `enableIndexedDbPersistence(firestore)` (`src/lib/firebase.ts:27`)

### Browser Notifications API
- **Where:** `src/lib/notifications.ts:73-97`
- **Data in:** title, body, icon, tag
- **Data out:** `Notification` object

### Electron IPC (Notifications)
- **Where:** `electron/main.ts:60-70`, `electron/preload.ts:7-9`, `src/lib/notifications.ts:55-65`
- **Channel:** `show-notification`
- **Data in:** `{ title: string; body: string }`

### Chrome Extension APIs
- **`chrome.identity`:** OAuth flow (`extension/src/lib/auth.ts:10`)
- **`chrome.alarms`:** scheduled reminder checks and badge updates (`extension/src/background/service-worker.ts:31-32`)
- **`chrome.notifications`:** native extension notifications (`extension/src/background/service-worker.ts:128,157`)
- **`chrome.action`:** badge text/color (`extension/src/background/service-worker.ts:52,73-74`)
- **`chrome.tabs`:** query active tab for page title/URL (`extension/src/popup/components/QuickAddNote.tsx:21`)

### Chrono-Node (NLP Date Parsing)
- **Where:** imported in parser utilities (via `src/lib/parser/` modules)
- **Data in:** natural language date strings
- **Data out:** parsed `Date` objects

---

## 10. Ambiguities & Gaps

### 1. Duplicate `setSelectedTag` in `useNotesStore` interface
- **File:** `src/stores/useNotesStore.ts:16-17`
- **What's ambiguous:** The `setSelectedTag` method is declared twice in the interface definition.
- **Resolution:** Verify if this compiles without error (TypeScript may allow duplicate identical declarations). Likely a copy-paste artifact.

### 2. `auth` variable reassignment in `firebase.ts`
- **File:** `src/lib/firebase.ts:22`
- **What's ambiguous:** Line 22 reads `app = getAuth(app)` - this assigns the return of `getAuth()` (an `Auth` object) to the `app` variable (declared as `FirebaseApp | null`). This appears to be a bug; the intent was likely `auth = getAuth(app)`.
- **Resolution:** Check if the app crashes at this point. The `app` variable would then contain an `Auth` object instead of `FirebaseApp`, which would cause `getFirestore(app)` on the next line to receive the wrong type.

### 3. `enableIndexedDbPersistence` error handling incomplete
- **File:** `src/lib/firebase.ts:27-31`
- **What's ambiguous:** The `.catch()` handler checks `err.code` but the callback parameter structure is incomplete in the pasted source (the `if (err.code === 'failed-precondition')` branch's opening brace is missing from the visible code).
- **Resolution:** Read the actual file to confirm whether the error handling logic is syntactically complete.

### 4. Extension hardcodes `https://dotpoint.vercel.app`
- **File:** `extension/src/background/service-worker.ts:199`
- **What's ambiguous:** Notification click handler opens a hardcoded URL. Unclear if this URL is the actual deployed app or a placeholder.
- **Resolution:** Confirm whether DotPoint is actually deployed at this Vercel URL.

### 5. Extension OAuth `client_id` hardcoded in manifest
- **File:** `extension/manifest.json:38`
- **What's ambiguous:** The OAuth2 client ID is hardcoded directly in `manifest.json`. Unclear if this is a production or development client ID, and whether it should be in a config/env var instead.
- **Resolution:** Confirm the intended OAuth2 client configuration for the extension.

### 6. `useTheme` hook directly writes to Dexie, bypassing `dataService`
- **File:** `src/hooks/useTheme.ts:22`
- **What's ambiguous:** `setTheme` calls `db.settings.update('settings', { theme })` directly instead of going through `dataService.updateSettings()`. Meanwhile, `useSettingsStore.setTheme` (`src/stores/useSettingsStore.ts:31-32`) correctly uses `dataService`. This means theme changes made via `useTheme().setTheme` won't sync to Firestore.
- **Resolution:** Determine which code path is actually used for theme changes by the UI.

### 7. Orphaned `src/lib/parser.ts` re-export
- **File:** `src/lib/parser.ts`
- **What's ambiguous:** This file contains only `export * from './parser/index'`. It exists alongside `src/lib/parser/index.ts`. Some imports use `@/lib/parser` (which resolves here) and some use `@/lib/parser/index`. The top-level file is a passthrough.
- **Resolution:** Functionally equivalent; just a structural redundancy.

### 8. `subjectColors` duplicated across two files
- **Files:** `src/lib/utils.ts:38-49` and `src/lib/colors.ts:4-20`
- **What's ambiguous:** Both files export `subjectColors` and `getSubjectColor()`. The `colors.ts` version has more subjects (includes Biology, Chemistry, Physics, Economics, Psychology). The `utils.ts` version is a subset.
- **Resolution:** Check which file is actually imported by consumers. The `colors.ts` version appears to be the canonical one used by components.

### 9. Extension writes directly to Firestore without optimistic Dexie update
- **Files:** `extension/src/popup/components/QuickAddAssessment.tsx:73-74`, `extension/src/popup/components/QuickAddNote.tsx:61-62`
- **What's ambiguous:** Extension creates notes/assessments by writing directly to Firestore, bypassing the Dexie-first pattern used by the main app. This means the data only appears in the main app once the Firestore listener picks it up.
- **Resolution:** This is architecturally intentional (extension has no Dexie), but worth noting for the port since the extension essentially acts as a Firestore-only client.

### 10. `urgencyColors` exported from two locations
- **Files:** `src/lib/utils.ts:29-35` and `src/lib/colors.ts:134-140`
- **What's ambiguous:** Same constant exported from both files. Components import from different locations.
- **Resolution:** Confirm which is the canonical export. The `colors.ts` version also exports `urgencyBorderColors` which `utils.ts` does not.

---

## Summary

| Metric | Count |
|---|---|
| **Total source files walked** | 72 |
| **Top-level routes** | 4 (`/`, `/assessments`, `/calendar`, `/settings`) |
| **Server functions** | 0 (fully client-side; 10 DataService methods + 4 Firestore listeners) |
| **Data entities** | 5 (`Note`, `Assessment`, `ReminderLog`, `AppSettings`, `SchoolTermConfig`) |
| **Zustand stores** | 3 |
| **Chrome Extension entry points** | 2 (popup + service worker) |
| **Electron entry points** | 2 (main + preload) |

### Top 5 Ambiguities

1. **`auth = getAuth(app)` assigned to `app` variable** (`src/lib/firebase.ts:22`) - likely a bug causing type mismatch
2. **`useTheme` bypasses `dataService` for Dexie writes** (`src/hooks/useTheme.ts:22`) - theme changes may not sync to cloud
3. **`subjectColors` and `urgencyColors` duplicated** across `src/lib/utils.ts` and `src/lib/colors.ts` with different contents
4. **Extension writes directly to Firestore** without Dexie, diverging from main app's dual-write pattern
5. **Duplicate `setSelectedTag` interface declaration** (`src/stores/useNotesStore.ts:16-17`) - likely harmless but sloppy
