"""Proves server_code/_validation.py enforces each check SAT criterion 7.3 names.

The rubric lists five classes of check — existence, type, range, format, and
reasonableness/completeness — so the suites below are organised under those exact
five headings, plus two more for the things the teacher's brief adds: that data read
back OUT of the database is guarded too, and that the messages a student sees are
meaningful.

Each rejection test asserts on the MESSAGE as well as the exception, because "raises
ValueError" is not the requirement; "tells the student what to fix" is.
"""

from .harness import load_server_code

load_server_code()

import datetime

from server_code import _validation as validation


TODAY = datetime.date(2026, 9, 2)


# --- existence -------------------------------------------------------------

def suite_existence(results):
    """A required field must be supplied, and whitespace does not count as supplied."""
    results.raises(ValueError, lambda: validation.require_present(None, 'Due date'),
                   'None is rejected as missing', message_contains='required')
    results.raises(ValueError, lambda: validation.require_text('', 'Title', 200),
                   'empty string is rejected as missing', message_contains='required')
    results.raises(ValueError, lambda: validation.require_text('   ', 'Title', 200),
                   'whitespace-only is rejected as missing', message_contains='required')

    # Zero and False are legitimate values, NOT absences. A truthiness check would get
    # this wrong, which is why require_present tests explicitly.
    results.equal(validation.require_present(0, 'Weight (%)'), 0,
                  'zero counts as present')
    results.equal(validation.require_present(False, 'Notifications'), False,
                  'False counts as present')

    # allow_blank is how an optional text field opts out of the existence check.
    results.equal(validation.require_text('', 'Description', 2000, allow_blank=True), '',
                  'allow_blank permits an empty optional field')

    results.equal(validation.require_text('  Methods SAC2  ', 'Title', 200), 'Methods SAC2',
                  'accepted text is returned stripped')


# --- type ------------------------------------------------------------------

def suite_type(results):
    """A value of the wrong shape is refused before it can reach the database."""
    results.raises(ValueError, lambda: validation.require_text(42, 'Title', 200),
                   'a number is not text', message_contains='must be text')
    results.raises(ValueError, lambda: validation.require_number('not a number', 'Weight (%)'),
                   'a word is not a number', message_contains='must be a number')
    results.raises(ValueError, lambda: validation.require_list('7', 'Reminder days'),
                   'a string is not a list', message_contains='must be a list')
    results.raises(ValueError, lambda: validation.require_bool('yes', 'Notifications'),
                   'a string is not a boolean', message_contains='on or off')

    # THE BOOL/INT TRAP. In Python `bool` subclasses `int`, so `isinstance(True, int)`
    # is True and an unguarded check lets True through as the number 1. That is not
    # academic here: a stored `[True]` in reminder_days would fire a "due tomorrow"
    # email for an assessment the student never set a 1-day reminder on.
    results.raises(ValueError, lambda: validation.require_number(True, 'Weight (%)'),
                   'True is not accepted as the number 1')
    results.raises(ValueError, lambda: validation.require_int(True, 'Reminder day'),
                   'True is not accepted as a whole number')
    results.ok(not validation.is_positive_int(True),
               'is_positive_int rejects True')
    results.ok(validation.is_positive_int(7),
               'is_positive_int accepts a genuine positive int')

    # A numeric string is accepted, because that is what a TextBox hands the server.
    results.equal(validation.require_number('25.5', 'Weight (%)'), 25.5,
                  'a numeric string is accepted from a text box')


# --- range -----------------------------------------------------------------

