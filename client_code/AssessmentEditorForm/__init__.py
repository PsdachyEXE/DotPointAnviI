import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""AssessmentEditorForm - create / edit / parser-preview a single assessment.

Opened as an alert(..., large=True) from DashboardForm. One form, three modes
via the `mode` constructor arg (spec section 3):
  mode='create'  - blank manual entry (FR03).
  mode='edit'    - load an existing assessment by id and save changes (FR04).
  mode='preview' - prefill from an nlp.parse_text() result dict; show the
                   confidence badge and per-field 'why' provenance (FR17).

ParserPreviewForm was dropped from the design; its preview-before-commit role is
this form in 'preview' mode. Save raises 'x-close' so the parent alert() returns
the new/updated assessment id; Cancel returns None. (bulk mode and the linked-
notes manager land in later slices - spec section 10 steps 5 & 7.)

See IMPLEMENTATION_SPEC.md section 3 (AssessmentEditorForm).
"""

import anvil
import anvil.server
import datetime
from anvil import (
    ColumnPanel, FlowPanel, Label, TextBox, TextArea, DropDown, DatePicker,
    CheckBox, Button, Notification,
)

# Canonical subjects (mirror of set(_constants.SUBJECT_ALIASES.values()); the
# client cannot import server modules, so the list is duplicated here).
SUBJECTS = (
    'Mathematics', 'Mathematical Methods', 'Specialist Mathematics',
    'Further Mathematics', 'English', 'Chemistry', 'Biology', 'Physics',
    'Software Development', 'Geography', 'Physical Education',
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

_CONF_COLOUR = {'HIGH': '#2e7d32', 'MEDIUM': '#e8833a', 'LOW': '#d64550'}


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


class AssessmentEditorForm(ColumnPanel):
    def __init__(self, mode='create', assessment_id=None, prefill=None, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self._mode = mode
        self._assessment_id = assessment_id
        self._prefill = prefill

        # --- header (+ confidence badge in preview mode) ---
        header = FlowPanel()
        titles = {'create': 'Add assessment', 'edit': 'Edit assessment',
                  'preview': 'Confirm parsed assessment'}
        header.add_component(Label(text=titles.get(mode, 'Assessment'),
                                   font_size=20, bold=True))
        if mode == 'preview' and prefill:
            conf = prefill.get('confidence', 'LOW')
            badge = Label(text='  %s  ' % conf, bold=True,
                          foreground='#ffffff', background=_CONF_COLOUR.get(conf, '#9aa0a6'))
            header.add_component(badge)
        self.add_component(header)

        why = (prefill or {}).get('why', {}) if mode == 'preview' else {}
        body = ColumnPanel()
        self.add_component(body)

        # --- title ---
        body.add_component(Label(text='Title'))
        self._title_tb = TextBox()
        body.add_component(self._title_tb)

        # --- subject ---
        body.add_component(Label(text='Subject'))
        self._subject_dd = DropDown(items=list(SUBJECTS), include_placeholder=True)
        body.add_component(self._subject_dd)
        self._add_why(body, why, 'subject')

        # --- type ---
        body.add_component(Label(text='Type'))
        self._type_dd = DropDown(items=list(TYPES))
        body.add_component(self._type_dd)
        self._add_why(body, why, 'type')

        # --- due date ---
        body.add_component(Label(text='Due date'))
        self._due_dp = DatePicker(format='DD MMM YYYY')
        body.add_component(self._due_dp)
        self._add_why(body, why, 'due_date')

        # --- start date (optional) ---
        body.add_component(Label(text='Start date (optional)'))
        self._start_dp = DatePicker(format='DD MMM YYYY')
        body.add_component(self._start_dp)

        # --- weight ---
        body.add_component(Label(text='Weight (%)'))
        self._weight_tb = TextBox(type='number')
        body.add_component(self._weight_tb)
        self._add_why(body, why, 'weight')

        # --- status ---
        body.add_component(Label(text='Status'))
        self._status_dd = DropDown(items=list(STATUSES))
        self._status_dd.selected_value = 'not_started'
        body.add_component(self._status_dd)

        # --- reminder pills ---
        body.add_component(Label(text='Remind me (days before due)'))
        self._day_checks = {}
        pills = FlowPanel()
        for d in REMINDER_DAY_OPTIONS:
            cb = CheckBox(text='%d' % d)
            self._day_checks[d] = cb
            pills.add_component(cb)
        body.add_component(pills)

        # --- description ---
        body.add_component(Label(text='Description (optional)'))
        self._desc_ta = TextArea()
        body.add_component(self._desc_ta)

        # --- footer ---
        footer = FlowPanel()
        cancel_btn = Button(text='Cancel', role='secondary')
        cancel_btn.set_event_handler('click', self._on_cancel_click)
        footer.add_component(cancel_btn)
        save_btn = Button(text='Save', role='primary')
        save_btn.set_event_handler('click', self._on_save_click)
        footer.add_component(save_btn)
        self.add_component(footer)

        self._load()

    # --- helpers -----------------------------------------------------------
    def _add_why(self, parent, why, key):
        """In preview mode, show the parser's provenance string under a field."""
        if why and key in why:
            parent.add_component(Label(text=why[key], font_size=11,
                                       foreground='#9aa0a6', italic=True))

    def _load(self):
        """Populate fields from prefill (preview) or the server (edit)."""
        default_days = [7, 2]
        try:
            settings = anvil.server.call('get_settings')
            default_days = settings.get('default_reminder_days') or [7, 2]
        except Exception:
            pass

        if self._mode == 'preview' and self._prefill:
            f = self._prefill.get('fields', {})
            self._title_tb.text = f.get('title') or ''
            if f.get('subject') in SUBJECTS:
                self._subject_dd.selected_value = f.get('subject')
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
                Notification("Couldn't load assessment: %s" % e, style='danger').show()
                return
            self._title_tb.text = a.get('title') or ''
            self._subject_dd.selected_value = a.get('subject')
            self._type_dd.selected_value = a.get('type')
            self._due_dp.date = _from_iso(a.get('due_date'))
            self._start_dp.date = _from_iso(a.get('start_date'))
            if a.get('weight') is not None:
                self._weight_tb.text = ('%g' % a.get('weight'))
            self._status_dd.selected_value = a.get('status') or 'not_started'
            self._desc_ta.text = a.get('description') or ''
            self._check_days(a.get('reminder_days') or default_days)

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
            Notification("Weight must be a number.", style='danger').show()
            return
        try:
            if self._mode == 'edit':
                anvil.server.call('update_assessment', self._assessment_id, payload)
                result_id = self._assessment_id
            else:
                result_id = anvil.server.call('create_assessment', payload)
        except Exception as e:
            Notification(str(e), style='danger').show()
            return
        Notification("Assessment saved.", style='success').show()
        self.raise_event('x-close', value=result_id)

    def _on_cancel_click(self, **event_args):
        self.raise_event('x-close', value=None)
