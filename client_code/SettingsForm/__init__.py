import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""SettingsForm - per-user preferences (reminders, school year/terms, timezone).

Resolves Pending Decision 2 (timezone) end-to-end: the timezone dropdown writes
user_settings.timezone, which drives all server-side "today" / date-math.

Theme has no UI control in MVP (a placeholder label stands in); the column
exists for a future release.

See IMPLEMENTATION_SPEC.md section 3 (SettingsForm) and section 2
(notes.get_settings / notes.update_settings).
"""

import anvil
import anvil.server
import datetime
from anvil import (
    ColumnPanel, FlowPanel, Label, CheckBox, DatePicker, TextBox, DropDown,
    Button, Notification,
)

from ..common import make_top_bar

# Reminder-day options offered in the UI (spec §3): N days before due date.
REMINDER_DAY_OPTIONS = (14, 7, 3, 2, 1)

# Static IANA Australian timezones (spec §3 / Decision 2).
TIMEZONES = (
    'Australia/Sydney', 'Australia/Melbourne', 'Australia/Brisbane',
    'Australia/Perth', 'Australia/Darwin', 'Australia/Adelaide', 'Australia/Hobart',
)

_NUM_TERMS = 4


def _to_iso(d):
    """date -> 'YYYY-MM-DD' (manual; avoids Skulpt date.isoformat differences)."""
    return '%04d-%02d-%02d' % (d.year, d.month, d.day)


def _from_iso(s):
    """'YYYY-MM-DD' -> date, or None if unparseable."""
    if not s or not isinstance(s, str):
        return None
    parts = s.split('-')
    if len(parts) != 3:
        return None
    try:
        return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError):
        return None


class SettingsForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self.add_component(make_top_bar())

        body = ColumnPanel()
        self.add_component(body)

        # --- Reminders ---
        body.add_component(Label(text='Reminders', role='heading'))
        body.add_component(Label(text='Default reminder days before an assessment is due:'))
        self._day_checks = {}
        days_row = FlowPanel()
        for d in REMINDER_DAY_OPTIONS:
            cb = CheckBox(text='%d days' % d)
            self._day_checks[d] = cb
            days_row.add_component(cb)
        body.add_component(days_row)

        self._notifications_cb = CheckBox(text='Enable email reminders')
        body.add_component(self._notifications_cb)

        # --- School terms ---
        body.add_component(Label(text='School terms', role='heading'))
        self._term_pickers = []  # [(start_DatePicker, end_DatePicker), ...]
        for term in range(1, _NUM_TERMS + 1):
            row = FlowPanel()
            row.add_component(Label(text='Term %d' % term))
            start_dp = DatePicker(placeholder='Start')
            end_dp = DatePicker(placeholder='End')
            row.add_component(start_dp)
            row.add_component(end_dp)
            body.add_component(row)
            self._term_pickers.append((start_dp, end_dp))

        year_row = FlowPanel()
        year_row.add_component(Label(text='School year'))
        self._school_year_tb = TextBox(placeholder='e.g. 2026')
        year_row.add_component(self._school_year_tb)
        body.add_component(year_row)

        # --- Timezone ---
        body.add_component(Label(text='Timezone', role='heading'))
        self._timezone_dd = DropDown(items=list(TIMEZONES))
        body.add_component(self._timezone_dd)

        # --- Theme (no control in MVP) ---
        body.add_component(Label(text='Theme', role='heading'))
        body.add_component(Label(text='Theme control coming in a future release.'))

        # --- Save ---
        save_btn = Button(text='Save', role='primary')
        save_btn.set_event_handler('click', self._on_save_click)
        body.add_component(save_btn)

        self._load_settings()

    def _load_settings(self):
        try:
            s = anvil.server.call('get_settings')
        except Exception as e:
            Notification("Couldn't load settings: %s" % e, style='danger').show()
            return

        reminder_days = s.get('default_reminder_days') or []
        for d, cb in self._day_checks.items():
            cb.checked = d in reminder_days

        self._notifications_cb.checked = bool(s.get('notifications_enabled'))

        terms_by_num = {
            t.get('term'): t
            for t in (s.get('school_terms') or [])
            if isinstance(t, dict)
        }
        for i, (start_dp, end_dp) in enumerate(self._term_pickers, start=1):
            t = terms_by_num.get(i)
            if t:
                start_dp.date = _from_iso(t.get('start_date'))
                end_dp.date = _from_iso(t.get('end_date'))

        year = s.get('school_year')
        self._school_year_tb.text = '' if year is None else str(year)

        tz = s.get('timezone') or 'Australia/Melbourne'
        if tz not in TIMEZONES:
            # Keep an out-of-list stored value selectable.
            self._timezone_dd.items = list(TIMEZONES) + [tz]
        self._timezone_dd.selected_value = tz

    def _on_save_click(self, **event_args):
        fields = {
            'default_reminder_days': sorted(
                (d for d, cb in self._day_checks.items() if cb.checked),
                reverse=True,
            ),
            'notifications_enabled': bool(self._notifications_cb.checked),
        }

        terms = []
        for i, (start_dp, end_dp) in enumerate(self._term_pickers, start=1):
            if start_dp.date and end_dp.date:
                terms.append({
                    'term': i,
                    'start_date': _to_iso(start_dp.date),
                    'end_date': _to_iso(end_dp.date),
                })
        fields['school_terms'] = terms

        year_text = (self._school_year_tb.text or '').strip()
        if year_text:
            try:
                fields['school_year'] = int(year_text)
            except ValueError:
                Notification("School year must be a whole number.", style='danger').show()
                return
        else:
            fields['school_year'] = None

        tz = self._timezone_dd.selected_value
        if tz:
            fields['timezone'] = tz

        try:
            anvil.server.call('update_settings', fields)
            Notification("Settings saved.", style='success').show()
        except Exception as e:
            Notification("Couldn't save settings: %s" % e, style='danger').show()
