import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Date/time helpers for user-local time handling.

Defines: _user_now(settings), _user_today(settings), _format_date_au(d).

The Anvil server runs in UTC; all "today" / date-math must be done in the
user's local timezone. See IMPLEMENTATION_SPEC.md section 2 (server_code/_datetime.py).

Also defines: _urgency_band(days_remaining) — added in the Assessments slice
(§10 step 2) alongside _constants.URGENCY_THRESHOLDS, its first consumer.
"""

import datetime
from zoneinfo import ZoneInfo

from ._constants import URGENCY_THRESHOLDS

# Pending Decision 2 (A): the user's timezone is stored per-user in
# user_settings.timezone. This is the fallback when the settings row, or its
# timezone value, is absent.
_DEFAULT_TZ = 'Australia/Melbourne'


def _user_now(user_settings_row) -> datetime.datetime:
    """Timezone-aware 'now' in the user's local timezone.

    `user_settings_row` may be None (falls back to the default timezone).
    """
    tz_name = _DEFAULT_TZ
    if user_settings_row is not None and user_settings_row['timezone']:
        tz_name = user_settings_row['timezone']
    return datetime.datetime.now(ZoneInfo(tz_name))


def _user_today(user_settings_row) -> datetime.date:
    """Today's date in the user's local timezone."""
    return _user_now(user_settings_row).date()


def _format_date_au(d: datetime.date) -> str:
    """Format a date as 'DD MMM YYYY' (NFR08), e.g. '15 Mar 2026'.

    Locale-independent: %b yields the English month abbreviation under the
    standard C locale, which Anvil's runtime uses.
    """
    return d.strftime('%d %b %Y')


def _urgency_band(days_remaining: int) -> str:
    """Map days-until-due to an urgency band name (FR21).

    Walks _constants.URGENCY_THRESHOLDS in order and returns the first band
    whose threshold is >= days_remaining. The final (9999, 'distant') entry
    guarantees a match for any input.
    """
    for threshold, band in URGENCY_THRESHOLDS:
        if days_remaining <= threshold:
            return band
    return URGENCY_THRESHOLDS[-1][1]
