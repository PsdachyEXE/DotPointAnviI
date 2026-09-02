===============================================================================
 DotPoint Assessment Tracker
 User manual and legal notice
===============================================================================

 Version:    1.0
 Updated:    2 September 2026
 Web app:    https://honored-willing-tea.anvil.app
 Built for:  Will Martin, Year 12, Haileybury College
 Built by:   Lachlan Rachor
 Platform:   Anvil (Python), hosted at anvil.works

 This file lives beside the server modules it describes. If you are reading it
 in the Anvil editor, the code it refers to is in the Server Code section on
 your left.


===============================================================================
 1. WHAT DOTPOINT IS
===============================================================================

DotPoint is an assessment tracker built specifically for VCE students.

You type one ordinary sentence:

    Methods SAC2 due Friday week 5 worth 25%

DotPoint reads that sentence and works out four things from it: the subject
(Mathematical Methods), the type of assessment (a SAC), the due date (resolved
from your own school term dates), and how much it is worth (25%). It shows you
what it worked out, you check it, and you save it.

Nothing is ever saved behind your back. The app always shows you what it
understood, labelled HIGH, MEDIUM or LOW confidence, and waits for you to press
Save. If it got something wrong, you correct it in the same window before it is
stored.

WHY IT EXISTS

Four problems came out of the interview and survey with Will:

  * Logging an assessment in Google Calendar or Notion takes over 30 seconds of
    field-by-field typing, and there are 50 or more assessments in a term. The
    friction meant assessments simply did not get logged.
  * General task apps treat a 30% SAT and a homework sheet as equally important.
  * Reminders in those apps are passive: you have to remember to go and look.
  * Nothing generic understands VCE phrasing. "Term 1, Week 4B", "SAC", "SAT",
    and subject shorthand like "bio" or "spesh" mean nothing to them.

DotPoint answers all four: one-sentence entry, urgency shown by colour on every
view, reminder emails that arrive without you opening the app, and a parser
that knows VCE vocabulary.

WHAT IT DOES

  * Turns a typed sentence into a saved assessment (or lets you fill a form in
    by hand, or paste a whole list at once).
  * Shows everything on one dashboard: a list sorted by urgency, a month
    calendar, and what is coming up in the next 30 days.
  * Colours everything by how close it is: overdue, due today or within three
    days, due within a week, or further off.
  * Emails you a reminder 7 days and 2 days before each due date, automatically.
  * Keeps study notes, which can be linked to the assessments they belong to.
  * Shows the official VCAA 2026 written exam timetable, filtered to just your
    subjects.
  * Exports everything you have to a file you keep, and reads it back later.


===============================================================================
 2. GETTING IN
===============================================================================

2.1 TEST LOGIN

A working account is provided for testing and marking. Use it as it is:

    Web address:  https://honored-willing-tea.anvil.app
    Email:        claude.tester@dotpoint.dev
    Password:     DotPointTest2026!

This account already holds assessments, notes, subjects and school term dates,
so every screen has something real on it.

A second account exists which has subjects chosen but NO assessments, if you
want to see what the app looks like when it is empty:

    Email:        claude.ui@dotpoint.dev
    Password:     DotPointTest2026!

2.2 THERE IS ONLY ONE KIND OF ACCOUNT

There is NO admin account, and no admin screen, because the app does not have
one. Every account in DotPoint is identical: it can see and change its own
assessments and notes, and nothing else. There are no roles, no permission
levels, no teacher view, no parent view and no way to share a record with
anyone.

That was a deliberate decision, not an omission. Asked whether the app should
support multiple users, the client's answer was: "Just me. I don't want anyone
else to see my stuff."

So there is one login above, not two, and that is the whole story.

2.3 SIGNING IN

  1. Open https://honored-willing-tea.anvil.app in Chrome.
  2. Click "Sign in".
  3. Type the email address and password.
  4. Click "Sign in".

2.4 MAKING YOUR OWN ACCOUNT

  1. Click "Create an account".
  2. Type an email address and a password.
  3. Click "Create account".
  4. You are taken straight to choosing your subjects (see section 3).

  BE CAREFUL: there is no "confirm password" box and no "forgot password" link
  in the app. If you mistype your password when creating the account, you will
  not be able to get back in and will need to make a new account. Type it
  carefully.

