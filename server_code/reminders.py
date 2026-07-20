import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Background email reminder dispatcher (FR13, FR14, NFR02).

run_reminder_check (@anvil.server.background_task) is meant to run every 30
minutes (Anvil's documented minimum). Register the schedule in the Anvil IDE
(Scheduled Tasks -> run_reminder_check -> every 30 minutes) once this function
exists — it can't be added to anvil.yaml until then.

For each user with notifications enabled, it walks their non-completed
assessments and emails a reminder for each reminder-day threshold whose window
is open (0 <= days_remaining <= d), deduplicated permanently on
(assessment_id, user, reminder_type) so a scheduler gap never re-fires a sent
reminder. A reminder_logs row is written only on send success, so a failed send
retries on the next tick.

See IMPLEMENTATION_SPEC.md section 2 (server_code/reminders.py) and section 6.
"""

import anvil.server
import anvil.email
import datetime

from ._auth import _require_user
from ._datetime import _user_today, _format_date_au
from ._constants import APP_BASE_URL

try:
    import anvil.secrets as _secrets
except ImportError:
    _secrets = None


# --- pure helpers (unit-testable) ------------------------------------------

def _due_thresholds(days_remaining, reminder_days) -> list:
    """The reminder-day thresholds whose window is open for this assessment.

    Fires when 0 <= days_remaining <= d. Overdue assessments (days_remaining < 0)
    are intentionally out of scope for emailed reminders (the dashboard urgency
    band carries that signal), so they yield no thresholds.
    """
    if days_remaining is None or days_remaining < 0:
        return []
    return sorted({d for d in (reminder_days or []) if isinstance(d, int) and days_remaining <= d})


def _build_email(assessment: dict, d: int):
    """Return (subject, text, html) for one reminder. assessment is a plain dict
    with keys title, subject, type, due_date(date), weight."""
    title = assessment.get('title') or '(untitled)'
    subject_name = assessment.get('subject') or ''
    a_type = assessment.get('type') or ''
    due_date = assessment.get('due_date')
    due_str = _format_date_au(due_date) if due_date else 'no date'
    weight = assessment.get('weight')

    plural = 's' if d != 1 else ''
    subject = 'Reminder: %s due in %d day%s' % (title, d, plural)

    lines = [
        'Hi,',
        '',
        'This is a reminder that the following assessment is coming up:',
        '',
        '  %s' % title,
        '  Subject: %s' % subject_name,
        '  Due:     %s  (in %d day%s)' % (due_str, d, plural),
        '  Type:    %s' % a_type,
    ]
    if weight is not None:
        lines.append('  Weight:  %g%%' % weight)
    lines += [
        '',
        'Open DotPoint to update your status or notes:',
        '%s/#dashboard' % APP_BASE_URL,
        '',
        '— DotPoint',
    ]
    text = '\n'.join(lines)

    weight_html = ''
    if weight is not None:
        weight_html = '<dt>Weight</dt><dd>%g%%</dd>' % weight
    html = (
        '<div style="font-family:sans-serif">'
        '<h2 style="margin-bottom:4px">%s</h2>'
        '<p>This assessment is due in <strong>%d day%s</strong>.</p>'
        '<dl>'
        '<dt>Subject</dt><dd>%s</dd>'
        '<dt>Due</dt><dd>%s</dd>'
        '<dt>Type</dt><dd>%s</dd>'
        '%s'
        '</dl>'
        '<p><a href="%s/#dashboard">Open DotPoint</a></p>'
        '<p style="color:#888">— DotPoint</p>'
        '</div>'
    ) % (title, d, plural, subject_name, due_str, a_type, weight_html, APP_BASE_URL)

    return subject, text, html


# --- dispatcher ------------------------------------------------------------

def _process_user(user, sent_errors):
    """Send any due reminders for one user. Mutates sent_errors = [sent, errors]."""
    settings = app_tables.user_settings.get(user=user)
    if settings is None or settings['notifications_enabled'] is False:
        return
    today = _user_today(settings)
    default_days = settings['default_reminder_days'] or []

    for a in app_tables.assessments.search(user=user):
        if a['status'] == 'completed' or a['due_date'] is None:
            continue
        days_remaining = (a['due_date'] - today).days
        reminder_days = a['reminder_days'] or default_days
        for d in _due_thresholds(days_remaining, reminder_days):
            reminder_type = '%d_day' % d
            existing = app_tables.reminder_logs.get(
                assessment_id=a.get_id(), user=user, reminder_type=reminder_type)
            if existing is not None:
                continue
            subject, text, html = _build_email({
                'title': a['title'], 'subject': a['subject'], 'type': a['type'],
                'due_date': a['due_date'], 'weight': a['weight'],
            }, d)
            try:
                anvil.email.send(to=user['email'], subject=subject, text=text, html=html)
            except anvil.email.SendFailure:
                sent_errors[1] += 1
                continue
            app_tables.reminder_logs.add_row(
                assessment_id=a.get_id(), user=user,
                sent_date=today, reminder_type=reminder_type)
            sent_errors[0] += 1


@anvil.server.background_task
def run_reminder_check() -> dict:
    """Scheduled dispatcher (every 30 min). Returns a run summary."""
    sent_errors = [0, 0]   # [sent, errors]
    for user in app_tables.users.search():
        try:
            _process_user(user, sent_errors)
        except Exception as e:
            sent_errors[1] += 1
            print('reminder run: user failed: %r' % e)
    return {
        'sent': sent_errors[0],
        'errors': sent_errors[1],
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
