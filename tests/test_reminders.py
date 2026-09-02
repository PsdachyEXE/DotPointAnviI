"""Proves the reminder dispatcher sends the right email once, and only when allowed.

This is the only part of DotPoint that acts with nobody present, so every value it
reads comes out of the database and every failure is invisible to the student. Three
of the defects fixed in this change lived here, and each has a suite below.
"""

from .harness import load_server_code, make_user, make_settings
from . import anvil_stub

load_server_code()

import datetime

from server_code import reminders


def _add_assessment(user, **overrides):
    """Insert an assessment row with sane defaults, overridable per test."""
    fields = {
        'user': user, 'title': 'Methods SAC2', 'subject': 'Mathematical Methods',
        'type': 'sac', 'due_date': None, 'start_date': None, 'weight': 25.0,
        'status': 'not_started', 'description': None, 'reminder_days': [7, 2],
        'linked_note_ids': [], 'term_info': None, 'confidence': 'HIGH',
        'source_text': None, 'created_at': None, 'updated_at': None,
    }
    fields.update(overrides)
    return anvil_stub.app_tables.assessments.add_row(**fields)


def _days_from_today(settings, days):
    """A due date exactly `days` from the student's local today."""
    from server_code._datetime import _user_today
    return _user_today(settings) + datetime.timedelta(days=days)


# --- which thresholds are open ---------------------------------------------

def suite_thresholds(results):
    """_get_due_thresholds decides whether an email is owed, from untrusted input."""
    results.equal(reminders._get_due_thresholds(7, [7, 2]), [7],
                  'exactly seven days out opens the 7-day threshold')
    results.equal(reminders._get_due_thresholds(2, [7, 2]), [2, 7],
                  'two days out opens both, earliest first')
    results.equal(reminders._get_due_thresholds(0, [7, 2]), [2, 7],
                  'due today still opens both')
    results.equal(reminders._get_due_thresholds(8, [7, 2]), [],
                  'eight days out opens nothing yet')

    # Overdue work is deliberately out of scope for email — the dashboard's red band
    # already carries that signal, and a daily "this was due" email is nagging.
    results.equal(reminders._get_due_thresholds(-1, [7, 2]), [],
                  'overdue work generates no email')
    results.equal(reminders._get_due_thresholds(None, [7, 2]), [],
                  'an undated assessment generates no email')

    # THE REGRESSION: a hand-edited column holding a scalar used to raise TypeError
    # here, which the per-user handler swallowed — silently skipping every REMAINING
    # assessment for that student on that run.
    for corrupt in (7, '7', {'a': 1}, None, 'seven'):
        results.does_not_raise(
            lambda c=corrupt: reminders._get_due_thresholds(3, c),
            'a corrupt reminder_days column %r degrades instead of raising' % (corrupt,))
        results.equal(reminders._get_due_thresholds(3, corrupt), [],
                      'and yields no thresholds')

    # Partial corruption keeps the usable entries.
    results.equal(reminders._get_due_thresholds(2, [7, 'x', None, 2]), [2, 7],
                  'unusable entries are dropped and the good ones still fire')

    # True is not the number 1. Without the bool guard a stored [True] would send a
    # "due tomorrow" email the student never asked for.
    results.equal(reminders._get_due_thresholds(1, [True]), [],
                  'a stored True is not treated as a 1-day reminder')

    # A duplicated entry must not send the same reminder twice. At three days out only
    # the 7-day window is open (3 <= 7 but 3 > 2), so the answer is one threshold.
    results.equal(reminders._get_due_thresholds(3, [7, 7, 2, 2]), [7],
                  'a duplicated threshold still sends only one email')
    results.equal(reminders._get_due_thresholds(1, [7, 7, 2, 2]), [2, 7],
                  'once both windows are open, each sends exactly once')


# --- the master switch -----------------------------------------------------

