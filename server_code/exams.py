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
from ._constants import ENGLISH_GROUP
from ._datetime import _user_now, _urgency_band
from .notes import _get_or_create_settings, _row_value

EXAM_SOURCE_URL = 'https://www.vcaa.vic.edu.au/administration/key-dates/vce-examination-timetable'

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

def _exam_subjects(settings_row) -> list:
    """The subjects whose exams the user sees: their locked list, with English
    appended if no English-group study is present (the same guarantee
    notes._clean_subjects enforces; kept here for legacy rows written before
    onboarding shipped). Empty list = not onboarded yet."""
    subjects = _row_value(settings_row, 'subjects') or []
    if subjects and not any(s in ENGLISH_GROUP for s in subjects):
        subjects = list(subjects) + ['English']
    return subjects


def _exams_for_subjects(subjects, today: datetime.date, now=None) -> list:
    """Decorated exam list for `subjects`, soonest first.

    Each item: subject, paper, date (ISO), start, end, days_remaining,
    urgency_band ('done' once the paper is over — including earlier TODAY
    when `now`, a tz-aware datetime in the user's zone, is supplied).
    """
    out = []
    for subject in subjects:
        for e in EXAM_TIMETABLE_2026.get(subject, []):
            d = datetime.date.fromisoformat(e['date'])
            days = (d - today).days
            band = 'done' if days < 0 else _urgency_band(days)
            if days == 0 and now is not None:
                end_h, end_m = e['end'].split(':')
                if (now.hour, now.minute) >= (int(end_h), int(end_m)):
                    band = 'done'
            out.append({
                'subject': subject,
                'paper': e['paper'],
                'date': e['date'],
                'start': e['start'],
                'end': e['end'],
                'days_remaining': days,
                'urgency_band': band,
            })
    out.sort(key=lambda e: (e['date'], e['start'], e['subject']))
    return out


def _next_exam(exams: list):
    """The first not-yet-finished exam dict, or None (input must be sorted).

    Skips 'done' bands, so a paper that ended earlier today is never shown
    as 'Next exam: TODAY'.
    """
    for e in exams:
        if e['days_remaining'] >= 0 and e['urgency_band'] != 'done':
            return e
    return None


def _exam_days_for_month(exams: list, year: int, month: int) -> dict:
    """{str(day): ['Subject — paper', ...]} for the given calendar month.

    Keys are stringified for Anvil serialization, matching the dashboard
    calendar's day_buckets convention.
    """
    days = {}
    for e in exams:
        d = datetime.date.fromisoformat(e['date'])
        if d.year == year and d.month == month:
            days.setdefault(str(d.day), []).append(
                '%s — %s' % (e['subject'], e['paper']))
    return days


# --- callable ----------------------------------------------------------------

@anvil.server.callable
def get_exam_timetable() -> dict:
    """The logged-in student's VCE 2026 written exams, sorted by date."""
    user = _require_user()
    settings = _get_or_create_settings(user)
    now = _user_now(settings)
    today = now.date()

    subjects = _exam_subjects(settings)
    exams = _exams_for_subjects(subjects, today, now)
    uncovered = [s for s in subjects if s not in EXAM_TIMETABLE_2026]
    return {
        'today': today.isoformat(),
        'onboarded': bool(subjects),
        'exams': exams,
        'next_exam': _next_exam(exams),
        'no_exam_subjects': [s for s in uncovered if s in NO_WRITTEN_EXAM],
        'not_covered': [s for s in uncovered if s not in NO_WRITTEN_EXAM],
        'source_url': EXAM_SOURCE_URL,
    }