2.5 SIGNING OUT

Click "Sign out" at the top right. This ends the session on that device; you
will need your password again next time.


===============================================================================
 3. FIRST-RUN SETUP -- DO THIS BEFORE ANYTHING ELSE
===============================================================================

3.1 CHOOSING YOUR SUBJECTS

The first time you sign in, DotPoint asks which VCE studies you are doing. You
cannot skip this, because the parser uses your subject list to work out what
"methods" or "bio" means in a sentence.

  1. The studies are shown as buttons, grouped by learning area.
  2. Click each one you are doing. Click again to unpick it.
  3. Click "Save subjects".

Three rules apply:

  * You must pick at least one mathematics study. That is DotPoint's own rule,
    not a VCAA one -- the app is built around tracking a maths workload.
  * If you pick no English study, DotPoint will offer to add English for you,
    because VCAA requires an English study to complete the VCE.
  * You can pick at most 12 studies.

You can change your subjects later in Settings (section 8.4).

3.2 SETTING YOUR SCHOOL TERM DATES

*** THIS IS THE MOST IMPORTANT SETUP STEP IN THE APP. ***

Until your term dates are saved, DotPoint cannot work out what "week 5" or
"Term 3 Week 2" means. Phrases like that will silently produce no date at all
-- the app will not show an error, it will just quietly fail to understand the
part of the sentence you probably cared about most.

  1. Go to Settings.
  2. Find the "School terms" section.
  3. Either click the "Victorian 2026 term dates" button to fill in the
     standard Victorian government school dates, or type your own four terms.
  4. Check them against your school's own calendar -- Haileybury sets its own
     dates and they may differ.
  5. Click Save.

Each term needs a start date and an end date, the start must come before the
end, and the four terms must not overlap. DotPoint will tell you if they do.


===============================================================================
 4. ADDING ASSESSMENTS
===============================================================================

There are three ways to add an assessment. All three end at the same window,
where you check what will be saved before anything is stored.

4.1 BY TYPING A SENTENCE (the main way)

  1. On the Dashboard, click the box at the top that says
     "Type an assessment, e.g. ...".
  2. Type your sentence. For example:
         Methods SAC2 due Friday week 5 worth 25%
         bio prac next tuesday
         Physics SAT due 12 November worth 40%
         chem test in 10 days
  3. Click "Parse".
  4. A window opens showing what DotPoint understood. Each field says where it
     came from -- for example, Due date will say it matched "Friday week 5".
     A badge at the top says HIGH, MEDIUM or LOW confidence, which is simply a
     count of how many of the four fields it managed to find.
  5. Correct anything that is wrong. Every field is editable.
  6. Click Save.

  What the parser understands:
     Subjects   Full names, and shorthand: methods, spesh, bio, chem, psych,
                soft dev, lit, and many more.
     Types      SAC, SAT, exam, test, assignment, prac, homework -- with or
                without a number after them ("SAC2").
     Dates      A written date ("12 November"), "today", "tomorrow", "in 10
                days", a weekday ("Friday", "next Friday"), or a term-and-week
                phrase ("week 5", "Term 3 week 2") if your term dates are set.
     Weight     Any percentage: "worth 25%", "25%".

  If a field is blank in the preview, DotPoint could not find it. Just fill it
  in yourself and save.

4.2 BY FILLING IN A FORM

  1. On the Dashboard, click "Add manually".
  2. Fill in the fields. Title, Subject and Due date are marked with * and are
     required; everything else is optional.
  3. Click Save.

4.3 BY PASTING A LIST (bulk add)

Useful at the start of a term when you have a whole course outline to enter.

  1. On the Dashboard, click "Bulk add".
  2. Paste your lines, one assessment per line.
  3. Click "Parse lines". Each line is shown with a tick box and what DotPoint
     made of it.
  4. Lines DotPoint is not confident about are unticked automatically, with the
     reason shown beside them. Tick any you still want, untick any you do not.
  5. Click "Create ticked".

  Lines that fail are reported back to you with their line number and the
  reason. The lines that were fine are still saved -- one bad line does not
  block the rest.