def suite_notifications_switch(results):
    """The Settings screen and the dispatcher must agree about the master switch.

    The regression this guards: the two read the same column with different rules —
    bool() on the Settings screen (None -> shows OFF) and `is False` in the dispatcher
    (None -> keeps sending). The app could tell a student their reminders were off and
    email them anyway.
    """
    user = make_user()
    settings = make_settings(user, notifications_enabled=True)
    _add_assessment(user, due_date=_days_from_today(settings, 7))

    run_counts = {'sent': 0, 'errors': 0}
    reminders._process_user(user, run_counts)
    results.equal(run_counts['sent'], 1, 'switch on: the reminder is sent')

    # Explicitly off.
    anvil_stub.reset()
    user = make_user()
    settings = make_settings(user, notifications_enabled=False)
    _add_assessment(user, due_date=_days_from_today(settings, 7))
    run_counts = {'sent': 0, 'errors': 0}
    reminders._process_user(user, run_counts)
    results.equal(run_counts['sent'], 0, 'switch off: nothing is sent')

    # THE REGRESSION ITSELF: an unset column. The Settings screen draws this as OFF,
    # so the dispatcher must treat it as off too. Failing closed is the safe direction
    # for outbound mail — an unsent reminder is a nuisance, an unwanted one is worse.
    for unset_value in (None, 'yes', 0, 1, ''):
        anvil_stub.reset()
        user = make_user()
        settings = make_settings(user, notifications_enabled=unset_value)
        _add_assessment(user, due_date=_days_from_today(settings, 7))
        run_counts = {'sent': 0, 'errors': 0}
        results.does_not_raise(
            lambda: reminders._process_user(user, run_counts),
            'a non-boolean switch value %r does not raise' % (unset_value,))
        results.equal(run_counts['sent'], 0,
                      'a non-boolean switch value %r sends nothing (fails closed)'
                      % (unset_value,))


# --- what gets skipped -----------------------------------------------------

def suite_skips(results):
    """Finished, undated and already-reminded work must not generate email."""
    anvil_stub.reset()
    user = make_user()
    settings = make_settings(user)

    _add_assessment(user, due_date=_days_from_today(settings, 7), status='completed')
    run_counts = {'sent': 0, 'errors': 0}
    reminders._process_user(user, run_counts)
    results.equal(run_counts['sent'], 0, 'completed work generates no reminder')

    # A legacy Title-Case status was never equal to 'completed', so the old exact
    # string test kept emailing about work the student had already finished.
    for legacy_status in ('Completed', 'Complete', 'completed '):
        anvil_stub.reset()
        user = make_user()
        settings = make_settings(user)
        _add_assessment(user, due_date=_days_from_today(settings, 7),
                        status=legacy_status)
        run_counts = {'sent': 0, 'errors': 0}
        results.does_not_raise(lambda: reminders._process_user(user, run_counts),
                               'an off-enum status %r does not raise' % legacy_status)

    anvil_stub.reset()
    user = make_user()
    settings = make_settings(user)
    _add_assessment(user, due_date=None)
    run_counts = {'sent': 0, 'errors': 0}
    reminders._process_user(user, run_counts)
    results.equal(run_counts['sent'], 0, 'an undated assessment generates no reminder')


# --- NFR02: never twice ----------------------------------------------------

def suite_deduplication(results):
    """NFR02: no reminder is delivered more than once per (assessment, threshold)."""
    anvil_stub.reset()
    user = make_user()
    settings = make_settings(user)
    _add_assessment(user, due_date=_days_from_today(settings, 7))

    run_counts = {'sent': 0, 'errors': 0}
    reminders._process_user(user, run_counts)
    results.equal(run_counts['sent'], 1, 'first pass sends the 7-day reminder')

    # The scheduler runs every 30 minutes, so this happens 48 times a day.
    reminders._process_user(user, run_counts)
    reminders._process_user(user, run_counts)
    results.equal(run_counts['sent'], 1, 'later passes on the same day send nothing more')
    results.equal(len(anvil_stub.sent_emails), 1, 'exactly one email exists')
    results.equal(len(anvil_stub.app_tables.reminder_logs.rows), 1,
                  'exactly one log row was written')

    # The 2-day threshold is a DIFFERENT reminder_type, so it must still fire later.
    # This is why reminder_type is part of the dedup key.
    anvil_stub.reset()
    user = make_user()
    settings = make_settings(user)
    assessment = _add_assessment(user, due_date=_days_from_today(settings, 2))
    run_counts = {'sent': 0, 'errors': 0}
    reminders._process_user(user, run_counts)
    results.equal(run_counts['sent'], 2,
                  'two days out, both the 7-day and 2-day reminders are owed and sent')
    types_sent = sorted(r['reminder_type'] for r in
                        anvil_stub.app_tables.reminder_logs.rows.values())
    results.equal(types_sent, ['2_day', '7_day'],
                  'and they are logged under distinct reminder types')


# --- failed delivery -------------------------------------------------------

