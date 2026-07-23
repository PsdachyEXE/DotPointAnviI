import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Dashboard aggregator: one round-trip payload for the all-in-view DashboardForm.

Exposes get_dashboard_data(month, filters, sort) as @anvil.server.callable
(NFR01: the whole dashboard is populated by a single server call). Combines the
filtered assessment list, a month calendar grid with per-day urgency colours
(FR08, FR21), an "upcoming" 30-day sidebar (FR09), and the distinct subject set
for the filter dropdown.

See IMPLEMENTATION_SPEC.md section 2 (server_code/dashboard.py).
"""

import anvil.server
import datetime
import calendar as _calendar

from ._auth import _require_user
from ._datetime import _user_today, _urgency_band
from .notes import _get_or_create_settings, _settings_row_to_dict
from .assessments import _list_assessments_impl, _row_to_dict, _decorate


def _build_calendar(year: int, month: int, decorated: list) -> dict:
    """Build the calendar-grid payload from already-decorated assessment dicts.

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
        due = a.get('due_date')
        if not due:
            continue
        d = datetime.date.fromisoformat(due)
        if d.year == year and d.month == month:
            day_buckets.setdefault(str(d.day), []).append(a)

    cell_colours = {}
    for day_key, items in day_buckets.items():
        days = [it['days_remaining'] for it in items if it.get('days_remaining') is not None]
        cell_colours[day_key] = _urgency_band(min(days)) if days else 'distant'

    return {
        'year': year,
        'month': month,
        'weeks': weeks,
        'day_buckets': day_buckets,
        'cell_colours': cell_colours,
    }


def _parse_month(month, today):
    """'YYYY-MM' -> (year, month); fall back to today's year/month.

    Validates the month is 1-12 so a malformed value can't reach
    calendar.monthcalendar and raise IllegalMonthError.
    """
    if month:
        try:
            year_s, month_s = month.split('-')
            year, mon = int(year_s), int(month_s)
            if 1 <= mon <= 12:
                return year, mon
        except (ValueError, TypeError, AttributeError):
            pass
    return today.year, today.month


@anvil.server.callable
def get_dashboard_data(month: str = None, filters: dict = None, sort: dict = None) -> dict:
    """Return the whole dashboard in one payload (NFR01)."""
    user = _require_user()
    settings = _get_or_create_settings(user)
    today = _user_today(settings)

    # Panel 1: the filtered/sorted assessment list.
    assessment_list = _list_assessments_impl(user, settings, filters, sort)

    # One unfiltered read powers the calendar, upcoming sidebar and subject set.
    all_rows = app_tables.assessments.search(user=user)
    all_decorated = [_decorate(_row_to_dict(r), today) for r in all_rows]

    # Panel 2: calendar grid for the requested month.
    year, mon = _parse_month(month, today)
    calendar_payload = _build_calendar(year, mon, all_decorated)

    # Panel 3: upcoming (not completed, due within 30 days), soonest first.
    upcoming = [
        a for a in all_decorated
        if a.get('status') != 'completed'
        and a.get('days_remaining') is not None
        and 0 <= a['days_remaining'] <= 30
    ]
    upcoming.sort(key=lambda a: a['due_date'])

    subjects = sorted({a['subject'] for a in all_decorated if a.get('subject')})

    return {
        'today': today.isoformat(),
        'assessment_list': assessment_list,
        'calendar': calendar_payload,
        'upcoming': upcoming,
        'subjects': subjects,
        'settings': _settings_row_to_dict(settings),
    }