===============================================================================
 5. WORKING WITH YOUR ASSESSMENTS
===============================================================================

5.1 READING THE DASHBOARD

The Dashboard has three panels:

  YOUR ASSESSMENTS   A card for each assessment, sorted by how soon it is due.
                     Each card shows the title, subject, type, due date, how
                     many days are left, and a coloured left edge showing
                     urgency.
  CALENDAR           The current month. A day with assessments on it shows a
                     number badge. A purple triangle marks an exam day. Click
                     a day to see what is on it. Days with nothing on them are
                     deliberately not clickable.
  NEXT 30 DAYS       Everything due in the next month, soonest first.

  The urgency colours are:
      Red      overdue
      Orange   due today, or within 3 days
      Blue     due within 7 days
      Neutral  more than 7 days away

5.2 CHANGING THE STATUS

Each card has a status dropdown. Change it straight on the card -- there is no
window to open and nothing to save.

  Marking something "Completed" makes it disappear from the list, because
  completed work is hidden by default. Tick "Show completed" to bring it back.

5.3 EDITING

Click "Edit" on the card, change what you need, click Save.

  Two things cannot be edited: the confidence badge and the original sentence
  you typed. Those are kept as a record of what the parser did.

5.4 DELETING

Click "Delete" on the card and confirm.

  There is no undo and no recycle bin. If you might want it back, export first
  (section 9).

5.5 FILTERING AND SORTING

Above the list there are dropdowns for subject, status and type, a "Show
completed" toggle, and a sort control (due date, weight, or subject).

  The filters change the LIST only. The calendar and the Next 30 Days panel
  always show everything, so a short list next to a busy calendar is not a bug.

5.6 LOOKING AT OTHER MONTHS

Use the small arrows either side of the month name in the calendar.


===============================================================================
 6. NOTES
===============================================================================

6.1 MAKING A NOTE

  1. Click "Notes" in the top bar.
  2. Click "New note".
  3. Type a title (required) and your content.
  4. Add tags if you want them -- they make notes easier to find later.
  5. Click Save.

  Note content is stored and shown as plain text. Markdown is not formatted.

6.2 FINDING A NOTE

Use the search box, which looks in both the title and the content, ignoring
capital letters. You can also filter by tag. Search and tag filter combine --
a note has to match both.

  Search is a plain text match, not a word match, so searching "sac" will also
  match the word "sacrifice".

6.3 PINNING

Click the pin icon on a note to keep it at the top of the list. It takes effect
immediately; there is nothing to save.

6.4 LINKING A NOTE TO AN ASSESSMENT

  1. Open the assessment (Edit).
  2. Find the linked-notes section.
  3. Search for the note by name and click it to link it.
  4. Click Save on the assessment. The link is not stored until you do.

  Deleting a note that is linked to assessments removes it from those
  assessments automatically. The assessments themselves are untouched.


===============================================================================
 7. REMINDER EMAILS
===============================================================================

DotPoint checks every 30 minutes, on its own, whether any of your assessments
have come within a reminder window. If one has, it emails you.

  * By default you are emailed 7 days before and 2 days before an assessment
    is due. You can change these defaults in Settings, and you can override
    them for a single assessment while editing it.
  * Emails go to the address you signed up with, and to nobody else.
  * Each reminder is sent once and once only. If the app misses a check
    (because the server was busy, say), the reminder still goes out on the next
    check rather than being lost -- but it will never be sent twice.
  * Assessments you have marked Completed do not generate reminders.
  * Overdue assessments do not generate reminders either. The red "overdue"
    colour on the dashboard is the signal for those; a daily email about work
    you already missed would just be nagging.
  * You can switch reminders off entirely in Settings.

  If reminders are switched off in Settings, no email is sent. The switch on
  the screen and the behaviour of the emailer always agree.


===============================================================================
 8. SETTINGS
===============================================================================

8.1 REMINDER DAYS

