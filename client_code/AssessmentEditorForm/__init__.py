import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""AssessmentEditorForm - create / edit / parser-preview / bulk-add assessments.

Opened as an alert(..., large=True) from DashboardForm, so this form is *modal
content*: it deliberately has no top bar and no make_page() wrapper — the modal
itself is the frame. One form, four modes via the `mode` constructor arg
(spec section 3):
  mode='create'  - blank manual entry (FR03).
  mode='edit'    - load an existing assessment by id and save changes (FR04).
  mode='preview' - prefill from an nlp.parse_text() result dict; show the
                   confidence chip and per-field 'why' provenance (FR17).
  mode='bulk'    - paste many lines, parse them all, tick the ones to keep and
                   insert every line that validates (FR02).

ParserPreviewForm was dropped from the design; its preview-before-commit role is
this form in 'preview' mode. Save raises 'x-close-alert' so the parent alert()
returns the new/updated assessment id — except in bulk mode, where the value is
the NUMBER of assessments actually inserted. Cancel returns None, or that same
count when a bulk run has already written rows, so the dashboard still refreshes.

The three single-record modes check the form before they call the server
(FR03): the message lands beside the offending field via common.set_field_error
rather than arriving as a toast after a round trip (FR04). Bulk mode has its
own first pass instead — _blocking_reason decides which lines arrive ticked,
and a server rejection is written beside the line it belongs to.

Nothing HARD-gates on parser confidence. 'preview' mode never looks at the
score, because a LOW parse is exactly what that mode exists to let the student
hand-correct; in bulk mode a LOW line only arrives unticked, which the student
can override by ticking it.

Layout is composed from the shared UI kit (client_code/common, spec section 14)
rather than styled here. Two consequences worth defending:
  * every control is a make_field(), so the label, the input and the parser's
    'why' provenance line are one unit — the provenance no longer floats as a
    loose grey sentence between fields, and
  * nothing in this file names a colour or a size. The confidence badge is a
    chip tone ('ok'/'warn'/'bad'), so it stays legible when the student switches
    the app to the dark theme; the old white-on-hex badge did not.

