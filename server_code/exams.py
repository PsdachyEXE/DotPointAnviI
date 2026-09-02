import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""VCE 2026 written-exam timetable (spec §13).

Exposes get_exam_timetable() as @anvil.server.callable: the logged-in
student's exams (their locked subjects, plus English if they somehow have no
English-group study), sorted by date, decorated with days-remaining and the
shared urgency band. Also provides the pure helpers the dashboard uses to
flag exam days on the calendar and show the next-exam countdown chip.

EXAM_TIMETABLE_2026 was transcribed from the official VCAA "2026 VCE
examination timetable" (https://www.vcaa.vic.edu.au/administration/key-dates/vce-examination-timetable,
retrieved 2026-07-23/24, every entry independently re-verified against the
same page; written-exam period Tue 27 Oct - Wed 18 Nov 2026, all times
Melbourne local). It covers every catalog subject except the three in
NO_WRITTEN_EXAM, which have no written exam on the VCAA timetable and are
reported under 'no_exam_subjects'; any future catalog subject missing from
both tables is reported under 'not_covered' so absence of data is never
presented as absence of an exam. Languages are the written paper only (orals
run earlier, outside this window) for the most-taken stream, noted in the
paper label. Music is one picker subject over four VCAA studies: each paper
row names its stream, and the two 17 Nov performance papers share one row.

WHERE THIS SITS IN THE REQUIREMENTS. The exam screen is spec §13, added after
the SRS was signed off, so it has no FR number of its own. What it does have is
a duty to behave like the rest of the app: the countdown is the same
(date - today).days FR09 specifies for assessments, the colour bands are FR21's
first-match-wins table via _datetime._urgency_band, and NFR03 is satisfied
without a single explicit user test in this module because the only thing read
per-user is the caller's own settings row, reached from _require_user().

