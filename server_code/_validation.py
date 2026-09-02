import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Shared input-validation helpers for every server module (SAT criterion 7.3).

WHY THIS MODULE EXISTS
----------------------
Before this module the app validated inputs with hand-written `if` blocks inside
each callable. That worked, but it produced three problems the marking rubric names
directly: the same field was checked slightly differently on different write paths
("no inconsistencies are present"), values read back OUT of the database were barely
checked at all, and the error text was written for a developer rather than for the
student who has to read it.

Centralising the checks here fixes all three at once: one rule per concept, applied
identically everywhere, with one student-readable message per rule.

THE TWO FAMILIES — this is the important idea in this file
----------------------------------------------------------
The teacher's brief requires guarding "all inputs from the UI, AS WELL AS FROM THE
DATABASE". Those two jobs need OPPOSITE behaviour, and which family a check belongs
to is settled by one question: is there a person present who can fix this?

  require_*  — for data ARRIVING (a form submission, a server argument, an imported
               file). Someone is at the keyboard, so STOPPING is the helpful answer:
               they are told what is wrong and can correct it. These raise ValueError
               carrying a sentence written for the student, and every check runs
               before any write, so a rejected record leaves the database untouched.

  safe_*     — for data LEAVING the database (a row the app wrote earlier, possibly
               by an older version of the app, possibly hand-edited in the Anvil Data
               Tables console). There is nobody to correct anything, so raising would
               turn one damaged cell into a blank screen: refusing to render is worse
               than degrading. These NEVER raise — they coerce the value to a
               documented, safe default and carry on.

The two are not interchangeable, and swapping them fails in both directions.
safe_* on the way IN would quietly store a wrong value the student was standing right
there to fix. require_* on the way OUT would let one corrupt cell take down a screen —
or, in safe_timezone()'s case, the entire app, including the Settings page that is the
only place the bad value can be repaired.

Every rule the rubric names is represented:
  existence   -> require_present, require_text
  type        -> require_number, require_int, require_list, require_bool,
                 require_text, require_choice
  range       -> require_number_in_range, require_int_in_range, require_choice,
                 require_text (max_length)
  format      -> require_date, require_iso_date_text, require_email,
                 require_timezone, round_percentage
  reasonable/ -> require_not_after, require_within_horizon, require_complete_record
  completeness

and every column read back out of a table has a degrading twin:
  safe_text, safe_bool, safe_number, safe_choice, safe_list, safe_date,
  safe_timezone, plus the element predicates safe_list takes — is_positive_int
  and is_valid_reminder_day.

MESSAGE STYLE (applies to every string in this file)
----------------------------------------------------
A message is shown to a Year 12 student mid-task, so it: names the field as the UI
labels it, says what is wrong, and says what to do instead. Sentence case, full stop,
no Python type names, no field identifiers like `due_date`.

