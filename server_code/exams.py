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
retrieved 2026-07-23; written-exam period Tue 27 Oct - Wed 18 Nov 2026, all
times Melbourne local). Subjects DotPoint supports that have no VCAA written
exam (e.g. Applied Computing Units 1&2, Extended Investigation) simply have
no entry and are reported under 'no_exam_subjects'.
"""

import anvil.server
import datetime

from ._auth import _require_user
from ._constants import ENGLISH_GROUP
from ._datetime import _user_today, _urgency_band
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
        {'date': '2026-11-05', 'start': '09:00', 'end': '10:15', 'paper': 'Composition — written examination'},
        {'date': '2026-11-09', 'start': '09:00', 'end': '10:15', 'paper': 'Inquiry — written examination'},
        {'date': '2026-11-17', 'start': '09:00', 'end': '10:15', 'paper': 'Contemporary Performance — aural & written'},
        {'date': '2026-11-17', 'start': '09:00', 'end': '10:15', 'paper': 'Repertoire Performance — aural & written'},
    ],
    'Drama': [
        {'date': '2026-11-13', 'start': '11:45', 'end': '13:30', 'paper': 'Written examination'},
    ],
    'Theatre Studies': [
        {'date': '2026-10-29', 'start': '09:00', 'end': '10:45', 'paper': 'Written examination'},
    ],
}


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


def _exams_for_subjects(subjects, today: datetime.date) -> list:
    """Decorated exam list for `subjects`, soonest first.

    Each item: subject, paper, date (ISO), start, end, days_remaining,
    urgency_band ('done' once the date has passed).
    """
    out = []
    for subject in subjects:
        for e in EXAM_TIMETABLE_2026.get(subject, []):
            d = datetime.date.fromisoformat(e['date'])
            days = (d - today).days
            out.append({
                'subject': subject,
                'paper': e['paper'],
                'date': e['date'],
                'start': e['start'],
                'end': e['end'],
                'days_remaining': days,
                'urgency_band': 'done' if days < 0 else _urgency_band(days),
            })
    out.sort(key=lambda e: (e['date'], e['start'], e['subject']))
    return out


def _next_exam(exams: list):
    """The first not-yet-past exam dict, or None (input must be sorted)."""
    for e in exams:
        if e['days_remaining'] >= 0:
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
    today = _user_today(settings)

    subjects = _exam_subjects(settings)
    exams = _exams_for_subjects(subjects, today)
    return {
        'today': today.isoformat(),
        'onboarded': bool(subjects),
        'exams': exams,
        'next_exam': _next_exam(exams),
        'no_exam_subjects': [s for s in subjects if s not in EXAM_TIMETABLE_2026],
        'source_url': EXAM_SOURCE_URL,
    }