Choose how many days before an assessment you want to be emailed. These are the
DEFAULTS applied to assessments you create from now on. Assessments you have
already made keep the reminder days stored on them -- change those by editing
the assessment itself.

8.2 TIMEZONE

Sets what "today" means for every countdown, every urgency colour and every
reminder. It defaults to Australia/Melbourne. This is not cosmetic -- get it
wrong and an assessment due tomorrow can show as due today.

8.3 THEME

Light or dark. The choice is stored on your account, not in the browser, so it
follows you to another computer.

8.4 CHANGING YOUR SUBJECTS

Click "Change subjects", adjust your picks, and save. The same rules from
section 3.1 apply: at least one maths, English added if you have none, twelve
at most. If a rule is not met, the picker stays open with your choices intact
so you can fix it.

  Changing your subjects also changes which papers appear on the Exams screen.

8.5 SCHOOL TERM DATES

See section 3.2. There is a one-click button for the standard Victorian 2026
dates.


===============================================================================
 9. THE EXAM TIMETABLE
===============================================================================

Click "Exams" in the top bar.

This shows the official VCAA 2026 written examination timetable, filtered to
the studies you have chosen, with the date and time of each paper and a
countdown to the next one. Exam days are also marked on the dashboard calendar
with a purple triangle.

  These dates were transcribed by hand from the VCAA published timetable and
  checked twice. VCAA's own published timetable is the authority -- always
  check yours against it before relying on a date here.

  A small number of VCE studies have no written examination. Those are listed
  separately so you can see the app has not simply lost them.


===============================================================================
 10. BACKING UP AND RESTORING
===============================================================================

10.1 EXPORTING

  1. Click "Import & export" in the top bar.
  2. Click "Export my data".
  3. A file called dotpoint-export-YYYY-MM-DD.json downloads to your computer.

  It contains every assessment, every note and your settings -- for your
  account only. It is plain readable text, so anyone who opens the file can
  read all of it. Keep it somewhere sensible.

10.2 IMPORTING

  1. Click "Import & export".
  2. Click "Choose file" and pick a DotPoint export file.
  3. The file is checked before anything is saved. If it is not a valid export,
     nothing at all is written.

  IMPORT ADDS, IT NEVER REPLACES. Importing your own export twice gives you two
  of everything. If an assessment's title already exists, the imported one is
  saved with the date and time added to its title so you can tell them apart.


===============================================================================
 11. WHAT DOTPOINT CHECKS BEFORE IT SAVES ANYTHING
===============================================================================

Every value is checked twice: once in the browser so you find out immediately,
and again on the server, which is the real authority. If either objects, you
are told which field is wrong and why.

  FIELD              MUST BE
  ---------------    ---------------------------------------------------------
  Title              Filled in. 200 characters at most.
  Subject            One of the studies you have chosen.
  Type               SAC, SAT, exam, project, homework or other.
  Due date           A real date, within five years either side of today.
                     (The five-year limit catches a mistyped year, which
                     otherwise disappears to the far end of your calendar.)
  Start date         Optional. If given, it cannot be after the due date.
  Weight             A number from 0 to 100. Stored to two decimal places.
  Status             Not started, In progress, or Completed.
  Description        Optional. 2000 characters at most.
  Reminder days      Whole numbers from 1 to 365, six at most.
  Note title         Filled in. 200 characters at most.
  Note content       20,000 characters at most.
  Tags               20 at most, each 40 characters at most.
  Email address      Must look like an email address.
  Timezone           Must be a timezone the system recognises.
  School terms       Term numbers 1 to 4, each start before its end, no two
                     terms overlapping.
  Import file        Must be a .json file of a reasonable size, and must match
                     the shape of a DotPoint export before a single row is
                     written.

DotPoint also checks values it reads back OUT of its own database, not just
what you type in. If a stored value has somehow become unreadable, the app
shows a sensible default and keeps working rather than showing you an error
page.


===============================================================================
 12. LEGAL NOTICE
===============================================================================

12.1 WHAT DATA THIS APP HOLDS ABOUT YOU

Your account itself holds only two things: your email address, and a scrambled
(hashed) version of your password. No name, no school, no student number, no
year level, no date of birth. Nothing else is asked for and nothing else is
stored.

