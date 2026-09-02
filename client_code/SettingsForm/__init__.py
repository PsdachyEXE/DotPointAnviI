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
    toast_warn, set_field_error, clear_field_errors, friendly_error,
    SubjectPicker, apply_theme, set_session_settings, get_session_settings,
    from_iso, to_iso,
)

# --- mirrors of server constants --------------------------------------------
# Anvil client code cannot import a server module, so these two tuples are hand
# copies. Each one names the constant it copies so the original is findable:
#
#   ENGLISH_GROUP  copies  server_code/_constants.py  ENGLISH_GROUP
#   MATHS_GROUP    copies  server_code/_constants.py  MATHS_GROUP
#
# They must hold EXACTLY what the server holds, because notes._clean_subjects
# applies the same two membership tests to the same selection: any entry the
# client's copy is missing is a selection this form rejects and the server would
# have accepted. That is not hypothetical — MATHS_GROUP here was missing
# 'Mathematics' (the parser's generic maths study), so a student tracking that
# study could not get past "select at least one mathematics study" on a screen
# the server would have saved.
#
# 'Mathematics' is not offered by the picker (SUBJECT_GROUPS deliberately omits
# it), so copying it here cannot put a non-study in front of the student; all it
# does is make the client's ANSWER to "have you picked a maths study?" identical
# to the server's.
ENGLISH_GROUP = ('English', 'English as an Additional Language',
                 'English Language', 'Literature')
MATHS_GROUP = ('Mathematics', 'Foundation Mathematics', 'General Mathematics',
               'Mathematical Methods', 'Specialist Mathematics')

# The two VCE program rules, worded ONCE. The same sentences appear in
# OnboardingForm, and the maths sentence is word-for-word the one
# notes._clean_subjects raises, so a student meets one wording for one rule no
# matter which screen they are on and no matter which side caught them.
MATHS_RULE_MESSAGE = ('Select at least one mathematics study '
                      '(Foundation, General, Methods or Specialist).')
ENGLISH_RULE_MESSAGE = ("You haven't picked an English-group study. Every VCE "
                        "program includes one, so 'English' will be added "
                        "automatically. Continue?")
NO_SELECTION_MESSAGE = 'Pick your subjects first.'

