import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""SettingsForm - per-user preferences (reminders, school year/terms, timezone,
theme, and the deliberate change-subjects flow).

Layout (spec §14): a page title, then one card per decision the student makes -
Reminders, School terms, Timezone & theme, My subjects - and a single Save at the
bottom. Grouping matters here because Settings is the one screen with unrelated
controls on it: cards give each group a visible boundary, so the student can find
"the term dates" without reading the whole page. Nothing on this form styles
itself; every colour and size comes from a role the stylesheet paints, which is
what lets the same markup work in the light and dark themes.

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
    ColumnPanel, Label, CheckBox, DatePicker, TextBox, DropDown,
    Button, Link, alert, confirm,
)

from ..common import (
    make_top_bar, make_page, make_page_title, make_section_header, make_card,
    make_row, make_field, make_chip, make_empty_state, toast, toast_error,
    SubjectPicker, apply_theme, set_session_settings, from_iso, to_iso,
)

# Mirrors _constants.ENGLISH_GROUP / MATHS_GROUP (client can't import server
# modules; keep in sync with OnboardingForm).
ENGLISH_GROUP = ('English', 'English as an Additional Language',
                 'English Language', 'Literature')
MATHS_GROUP = ('Foundation Mathematics', 'General Mathematics',
               'Mathematical Methods', 'Specialist Mathematics')

# Reminder-day options offered in the UI (spec §3): N days before due date.
# Mirrors AssessmentEditorForm.REMINDER_DAY_OPTIONS — the same choices must be
# offered here as per-assessment; the offline constants suite (docs/TESTING.md) asserts the two copies match.
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