See docs/VALIDATION.md for the field-by-field table this module implements.
"""

import datetime
import re

from ._datetime import _get_tz, _safe_timezone
from ._constants import MIN_REMINDER_DAY, MAX_REMINDER_DAY


# --- format patterns -------------------------------------------------------

# Deliberately permissive: the job is to catch a typo like "will@" or "will example
# .com", not to police the RFC. Anything that has one @, some text either side, and a
# dot in the domain is accepted; anything else is almost certainly a mistake.
_EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

# How far from today a due date may sit. A VCE assessment is set within the school
# year, so a date outside this window is a mistyped year (2062 for 2026) rather than
# a real plan. Generous on both sides so a legitimately old record still imports.
_PAST_HORIZON_DAYS = 366 * 5
_FUTURE_HORIZON_DAYS = 366 * 5

# Anvil stores weight as a number column; percentages are quoted to at most two
# decimal places so "25.333333333333336" never reaches the student's screen.
_WEIGHT_DECIMAL_PLACES = 2


# ===========================================================================
# require_* — data arriving from a person. Raise ValueError on anything wrong.
# ===========================================================================

def require_present(value, field_label):
    """Existence check: the value must be supplied at all.

    `field_label` is the field's name AS THE UI SHOWS IT ("Due date", not
    "due_date") because the message goes straight to the student. Returns the value
    so the check can be used inline.
    """
    # None and empty string both mean "the student left this blank". Zero and False
    # are legitimate values for some fields, so they must NOT be treated as missing —
    # this is why the test is explicit rather than a plain truthiness check.
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError('%s is required.' % field_label)
    return value


def require_text(value, field_label, max_length, allow_blank=False):
    """Existence + type + range for a text field, returned stripped.

    Combines three of the rubric's five checks because they are inseparable for text:
    a field that is absent, a field that is a number, and a field that is 5,000
    characters long all fail for the same reason from the student's point of view.
    """
    # None is folded into the empty string rather than rejected as a type error: an
    # omitted argument and a cleared TextBox both mean "blank", and blankness is
    # reported below with the right message ("... is required") instead of a
    # confusing "must be text". Any OTHER non-string is a genuine wrong shape from an
    # import file or a client bug, and .strip() would raise a bare AttributeError.
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ValueError('%s must be text.' % field_label)

    stripped = value.strip()

    # Existence: checked on the STRIPPED value, so a box holding only spaces counts
    # as blank — which is what the student sees.
    if not stripped and not allow_blank:
        raise ValueError('%s is required.' % field_label)

    # Range: the cap protects the dashboard layout and the Anvil row size. The message
    # quotes both the limit and the actual length so the student knows how much to cut.
    if len(stripped) > max_length:
        raise ValueError(
            '%s is too long — keep it to %d characters or fewer (currently %d).'
            % (field_label, max_length, len(stripped)))
    return stripped


def require_choice(value, allowed, field_label):
    """Type/range check for an enum-like field: the value must be one of `allowed`.

    `allowed` is any container of permitted values. The message lists them, because a
    student who sees "must be one of ..." can fix the input; one who sees "invalid"
    cannot.
    """
    if value not in allowed:
        # sorted() is not cosmetic. `allowed` is usually a frozenset (VALID_TYPES,
        # VALID_STATUSES), which has no reliable iteration order, so without it the
        # same mistake could list the options in a different order on each call —
        # confusing for the student and impossible to assert on in a test.
        raise ValueError(
            'That is not a valid %s. Choose one of: %s.'
            % (field_label.lower(), ', '.join(sorted(str(a) for a in allowed))))
    return value


def require_number(value, field_label):
    """Type check for a numeric field, returned as a float.

    Accepts a number or a numeric string (the client sends TextBox text). Rejects
    booleans explicitly: in Python `bool` subclasses `int`, so `True` would otherwise
    sail through as the number 1.
    """
    # bool is tested first and on its own: it subclasses int, so the isinstance test
    # below would accept True and hand back the weight 1.0.
    if isinstance(value, bool):
        raise ValueError('%s must be a number.' % field_label)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            # Swallowed on purpose, and NOT re-raised here: falling through to the
            # single raise at the bottom means "25kg", None and {} all produce the
            # one message, so there is only ever one sentence to keep student-readable.
            pass
    raise ValueError('%s must be a number.' % field_label)


def require_number_in_range(value, field_label, minimum, maximum):
    """Type + range check: a number, and inside [minimum, maximum] inclusive."""
    number = require_number(value, field_label)
    if not (minimum <= number <= maximum):
        # %g keeps the bounds readable — "0 and 100", not "0.0 and 100.0".
        raise ValueError(
            '%s must be between %g and %g (you entered %g).'
            % (field_label, minimum, maximum, number))
    return number


def require_int(value, field_label):
    """Type check for a whole number. Rejects bool for the same reason as above."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError('%s must be a whole number.' % field_label)
    return value


def require_int_in_range(value, field_label, minimum, maximum):
    """Type + range check for a whole number inside [minimum, maximum] inclusive."""
    number = require_int(value, field_label)
    if not (minimum <= number <= maximum):
        raise ValueError(
            '%s must be between %d and %d (you entered %d).'
            % (field_label, minimum, maximum, number))
    return number


