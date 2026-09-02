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

    EVERY assessment in the payload passes through here — the list, the calendar
    day buckets and the upcoming sidebar all hold rows this function built, which
    is what guarantees one row shown in three panels reads the same way in all
    three.

    Args:
        item: a decorated assessment dict, i.e. assessments._row_to_dict() put
            through assessments._decorate(). Its 'id', 'description',
            'term_info', 'source_text', 'created_at' and 'updated_at' are carried
            across untouched; the fields listed below are re-guarded.
        today: the student's local date, passed on to _days_remaining.

    Returns:
        A NEW dict holding every key `item` had, with these overwritten:
            title, subject   str; '' when unusable
            type             VALID_TYPES member; 'other' when off-enum
            status           VALID_STATUSES member; STATUS_DEFAULT when off-enum
            confidence       VALID_CONFIDENCE member, or None (FR17)
            weight           float MIN_WEIGHT-MAX_WEIGHT, or None
            reminder_days    list[int], each positive
            linked_note_ids  list[str], each non-empty (FR12)
            due_date         'YYYY-MM-DD', or None
            start_date       'YYYY-MM-DD', or None
            due_display      'DD MMM YYYY' (NFR08), or ''
            days_remaining   int, or None (FR09)
            urgency_band     'overdue'|'today'|'soon'|'distant' (FR21)
        Reads no table — `item` was already read for it.

    Raises:
        Nothing. Every check here degrades to a documented default.
    """
    # dict(item) makes a shallow COPY before anything is overwritten, so the
    # caller's dict is left as it was. That matters because get_dashboard_data
    # builds one decorated list and reads it for three panels: mutating in place
    # would make the order those panels are built in significant.
    view = dict(item)

    view['title'] = safe_text(item.get('title'))
    view['subject'] = safe_text(item.get('subject'))
    # 'other' is the enum's own "unclassified" member, so a stored value that has
    # fallen off the enum is reported honestly instead of being blanked.
    view['type'] = safe_choice(item.get('type'), VALID_TYPES, 'other')
    view['status'] = safe_choice(item.get('status'), VALID_STATUSES, STATUS_DEFAULT)
    view['confidence'] = safe_choice(item.get('confidence'), VALID_CONFIDENCE, None)
    # default=None, not 0: a row with no weighting and a row worth 0% of the
    # grade are different things, and the card prints "% of grade" only for the
    # second. The bounds are re-applied on the way OUT as well as in, because a
    # Data Tables console edit could have left 500 in a percentage column.
    view['weight'] = safe_number(item.get('weight'), default=None,
                                 minimum=MIN_WEIGHT, maximum=MAX_WEIGHT)
    # Both of these are Anvil simpleObject columns, so the cell can legally hold
    # any JSON at all. safe_list drops the unusable ELEMENTS and keeps the rest,
    # so one junk entry costs that entry rather than the whole list.
    view['reminder_days'] = safe_list(item.get('reminder_days'),
                                      element_check=is_positive_int)
    # A lambda rather than a named predicate because this is the only place the
    # rule is needed on this path: a linked note id is a Data Tables row id, so
    # a non-empty string is the whole test — the id is not resolved here, since
    # the dashboard never opens the notes it lists (FR12).
    view['linked_note_ids'] = safe_list(
        item.get('linked_note_ids'),
        element_check=lambda note_id: isinstance(note_id, str) and bool(note_id))

    # One guarded date drives all four date-derived fields, so due_date,
    # due_display, days_remaining and urgency_band cannot contradict each other.
    due = safe_date(item.get('due_date'))
    start = safe_date(item.get('start_date'))
    # Re-serialised from the parsed date rather than echoed as it arrived, so
    # the string the client receives is always the one safe_date could read.
    view['due_date'] = due.isoformat() if due is not None else None
    view['start_date'] = start.isoformat() if start is not None else None
    # Formatted server-side, once, so every screen shows the identical string
    # whatever the browser's locale thinks 03/04 means (NFR08). '' rather than
    # None because the client prints this straight into a label.
    view['due_display'] = _format_date_au(due) if due is not None else ''

    days = _days_remaining(due, today)
    view['days_remaining'] = days
    # 'distant' is the deliberate answer for a row with no countdown, and it is
    # the calm band on purpose: a card whose date could not be read, or was too
    # implausible to count from, should look unremarkable rather than alarming.
    # The keys are always SET, never omitted, because the client reads all four
    # date fields unconditionally.
    view['urgency_band'] = _urgency_band(days) if days is not None else 'distant'
    return view


def _build_calendar(year: int, month: int, decorated: list) -> dict:
    """Build the calendar-grid payload from already-guarded assessment dicts.

    FR08's month grid: seven columns, Monday first, with each day's assessments
    attached and a colour for the day taken from the most urgent thing on it
    (FR21). Pure function — no database read — so the whole grid can be tested
    from a list of dicts.

    THE GRID IS NOT ALWAYS 42 CELLS. calendar.monthcalendar() returns whole
    Monday-to-Sunday weeks covering the month, which is FOUR, FIVE or SIX rows
    depending on where the month falls: exactly four for a 28-day February that
    begins on a Monday, six for a 31-day month beginning late in the week. Days
    outside the month are 0, and the client draws a blank square for them. Any
    code that assumes six rows — or that indexes the grid by (week * 7 + weekday)
    against a fixed 42 — is wrong for a real calendar year.

    Args:
        year: four-digit year, already bounded by _parse_month.
        month: 1-12, already bounded by _parse_month. The pair goes straight to
            calendar.monthcalendar(), which raises on a year outside
            datetime's range — which is why the bounding is not optional.
        decorated: assessment dicts already through _safe_assessment_view, so
            each carries an ISO 'due_date' (or None), 'days_remaining' (int or
            None) and 'urgency_band'. This is the WHOLE unfiltered set, not the
            filtered list: the calendar deliberately shows everything due in the
            month, so a filter narrowing the list beside it cannot hide a
            deadline from the grid.

    Returns:
        {'year':         int, echoed so the client titles the grid from the
                         payload rather than from what it asked for,
         'month':        int 1-12, same reason,
         'weeks':        [[int x 7], ...] — 4, 5 or 6 rows; 0 is a square
                         outside the month,
         'day_buckets':  {'<day>': [assessment, ...]} — what the day dialog
                         lists when a square is clicked,
         'cell_colours': {'<day>': urgency band} — the tint for the square}
        get_dashboard_data adds one more key, 'exam_days', after this returns.

        day_buckets / cell_colours are keyed by STR(day) — Anvil refuses to
        serialize dicts with non-string keys ("Cannot serialize dictionaries with
        keys that aren't strings"). The client's _cell() helper looks up str keys.

    Raises:
        Nothing, given a year/month _parse_month produced.
    """
    weeks = _calendar.monthcalendar(year, month)
    # 1. Bucket the month's assessments by day. One pass over every row rather
    #    than a per-day query: `decorated` is already in memory, and 31 queries
    #    to build one grid is exactly what the single-round-trip design (NFR01)
    #    exists to avoid.
    day_buckets = {}
    for a in decorated:
        # safe_date rather than fromisoformat: this helper stays safe even if a
        # future caller hands it a dict that has not been through the view guard.
        d = safe_date(a.get('due_date'))
        if d is None:
            # No readable due date, so there is no square to put it on. It is
            # still in the list panel — this drops it from the GRID, not the app.
            continue
        # Year as well as month, or last November's assessments would land on
        # this November's grid.
        if d.year == year and d.month == month:
            # The whole assessment dict is stored, not just its title, because
            # the day dialog draws a full card per row and would otherwise need
            # a second lookup for data the payload already carries.
            day_buckets.setdefault(str(d.day), []).append(a)

    # 2. One colour per day, from the MOST urgent thing on it. Derived from the
    #    buckets rather than computed alongside them, so a day's colour is
    #    decided once its full contents are known — the most urgent item may be
    #    the last one added.
    cell_colours = {}
    for day_key, items in day_buckets.items():
        # Smallest days_remaining = most urgent, because the number counts DOWN
        # and goes negative once overdue. Rows with no countdown are filtered
        # out here rather than treated as 0, which would paint an unreadable
        # date as if it were due today.
        days = [it.get('days_remaining') for it in items
                if it.get('days_remaining') is not None]
        # min() only on a non-empty list: a day holding nothing but rows whose
        # countdown was suppressed still needs a colour, and 'distant' is it.
        cell_colours[day_key] = _urgency_band(min(days)) if days else 'distant'

    # 3. The colour is decided HERE, not on the client, so the grid and the cards
    #    beside it can never band the same assessment differently. The client is
    #    sent a band NAME and maps it to a stylesheet role, which is what lets the
    #    actual colour follow the light/dark theme.
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
    under-two-seconds render budget. client_code/DashboardForm makes no other
    read call, so the dict returned here is the entire contents of that screen;
    the comment block at the top of that form documents the same payload from the
    consumer's side, and the two are meant to be read together.

    Args:
        month: 'YYYY-MM', the month the calendar should draw. None on a first
            load, which shows the student's current month. Anything unusable
            degrades the same way — see _parse_month.
        filters: FR06's filters for the LIST panel only, never the calendar.
            {'subjects': [str], 'types': [str], 'statuses': [str],
            'show_completed': bool, 'month': 'YYYY-MM'}; every key optional, all
            of them whitelisted and cleaned by _safe_filters first.
        sort: FR07's sort for the list panel. {'by': one of ALLOWED_SORT_KEYS,
            'direction': 'asc'|'desc'}; both optional, whitelisted inside
            _list_assessments_impl, defaulting to due_date ascending.

    Returns:
        A plain dict of seven keys — three panels, plus the four values the
        surrounding chrome needs:

        today            'YYYY-MM-DD' — the student's today in THEIR timezone.
                         The form keeps it only to mark today's calendar cell,
                         but it is what every countdown below was measured from.
        assessment_list  [assessment, ...] — the LEFT panel. Already filtered and
                         sorted server-side to match what was asked for; the form
                         renders it in the order given and never re-sorts, so the
                         screen always matches the request (FR06, FR07).
        calendar         the MIDDLE panel's grid — {'year', 'month', 'weeks',
                         'day_buckets', 'cell_colours', 'exam_days'}. See
                         _build_calendar for the first five (note 'weeks' is 4, 5
                         or 6 rows, not always 6) and exams._get_exam_days_for_month
                         for the last (FR08, FR21, spec §13).
        upcoming         [assessment, ...] — the RIGHT sidebar. Not completed,
                         due within the next 30 days, soonest first (FR09).
        subjects         [str, ...] — fills the subject filter dropdown. The
                         student's locked subjects first, then any subject only
                         older rows still use. Every entry is a value
                         _safe_filters would accept back, so the dropdown cannot
                         offer a choice the filter would silently drop.
        next_exam        the soonest unfinished VCE written paper, or None —
                         {'subject', 'paper', 'date', 'start', 'end',
                         'days_remaining', 'urgency_band'}. Draws the countdown
                         chip (spec §13).
        settings         the user_settings dict from notes._settings_row_to_dict.
                         The form reads only 'school_terms', to decide whether
                         the FR15 tip banner is still worth showing — the rest is
                         sent because it is one dict either way.

        An `assessment` anywhere above is a dict built by _safe_assessment_view.
        They are separate objects — the list panel comes from its own query — but
        one row appearing in the list, a day bucket and the sidebar carries
        identical values in all three, because the one guard produced all three.

        Reads `assessments` (twice — see the comment on the unfiltered read) and
        `user_settings`, both scoped to the caller. Writes nothing, except the
        defaults row _get_or_create_settings inserts on a first ever call.

    Raises:
        anvil.users.AuthenticationFailed — nobody signed in (_require_user).
        ValueError — `filters` was not a dict, or a list filter was not a list
        (_safe_filters). Both reach the form's error banner.
    """
    # 1. Identity first, before an argument is examined or a table is touched
    #    (_auth.py rule 1). `user` is then the value every query below is scoped
    #    on, which is how NFR03 is satisfied on this screen.
    user = _require_user()
    settings = _get_or_create_settings(user)
    # ONE today, computed once in the student's timezone and threaded through
    # every helper below. If each panel worked out its own, a request that
    # straddled midnight in Melbourne could band the same assessment two ways.
    today = _user_today(settings)

    # 2. Guard everything the client sent before it reaches a query or the
    #    calendar. All three arguments are cleaned here, at the top, so nothing
    #    downstream has to wonder whether a value has been checked yet.
    if not isinstance(sort, dict):
        # _list_assessments_impl whitelists sort['by'] itself, but it calls .get()
        # on this value first, which raises on a string or a list.
        sort = {}
    clean_filters = _safe_filters(filters, today)
    year, mon = _parse_month(month, today)

    # 3. Panel 1: the filtered/sorted assessment list. This is the only panel the
    #    filters touch — the calendar and the sidebar deliberately show
    #    everything, so narrowing the list cannot hide a deadline elsewhere on
    #    the screen.
    assessment_list = [
        _safe_assessment_view(a, today)
        for a in _list_assessments_impl(user, settings, clean_filters, sort)
    ]

    # 4. One unfiltered read powers the calendar, upcoming sidebar and subject
    #    set. It is a SECOND query rather than a re-use of the list above,
    #    because that list has been narrowed by the filters and these three
    #    panels must not be. Two scoped queries is still one round trip from the
    #    browser, which is the cost NFR01 actually cares about. `user=user` is
    #    the scoping NFR03 requires; no by-id path is involved, so no
    #    _own_or_raise is needed here.
    all_rows = app_tables.assessments.search(user=user)
    all_decorated = [
        _safe_assessment_view(_decorate(_row_to_dict(r), today), today)
        for r in all_rows
    ]

    # 5. Panel 2: calendar grid for the requested month.
    calendar_payload = _build_calendar(year, mon, all_decorated)

    # 6. Panel 3: upcoming (not completed, due within 30 days), soonest first.
    #    Three conditions, each ruling out something different: a finished task
    #    is not upcoming; a row whose countdown was suppressed has no place on a
    #    30-day list; and the 0 lower bound leaves overdue work OUT of this
    #    sidebar, because it belongs in the list panel painted red rather than
    #    under a heading that says what is coming.
    upcoming = [
        a for a in all_decorated
        if a.get('status') != STATUS_COMPLETED
        and a.get('days_remaining') is not None
        and 0 <= a['days_remaining'] <= _UPCOMING_WINDOW_DAYS
    ]
    # Sorted on the ISO date STRING, which is safe because 'YYYY-MM-DD' is
    # zero-padded and so sorts chronologically as text. `or ''` cannot fire in
    # practice — a row with no date was filtered out above by its missing
    # days_remaining — but it keeps the key total, so a future edit to the filter
    # cannot turn this line into a TypeError comparing None with str.
    upcoming.sort(key=lambda a: a.get('due_date') or '')

    # 7. Filter dropdown: the student's locked subjects first (spec §11), then any
    #    legacy data subjects not in that list (so old rows stay filterable). Only
    #    subjects _safe_filters would accept are offered, so the dropdown can never
    #    present a choice the filter would silently drop.
    #
    #    `locked` is the student's own program, in the order they saved it, so the
    #    subjects they actually study sit at the top of the dropdown rather than
    #    alphabetically among renamed studies they dropped two years ago.
    #    `data_subjects` is the set actually present in their rows, sorted for a
    #    stable order — a set has none, and a dropdown that reshuffled itself
    #    between loads would be unusable. The de-duplicating comprehension on the
    #    last line is what stops a locked subject appearing twice.
    locked = _get_exam_subjects(settings)
    data_subjects = sorted({a['subject'] for a in all_decorated
                            if a.get('subject') in _FILTERABLE_SUBJECTS})
    subjects = list(locked) + [s for s in data_subjects if s not in locked]

    # 8. Exam overlay (spec §13): flag this month's exam days on the calendar and
    #    surface the next-exam countdown chip. next_exam is None when the student has
    #    no examinable subjects or every paper is over; the client draws no chip.
    #
    #    exams.py's helpers are imported and called directly rather than going
    #    through get_exam_timetable, so this overlay is computed by exactly the
    #    code the Exams page uses and the two cannot disagree about which paper is
    #    next — and it costs no second round trip (NFR01). _user_now, not just
    #    `today`, because the clock is what marks a paper sat this morning 'done'.
    #
    #    exam_days is attached to the calendar dict AFTER _build_calendar returns,
    #    which keeps that function pure and knowing nothing about exams.
    user_exams = _build_exams_for_subjects(locked, today, _user_now(settings))
    calendar_payload['exam_days'] = _get_exam_days_for_month(user_exams, year, mon)

    # 9. Assembled last, from values already computed, so the payload reads as a
    #    list of what the screen contains rather than as work still being done.
    return {
        'today': today.isoformat(),
        'assessment_list': assessment_list,
        'calendar': calendar_payload,
        'upcoming': upcoming,
        'subjects': subjects,
        'next_exam': _find_next_exam(user_exams),
        'settings': _settings_row_to_dict(settings),
    }
