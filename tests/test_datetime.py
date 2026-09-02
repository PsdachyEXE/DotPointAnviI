"""Proves the user-local date helpers survive a damaged database, and band correctly.

_user_now() is called by every screen in the app, so it is the single function whose
failure is total: before the guard added here, one unresolvable timezone string in
one settings row raised on every page load, INCLUDING the Settings page that is the
only place the student could correct it. The first suite is that regression.
"""

from .harness import load_server_code, make_user, make_settings
from . import anvil_stub

load_server_code()

import datetime

from server_code import _datetime
from server_code._constants import URGENCY_THRESHOLDS


class _RowWithoutTimezoneColumn(object):
    """Stands in for a settings row written before the timezone column existed.

    Anvil raises when a column that the table does not define is read, so this raises
    too — a dict returning None would not reproduce the failure being guarded against.
    """

    def __getitem__(self, column):
        raise KeyError('no column %r (pre-migration row)' % column)


def suite_timezone_read_guard(results):
    """A damaged user_settings.timezone must degrade, never take the app down."""
    user = make_user()

    # The regression. 'Australia/Melbourn' is a plausible typo, passes any pattern
    # check, and is fatal to ZoneInfo/pytz.
    settings = make_settings(user, timezone='Australia/Melbourn')
    results.does_not_raise(lambda: _datetime._user_now(settings),
                           'an unresolvable stored timezone does not raise')
    results.does_not_raise(lambda: _datetime._user_today(settings),
                           'and _user_today survives it too')

    # Whatever is in the cell, the app still gets a usable "now".
    for bad_value in ('Australia/Melbourn', '', None, 12345, 'Mars/Olympus_Mons', []):
        row = anvil_stub.app_tables.user_settings.add_row(
            user=user, theme='light', default_reminder_days=[7, 2],
            notifications_enabled=True, school_year=2026, school_terms=[],
            timezone=bad_value, subjects=[])
        results.does_not_raise(lambda r=row: _datetime._user_now(r),
                               'stored timezone %r degrades instead of raising' % (bad_value,))
        row.delete()

    # A settings row predating the column raises on the lookup itself.
    results.does_not_raise(lambda: _datetime._user_now(_RowWithoutTimezoneColumn()),
                           'a pre-migration row missing the column degrades')

    # No settings row at all — a user who has signed up but never opened Settings.
    results.does_not_raise(lambda: _datetime._user_now(None),
                           'a missing settings row falls back to the app default')

    # And a GOOD value must still be honoured — the guard must not flatten everyone
    # to Melbourne, or a Perth student's "today" would be wrong for two hours a day.
    results.equal(_datetime._safe_timezone('Australia/Perth'), 'Australia/Perth',
                  'a valid stored timezone is still used')
    results.equal(_datetime._safe_timezone('Australia/Melbourn'), 'Australia/Melbourne',
                  'an invalid one falls back to the app default')

    perth = anvil_stub.app_tables.user_settings.add_row(
        user=user, theme='light', default_reminder_days=[7, 2],
        notifications_enabled=True, school_year=2026, school_terms=[],
        timezone='Australia/Perth', subjects=[])
    results.ok(_datetime._user_now(perth).utcoffset() is not None,
               'the returned datetime is timezone-aware, not naive')


def suite_date_display(results):
    """NFR08: every user-facing date renders as DD MMM YYYY, whatever the locale."""
    results.equal(_datetime._format_date_au(datetime.date(2026, 3, 21)), '21 Mar 2026',
                  'a date renders as DD MMM YYYY')
    results.equal(_datetime._format_date_au(datetime.date(2026, 12, 1)), '01 Dec 2026',
                  'the day is zero-padded')
    # The month is a WORD specifically so DD/MM and MM/DD can never be confused —
    # the client asked for this directly (SRS follow-up question 5).
    results.ok(not _datetime._format_date_au(datetime.date(2026, 5, 6))[3].isdigit(),
               'the month is a word, not a number')


def suite_urgency_bands(results):
    """FR21: days-remaining maps to a band name, and the table's order is the rule."""
    results.equal(_datetime._urgency_band(-1), 'overdue', 'yesterday is overdue')
    results.equal(_datetime._urgency_band(-500), 'overdue', 'long past is still overdue')
    results.equal(_datetime._urgency_band(0), 'today', 'today is today')

    # Every threshold boundary, derived from the table rather than hardcoded here, so
    # this suite keeps testing the real rule if the thresholds are ever retuned.
    for threshold, band in URGENCY_THRESHOLDS:
        if threshold < 9000:
            results.equal(_datetime._urgency_band(threshold), band,
                          'the boundary day %d is in band %r' % (threshold, band))

    # Past the end of the table: must still answer, because the caller is choosing a
    # colour and has nothing to do with an exception.
    results.does_not_raise(lambda: _datetime._urgency_band(99999),
                           'a date beyond every threshold still returns a band')
    results.equal(_datetime._urgency_band(99999), URGENCY_THRESHOLDS[-1][1],
                  'and that band is the least urgent one')


SUITES = [
    ('timezone read guard', suite_timezone_read_guard),
    ('date display (NFR08)', suite_date_display),
    ('urgency bands (FR21)', suite_urgency_bands),
]