def require_list(value, field_label, allow_empty=True):
    """Type check for a list. Anvil simpleObject columns can hold anything at all."""
    if not isinstance(value, list):
        raise ValueError('%s must be a list of values.' % field_label)
    if not value and not allow_empty:
        raise ValueError('%s cannot be empty.' % field_label)
    return value


def require_bool(value, field_label):
    """Type check for a true/false setting."""
    if not isinstance(value, bool):
        raise ValueError('%s must be either on or off.' % field_label)
    return value


def require_date(value, field_label):
    """Format check for a date, returned as a `datetime.date`.

    Accepts a date, a datetime (Anvil sometimes hands one back), or an ISO
    'YYYY-MM-DD' string (which is what an export file and the JSON importer carry).
    Anything else is a format error, so the message says what shape is expected.
    """
    # datetime is checked BEFORE date: datetime.datetime subclasses datetime.date, so
    # testing date first would silently keep the time component.
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip())
        except (ValueError, TypeError):
            pass
    raise ValueError(
        '%s must be a real date in the form YYYY-MM-DD.' % field_label)


def require_iso_date_text(value, field_label):
    """Format check for a date that must STAY a 'YYYY-MM-DD' string.

    Used for `user_settings.school_terms`, whose dates live inside an Anvil
    simpleObject column and so cannot be stored as date objects.
    """
    if not isinstance(value, str):
        raise ValueError('%s must be a date in the form YYYY-MM-DD.' % field_label)
    try:
        datetime.date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(
            '%s must be a real date in the form YYYY-MM-DD (got "%s").'
            % (field_label, value))
    return value.strip()


def require_email(value, field_label='Email address'):
    """Existence + format check for an email address.

    The account IS the email address, so a typo here creates an account the student
    can never sign back into — worth catching before the account exists.
    """
    # 254 is the longest address a mail server is required to accept (RFC 5321), so
    # anything past it is a paste accident rather than a real address.
    text = require_text(value, field_label, max_length=254)
    if not _EMAIL_PATTERN.match(text):
        raise ValueError(
            'That does not look like an email address. '
            'Check it looks like name@example.com.')
    return text


def require_timezone(value, field_label='Timezone'):
    """Format check for an IANA timezone name, verified against the tz database.

    A pattern match is not enough — 'Australia/Melbourn' looks fine and is fatal, so
    the only honest check is to ask the timezone backend to resolve it.
    """
    # 64 is comfortably past the longest IANA name in use (about 30 characters), and
    # bounds the string before it is handed to the timezone backend.
    text = require_text(value, field_label, max_length=64)
    try:
        _get_tz(text)
    except Exception:
        # Deliberately broad: zoneinfo raises ZoneInfoNotFoundError and pytz raises
        # UnknownTimeZoneError, neither of which this module can name without
        # importing a backend it has decided not to depend on.
        raise ValueError(
            'That is not a timezone the system recognises. '
            'Use a name like Australia/Melbourne.')
    return text


def round_percentage(value):
    """Format a validated weight to the app's fixed 2 decimal places.

    Applied AFTER the range check so the stored value and the displayed value can
    never disagree — 25.333333333333336 becomes 25.33 once, at the point of writing,
    rather than being re-rounded differently by each screen that renders it.
    """
    if value is None:
        return None
    return round(float(value), _WEIGHT_DECIMAL_PLACES)


# ===========================================================================
# Reasonableness and completeness — the checks that look at a WHOLE record
# rather than one field. This is the half of rubric 7.3 that field-by-field
# validation cannot reach: every field below is individually valid.
# ===========================================================================

def require_not_after(earlier, later, earlier_label, later_label):
    """Reasonableness: `earlier` must not fall after `later`.

    Both may be None (an optional date is not an error); the check only runs when
    there are two real dates to compare.
    """
    if earlier is None or later is None:
        return
    if earlier > later:
        raise ValueError(
            '%s cannot be after %s. Check the two dates.'
            % (earlier_label, later_label))


