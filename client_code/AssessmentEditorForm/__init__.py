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
  mode='bulk'    - paste many lines, parse them all, tick the createable ones
                   and insert them atomically (FR18).

ParserPreviewForm was dropped from the design; its preview-before-commit role is
this form in 'preview' mode. Save raises 'x-close-alert' so the parent alert()
returns the new/updated assessment id; Cancel returns None.

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
import datetime
from anvil import (
    ColumnPanel, Label, TextBox, TextArea, DropDown, DatePicker,
    CheckBox, Button,
)
from ..common import (
    get_session_settings, make_chip, make_divider, make_empty_state,
    make_field, make_list_card, make_row, make_section_header, make_toolbar,
    toast, toast_error,
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
TYPES = (
    ('SAC', 'sac'), ('SAT', 'sat'), ('Exam', 'exam'),
    ('Project', 'project'), ('Homework', 'homework'), ('Other', 'other'),
)
STATUSES = (
    ('Not started', 'not_started'), ('In progress', 'in_progress'),
    ('Completed', 'completed'),
)
REMINDER_DAY_OPTIONS = (14, 7, 3, 2, 1)

# Parser confidence -> chip TONE, not colour. The tone names are roles the
# stylesheet paints, so the badge re-tints itself in dark mode; an unknown
# confidence falls through to None, i.e. the neutral chip.
_CONF_TONE = {'HIGH': 'ok', 'MEDIUM': 'warn', 'LOW': 'bad'}

_MONTHS_ABBR = ('', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _from_iso(s):
    """'YYYY-MM-DD' -> date, or None (manual; avoids Skulpt isoformat quirks)."""
    if not s or not isinstance(s, str):
        return None
    parts = s.split('-')
    if len(parts) != 3:
        return None
    try:
        return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError):
        return None


def _fmt_date(d):
    """date -> 'DD Mon YYYY' via components (avoids Skulpt strftime gaps)."""
    if d is None:
        return 'no date'
    return '%02d %s %d' % (d.day, _MONTHS_ABBR[d.month], d.year)


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
            header.add_component(make_chip(conf, _CONF_TONE.get(conf)))
        self.add_component(header)

        # --- title ---
        self._title_tb = TextBox()
        self.add_component(make_field('Title', self._title_tb))

        # --- subject ---
        # The dropdown offers the student's locked subjects (spec §11) when
        # they've onboarded; out-of-list values (legacy rows, parser fallback
        # hits) are appended on load so nothing becomes uneditable.
        self._subject_dd = DropDown(items=self._subject_items(),
                                    include_placeholder=True)
        self.add_component(make_field('Subject', self._subject_dd,
                                      hint=self._why_text('subject')))

        # --- type ---
        self._type_dd = DropDown(items=list(TYPES))
        self.add_component(make_field('Type', self._type_dd,
                                      hint=self._why_text('type')))

        # --- due date ---
        self._due_dp = DatePicker(format='DD MMM YYYY')
        self.add_component(make_field('Due date', self._due_dp,
                                      hint=self._why_text('due_date')))

        # --- start date (optional) ---
        self._start_dp = DatePicker(format='DD MMM YYYY')
        self.add_component(make_field('Start date (optional)', self._start_dp))

        # --- weight ---
        self._weight_tb = TextBox(type='number')
        self.add_component(make_field('Weight (%)', self._weight_tb,
                                      hint=self._why_text('weight')))

        # --- status ---
        self._status_dd = DropDown(items=list(STATUSES))
        self._status_dd.selected_value = 'not_started'
        self.add_component(make_field('Status', self._status_dd))

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
        self._note_results = ColumnPanel(spacing_above='none', spacing_below='none')
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
                self._type_dd.selected_value = f.get('type')
            self._due_dp.date = f.get('due_date')
            if f.get('weight') is not None:
                self._weight_tb.text = ('%g' % f.get('weight'))
            self._check_days(default_days)

        elif self._mode == 'edit' and self._assessment_id:
            try:
                a = anvil.server.call('get_assessment', self._assessment_id)
            except Exception as e:
                toast_error("Couldn't load assessment: %s" % e)
                return
            self._title_tb.text = a.get('title') or ''
            self._select_subject(a.get('subject'))
            self._type_dd.selected_value = a.get('type')
            self._due_dp.date = _from_iso(a.get('due_date'))
            self._start_dp.date = _from_iso(a.get('start_date'))
            if a.get('weight') is not None:
                self._weight_tb.text = ('%g' % a.get('weight'))
            self._status_dd.selected_value = a.get('status') or 'not_started'
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
            'status': self._status_dd.selected_value or 'not_started',
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

    # --- handlers ----------------------------------------------------------
    def _on_save_click(self, **event_args):
        try:
            payload = self._build_payload()
        except (ValueError, TypeError):
            toast_error("Weight must be a number.")
            return
        try:
            if self._mode == 'edit':
                anvil.server.call('update_assessment', self._assessment_id, payload)
                result_id = self._assessment_id
            else:
                result_id = anvil.server.call('create_assessment', payload)
        except Exception as e:
            toast_error(str(e))
            return
        toast("Assessment saved.")
        self.raise_event('x-close-alert', value=result_id)

    def _on_cancel_click(self, **event_args):
        self.raise_event('x-close-alert', value=None)

    # --- linked notes (FR12) -----------------------------------------------
    def _on_note_search(self, **event_args):
        query = (self._note_search_tb.text or '').strip()
        try:
            notes = anvil.server.call('search_notes', query=query or None)
        except Exception as e:
            toast_error("Couldn't search notes: %s" % e)
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
        """Paste-many UI: parse each line, tick the createable ones, insert atomically."""
        self.add_component(Label(text='Bulk add assessments', role='pagetitle'))
        self.add_component(Label(text='Paste one assessment per line, then Parse all.',
                                 role='caption'))
        self._bulk_ta = TextArea(
            placeholder='Methods SAC2 due Friday week 5 worth 25%\nPhysics exam 12/06 30%',
            height='160px')
        self.add_component(self._bulk_ta)

        parse_btn = Button(text='Parse all', role='primary')
        parse_btn.set_event_handler('click', self._on_bulk_parse_click)
        self.add_component(make_row(parse_btn))

        self._multi_panel = ColumnPanel(spacing_above='none', spacing_below='none')
        self.add_component(self._multi_panel)
        self._multi_rows = []   # [(parsed, checkbox), ...]

        self.add_component(make_divider())
        cancel_btn = Button(text='Cancel', role='secondary')
        cancel_btn.set_event_handler('click', self._on_cancel_click)
        create_btn = Button(text='Create selected', role='primary')
        create_btn.set_event_handler('click', self._on_bulk_create_click)
        self.add_component(make_row(cancel_btn, create_btn))

    def _createable(self, parsed):
        """A parsed line can auto-create only with a valid subject and a due date."""
        f = parsed.get('fields', {})
        return (parsed.get('confidence') != 'LOW'
                and f.get('subject') in SUBJECTS
                and f.get('due_date') is not None)

    def _on_bulk_parse_click(self, **event_args):
        text = (self._bulk_ta.text or '').strip()
        if not text:
            toast("Paste some lines first.", style='warning')
            return
        try:
            results = anvil.server.call('parse_bulk', text)
        except Exception as e:
            toast_error("Couldn't parse: %s" % e)
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
                f.get('type') or '?', _fmt_date(f.get('due_date')))
            if f.get('weight') is not None:
                summary += ' · %g%%' % f.get('weight')
            row = make_row(cb, make_chip(conf, _CONF_TONE.get(conf)),
                           Label(text=summary))
            if not createable:
                reason = 'LOW confidence' if conf == 'LOW' else 'needs subject + due date'
                row.add_component(Label(text='(%s — unticked)' % reason,
                                        role=['micro', 't-bad']))
            card.add_component(row)
            self._multi_panel.add_component(card)
            self._multi_rows.append((parsed, cb))

    def _on_bulk_create_click(self, **event_args):
        records = []
        for parsed, cb in self._multi_rows:
            if not cb.checked:
                continue
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
        if not records:
            toast("Nothing ticked to create.", style='warning')
            return
        try:
            result = anvil.server.call('create_bulk_assessments', records)
        except Exception as e:
            toast_error(str(e))
            return
        if result.get('rejected'):
            msgs = ', '.join('line %d: %s' % (r['index'] + 1, r['reason'])
                             for r in result['rejected'])
            toast_error("Some lines were invalid — nothing was saved. %s" % msgs)
            return
        toast("Created %d assessment(s)." % result.get('inserted', 0))
        self.raise_event('x-close-alert', value=result.get('inserted', 0))
