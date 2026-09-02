import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Background email reminder dispatcher (FR13, FR14, NFR02).

WHAT THIS MODULE DOES
    run_reminder_check() is the only part of DotPoint that acts without the student
    present. Anvil runs it every 30 minutes (the platform's documented minimum
    interval — the schedule IS registered, in anvil.yaml under scheduled_tasks as
    job_id ZONOLIGB). Each pass walks every account and emails a reminder for any
    assessment whose due date has crossed one of that student's reminder thresholds.

    Reminders are OPT-OUT at the column level but fail closed in code: a student is
    emailed only when user_settings.notifications_enabled reads back as a genuine
    True. A missing or unreadable value is treated as "off", so the switch shown on
    the Settings screen and the behaviour of this dispatcher can never disagree.

DEDUPLICATION (NFR02)
    The key is (assessment_id, user, reminder_type) and deliberately NOT the date.
    That is what lets a scheduler tick missed overnight still deliver a reminder that
    has fallen due, while making it impossible to send the same threshold twice. The
    reminder_logs row is written only after the send succeeds, so a delivery failure
    is retried on the next tick rather than being recorded as sent.

    This is deliberately STRICTER than the key FR14 writes down, which includes
    sent_date. Keyed on the date as well, a threshold that first came due on a day
    the app missed would be re-sent the following day as a new (assessment, user,
    type, date) combination — the student gets the same "due in 7 days" email twice.
    Dropping the date from the LOOKUP satisfies NFR02's "once per (assessment, user,
    threshold, day)" by satisfying something narrower: once per threshold, ever.
    sent_date is still recorded on the row, because the log is also the audit trail
    for when a reminder actually went out.

WHY THE HELPERS TAKE PLAIN DICTS
    _get_due_thresholds() and _build_email() are pure functions over plain values, not
    over Anvil Rows, so the whole decision — should this email go, and what does it
    say — is unit-testable offline. See tests/test_reminders.py.

