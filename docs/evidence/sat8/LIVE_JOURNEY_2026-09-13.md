# Live journey transcript — 13 September 2026

Run against the published app at <https://honored-willing-tea.anvil.app> as the test
account `claude.tester@dotpoint.dev`, on Chrome (Windows 11), after the Anvil app was
synced to commit `94d0a5c` and re-published. Every "Actual output" line below was read
out of the running page, not reconstructed.

Today during this run was **Sunday 13 September 2026**. Relative dates ("in 44 days",
"51 days overdue") are computed from that.

> **On screenshots.** The browser automation available in this session returns images
> for inspection but cannot write them to disk, so the actual output below is captured
> as verbatim page text and DOM queries instead. The five screenshots worth taking by
> hand are listed at the end of this file.

---

## TC-PRE-01 — parser preview, provenance and confidence (FR01, FR15, FR16, FR17)

**Input** — typed into the dashboard parse box, then Enter (not the Parse button, to
confirm `pressed_enter` is bound):

    Chemistry SAC4 due term 4 week 2 worth 20%

**Actual output** — dialog "Confirm parsed assessment", confidence pill `HIGH`, and one
provenance line under each detected field:

    Subject *   Chemistry     matched "chemistry" → Chemistry
    Type        SAC           matched "sac4" → sac
    Due date *  12 Oct 2026   matched "term 4 week 2" → 12 Oct 2026
    Weight (%)  20            matched "20%" → 20%

Required markers rendered on `Title *`, `Subject *`, `Due date *`; `Start date
(optional)` correctly unmarked. Field hint under Title: `Up to 200 characters.`

**PASS.** Note what this proves beyond FR01: `matched "term 4 week 2" → 12 Oct 2026` is
the FR15 term-and-week resolver running in the live app. Term 4 begins Monday 5 Oct
2026, so week 2 is Monday 12 Oct 2026. Until today that path had no offline test at
all (see the debugging case study).

---

## TC-PRE-02 — submit blocked before any server call (FR03, FR04)

**Setup** — in the same dialog, cleared Title and set Weight to `150`, then pressed Save.

**Actual output** — the dialog stayed open and two messages appeared, each in red
beneath its own field, read back from the DOM by their `fielderror` role:

    Title is required.
    Weight (%) must be between 0 and 100 (you entered 150).

No toast was raised; the toast stack was empty. **PASS** — FR03's "submit is blocked if
a required field is missing or weight is out of range", and FR04's "beside the offending
field" rather than in a banner. The range message quotes the value back.

---

## TC-PRE-03 — the same record saves once valid (FR03, FR09)

**Setup** — restored Title `Chemistry SAC4` and Weight `20`, pressed Save.

**Actual output** — dialog closed, no field errors, list header moved from
`16 shown` to `17 shown`. On reload the new row appears in the NEXT 30 DAYS panel as
`12 Oct 2026 — Chemistry SAC4`.

**PASS** — parse → preview → save → dashboard, end to end, on a term-resolved date.

---

## TC-PRE-04 — the weight fix, live (FR01)

**Input**

    Physics prac due friday worth -5%

**Actual output** — preview opened with confidence `MEDIUM` and:

    Subject *   Physics    matched "physics" → Physics
    Type        Project    matched "prac" → project
    Due date *  18 Sep 2026  matched "friday" → 18 Sep 2026
    Weight (%)  (empty)    — no provenance line

**PASS.** Before today's fix this sentence produced `Weight = 5` with the provenance
line `matched "5%" → 5%` and a `HIGH` pill — a number the student never typed, presented
confidently. The parser now declines to read it, the student fills the box in, and the
confidence honestly reflects that one of the four scored fields was not found.

---

## TC-PRE-05 — Cancel discards without writing (FR17)

**Setup** — pressed Cancel on the TC-PRE-04 preview.

**Actual output** — dialog closed; list still `17 shown`; the parse box still held
`Physics prac due friday worth -5%`.

**PASS** — "Pressing Cancel discards the parse without writing", and the sentence is
deliberately left in the box so it can be corrected and re-parsed.

---

## TC-PRE-06 — empty parse costs no round trip (FR01)

**Input** — three spaces in the parse box, then the Parse button.

**Actual output** — orange toast reading exactly `Type an assessment first.`; no dialog
opened; no `parse_text` call was made.

**PASS.**

---

## TC-PRE-07 — empty filter result (FR06, FR07) — FAILED, FIXED, RE-TESTED

**Setup** — with 17 assessments present, set Subject = `Literature` and Type = `Exam`,
a combination nothing matches.

**Expected** (SRS FR07) — "Empty result set shows a 'no assessments match' message
rather than rendering a blank panel."

**Actual output, first run**

    Nothing here yet
    Type an assessment above and press Parse, or add one manually.
    [Add manually]