def suite_range(results):
    """A value of the right type can still be out of bounds."""
    results.equal(validation.require_number_in_range(25, 'Weight (%)', 0, 100), 25.0,
                  'a weight inside 0-100 is accepted')
    results.does_not_raise(
        lambda: validation.require_number_in_range(0, 'Weight (%)', 0, 100),
        'the lower bound itself is accepted')
    results.does_not_raise(
        lambda: validation.require_number_in_range(100, 'Weight (%)', 0, 100),
        'the upper bound itself is accepted')

    results.raises(ValueError,
                   lambda: validation.require_number_in_range(150, 'Weight (%)', 0, 100),
                   'a weight above 100 is rejected', message_contains='between 0 and 100')
    results.raises(ValueError,
                   lambda: validation.require_number_in_range(-1, 'Weight (%)', 0, 100),
                   'a negative weight is rejected', message_contains='between 0 and 100')

    # The message must quote what the student actually typed, so they can see the typo.
    try:
        validation.require_number_in_range(250, 'Weight (%)', 0, 100)
    except ValueError as error:
        results.ok('250' in str(error),
                   'the range message quotes the offending value back')

    results.raises(ValueError,
                   lambda: validation.require_text('x' * 201, 'Title', 200),
                   'a title over the cap is rejected', message_contains='too long')

    # The length message has to be actionable: both the limit and the actual length.
    try:
        validation.require_text('x' * 250, 'Title', 200)
    except ValueError as error:
        results.ok('200' in str(error) and '250' in str(error),
                   'the length message states both the limit and the actual length')

    results.raises(ValueError,
                   lambda: validation.require_int_in_range(999999, 'Reminder day', 1, 365),
                   'an unbounded reminder day is rejected',
                   message_contains='between 1 and 365')


# --- format ----------------------------------------------------------------

def suite_format(results):
    """A value in range can still be the wrong SHAPE — a date, an email, a timezone."""
    results.equal(validation.require_date('2026-05-22', 'Due date'),
                  datetime.date(2026, 5, 22), 'an ISO date string is parsed')
    results.equal(validation.require_date(datetime.date(2026, 5, 22), 'Due date'),
                  datetime.date(2026, 5, 22), 'a date passes through unchanged')

    # datetime must be narrowed to date, and must be tested BEFORE date: datetime
    # subclasses date, so checking date first would silently keep the time component.
    results.equal(validation.require_date(datetime.datetime(2026, 5, 22, 14, 30), 'Due date'),
                  datetime.date(2026, 5, 22), 'a datetime is narrowed to its date')

    results.raises(ValueError, lambda: validation.require_date('22/05/2026', 'Due date'),
                   'DD/MM/YYYY is rejected', message_contains='YYYY-MM-DD')
    results.raises(ValueError, lambda: validation.require_date('2026-13-01', 'Due date'),
                   'month 13 is rejected as not a real date')
    results.raises(ValueError, lambda: validation.require_date('2026-02-30', 'Due date'),
                   '30 February is rejected as not a real date')

    results.equal(validation.require_email('will@example.com'), 'will@example.com',
                  'a well-formed email is accepted')
    for bad_email in ('will@', '@example.com', 'will example.com', 'will@example'):
        results.raises(ValueError, lambda e=bad_email: validation.require_email(e),
                       'malformed email %r is rejected' % bad_email,
                       message_contains='email address')

    results.equal(validation.require_timezone('Australia/Melbourne'), 'Australia/Melbourne',
                  'a real IANA timezone is accepted')
    # A pattern match would pass this — only asking the tz database catches it, and an
    # unresolvable name here used to take the whole app down.
    results.raises(ValueError, lambda: validation.require_timezone('Australia/Melbourn'),
                   'a plausible-looking but unreal timezone is rejected',
                   message_contains='timezone')

    # Currency/percentage formatting: a weight is stored to a fixed 2dp so the stored
    # value and every rendering of it agree.
    results.equal(validation.round_percentage(25.333333333333336), 25.33,
                  'a weight is stored to 2 decimal places')
    results.equal(validation.round_percentage(None), None,
                  'an absent weight stays absent')


# --- reasonableness and completeness ---------------------------------------

