import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Date/time helpers for user-local time handling.

The Anvil server runs in UTC, and Melbourne sits 10-11 hours AHEAD of it, so
through the whole Melbourne morning the server's own date is still yesterday's.
Every "today" and every piece of date arithmetic therefore has to be done in the
student's local timezone: measured against the server's date instead, an
assessment due today in Melbourne reads as due tomorrow, and every days-remaining
count and urgency band comes out one day too relaxed. See IMPLEMENTATION_SPEC.md
section 2 (server_code/_datetime.py).

Defines, in the order they appear:
  _get_tz(name)             resolve an IANA name through whichever timezone
                            backend this runtime has (zoneinfo, else pytz).
  _DEFAULT_TZ               the app-wide fallback zone, Australia/Melbourne.
  _safe_timezone(stored)    the safe_* read-guard for user_settings.timezone —
                            degrades to _DEFAULT_TZ instead of raising.
  _user_now(settings_row)   timezone-aware now in the student's local zone.
  _user_today(settings_row) the date half of the above.
  _format_date_au(d)        'DD MMM YYYY' for display (NFR08).
  _urgency_band(days)       days-until-due -> band name (FR21), added in the
                            Assessments slice (§10 step 2) alongside
                            _constants.URGENCY_THRESHOLDS, its first consumer.

This module deliberately imports no other DotPoint module except _constants —
only the standard library, and pytz on the older runtime the fallback below
covers. That is why the two guards it needs — _safe_timezone here and the column
read inside _user_now — are written out rather than imported from _validation.py
and notes.py. _validation imports _get_tz and _safe_timezone from here, so
importing it back would be a circular import; notes.py is a feature module that
sits above this one, so importing it would invert the layering even though
nothing today would actually cycle.
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
        """Resolve an IANA name to a tzinfo. Raises on a name the tz database
        does not hold — which is what _safe_timezone below relies on."""
        return _ZoneInfo(name)
except ImportError:
    import pytz

    def _get_tz(name: str):
        """The pytz spelling of the same thing, for the older server image. Also
        raises on an unknown name, so callers cannot tell the two apart."""
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
        # The same guard notes._row_value() applies, inlined for the layering reason
        # in the module docstring: a settings row written before the timezone column
        # was added raises on the lookup rather than returning None, and this
        # function is on the hot path of every screen.
        try:
            stored_name = user_settings_row['timezone']
        except Exception:
            stored_name = None
        tz_name = _safe_timezone(stored_name)
    return datetime.datetime.now(_get_tz(tz_name))


def _user_today(user_settings_row) -> datetime.date:
    """Today's date in the user's local timezone.

    The workhorse of the pair: every days-remaining figure in the app is
    (due_date - _user_today(settings)).days, so this is the single anchor the
    urgency bands, the dashboard counts and the reminder dispatcher all measure
    from. Takes the same possibly-None settings row as _user_now.

    Calling .date() on the timezone-aware datetime yields the LOCAL calendar day,
    which is the whole reason for going through _user_now: datetime.date.today()
    on the Anvil server would give the UTC day, which through the entire Melbourne
    morning is still yesterday's.
    """
    return _user_now(user_settings_row).date()


def _format_date_au(d: datetime.date) -> str:
    """Format a date as 'DD MMM YYYY' (NFR08), e.g. '15 Mar 2026'.

    Every user-facing date in the app goes through here, server-side, so the
    browser's own locale can never turn 03/04 into April 3rd on one machine and
    March 4th on another — which is the ambiguity NFR08 exists to remove.

    Locale-independent: %b yields the English month abbreviation under the
    standard C locale, which Anvil's runtime uses. %d is zero-padded, so the
    5th of March reads '05 Mar 2026' — two digits, as "DD" specifies.

    Takes a datetime.date (a datetime works too, and its time is ignored).
    Callers must not pass None; the ones that can hold a missing date test for
    it first, because there is no sensible date string for "no date".
    """
    return d.strftime('%d %b %Y')


def _urgency_band(days_remaining: int) -> str:
    """Map days-until-due to an urgency band name (FR21).

    `days_remaining` is (due_date - today).days in the STUDENT's timezone, so it is
    signed: negative is overdue, 0 is due today, positive is days still to go.

    Returns one of the four band names 'overdue' / 'today' / 'soon' / 'distant'. The
    server's job is only to say WHICH band an assessment is in; the client turns the
    name into a stylesheet role (client_code/common.band_role), so the colour can
    follow the light/dark theme. That is also why a name is returned rather than a
    number the client would have to re-threshold.

    Walks _constants.URGENCY_THRESHOLDS in order and returns the first band whose
    threshold is >= days_remaining, so the ordering of that table IS the rule:
    overdue, then today, then soon, then distant. Note that the band NAMED 'today'
    covers 0 to 3 days, not just 0 — FR21 puts "today or within 3 days" in one
    colour, so the name is the band's headline case rather than its whole range.

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