See IMPLEMENTATION_SPEC.md section 2 (server_code/reminders.py) and section 6.
"""

import anvil.server
import anvil.email
import datetime

from ._auth import _require_user
from ._datetime import _user_today, _format_date_au
from ._constants import (
    APP_BASE_URL, VALID_STATUSES, STATUS_COMPLETED, STATUS_DEFAULT,
)
from ._validation import safe_list, safe_bool, safe_choice, safe_date, is_positive_int

try:
    import anvil.secrets as _secrets
except ImportError:
    _secrets = None


# --- pure helpers (unit-testable) ------------------------------------------

def _get_due_thresholds(days_remaining, reminder_days) -> list:
    """The reminder-day thresholds whose window is open for this assessment.

    Fires when 0 <= days_remaining <= d. Overdue assessments (days_remaining < 0)
    are intentionally out of scope for emailed reminders (the dashboard urgency
    band carries that signal), so they yield no thresholds.

    `reminder_days` comes straight out of a simpleObject database column, so it is
    treated as untrusted: `safe_list` drops anything that is not a positive whole
    number and returns [] for a column holding a scalar or a dict. Before that guard,
    a hand-edited column holding `7` (not `[7]`) raised TypeError here, which the
    dispatcher's per-user handler swallowed — silently skipping every remaining
    assessment for that student on that run.
    """
    if days_remaining is None or days_remaining < 0:
        return []
    # is_positive_int also rejects True/False, which subclass int and would otherwise
    # be read as the threshold 1 and fire a bogus "due tomorrow" email.
    usable_days = safe_list(reminder_days, is_positive_int)
    # De-duplicated and sorted so a column holding [7, 7, 2] sends one 7-day email,
    # and the earliest threshold is always considered first.
    return sorted({d for d in usable_days if days_remaining <= d})


def _build_email(assessment: dict, days_remaining: int):
    """Compose one reminder email. Returns (email_subject, text_body, html_body).

    `assessment` is a plain dict — deliberately not a live Anvil Row — with the keys
    title, subject, type, due_date (a date) and weight. Passing a dict keeps this
    function pure and therefore unit-testable offline; the caller does the database
    reading and the guarding.

    Two bodies are produced because Anvil's email service sends both and the reader's
    mail client picks: the text body is the fallback for a plain-text client.

    The countdown reflects the ACTUAL days remaining, not the reminder threshold that
    triggered it — a threshold can first fire late (a missed scheduler tick, or an
    assessment created close to its due date), so "due in 7 days" on a due-today item
    would be actively misleading.
    """
    # Every field is defaulted rather than assumed: this dict is assembled from
    # database columns, any of which can be None.
    title = assessment.get('title') or '(untitled)'
    # NOTE THE TWO MEANINGS OF "SUBJECT" IN THIS FUNCTION. `assessment_subject` is the
    # VCE study ("Mathematical Methods"); `email_subject` below is the message header.
    # They were both called some form of "subject" and sat four lines apart, which is
    # exactly the kind of collision SAT criterion 7.1 is asking to see removed.
    assessment_subject = assessment.get('subject') or ''
    assessment_type = assessment.get('type') or ''
    due_date = assessment.get('due_date')
    # The 'no date' arm looks unreachable, and from the dispatcher it is — step 4 of
    # _process_user skips an assessment with no due date. It stays because this
    # function is public to the tests and is called with hand-built dicts there, and
    # because _format_date_au(None) would raise rather than degrade.
    due_display = _format_date_au(due_date) if due_date else 'no date'
    weight = assessment.get('weight')

    # Phrase the countdown the way a person would say it. <= 0 rather than == 0
    # because a reminder can be generated on the due date itself.
    if days_remaining <= 0:
        countdown = 'today'
    elif days_remaining == 1:
        countdown = 'in 1 day'
    else:
        countdown = 'in %d days' % days_remaining
    email_subject = 'Reminder: %s due %s' % (title, countdown)

    # Plain-text body, assembled as a list of lines so the weight row can be omitted
    # without leaving a blank gap when the assessment has no weighting recorded.
    text_lines = [
        'Hi,',
        '',
        'This is a reminder that the following assessment is coming up:',
        '',
        '  %s' % title,
        '  Subject: %s' % assessment_subject,
        '  Due:     %s  (%s)' % (due_display, countdown),
        '  Type:    %s' % assessment_type,
    ]
    if weight is not None:
        # %g drops a trailing '.0', so a 25.0% weighting reads as "25%".
        text_lines.append('  Weight:  %g%%' % weight)
    text_lines += [
        '',
        'Open DotPoint to update your status or notes:',
        '%s/#dashboard' % APP_BASE_URL,
        '',
        '— DotPoint',
    ]
    text_body = '\n'.join(text_lines)

    # HTML body: the same facts as a definition list. Styles are inline because email
    # clients strip <style> blocks, so the app's stylesheet cannot reach here.
    # `countdown` and `due_display` are reused rather than recomputed, so the two
    # bodies of one message can never phrase the same deadline differently.
    #
    # The weight row is built as a fragment substituted in below, for the same reason
    # the text body was assembled as a list: an assessment with no weighting recorded
    # must leave no empty <dt>/<dd> pair behind.
    weight_html = ''
    if weight is not None:
        weight_html = '<dt>Weight</dt><dd>%g%%</dd>' % weight
    html_body = (
        '<div style="font-family:sans-serif">'
        '<h2 style="margin-bottom:4px">%s</h2>'
        '<p>This assessment is due <strong>%s</strong>.</p>'
        '<dl>'
        '<dt>Subject</dt><dd>%s</dd>'
        '<dt>Due</dt><dd>%s</dd>'
        '<dt>Type</dt><dd>%s</dd>'
        '%s'
        '</dl>'
        '<p><a href="%s/#dashboard">Open DotPoint</a></p>'
        '<p style="color:#888">— DotPoint</p>'
        '</div>'
    ) % (title, countdown, assessment_subject, due_display, assessment_type,
         weight_html, APP_BASE_URL)

    return email_subject, text_body, html_body


# --- dispatcher ------------------------------------------------------------

def _process_user(user, run_counts):
    """Send every reminder that has fallen due for one student.

    `run_counts` is the shared tally dict from run_reminder_check; this function
    mutates its 'sent' and 'errors' entries in place rather than returning, so one
    student failing cannot lose the counts for the students already processed.

    Every value read below comes out of the database, so each is guarded on the way
    out (SAT criterion 7.3, the "as well as from the database" limb): a settings row
    can predate a column, and any simpleObject cell can be edited by hand in the Anvil
    Data Tables console.
    """
    # 1. Find the student's settings. No row at all means they have never opened the
    #    app past sign-up, so there is nothing to remind them about yet.
    settings = app_tables.user_settings.get(user=user)
    if settings is None:
        return

    # 2. Honour the master notifications switch. safe_bool() is what makes this
    #    correct: the column can hold None on a row written before the switch existed,
    #    and the previous test (`is False`) treated that None as "still enabled" while
    #    the Settings screen — which reads the same column through bool() — drew the
    #    switch as OFF. The app could therefore tell a student their reminders were
    #    off and keep emailing them. Both readers now use the same rule, and the
    #    default is OFF so an unreadable value can never cause unwanted email.
    if not safe_bool(settings['notifications_enabled'], default=False):
        return

    # 3. "Today" must be the student's local today, not the server's UTC today, or an
    #    assessment due tomorrow in Melbourne looks due today to a UTC server.
    today = _user_today(settings)
    # Read once, outside the loop: it is the same for every assessment this student
    # owns, and a student with 100 assessments would otherwise re-read the same
    # settings column 100 times (NFR01). It is NOT guarded here — step 6 hands it to
    # _get_due_thresholds, which sanitises whichever list it is given.
    default_reminder_days = settings['default_reminder_days']

    for assessment in app_tables.assessments.search(user=user):
        # 4. Skip anything finished or undated. safe_choice pins an unrecognised
        #    stored status to the default rather than comparing raw strings: a legacy
        #    Title-Case 'Completed' is not equal to 'completed', so the old exact test
        #    kept emailing about work the student had already marked done.
        status = safe_choice(assessment['status'], VALID_STATUSES, STATUS_DEFAULT)
        due_date = safe_date(assessment['due_date'])
        if status == STATUS_COMPLETED or due_date is None:
            continue

        # 5. How many days away is it? Negative means overdue, which _get_due_thresholds
        #    deliberately declines to email about.
        days_remaining = (due_date - today).days

        # 6. Per-assessment reminder days override the student's default. Both are
        #    untrusted simpleObject columns; _get_due_thresholds sanitises whichever
        #    it is handed, so a corrupt override degrades to the default rather than
        #    aborting this student's whole run.
        #
        #    The safe_list() call here is a TEST, not the value used — its result is
        #    thrown away and the raw column is passed on, because _get_due_thresholds
        #    sanitises again at the point of use. The test is falsiness, so a column
        #    holding [] or a column holding only junk are treated the same way: both
        #    fall back to the student's default schedule.
        reminder_days = assessment['reminder_days']
        if not safe_list(reminder_days, is_positive_int):
            reminder_days = default_reminder_days

        for threshold_days in _get_due_thresholds(days_remaining, reminder_days):
            # 7. The dedup key is (assessment, user, reminder_type) and nothing else —
            #    deliberately not the date — so a scheduler tick missed overnight still
            #    sends a reminder that is due, but never re-sends one already sent.
            reminder_type = '%d_day' % threshold_days
            already_sent = app_tables.reminder_logs.get(
                assessment_id=assessment.get_id(), user=user,
                reminder_type=reminder_type)
            if already_sent is not None:
                continue

            # 8. A user row with no email address cannot be sent to. Checking here
            #    turns what would be an exception inside the send into a counted,
            #    skipped reminder.
            recipient = user['email']
            if not recipient:
                run_counts['errors'] += 1
                continue

            email_subject, text_body, html_body = _build_email({
                'title': assessment['title'], 'subject': assessment['subject'],
                'type': assessment['type'], 'due_date': due_date,
                'weight': assessment['weight'],
            }, days_remaining)

            # 9. The log row is written ONLY after a successful send, so a failed
            #    delivery is retried on the next tick instead of being silently
            #    recorded as delivered.
            try:
                anvil.email.send(to=recipient, subject=email_subject,
                                 text=text_body, html=html_body)
            except anvil.email.SendFailure:
                run_counts['errors'] += 1
                continue
            app_tables.reminder_logs.add_row(
                assessment_id=assessment.get_id(), user=user,
                sent_date=today, reminder_type=reminder_type)
            run_counts['sent'] += 1


@anvil.server.background_task
def run_reminder_check() -> dict:
    """Scheduled dispatcher (FR13). Anvil runs this every 30 minutes.

    Walks every account, sends whatever reminders have fallen due, and returns a
    summary that the Anvil Background Tasks console shows:
        {'sent': int, 'errors': int, 'run_at': ISO-8601 UTC string}

    Runs as the app rather than as a signed-in user, which is why it is the one place
    in the codebase that reads other people's rows — the single documented exception
    to NFR03's "every Data Table query scoped to current_user". Nothing it reads
    leaves the account it belongs to: the only outputs are an email to each account
    holder's own address and a count in the task log. See server_code/README.txt
    section 12.3, "Who can see your data".
    """
    # Named keys rather than the two-slot list this used to be: the counters are
    # mutated from a helper, and `run_counts[1] += 1` gave a reader no way to tell
    # which of the two numbers was being incremented.
    run_counts = {'sent': 0, 'errors': 0}

    for user in app_tables.users.search():
        # One student's bad data must never stop the run for everybody after them, so
        # each pass is isolated. The exception is counted and printed to the task log
        # rather than swallowed silently — the missing Email service went undiagnosed
        # for weeks precisely because a failure here was invisible (see
        # docs/TESTING.md defect 12).
        try:
            _process_user(user, run_counts)
        except Exception as error:
            run_counts['errors'] += 1
            print('reminder run: user %r failed: %r' % (user['email'], error))

    return {
        'sent': run_counts['sent'],
        'errors': run_counts['errors'],
        # Stamped in UTC because this is a server-side audit value, not something
        # shown to a student in their own timezone.
        'run_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@anvil.server.callable
def trigger_reminder_check_now() -> dict:
    """Dev-only manual trigger. Gated by the DEV_EMAIL app secret."""
    user = _require_user()
    dev_email = None
    if _secrets is not None:
        try:
            dev_email = _secrets.get_secret('DEV_EMAIL')
        except Exception:
            dev_email = None
    if not dev_email or user['email'] != dev_email:
        raise PermissionError("dev only")
    return run_reminder_check()