**FAIL.** The panel is not blank, so the letter of FR07 is met, but the sentence
describes an account with nothing in it — not a list the student has just filtered.
To someone who has seventeen assessments and has hidden them all, it reads as data
loss, and the control that would restore the view is inside the panel that is empty.
`NotesForm` already distinguishes these two cases; `DashboardForm` did not.

**Action taken** — `_filters_are_narrowed()` added to `DashboardForm`, reading the same
sentinels `_build_filters` reads, plus a second empty state and a `Clear filters`
button. Locked by `test_constants_integrity.suite_empty_states_name_their_cause`.
Committed as `94d0a5c`, pushed, synced into Anvil, re-published.

**Actual output, after the fix**

    No assessments match
    Nothing matches those filters. Set them back to "All" to see everything.
    [Clear filters]

Pressing `Clear filters` returned all three dropdowns to `All subjects` / `All status` /
`All types` and the list to `17 shown`. **PASS.**

---

## TC-PRE-08 — dashboard render and urgency (FR08, FR09, FR21)

**Actual output** — three panels side by side. Calendar headed `September 2026`, weekday
row `MON TUE WED THU FRI SAT SUN`, today (13) drawn as a filled accent pill, 7 Sep
carrying a count badge `1` and an overdue tint. Assessment cards show a coloured left
edge and a literal day count, e.g.

    English essay        English   Other  parsed · MEDIUM  24 Jul 2026 · 51 days overdue
    Physics SAC3         Physics   SAC    parsed · HIGH    07 Sep 2026 · 6 days overdue
    psych sac            Psychology SAC   parsed · HIGH    12 Oct 2026 · in 29 days

Next-exam strip: `NEXT EXAM  English — Written examination  in 44 days`.

**PASS** — every date renders `DD MMM YYYY` (NFR08), overdue and upcoming are worded
differently, and colour is never the only signal because the day count sits beside it.

---

## TC-DAT-01 — the reminder pipeline is live (FR13, FR14, NFR02)

**Actual output** — Anvil IDE → Scheduled Tasks:
`Run run_reminder_check every 30 minutes`, enabled for both the Default and Published
environments. Background Tasks lists an unbroken series of `run_reminder_check` runs at
roughly 30-minute spacing going back more than five days, every one `COMPLETED`, each
"in a few seconds". Most recent at the time of the run: 7 minutes earlier.

`reminder_logs` holds real rows written by those runs, e.g.

    assessment_id            user                        sent_date     reminder_type
    [1068587,8404435901]     claude.tester@dotpoint.dev  29 Jul 2026   2_day
    [1068587,8404435901]     claude.tester@dotpoint.dev  29 Jul 2026   7_day
    [1068587,8437717919]     claude.tester@dotpoint.dev  29 Jul 2026   2_day
    ...

Two rows per assessment with distinct `reminder_type` values, which is the dedup key
working as designed (once per threshold, ever). **PASS.**

---

## TC-DAT-02 — what the error log actually contains (all modules function correctly)

**Setup** — Anvil IDE → App Sessions, session filter cleared, "Only show sessions with
errors" ticked, listing back to the earliest session retained.

**Actual output** — across the window `23:43:04 ACST Tue 18 Aug 2026` to
`Sun 13 Sep 2026` — roughly 26 days, about 1,250 scheduled runs at 48 a day — the log
holds **five** error sessions, all of them the same class:

    4 x  anvil.server.RuntimeUnavailableError: Internal server error: Server runtime failed to start
    1 x  anvil.server.RuntimeUnavailableError: Downlink disconnected

    e.g. 02:13:19, 02:39:10 and 02:43:19 ACST on Sun 13 Sep 2026

**No exception raised by application code appears anywhere in the window.** Every one of
the five is Anvil's own runtime failing to start, so `run_reminder_check` did not run on
those ticks — the app was never reached.

**PASS, with a recorded limitation.** A missed tick is not a lost reminder: thresholds
are tested with `days_remaining <= d` rather than `== d`, and the dedup key is
`(assessment, user, reminder_type)` with no date in it, so the next successful run sends
exactly what the missed one would have and nothing twice. That property was designed in
(`server_code/reminders.py` module docstring, lines 18-32); this log is the first
evidence that it is load-bearing rather than theoretical, because the platform does drop
ticks in practice.

---

## Screenshots still worth capturing by hand

The five that carry information the text above cannot:

1. The parser preview of TC-PRE-01 — the `HIGH` pill and four provenance lines together.
2. TC-PRE-02 — both red messages sitting under their own fields.
3. TC-PRE-07 after the fix — "No assessments match" with the `Clear filters` button.
4. The calendar with today ringed and the count badge on the 7th.
5. Anvil IDE → App Sessions with "Only show sessions with errors" ticked (TC-DAT-02).