Everything else in the app is content you chose to type: your assessment
titles, subjects, types, dates, weights, statuses and descriptions; your note
titles, content and tags; your subject list, term dates, timezone and theme;
and, for assessments created with the parser, the original sentence you typed,
which is kept so the app can show you later how it read it.

12.2 YOUR PASSWORD

DotPoint never stores your password and never sees it after you type it. It is
handed straight to the Anvil platform's own accounts service, which stores only
a hash. No part of this app's code writes, prints, logs or returns a password.

The account service is configured to require a reasonably secure password, and
to lock an account after 10 failed sign-in attempts.

12.3 WHO CAN SEE YOUR DATA

Nobody but you.

Every request to the server identifies who is asking before it touches
anything, and every record is fetched with a check that it belongs to the
person asking. The browser has no direct database access at all. There is no
sharing feature, no invite, no teacher or parent view, and no way to make a
record public.

The single exception is the automatic reminder emailer. It runs every 30
minutes as the application rather than as any one user, so it necessarily looks
across all accounts to work out whose reminders are due. It never sends that
information anywhere: its only outputs are an email to each account holder's
own address, and a count in the server log.

12.4 WHAT LEAVES THE APP

Only reminder emails, and only to your own address. Each one contains the
assessment title, subject, due date, type, weight and a link back to the app.

Nothing else is sent anywhere. There is no analytics, no tracking, no
advertising, and no connection to any third-party service. The app makes no
outbound network call to anything other than its own platform.

Your export file leaves the app when you ask it to, by downloading to your own
computer. From that point it is yours to look after.

12.5 KEEPING AND DELETING YOUR DATA

Your data is kept until you delete it. You can delete any assessment or any
note from within the app at any time, and the deletion is immediate and
permanent -- there is no recycle bin.

  One deliberate exception: deleting an assessment does NOT delete the record
  that a reminder was emailed about it. Those records exist so the app can be
  certain it never sends the same reminder twice, and they are kept.

There is currently no button in the app to delete your whole account. To have
an account and all its data removed, ask the developer, who will delete it from
the database directly.

12.6 WHERE YOUR DATA IS STORED

In Anvil Data Tables, on Anvil's hosted infrastructure, on servers Anvil
operates. All traffic between your browser and the app is encrypted (HTTPS).
DotPoint does not store anything on your own computer beyond what the browser
needs to keep you signed in.

12.7 CONTENT YOU PASTE IN

Notes and descriptions are free text. If you paste in a teacher's SAC brief, a
textbook extract, a study guide or anything else somebody else wrote, that
material stays the property of whoever wrote it. DotPoint stores it exactly as
you typed it, shows it back only to you, and claims no ownership of it.

Please do not paste in material you are not entitled to keep a copy of.

12.8 THIS APP AND ITS CODE

DotPoint was written by Lachlan Rachor as a VCE Unit 3-4 Software Development
School Assessed Task. It is a student project, provided for the client's own
use. It is not a commercial product, it carries no warranty, and it should not
be the only place you record something that matters -- keep an eye on your
school's own assessment schedule too.


===============================================================================
 13. ACKNOWLEDGEMENTS
===============================================================================

Material in this app belongs to other people, and is used as follows.

VICTORIAN CURRICULUM AND ASSESSMENT AUTHORITY (VCAA)

  * The 2026 VCE written examination timetable shown on the Exams screen is
    published by the VCAA. It is public information, reproduced here for
    convenience only. It was transcribed by hand and checked against the VCAA
    page on 23-24 July 2026. VCAA's own published timetable is authoritative.
  * The names of the VCE studies, and the learning areas they are grouped
    into, come from the VCAA's published list of VCE study designs. They are
    used to label your own subjects, not to reproduce any course content.
  * The rule that a VCE program must include an English study is VCAA's.
    The rule that it must include a mathematics study is DotPoint's own, added
    at the client's request; VCAA does not require it.

VICTORIAN DEPARTMENT OF EDUCATION

  * The one-click 2026 term dates are the published Victorian government school
    term dates. Independent schools set their own; check yours.