# Reminder-day options offered in the UI (spec §3): N days before due date.
# Mirrors AssessmentEditorForm.REMINDER_DAY_OPTIONS — the same choices must be
# offered here as per-assessment. tests/test_constants_integrity.py
# (suite_reminder_option_mirrors) compares the two copies, so a change to one
# without the other fails the suite rather than shipping.
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
        self._loaded = False
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

        Each term is one make_field('Term N', row-of-two-pickers), so the four
        terms form a column of aligned Start/End pairs AND each one owns a place
        to put its own error message (make_field's error_label). The school year
        sits in this card rather than with the display settings because the
        preset link fills in both at once — a value silently changing in a card
        the student is not looking at reads as a bug.
        """
        card = make_card()
        card.add_component(make_section_header(
            'School terms', 'Lets the parser resolve "term 2 week 3" dates'))

        # WHY THE RULES BELOW ARE WORTH EXPLAINING ON SCREEN. The parser resolves
        # "Term 2 week 3" by counting weeks forward from that term's start date
        # and checking the result still falls inside the term. Dates that run
        # backwards, or two terms claiming the same weeks, make that impossible —
        # and the failure is silent: no error appears, the phrase simply never
        # becomes a due date. A student would have no way to connect the two, so
        # the hint says it before they type rather than after.
        card.add_component(Label(
            text='Each term must start before it ends, and terms cannot '
                 'overlap. If the dates cannot be resolved, phrases like '
                 '"Term 2 week 3" quietly fail to become a due date — nothing '
                 'looks broken, the date just never appears.',
            role='micro'))

        self._term_pickers = []  # [(start_DatePicker, end_DatePicker), ...]
        self._term_fields = []   # the make_field wrappers, same order
        for term_number in range(1, _NUM_TERMS + 1):
            start_dp = DatePicker(placeholder='Start')
            end_dp = DatePicker(placeholder='End')
            field = make_field('Term %d' % term_number,
                               make_row(start_dp, end_dp))
            card.add_component(field)
            self._term_pickers.append((start_dp, end_dp))
            self._term_fields.append(field)

        preset = Link(text='Load VIC 2026 term dates', role='t-accent')
        preset.set_event_handler('click', self._on_load_preset)
        card.add_component(make_row(preset))

        self._school_year_tb = TextBox(placeholder='e.g. 2026')
        self._school_year_field = make_field(
            'School year', self._school_year_tb,
            hint='The year these term dates belong to.')
        card.add_component(self._school_year_field)
        return card

    def _build_display_card(self):
        """Timezone (spec Decision 2) and theme (spec §12).

        Both answer "how should DotPoint present itself to me", so they share a
        card; neither needs an explanation longer than one line.
        """
        card = make_card()
        card.add_component(make_section_header('Timezone & theme'))

        # required=True because there is no sensible "no timezone": every
        # "due today", every countdown and every reminder email is worked out in
        # it, so a blank one is refused on save rather than saved as nothing.
        self._timezone_dd = DropDown(items=list(TIMEZONES))
        self._timezone_field = make_field(
            'Timezone', self._timezone_dd, required=True,
            hint='Every "due today" and countdown is worked out in this zone.')
        card.add_component(self._timezone_field)

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
            settings = anvil.server.call('get_settings')
        except Exception as e:
            toast_error(friendly_error(
                e, "Couldn't load your settings. Check your connection and "
                   "reload the page."))
            return
        # Only now is self._subjects trustworthy. _on_change_subjects checks
        # this before seeding the picker (see the comment there).
        self._loaded = True
        # Heal the per-session cache with this fresh copy (e.g. after an
        # import changed settings server-side).
        set_session_settings(settings)

        reminder_days = settings.get('default_reminder_days') or []
        for d, cb in self._day_checks.items():
            cb.checked = d in reminder_days

        self._notifications_cb.checked = bool(
            settings.get('notifications_enabled'))

        terms_by_num = {
            t.get('term'): t
            for t in (settings.get('school_terms') or [])
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

        year = settings.get('school_year')
        self._school_year_tb.text = '' if year is None else str(year)

        tz = settings.get('timezone') or 'Australia/Melbourne'
        if tz not in TIMEZONES:
            # Keep an out-of-list stored value selectable.
            self._timezone_dd.items = list(TIMEZONES) + [tz]
        self._timezone_dd.selected_value = tz

        self._theme_dd.selected_value = settings.get('theme') or 'light'
        self._subjects = settings.get('subjects') or []
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
        for subject in self._subjects:
            chips.add_component(make_chip(subject))
        self._subjects_panel.add_component(chips)

    # --- events --------------------------------------------------------------

    def _on_change_subjects(self, **event_args):
        # Guard against overwriting a locked subject list with nothing. If the
        # page-load get_settings() failed, self._subjects is still [] — the
        # picker would open with no ticks, and saving it would wipe the
        # student's real subjects on the back of a transient network error.
        # Try once more to find out what they actually have before offering
        # the picker at all.
        if not self._loaded:
            try:
                self._subjects = get_session_settings(refresh=True).get('subjects') or []
                self._loaded = True
            except Exception as e:
                toast_error(friendly_error(
                    e, "Couldn't read your current subjects, so changing them "
                       "isn't safe right now. Try again in a moment."))
                return

        proceed = confirm(
            "Changing your subjects re-tailors the parser, dashboard filter "
            "and exam timetable. Assessments you've already saved keep their "
            "subject either way. Continue?")
        if not proceed:
            return

        try:
            catalog = anvil.server.call('get_subject_catalog')
        except Exception as e:
            toast_error(friendly_error(
                e, "Couldn't load the subject list. Check your connection and "
                   "try again."))
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

            # Same three checks, in the same order, with the same sentences as
            # OnboardingForm — the rule a student meets must not depend on which
            # screen they changed their subjects from. The picker is inside an
            # alert(), so there is no field on the page to attach a message to;
            # a toast is the only visible channel here.
            if not selected:
                toast_warn(NO_SELECTION_MESSAGE)
                continue
            if not any(s in MATHS_GROUP for s in selected):
                toast_error(MATHS_RULE_MESSAGE)
                continue
            if not any(s in ENGLISH_GROUP for s in selected):
                if not confirm(ENGLISH_RULE_MESSAGE):
                    continue

            try:
                settings = anvil.server.call('set_subjects', selected)
            except Exception as e:
                toast_error(friendly_error(
                    e, "Couldn't save those subjects. Check your connection "
                       "and try again."))
                continue
            break

        set_session_settings(settings)
        self._subjects = settings.get('subjects') or []
        self._render_subject_chips()
        toast("Subjects updated.")

    def _on_load_preset(self, **event_args):
        """Fill the term pickers with the VIC 2026 dates (user still clicks Save)."""
        for (start_dp, end_dp), (start_date, end_date) in zip(
                self._term_pickers, _VIC_2026_TERMS):
            start_dp.date = start_date
            end_dp.date = end_date
        self._school_year_tb.text = '2026'
        # The preset replaces whatever was in the pickers, so any message left
        # over from a previous failed save is now describing dates that are gone.
        clear_field_errors(self._school_year_field, *self._term_fields)
        toast("VIC 2026 term dates loaded — click Save to apply.", style='info')

    # --- save-time validation ------------------------------------------------
    # These three readers are the client half of SAT criterion 7.3 for this
    # screen. They are ADDITIVE: notes._validate_settings still re-checks
    # everything they check, and it, not this form, is the authority. What they
    # add is speed and place — the student is told which of the eight date
    # pickers is wrong, next to that picker, without a round trip.
    #
    # Two deliberate limits:
    #   * they run on SAVE, never on change. A message that appears while the
    #     student is still choosing the second half of a date pair would be
    #     wrong more often than right.
    #   * every rule below is worded from the server's own message for the same
    #     rule, so the sentence does not change depending on which side caught it.
    #
    # Each returns (ok, value) and has already written its own field message when
    # ok is False, so _on_save_click can run all three and show every problem at
    # once instead of one per attempt.

    def _read_terms(self):
        """The eight term pickers -> the server's school_terms shape."""
        terms, ok = [], True

        for term_number, (start_dp, end_dp) in enumerate(
                self._term_pickers, start=1):
            field = self._term_fields[term_number - 1]
            start_date, end_date = start_dp.date, end_dp.date

            # COMPLETENESS. A half-filled pair used to be dropped in silence:
            # the student filled in a start date, saved, and the term simply was
            # not there afterwards, with nothing said about which half was
            # ignored. Leaving both empty is still fine — that is how a term is
            # cleared.
            if bool(start_date) != bool(end_date):
                set_field_error(
                    field, 'Term %d needs both a start date and an end date '
                           '(or leave both empty).' % term_number)
                ok = False
                continue
            if start_date is None:
                continue

            # REASONABLENESS. Same rule and same sentence as the server's
            # require_not_after on this pair.
            if start_date > end_date:
                set_field_error(
                    field, 'Term %d start date cannot be after Term %d end '
                           'date. Check the two dates.'
                           % (term_number, term_number))
                ok = False
                continue

            terms.append({'term': term_number,
                          'start_date': to_iso(start_date),
                          'end_date': to_iso(end_date)})

        if not ok:
            return False, None

        # UNIQUENESS. This form assigns each term number exactly once, from its
        # four fixed rows, so this cannot fire from this screen. It is written
        # against the list actually being sent anyway, so the client applies the
        # same three whole-value rules as the server rather than two of them.
        seen_numbers = set()
        for term in terms:
            if term['term'] in seen_numbers:
                set_field_error(
                    self._term_fields[term['term'] - 1],
                    'Term %d is listed twice. Each term can only have one set '
                    'of dates.' % term['term'])
                ok = False
            seen_numbers.add(term['term'])

        # OVERLAP. 'YYYY-MM-DD' strings sort chronologically, so sorting the
        # strings is the same as sorting the dates — which is how the server
        # does it too. The message goes on the LATER term, because that is the
        # one whose start date has to move.
        ordered = sorted(terms, key=lambda t: t['start_date'])
        for earlier, later in zip(ordered, ordered[1:]):
            if later['start_date'] <= earlier['end_date']:
                set_field_error(
                    self._term_fields[later['term'] - 1],
                    'Term %d and Term %d overlap. School terms cannot share '
                    'dates — check their start and end dates.'
                    % (earlier['term'], later['term']))
                ok = False

        return (True, terms) if ok else (False, None)

    def _read_school_year(self):
        """The school-year box -> an int, or None when the student cleared it.

        Only the TYPE is checked here. The plausible-year range lives in
        notes._validate_settings and nowhere else: a second copy of the bounds
        in the client is exactly the kind of duplicate rule that drifts, and the
        server's message for it is already a sentence, which _on_save_click puts
        under this field.
        """
        year_text = (self._school_year_tb.text or '').strip()
        if not year_text:
            return True, None
        try:
            return True, int(year_text)
        except ValueError:
            set_field_error(self._school_year_field,
                            'School year must be a whole number, like 2026.')
            return False, None

    def _read_timezone(self):
        """The timezone dropdown -> an IANA name; blank is refused.

        The NAME is only checked for presence here. Whether it is a zone that
        actually exists is a question only the tz database can answer, so the
        server's require_timezone owns that and its answer is shown under this
        field by _on_save_click.
        """
        timezone_name = self._timezone_dd.selected_value
        if not timezone_name:
            set_field_error(
                self._timezone_field,
                'Choose a timezone. Every "due today" and countdown is worked '
                'out in it.')
            return False, None
        return True, timezone_name

    def _field_for_message(self, message):
        """The field a server message is about, or None if it names no field.

        The server raises one sentence, not a field identifier — it has no idea
        what this page looks like, and giving it one would make every validator
        answerable to a form. So the routing is done here, by looking for the
        field label the message already quotes ('Timezone', 'School year',
        'Term 2 start date'). A message that names nothing on this page falls
        through to None and is toasted, which is the honest outcome: better a
        toast than a message pinned under the wrong control.
        """
        lowered = message.lower()
        if 'timezone' in lowered:
            return self._timezone_field
        if 'school year' in lowered:
            return self._school_year_field
        for term_number, field in enumerate(self._term_fields, start=1):
            if 'term %d' % term_number in lowered:
                return field
        return None

    def _on_save_click(self, **event_args):
        # Wipe every message first, so nothing left over from the last attempt
        # is read as a verdict on this one.
        clear_field_errors(self._school_year_field, self._timezone_field,
                           *self._term_fields)

        # All three run before anything is reported, so a student with a
        # backwards term AND a blank timezone sees both now rather than one,
        # then the other on the next click.
        ok_terms, terms = self._read_terms()
        ok_year, school_year = self._read_school_year()
        ok_timezone, timezone_name = self._read_timezone()
        if not (ok_terms and ok_year and ok_timezone):
            # Each message is already under its own field; this only says that
            # there is something to look at, because the offending field can be
            # scrolled off the screen. toast_warn, not toast_error: nothing has
            # failed, the student just has to finish something.
            toast_warn('Check the highlighted fields and try again.')
            return

        fields = {
            'default_reminder_days': sorted(
                (d for d, cb in self._day_checks.items() if cb.checked),
                reverse=True,
            ),
            'notifications_enabled': bool(self._notifications_cb.checked),
            'school_terms': terms,
            'school_year': school_year,
            'timezone': timezone_name,
        }

        theme = self._theme_dd.selected_value
        if theme:
            fields['theme'] = theme

        try:
            settings = anvil.server.call('update_settings', fields)
        except Exception as e:
            message = friendly_error(
                e, "Couldn't save your settings. Check your connection and "
                   "try again.")
            field = self._field_for_message(message)
            if field is None:
                toast_error(message)
            else:
                set_field_error(field, message)
                toast_warn('Check the highlighted field and try again.')
            return

        set_session_settings(settings)
        apply_theme(settings.get('theme'))
        toast("Settings saved.")