def suite_send_failure(results):
    """A failed send must NOT be logged, so the next tick retries it."""
    anvil_stub.reset()
    user = make_user()
    settings = make_settings(user)
    _add_assessment(user, due_date=_days_from_today(settings, 7))

    anvil_stub.set_email_failure(True)
    run_counts = {'sent': 0, 'errors': 0}
    reminders._process_user(user, run_counts)
    results.equal(run_counts['sent'], 0, 'a failed send counts as sent zero')
    results.ok(run_counts['errors'] >= 1, 'and is counted as an error')
    results.equal(len(anvil_stub.app_tables.reminder_logs.rows), 0,
                  'no log row is written, so the reminder is not lost')

    # The retry on the next tick must then succeed.
    anvil_stub.set_email_failure(False)
    reminders._process_user(user, run_counts)
    results.equal(run_counts['sent'], 1, 'the next tick delivers the retried reminder')


# --- one student cannot break the run for everyone -------------------------

def suite_run_isolation(results):
    """run_reminder_check must survive one student's bad data and keep going."""
    anvil_stub.reset()
    broken_user = make_user('broken@example.com')
    make_settings(broken_user, timezone='Nowhere/Nothing',
                  default_reminder_days='not a list')
    _add_assessment(broken_user, due_date=datetime.date(2026, 12, 1),
                    reminder_days={'bad': True})

    good_user = make_user('fine@example.com')
    good_settings = make_settings(good_user)
    _add_assessment(good_user, due_date=_days_from_today(good_settings, 7),
                    title='Physics SAC1')

    summary = reminders.run_reminder_check()
    results.ok(isinstance(summary, dict), 'the run returns a summary dict')
    results.ok('sent' in summary and 'errors' in summary and 'run_at' in summary,
               'the summary carries sent, errors and run_at')
    results.equal(summary['sent'], 1,
                  "the healthy student's reminder is still delivered")
    recipients = [message['to'] for message in anvil_stub.sent_emails]
    results.ok('fine@example.com' in recipients,
               'and it went to the right address')


# --- the message itself ----------------------------------------------------

def suite_email_content(results):
    """The countdown must reflect ACTUAL days remaining, not the threshold."""
    subject, text, html = reminders._build_email(
        {'title': 'Methods SAC2', 'subject': 'Mathematical Methods', 'type': 'sac',
         'due_date': datetime.date(2026, 5, 22), 'weight': 25.0}, 7)
    results.ok('in 7 days' in subject, 'seven days out reads "in 7 days"')

    # A threshold can first fire late — a missed tick, or an assessment created close
    # to its due date. Quoting the threshold would then say "due in 7 days" about
    # something due today.
    subject, _, _ = reminders._build_email(
        {'title': 'Methods SAC2', 'subject': 'Mathematical Methods', 'type': 'sac',
         'due_date': datetime.date(2026, 5, 22), 'weight': 25.0}, 0)
    results.ok('today' in subject, 'due today reads "today", not "in 7 days"')

    subject, _, _ = reminders._build_email(
        {'title': 'X', 'subject': 'Physics', 'type': 'sac',
         'due_date': datetime.date(2026, 5, 22), 'weight': None}, 1)
    results.ok('in 1 day' in subject and 'in 1 days' not in subject,
               'one day out is singular')

    # Every field is defaulted, because each one comes from a nullable column.
    results.does_not_raise(
        lambda: reminders._build_email(
            {'title': None, 'subject': None, 'type': None,
             'due_date': None, 'weight': None}, 3),
        'an assessment of all-empty columns still produces an email')

    _, text, html = reminders._build_email(
        {'title': 'Methods SAC2', 'subject': 'Mathematical Methods', 'type': 'sac',
         'due_date': datetime.date(2026, 5, 22), 'weight': 25.0}, 2)
    results.ok('25%' in text, 'a whole-number weight renders without a trailing .0')
    results.ok('22 May 2026' in text,
               'the due date renders in the app format (NFR08)')
    results.ok('Mathematical Methods' in text and 'Mathematical Methods' in html,
               'the VCE study appears in both bodies')


SUITES = [
    ('due thresholds', suite_thresholds),
    ('notifications switch', suite_notifications_switch),
    ('skips', suite_skips),
    ('deduplication (NFR02)', suite_deduplication),
    ('send failure retry', suite_send_failure),
    ('run isolation', suite_run_isolation),
    ('email content', suite_email_content),
]
