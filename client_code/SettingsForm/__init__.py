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

# The Victorian school year is four terms, always. This is the number of ROWS
# the terms card draws, not a count of whatever happens to be stored, so an
# unconfigured term shows as an empty pair rather than being absent.
_NUM_TERMS = 4

# One-click preset: Victorian government school term dates for 2026.
#
# (start, end) pairs of date OBJECTS, positionally in term order 1-4 — they are
# written straight into DatePicker.date, and only _read_terms turns them into
# the ISO text the column stores. Typing eight dates correctly is the most
# tedious thing this app asks of a student, and getting one wrong breaks FR15
# silently, so the common case is offered as a link. It only fills the pickers
# in: a school whose dates differ can correct a row before pressing Save.
_VIC_2026_TERMS = (
    (datetime.date(2026, 1, 28), datetime.date(2026, 4, 2)),
    (datetime.date(2026, 4, 20), datetime.date(2026, 6, 26)),
    (datetime.date(2026, 7, 13), datetime.date(2026, 9, 18)),
    (datetime.date(2026, 10, 5), datetime.date(2026, 12, 18)),
)


class SettingsForm(ColumnPanel):
    """The #settings page: top bar, four cards, one Save.

    The one screen where every per-account preference is decided — how many days
    before a due date to be reminded, whether reminder email is on at all, the
    four school term date pairs, the timezone the whole app does its date maths
    in, the light/dark theme, and the locked list of VCE subjects.

    WHICH REQUIREMENTS IT IMPLEMENTS
      FR15  user_settings.school_terms is the per-user config FR15 names. The
            parser resolves "Term 2 Week 3" by counting weeks from a term's
            start_date, so these eight date pickers are the only thing feeding
            that feature; with them empty, FR15 falls through to LOW confidence.
      FR13 / FR14  the day pills and the email switch write
            default_reminder_days and notifications_enabled, the two columns
            reminders.run_reminder_check reads before it sends anything.
      FR03  the same day list is the default the manual-entry form seeds its
            reminder multi-select from (AssessmentEditorForm falls back to
            [7, 2] only when this is unset).
      FR06  the locked subjects head the dashboard's subject filter list
            (dashboard.get_dashboard puts them before any legacy data subject).
      NFR03  nothing on this page names a user. Every callable it makes
            re-derives the settings row from current_user server-side, so a
            student can only ever read or write their own row.
    Timezone (Decision 2), theme (spec §12) and the subject list (spec §11) are
    spec items rather than numbered FRs. The subject list then feeds FR16's
    alias ranking in the parser and picks the rows of the exam timetable
    (spec §13) as well as the FR06 filter above.

    HOW IT IS CONSTRUCTED
      SettingsForm() — no arguments of its own; **properties are ColumnPanel's,
      and Main._make_form() passes none. There are no modes: the same four cards
      are always built, then filled in from the server by _load_settings().

    SERVER CALLABLES IT DEPENDS ON
      get_settings         once, during construction (_load_settings)
      update_settings      on Save, with a PATCH of five fields (+ theme)
      get_subject_catalog  only when 'Change subjects…' is clicked
      set_subjects         the sole writer of user_settings.subjects

    WHAT IT HANDS BACK
      Nothing. This is a whole page added as Main's child, not a dialog, so
      there is no return value and no raised event. What it passes on instead is
      the server's echo of the saved settings, pushed into common's per-session
      cache by set_session_settings() so the router, the editor and the
      dashboard stop serving the pre-save copy, plus apply_theme() to repaint
      the window immediately.

    Each card is built by its own _build_* method. Splitting the constructor up
    this way keeps every control next to the comment that justifies it, and means
    the page order can be changed in one place (__init__) without touching the
    controls themselves. The _build_* methods also stash the widgets they create
    on self, because _load_settings and _on_save_click read them all back.
    """

    def __init__(self, **properties):
        """Build the four cards, then load the current values into them.

        Takes no arguments of its own. The page is assembled empty and then
        filled: every control exists before a single value arrives, so a slow or
        failed get_settings leaves a usable page rather than half a page.
        """
        super().__init__(**properties)
        # 1. The form IS the page shell, so its own spacing is removed and the
        #    padding is left to make_page below. Anything else double-spaces the
        #    top bar away from the window edge.
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        # 2. The top bar spans the full window; active='settings' is what makes
        #    the Settings link the highlighted one.
        self.add_component(make_top_bar(active='settings'))

        # 3. Everything else lives inside the centred page column, so this
        #    screen lines up with the dashboard and the notes list.
        body = make_page()
        self.add_component(body)
        body.add_component(make_page_title(
            'Settings',
            'Reminders, term dates, timezone and the subjects DotPoint works from.'))

        # 4. Card order is the order a student thinks about these settings in:
        #    the thing they change most often (reminders) first, the thing they
        #    set once a year (terms) next, then presentation, then the subject
        #    list that is meant to be changed almost never.
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

        # 5. Two pieces of state, both set BEFORE the load so they exist even if
        #    the server call raises:
        #      _subjects  the locked subject list the chips are drawn from and
        #                 the picker is seeded with.
        #      _loaded    False until get_settings has actually answered. It is
        #                 the difference between "this student has no subjects"
        #                 and "we never found out" — see _on_change_subjects,
        #                 where saving the wrong one of those wipes real data.
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

        Returns the card panel. Stashes two things on self for later:
          _day_checks       {day_int: CheckBox} keyed by the NUMBER of days, so
                            _load_settings and _on_save_click can go straight
                            from a stored value to its control and back without
                            parsing the label text.
          _notifications_cb the master switch for reminder email; when it is off
                            reminders.run_reminder_check sends the student
                            nothing at all, whatever the day list says.
        """
        card = make_card()
        card.add_component(make_section_header(
            'Reminders', 'How far ahead of a due date to be reminded'))

        self._day_checks = {}
        days_row = make_row()
        for d in REMINDER_DAY_OPTIONS:
            # '1 day', not '1 days'. Every option here is a plain count, so the
            # singular is the only irregular case worth handling.
            cb = CheckBox(text='%d day' % d if d == 1 else '%d days' % d,
                          role='pill')
            self._day_checks[d] = cb
            days_row.add_component(cb)
        card.add_component(days_row)

        # Not a pill: this one is not a member of the set above, it governs the
        # whole set, so it deliberately looks like a different kind of control.
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

        This card is the whole of FR15's configuration. Returns the card panel
        and stashes two parallel lists on self, both in term order 1-4:
          _term_pickers  [(start_DatePicker, end_DatePicker), ...] — the values
          _term_fields   [make_field wrapper, ...]                 — the places
                         a message about term N can be written
        They are kept parallel rather than combined so index N-1 means Term N in
        both, which is how _read_terms and _field_for_message address a term.
        """
        card = make_card()
        card.add_component(make_section_header(
            'School terms', 'Lets the parser resolve "term 2 week 3" dates'))

        # WHY THE RULES BELOW ARE WORTH EXPLAINING ON SCREEN. The parser resolves
        # "Term 2 week 3" by counting weeks forward from that term's start date
        # and then checking the result still falls inside the term
        # (nlp._try_parse_week_phrase tests start <= due <= end). A term stored
        # back-to-front therefore fails that test for EVERY week of that term,
        # and it fails silently: no error appears anywhere, the phrase simply
        # never becomes a due date. Overlapping terms are the milder problem —
        # each phrase still resolves, but a date can belong to two different
        # school weeks at once, so the answer cannot be trusted, and the server
        # refuses the save. A student would have no way to connect either of
        # those to their term dates, so the hint says it before they type.
        card.add_component(Label(
            text='Each term must start before it ends, and terms cannot '
                 'overlap. If the dates cannot be resolved, phrases like '
                 '"Term 2 week 3" quietly fail to become a due date — nothing '
                 'looks broken, the date just never appears.',
            role='micro'))

        # Four fixed rows, built from _NUM_TERMS rather than from whatever the
        # student happens to have stored: a Victorian school year always has
        # four terms, so an empty pair means "not set yet", never "missing".
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

        # A Link, not a Button: it fills the pickers in, it does not save, and a
        # second primary-looking control next to Save would suggest it did.
        preset = Link(text='Load VIC 2026 term dates', role='t-accent')
        preset.set_event_handler('click', self._on_load_preset)
        card.add_component(make_row(preset))

        # A TextBox rather than a number picker: the year is typed once a year,
        # and _read_school_year turns it into an int (or refuses it) on save.
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

        Returns the card panel and stashes _timezone_dd / _timezone_field (the
        field wrapper is kept because the timezone is the one control on this
        card that can be refused, by this form or by the server) and _theme_dd.

        DATA. The timezone value is an IANA name string, one of TIMEZONES above
        or whatever was already stored; the theme value is 'light' or 'dark',
        which is exactly the pair notes._VALID_THEMES accepts. Both are saved on
        the user's own settings row, not in browser storage, so the choice
        follows the account onto the school laptop and back again.
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

        # (label, value) pairs: the student reads 'Light'/'Dark', the server is
        # sent 'light'/'dark'. Sending the display text instead would be
        # rejected by require_choice, which is why the pairs are written here
        # rather than relying on a plain list of strings.
        self._theme_dd = DropDown(items=[('Light', 'light'), ('Dark', 'dark')])
        card.add_component(make_field('Theme', self._theme_dd))
        return card

    def _build_subjects_card(self):
        """The locked-in subjects (spec §11).

        Read-only chips plus one deliberate way out. The chips live in their own
        panel so _render_subject_chips can clear and redraw just that panel after
        a successful change, without rebuilding the card around it.

        Returns the card panel and stashes _subjects_panel, the only part of
        this page that is ever redrawn after construction. Note there is no
        control here that Save reads: subjects are written by their own callable
        (set_subjects) from inside _on_change_subjects, because they need a
        confirmation of their own and must not ride along with a term-date save.
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
        """Fill every control on the page from one get_settings call.

        THE PAYLOAD. get_settings returns a plain dict, never a live table Row —
        notes._settings_row_to_dict builds it, and every value has already been
        through a read guard there, so nothing below has to defend itself
        against a damaged cell. It always carries exactly these seven keys, and
        this form reads all seven:

          theme                 'light' or 'dark'. Never None: an unreadable
                                stored value comes back as 'light'.
          default_reminder_days list of ints, each 1-365. The SAVE path also
                                caps the list at 6; the read guard only filters
                                values, so it does not re-impose that count.
          notifications_enabled bool; a missing or junk value reads False, the
                                same default reminders.run_reminder_check uses.
          school_year           int 2000-2100, or None when the box is empty.
          school_terms          list of dicts (see below). May be short, empty,
                                or out of term order.
          timezone              an IANA name that the tz database still
                                resolves; falls back to the app default.
          subjects              list of canonical catalog subject names.

        SCHOOL_TERMS IS THE ONE WORTH READING TWICE.  Each entry is
        {'term': 1-4, 'start_date': 'YYYY-MM-DD', 'end_date': 'YYYY-MM-DD'} and
        the two dates are ISO STRINGS, not date objects. That is not a choice
        this form makes: user_settings.school_terms is an Anvil simpleObject
        column, i.e. JSON, and JSON has no date type — so the stored text form
        is what every reader agrees on (notes._validate_school_terms writes it,
        nlp._iso_to_date reads it back). This form therefore has to convert in
        both directions: from_iso here on the way in, to_iso in _read_terms on
        the way out. Handing a date object to update_settings would fail the
        server's require_iso_date_text rather than being coerced.

        The list is also not guaranteed to be in term order or even complete,
        which is why the terms are indexed by their own 'term' number below
        instead of being zipped positionally against the four rows.
        """
        try:
            settings = anvil.server.call('get_settings')
        except Exception as e:
            # 1. A failed load leaves every control at its blank default. The
            #    page is NOT torn down: the student can still see the layout,
            #    and _loaded stays False so nothing here is mistaken for their
            #    real answers.
            toast_error(friendly_error(
                e, "Couldn't load your settings. Check your connection and "
                   "reload the page."))
            return
        # 2. Only now is self._subjects trustworthy. _on_change_subjects checks
        #    this before seeding the picker (see the comment there).
        self._loaded = True
        # 3. Heal the per-session cache with this fresh copy (e.g. after an
        #    import changed settings server-side).
        set_session_settings(settings)

        # 4. Reminder days: the stored list is the source of truth and the pills
        #    are set from it, not the other way round. A day the student saved
        #    that is no longer offered (the options tuple changed) simply has no
        #    pill to tick — it survives untouched in the database, but it will
        #    be dropped the next time this page is saved, because Save sends
        #    only what is ticked.
        reminder_days = settings.get('default_reminder_days') or []
        for d, cb in self._day_checks.items():
            cb.checked = d in reminder_days

        # 5. bool() because a CheckBox wants a real True/False, and because the
        #    key can legitimately be absent on a row written before the column.
        self._notifications_cb.checked = bool(
            settings.get('notifications_enabled'))

        # 6. Index the stored terms by their own term number. A dict lookup, not
        #    a zip: the list can arrive short (only terms 1 and 2 configured) or
        #    in any order, and positional pairing would then put Term 3's dates
        #    in Term 2's row. `isinstance(t, dict)` is belt-and-braces over the
        #    server's own read guard — a non-dict here would raise on .get and
        #    take the whole page down.
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

        # 7. None means "no year set", and it has to render as an EMPTY box, not
        #    as the text 'None' — which is what str(year) alone would put there
        #    and what _read_school_year would then refuse as not a number.
        year = settings.get('school_year')
        self._school_year_tb.text = '' if year is None else str(year)

        # 8. Melbourne is the fallback because the client is a Victorian
        #    student; it is only reached if the column is somehow empty, since
        #    safe_timezone already substitutes the app default for junk.
        tz = settings.get('timezone') or 'Australia/Melbourne'
        if tz not in TIMEZONES:
            # A zone that is valid but not in the short list (set by an import,
            # or by an earlier version of TIMEZONES) is APPENDED rather than
            # replaced. Otherwise the dropdown would show no selection, and the
            # next Save would quietly move the student to a zone they never
            # chose — changing what "due today" means for every assessment.
            self._timezone_dd.items = list(TIMEZONES) + [tz]
        self._timezone_dd.selected_value = tz

        # 9. Theme and subjects. The theme is only set on the control here; it
        #    is not applied to the window, because Main already applied the
        #    stored theme before this form was built.
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
        # clear() then rebuild, rather than editing the chips in place: the
        # subject list can change length, and a full redraw of one small panel
        # is cheaper to get right than working out which chips to remove.
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
        """The deliberate way out of a locked subject list (spec §11).

        Confirm, then loop on the shared SubjectPicker until the selection
        satisfies the two VCE program rules and set_subjects accepts it, or the
        student cancels. Returns nothing; on success it updates self._subjects,
        the chips and the session cache, and toasts.

        DATA
          catalog   from get_subject_catalog: [{'group': <learning area>,
                    'subjects': [<canonical name>, ...]}, ...]. Fetched once
                    per click, after the confirm, so a student who backs out
                    costs nothing.
          selected  list of canonical subject names; starts as the student's
                    current list and is re-seeded from the picker on every
                    failed attempt so nothing they ticked is ever thrown away.
          settings  set_subjects returns the WHOLE settings dict, the same
                    shape get_settings gives, not just the subject list.

        set_subjects raises ValueError with a sentence for a subject outside the
        catalog, a missing maths study, or more than MAX_SUBJECTS_PER_STUDENT
        studies; the server, not this loop, is the authority on all three.
        """
        # 1. Guard against overwriting a locked subject list with nothing. If
        # the page-load get_settings() failed, self._subjects is still [] — the
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

        # 2. Ask BEFORE fetching anything. The sentence names the three things
        #    that visibly change, and says the one thing a student actually
        #    worries about here: their saved assessments are not touched.
        proceed = confirm(
            "Changing your subjects re-tailors the parser, dashboard filter "
            "and exam timetable. Assessments you've already saved keep their "
            "subject either way. Continue?")
        if not proceed:
            return

        # 3. The catalog is fetched fresh rather than cached on the form: it is
        #    one small call, made at most once per click, and a stale catalog
        #    would offer studies the server no longer accepts.
        try:
            catalog = anvil.server.call('get_subject_catalog')
        except Exception as e:
            toast_error(friendly_error(
                e, "Couldn't load the subject list. Check your connection and "
                   "try again."))
            return

        # 4. Re-open the picker with the user's own ticks after any failed
        #    attempt — a validation error must never throw their selection away.
        #    That is what the loop is for: `continue` re-opens the alert with
        #    `selected` as it was left, and the only ways out are Cancel
        #    (return) and a save the server accepted (break).
        selected = self._subjects
        while True:
            picker = SubjectPicker(catalog, selected=selected)
            if not alert(picker, title='Change subjects', large=True,
                         buttons=[('Save subjects', True), ('Cancel', False)]):
                return
            selected = picker.get_selection()

            # 5. Same three checks, in the same order, with the same wording as
            #    OnboardingForm — the rule a student meets must not depend on
            #    which screen they changed their subjects from. The picker is
            #    inside an alert(), so there is no field on the page to attach a
            #    message to; a toast is the only visible channel here.
            #
            #    Only the English rule asks rather than refuses, because it is
            #    the only one the server FIXES instead of rejecting: it appends
            #    'English' itself. The confirm exists so that addition is never
            #    a surprise. Declining loops back to the picker rather than
            #    abandoning the change.
            if not selected:
                toast_warn(NO_SELECTION_MESSAGE)
                continue
            if not any(s in MATHS_GROUP for s in selected):
                toast_error(MATHS_RULE_MESSAGE)
                continue
            if not any(s in ENGLISH_GROUP for s in selected):
                if not confirm(ENGLISH_RULE_MESSAGE):
                    continue

            # 6. The server re-applies all of the above and owns the answer. A
            #    ValueError from it is shown and the picker re-opens, so a rule
            #    only the server knows about (a renamed study, the 12-subject
            #    cap) is still recoverable without losing the selection.
            try:
                settings = anvil.server.call('set_subjects', selected)
            except Exception as e:
                toast_error(friendly_error(
                    e, "Couldn't save those subjects. Check your connection "
                       "and try again."))
                continue
            break

        # 7. Redraw from the SERVER's list, not from `selected`: set_subjects
        #    may have added 'English', dropped a duplicate or renamed a legacy
        #    study, so the chips would otherwise show something the account does
        #    not actually hold. The session cache is healed with the same copy.
        set_session_settings(settings)
        self._subjects = settings.get('subjects') or []
        self._render_subject_chips()
        toast("Subjects updated.")

    def _on_load_preset(self, **event_args):
        """Fill the term pickers with the VIC 2026 dates (Save still required).

        Nothing is sent to the server here. The link only writes into the eight
        pickers and the year box, so the student can still change a date the
        preset got wrong for their school before committing to any of it.

        _VIC_2026_TERMS holds date OBJECTS, not the ISO strings the column
        stores, because they go straight into DatePicker.date; the conversion to
        text happens once, in _read_terms, on the way to the server.
        """
        # zip pairs term 1 with the first tuple, term 2 with the second, and so
        # on: both sequences are in term order and both are four long, so the
        # positional pairing that would be wrong for stored data (see step 6 of
        # _load_settings) is exactly right for this fixed table.
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
        """The eight term pickers -> the server's school_terms shape.

        Returns (True, terms) or (False, None), having already written a message
        under every offending term's field. `terms` is the exact value sent as
        update_settings' school_terms: a list of
        {'term': int 1-4, 'start_date': 'YYYY-MM-DD', 'end_date': 'YYYY-MM-DD'},
        in row order, with any term whose two pickers are both empty simply left
        out — that is how a student clears a term.

        The dates are converted to ISO TEXT here (to_iso) because the column is
        an Anvil simpleObject and the server validates the text form; see the
        payload notes in _load_settings.

        Four rules are applied, in this order: both-or-neither, start not after
        end, no term number twice, no two terms overlapping. Raises nothing.
        """
        # `ok` is not returned early: every term is checked so a student with
        # two bad terms sees two messages, not the first one four times over.
        terms, ok = [], True

        for term_number, (start_dp, end_dp) in enumerate(
                self._term_pickers, start=1):
            # term_number is 1-based (Term 1..Term 4) and the two lists are
            # 0-based, so every lookup into _term_fields is offset by one. The
            # server's messages are worded 'Term N ...' with the same number.
            field = self._term_fields[term_number - 1]
            # DatePicker.date is a real date object or None — Anvil parses the
            # browser's date input for us, which is why nothing here has to
            # cope with the student's locale or with half-typed text.
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
            # Both empty (the check above proved they agree): the student is
            # clearing this term, so it is left out of the list entirely rather
            # than sent as a pair of nulls the server would reject.
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

        # The two whole-list rules below are only worth running on a COMPLETE
        # list. If a term above was rejected it is missing from `terms`, so an
        # overlap check would be answering a question about dates the student
        # has not finished giving — and could report an overlap that disappears
        # the moment they fix the other field.
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
        # Matched case-insensitively and by substring, because the server writes
        # 'Term 2 start date' and 'Term 2 end date' but this page has ONE field
        # per term: 'term 2' is the longest fragment both wordings share.
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
        """Read the whole page, validate it, and save it in one call.

        Everything except the subject list is saved here, together, because the
        page has one Save button. Returns nothing; the visible outcomes are a
        toast plus, on a rejection, a message under the offending field.

        WHAT IS SENT. update_settings takes a PATCH, so only the keys built
        below are touched and any settings column not on this page is left
        alone. The patch always carries:
          default_reminder_days  list[int], descending, from the ticked pills
          notifications_enabled  bool
          school_terms           list of term dicts (see _read_terms)
          school_year            int or None
          timezone               IANA name string
        and 'theme' as well whenever the dropdown has a value.

        WHAT COMES BACK. update_settings returns the freshly stored settings
        dict — the same seven-key shape get_settings gives — which is what the
        session cache and the theme are then updated from, rather than from what
        was typed. The server raises ValueError with a sentence for anything it
        refuses, and _field_for_message decides where that sentence goes.
        """
        # 1. Wipe every message first, so nothing left over from the last try
        #    is read as a verdict on this one.
        clear_field_errors(self._school_year_field, self._timezone_field,
                           *self._term_fields)

        # 2. All three run before anything is reported, so a student with a
        #    backwards term AND a blank timezone sees both now rather than one,
        #    then the other on the next click.
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

        # 3. Build the patch. reverse=True stores the days furthest-ahead first,
        #    matching the order the pills are drawn in, so the value written and
        #    the value shown never disagree; sorting at all means an unchanged
        #    page saves the identical list every time rather than one in
        #    whatever order the dict happened to iterate. bool() is needed
        #    because a CheckBox can read back None, which require_bool refuses.
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

        # 4. Theme is the one optional key: an empty dropdown means "no answer",
        #    and because this is a patch, omitting it leaves the stored theme
        #    alone instead of blanking it into an invalid value.
        theme = self._theme_dd.selected_value
        if theme:
            fields['theme'] = theme

        # 5. One call for the whole page. The server validates the entire patch
        #    before writing any of it, so a save that fails on the terms does
        #    not leave the timezone half-applied.
        try:
            settings = anvil.server.call('update_settings', fields)
        except Exception as e:
            # 6. A server refusal is a sentence, not a field name, so it is
            #    routed to the field it quotes when it quotes one and toasted
            #    when it does not (a dropped connection names nothing).
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

        # 7. Both of these use the SERVER's copy, never the local `fields`: the
        #    stored theme is the one the window should be painted in, and the
        #    cached settings other screens read must match what was actually
        #    written (the server may normalise or reject-and-keep a value).
        set_session_settings(settings)
        apply_theme(settings.get('theme'))
        toast("Settings saved.")