def suite_reasonableness(results):
    """Every field individually valid, and the record still wrong as a whole.

    This is the half of the rubric's Very High wording that field-by-field validation
    cannot reach, and the half the project had none of before this module.
    """
    # Two perfectly valid dates in an impossible order.
    results.raises(
        ValueError,
        lambda: validation.require_not_after(
            datetime.date(2026, 6, 1), datetime.date(2026, 5, 1), 'Start date', 'Due date'),
        'a start date after the due date is rejected',
        message_contains='cannot be after')
    results.does_not_raise(
        lambda: validation.require_not_after(
            datetime.date(2026, 5, 1), datetime.date(2026, 6, 1), 'Start date', 'Due date'),
        'a start date before the due date is accepted')
    results.does_not_raise(
        lambda: validation.require_not_after(
            datetime.date(2026, 5, 1), datetime.date(2026, 5, 1), 'Start date', 'Due date'),
        'starting and finishing on the same day is accepted')
    results.does_not_raise(
        lambda: validation.require_not_after(None, datetime.date(2026, 6, 1),
                                             'Start date', 'Due date'),
        'an omitted optional start date is not an error')

    # A mistyped year passes every type, range and format check and then sorts to the
    # end of the dashboard forever.
    results.raises(ValueError,
                   lambda: validation.require_within_horizon(
                       datetime.date(2062, 5, 1), TODAY, 'Due date'),
                   'a due date decades away is rejected',
                   message_contains='five years')
    results.raises(ValueError,
                   lambda: validation.require_within_horizon(
                       datetime.date(1998, 5, 1), TODAY, 'Due date'),
                   'a due date decades ago is rejected',
                   message_contains='five years')
    results.does_not_raise(
        lambda: validation.require_within_horizon(
            datetime.date(2026, 11, 1), TODAY, 'Due date'),
        'a due date later this year is accepted')
    results.does_not_raise(
        lambda: validation.require_within_horizon(
            datetime.date(2026, 3, 1), TODAY, 'Due date'),
        'a due date earlier this year is accepted (overdue work is still real)')

    # Completeness: reported as ONE message listing everything missing, so a student
    # fixing a bulk-import line does not have to submit four times to find four gaps.
    required = [('title', 'Title'), ('subject', 'Subject'), ('due_date', 'Due date')]
    results.does_not_raise(
        lambda: validation.require_complete_record(
            {'title': 'SAC 2', 'subject': 'Physics', 'due_date': TODAY}, required, 'Line 1'),
        'a complete record passes')
    try:
        validation.require_complete_record({'title': 'SAC 2'}, required, 'Line 3')
    except ValueError as error:
        message = str(error)
        results.ok('Subject' in message and 'Due date' in message,
                   'the completeness message names EVERY missing field at once')
        results.ok('Line 3' in message,
                   'the completeness message says which record is incomplete')


# --- guarding data read back out of the database ---------------------------

def suite_database_reads(results):
    """The safe_* family must never raise, whatever a column turns out to hold.

    Anvil simpleObject columns accept any JSON and the Data Tables console can write
    anything at all, so each of these is a value the app could genuinely meet.
    """
    # THE ONE THAT USED TO TAKE THE APP DOWN. _user_now() is on the hot path of every
    # screen; an unresolvable stored timezone raised there, including on the Settings
    # page that is the only way to fix it.
    results.equal(validation.safe_timezone('Australia/Melbourn'), 'Australia/Melbourne',
                  'an unresolvable stored timezone falls back to the default')
    results.equal(validation.safe_timezone(None), 'Australia/Melbourne',
                  'a missing stored timezone falls back to the default')
    results.equal(validation.safe_timezone(12345), 'Australia/Melbourne',
                  'a non-string stored timezone falls back to the default')
    results.equal(validation.safe_timezone('Australia/Perth'), 'Australia/Perth',
                  'a valid stored timezone is kept')

    # THE ONE THAT CAUSED THE CONTRADICTION. An unset column read as "still enabled" by
    # the dispatcher and "off" by the Settings screen, so the app could say reminders
    # were off while emailing. Failing closed is the safe direction for outbound mail.
    results.equal(validation.safe_bool(None), False,
                  'an unset boolean column reads as False, not as enabled')
    results.equal(validation.safe_bool('true'), False,
                  'a non-boolean stored value reads as False')
    results.equal(validation.safe_bool(True), True, 'a real True is kept')
    results.equal(validation.safe_bool(False), False, 'a real False is kept')

    # A list column that is not a list at all: the old code raised TypeError here,
    # which was swallowed and silently skipped the rest of that student's run.
    results.equal(validation.safe_list(7, validation.is_positive_int), [],
                  'a scalar in a list column degrades to empty, it does not raise')
    results.equal(validation.safe_list({'a': 1}, validation.is_positive_int), [],
                  'a dict in a list column degrades to empty')
    results.equal(validation.safe_list(None, validation.is_positive_int), [],
                  'a null list column degrades to empty')

    # Partial corruption keeps the good elements — refusing the whole column would lose
    # data the student can still see and use.
    results.equal(validation.safe_list([7, True, 0, -3, 'x', 2], validation.is_positive_int),
                  [7, 2],
                  'unusable elements are dropped and the good ones survive')

    # An enum that has moved on. Without this the stored value is assigned to a dropdown
    # that does not offer it, silently falls back to the first item, and is written back.
    statuses = ('not_started', 'in_progress', 'completed')
    results.equal(validation.safe_choice('Complete', statuses, 'not_started'), 'not_started',
                  'a legacy Title-Case status falls back rather than corrupting')
    results.equal(validation.safe_choice('completed', statuses, 'not_started'), 'completed',
                  'a current status is kept')
    results.equal(validation.safe_choice(None, statuses, 'not_started'), 'not_started',
                  'a null status falls back')

    # Range enforced on READ as well as write, because a value predating the rule (or
    # written through the console) would otherwise flow into the dashboard unchecked.
    results.equal(validation.safe_number(250, None, 0, 100), None,
                  'a stored weight above the range is refused on read')
    results.equal(validation.safe_number(25, None, 0, 100), 25.0,
                  'a stored weight inside the range is kept')
    results.equal(validation.safe_number('25', None, 0, 100), None,
                  'a stored string in a number column is refused on read')

    results.equal(validation.safe_date('2026-05-22'), datetime.date(2026, 5, 22),
                  'a stored ISO date string is parsed on read')
    results.equal(validation.safe_date('not a date'), None,
                  'an unparseable stored date degrades to None')

    # The whole point of the family: not one of these may raise.
    for bad_value in (None, 0, '', [], {}, 'nonsense', -1, 3.14, True):
        results.does_not_raise(
            lambda v=bad_value: (
                validation.safe_text(v), validation.safe_bool(v), validation.safe_number(v),
                validation.safe_list(v), validation.safe_date(v),
                validation.safe_timezone(v),
                validation.safe_choice(v, ('a',), 'a')),
            'no safe_* helper raises on stored value %r' % (bad_value,))


