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

    Args:
        month: what the client sent. Expected 'YYYY-MM' (the form the payload
            hands back, so the arrows return exactly what they were given), but
            anything at all may arrive — None on a first load, a list, a number,
            '99999-05' from a hand-written call.
        today: the student's local date, from _user_today. Supplies the fallback
            year/month, and is a parameter rather than a datetime.date.today()
            call inside so the whole payload is built against ONE notion of
            today.

    Returns:
        (year, month) as ints, year within 2000-2100 and month within 1-12 —
        the ranges _MIN_CALENDAR_YEAR/_MAX_CALENDAR_YEAR and 1-12 enforce, which
        is what makes the result safe to hand straight to
        calendar.monthcalendar(). Touches no database table.

    Raises:
        Nothing. That is the contract every caller here relies on.
    """
    # 1. Shape test before any splitting. count('-') == 1 rejects both '2026'
    #    and '2026-05-01' up front, which is what lets the unpacking on the next
    #    line be a plain two-name assignment instead of a length check.
    #    A negative year like '-2026-05' also has two dashes and is refused here.
    if isinstance(month, str) and month.count('-') == 1:
        year_text, month_text = month.split('-')
        try:
            # 2. int() first, then the range check, because require_int_in_range
            #    is a RANGE test and would reject a string on type alone with a
            #    message about whole numbers. .strip() forgives ' 2026-05 '.
            #    The upper bound is what stops calendar.monthcalendar() being
            #    handed a year datetime cannot build — that is the crash '99999-05'
            #    used to cause, and the reason this parser is shared.
            year = require_int_in_range(
                int(year_text.strip()), 'Calendar year',
                _MIN_CALENDAR_YEAR, _MAX_CALENDAR_YEAR)
            mon = require_int_in_range(
                int(month_text.strip()), 'Calendar month', 1, 12)
            return year, mon
        except ValueError:
            # int() on non-digits, or a year/month outside the supported window.
            # One except covers both because the answer is the same, and it is
            # ValueError specifically: require_int_in_range raises exactly that,
            # and so does int() on 'abc', so nothing unexpected is swallowed.
            pass
    # 3. The single fallback both failure paths fall through to — a bad shape
    #    above, or a bad number inside the try. Returning today's month rather
    #    than raising is what keeps a stale bookmark or a mistyped call showing a
    #    dashboard instead of an error page.
    return today.year, today.month


def _safe_filters(filters, today):
    """Guard the filter dict before any part of it reaches a database query.

    These are FR06's filters (status, subject, type, plus the show-completed
    toggle and the month the calendar is showing), and they arrive from the
    browser, so nothing in the dict is trusted. A WHITELIST is used rather than a
    blacklist for the reason whitelists always are: this module has to know every
    key it will honour, and it cannot know every key an attacker might invent.

    Each list filter must actually be a list (require_list, which RAISES — unlike
    almost every other guard in this file, a person is driving these dropdowns
    and can be told), and members the app could never have stored are dropped; if
    that empties a filter the key goes too, so the query is left unrestricted on
    that column rather than being handed an empty any_of().

    Args:
        filters: whatever the client sent. None and {} both mean "no filters".
            Recognised keys are ALLOWED_FILTER_KEYS: 'subjects', 'types',
            'statuses' (lists), 'show_completed' (bool), 'month' ('YYYY-MM'),
            and 'sort_by', which is whitelisted but read by nothing — sorting
            travels in the separate `sort` argument, so a 'sort_by' entry is
            carried through and then ignored by _list_assessments_impl.
        today: the student's local date, only ever passed on to _parse_month for
            the month fallback.

    Returns:
        A NEW dict safe to hand to _list_assessments_impl. 'show_completed' is
        always present (bool); every other key is present only when it survived
        cleaning, which is what "no restriction on this column" looks like to the
        query builder. Reads no database table itself.

    Raises:
        ValueError — `filters` is not a dict, or a list filter is not a list.
        Both are messages a student can act on, which is why these two raise
        where the value checks around them degrade.
    """
    # `filters or {}` folds None and {} together first; the isinstance test then
    # catches a non-dict. Both lines are needed — `or` alone would let a string
    # through, and isinstance alone would reject None.
    filters = filters or {}
    if not isinstance(filters, dict):
        raise ValueError('The dashboard filters were not sent correctly. '
                         'Reload the page and try again.')

    # 1. The whitelist, applied first so nothing unrecognised is even looked at
    #    below. Rebuilt as a NEW dict rather than popping from the caller's,
    #    because this function must not mutate an argument the caller still
    #    holds. `clean` from here on is the only version anything downstream sees.
    clean = dict((k, v) for k, v in filters.items() if k in ALLOWED_FILTER_KEYS)

    # 2. The three list filters, driven by a table rather than three near-copies
    #    of the same block: the only thing that differs between them is the
    #    message label and the set of values the app could have stored.
    for key, label, allowed in (
        ('subjects', 'Subject filter', _FILTERABLE_SUBJECTS),
        ('types', 'Type filter', VALID_TYPES),
        ('statuses', 'Status filter', VALID_STATUSES),
    ):
        # An absent key and an explicit None mean the same thing — "don't filter
        # on this" — so both are folded onto the same removal. Removing rather
        # than leaving None is what the query builder reads as "unrestricted".
        if clean.get(key) is None:
            clean.pop(key, None)
            continue
        # require_list RAISES on a non-list (the student can be told), but a
        # MEMBER the app could never have written is merely dropped: a subject
        # name outside _FILTERABLE_SUBJECTS cannot match any row, so sending it
        # to the query would only widen an any_of() for nothing.
        wanted = [v for v in require_list(clean[key], label) if v in allowed]
        if wanted:
            clean[key] = wanted
        else:
            # Every member was junk. The key is DROPPED rather than left as [],
            # because an empty any_of() is not "no restriction" — it is a query
            # nothing can satisfy, and the student would see an empty dashboard
            # with no explanation for it.
            clean.pop(key)

    # 3. A missing checkbox means "off", and a non-bool means the client sent
    #    something this module has no opinion about; both give the documented
    #    default. Always written, never dropped, because unlike the lists above
    #    "absent" would leave _list_assessments_impl to invent its own default.
    clean['show_completed'] = safe_bool(clean.get('show_completed'), default=False)

    # 4. Same parser as the calendar, so a month string can never restrict the
    #    list to one range while the grid shows another. The value is REBUILT
    #    from the parsed numbers rather than passed along as it arrived, because
    #    _parse_month may have substituted today's month for an unusable one —
    #    passing the original through would leave the list querying a month the
    #    grid is not drawing. '%04d-%02d' puts it back in the canonical
    #    zero-padded form assessments._get_month_bounds is written against.
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

    Args:
        due_date: a datetime.date already through safe_date, or None for a row
            with no usable due date.
        today: the student's local date from _user_today — never the server's
            UTC date, or the countdown would be a day out for most of a
            Melbourne evening.

    Returns:
        int (FR09's (due_date - today).days: negative overdue, 0 today), or None
        when there is no trustworthy number to report. Reads nothing.

    Raises:
        Nothing — the one ValueError it can provoke is caught below.
    """
    if due_date is None:
        return None
    # try/except rather than an if, because the horizon rule lives in ONE place:
    # require_within_horizon is the same function the write path calls, so the
    # date a new assessment is refused for is exactly the date whose countdown is
    # suppressed here. Re-implementing the five-year test as a comparison would
    # be a second copy of the rule, free to drift.
    try:
        require_within_horizon(due_date, today, 'Due date')
    except ValueError:
        # The exception is used as a signal, not an error: on a READ path there
        # is nobody present to correct the cell, so the message is discarded and
        # the neutral answer returned instead.
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
