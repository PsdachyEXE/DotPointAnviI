import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""DashboardForm - landing screen: NLP input + assessment list (FR01, FR07, FR09, FR21).

This slice implements the parse->preview->save->see loop end-to-end:
  - an NLP input bar that calls parse_text and opens AssessmentEditorForm
    (mode='preview') as an alert,
  - a "+ Add manually" entry to AssessmentEditorForm(mode='create'),
  - a list of the user's assessments (list_assessments) with urgency colour
    (FR21) + days-remaining number (FR09), and per-row Edit / Delete.

The calendar grid, filter row and upcoming sidebar (the full three-panel layout)
land in the dashboard slice (spec section 10 step 3, get_dashboard_data). Kept
minimal here so the vertical slice is runnable and reviewable first.

See IMPLEMENTATION_SPEC.md section 3 (DashboardForm).
"""

import anvil
import anvil.server
from anvil import (
    ColumnPanel, FlowPanel, Label, TextBox, Button, Notification, Spacer,
    alert, confirm,
)

from ..common import make_top_bar

# Mirror of _constants.URGENCY_COLOURS (client cannot import server modules).
_URGENCY_COLOURS = {
    'overdue': '#d64550', 'today': '#e8833a', 'soon': '#3b7dd8', 'distant': '#9aa0a6',
}
_TYPE_LABELS = {'sac': 'SAC', 'sat': 'SAT', 'exam': 'Exam', 'project': 'Project',
                'homework': 'Homework', 'other': 'Other'}
_STATUS_LABELS = {'not_started': 'Not started', 'in_progress': 'In progress',
                  'completed': 'Completed'}


class DashboardForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self.add_component(make_top_bar())

        body = ColumnPanel()
        self.add_component(body)

        # --- NLP input bar ---
        bar = FlowPanel()
        self._nlp_tb = TextBox(
            placeholder='Type an assessment, e.g. "Methods SAC2 due Friday week 5 worth 25%"')
        self._nlp_tb.set_event_handler('pressed_enter', self._on_parse_click)
        bar.add_component(self._nlp_tb)
        parse_btn = Button(text='Parse', role='primary')
        parse_btn.set_event_handler('click', self._on_parse_click)
        bar.add_component(parse_btn)
        add_btn = Button(text='+ Add manually', role='secondary')
        add_btn.set_event_handler('click', self._on_add_click)
        bar.add_component(add_btn)
        body.add_component(bar)

        body.add_component(Spacer(height=8))
        body.add_component(Label(text='Your assessments', font_size=18, bold=True))

        # --- list panel (populated by _refresh) ---
        self._list_panel = ColumnPanel()
        body.add_component(self._list_panel)

        self._refresh()

    # --- data --------------------------------------------------------------
    def _refresh(self):
        self._list_panel.clear()
        try:
            rows = anvil.server.call('list_assessments')
        except Exception as e:
            self._list_panel.add_component(
                Label(text="Couldn't load assessments: %s" % e, foreground='#d64550'))
            return
        if not rows:
            self._list_panel.add_component(
                Label(text='No assessments yet — parse a sentence or add one manually.',
                      foreground='#9aa0a6', italic=True))
            return
        for a in rows:
            self._list_panel.add_component(self._make_card(a))

    def _make_card(self, a):
        card = FlowPanel(spacing_above='small', spacing_below='small')

        band = a.get('urgency_band', 'distant')
        dot = Label(text=' ', background=_URGENCY_COLOURS.get(band, '#9aa0a6'))
        card.add_component(dot)

        title = a.get('title') or '(untitled)'
        subject = a.get('subject') or ''
        meta = '%s · %s' % (subject, _TYPE_LABELS.get(a.get('type'), a.get('type') or ''))
        card.add_component(Label(text=title, bold=True))
        card.add_component(Label(text=meta, foreground='#9aa0a6'))

        due = a.get('due_display') or 'no date'
        days = a.get('days_remaining')
        if days is None:
            due_text = due
        elif days < 0:
            due_text = '%s (%d days overdue)' % (due, -days)
        elif days == 0:
            due_text = '%s (today)' % due
        else:
            due_text = '%s (in %d days)' % (due, days)
        card.add_component(Label(text=due_text))

        if a.get('weight') is not None:
            card.add_component(Label(text='%g%%' % a.get('weight'), foreground='#9aa0a6'))
        card.add_component(Label(text=_STATUS_LABELS.get(a.get('status'), ''),
                                 foreground='#9aa0a6'))

        edit_btn = Button(text='Edit', role='secondary')
        edit_btn.set_event_handler('click',
                                   lambda aid=a['id'], **e: self._on_edit_click(aid))
        card.add_component(edit_btn)
        del_btn = Button(text='Delete', role='secondary')
        del_btn.set_event_handler('click',
                                  lambda aid=a['id'], **e: self._on_delete_click(aid))
        card.add_component(del_btn)
        return card

    # --- handlers ----------------------------------------------------------
    def _on_parse_click(self, **event_args):
        text = (self._nlp_tb.text or '').strip()
        if not text:
            Notification("Type an assessment first.", style='warning').show()
            return
        try:
            parsed = anvil.server.call('parse_text', text)
        except Exception as e:
            Notification("Couldn't parse: %s" % e, style='danger').show()
            return
        from ..AssessmentEditorForm import AssessmentEditorForm
        editor = AssessmentEditorForm(mode='preview', prefill=parsed)
        result = alert(editor, title='', large=True, buttons=[])
        if result:
            self._nlp_tb.text = ''
            self._refresh()

    def _on_add_click(self, **event_args):
        from ..AssessmentEditorForm import AssessmentEditorForm
        editor = AssessmentEditorForm(mode='create')
        result = alert(editor, title='', large=True, buttons=[])
        if result:
            self._refresh()

    def _on_edit_click(self, assessment_id):
        from ..AssessmentEditorForm import AssessmentEditorForm
        editor = AssessmentEditorForm(mode='edit', assessment_id=assessment_id)
        result = alert(editor, title='', large=True, buttons=[])
        if result:
            self._refresh()

    def _on_delete_click(self, assessment_id):
        if not confirm('Delete this assessment?'):
            return
        try:
            anvil.server.call('delete_assessment', assessment_id)
        except Exception as e:
            Notification("Couldn't delete: %s" % e, style='danger').show()
            return
        self._refresh()
