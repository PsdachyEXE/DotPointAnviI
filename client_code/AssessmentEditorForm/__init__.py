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

Every mode checks the form before it calls the server (FR03): the message lands
beside the offending field via common.set_field_error rather than arriving as a
toast after a round trip (FR04). Nothing gates on parser confidence — a LOW
parse is exactly what 'preview' mode exists to let the student hand-correct.

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

# Parser confidence -> chip tone, the date helpers and the page heading all come
# from common now. They used to be copied into this file, which is how the same
# confidence ended up a different colour here than on the dashboard.


class AssessmentEditorForm(ColumnPanel):
    def __init__(self, mode='create', assessment_id=None, prefill=None, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self._mode = mode
        self._assessment_id = assessment_id
        self._prefill = prefill
        self._linked_note_ids = []
        self._note_titles = {}   # id -> title cache for the linked-note pills
        # Rows written by bulk mode so far. Carried on the close event so a
        # partial commit still tells the dashboard there is something new.
        self._bulk_inserted = 0

        # The parser's per-field provenance (FR17), read once here so each
        # make_field() below can hang its own 'why' line under its control.
        # Empty outside preview mode, which is what silences the hints.
        self._why = (prefill or {}).get('why', {}) if mode == 'preview' else {}

        if mode == 'bulk':
            self._build_bulk()
            return

        # --- header (+ confidence chip in preview mode) ---
        titles = {'create': 'Add assessment', 'edit': 'Edit assessment',
                  'preview': 'Confirm parsed assessment'}
        header = make_row(Label(text=titles.get(mode, 'Assessment'),
                                role='pagetitle'))
        if mode == 'preview' and prefill:
            conf = prefill.get('confidence', 'LOW')
            header.add_component(make_chip(conf, CONF_TONE.get(conf)))
        self.add_component(header)

        # Each make_field() panel is kept, not just added: _validate_fields()
        # needs the wrapper to hang a message under the control it belongs to.

        # --- title ---
        # The cap is stated up front rather than only on rejection — a student
        # should not have to fail a save to learn how long a title may be.
        self._title_tb = TextBox()
        self._title_field = make_field(
            'Title', self._title_tb,
            hint='Up to %d characters.' % MAX_TITLE_LENGTH, required=True)
        self.add_component(self._title_field)

        # --- subject ---
        # The dropdown offers the student's locked subjects (spec §11) when
        # they've onboarded; out-of-list values (legacy rows, parser fallback
        # hits) are appended on load so nothing becomes uneditable.
        self._subject_dd = DropDown(items=self._subject_items(),
                                    include_placeholder=True)
        self._subject_field = make_field('Subject', self._subject_dd,
                                         hint=self._why_text('subject'),
                                         required=True)
        self.add_component(self._subject_field)

        # --- type ---
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

        # --- due date ---
        self._due_dp = DatePicker(format='DD MMM YYYY')
        self._due_field = make_field('Due date', self._due_dp,
                                     hint=self._why_text('due_date'),
                                     required=True)
        self.add_component(self._due_field)

        # --- start date (optional) ---
        self._start_dp = DatePicker(format='DD MMM YYYY')
        self._start_field = make_field('Start date (optional)', self._start_dp)
        self.add_component(self._start_field)

        # --- weight ---
        # The range is shown as a hint, but only when the parser has nothing to
        # say about this field — its provenance line is the more useful of the two.
        self._weight_tb = TextBox(type='number')
        self._weight_field = make_field(
            'Weight (%)', self._weight_tb,
            hint=(self._why_text('weight')
                  or 'A percentage between %g and %g.' % (MIN_WEIGHT, MAX_WEIGHT)))
        self.add_component(self._weight_field)

        # --- status ---
        self._status_dd = DropDown(items=list(STATUSES), include_placeholder=True)
        self._status_dd.selected_value = STATUS_DEFAULT
        self._status_field = make_field('Status', self._status_dd)
        self.add_component(self._status_field)

        # --- reminder pills ---
        # Toggle pills rather than a column of tickboxes: the five offsets are a
        # single choice the student scans horizontally, and they fit on one line.
        self._day_checks = {}
        pills = make_row()
        for d in REMINDER_DAY_OPTIONS:
            cb = CheckBox(text='%d' % d, role='pill')
            self._day_checks[d] = cb
            pills.add_component(cb)
        self.add_component(make_field('Remind me (days before due)', pills))

        # --- description ---
        self._desc_ta = TextArea()
        self.add_component(make_field('Description (optional)', self._desc_ta))

        # --- linked notes (FR12) ---
        # Its own section under a divider: everything above is the assessment
        # itself, everything below is a search-and-attach flow with its own
        # results list, so mixing them into the field stack read as clutter.
        self.add_component(make_divider())
        self.add_component(make_section_header('Linked notes', 'optional'))
        self._note_search_tb = TextBox(placeholder='Search your notes to link')
        self._note_search_tb.set_event_handler('pressed_enter', self._on_note_search)
        search_btn = Button(text='Search', role='secondary')
        search_btn.set_event_handler('click', self._on_note_search)
        self.add_component(make_toolbar(self._note_search_tb, search_btn))
        self._note_results = ColumnPanel()
        self.add_component(self._note_results)
        self._linked_pills = make_row()
        self.add_component(self._linked_pills)

        # --- footer ---
        self.add_component(make_divider())
        cancel_btn = Button(text='Cancel', role='secondary')
        cancel_btn.set_event_handler('click', self._on_cancel_click)
        save_btn = Button(text='Save', role='primary')
        save_btn.set_event_handler('click', self._on_save_click)
        self.add_component(make_row(cancel_btn, save_btn))

        self._load()

    # --- helpers -----------------------------------------------------------
    def _subject_items(self):
        """The student's locked subjects (session-cached settings), falling
        back to the full canonical catalog pre-onboarding."""
        try:
            locked = get_session_settings().get('subjects')
        except Exception:
            locked = None
        return list(locked) if locked else list(SUBJECTS)

    def _select_subject(self, subject):
        """Select `subject` in the dropdown, appending it if out-of-list."""
        if not subject:
            return
        items = list(self._subject_dd.items)
        if subject not in items:
            items.append(subject)
            self._subject_dd.items = items
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
        """
        if stored in allowed:
            dropdown.selected_value = stored
            set_field_error(field_panel, None)
            return
        dropdown.selected_value = None
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

        Returns rather than renders: make_field() puts it directly under the
        control it explains, so preview mode needs no extra components.
        """
        if self._why and key in self._why:
            return self._why[key]
        return None

    def _load(self):
        """Populate fields from prefill (preview) or the server (edit)."""
        default_days = [7, 2]
        try:
            settings = get_session_settings()
            default_days = settings.get('default_reminder_days') or [7, 2]
        except Exception:
            pass

        if self._mode == 'preview' and self._prefill:
            f = self._prefill.get('fields', {})
            self._title_tb.text = f.get('title') or ''
            if f.get('subject') in SUBJECTS:
                self._select_subject(f.get('subject'))
            if f.get('type'):
                self._select_choice(self._type_dd, self._type_field,
                                    f.get('type'), TYPE_VALUES, 'Type')
            self._due_dp.date = f.get('due_date')
            if f.get('weight') is not None:
                self._weight_tb.text = ('%g' % f.get('weight'))
            self._check_days(default_days)

        elif self._mode == 'edit' and self._assessment_id:
            try:
                a = anvil.server.call('get_assessment', self._assessment_id)
            except Exception as e:
                toast_error(friendly_error(
                    e, fallback="Couldn't open that assessment. Try again."))
                return
            self._title_tb.text = a.get('title') or ''
            self._select_subject(a.get('subject'))
            # Membership-checked, never assigned blind: see _select_choice().
            self._select_choice(self._type_dd, self._type_field,
                                a.get('type'), TYPE_VALUES, 'Type')
            self._due_dp.date = from_iso(a.get('due_date'))
            self._start_dp.date = from_iso(a.get('start_date'))
            if a.get('weight') is not None:
                self._weight_tb.text = ('%g' % a.get('weight'))
            self._select_choice(self._status_dd, self._status_field,
                                a.get('status'), STATUS_VALUES, 'Status')
            self._desc_ta.text = a.get('description') or ''
            self._check_days(a.get('reminder_days') or default_days)
            self._linked_note_ids = list(a.get('linked_note_ids') or [])
            self._resolve_link_titles()
            self._render_links()

        else:  # create
            self._check_days(default_days)

    def _check_days(self, days):
        for d, cb in self._day_checks.items():
            cb.checked = d in (days or [])

    def _build_payload(self):
        weight_text = (self._weight_tb.text if self._weight_tb.text is not None else '')
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
            'description': (self._desc_ta.text or '').strip() or None,
            'reminder_days': sorted(
                (d for d, cb in self._day_checks.items() if cb.checked), reverse=True),
            'linked_note_ids': list(self._linked_note_ids),
        }
        # Preserve the parser audit trail (FR17) on create/preview.
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
        """
        clear_field_errors(self._title_field, self._subject_field,
                           self._type_field, self._due_field, self._start_field,
                           self._weight_field, self._status_field)

        # Collected rather than reported one at a time, and in the order the fields
        # appear on screen: one Save then names every problem, and the cursor can
        # be sent to the first of them.
        problems = []

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

        if self._type_dd.selected_value not in TYPE_VALUES:
            problems.append((self._type_field, 'Choose a type.'))

        due_date = self._due_dp.date
        if due_date is None:
            problems.append((self._due_field, 'Due date is required.'))

        start_date = self._start_dp.date
        if start_date is not None and due_date is not None and start_date > due_date:
            # Both dates are fine on their own; only the pair is wrong.
            problems.append((
                self._start_field,
                'Start date cannot be after Due date. Check the two dates.'))

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

        for field_panel, message in problems:
            set_field_error(field_panel, message)
        if not problems:
            return True

        # Put the cursor in the first offending control so its message is on screen
        # even when the dialog is scrolled past it. No toast: the message belongs
        # beside the field (FR04), and repeating it in the corner would say the
        # same thing twice.
        try:
            problems[0][0].input_component.focus()
        except Exception:
            pass
        return False

    # --- handlers ----------------------------------------------------------
    def _on_save_click(self, **event_args):
        if not self._validate_fields():
            return
        try:
            payload = self._build_payload()
        except (ValueError, TypeError):
            # Unreachable while _validate_fields() runs first (it is what proves
            # the weight parses); kept so a later change there cannot put a raw
            # traceback in front of the student.
            set_field_error(self._weight_field, 'Weight must be a number.')
            return
        try:
            if self._mode == 'edit':
                anvil.server.call('update_assessment', self._assessment_id, payload)
                result_id = self._assessment_id
            else:
                result_id = anvil.server.call('create_assessment', payload)
        except Exception as e:
            toast_error(friendly_error(e))
            return
        toast("Assessment saved.")
        self.raise_event('x-close-alert', value=result_id)

    def _on_cancel_click(self, **event_args):
        # Cancel means "no new id" everywhere except bulk mode, where rows may
        # already have been committed before the student closed the dialog; the
        # count goes back so the dashboard still refreshes and shows them.
        self.raise_event('x-close-alert', value=self._bulk_inserted or None)

    # --- linked notes (FR12) -----------------------------------------------
    def _on_note_search(self, **event_args):
        query = (self._note_search_tb.text or '').strip()
        try:
            notes = anvil.server.call('search_notes', query=query or None)
        except Exception as e:
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
        for n in notes[:8]:
            self._note_titles[n['id']] = n.get('title') or '(untitled)'
            b = Button(text='+ %s' % self._note_titles[n['id']], role='ghost')
            # Default argument captures this loop's id; a bare closure would
            # give every button the last note's id.
            b.set_event_handler('click', lambda nid=n['id'], **e: self._add_link(nid))
            row.add_component(b)
        self._note_results.add_component(row)

    def _add_link(self, note_id):
        if note_id not in self._linked_note_ids:
            self._linked_note_ids.append(note_id)
        self._render_links()

    def _remove_link(self, note_id):
        self._linked_note_ids = [n for n in self._linked_note_ids if n != note_id]
        self._render_links()

    def _resolve_link_titles(self):
        """Populate the id->title cache for already-linked notes (edit mode)."""
        if not self._linked_note_ids:
            return
        try:
            for n in anvil.server.call('search_notes'):
                self._note_titles[n['id']] = n.get('title') or '(untitled)'
        except Exception:
            pass

    def _render_links(self):
        self._linked_pills.clear()
        for nid in self._linked_note_ids:
            # chip + its own remove button, so the pair reads as one token.
            pill = make_row(make_chip(self._note_titles.get(nid, nid), 'accent'))
            x = Button(text='x', role='iconbtn')
            x.set_event_handler('click', lambda i=nid, **e: self._remove_link(i))
            pill.add_component(x)
            self._linked_pills.add_component(pill)

    # --- bulk mode ---------------------------------------------------------
    def _build_bulk(self):
        """Paste-many UI: parse each line, tick the createable ones, insert them.

        Per line, not all-or-nothing: create_bulk_assessments commits every record
        that validates and reports the rest (FR02), so one bad line no longer
        discards a whole screen of correctly parsed assessments.
        """
        self.add_component(make_page_title(
            'Bulk add assessments',
            'Paste one assessment per line, then Parse all.'))
        # 160px ~= six pasted lines visible at once: enough for the student to
        # see and proof-read a whole paste without scrolling the box.
        self._bulk_ta = TextArea(
            placeholder='Methods SAC2 due Friday week 5 worth 25%\nPhysics exam 12/06 30%',
            height='160px')
        self.add_component(self._bulk_ta)

        parse_btn = Button(text='Parse all', role='primary')
        parse_btn.set_event_handler('click', self._on_bulk_parse_click)
        self.add_component(make_row(parse_btn))

        self._multi_panel = ColumnPanel()
        self.add_component(self._multi_panel)
        # One entry per parsed line: the parse itself (which carries the line
        # number), its tick box, the row the chips sit in, and the hidden label a
        # server rejection is written into.
        self._multi_rows = []   # [{'parsed', 'check', 'row', 'mark'}, ...]

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
        """
        if parsed.get('confidence') == 'LOW':
            return 'LOW confidence'

        f = parsed.get('fields', {})
        title = f.get('title')
        title = title.strip() if isinstance(title, str) else ''
        if not title:
            return 'needs a title'
        if len(title) > MAX_TITLE_LENGTH:
            return 'title too long'
        if f.get('subject') not in SUBJECTS:
            return 'needs a subject'
        if f.get('type') not in TYPE_VALUES:
            return 'needs a type'
        if f.get('due_date') is None:
            return 'needs a due date'

        weight = f.get('weight')
        if weight is not None:
            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                return 'weight must be a number'
            if not (MIN_WEIGHT <= weight <= MAX_WEIGHT):
                return 'weight must be %g-%g' % (MIN_WEIGHT, MAX_WEIGHT)
        return None

    def _createable(self, parsed):
        """True when the server would accept this line as parsed."""
        return self._blocking_reason(parsed) is None

    def _line_label(self, parsed):
        """'Line 7' — the line's position in what the student actually PASTED.

        parse_bulk carries this through as 'line_index' (0-based over the original
        paste, blank lines included). It has to be carried, because a rejection's
        'index' is a position in the list of TICKED records: the two stop agreeing
        the moment one line is left unticked, and reporting the wrong one sends the
        student to a line of their own paste that is perfectly fine.
        """
        line_index = parsed.get('line_index')
        if isinstance(line_index, int) and not isinstance(line_index, bool):
            return 'Line %d' % (line_index + 1)
        return 'One line'

    def _on_bulk_parse_click(self, **event_args):
        text = (self._bulk_ta.text or '').strip()
        if not text:
            toast_warn("Paste some lines first.")
            return
        try:
            results = anvil.server.call('parse_bulk', text)
        except Exception as e:
            toast_error(friendly_error(
                e, fallback="Couldn't read those lines. Try again."))
            return
        self._render_multi(results)

    def _render_multi(self, results):
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
            createable = self._createable(parsed)
            # One card per line so the tick, the confidence and the summary of
            # what will actually be created stay visually bound together.
            card = make_list_card()
            cb = CheckBox(checked=createable)
            summary = '%s — %s · %s · %s' % (
                f.get('title') or '(untitled)', f.get('subject') or '?',
                f.get('type') or '?', fmt_date(f.get('due_date')))
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
        """
        if not entry['check'].enabled:
            return
        entry['check'].checked = False
        entry['check'].enabled = False
        entry['row'].add_component(make_chip('added', 'ok'))

    def _mark_rejected(self, entry, reason):
        """Write the server's reason beside the line it belongs to; return its label.

        Left ticked on purpose: the line still has to go in once it is fixed.
        """
        label = self._line_label(entry['parsed'])
        entry['mark'].text = '%s: %s' % (label, reason)
        entry['mark'].visible = True
        return label

    def _on_bulk_create_click(self, **event_args):
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
        if not records:
            toast_warn("Nothing ticked to create.")
            return
        try:
            result = anvil.server.call('create_bulk_assessments', records)
        except Exception as e:
            toast_error(friendly_error(e))
            return

        inserted = result.get('inserted', 0)
        rejected = result.get('rejected') or []
        self._bulk_inserted += inserted

        # create_bulk_assessments commits per line (FR02), so 'rejected' can be
        # non-empty while rows were still written — the old "nothing was saved"
        # message would now be a lie. Its 'index' is the record's position in the
        # list this form sent, so every position it does NOT name went in.
        reasons = {}
        for rejection in rejected:
            position = rejection.get('index')
            if isinstance(position, int) and 0 <= position < len(submitted):
                reasons[position] = rejection.get('reason')
        # Only lock lines when every rejection could be placed. If one could not,
        # it is no longer knowable which lines were written, and locking the wrong
        # one would lose it.
        placed_all = len(reasons) == len(rejected)

        marked = []
        for position, entry in enumerate(submitted):
            if position in reasons:
                marked.append(self._mark_rejected(entry, reasons[position]))
            elif placed_all:
                self._mark_created(entry)

        if not rejected:
            toast("Created %d assessment(s)." % inserted)
            self.raise_event('x-close-alert', value=self._bulk_inserted)
            return

        # Both halves, honestly, and the dialog stays open so the lines that
        # failed can be fixed and sent again. Each reason is written beside its own
        # line above, so the toast only has to name them.
        if placed_all:
            detail = ('%s could not be added — the reason is beside each one.'
                      % ', '.join(marked))
        else:
            detail = ' '.join(str(r.get('reason')) for r in rejected)
        if inserted:
            toast_error('Added %d of %d. %s' % (inserted, len(records), detail))
        else:
            toast_error('Nothing was added. %s' % detail)