class SettingsForm(ColumnPanel):
    """The settings page: top bar, four cards, one Save.

    Each card is built by its own _build_* method. Splitting the constructor up
    this way keeps every control next to the comment that justifies it, and means
    the page order can be changed in one place (__init__) without touching the
    controls themselves. The _build_* methods also stash the widgets they create
    on self, because _load_settings and _on_save_click read them all back.
    """

    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self.add_component(make_top_bar(active='settings'))

        body = make_page()
        self.add_component(body)
        body.add_component(make_page_title(
            'Settings',
            'Reminders, term dates, timezone and the subjects DotPoint works from.'))

        body.add_component(self._build_reminders_card())
        body.add_component(self._build_terms_card())
        body.add_component(self._build_display_card())
        body.add_component(self._build_subjects_card())

        # One Save for the whole page. It sits after the last card, on its own
        # row, so it reads as the commit action for everything above it — the
        # subject change is the one setting that saves itself, because it needs
        # its own confirmation.
        save_btn = Button(text='Save', role='primary')
        save_btn.set_event_handler('click', self._on_save_click)
        body.add_component(make_row(save_btn))

        self._subjects = []
        self._load_settings()

    # --- card builders -------------------------------------------------------

    def _build_reminders_card(self):
        """Reminders: the N-days-before choices, plus the email master switch.

        The days are a small fixed set the student may pick any number of, so
        they are rendered as pills (role='pill' restyles a CheckBox as a toggle):
        all five options and the current answer read in a single line, where a
        stacked column of tick boxes was the tallest thing on the page.
        """
        card = make_card()
        card.add_component(make_section_header(
            'Reminders', 'How far ahead of a due date to be reminded'))

        self._day_checks = {}
        days_row = make_row()
        for d in REMINDER_DAY_OPTIONS:
            cb = CheckBox(text='%d day' % d if d == 1 else '%d days' % d,
                          role='pill')
            self._day_checks[d] = cb
            days_row.add_component(cb)
        card.add_component(days_row)

        self._notifications_cb = CheckBox(text='Enable email reminders')
        card.add_component(self._notifications_cb)
        return card

    def _build_terms_card(self):
        """School terms, and the year those terms belong to.

        Each term is one make_row(label, start, end), so the four rows form a
        column of aligned Start/End pairs instead of four differently-indented
        lines. The school year sits in this card rather than with the display
        settings because the preset link fills in both at once — a value silently
        changing in a card the student is not looking at reads as a bug.
        """
        card = make_card()
        card.add_component(make_section_header(
            'School terms', 'Lets the parser resolve "term 2 week 3" dates'))

        self._term_pickers = []  # [(start_DatePicker, end_DatePicker), ...]
        for term in range(1, _NUM_TERMS + 1):
            start_dp = DatePicker(placeholder='Start')
            end_dp = DatePicker(placeholder='End')
            card.add_component(make_row(
                Label(text='Term %d' % term, role='caption'), start_dp, end_dp))
            self._term_pickers.append((start_dp, end_dp))

        preset = Link(text='Load VIC 2026 term dates', role='t-accent')
        preset.set_event_handler('click', self._on_load_preset)
        card.add_component(make_row(preset))

        self._school_year_tb = TextBox(placeholder='e.g. 2026')
        card.add_component(make_field('School year', self._school_year_tb))
        return card

    def _build_display_card(self):
        """Timezone (spec Decision 2) and theme (spec §12).

        Both answer "how should DotPoint present itself to me", so they share a
        card; neither needs an explanation longer than one line.
        """
        card = make_card()
        card.add_component(make_section_header('Timezone & theme'))

        self._timezone_dd = DropDown(items=list(TIMEZONES))
        card.add_component(make_field(
            'Timezone', self._timezone_dd,
            hint='Every "due today" and countdown is worked out in this zone.'))

        self._theme_dd = DropDown(items=[('Light', 'light'), ('Dark', 'dark')])
        card.add_component(make_field('Theme', self._theme_dd))
        return card

    def _build_subjects_card(self):
        """The locked-in subjects (spec §11).

        Read-only chips plus one deliberate way out. The chips live in their own
        panel so _render_subject_chips can clear and redraw just that panel after
        a successful change, without rebuilding the card around it.
        """
        card = make_card()
        card.add_component(make_section_header('My subjects'))

        self._subjects_panel = ColumnPanel()
        card.add_component(self._subjects_panel)

        card.add_component(Label(
            text='Subjects drive the parser, the dashboard filter and the exam '
                 'timetable.',
            role='micro'))

        change_btn = Button(text='Change subjects…', role='secondary')
        change_btn.set_event_handler('click', self._on_change_subjects)
        card.add_component(make_row(change_btn))
        return card

    # --- data ----------------------------------------------------------------

    def _load_settings(self):
        try:
            s = anvil.server.call('get_settings')
        except Exception as e:
            toast_error("Couldn't load settings: %s" % e)
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
                # start_date / end_date are always 'YYYY-MM-DD' strings — the
                # server validates that shape in notes._validate_school_terms —
                # so the shared parser reads them the same way everywhere else
                # in the app does, and still gives None for a missing term.
                start_dp.date = from_iso(t.get('start_date'))
                end_dp.date = from_iso(t.get('end_date'))

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
        """Redraw the subject chips from self._subjects.

        The empty case is a real empty state rather than a stray sentence: it is
        reachable (an import can clear subjects), and the 'Change subjects…'
        button directly below it is already the way out, so the block itself
        carries no duplicate action.
        """
        self._subjects_panel.clear()
        if not self._subjects:
            self._subjects_panel.add_component(make_empty_state(
                'No subjects locked in yet',
                'Choose your studies so DotPoint knows what to look for.'))
            return
        chips = make_row()
        for s in self._subjects:
            chips.add_component(make_chip(s))
        self._subjects_panel.add_component(chips)

    # --- events --------------------------------------------------------------

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
            toast_error("Couldn't load the subject list: %s" % e)
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
                toast_error("Select at least one mathematics study.")
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
                toast_error(str(e))
                continue
            break

        set_session_settings(settings)
        self._subjects = settings.get('subjects') or []
        self._render_subject_chips()
        toast("Subjects updated.")

    def _on_load_preset(self, **event_args):
        """Fill the term pickers with the VIC 2026 dates (user still clicks Save)."""
        for (start_dp, end_dp), (s, e) in zip(self._term_pickers, _VIC_2026_TERMS):
            start_dp.date = s
            end_dp.date = e
        self._school_year_tb.text = '2026'
        toast("VIC 2026 term dates loaded — click Save to apply.", style='info')

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
                    'start_date': to_iso(start_dp.date),
                    'end_date': to_iso(end_dp.date),
                })
        fields['school_terms'] = terms

        year_text = (self._school_year_tb.text or '').strip()
        if year_text:
            try:
                fields['school_year'] = int(year_text)
            except ValueError:
                toast_error("School year must be a whole number.")
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
            toast("Settings saved.")
        except Exception as e:
            toast_error("Couldn't save settings: %s" % e)
