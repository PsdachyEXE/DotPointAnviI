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

# {canonical subject: [{'date': 'YYYY-MM-DD', 'start': 'HH:MM', 'end': 'HH:MM',
#                       'paper': str}, ...]} — one entry per written paper.
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

# Catalog subjects with NO written exam on the VCAA 2026 timetable. Anything
# absent from BOTH this set and EXAM_TIMETABLE_2026 is a data gap and is
# reported as 'not_covered', never as exam-less.
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

    Returns None rather than raising so a mistyped timetable entry costs one
    finished-today check, not the whole exams page.
    """
    parts = text.split(':') if isinstance(text, str) else []
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
    """
    subjects = safe_list(_row_value(settings_row, 'subjects'),
                         element_check=_is_catalog_subject)
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
    """
    out = []
    for subject in (subjects or []):
        for e in EXAM_TIMETABLE_2026.get(subject, []):
            d = safe_date(e.get('date'))
            if d is None:
                # One unreadable timetable row is skipped rather than raised, so a
                # single bad transcription cannot cost the student the other fifty.
                continue
            days = (d - today).days
            band = 'done' if days < 0 else _urgency_band(days)
            if days == 0 and now is not None:
                end = _parse_clock(e.get('end'))
                if end is not None and (now.hour, now.minute) >= end:
                    band = 'done'
            out.append({
                'subject': subject,
                'paper': safe_text(e.get('paper')),
                'date': d.isoformat(),
                'start': safe_text(e.get('start')),
                'end': safe_text(e.get('end')),
                'days_remaining': days,
                'urgency_band': band,
            })
    out.sort(key=lambda e: (e['date'], e['start'], e['subject']))
    return out


def _find_next_exam(exams: list):
    """The first not-yet-finished exam dict, or None (input must be sorted).

    Deliberately a scan with a None result rather than exams[0]: the list is empty
    for a student who has not onboarded, and every entry is 'done' once the exam
    period has passed. Both are ordinary states, not errors, and both must produce
    None so the dashboard chip and the exams page simply draw nothing.
    """
    for e in (exams or []):
        days = e.get('days_remaining')
        if days is not None and days >= 0 and e.get('urgency_band') != 'done':
            return e
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