def require_within_horizon(value, today, field_label):
    """Reasonableness: a date must sit within a plausible window around today.

    Catches the mistyped year — 2062 for 2026, or 0025 for 2025 — which passes every
    type and format check and then silently sorts to the end of the dashboard forever.
    """
    # Either date missing means there is nothing to measure against, which is not an
    # error — an optional due date and a caller that has no "today" both land here.
    if value is None or today is None:
        return value
    # Signed, so one subtraction answers both directions: positive is into the
    # future, negative is into the past. The two messages differ because "check the
    # year" means a different typo in each case.
    days_away = (value - today).days
    if days_away > _FUTURE_HORIZON_DAYS:
        raise ValueError(
            '%s is more than five years away (%s). Check the year.'
            % (field_label, value.strftime('%d %b %Y')))
    if days_away < -_PAST_HORIZON_DAYS:
        raise ValueError(
            '%s is more than five years ago (%s). Check the year.'
            % (field_label, value.strftime('%d %b %Y')))
    return value


def require_complete_record(record, required_fields, record_label):
    """Completeness: every field in `required_fields` must be present in `record`.

    `record` is a plain dict of the values being submitted. `required_fields` is a
    sequence of (key, label) pairs — the key to look up, and the label the UI uses for
    it — so the message can name fields the way the student sees them.

    Distinct from calling require_present on each field in turn, because it reports
    ALL the missing pieces in one message. A student fixing a bulk-import line should
    not have to submit four times to discover four omissions.
    """
    # A comprehension rather than a loop-and-raise for exactly that reason: it
    # collects every missing label first, and only then raises once. The blank-string
    # half of the test mirrors require_present — a box holding only spaces is missing.
    missing = [label for key, label in required_fields
               if record.get(key) is None
               or (isinstance(record.get(key), str) and not record[key].strip())]
    if missing:
        raise ValueError(
            '%s is incomplete — still needed: %s.'
            % (record_label, ', '.join(missing)))
    return record


# ===========================================================================
# safe_* — data LEAVING the database. These never raise; they degrade.
#
# Every function here answers the same question: "the value in this column is not
# what the app wrote — what is the least surprising thing to show the student?"
# ===========================================================================

def safe_text(stored_value, default=''):
    """Return stored text, or `default` when the cell holds anything else."""
    return stored_value if isinstance(stored_value, str) else default


def safe_bool(stored_value, default=False):
    """Return a stored true/false, or `default` when the cell is None or non-bool.

    Exists because two readers of `notifications_enabled` used to disagree: one
    treated an unset column as "off" and the other as "on", so the Settings page
    could show reminders switched off while the dispatcher kept emailing. Routing
    every read through here makes that disagreement impossible.
    """
    return stored_value if isinstance(stored_value, bool) else default


def safe_number(stored_value, default=None, minimum=None, maximum=None):
    """Return a stored number, or `default` if it is missing, mistyped or out of range.

    Range is enforced on READ as well as on write, because a value that predates the
    rule (or arrived through the Data Tables console) would otherwise flow into the
    dashboard and the reminder emails unchecked.
    """
    # bool is excluded before the numeric test for the same reason as require_number:
    # it subclasses int, so a stored True would be read back as a weight of 1.0.
    if isinstance(stored_value, bool) or not isinstance(stored_value, (int, float)):
        return default
    # A bound of None means "no bound", which is why each is tested for None rather
    # than defaulting to +/-infinity: most callers only constrain one end.
    if minimum is not None and stored_value < minimum:
        return default
    if maximum is not None and stored_value > maximum:
        return default
    return float(stored_value)


def safe_choice(stored_value, allowed, default):
    """Return the stored value if it is still a member of `allowed`, else `default`.

    Guards against an enum that has moved on: a row written under an older value set
    would otherwise be assigned to a dropdown that does not offer it, silently fall
    back to the first item, and be written back — quietly rewriting the student's data.
    """
    return stored_value if stored_value in allowed else default


