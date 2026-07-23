import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""SettingsForm - per-user preferences (reminders, school year/terms, timezone,
theme, and the deliberate change-subjects flow).

Resolves Pending Decision 2 (timezone) end-to-end: the timezone dropdown writes
user_settings.timezone, which drives all server-side "today" / date-math.

Theme (spec §12): a light/dark dropdown saved via update_settings and applied
immediately through common.apply_theme.

Subjects (spec §11): locked after onboarding; the 'Change subjects…' button is
the only way to alter them — a confirm dialog explains the consequences, then
the shared common.SubjectPicker re-runs the same client-side checks as
onboarding and notes.set_subjects re-applies the server-side VCE rules.

See IMPLEMENTATION_SPEC.md section 3 (SettingsForm) and section 2
(notes.get_settings / notes.update_settings / notes.set_subjects).
"""

import anvil
import anvil.server
import datetime
from anvil import (
    ColumnPanel, FlowPanel, Label, CheckBox, DatePicker, TextBox, DropDown,
    Button, Link, Notification, alert, confirm,
)

from ..common import (
    make_top_bar, SubjectPicker, apply_theme, set_session_settings,
)

# Mirrors _constants.ENGLISH_GROUP / MATHS_GROUP (client can't import server
# modules; keep in sync with OnboardingForm).
ENGLISH_GROUP = ('English', 'English as an Additional Language',
                 'English Language', 'Literature')
MATHS_GROUP = ('Foundation Mathematics', 'General Mathematics',
               'Mathematical Methods', 'Specialist Mathematics')

# Reminder-day options offered in the UI (spec §3): N days before due date.
REMINDER_DAY_OPTIONS = (14, 7, 3, 2, 1)

# Static IANA Australian timezones (spec §3 / Decision 2).
TIMEZONES = (
    'Australia/Sydney', 'Australia/Melbourne', 'Australia/Brisbane',
    'Australia/Perth', 'Australia/Darwin', 'Australia/Adelaide', 'Australia/Hobart',
)

_NUM_TERMS = 4

# One-click preset: Victorian government school term dates for 2026.
_VIC_2026_TERMS = (
    (datetime.date(2026, 1, 28), datetime.date(2026, 4, 2)),
    (datetime.date(2026, 4, 20), datetime.date(2026, 6, 26)),
    (datetime.date(2026, 7, 13), datetime.date(2026, 9, 18)),
    (datetime.date(2026, 10, 5), datetime.date(2026, 12, 18)),
)


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

        preset = Link(text='Load VIC 2026 term dates', foreground='#2f6fd0')
        preset.set_event_handler('click', self._on_load_preset)
        body.add_component(preset)

        year_row = FlowPanel()
        year_row.add_component(Label(text='School year'))
        self._school_year_tb = TextBox(placeholder='e.g. 2026')
        year_row.add_component(self._school_year_tb)
        body.add_component(year_row)

        # --- Timezone ---
        body.add_component(Label(text='Timezone', role='heading'))
        self._timezone_dd = DropDown(items=list(TIMEZONES))
        body.add_component(self._timezone_dd)

        # --- Theme (spec §12) ---
        body.add_component(Label(text='Theme', role='heading'))
        self._theme_dd = DropDown(items=[('Light', 'light'), ('Dark', 'dark')])
        body.add_component(self._theme_dd)

        # --- Subjects (spec §11: locked; deliberate change flow only) ---
        body.add_component(Label(text='My subjects', role='heading'))
        self._subjects_row = FlowPanel()
        body.add_component(self._subjects_row)
        body.add_component(Label(
            text='Subjects are locked in — they drive the parser, dashboard '
                 'and exam timetable.',
            font_size=12, italic=True, foreground='#9aa0a6'))
        change_btn = Button(text='Change subjects…', role='secondary')
        change_btn.set_event_handler('click', self._on_change_subjects)
        body.add_component(change_btn)

        # --- Save ---
        save_btn = Button(text='Save', role='primary')
        save_btn.set_event_handler('click', self._on_save_click)
        body.add_component(save_btn)

        self._subjects = []
        self._load_settings()

    def _load_settings(self):
        try:
            s = anvil.server.call('get_settings')
        except Exception as e:
            Notification("Couldn't load settings: %s" % e, style='danger', timeout=4).show()
            return
        # Heal the per-session cache with this fresh copy (e.g. after an
        # import changed settings server-side).
        set_session_settings(s)

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

        self._theme_dd.selected_value = s.get('theme') or 'light'
        self._subjects = s.get('subjects') or []
        self._render_subject_chips()

    def _render_subject_chips(self):
        self._subjects_row.clear()
        if not self._subjects:
            self._subjects_row.add_component(Label(
                text='No subjects locked in yet.', italic=True,
                foreground='#9aa0a6'))
            return
        for s in self._subjects:
            self._subjects_row.add_component(Label(text=s, role='chip'))

    def _on_change_subjects(self, **event_args):
        proceed = confirm(
            "Changing your subjects re-tailors the parser, dashboard filter "
            "and exam timetable. Assessments you've already saved keep their "
            "subject either way. Continue?")
        if not proceed:
            return

        try:
            catalog = anvil.server.call('get_subject_catalog')
        except Exception as e:
            Notification("Couldn't load the subject list: %s" % e,
                         style='danger', timeout=4).show()
            return

        # Re-open the picker with the user's own ticks after any failed
        # attempt — a validation error must never throw their selection away.
        selected = self._subjects
        while True:
            picker = SubjectPicker(catalog, selected=selected)
            if not alert(picker, title='Change subjects', large=True,
                         buttons=[('Save subjects', True), ('Cancel', False)]):
                return
            selected = picker.get_selection()

            if not any(s in MATHS_GROUP for s in selected):
                Notification("Select at least one mathematics study.",
                             style='danger', timeout=6).show()
                continue
            if not any(s in ENGLISH_GROUP for s in selected):
                if not confirm(
                        "No English-group study selected — 'English' will be "
                        "added automatically (every VCE program includes "
                        "one). Continue?"):
                    continue

            try:
                settings = anvil.server.call('set_subjects', selected)
            except Exception as e:
                Notification(str(e), style='danger', timeout=6).show()
                continue
            break

        set_session_settings(settings)
        self._subjects = settings.get('subjects') or []
        self._render_subject_chips()
        Notification("Subjects updated.", style='success', timeout=4).show()

    def _on_load_preset(self, **event_args):
        """Fill the term pickers with the VIC 2026 dates (user still clicks Save)."""
        for (start_dp, end_dp), (s, e) in zip(self._term_pickers, _VIC_2026_TERMS):
            start_dp.date = s
            end_dp.date = e
        self._school_year_tb.text = '2026'
        Notification("VIC 2026 term dates loaded — click Save to apply.",
                     style='info', timeout=4).show()

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
                Notification("School year must be a whole number.", style='danger', timeout=4).show()
                return
        else:
            fields['school_year'] = None

        tz = self._timezone_dd.selected_value
        if tz:
            fields['timezone'] = tz

        theme = self._theme_dd.selected_value
        if theme:
            fields['theme'] = theme

        try:
            settings = anvil.server.call('update_settings', fields)
            set_session_settings(settings)
            apply_theme(settings.get('theme'))
            Notification("Settings saved.", style='success', timeout=4).show()
        except Exception as e:
            Notification("Couldn't save settings: %s" % e, style='danger', timeout=4).show()