See IMPLEMENTATION_SPEC.md section 3 (AssessmentEditorForm).
"""

import anvil
import anvil.server
from anvil import (
    ColumnPanel, Label, TextBox, TextArea, DropDown, DatePicker,
    CheckBox, Button,
)
from ..common import (
    CONF_TONE, clear_field_errors, fmt_date, friendly_error, from_iso,
    get_session_settings, make_chip, make_divider, make_empty_state, make_field,
    make_list_card, make_page_title, make_row, make_section_header,
    make_toolbar, set_field_error, toast, toast_error, toast_warn,
)

# Full canonical catalog (mirror of _constants.CANONICAL_SUBJECTS plus the
# generic 'Mathematics' the parser can emit; the client cannot import server
# modules, so the list is duplicated here). The dropdown shows the student's
# LOCKED subjects (spec §11) when available — this full list is the fallback
# and the validity check for bulk auto-create.
SUBJECTS = (
    'English', 'English as an Additional Language', 'English Language',
    'Literature',
    'Mathematics', 'Foundation Mathematics', 'General Mathematics',
    'Mathematical Methods', 'Specialist Mathematics',
    'Biology', 'Chemistry', 'Environmental Science', 'Physics', 'Psychology',
    'Classical Studies', 'Geography', 'History: Ancient History',
    'History: Australian History', 'History: Revolutions', 'Philosophy',
    'Politics', 'Religion and Society', 'Sociology', 'Texts and Traditions',
    'Accounting', 'Business Management', 'Economics', 'Industry and Enterprise',
    'Legal Studies',
    'Algorithmics', 'Applied Computing', 'Data Analytics', 'Food Studies',
    'Product Design and Technologies', 'Software Development',
    'Systems Engineering',
    'Art Creative Practice', 'Art Making and Exhibiting', 'Dance', 'Drama',
    'Media', 'Music', 'Theatre Studies', 'Visual Communication Design',
    'Health and Human Development', 'Outdoor and Environmental Studies',
    'Physical Education',
    'Chinese', 'French', 'German', 'Greek', 'Indonesian', 'Italian',
    'Japanese', 'Spanish', 'Vietnamese',
    'Extended Investigation',
)

# DropDown items as (display, value) pairs — value is the stored canonical enum.
# Mirrors of the server enums (the client cannot import server modules; keep in
# sync). The offline constants-integrity suite in docs/TESTING.md asserts
# these still match server_code/_constants.py.
TYPES = (
    ('SAC', 'sac'), ('SAT', 'sat'), ('Exam', 'exam'),
    ('Project', 'project'), ('Homework', 'homework'), ('Other', 'other'),
)
# Mirrors of the server enums (the client cannot import server modules; keep in
# sync). The offline constants-integrity suite in docs/TESTING.md asserts
# these still match server_code/_constants.py.
STATUSES = (
    ('Not started', 'not_started'), ('In progress', 'in_progress'),
    ('Completed', 'completed'),
)

# The stored values on their own. Membership of these is what makes a value
# legal; the display label beside it is only what the student reads. Used to
# check a value BEFORE it is put into a DropDown, so a value the list does not
# offer can be reported instead of silently becoming the list's first item.
TYPE_VALUES = frozenset(value for _label, value in TYPES)
STATUS_VALUES = frozenset(value for _label, value in STATUSES)

# The status a new assessment starts in (mirror of _constants.STATUS_DEFAULT).
STATUS_DEFAULT = 'not_started'

# Field bounds this form checks before it calls the server. Mirrors of
# _constants.MAX_TITLE_LENGTH / MIN_WEIGHT / MAX_WEIGHT (the client cannot import
# server modules; keep in sync). Both sides must use the SAME number: a client
# bound that is tighter blocks a save the server would have accepted, and one
# that is looser promises a save the server will refuse.
MAX_TITLE_LENGTH = 200
MIN_WEIGHT = 0.0
MAX_WEIGHT = 100.0

# Reminder offsets offered as pills, in display order. There is no server list to
# mirror — the server bounds each offset with MIN_REMINDER_DAY/MAX_REMINDER_DAY
# rather than enumerating choices — but SettingsForm offers the same five. The
# offline constants-integrity suite asserts the two client copies still match
# each other and that every option sits inside the server's accepted range.
REMINDER_DAY_OPTIONS = (14, 7, 3, 2, 1)

# How many note matches the link picker shows at once. This is a picker inside an
# already-crowded modal: a student who cannot see the note they want should type a
# better search rather than scroll a hundred buttons past the Save row. Named rather
# than inlined because the cap and the "showing the first N" message below it must
# quote the same number.
_NOTE_RESULT_LIMIT = 8

# Parser confidence -> chip tone, the date helpers and the page heading all come
# from common now. They used to be copied into this file, which is how the same
# confidence ended up a different colour here than on the dashboard.


class AssessmentEditorForm(ColumnPanel):
    """The assessment editor: one modal dialog doing four different jobs.

    DashboardForm opens it as alert(AssessmentEditorForm(...), buttons=[]), so
    this class IS the dialog body — no top bar, no make_page() wrapper — and
    the only way out is the 'x-close-alert' event raised below.

    Requirements it implements: FR03 (the manual create form; the controls
    built here are exactly the fields FR03 names — subject, type, weight
    0-100, due date, description, status, reminder_days — and Save is blocked
    while any of them is missing or out of range), FR04 (edit an existing
    record), FR17 (show the parse's confidence and let the student correct
    every field BEFORE anything is written), FR02 (bulk add, committed per
    line), FR12 (the stretch-list UI for the note cross-reference) and
    NFR08 (both DatePickers
    are format='DD MMM YYYY' and the bulk summaries go through
    common.fmt_date, so no browser locale can read 03/04 as 4 March).

    Constructed with three arguments; which of them matter depends on `mode`:
      mode='create'   neither other argument is used. A blank form.
      mode='edit'     assessment_id REQUIRED — the row to load and update.
      mode='preview'  prefill REQUIRED — an nlp.parse_text() result dict
                      {'fields', 'why', 'confidence', 'source_text'}.
                      'fields' fills the controls, 'why' becomes the per-field
                      provenance hints, 'confidence' becomes the header chip.
      mode='bulk'     neither is used. __init__ returns early after
                      _build_bulk(), so none of the single-record controls
                      below are ever created.

    Server callables it depends on: get_assessment (edit load),
    create_assessment (create/preview save), update_assessment (edit save),
    search_notes (the linked-note picker), and parse_bulk +
    create_bulk_assessments (bulk mode). Ownership and the real validation
    live on the server (NFR03, FR04); nothing here scopes a query.

    What it hands back, as the value of 'x-close-alert'. The shapes are NOT
    the same, which is the one asymmetry a reader has to know about:
      * create / preview -> the NEW assessment id (str)
      * edit             -> the SAME assessment_id it was handed (str)
      * bulk             -> the COUNT of rows inserted (int), never an id
      * cancel           -> None, except in bulk mode after a partial commit,
                            where the count goes back instead so the dashboard
                            still learns there is something new to show.
    DashboardForm only asks `if result:`, so an id and a non-zero count are
    interchangeable to it, and a bulk run that inserted nothing is correctly
    falsy and skips the refresh.
    """

    def __init__(self, mode='create', assessment_id=None, prefill=None, **properties):
        """Build the dialog for `mode`, then fill it in.

        mode: 'create' | 'edit' | 'preview' | 'bulk'. Anything else falls
            through to the create layout under the generic 'Assessment'
            heading — a bad mode is a caller bug, and a traceback in front of
            the student would be the wrong way to report it.
        assessment_id: an assessments row id. Read in 'edit' mode only; None
            everywhere else.
        prefill: a parse_text() result dict. Read in 'preview' mode only.
        **properties: forwarded to ColumnPanel, which is the Anvil contract
            for a form class.
        """
        # 1. ColumnPanel first — the components added below need the panel to
        #    exist. The spacing is stripped because this panel sits inside an
        #    alert() that already supplies the dialog's padding; left at the
        #    default it would gain a second gap top and bottom.
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        # 2. Everything the handlers below need to know later, captured before
        #    any component exists. _linked_note_ids is the working set the
        #    linked-notes section edits (FR12) and _build_payload sends;
        #    _note_titles is only a display cache, so a missing entry falls
        #    back to showing the raw id rather than failing.
        self._mode = mode
        self._assessment_id = assessment_id
        self._prefill = prefill
        self._linked_note_ids = []
        self._note_titles = {}   # id -> title cache for the linked-note pills
        # Rows written by bulk mode so far. Carried on the close event so a
        # partial commit still tells the dashboard there is something new.
        self._bulk_inserted = 0

        # 3. The parser's per-field provenance (FR17), read once here so each
        #    make_field() below can hang its own 'why' line under its control.
        #    Empty outside preview mode, which is what silences the hints.
        self._why = (prefill or {}).get('why', {}) if mode == 'preview' else {}

        # 4. Bulk is a different screen, not a variant of this one: it has a
        #    paste box and a list of parsed lines instead of a field stack, and
        #    none of the controls below apply to it. Returning here rather than
        #    wrapping the rest in an `else` is also what makes the missing
        #    attributes deliberate — in bulk mode self._title_tb and friends do
        #    not exist, and _on_save_click/_validate_fields are never wired up.
        if mode == 'bulk':
            self._build_bulk()
            return

        # From here down the components are added in the order they appear on
        # screen, and that order is load-bearing twice over: _validate_fields()
        # walks its problems in the same sequence so the first message it
        # focuses is the topmost one, and _load() runs LAST (step 16) because
        # it writes into controls that all have to exist first.

        # --- 5. header (+ confidence chip in preview mode) ---
        # The three single-record modes build an identical field stack, so the
        # heading (and, in preview, the chip beside it) is all the student has
        # to tell them apart. .get(mode, 'Assessment') is the fall-through for
        # an unexpected mode described in the docstring.
        titles = {'create': 'Add assessment', 'edit': 'Edit assessment',
                  'preview': 'Confirm parsed assessment'}
        header = make_row(Label(text=titles.get(mode, 'Assessment'),
                                role='pagetitle'))
        # The confidence chip answers "how much should I trust what is already
        # filled in?" (FR17). Default 'LOW' because an absent score is not a
        # good one, and CONF_TONE maps HIGH/MEDIUM/LOW onto ok/warn/bad so the
        # chip matches the same score shown on the dashboard.
        if mode == 'preview' and prefill:
            conf = prefill.get('confidence', 'LOW')
            header.add_component(make_chip(conf, CONF_TONE.get(conf)))
        self.add_component(header)

        # Each make_field() panel is kept, not just added: _validate_fields()
        # needs the wrapper to hang a message under the control it belongs to.

        # --- 6. title ---
        # The cap is stated up front rather than only on rejection — a student
        # should not have to fail a save to learn how long a title may be.
        self._title_tb = TextBox()
        self._title_field = make_field(
            'Title', self._title_tb,
            hint='Up to %d characters.' % MAX_TITLE_LENGTH, required=True)
        self.add_component(self._title_field)

        # --- 7. subject ---
        # The dropdown offers the student's locked subjects (spec §11) when
        # they've onboarded; out-of-list values (legacy rows, parser fallback
        # hits) are appended on load so nothing becomes uneditable.
        self._subject_dd = DropDown(items=self._subject_items(),
                                    include_placeholder=True)
        self._subject_field = make_field('Subject', self._subject_dd,
                                         hint=self._why_text('subject'),
                                         required=True)
        self.add_component(self._subject_field)

        # --- 8. type ---
        # include_placeholder so this control is able to show NOTHING. Without
        # it a DropDown always reports one of its items, and _load() could not
        # surface a stored value the list no longer offers without inventing an
        # answer on the student's behalf — see _select_choice().
        self._type_dd = DropDown(items=list(TYPES), include_placeholder=True)
        # Previously implicit in "the first item wins"; stated here so adding the
        # placeholder does not quietly change what a new assessment defaults to.
        self._type_dd.selected_value = TYPES[0][1]
        self._type_field = make_field('Type', self._type_dd,
                                      hint=self._why_text('type'))
        self.add_component(self._type_field)

        # --- 9. due date + start date ---
        # 'DD MMM YYYY' on both pickers is NFR08: the month is a word, so a
        # student reading the box cannot mistake 03/04 for 4 March, and the
        # display does not change with the browser's locale. A DatePicker hands
        # back a datetime.date object, not text, so nothing here has to parse
        # what was typed — which is why only the due date is marked required
        # and neither needs a format check in _validate_fields().
        self._due_dp = DatePicker(format='DD MMM YYYY')
        self._due_field = make_field('Due date', self._due_dp,
                                     hint=self._why_text('due_date'),
                                     required=True)
        self.add_component(self._due_field)

        # Start date is optional and has no 'why' hint: the parser never emits
        # one, so there would be nothing to put under it.
        self._start_dp = DatePicker(format='DD MMM YYYY')
        self._start_field = make_field('Start date (optional)', self._start_dp)
        self.add_component(self._start_field)

        # --- 10. weight ---
        # The range is shown as a hint, but only when the parser has nothing to
        # say about this field — its provenance line is the more useful of the two.
        self._weight_tb = TextBox(type='number')
        self._weight_field = make_field(
            'Weight (%)', self._weight_tb,
            hint=(self._why_text('weight')
                  or 'A percentage between %g and %g.' % (MIN_WEIGHT, MAX_WEIGHT)))
        self.add_component(self._weight_field)

        # --- 11. status ---
        # Placeholder for the same reason as Type: _load() must be able to show
        # an unreadable stored status as a gap. The explicit default is what a
        # NEW assessment starts as ('not_started'), and it mirrors the server's
        # STATUS_DEFAULT so create and the server agree without a round trip.
        self._status_dd = DropDown(items=list(STATUSES), include_placeholder=True)
        self._status_dd.selected_value = STATUS_DEFAULT
        self._status_field = make_field('Status', self._status_dd)
        self.add_component(self._status_field)

        # --- 12. reminder pills ---
        # Toggle pills rather than a column of tickboxes: the five offsets are a
        # single choice the student scans horizontally, and they fit on one line.
        # _day_checks maps the offset (int) to its CheckBox, so _check_days()
        # can tick a set of days and _build_payload() can read them back
        # without either of them caring what order the pills were built in.
        self._day_checks = {}
        pills = make_row()
        for d in REMINDER_DAY_OPTIONS:
            cb = CheckBox(text='%d' % d, role='pill')
            self._day_checks[d] = cb
            pills.add_component(cb)
        # This field panel is NOT kept: nothing validates the reminder pills
        # (any subset of them, including none, is legal), so there is never a
        # message to hang under it.
        self.add_component(make_field('Remind me (days before due)', pills))

        # --- 13. description ---
        self._desc_ta = TextArea()
        self.add_component(make_field('Description (optional)', self._desc_ta))

        # --- 14. linked notes (FR12) ---
        # Its own section under a divider: everything above is the assessment
        # itself, everything below is a search-and-attach flow with its own
        # results list, so mixing them into the field stack read as clutter.
        self.add_component(make_divider())
        self.add_component(make_section_header('Linked notes', 'optional'))
        self._note_search_tb = TextBox(placeholder='Search your notes to link')
        # Enter and the button are wired to the SAME handler, so searching does
        # not depend on the student noticing there is a button.
        self._note_search_tb.set_event_handler('pressed_enter', self._on_note_search)
        search_btn = Button(text='Search', role='secondary')
        search_btn.set_event_handler('click', self._on_note_search)
        self.add_component(make_toolbar(self._note_search_tb, search_btn))
        # Two separate panels, both empty until something fills them:
        # _note_results holds the search hits (rewritten on every search) and
        # _linked_pills holds what is currently attached (rewritten by
        # _render_links). Keeping them apart is what lets one be cleared
        # without disturbing the other.
        self._note_results = ColumnPanel()
        self.add_component(self._note_results)
        self._linked_pills = make_row()
        self.add_component(self._linked_pills)

        # --- 15. footer ---
        # Cancel first, Save last: Save is the primary action and sits closest
        # to the right-hand edge the student's eye finishes on. Neither button
        # is kept as an attribute — both are only ever reached through the
        # handlers they are bound to here.
        self.add_component(make_divider())
        cancel_btn = Button(text='Cancel', role='secondary')
        cancel_btn.set_event_handler('click', self._on_cancel_click)
        save_btn = Button(text='Save', role='primary')
        save_btn.set_event_handler('click', self._on_save_click)
        self.add_component(make_row(cancel_btn, save_btn))

        # 16. Filling in comes last, once every control above exists. _load()
        #     is also the only place a server call is made during construction
        #     (edit mode), so a slow or failed load cannot leave a half-built
        #     dialog on screen — the frame is already complete by this line.
        self._load()

    # --- helpers -----------------------------------------------------------
    def _subject_items(self):
        """The student's locked subjects (session-cached settings), falling
        back to the full canonical catalog pre-onboarding.

        Returns a list of subject name strings — a plain list, not the (label,
        value) pairs TYPES and STATUSES use, because a subject's stored value
        and its display label are the same string.
        """
        # try/except rather than a check: get_session_settings() reads a cache
        # that may not be populated yet, and on a first run it calls the
        # server. Neither a missing cache nor a failed call should stop the
        # dialog opening — the full catalog below is a usable answer, so the
        # failure is swallowed and the fallback stands in.
        try:
            locked = get_session_settings().get('subjects')
        except Exception:
            locked = None
        # Falsy covers both "the key is missing" and "onboarding saved an
        # empty list": an empty dropdown would be worse than the full catalog.
        # list() copies, so a caller cannot mutate the cached settings through
        # the DropDown's items.
        return list(locked) if locked else list(SUBJECTS)

    def _select_subject(self, subject):
        """Select `subject` in the dropdown, appending it if out-of-list.

        subject: a canonical subject name, or None/'' to do nothing.

        Grows the dropdown rather than refusing the value, because the list it
        was built from is the student's LOCKED subjects (spec §11), which is
        usually narrower than what is stored on the row. A subject dropped
        during a subject change would otherwise make its assessments
        uneditable — opening one would show a different subject and saving
        would write that back.
        """
        # "No subject" is an ordinary outcome, not a fault: a create opens with
        # nothing chosen and FR16 lets an unrecognised alias through the parser
        # unchanged, so _load deliberately calls with None. Returning leaves
        # the dropdown on its blank placeholder; falling through would not,
        # because selected_value None and '' do not behave the same here.
        if not subject:
            return
        # THE MEMBERSHIP TEST IS THE POINT OF THIS FUNCTION. A DropDown given
        # selected_value its items do not contain does not refuse it and does
        # not go blank — it keeps showing its FIRST item, and _build_payload
        # reads that item straight back on Save. That is exactly how a stored
        # off-enum type or status used to be silently rewritten by opening a
        # record and pressing Save; subject never had the bug because of these
        # three lines, and _select_choice is that same defence for the rest.
        items = list(self._subject_dd.items)
        if subject not in items:
            items.append(subject)
            # Reassigned, not appended in place: Anvil repaints a DropDown when
            # .items is set, so mutating the live list would leave the widget
            # unaware of the new option and the selection below would miss it.
            self._subject_dd.items = items
        # Only now, with the value certain to be in the list, is it selected.
        self._subject_dd.selected_value = subject

    def _select_choice(self, dropdown, field_panel, stored, allowed, field_label):
        """Select a stored enum value, or select NOTHING and say why.

        A value the list does not offer — a legacy Title-Case 'SAC', a typo made in
        the Data Tables console, an empty cell on a row written before the column
        existed — used to leave the control showing its FIRST item. _build_payload
        then read that item straight back and wrote it on save, so simply opening a
        record and pressing Save could quietly change what it said.

        Selecting nothing makes the gap visible and puts the answer back where it
        belongs: with the student, who is the only one here who knows it. The
        subject field has always been defended this way (_select_subject); this is
        the same defence for the other two dropdowns.

        dropdown: the DropDown to set (self._type_dd or self._status_dd).
        field_panel: that control's make_field() wrapper — where the message
            goes, so it lands under the control it is about (FR04).
        stored: the value read off the row. Any type: it is exactly what the
            data table held, which is the whole reason it is checked.
        allowed: the frozenset of legal stored values (TYPE_VALUES /
            STATUS_VALUES), NOT the (label, value) pairs.
        field_label: 'Type' or 'Status'; lower-cased into the message.

        Returns nothing; its whole effect is on the dropdown and the panel.
        """
        # The good case clears any message left from a previous _load(), so a
        # row that was fixed does not keep the old complaint under it.
        if stored in allowed:
            dropdown.selected_value = stored
            set_field_error(field_panel, None)
            return
        # Show nothing. This is the line that makes the gap visible; the
        # placeholder added in __init__ is what makes it possible at all.
        dropdown.selected_value = None
        # Two different messages, because these are two different problems: a
        # value the app dropped is worth naming so the student can recognise
        # it, whereas an empty cell has no name to quote.
        if stored:
            set_field_error(
                field_panel,
                'This assessment has a %s this app no longer offers ("%s"). '
                'Choose one from the list.' % (field_label.lower(), stored))
        else:
            set_field_error(
                field_panel,
                'This assessment has no %s recorded. Choose one from the list.'
                % field_label.lower())

    def _why_text(self, key):
        """The parser's provenance string for a field, or None.

        key: a field name as nlp.py writes it into the 'why' map — 'subject',
            'type', 'due_date' or 'weight'. Only detected fields appear there,
            so a miss is normal and returns None, not an error.

        Returns a sentence like 'matched "friday" → 06 Mar 2026' (FR17), or
        None outside preview mode, where self._why is empty by construction.

        Returns rather than renders: make_field() puts it directly under the
        control it explains, so preview mode needs no extra components.
        """
        if self._why and key in self._why:
            return self._why[key]
        return None

    def _load(self):
        """Populate fields from prefill (preview) or the server (edit).

        Called once, at the end of __init__. Three-way branch on self._mode:
        preview reads the prefill dict already in hand, edit calls
        get_assessment for the row, and create only ticks the default
        reminders. Returns nothing; everything it does is to the controls.

        Reads the assessments row through get_assessment, which returns a
        plain dict with dates as 'YYYY-MM-DD' strings — hence from_iso() on
        due_date and start_date, where the prefill branch can assign the
        parser's date objects directly.
        """
        # The reminder defaults the student chose in Settings, wanted by every
        # branch, so it is resolved once up front. The literal [7, 2] is the
        # app-wide fallback quoted in the SRS interview (question 11) and
        # matches the server's default; the except swallows a cache miss the
        # same way _subject_items() does, since a default is not worth failing
        # the whole dialog over.
        default_days = [7, 2]
        try:
            settings = get_session_settings()
            # `is None` again, for the same reason as the edit branch below: a student
            # who has turned every default reminder off in Settings has stored [], and
            # `or [7, 2]` would quietly hand it back to them on every new assessment.
            stored_default = settings.get('default_reminder_days')
            if stored_default is not None:
                default_days = stored_default
        except Exception:
            pass

        # Branch 1 — preview. The values come from parse_text(), so they are
        # already Python objects (a date, a float) and need no conversion.
        # Status, description, start date and links are untouched: the parser
        # never produces them, so they keep the __init__ defaults.
        if self._mode == 'preview' and self._prefill:
            f = self._prefill.get('fields', {})
            self._title_tb.text = f.get('title') or ''
            # Checked against the FULL catalog, not the dropdown's items: FR16
            # lets an unrecognised alias pass through the parser unchanged, and
            # a guess like that should not be planted in the dropdown as though
            # the app had recognised it. Leaving it blank makes the student
            # answer, and _validate_fields() will not let Save past until they
            # have.
            # The membership test is a tripwire, not a filter. parse_text only ever
            # emits a canonical study name or None, and SUBJECTS mirrors that same
            # catalogue, so in a correctly-built app this condition is always true
            # when a subject was found — there is deliberately no "else" telling the
            # student their subject was unrecognised, because that cannot happen from
            # a parse. What it DOES protect against is the two catalogues drifting
            # apart, in which case a subject silently fails to select rather than
            # being planted in the dropdown as though the app had recognised it.
            # tests/test_constants_integrity.py asserts they have not drifted.
            if f.get('subject') in SUBJECTS:
                self._select_subject(f.get('subject'))
            # 'type' is only ever absent on a parse that found nothing at all;
            # the fallback value 'other' still goes through _select_choice, so
            # a type the app no longer offers is reported rather than shown as
            # whatever happens to be first in the list.
            if f.get('type'):
                self._select_choice(self._type_dd, self._type_field,
                                    f.get('type'), TYPE_VALUES, 'Type')
            self._due_dp.date = f.get('due_date')
            # `is not None` rather than a truth test: 0 is a legal weight (a
            # practice SAC that counts for nothing), and `if f.get('weight')`
            # would silently drop it. '%g' keeps 25.0 showing as '25'.
            if f.get('weight') is not None:
                self._weight_tb.text = ('%g' % f.get('weight'))
            self._check_days(default_days)

        # Branch 2 — edit. The only branch that talks to the server, and the
        # only one that has stored values to defend itself against.
        elif self._mode == 'edit' and self._assessment_id:
            try:
                a = anvil.server.call('get_assessment', self._assessment_id)
            except Exception as e:
                # The row may have been deleted in another tab, or the id may
                # be stale. Returning leaves the dialog open and empty rather
                # than half-filled from a failed read; friendly_error turns the
                # server's ValueError into something worth reading.
                toast_error(friendly_error(
                    e, fallback="Couldn't open that assessment. Try again."))
                return
            self._title_tb.text = a.get('title') or ''
            self._select_subject(a.get('subject'))
            # Membership-checked, never assigned blind: see _select_choice().
            self._select_choice(self._type_dd, self._type_field,
                                a.get('type'), TYPE_VALUES, 'Type')
            # get_assessment hands dates back as ISO strings, so both have to
            # come back through from_iso before a DatePicker will take them.
            # from_iso returns None for an unreadable or absent cell, which is
            # exactly what an empty picker wants.
            self._due_dp.date = from_iso(a.get('due_date'))
            self._start_dp.date = from_iso(a.get('start_date'))
            if a.get('weight') is not None:
                self._weight_tb.text = ('%g' % a.get('weight'))
            self._select_choice(self._status_dd, self._status_field,
                                a.get('status'), STATUS_VALUES, 'Status')
            self._desc_ta.text = a.get('description') or ''
            # `is None` rather than `or`, because an EMPTY list is a real answer here
            # and a falsy one. The server distinguishes the two deliberately: a missing
            # reminder_days means "use the student's defaults", but a stored [] means
            # "this assessment sends no reminders at all" (assessments.py, the
            # `if reminder_days is None:` branch). With `or default_days`, unticking
            # every pill and saving re-ticked 7 and 2 the next time the row was opened,
            # and _build_payload then wrote them straight back — so "no reminders" was
            # a setting the student could choose but never keep.
            stored_days = a.get('reminder_days')
            self._check_days(default_days if stored_days is None else stored_days)
            # list() copies rather than aliases: _add_link appends to this list
            # in place, and it must not be the same object the server's reply
            # is still holding.
            self._linked_note_ids = list(a.get('linked_note_ids') or [])
            # Titles first, then draw: _render_links() reads the cache
            # _resolve_link_titles() fills, and drawing first would show a
            # screen of raw row ids.
            self._resolve_link_titles()
            self._render_links()

        # Branch 3 — create. Nothing to load; the controls already hold their
        # __init__ defaults, and only the reminder pills need an answer.
        else:  # create
            self._check_days(default_days)

    def _check_days(self, days):
        """Tick exactly the reminder pills named in `days`, unticking the rest.

        days: a list of ints (offsets in days before the due date), or None.
            Values that are not among REMINDER_DAY_OPTIONS simply have no pill
            and are ignored — the pills are a fixed set, so this cannot invent
            a control for an offset the form does not offer.

        Sets every pill on each call rather than only the ones to tick, so
        calling it twice (an edit that re-loads) leaves no stale ticks behind.
        """
        for d, cb in self._day_checks.items():
            cb.checked = d in (days or [])

    def _build_payload(self):
        """Read every control into the dict the server's create/update expect.

        Returns a dict of ten keys, every one of them inside the server's edit
        whitelist (_constants.EDITABLE_FIELDS_ASSESSMENT), so the same dict is
        accepted by both create_assessment and update_assessment:
            title           str, stripped
            subject         str or None (None only if validation was skipped)
            type            one of TYPE_VALUES
            due_date        datetime.date
            start_date      datetime.date or None
            weight          float or None
            status          one of STATUS_VALUES
            description     str or None (empty text becomes None)
            reminder_days   list of int, descending
            linked_note_ids list of note id strings
        Preview mode adds three more: 'term_info' (also editable) and the two
        audit columns 'confidence' and 'source_text', which only the create
        path accepts.

        Raises ValueError/TypeError if the weight box holds something that is
        not a number — which is why _on_save_click runs _validate_fields()
        first and still wraps this call.
        """
        # Read once into a local: a TextBox with type='number' can report None
        # (empty), a string, or a number, and doing this test twice against the
        # live control invites the three cases being handled differently.
        weight_text = (self._weight_tb.text if self._weight_tb.text is not None else '')
        # Empty means "no weight", which is legal — None, not 0. A homework
        # task that counts for nothing and one whose weight was never recorded
        # are different facts, and 0 would assert the first.
        weight = None
        if str(weight_text).strip() != '':
            weight = float(weight_text)
        payload = {
            'title': (self._title_tb.text or '').strip(),
            'subject': self._subject_dd.selected_value,
            'type': self._type_dd.selected_value,
            'due_date': self._due_dp.date,
            'start_date': self._start_dp.date,
            'weight': weight,
            # No `or STATUS_DEFAULT` fallback: substituting a default for a status
            # the form could not read is the same silent rewrite _select_choice()
            # exists to stop. _validate_fields() blocks the save instead.
            'status': self._status_dd.selected_value,
            # A description of spaces is no description: '' is normalised to
            # None so the column holds one value for "nothing here", which is
            # what the dashboard and the export both test against.
            'description': (self._desc_ta.text or '').strip() or None,
            # Sorted descending so the list reads 14, 7, 3, 2, 1 — furthest-out
            # reminder first, matching the pill order on screen. The comparison
            # is over the dict's KEYS (the offsets), not the CheckBoxes, which
            # is why the comprehension unpacks both.
            'reminder_days': sorted(
                (d for d, cb in self._day_checks.items() if cb.checked), reverse=True),
            # Copied, not passed by reference: the payload crosses to the
            # server, and handing over the live working list would let a later
            # edit in this dialog appear to change what was already sent.
            'linked_note_ids': list(self._linked_note_ids),
        }
        # Preserve the parser audit trail (FR17) on create/preview. Only these
        # three, and only here: the server excludes 'confidence' and
        # 'source_text' from the edit whitelist, so once a record exists the
        # trail of what the parser saw can no longer be rewritten.
        if self._mode == 'preview' and self._prefill:
            payload['confidence'] = self._prefill.get('confidence')
            payload['source_text'] = self._prefill.get('source_text')
            payload['term_info'] = self._prefill.get('fields', {}).get('term_info')
        return payload

    # --- validation --------------------------------------------------------
    def _validate_fields(self):
        """Check the form on submit; True when it is worth calling the server.

        ADDITIVE, never a replacement (docs/VALIDATION.md §6): every rule below is
        also enforced by the server's _validate_assessment_payload, which stays the
        authority. What checking here buys is WHERE the answer appears — beside the
        field that is wrong, the moment Save is pressed (FR03/FR04) — instead of a
        toast arriving after a round trip.

        Runs on submit only. A message that appears while the student is still
        typing the first letter of a title is worse than no message at all.

        Deliberately says nothing about parser confidence. 'preview' mode exists so
        a LOW-confidence parse can be hand-corrected, so a low score must never be
        what stops a save.

        Takes nothing and returns True/False. Its other effect is on screen: a
        message under every field that failed, and the cursor in the first of
        them. Reads the seven kept field panels; touches no table.
        """
        # Wipe last attempt's messages first. Without this a field the student
        # has since fixed would keep its complaint, and they would be reading a
        # screen that describes a form that no longer exists.
        clear_field_errors(self._title_field, self._subject_field,
                           self._type_field, self._due_field, self._start_field,
                           self._weight_field, self._status_field)

        # Collected rather than reported one at a time, and in the order the fields
        # appear on screen: one Save then names every problem, and the cursor can
        # be sent to the first of them. Each entry is a (field_panel, message)
        # pair, so the message and the control it belongs to travel together.
        problems = []

        # Stripped before it is measured, so a title of spaces counts as empty
        # and a trailing space cannot be what pushes it over the length cap —
        # the server strips it the same way before applying the same bound.
        title = (self._title_tb.text or '').strip()
        if not title:
            problems.append((self._title_field, 'Title is required.'))
        elif len(title) > MAX_TITLE_LENGTH:
            problems.append((
                self._title_field,
                'Title is too long — keep it to %d characters or fewer '
                '(currently %d).' % (MAX_TITLE_LENGTH, len(title))))

        # The subject DropDown carries a placeholder, so "not chosen yet" arrives
        # here as None. That is a student who has not answered, not a student who
        # answered wrongly, and it must not be reported as an invalid subject.
        if not self._subject_dd.selected_value:
            problems.append((self._subject_field, 'Choose a subject.'))

        # Membership, not `is None`: this one check catches both the unanswered
        # placeholder and a stored value _select_choice() refused to select,
        # and both need the same answer from the student.
        if self._type_dd.selected_value not in TYPE_VALUES:
            problems.append((self._type_field, 'Choose a type.'))

        # No format or range check on either date: a DatePicker yields a real
        # date object or None, so "is it a date?" cannot fail here. Nothing
        # rejects a due date in the past either — a SAC the student is logging
        # late is still a SAC.
        due_date = self._due_dp.date
        if due_date is None:
            problems.append((self._due_field, 'Due date is required.'))

        start_date = self._start_dp.date
        if start_date is not None and due_date is not None and start_date > due_date:
            # Both dates are fine on their own; only the pair is wrong.
            problems.append((
                self._start_field,
                'Start date cannot be after Due date. Check the two dates.'))

        # Weight is optional, so an empty box is skipped entirely; only a box
        # with something in it has to be a number in range. try/float rather
        # than a pattern test because float() is the same conversion
        # _build_payload will perform — testing anything else would let a value
        # pass here and raise there. else: runs only when no exception fired,
        # which keeps the range check out of the try and stops it from
        # accidentally catching an error of its own.
        weight_text = self._weight_tb.text
        weight_text = str(weight_text).strip() if weight_text is not None else ''
        if weight_text:
            try:
                weight = float(weight_text)
            except (ValueError, TypeError):
                problems.append((self._weight_field, 'Weight must be a number.'))
            else:
                if not (MIN_WEIGHT <= weight <= MAX_WEIGHT):
                    problems.append((
                        self._weight_field,
                        'Weight (%%) must be between %g and %g (you entered %g).'
                        % (MIN_WEIGHT, MAX_WEIGHT, weight)))

        if self._status_dd.selected_value not in STATUS_VALUES:
            problems.append((self._status_field, 'Choose a status.'))

        # Everything is written at the end, in one pass, so the screen changes
        # once. The clean case returns after this loop rather than before it,
        # which is what guarantees the clear at the top has already run.
        for field_panel, message in problems:
            set_field_error(field_panel, message)
        if not problems:
            return True

        # Put the cursor in the first offending control so its message is on screen
        # even when the dialog is scrolled past it. No toast: the message belongs
        # beside the field (FR04), and repeating it in the corner would say the
        # same thing twice.
        #
        # Wrapped because focus() is a courtesy, not the job: not every Anvil
        # control implements it (a DatePicker on some browsers does not), and
        # failing to move the cursor must not swallow the False below and let a
        # form the app knows is wrong reach the server.
        try:
            problems[0][0].input_component.focus()
        except Exception:
            pass
        return False

    # --- handlers ----------------------------------------------------------
    def _on_save_click(self, **event_args):
        """Save button: validate, build the payload, call the server, close.

        **event_args is Anvil's event contract; nothing in it is read.

        Calls update_assessment(assessment_id, payload) in edit mode and
        create_assessment(payload) in create and preview mode — preview has no
        row yet, which is the whole point of confirming before commit (FR17).
        Both write to the assessments table under the server's ownership check
        (NFR03/FR04); this method never touches a table itself.

        On success it raises 'x-close-alert' with the assessment id, which is
        what the alert() in DashboardForm returns. On any failure it returns
        quietly with the dialog still open, so nothing the student typed is
        lost.
        """
        # 1. Client-side first pass. Cheap, and it puts the message beside the
        #    field instead of in a toast after a round trip. The server
        #    re-checks all of it regardless (docs/VALIDATION.md §6).
        if not self._validate_fields():
            return
        # 2. Gather the controls into the server's dict.
        try:
            payload = self._build_payload()
        except (ValueError, TypeError):
            # Unreachable while _validate_fields() runs first (it is what proves
            # the weight parses); kept so a later change there cannot put a raw
            # traceback in front of the student.
            set_field_error(self._weight_field, 'Weight must be a number.')
            return
        # 3. One branch, two callables. Edit already knows its id, so it keeps
        #    the one it was handed; create and preview get the id back from the
        #    insert. Preview falls into the else on purpose — it is a create
        #    that happens to arrive pre-filled.
        try:
            if self._mode == 'edit':
                anvil.server.call('update_assessment', self._assessment_id, payload)
                result_id = self._assessment_id
            else:
                result_id = anvil.server.call('create_assessment', payload)
        except Exception as e:
            # The server's own validators raise ValueError with text written
            # for the student, so friendly_error shows those as they are and
            # replaces anything technical. Returning keeps the dialog open with
            # the student's work in it, which a close would throw away.
            toast_error(friendly_error(e))
            return
        # 4. Confirm, then close by raising the event the parent alert() is
        #    listening for. The toast fires BEFORE the close because the form
        #    is about to be torn down; common.toast attaches to the page, not
        #    to this panel, so the message survives the dialog going away.
        toast("Assessment saved.")
        self.raise_event('x-close-alert', value=result_id)

    def _on_cancel_click(self, **event_args):
        """Cancel button (all four modes): close without saving anything.

        **event_args is Anvil's event contract; nothing in it is read. Closes
        by raising 'x-close-alert', the same single exit every mode uses.
        """
        # Cancel means "no new id" everywhere except bulk mode, where rows may
        # already have been committed before the student closed the dialog; the
        # count goes back so the dashboard still refreshes and shows them.
        self.raise_event('x-close-alert', value=self._bulk_inserted or None)

    # --- linked notes (FR12) -----------------------------------------------
    # FR12 asks for the cross-reference itself and puts a UI for managing it in
    # the stretch list; these five methods are that stretch UI. The search
    # behind it is FR11's search_notes, reused rather than reimplemented, so
    # the picker matches the Notes screen word for word.

    def _on_note_search(self, **event_args):
        """Search button / Enter in the search box: list notes to attach.

        **event_args is Anvil's event contract; nothing in it is read. Reads
        the search box, calls search_notes(query=...) — which is scoped to the
        current user on the server (NFR03) — and redraws _note_results.
        Returns nothing.
        """
        # An empty box is passed as None, not '': search_notes reads None as
        # "no filter" and returns everything, which is the sensible answer to
        # pressing Search with nothing typed.
        query = (self._note_search_tb.text or '').strip()
        try:
            notes = anvil.server.call('search_notes', query=query or None)
        except Exception as e:
            # Returning before the clear() below leaves the previous results on
            # screen. A failed search should not also destroy the hits the
            # student was in the middle of using.
            toast_error(friendly_error(
                e, fallback="Couldn't search your notes. Try again."))
            return
        self._note_results.clear()
        if not notes:
            self._note_results.add_component(
                make_empty_state('No notes found',
                                 'Try another word from the note title.'))
            return
        # Results are quiet ghost buttons in one wrapping row — they are a
        # picker, not a list the student is meant to read.
        row = make_row()
        # Capped at _NOTE_RESULT_LIMIT; see the constant for why.
        for n in notes[:_NOTE_RESULT_LIMIT]:
            # Titles are cached on the way past, so _render_links() can name a
            # linked note without a second call. '(untitled)' stands in for a
            # note saved with an empty title, which is legal.
            self._note_titles[n['id']] = n.get('title') or '(untitled)'
            b = Button(text='+ %s' % self._note_titles[n['id']], role='ghost')
            # Default argument captures this loop's id; a bare closure would
            # give every button the last note's id.
            b.set_event_handler('click', lambda nid=n['id'], **e: self._add_link(nid))
            row.add_component(b)
        self._note_results.add_component(row)

        # Say when the list was cut. Without this the ninth match simply is not
        # there, and a student who knows the note exists has no way to tell whether
        # the search failed or the list was truncated — so they retype the same
        # search instead of narrowing it, which is the one thing that would help.
        if len(notes) > _NOTE_RESULT_LIMIT:
            self._note_results.add_component(
                Label(text='Showing the first %d of %d matches — type more of the '
                           'title to narrow it down.'
                           % (_NOTE_RESULT_LIMIT, len(notes)),
                      role='micro'))

    def _add_link(self, note_id):
        """Attach a note to this assessment (in memory only, until Save).

        note_id: a notes row id string, from a result button in the picker.

        Nothing is written here — the list only reaches the table as the
        payload's 'linked_note_ids' when Save is pressed, so closing the dialog
        discards the change. The membership test makes a double-click a no-op
        instead of a duplicated id.
        """
        if note_id not in self._linked_note_ids:
            self._linked_note_ids.append(note_id)
        self._render_links()

    def _remove_link(self, note_id):
        """Detach a note (in memory only). note_id: the id to drop.

        Rebuilds the list rather than calling .remove(): a comprehension
        cannot raise on an id that is not there, so a double-clicked x is
        harmless, and it drops any duplicate that somehow got in.
        """
        self._linked_note_ids = [n for n in self._linked_note_ids if n != note_id]
        self._render_links()

    def _resolve_link_titles(self):
        """Populate the id->title cache for already-linked notes (edit mode).

        Called from _load() before the first _render_links(), because the
        stored row carries note IDS and the pills have to show note TITLES.

        Fetches ALL of the student's notes with one unfiltered search_notes
        call rather than one lookup per linked id: there is no get-by-id
        callable for notes, and a handful of links would otherwise mean a
        handful of round trips while the dialog is opening (NFR01).

        Failure is swallowed on purpose. The titles are decoration — if they
        cannot be fetched _render_links() falls back to showing the raw id,
        and an assessment must still be editable when the notes table is not
        reachable.
        """
        # Guard first, because the common case is no links at all: create mode
        # and most edits have an empty list, and this saves them a whole
        # round-trip on the way to a dialog that is trying to open (NFR01).
        if not self._linked_note_ids:
            return
        try:
            # The cache is keyed by note id and filled from EVERY note the
            # student owns, not just the linked ones — search_notes is already
            # scoped to current_user (NFR03), so the extra rows cost nothing to
            # trust and mean a note linked later by _add_link is already named.
            for n in anvil.server.call('search_notes'):
                # A note saved with an empty title is a real note the student
                # can still open, so it gets a visible stand-in rather than a
                # blank pill that looks like a rendering fault.
                self._note_titles[n['id']] = n.get('title') or '(untitled)'
        except Exception:
            pass
        # AN ID WHOSE NOTE IS GONE IS HANDLED BY DOING NOTHING. delete_note
        # unlinks the back-references as it deletes (FR12), so normally no such
        # id survives — but this form read linked_note_ids at _load, and a note
        # deleted in another tab in the moment since then is still in the list
        # and in no search result. It therefore gets no cache entry at all,
        # which is precisely what _render_links' .get(nid, nid) fallback is
        # for: the pill shows the raw id, and its x is how the student clears
        # the dead reference off the row on the next Save.

    def _render_links(self):
        """Redraw the row of attached-note pills from _linked_note_ids.

        Clears and rebuilds the whole row on every add and remove. The row is
        at most a few pills, so tracking which one changed would cost more to
        read than it saves, and a full redraw cannot leave a pill behind for a
        note that is no longer linked.
        """
        self._linked_pills.clear()
        for nid in self._linked_note_ids:
            # chip + its own remove button, so the pair reads as one token.
            # .get(nid, nid) falls back to the raw id when the title lookup
            # failed — an ugly pill the student can still remove beats a pill
            # that is not there for a link that is.
            pill = make_row(make_chip(self._note_titles.get(nid, nid), 'accent'))
            x = Button(text='x', role='iconbtn')
            # Same default-argument capture as the search buttons: without
            # `i=nid` every x would remove whichever note was drawn last.
            x.set_event_handler('click', lambda i=nid, **e: self._remove_link(i))
            pill.add_component(x)
            self._linked_pills.add_component(pill)

    # --- bulk mode ---------------------------------------------------------
    def _build_bulk(self):
        """Paste-many UI: parse each line, tick the createable ones, insert them.

        Per line, not all-or-nothing: create_bulk_assessments commits every record
        that validates and reports the rest (FR02), so one bad line no longer
        discards a whole screen of correctly parsed assessments.

        Called from __init__ INSTEAD OF the field stack, not as well as it, so
        none of self._title_tb and friends exist in this mode. Builds the
        screen only; nothing is parsed or written until a button is pressed.
        """
        # 1. Heading and instruction. The subtitle states the one rule the
        #    student has to follow — one assessment per line — because the
        #    paste box cannot enforce it.
        self.add_component(make_page_title(
            'Bulk add assessments',
            'Paste one assessment per line, then Parse all.'))
        # 2. The paste box. The placeholder is two worked examples rather than
        #    a description, because the parser's input is free text and an
        #    example teaches its shape faster than a rule would.
        #    160px ~= six pasted lines visible at once: enough for the student
        #    to see and proof-read a whole paste without scrolling the box.
        self._bulk_ta = TextArea(
            placeholder='Methods SAC2 due Friday week 5 worth 25%\nPhysics exam 12/06 30%',
            height='160px')
        self.add_component(self._bulk_ta)

        # 3. Parse is a separate step from Create on purpose: FR02 and the
        #    client's answer to interview question 6 both say the student sees
        #    what was understood BEFORE anything is written.
        parse_btn = Button(text='Parse all', role='primary')
        parse_btn.set_event_handler('click', self._on_bulk_parse_click)
        self.add_component(make_row(parse_btn))

        # 4. Empty until Parse fills it; _render_multi owns everything inside.
        self._multi_panel = ColumnPanel()
        self.add_component(self._multi_panel)
        # One entry per parsed line: the parse itself (which carries the line
        # number), its tick box, the row the chips sit in, and the hidden label a
        # server rejection is written into.
        self._multi_rows = []   # [{'parsed', 'check', 'row', 'mark'}, ...]

        # 5. Footer. 'Create selected', not 'Save': the button acts on the
        #    ticked subset, and the label has to say so before it is pressed.
        #    Cancel shares _on_cancel_click with the other three modes, which
        #    is where the partial-commit count is handed back.
        self.add_component(make_divider())
        cancel_btn = Button(text='Cancel', role='secondary')
        cancel_btn.set_event_handler('click', self._on_cancel_click)
        create_btn = Button(text='Create selected', role='primary')
        create_btn.set_event_handler('click', self._on_bulk_create_click)
        self.add_component(make_row(cancel_btn, create_btn))

    def _blocking_reason(self, parsed):
        """Why a parsed line cannot be ticked by default, or None when it can.

        Mirrors what the server's _validate_assessment_payload will actually
        demand, so a line that arrives ticked is one it will accept. It used to
        ask only about the confidence, the subject and the due date, which left
        the title and the weight range to be discovered as a rejection AFTER the
        student had pressed Create.

        The student can still tick any line by hand — this decides the default,
        not what is allowed.

        parsed: one element of the parse_bulk() list —
            {'fields', 'why', 'confidence', 'source_text', 'line_index'}.
        Returns a short lower-case phrase for the 'bad' chip beside the line
        ('needs a subject'), or None when the line is fit to tick. The phrase
        is a fragment, not a sentence, because it is read inside a chip.
        """
        # Confidence first: a LOW parse means fewer than two fields were
        # recognised (FR17), so the checks below would mostly be reporting
        # symptoms of that one cause.
        if parsed.get('confidence') == 'LOW':
            return 'LOW confidence'

        # First failure wins and returns immediately. One chip has room for one
        # reason, and the student fixes lines one at a time anyway.
        f = parsed.get('fields', {})
        # isinstance rather than `or ''`: the parser is meant to give a string,
        # but this value goes straight into len(), and a None or a number
        # arriving there would raise inside a UI redraw.
        title = f.get('title')
        title = title.strip() if isinstance(title, str) else ''
        if not title:
            return 'needs a title'
        if len(title) > MAX_TITLE_LENGTH:
            return 'title too long'
        # Against the full canonical catalog, not the student's locked
        # subjects: bulk lines are not hand-picked from a dropdown, and an
        # unrecognised alias (FR16) passes through the parser unchanged.
        if f.get('subject') not in SUBJECTS:
            return 'needs a subject'
        if f.get('type') not in TYPE_VALUES:
            return 'needs a type'
        if f.get('due_date') is None:
            return 'needs a due date'

        # Weight is optional, so only a value that is present has to be legal.
        # bool is excluded explicitly because in Python True is an int and
        # would otherwise sail through as the weight 1.
        weight = f.get('weight')
        if weight is not None:
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                return 'weight must be a number'
            if not (MIN_WEIGHT <= weight <= MAX_WEIGHT):
                return 'weight must be %g-%g' % (MIN_WEIGHT, MAX_WEIGHT)
        return None

    def _createable(self, parsed):
        """True when the server would accept this line as parsed.

        parsed: one parse_bulk() result dict. Thin wrapper so the tick-box
        default reads as a question ('is this createable?') while the chip
        beside it gets the answer's reason from the same single rule set.
        """
        return self._blocking_reason(parsed) is None

    def _line_label(self, parsed):
        """'Line 7' — the line's position in what the student actually PASTED.

        parse_bulk carries this through as 'line_index' (0-based over the original
        paste, blank lines included). It has to be carried, because a rejection's
        'index' is a position in the list of TICKED records: the two stop agreeing
        the moment one line is left unticked, and reporting the wrong one sends the
        student to a line of their own paste that is perfectly fine.

        parsed: one parse_bulk() result dict.
        Returns 'Line N' (1-based, for a human counting down the paste box) or
        the vague 'One line' when line_index is missing or not a whole number —
        a wrong line number would be worse than no line number.
        """
        line_index = parsed.get('line_index')
        # bool excluded again: True is an int, and 'Line 2' for a True would be
        # a confident lie about where the problem is.
        if isinstance(line_index, int) and not isinstance(line_index, bool):
            return 'Line %d' % (line_index + 1)
        return 'One line'

    def _on_bulk_parse_click(self, **event_args):
        """'Parse all' button: send the paste to the parser, show the result.

        **event_args is Anvil's event contract; nothing in it is read. Calls
        parse_bulk(text) — parsing only, no table writes — and hands the list
        to _render_multi. Returns nothing; writes nothing.
        """
        text = (self._bulk_ta.text or '').strip()
        # Caught here rather than sent, because parse_bulk raises on an empty
        # paste and a toast_warn is the right weight for "you have not done
        # your half yet": nothing failed, there is just nothing to do.
        if not text:
            toast_warn("Paste some lines first.")
            return
        try:
            results = anvil.server.call('parse_bulk', text)
        except Exception as e:
            # The server's own limits (too many lines, a line too long) come
            # back as ValueErrors written for the student; the fallback covers
            # everything else. The previous results stay on screen.
            toast_error(friendly_error(
                e, fallback="Couldn't read those lines. Try again."))
            return
        self._render_multi(results)

    def _render_multi(self, results):
        """Draw one reviewable card per parsed line, and rebuild _multi_rows.

        results: the parse_bulk() list — each element
            {'fields', 'why', 'confidence', 'source_text', 'line_index'}.

        Leaves self._multi_rows holding one dict per card:
            {'parsed': the result dict this card came from,
             'check':  its CheckBox (ticked = send this line),
             'row':    the chip row, so a status chip can be appended later,
             'mark':   the hidden Label a server rejection is written into}
        That list is the ONLY link between a card on screen and the record
        sent for it, so _on_bulk_create_click can put each rejection back
        beside the line it belongs to.

        Rebuilds from scratch every parse, panel and list together, so a
        re-parse cannot leave a card pointing at a record that no longer
        exists.
        """
        self._multi_panel.clear()
        self._multi_rows = []
        if not results:
            self._multi_panel.add_component(
                make_empty_state('No lines to parse',
                                 'Add a line to the box above, then Parse all.'))
            return
        for parsed in results:
            f = parsed.get('fields', {})
            conf = parsed.get('confidence', 'LOW')
            # The tick starts where the eligibility rule puts it, so the
            # common case is "press Create" and only the doubtful lines need a
            # decision. The student can still tick or untick any of them.
            createable = self._createable(parsed)
            # One card per line so the tick, the confidence and the summary of
            # what will actually be created stay visually bound together.
            card = make_list_card()
            cb = CheckBox(checked=createable)
            # The summary is what WILL be written, not what was typed: '?'
            # marks a field the parser did not find, so a missing subject is
            # visible before Create rather than after. fmt_date renders the
            # date as 'DD MMM YYYY' (NFR08) and prints 'no date' for None.
            summary = '%s — %s · %s · %s' % (
                f.get('title') or '(untitled)', f.get('subject') or '?',
                f.get('type') or '?', fmt_date(f.get('due_date')))
            # Appended only when there is one, so a line with no weight does
            # not display a misleading '0%'. '%%' is the literal per-cent sign.
            if f.get('weight') is not None:
                summary += ' · %g%%' % f.get('weight')
            row = make_row(make_chip(self._line_label(parsed)), cb,
                           make_chip(conf, CONF_TONE.get(conf)),
                           Label(text=summary))
            if not createable:
                # Why this line came in unticked, as a plain 'bad' chip. It used
                # to be a Label with two roles, and two !important colour rules
                # at the same specificity are decided by stylesheet order — the
                # chip is one role, and matches every other status chip.
                row.add_component(make_chip(self._blocking_reason(parsed), 'bad'))
            # Created hidden and up front, the way make_field() creates its error
            # label: a rejection then has somewhere to land without re-flowing the
            # dialog, and it can be cleared and rewritten on the next attempt.
            mark = Label(text='', role='fielderror', visible=False)
            card.add_component(row)
            card.add_component(mark)
            self._multi_panel.add_component(card)
            self._multi_rows.append(
                {'parsed': parsed, 'check': cb, 'row': row, 'mark': mark})

    def _mark_created(self, entry):
        """Lock a line the server has written, so Create cannot insert it twice.

        Unticking alone would not do: the student can tick it again. This is the
        guard that makes a partial commit safe to retry — without it, fixing one
        bad line and pressing Create a second time would duplicate every line that
        went in the first time.

        entry: one _multi_rows dict. Its 'check' is unticked and disabled and
        an 'added' chip is appended to its 'row'. Returns nothing.
        """
        # Already locked means already added on an earlier attempt: return
        # before the chip, or a second Create would stack a second 'added' chip
        # on the same line.
        if not entry['check'].enabled:
            return
        entry['check'].checked = False
        entry['check'].enabled = False
        entry['row'].add_component(make_chip('added', 'ok'))

    def _mark_rejected(self, entry, reason):
        """Write the server's reason beside the line it belongs to; return its label.

        Left ticked on purpose: the line still has to go in once it is fixed.

        entry: one _multi_rows dict; the message goes into its hidden 'mark'
            Label, which was created up front by _render_multi.
        reason: the server's own sentence for this rejection, already written
            for the student by _validate_assessment_payload.
        Returns the line's label ('Line 7') so the caller can collect the
        failures for one summarising toast.
        """
        label = self._line_label(entry['parsed'])
        # The label is repeated inside the message even though the message sits
        # beside its own line: the toast names the same labels, and the two
        # have to be matchable when the list is long enough to scroll.
        entry['mark'].text = '%s: %s' % (label, reason)
        entry['mark'].visible = True
        return label

    def _on_bulk_create_click(self, **event_args):
        """'Create selected' button: insert every ticked line, report the rest.

        **event_args is Anvil's event contract; nothing in it is read.

        Sends the ticked lines to create_bulk_assessments as a list of dicts
        holding title, subject, type, due_date, weight, confidence,
        source_text and term_info; status, reminder_days and the rest take the
        server's defaults, since a bulk paste has no way to express them.

        The call returns {'inserted': int, 'ids': [...], 'rejected':
        [{'index', 'reason'}, ...]} where 'index' is a position in the list
        SENT, not in the paste. Per FR02 the valid lines commit even when
        others fail, so 'inserted' and 'rejected' can both be non-empty.

        Closes with the running total only when nothing was rejected;
        otherwise the dialog stays open so the failed lines can be fixed and
        sent again.
        """
        # 1. Collect the ticked lines. `records` is what goes to the server and
        #    `submitted` is the entry behind each one at the same position —
        #    two lists rather than one, because the server's rejections come
        #    back keyed by position and this is what turns a position back into
        #    a card on screen.
        records = []
        submitted = []   # the entry behind each record, in the same order
        for entry in self._multi_rows:
            # Stale marks are wiped before every attempt, the same way the single
            # editor clears its field errors before re-validating.
            entry['mark'].text = ''
            entry['mark'].visible = False
            if not entry['check'].checked:
                continue
            parsed = entry['parsed']
            f = parsed.get('fields', {})
            # Fields are copied out one by one rather than the whole dict being
            # forwarded: 'why' and 'line_index' are for this screen only, and
            # the server's whitelist would reject them.
            records.append({
                'title': f.get('title'),
                'subject': f.get('subject'),
                'type': f.get('type'),
                'due_date': f.get('due_date'),
                'weight': f.get('weight'),
                'confidence': parsed.get('confidence'),
                'source_text': parsed.get('source_text'),
                'term_info': f.get('term_info'),
            })
            submitted.append(entry)
        # 2. Nothing ticked is a nudge, not a failure — an empty list would
        #    otherwise be a wasted round trip that comes back saying nothing.
        if not records:
            toast_warn("Nothing ticked to create.")
            return
        # 3. One call for the whole batch, not one per line: the server wraps
        #    the accepted records in a single Transaction, and a line-at-a-time
        #    loop would be as many round trips as there are lines (NFR01).
        try:
            result = anvil.server.call('create_bulk_assessments', records)
        except Exception as e:
            # A raise here means the CALL failed — too many lines, no user, a
            # dropped connection — so no row was written and nothing on screen
            # should be marked either way.
            toast_error(friendly_error(e))
            return

        # 4. Unpack the result. += rather than =, because the student may press
        #    Create more than once: the count handed back on close has to be
        #    every row this dialog wrote, not just the last attempt's.
        inserted = result.get('inserted', 0)
        rejected = result.get('rejected') or []
        self._bulk_inserted += inserted

        # 5. Turn the rejections into a position -> reason map. This is the
        #    step that puts each server message back beside the right card.
        #
        # create_bulk_assessments commits per line (FR02), so 'rejected' can be
        # non-empty while rows were still written — the old "nothing was saved"
        # message would now be a lie. Its 'index' is the record's position in the
        # list this form sent, so every position it does NOT name went in.
        reasons = {}
        for rejection in rejected:
            # Range-checked before it is used as a key: an index outside the
            # list this form sent cannot name one of these cards, and using it
            # anyway would attach a message to the wrong line.
            position = rejection.get('index')
            if isinstance(position, int) and 0 <= position < len(submitted):
                reasons[position] = rejection.get('reason')
        # Only lock lines when every rejection could be placed. If one could not,
        # it is no longer knowable which lines were written, and locking the wrong
        # one would lose it.
        placed_all = len(reasons) == len(rejected)

        # 6. Mark the cards. A position that is in `reasons` failed; every
        #    other position went in, which is only safe to act on when every
        #    rejection was placed. `marked` collects the labels of the failures
        #    for the toast below.
        marked = []
        for position, entry in enumerate(submitted):
            if position in reasons:
                marked.append(self._mark_rejected(entry, reasons[position]))
            elif placed_all:
                self._mark_created(entry)

        # 7a. Everything went in: say so and close, handing back the running
        #     total so the dashboard refreshes.
        if not rejected:
            toast("Created %d assessment(s)." % inserted)
            self.raise_event('x-close-alert', value=self._bulk_inserted)
            return

        # 7b. Something failed. Both halves, honestly, and the dialog stays
        # open so the failed lines can be fixed and sent again. Each reason is
        # written beside its own line above, so the toast only has to name
        # them — unless nothing could be placed, in which case the reasons
        # never reached a card and the toast has to carry them itself.
        if placed_all:
            detail = ('%s could not be added — the reason is beside each one.'
                      % ', '.join(marked))
        else:
            detail = ' '.join(str(r.get('reason')) for r in rejected)
        # 'Added 3 of 5' counts THIS attempt (len(records) is what was ticked
        # this time), not the running total — the student is looking at the
        # result of the button they just pressed.
        if inserted:
            toast_error('Added %d of %d. %s' % (inserted, len(records), detail))
        else:
            toast_error('Nothing was added. %s' % detail)