# --- message quality -------------------------------------------------------

def suite_messages(results):
    """Every rejection message must be a sentence a Year 12 student can act on.

    The rubric asks for "meaningful warning/error messages", and these strings are
    shown to the student verbatim, so their wording IS the feature.
    """
    # A representative rejection from each family.
    failing_calls = [
        lambda: validation.require_text('', 'Title', 200),
        lambda: validation.require_number_in_range(150, 'Weight (%)', 0, 100),
        lambda: validation.require_date('nope', 'Due date'),
        lambda: validation.require_email('will@'),
        lambda: validation.require_timezone('Nowhere/Nothing'),
        lambda: validation.require_not_after(
            datetime.date(2026, 6, 1), datetime.date(2026, 5, 1), 'Start date', 'Due date'),
        lambda: validation.require_within_horizon(
            datetime.date(2062, 1, 1), TODAY, 'Due date'),
        lambda: validation.require_int_in_range(0, 'Reminder day', 1, 365),
        lambda: validation.require_bool('yes', 'Notifications'),
        lambda: validation.require_choice('nope', ('sac', 'sat'), 'Type'),
    ]
    for failing_call in failing_calls:
        try:
            failing_call()
        except ValueError as error:
            message = str(error)
            results.ok(message[:1].isupper(),
                       'message starts with a capital: %r' % message)
            results.ok(message.rstrip().endswith(('.', '!', '?')),
                       'message ends as a sentence: %r' % message)
            # No developer vocabulary. These are the words the OLD messages used
            # ('invalid subject', 'title required', 'must be a list of positive ints').
            lowered = message.lower()
            for leaked_term in ('none', 'nonetype', 'isinstance', 'valueerror',
                                'typeerror', 'traceback', 'int)', 'str)'):
                results.ok(leaked_term not in lowered,
                           'message leaks no developer term %r: %r' % (leaked_term, message))


SUITES = [
    ('existence', suite_existence),
    ('type', suite_type),
    ('range', suite_range),
    ('format', suite_format),
    ('reasonableness/completeness', suite_reasonableness),
    ('database reads', suite_database_reads),
    ('message quality', suite_messages),
]