ANVIL (anvil.works)

  * DotPoint is built with and hosted by Anvil. Account creation, password
    storage and sign-in are handled by Anvil's Users service, not by this app's
    own code. Reminder emails are delivered by Anvil's Email service. Data is
    stored in Anvil Data Tables.

OPEN SOURCE SOFTWARE

  * Bootstrap (MIT licence) -- the CSS framework underlying the Anvil runtime
    theme. DotPoint adds its own stylesheet on top of it.
  * bootstrap-notify by Robert McIntosh (MIT licence) -- the notification
    messages that appear at the corner of the screen.
  * dateparser (BSD 3-Clause) -- an optional helper for reading dates written
    in ways the app's own rules do not cover. It runs on the server and makes
    no network call.
  * pytz and the IANA timezone database -- used for Australian timezone
    handling.
  * The Python standard library (Python Software Foundation licence),
    particularly the calendar, datetime and re modules.

  No web fonts are downloaded and no font file is redistributed. The app names
  fonts your own device already has.

THE CLIENT

  * The requirements this app was built to came from an interview, a survey and
    an observation session with Will Martin, used with his consent for this
    school assessment. No personal or sensitive information was collected from
    him: no passwords, no student ID, no grades, and no contact details beyond
    the school email address he offered.


===============================================================================
 14. IF SOMETHING GOES WRONG
===============================================================================

"Week 5" or "Term 3 Week 2" is not being turned into a date.
    Your school term dates are not set, or one of them is back to front. Go to
    Settings and check section 3.2. This is by far the most common cause of the
    parser seeming not to work properly.

The parser got the subject wrong.
    Check Settings -- if a subject is not in your chosen list, DotPoint will not
    prefer it. Bare "maths" is resolved to your own maths study when you have
    exactly one; if you take two, say which one you mean.

An assessment I saved has disappeared from the list.
    You probably marked it Completed. Tick "Show completed" to bring it back.
    It may also be hidden by a subject, status or type filter.

A reminder email never arrived.
    Check that reminders are switched on in Settings; that the assessment is
    not marked Completed; that it is not already overdue (overdue work is not
    emailed about, by design); and that the email is not in your spam folder.
    Reminders are sent from a shared address, so they can be filtered.

I cannot sign in.
    There is no password reset link in the app. Check the email address is
    typed correctly. If the password is genuinely lost, the account cannot be
    recovered from within the app -- ask the developer.

The page looks wrong or a button does nothing.
    Reload the page. If a dialog is open, the browser Back button deliberately
    does nothing -- close the dialog first.

Nothing loads at all.
    The app is on Anvil's free hosting tier, so the very first visit after a
    quiet period can take a few seconds to wake up. Give it a moment and
    reload.


===============================================================================
 15. WHERE THE CODE IS
===============================================================================

For anyone reading this inside the Anvil editor, the server modules beside this
file are:

    nlp.py           Reads a typed sentence into a structured assessment.
    assessments.py   Creating, reading, editing, deleting, bulk add, export
                     and import.
    notes.py         Notes, plus your settings and subject list, plus sign-in
                     and account creation.
    dashboard.py     Builds the whole dashboard in a single request.
    exams.py         The VCAA 2026 exam timetable and the papers you sit.
    reminders.py     The background job that sends the reminder emails.
    _validation.py   The shared input checks used by all of the above.
    _constants.py    The subject lists, aliases, enums and field limits.
    _auth.py         Works out who is asking, and whether a record is theirs.
    _datetime.py     Your local "today", date formatting, urgency bands.

The screens are in Client Code: LoginForm, OnboardingForm, DashboardForm,
AssessmentEditorForm, NotesForm, NoteEditorForm, ExamsForm, SettingsForm,
ImportExportForm, Main (which decides which screen to show), and common (the
shared building blocks every screen is made from).

Fuller technical documentation is in the docs/ folder of the source
repository: IMPLEMENTATION_SPEC.md, VALIDATION.md, TESTING.md and
REQUIREMENTS_COVERAGE.md.

===============================================================================
 End of manual.
===============================================================================