Reading order: the pure helpers first — they take plain values and touch no
database, so they can be exercised without an Anvil session — then the single
callable at the bottom, which fetches the settings row and strings them
together. dashboard.py imports four of those helpers directly rather than
calling get_exam_timetable, so the calendar's exam overlay cannot disagree with
the Exams page (NFR01: one round trip, not two).
"""

import anvil.server
import datetime

from ._auth import _require_user
from ._constants import CANONICAL_SUBJECTS, ENGLISH_GROUP
from ._datetime import _user_now, _urgency_band
from ._validation import safe_date, safe_list, safe_text
from .notes import _get_or_create_settings, _row_value

EXAM_SOURCE_URL = 'https://www.vcaa.vic.edu.au/administration/key-dates/vce-examination-timetable'

# Membership set for the read-guard below; CANONICAL_SUBJECTS is a display-ordered
# tuple, and this is asked once per stored subject on every dashboard load.
_CATALOG_SUBJECTS = frozenset(CANONICAL_SUBJECTS)

# --- the transcribed VCAA timetable -----------------------------------------
# SHAPE. Keyed by the canonical subject name the picker uses
# (_constants.CANONICAL_SUBJECTS), so looking a student's subject up is a plain
# .get() with no normalising in between:
#
#   {subject: [{'date':  'YYYY-MM-DD',  the day of the paper
#               'start': 'HH:MM',       24-hour, Melbourne local
#               'end':   'HH:MM',       used to decide a paper is over today
#               'paper': str},          'Exam 1', 'Written examination', or
#              ...]}                    the stream the paper belongs to
#
# The value is always a LIST even for the many subjects with a single paper,
# because Methods, Specialist and General each have two and Music has three:
# one shape for every subject means _build_exams_for_subjects has one loop
# rather than a special case.
#
# WHERE IT CAME FROM, AND WHAT IT IS NOT. Every row below was transcribed BY
# HAND from VCAA's published "2026 VCE examination timetable" (EXAM_SOURCE_URL)
# — retrieved 2026-07-23 and then re-verified entry by entry against the same
# page on 2026-07-24. It is a convenience copy, not an authority. VCAA's
# timetable is the authority: a hand transcription can carry a typo, and VCAA
# can move a paper after this file was written. That is exactly why
# EXAM_SOURCE_URL is returned in the payload and the Exams page links to it
# rather than presenting these dates as final.
#
# Dates and times are stored as STRINGS rather than date/time objects for two
# reasons: the whole payload is serialised to the browser anyway, so objects
# would only be converted back; and a plain ISO string can be read straight off
# the screen against the VCAA page when the transcription is next re-checked.
#
# COVERAGE. 53 of the 56 catalog subjects appear here. The other three are in
# NO_WRITTEN_EXAM below. Nothing is silently absent — see that comment.
EXAM_TIMETABLE_2026 = {
    'English': [
        {'date': '2026-10-27', 'start': '09:00', 'end': '12:15', 'paper': 'Written examination'},
    ],
    'English as an Additional Language': [
        {'date': '2026-10-27', 'start': '09:00', 'end': '12:15', 'paper': 'Written examination'},
    ],
    'English Language': [
        {'date': '2026-10-28', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Literature': [
        {'date': '2026-10-29', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Foundation Mathematics': [
        {'date': '2026-11-17', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'General Mathematics': [
        {'date': '2026-10-30', 'start': '14:00', 'end': '15:45', 'paper': 'Exam 1'},
        {'date': '2026-11-02', 'start': '14:00', 'end': '15:45', 'paper': 'Exam 2'},
    ],
    'Mathematical Methods': [
        {'date': '2026-11-05', 'start': '09:00', 'end': '10:15', 'paper': 'Exam 1'},
        {'date': '2026-11-06', 'start': '11:45', 'end': '14:00', 'paper': 'Exam 2'},
    ],
    'Specialist Mathematics': [
        {'date': '2026-11-09', 'start': '09:00', 'end': '10:15', 'paper': 'Exam 1'},
        {'date': '2026-11-11', 'start': '11:45', 'end': '14:00', 'paper': 'Exam 2'},
    ],
    'Biology': [
        {'date': '2026-11-02', 'start': '09:00', 'end': '11:45', 'paper': 'Written examination'},
    ],
    'Chemistry': [
        {'date': '2026-11-10', 'start': '09:00', 'end': '11:45', 'paper': 'Written examination'},
    ],
    'Physics': [
        {'date': '2026-11-12', 'start': '09:00', 'end': '11:45', 'paper': 'Written examination'},
    ],
    'Psychology': [
        {'date': '2026-10-30', 'start': '09:00', 'end': '11:45', 'paper': 'Written examination'},
    ],
    'Software Development': [
        {'date': '2026-11-13', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Data Analytics': [
        {'date': '2026-11-09', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination'},
    ],
    'Accounting': [
        {'date': '2026-11-11', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Business Management': [
        {'date': '2026-11-04', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Economics': [
        {'date': '2026-10-29', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination'},
    ],
    'Legal Studies': [
        {'date': '2026-11-06', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'History: Revolutions': [
        {'date': '2026-11-09', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'History: Australian History': [
        {'date': '2026-11-11', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination'},
    ],
    'History: Ancient History': [
        {'date': '2026-11-12', 'start': '14:00', 'end': '16:15', 'paper': 'Written examination'},
    ],
    'Geography': [
        {'date': '2026-11-12', 'start': '14:00', 'end': '16:15', 'paper': 'Written examination'},
    ],
    'Physical Education': [
        {'date': '2026-11-09', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination'},
    ],
    'Health and Human Development': [
        {'date': '2026-11-05', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Media': [
        {'date': '2026-11-10', 'start': '14:00', 'end': '16:15', 'paper': 'Written examination'},
    ],
    'Visual Communication Design': [
        {'date': '2026-11-11', 'start': '09:00', 'end': '10:45', 'paper': 'Written examination'},
    ],
    'Art Making and Exhibiting': [
        {'date': '2026-10-28', 'start': '11:45', 'end': '13:30', 'paper': 'Written examination'},
    ],
    'Music': [
        # One picker subject spanning four VCAA studies; a student sits only
        # the paper for their own stream.
        {'date': '2026-11-05', 'start': '09:00', 'end': '10:15', 'paper': 'Written examination (Composition stream only)'},
        {'date': '2026-11-09', 'start': '09:00', 'end': '10:15', 'paper': 'Written examination (Inquiry stream only)'},
        {'date': '2026-11-17', 'start': '09:00', 'end': '10:15', 'paper': 'Aural & written (Contemporary/Repertoire Performance streams)'},
    ],
    'Drama': [
        {'date': '2026-11-13', 'start': '11:45', 'end': '13:30', 'paper': 'Written examination'},
    ],
    'Theatre Studies': [
        {'date': '2026-10-29', 'start': '09:00', 'end': '10:45', 'paper': 'Written examination'},
    ],
    'Environmental Science': [
        {'date': '2026-11-10', 'start': '14:00', 'end': '16:15', 'paper': 'Written examination'},
    ],
    'Classical Studies': [
        {'date': '2026-11-04', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination'},
    ],
    'Philosophy': [
        {'date': '2026-11-13', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Politics': [
        {'date': '2026-11-16', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Religion and Society': [
        {'date': '2026-11-16', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Sociology': [
        {'date': '2026-11-12', 'start': '14:00', 'end': '16:15', 'paper': 'Written examination'},
    ],
    'Texts and Traditions': [
        {'date': '2026-11-10', 'start': '14:00', 'end': '16:15', 'paper': 'Written examination'},
    ],
    'Industry and Enterprise': [
        {'date': '2026-11-09', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Algorithmics': [
        {'date': '2026-11-04', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination'},
    ],
    'Food Studies': [
        {'date': '2026-11-16', 'start': '09:00', 'end': '10:45', 'paper': 'Written examination'},
    ],
    'Product Design and Technologies': [
        {'date': '2026-11-13', 'start': '09:00', 'end': '10:45', 'paper': 'Written examination'},
    ],
    'Systems Engineering': [
        {'date': '2026-11-16', 'start': '11:45', 'end': '13:30', 'paper': 'Written examination'},
    ],
    'Dance': [
        {'date': '2026-11-06', 'start': '09:00', 'end': '10:45', 'paper': 'Written examination'},
    ],
    'Outdoor and Environmental Studies': [
        {'date': '2026-11-11', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination'},
    ],
    'Chinese': [
        {'date': '2026-11-18', 'start': '14:00', 'end': '16:15', 'paper': 'Written examination (Second Language stream)'},
    ],
    'French': [
        {'date': '2026-11-17', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'German': [
        {'date': '2026-11-13', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination'},
    ],
    'Greek': [
        {'date': '2026-11-10', 'start': '14:00', 'end': '16:15', 'paper': 'Written examination'},
    ],
    'Indonesian': [
        {'date': '2026-11-17', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination (Second Language stream)'},
    ],
    'Italian': [
        {'date': '2026-11-17', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination'},
    ],
    'Japanese': [
        {'date': '2026-11-18', 'start': '09:00', 'end': '11:15', 'paper': 'Written examination (Second Language stream)'},
    ],
    'Spanish': [
        {'date': '2026-11-09', 'start': '11:45', 'end': '14:00', 'paper': 'Written examination'},
    ],
    'Vietnamese': [
        {'date': '2026-10-28', 'start': '15:00', 'end': '17:15', 'paper': 'Written examination (First Language stream)'},
    ],
}

# Catalog subjects that genuinely have NO written examination on the VCAA 2026
# timetable — not subjects this file forgot.
#
# The distinction is the whole reason this set exists as a separate table from
# the timetable above. Without it, a student taking Extended Investigation
# would open the Exams page, see nothing for it, and have no way to tell "you
# have no exam for this" from "DotPoint has lost your exam". So the two answers
# are reported through two different payload keys: a subject in here comes back
# under 'no_exam_subjects' ("no written paper — nothing to sit"), and a subject
# missing from BOTH tables comes back under 'not_covered' ("this app has no
# data for it — check VCAA yourself"). A future catalog subject that nobody
# remembers to add here therefore surfaces as a visible gap in DotPoint rather
# than as a quiet claim that the student has no exam.
NO_WRITTEN_EXAM = frozenset((
    'Applied Computing',        # Units 1&2 study — no external exam
    'Extended Investigation',
    'Art Creative Practice',
))


# --- pure helpers (offline-testable) ----------------------------------------

def _is_catalog_subject(value) -> bool:
    """Element predicate for safe_list: a subject the picker actually offers.

    notes._clean_subjects enforces this when the student saves their program, but
    the Data Tables console and a half-applied import both bypass that path. An
    unguarded stray value would be looked up in EXAM_TIMETABLE_2026, miss, and then
    be reported to the student under 'not_covered' — presenting a bad cell as a gap
    in this app's timetable data.
    """
    return value in _CATALOG_SUBJECTS


def _parse_clock(text):
    """'HH:MM' -> (hour, minute), or None when the text is not a clock time.

    Only caller: _build_exams_for_subjects, deciding whether a paper being sat
    TODAY has already finished. The tuple is returned so the caller can compare
    it against (now.hour, now.minute) — tuple comparison is lexicographic, so
    that one expression orders the whole clock without any minutes arithmetic.

    Args:
        text: expected to be an 'end' or 'start' value out of
            EXAM_TIMETABLE_2026, i.e. 24-hour 'HH:MM'. Anything at all may
            arrive, including None, and is answered with None.

    Returns:
        (hour, minute) as ints, or None when the text is not two runs of digits
        separated by a colon. Note the ints are NOT range-checked: this is only
        ever compared against a real clock, so a nonsense '99:99' loses the
        comparison harmlessly instead of needing its own error path.

    Raises:
        Nothing. Returning None rather than raising is the point — a mistyped
        timetable entry should cost one finished-today check, not the whole
        exams page.
    """
    # The isinstance guard runs first because .split() is not defined on None
    # or on a number, and this value is only ever as trustworthy as the hand
    # transcription above. An empty list then fails the length test below, so
    # both failures leave through the same return.
    parts = text.split(':') if isinstance(text, str) else []
    # Checked before int() rather than catching ValueError around it: this
    # whole helper exists so its one caller never has to wrap the call in a
    # try, and the values are module-level ASCII literals from the transcribed
    # timetable, so a digit test is a sufficient screen for them.
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    return int(parts[0]), int(parts[1])


def _get_exam_subjects(settings_row) -> list:
    """The subjects whose exams the user sees: their locked list, with English
    appended if no English-group study is present (the same guarantee
    notes._clean_subjects enforces; kept here for legacy rows written before
    onboarding shipped). Empty list = not onboarded yet.

    The stored list is read through safe_list (criterion 7.3, the database limb):
    user_settings.subjects is an Anvil simpleObject, so the cell can hold a scalar,
    a dict, or a list with one bad entry among good ones. Unusable entries are
    dropped and the rest still work, because refusing to show a student their exams
    over one damaged value is worse than showing the ones that are readable.

    Args:
        settings_row: the caller's own `user_settings` row from
            notes._get_or_create_settings. May be missing the `subjects` column
            entirely on a pre-migration database, which _row_value absorbs.

    Returns:
        A list of canonical subject names, in the order the student saved them.
        Empty list means "not onboarded yet" and every caller reads it that
        way. Reads user_settings.subjects only; writes nothing.
    """
    # Reads the column through the same catalog predicate notes.set_subjects
    # validates against on the way in, so a value the picker would refuse to
    # save cannot be honoured just because it is already in the cell.
    subjects = safe_list(_row_value(settings_row, 'subjects'),
                         element_check=_is_catalog_subject)
    # The `subjects and` guard is what keeps the empty case empty: without it,
    # a student who has never onboarded would be handed a one-item list and the
    # Exams page would confidently show them an English exam they may not sit.
    # An empty list has to stay empty so get_exam_timetable's 'onboarded' key
    # reads False and the page offers "Choose my subjects" instead of a
    # timetable.
    if subjects and not any(s in ENGLISH_GROUP for s in subjects):
        subjects = subjects + ['English']
    return subjects


def _build_exams_for_subjects(subjects, today: datetime.date, now=None) -> list:
    """Decorated exam list for `subjects`, soonest first.

    Each item: subject, paper, date (ISO), start, end, days_remaining,
    urgency_band ('done' once the paper is over — including earlier TODAY
    when `now`, a tz-aware datetime in the user's zone, is supplied).

    Returns an empty list for an empty or missing subject list — a student who has
    not finished onboarding, or whose studies are all in NO_WRITTEN_EXAM. Every
    caller treats [] as "nothing to show" rather than indexing into it.

    This is the function that turns a student's locked subjects into their own
    paper list: subjects in, one row per paper out. Both the Exams page and the
    dashboard's calendar overlay come through here, which is why it is a pure
    helper — if each screen worked the timetable out for itself they could
    disagree about which paper is next.

    Args:
        subjects: canonical subject names, normally from _get_exam_subjects().
            None and [] are both accepted and both give [].
        today: the date to count from, in the STUDENT's timezone
            (_datetime._user_today), never the UTC server date — a paper the
            morning after would otherwise still read "tomorrow" until 11am
            Melbourne time.
        now: optional tz-aware datetime in the same zone. Supplying it is what
            lets a paper sat earlier TODAY be marked 'done'; without it a
            same-day exam stays 'today' until midnight.

    Returns:
        A list of plain dicts, soonest first:
            subject         canonical name, echoed from the input
            paper           label from the timetable, e.g. 'Exam 2'
            date            'YYYY-MM-DD'
            start / end     'HH:MM' as transcribed
            days_remaining  int; 0 = today, negative once past
            urgency_band    'overdue'|'today'|'soon'|'distant' from FR21's
                            table, or 'done' — see the note below.
        Reads no database table: everything comes from EXAM_TIMETABLE_2026 and
        the two date arguments.

    Raises:
        Nothing. Every value it handles is guarded on the way through.
    """
    out = []
    # 1. Two nested loops rather than a comprehension: a subject can contribute
    #    nought (not in the timetable), one, or three papers, and the body has
    #    to skip and continue. The flat `out` list is what gets sorted at the
    #    end, so papers from different subjects interleave by date.
    for subject in (subjects or []):
        # .get(subject, []) is the whole handling of a subject with no exam:
        # NO_WRITTEN_EXAM and an unknown subject both simply contribute no
        # rows here, and get_exam_timetable tells the two apart afterwards.
        for e in EXAM_TIMETABLE_2026.get(subject, []):
            d = safe_date(e.get('date'))
            if d is None:
                # One unreadable timetable row is skipped rather than raised, so a
                # single bad transcription cannot cost the student the other fifty.
                continue
            # 2. The countdown, and the FR21 band derived from it. `days` is
            #    negative for a paper already sat, which _urgency_band would
            #    call 'overdue' — right for an assessment you can still hand
            #    in, wrong for an exam, which is simply over. 'done' is this
            #    module's own fifth state and is set BEFORE _urgency_band is
            #    consulted so the two can never both apply.
            days = (d - today).days
            band = 'done' if days < 0 else _urgency_band(days)
            # 3. The same-day case the date arithmetic cannot see: it is still
            #    the day of the paper, but the paper finished at lunchtime.
            #    Only reachable when `now` was supplied, and only for days == 0
            #    — every other day is already settled by the subtraction above.
            #    Comparing (hour, minute) tuples orders the clock without any
            #    minutes-since-midnight arithmetic. `>=` counts the exact
            #    finishing minute as done.
            if days == 0 and now is not None:
                end = _parse_clock(e.get('end'))
                if end is not None and (now.hour, now.minute) >= end:
                    band = 'done'
            # 4. Rebuilt as a NEW dict rather than copying the timetable entry,
            #    so the module-level constant can never be mutated by a caller
            #    holding one of these rows, and every text field goes out
            #    through safe_text — the transcription is hand-typed, and the
            #    client renders these straight onto the page.
            out.append({
                'subject': subject,
                'paper': safe_text(e.get('paper')),
                'date': d.isoformat(),
                'start': safe_text(e.get('start')),
                'end': safe_text(e.get('end')),
                'days_remaining': days,
                'urgency_band': band,
            })
    # 5. Sorted once at the end, not per subject, because the loops built the
    #    list subject-by-subject. The key is (date, start, subject): dates and
    #    times are zero-padded ISO/24-hour text, so string order IS
    #    chronological order and no parsing is needed. Subject is the third key
    #    purely as a tie-break, so two papers in the same slot always come out
    #    in the same order — an unstable order here would make the Exams page
    #    reshuffle itself between reloads for no visible reason.
    out.sort(key=lambda e: (e['date'], e['start'], e['subject']))
    return out


def _find_next_exam(exams: list):
    """The first not-yet-finished exam dict, or None (input must be sorted).

    Deliberately a scan with a None result rather than exams[0]: the list is empty
    for a student who has not onboarded, and every entry is 'done' once the exam
    period has passed. Both are ordinary states, not errors, and both must produce
    None so the dashboard chip and the exams page simply draw nothing.

    Args:
        exams: the output of _build_exams_for_subjects, i.e. already sorted
            soonest-first. Sortedness is a precondition, not something checked
            here — the first survivor is returned, so an unsorted list would
            yield a wrong answer rather than an error. None and [] give None.

    Returns:
        The same dict object out of `exams` (not a copy), or None. Callers only
        read it, so sharing the object is safe and saves the copy.
    """
    for e in (exams or []):
        days = e.get('days_remaining')
        # Three conditions, not one, because they rule out different things.
        # `is not None` guards the comparison itself — a row whose date could
        # not be read has no countdown, and None >= 0 raises in Python 3.
        # `>= 0` rules out a paper already past. The band test then catches the
        # one case the number cannot see: a paper sat earlier TODAY, which
        # still has days_remaining == 0 but was marked 'done' on the clock.
        if days is not None and days >= 0 and e.get('urgency_band') != 'done':
            return e
    # Falling off the end is an ordinary outcome, not a failure: no subjects
    # locked in, or the exam period is over. Both mean "no countdown to show".
    return None


def _get_exam_days_for_month(exams: list, year: int, month: int) -> dict:
    """{str(day): ['Subject — paper', ...]} for the given calendar month.

    Keys are stringified for Anvil serialization, matching the dashboard
    calendar's day_buckets convention. An empty or missing list gives {}, which is
    what the calendar draws for a month with no exams in it.
    """
    days = {}
    for e in (exams or []):
        d = safe_date(e.get('date'))
        if d is None:
            continue
        if d.year == year and d.month == month:
            days.setdefault(str(d.day), []).append(
                '%s — %s' % (safe_text(e.get('subject')), safe_text(e.get('paper'))))
    return days


# --- callable ----------------------------------------------------------------

@anvil.server.callable
def get_exam_timetable() -> dict:
    """The logged-in student's VCE 2026 written exams, sorted by date."""
    user = _require_user()
    settings = _get_or_create_settings(user)
    now = _user_now(settings)
    today = now.date()

    subjects = _get_exam_subjects(settings)
    exams = _build_exams_for_subjects(subjects, today, now)
    uncovered = [s for s in subjects if s not in EXAM_TIMETABLE_2026]
    return {
        'today': today.isoformat(),
        'onboarded': bool(subjects),
        'exams': exams,
        'next_exam': _find_next_exam(exams),
        'no_exam_subjects': [s for s in uncovered if s in NO_WRITTEN_EXAM],
        'not_covered': [s for s in uncovered if s not in NO_WRITTEN_EXAM],
        'source_url': EXAM_SOURCE_URL,
    }
