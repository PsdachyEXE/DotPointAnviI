import anvil.secrets
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

from ._constants import URGENCY_THRESHOLDS

# Timezone backend compat: zoneinfo is stdlib on Python 3.9+, but Anvil's
# default Full-Python-3 server image predates it (and the python310-full base
# image is not provisionable on this account). Fall back to pytz, which the
# Anvil server image bundles. Both raise on an unknown zone name.
try:
    from zoneinfo import ZoneInfo as _ZoneInfo

    def _get_tz(name: str):
        return _ZoneInfo(name)
except ImportError:
    import pytz

    def _get_tz(name: str):
        return pytz.timezone(name)

# Pending Decision 2 (A): the user's timezone is stored per-user in
# user_settings.timezone. This is the fallback when the settings row, or its
# timezone value, is absent.
_DEFAULT_TZ = 'Australia/Melbourne'


def _safe_timezone(stored_name) -> str:
    """Return a timezone name the tz database actually resolves, else the default.

    This is the `safe_*` read-guard for `user_settings.timezone` (SAT criterion 7.3,
    the "guard inputs from the database" limb). It lives here rather than in
    _validation.py only to avoid a circular import — _validation imports _get_tz from
    this module — and _validation.safe_timezone() is the same rule for every other
    caller.

    Why it has to exist: the value is validated when the student SAVES it, but the
    Anvil Data Tables console bypasses that path entirely, and an import whose
    settings patch failed can leave anything in the cell. _get_tz() raises on a name
    it cannot resolve, and _user_now() below is called by every screen in the app — so
    an unresolvable stored name used to take the whole app down, INCLUDING the
    Settings page that is the only place to correct it. Degrading to the default
    keeps the student signed in and able to fix it.
    """
    # Not a usable string at all (None on a pre-migration row, or a console edit that
    # left a number or a dict): fall straight back, nothing to try.
    if not isinstance(stored_name, str) or not stored_name.strip():
        return _DEFAULT_TZ
    try:
        _get_tz(stored_name.strip())
    except Exception:
        # Deliberately broad: zoneinfo raises ZoneInfoNotFoundError, pytz raises
        # UnknownTimeZoneError, and a corrupt value can raise neither. Whatever
        # comes back, the answer is the same — use the default rather than fail.
        return _DEFAULT_TZ
    return stored_name.strip()


def _user_now(user_settings_row) -> datetime.datetime:
    """Timezone-aware 'now' in the user's local timezone.

    `user_settings_row` may be None (a user whose settings row has not been created
    yet), in which case the app default applies. A row whose stored timezone is
    missing or unrecognised also falls back, via _safe_timezone() — this function is
    on the hot path of every screen and must never be the thing that fails.
    """
    tz_name = _DEFAULT_TZ
    if user_settings_row is not None:
        # _row_value-style guard: a settings row written before the timezone column
        # existed raises on the lookup rather than returning None.
        try:
            stored_name = user_settings_row['timezone']
        except Exception:
            stored_name = None
        tz_name = _safe_timezone(stored_name)
    return datetime.datetime.now(_get_tz(tz_name))


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

    Walks _constants.URGENCY_THRESHOLDS in order and returns the first band whose
    threshold is >= days_remaining, so the ordering of that table IS the rule:
    overdue, then today, then soon, then distant.

    The final entry's threshold (9999) covers every realistic input, so the return
    after the loop is a belt-and-braces fallback rather than dead code — it is what
    answers a due date more than 9,999 days out, which the five-year horizon check in
    _validation.require_within_horizon now rejects on the way in but which older rows
    may still contain.
    """
    for threshold, band in URGENCY_THRESHOLDS:
        # First match wins; the table is ordered nearest-deadline-first so an overdue
        # item can never be classified as merely "soon".
        if days_remaining <= threshold:
            return band
    # Past the end of the table: report the least-urgent band rather than raising,
    # because this value is only ever used to pick a colour.
    return URGENCY_THRESHOLDS[-1][1]