def safe_list(stored_value, element_check=None, default=None):
    """Return a stored list with unusable elements dropped; never raises.

    Anvil simpleObject columns accept any JSON, so a console edit can leave a scalar,
    a dict, or a list with three good entries and one bad one where a clean list of
    ints belongs. Dropping the bad elements keeps the good ones usable — refusing the
    whole column would lose data the student can still see and fix.

    `element_check` is a predicate applied to each element; elements failing it are
    omitted. With no check, every element is kept.
    """
    # A mutable default cannot be written into the signature — one shared [] would be
    # handed to every caller, and the first one to append would change it for all of
    # them. None means "no default supplied" and the empty list is built per call.
    if default is None:
        default = []
    # Not a list at all (a bare 7 where [7] belongs, or a dict): there are no elements
    # to filter, so hand back a copy of the default.
    if not isinstance(stored_value, list):
        return list(default)
    # Every return is a NEW list, never the stored object itself. The value handed in
    # belongs to an Anvil row's cached cell, and a caller that sorted or appended to
    # it in place would be editing the database row's own copy.
    if element_check is None:
        return list(stored_value)
    # The whole point of the family: bad ELEMENTS are dropped and the good ones
    # survive, so [7, 'x', 2] still yields two usable reminder thresholds. Rejecting
    # the column outright would throw away data the student can still see and fix.
    return [element for element in stored_value if element_check(element)]


def safe_date(stored_value, default=None):
    """Return a stored date, or `default` when the cell holds anything else.

    The read-side twin of require_date, accepting the same three shapes: a date, a
    datetime (Anvil hands one back for a datetime column), or an ISO 'YYYY-MM-DD'
    string (which is what a hand-typed console edit leaves behind).
    """
    # datetime BEFORE date, exactly as in require_date: datetime.datetime subclasses
    # datetime.date, so testing date first would return the value unchanged with its
    # time component still attached, and (due_date - today).days would then be
    # comparing a datetime against a date and raise.
    if isinstance(stored_value, datetime.datetime):
        return stored_value.date()
    if isinstance(stored_value, datetime.date):
        return stored_value
    if isinstance(stored_value, str):
        try:
            return datetime.date.fromisoformat(stored_value.strip())
        except (ValueError, TypeError):
            return default
    return default


def safe_timezone(stored_value):
    """Return a stored timezone name only if the tz database still resolves it.

    THE most important function in this family. `_user_now()` is called by every
    screen, so an unresolvable stored timezone used to raise there and take the whole
    app down — including the Settings page that is the only way to correct it. Falling
    back to the app default keeps the student signed in and able to fix the value.

    Delegates to `_datetime._safe_timezone` so this rule is defined exactly once. It
    physically lives over there because _datetime cannot import this module without
    creating a cycle (this module needs _get_tz from it).
    """
    return _safe_timezone(stored_value)


def is_positive_int(value):
    """Element predicate for safe_list: a genuine positive whole number.

    Rejects bool, which subclasses int — a stored `True` would otherwise be read as
    the number 1 and fire a spurious "1 day before" reminder.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def is_valid_reminder_day(value):
    """Element predicate for safe_list: a reminder offset the app would still accept.

    The read-side twin of the MIN/MAX_REMINDER_DAY write rule. It lives here, rather
    than separately in each module that needs it, because three modules read a
    reminder-days column — assessments.py, notes.py and reminders.py — and if their
    read rules disagreed with the write rule (or with each other) a value could be
    refused on save yet still act on the student's data. That is the inconsistency
    rubric 7.3 is asking us to remove.

    Being stricter than is_positive_int matters: a row written before the upper bound
    existed can hold 999999, which made every assessment permanently "due soon" and
    emailed the student about all of them.
    """
    return (is_positive_int(value)
            and MIN_REMINDER_DAY <= value <= MAX_REMINDER_DAY)
