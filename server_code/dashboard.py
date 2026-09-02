import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Dashboard aggregator: one round-trip payload for the all-in-view DashboardForm.

Exposes get_dashboard_data(month, filters, sort) as @anvil.server.callable.
Combines the filtered assessment list, a month calendar grid with per-day urgency
colours (FR08, FR21), an "upcoming" 30-day sidebar (FR09), and the distinct
subject set for the filter dropdown. Building all four in one call is the design's
mitigation for NFR01 — NFR01 itself is the under-two-seconds dashboard render
budget, not a round-trip count.

VALIDATION (SAT criterion 7.3). This module reads far more than it writes, so both
limbs of the rule appear here:
  * arriving  — `month`, `filters` and `sort` come from the client, so they are
    whitelisted and range-checked before they reach a query or the calendar.
  * departing — every assessment value handed back to the client goes through the
    `safe_*` family first, because one off-enum or out-of-range cell used to reach
    the browser and render differently in two places at once.

See IMPLEMENTATION_SPEC.md section 2 (server_code/dashboard.py).
"""

import anvil.server
import calendar as _calendar

from ._auth import _require_user
from ._constants import (
    ALLOWED_FILTER_KEYS, CANONICAL_SUBJECTS, LEGACY_SUBJECT_RENAMES,
    MAX_WEIGHT, MIN_WEIGHT, STATUS_COMPLETED, STATUS_DEFAULT, SUBJECT_ALIASES,
    VALID_CONFIDENCE, VALID_STATUSES, VALID_TYPES,
)
from ._datetime import _user_today, _user_now, _urgency_band, _format_date_au
from ._validation import (
    is_positive_int, require_int_in_range, require_list, require_within_horizon,
    safe_bool, safe_choice, safe_date, safe_list, safe_number, safe_text,
)
from .notes import _get_or_create_settings, _settings_row_to_dict
from .assessments import _list_assessments_impl, _row_to_dict, _decorate
from .exams import (
    _get_exam_subjects, _build_exams_for_subjects, _find_next_exam,
    _get_exam_days_for_month,
)

# The window the month navigator may address. Wide enough for any school year this
# app will see, and narrow enough that calendar.monthcalendar() is never handed a
# year datetime cannot represent — it raises above 9999, which is how '99999-05'
# used to take the whole dashboard down.
_MIN_CALENDAR_YEAR = 2000
_MAX_CALENDAR_YEAR = 2100

# How far ahead the "upcoming" sidebar looks (FR09).
_UPCOMING_WINDOW_DAYS = 30

# Every subject name a filter may name, and therefore the only ones the subject
# dropdown offers: the picker catalog, plus the generic 'Mathematics' the parser can
# assign, plus the pre-rename names legacy rows may still carry. A value outside this
# set cannot match a row the app wrote, so it is dropped rather than sent to a query.
_FILTERABLE_SUBJECTS = frozenset(
    set(CANONICAL_SUBJECTS)
    | set(SUBJECT_ALIASES.values())
    | set(LEGACY_SUBJECT_RENAMES))


# --- incoming: guards for what the client sent ------------------------------

def _parse_month(month, today):
    """'YYYY-MM' -> (year, month), degrading to today's year/month.

    THE single month parser. There used to be two — this one and
    assessments._month_bounds — applying different rules to the same string, so
    '99999-05' was silently ignored by the list query and crashed the calendar
    (calendar.monthcalendar cannot build a year datetime.date rejects). Both of this
    module's consumers now derive from this one parse, so they cannot disagree.

    Degrades rather than raises: this value comes from the calendar's prev/next
    arrows, not from a person typing, so there is no input for anyone to correct and
    showing the current month is the only useful answer.
    """
    if isinstance(month, str) and month.count('-') == 1:
        year_text, month_text = month.split('-')
        try:
            year = require_int_in_range(
                int(year_text.strip()), 'Calendar year',
                _MIN_CALENDAR_YEAR, _MAX_CALENDAR_YEAR)
            mon = require_int_in_range(
                int(month_text.strip()), 'Calendar month', 1, 12)
            return year, mon
        except ValueError:
            # int() on non-digits, or a year/month outside the supported window.
            pass
    return today.year, today.month


def _safe_filters(filters, today):
    """Guard the filter dict before any part of it reaches a database query.

    Unknown keys are dropped silently against ALLOWED_FILTER_KEYS (NFR04). Each
    list filter must actually be a list (require_list — a person is driving these
    dropdowns and can be told), and members the app could never have stored are
    dropped; if that empties a filter the key goes too, so the query is left
    unrestricted on that column rather than being handed an empty any_of().
    """
    filters = filters or {}
    if not isinstance(filters, dict):
        raise ValueError('The dashboard filters were not sent correctly. '
                         'Reload the page and try again.')

    clean = dict((k, v) for k, v in filters.items() if k in ALLOWED_FILTER_KEYS)

    for key, label, allowed in (
        ('subjects', 'Subject filter', _FILTERABLE_SUBJECTS),
        ('types', 'Type filter', VALID_TYPES),
        ('statuses', 'Status filter', VALID_STATUSES),
    ):
        if clean.get(key) is None:
            clean.pop(key, None)
            continue
        wanted = [v for v in require_list(clean[key], label) if v in allowed]
        if wanted:
            clean[key] = wanted
        else:
            clean.pop(key)

    # A missing checkbox means "off", and a non-bool means the client sent something
    # this module has no opinion about; both give the documented default.
    clean['show_completed'] = safe_bool(clean.get('show_completed'), default=False)

    # Same parser as the calendar, so a month string can never restrict the list to
    # one range while the grid shows another.
    if clean.get('month') is None:
        clean.pop('month', None)
    else:
        year, mon = _parse_month(clean['month'], today)
        clean['month'] = '%04d-%02d' % (year, mon)

    return clean


# --- departing: guards for what the database returned -----------------------

def _days_remaining(due_date, today):
    """Reasonableness check on a CALCULATED result (rubric 7.3).

    days_remaining drives the countdown text, the urgency colour band and the 30-day
    sidebar, and it is computed from a stored date — so it is only as trustworthy as
    the cell it came from. Two cases are not answers:

      * no usable due date -> there is no countdown to report;
      * a date outside the five-year horizon require_within_horizon enforces on the
        way in (a mistyped year stored before that rule existed, or a Data Tables
        console edit) -> the subtraction still succeeds and yields something like
        13,140, which renders as "in 13140 days" and bands the row 'distant' forever.

    Both give the documented neutral value None, which every consumer already reads
    as "no countdown": the card prints the date on its own, the calendar cell falls
    back to the 'distant' band, and the sidebar skips the row. The stored date is
    still displayed — the implausible number is suppressed, not the record.
    """
    if due_date is None:
        return None
    try:
        require_within_horizon(due_date, today, 'Due date')
    except ValueError:
        return None
    return (due_date - today).days


def _safe_assessment_view(item, today):
    """Guard one decorated assessment dict on its way out to the client.

    The `safe_*` half of criterion 7.3: these values were read out of the database,
    so nobody is present to correct a bad cell and each check degrades to a
    documented default rather than raising. Degrading here rather than in each form
    is what keeps the two surfaces that render a row consistent — an off-enum `type`
    used to draw a chip on the list card and nothing at all in the calendar day
    dialog, from the same assessment.
    """
    view = dict(item)

    view['title'] = safe_text(item.get('title'))
    view['subject'] = safe_text(item.get('subject'))
    # 'other' is the enum's own "unclassified" member, so a stored value that has
    # fallen off the enum is reported honestly instead of being blanked.
    view['type'] = safe_choice(item.get('type'), VALID_TYPES, 'other')
    view['status'] = safe_choice(item.get('status'), VALID_STATUSES, STATUS_DEFAULT)
    view['confidence'] = safe_choice(item.get('confidence'), VALID_CONFIDENCE, None)
    view['weight'] = safe_number(item.get('weight'), default=None,
                                 minimum=MIN_WEIGHT, maximum=MAX_WEIGHT)
    view['reminder_days'] = safe_list(item.get('reminder_days'),
                                      element_check=is_positive_int)
    view['linked_note_ids'] = safe_list(
        item.get('linked_note_ids'),
        element_check=lambda note_id: isinstance(note_id, str) and bool(note_id))

    # One guarded date drives all four date-derived fields, so due_date,
    # due_display, days_remaining and urgency_band cannot contradict each other.
    due = safe_date(item.get('due_date'))
    start = safe_date(item.get('start_date'))
    view['due_date'] = due.isoformat() if due is not None else None
    view['start_date'] = start.isoformat() if start is not None else None
    view['due_display'] = _format_date_au(due) if due is not None else ''

    days = _days_remaining(due, today)
    view['days_remaining'] = days
    view['urgency_band'] = _urgency_band(days) if days is not None else 'distant'
    return view


def _build_calendar(year: int, month: int, decorated: list) -> dict:
    """Build the calendar-grid payload from already-guarded assessment dicts.

    Pure function (no DB): `decorated` items carry ISO 'due_date', 'days_remaining'
    and 'urgency_band'. Returns weeks (6x7, 0 = blank), per-day buckets, and the
    highest-urgency colour band per day (most urgent = smallest days_remaining).

    day_buckets / cell_colours are keyed by STR(day) — Anvil refuses to
    serialize dicts with non-string keys ("Cannot serialize dictionaries with
    keys that aren't strings"). The client's _cell() helper looks up str keys.
    """
    weeks = _calendar.monthcalendar(year, month)
    day_buckets = {}
    for a in decorated:
        # safe_date rather than fromisoformat: this helper stays safe even if a
        # future caller hands it a dict that has not been through the view guard.
        d = safe_date(a.get('due_date'))
        if d is None:
            continue
        if d.year == year and d.month == month:
            day_buckets.setdefault(str(d.day), []).append(a)

    cell_colours = {}
    for day_key, items in day_buckets.items():
        days = [it.get('days_remaining') for it in items
                if it.get('days_remaining') is not None]
        # min() only on a non-empty list: a day holding nothing but rows whose
        # countdown was suppressed still needs a colour, and 'distant' is it.
        cell_colours[day_key] = _urgency_band(min(days)) if days else 'distant'

    return {
        'year': year,
        'month': month,
        'weeks': weeks,
        'day_buckets': day_buckets,
        'cell_colours': cell_colours,
    }


@anvil.server.callable
def get_dashboard_data(month: str = None, filters: dict = None, sort: dict = None) -> dict:
    """Return the whole dashboard in one payload.

    One server call instead of four is the design's mitigation for NFR01's
    under-two-seconds render budget.
    """
    user = _require_user()
    settings = _get_or_create_settings(user)
    today = _user_today(settings)

    # Guard everything the client sent before it reaches a query or the calendar.
    if not isinstance(sort, dict):
        # _list_assessments_impl whitelists sort['by'] itself, but it calls .get()
        # on this value first, which raises on a string or a list.
        sort = {}
    clean_filters = _safe_filters(filters, today)
    year, mon = _parse_month(month, today)

    # Panel 1: the filtered/sorted assessment list.
    assessment_list = [
        _safe_assessment_view(a, today)
        for a in _list_assessments_impl(user, settings, clean_filters, sort)
    ]

    # One unfiltered read powers the calendar, upcoming sidebar and subject set.
    all_rows = app_tables.assessments.search(user=user)
    all_decorated = [
        _safe_assessment_view(_decorate(_row_to_dict(r), today), today)
        for r in all_rows
    ]

    # Panel 2: calendar grid for the requested month.
    calendar_payload = _build_calendar(year, mon, all_decorated)

    # Panel 3: upcoming (not completed, due within 30 days), soonest first.
    upcoming = [
        a for a in all_decorated
        if a.get('status') != STATUS_COMPLETED
        and a.get('days_remaining') is not None
        and 0 <= a['days_remaining'] <= _UPCOMING_WINDOW_DAYS
    ]
    upcoming.sort(key=lambda a: a.get('due_date') or '')

    # Filter dropdown: the student's locked subjects first (spec §11), then any
    # legacy data subjects not in that list (so old rows stay filterable). Only
    # subjects _safe_filters would accept are offered, so the dropdown can never
    # present a choice the filter would silently drop.
    locked = _get_exam_subjects(settings)
    data_subjects = sorted({a['subject'] for a in all_decorated
                            if a.get('subject') in _FILTERABLE_SUBJECTS})
    subjects = list(locked) + [s for s in data_subjects if s not in locked]

    # Exam overlay (spec §13): flag this month's exam days on the calendar and
    # surface the next-exam countdown chip. next_exam is None when the student has
    # no examinable subjects or every paper is over; the client draws no chip.
    user_exams = _build_exams_for_subjects(locked, today, _user_now(settings))
    calendar_payload['exam_days'] = _get_exam_days_for_month(user_exams, year, mon)

    return {
        'today': today.isoformat(),
        'assessment_list': assessment_list,
        'calendar': calendar_payload,
        'upcoming': upcoming,
        'subjects': subjects,
        'next_exam': _find_next_exam(user_exams),
        'settings': _settings_row_to_dict(settings),
    }
