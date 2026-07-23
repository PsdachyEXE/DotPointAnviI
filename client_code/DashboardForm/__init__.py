import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""DashboardForm - the all-in-view dashboard (FR06, FR07, FR08, FR09, FR21).

Three panels populated by a single get_dashboard_data() round-trip (NFR01):
  - assessment list (filtered/sorted) with urgency colour + days-remaining,
  - month calendar grid with per-day urgency colour + a month navigator,
  - "upcoming" 30-day sidebar.
Plus the NLP input bar (parse -> preview -> save), Bulk add, a filter row
(subject / status / type / show-completed), a sort control (FR07), a
school-terms hint banner (FR15 discoverability), inline card status changes
(EC-UX-05), and clickable calendar days that pop up that day's assessments.

See IMPLEMENTATION_SPEC.md section 3 (DashboardForm).
"""

import anvil
import anvil.server
from anvil import (
    ColumnPanel, FlowPanel, GridPanel, Label, Link, TextBox, Button, CheckBox,
    DropDown, Notification, Spacer, alert, confirm,
)

from ..common import make_top_bar

# Mirror of _constants.URGENCY_COLOURS (client cannot import server modules).
_URGENCY_COLOURS = {
    'overdue': '#d64550', 'today': '#e8833a', 'soon': '#3b7dd8', 'distant': '#9aa0a6',
}
_TYPES = (('SAC', 'sac'), ('SAT', 'sat'), ('Exam', 'exam'),
          ('Project', 'project'), ('Homework', 'homework'), ('Other', 'other'))
_STATUSES = (('Not started', 'not_started'), ('In progress', 'in_progress'),
             ('Completed', 'completed'))
_TYPE_LABELS = dict((v, k) for k, v in _TYPES)
_STATUS_LABELS = dict((v, k) for k, v in _STATUSES)
_SORTS = (('Due date', 'due_date'), ('Weight', 'weight'), ('Subject', 'subject'))
_CONF_COLOUR = {'HIGH': '#2e7d32', 'MEDIUM': '#e8833a', 'LOW': '#d64550'}
_WEEKDAY_HEADERS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
_MONTH_NAMES = ('', 'January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December')


def _cell(dct, day):
    """Look up a day key tolerant of int- or str-keyed dicts (Anvil transport)."""
    if dct is None:
        return None
    if day in dct:
        return dct[day]
    return dct.get(str(day))


class DashboardForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self._current_month = None   # 'YYYY-MM'; None -> server uses today's month

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
        bulk_btn = Button(text='Bulk add', role='secondary')
        bulk_btn.set_event_handler('click', self._on_bulk_click)
        bar.add_component(bulk_btn)
        body.add_component(bar)

        # --- filter row ---
        filters = FlowPanel()
        filters.add_component(Label(text='Filter:'))
        self._subject_dd = DropDown(items=['All'])
        self._subject_dd.set_event_handler('change', self._on_filter_change)
        filters.add_component(self._subject_dd)
        self._status_dd = DropDown(items=[('All status', '')] + list(_STATUSES))
        self._status_dd.set_event_handler('change', self._on_filter_change)
        filters.add_component(self._status_dd)
        self._type_dd = DropDown(items=[('All types', '')] + list(_TYPES))
        self._type_dd.set_event_handler('change', self._on_filter_change)
        filters.add_component(self._type_dd)
        self._show_completed_cb = CheckBox(text='Show completed')
        self._show_completed_cb.set_event_handler('change', self._on_filter_change)
        filters.add_component(self._show_completed_cb)
        filters.add_component(Label(text='Sort:'))
        self._sort_dd = DropDown(items=list(_SORTS))
        self._sort_dd.selected_value = 'due_date'
        self._sort_dd.set_event_handler('change', self._on_filter_change)
        filters.add_component(self._sort_dd)
        body.add_component(filters)

        # Hint banner slot (shown while school terms are unconfigured, FR15).
        self._hint_panel = ColumnPanel()
        body.add_component(self._hint_panel)

        # Next-exam countdown chip slot (spec §13).
        self._exam_chip_panel = ColumnPanel()
        body.add_component(self._exam_chip_panel)

        body.add_component(Spacer(height=8))

        # --- three-panel body (list | calendar | upcoming) ---
        grid = GridPanel()
        self._list_panel = ColumnPanel()
        self._calendar_panel = ColumnPanel()
        self._upcoming_panel = ColumnPanel()
        grid.add_component(self._list_panel, row='main', col_xs=0, width_xs=5)
        grid.add_component(self._calendar_panel, row='main', col_xs=5, width_xs=4)
        grid.add_component(self._upcoming_panel, row='main', col_xs=9, width_xs=3)
        body.add_component(grid)

        self._refresh()

    # --- data --------------------------------------------------------------
    def _build_filters(self):
        f = {'show_completed': bool(self._show_completed_cb.checked)}
        subj = self._subject_dd.selected_value
        if subj and subj != 'All':
            f['subjects'] = [subj]
        stat = self._status_dd.selected_value
        if stat:
            f['statuses'] = [stat]
        typ = self._type_dd.selected_value
        if typ:
            f['types'] = [typ]
        return f

    def _refresh(self):
        try:
            data = anvil.server.call('get_dashboard_data',
                                     month=self._current_month,
                                     filters=self._build_filters(),
                                     sort={'by': self._sort_dd.selected_value or 'due_date'})
        except Exception as e:
            self._list_panel.clear()
            self._list_panel.add_component(
                Label(text="Couldn't load dashboard: %s" % e, foreground='#d64550'))
            return
        self._populate_subjects(data.get('subjects', []))
        self._render_hint(data.get('settings', {}))
        self._render_exam_chip(data.get('next_exam'))
        self._render_list(data.get('assessment_list', []))
        self._render_calendar(data.get('calendar', {}))
        self._render_upcoming(data.get('upcoming', []))

    def _render_hint(self, settings):
        """FR15 discoverability: nudge until school terms are configured."""
        self._hint_panel.clear()
        if settings.get('school_terms'):
            return
        hint = FlowPanel(role='card')
        hint.add_component(Label(text='Tip:', bold=True, foreground='#2f6fd0'))
        hint.add_component(Label(
            text='set your school terms in Settings so dates like "term 3 week 5" resolve automatically.'))
        go = Link(text='Open Settings', foreground='#2f6fd0')
        go.set_event_handler('click', lambda **e: self._go_settings())
        hint.add_component(go)
        self._hint_panel.add_component(hint)

    def _go_settings(self):
        from ..common import _navigate
        _navigate('settings')

    def _render_exam_chip(self, next_exam):
        """Countdown chip for the next VCE written exam (spec §13)."""
        self._exam_chip_panel.clear()
        if not next_exam:
            return
        days = next_exam.get('days_remaining')
        chip = FlowPanel(role='card')
        chip.add_component(Label(text='▲', foreground='#7c3aed', bold=True))
        chip.add_component(Label(
            text='Next exam: %s (%s)' % (next_exam.get('subject'),
                                         next_exam.get('paper')), bold=True))
        if days == 0:
            when = 'TODAY'
        elif days == 1:
            when = 'tomorrow'
        else:
            when = 'in %d days' % days
        chip.add_component(Label(text=when, bold=True, foreground='#7c3aed'))
        go = Link(text='Exam timetable', foreground='#2f6fd0')
        go.set_event_handler('click', lambda **e: self._go_exams())
        chip.add_component(go)
        self._exam_chip_panel.add_component(chip)

    def _go_exams(self):
        from ..common import _navigate
        _navigate('exams')

    def _populate_subjects(self, subjects):
        current = self._subject_dd.selected_value
        items = ['All'] + list(subjects)
        self._subject_dd.items = items
        self._subject_dd.selected_value = current if current in items else 'All'

    # --- list panel --------------------------------------------------------
    def _render_list(self, rows):
        self._list_panel.clear()
        self._list_panel.add_component(Label(text='Your assessments', font_size=18, bold=True))
        if not rows:
            self._list_panel.add_component(
                Label(text='No assessments match — parse a sentence or add one manually.',
                      foreground='#9aa0a6', italic=True))
            return
        for a in rows:
            self._list_panel.add_component(self._make_card(a))

    def _make_card(self, a):
        card = ColumnPanel(spacing_above='small', spacing_below='small', role='card')
        band = a.get('urgency_band', 'distant')

        # Row 1: urgency dot, title, subject/type chips, parser confidence badge.
        top = FlowPanel()
        top.add_component(Label(text='●', foreground=_URGENCY_COLOURS.get(band, '#9aa0a6'),
                                bold=True))
        top.add_component(Label(text=a.get('title') or '(untitled)', bold=True, font_size=15))
        if a.get('subject'):
            top.add_component(Label(text=a['subject'], role='chip'))
        type_label = _TYPE_LABELS.get(a.get('type'), a.get('type') or '')
        if type_label:
            top.add_component(Label(text=type_label, role='chip'))
        conf = a.get('confidence')
        if conf:
            top.add_component(Label(text='parsed · %s' % conf, font_size=10, italic=True,
                                    foreground=_CONF_COLOUR.get(conf, '#9aa0a6')))
        card.add_component(top)

        # Row 2: due text, weight, inline status dropdown (EC-UX-05), actions.
        bottom = FlowPanel()
        bottom.add_component(Label(text=self._due_text(a),
                                   foreground=_URGENCY_COLOURS.get(band, '#9aa0a6')))
        if a.get('weight') is not None:
            bottom.add_component(Label(text='· %g%% of grade' % a.get('weight'),
                                       foreground='#9aa0a6'))
        status_dd = DropDown(items=list(_STATUSES))
        status_dd.selected_value = a.get('status') or 'not_started'
        status_dd.set_event_handler(
            'change', lambda aid=a['id'], dd=status_dd, **e:
            self._on_card_status_change(aid, dd.selected_value))
        bottom.add_component(status_dd)
        edit_btn = Button(text='Edit', role='secondary')
        edit_btn.set_event_handler('click', lambda aid=a['id'], **e: self._on_edit_click(aid))
        bottom.add_component(edit_btn)
        del_btn = Button(text='Delete', role='secondary')
        del_btn.set_event_handler('click', lambda aid=a['id'], **e: self._on_delete_click(aid))
        bottom.add_component(del_btn)
        card.add_component(bottom)
        return card

    def _on_card_status_change(self, assessment_id, new_status):
        """Single-action status change from the card (EC-UX-05)."""
        if not new_status:
            return
        try:
            anvil.server.call('update_assessment', assessment_id, {'status': new_status})
        except Exception as e:
            Notification("Couldn't update status: %s" % e, style='danger', timeout=4).show()
        self._refresh()

    def _due_text(self, a):
        due = a.get('due_display') or 'no date'
        days = a.get('days_remaining')
        if days is None:
            return due
        if days < 0:
            return '%s (%d day%s overdue)' % (due, -days, 's' if days != -1 else '')
        if days == 0:
            return '%s (today)' % due
        return '%s (in %d day%s)' % (due, days, 's' if days != 1 else '')

    # --- calendar panel ----------------------------------------------------
    def _render_calendar(self, cal):
        self._calendar_panel.clear()
        year = cal.get('year')
        month = cal.get('month')
        weeks = cal.get('weeks') or []
        colours = cal.get('cell_colours') or {}
        self._day_buckets = cal.get('day_buckets') or {}
        self._exam_days = cal.get('exam_days') or {}

        nav = FlowPanel()
        prev_btn = Button(text='◀', role='secondary')
        prev_btn.set_event_handler('click', lambda **e: self._change_month(-1))
        nav.add_component(prev_btn)
        label = '%s %s' % (_MONTH_NAMES[month] if month else '', year or '')
        nav.add_component(Label(text=label.strip(), bold=True))
        next_btn = Button(text='▶', role='secondary')
        next_btn.set_event_handler('click', lambda **e: self._change_month(1))
        nav.add_component(next_btn)
        self._calendar_panel.add_component(nav)
        # Remember the displayed month for prev/next arithmetic.
        self._displayed_year, self._displayed_month = year, month

        header = GridPanel()
        for i, name in enumerate(_WEEKDAY_HEADERS):
            header.add_component(Label(text=name, bold=True, font_size=11),
                                 row='h', col_xs=i * 12 // 7, width_xs=12 // 7 or 1)
        self._calendar_panel.add_component(header)

        for w, week in enumerate(weeks):
            row = GridPanel()
            for i, day in enumerate(week):
                col = i * 12 // 7
                if day == 0:
                    cell = Label(text=' ')
                else:
                    band = _cell(colours, day)
                    has_exam = bool(_cell(self._exam_days, day))
                    if band or has_exam:
                        # Coloured bold day number (white-on-background renders
                        # unreliably in this theme). Clickable: opens the day's
                        # assessments/exams popup. '▲' marks a VCE exam day
                        # (spec §13); exam-only days show purple.
                        text = ('● %d' % day) if band else str(day)
                        if has_exam:
                            text += ' ▲'
                        colour = (_URGENCY_COLOURS.get(band, '#9aa0a6')
                                  if band else '#7c3aed')
                        cell = Link(text=text, bold=True, foreground=colour)
                        cell.set_event_handler(
                            'click', lambda d=day, **e: self._on_day_click(d))
                    else:
                        cell = Label(text=str(day), foreground='#9aa0a6')
                row.add_component(cell, row='w%d' % w, col_xs=col, width_xs=12 // 7 or 1)
            self._calendar_panel.add_component(row)

    def _on_day_click(self, day):
        """Show the clicked calendar day's assessments and VCE exams."""
        items = _cell(getattr(self, '_day_buckets', {}), day) or []
        exams = _cell(getattr(self, '_exam_days', {}), day) or []
        panel = ColumnPanel()
        if not items and not exams:
            panel.add_component(Label(text='Nothing due this day.'))
        for label in exams:
            row = FlowPanel()
            row.add_component(Label(text='▲', foreground='#7c3aed', bold=True))
            row.add_component(Label(text='VCE exam: %s' % label, bold=True,
                                    foreground='#7c3aed'))
            panel.add_component(row)
        for a in items:
            row = FlowPanel()
            band = a.get('urgency_band', 'distant')
            row.add_component(Label(text='●', foreground=_URGENCY_COLOURS.get(band, '#9aa0a6')))
            row.add_component(Label(text=a.get('title') or '(untitled)', bold=True))
            row.add_component(Label(text='%s · %s' % (
                a.get('subject') or '', _TYPE_LABELS.get(a.get('type'), '')),
                foreground='#9aa0a6'))
            panel.add_component(row)
        title = (items[0].get('due_display') if items else '') or 'This day'
        alert(panel, title=title, large=False, buttons=[('Close', None)])

    def _change_month(self, delta):
        y = getattr(self, '_displayed_year', None)
        m = getattr(self, '_displayed_month', None)
        if y is None or m is None:
            return
        m += delta
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        self._current_month = '%04d-%02d' % (y, m)
        self._refresh()

    # --- upcoming panel ----------------------------------------------------
    def _render_upcoming(self, upcoming):
        self._upcoming_panel.clear()
        self._upcoming_panel.add_component(Label(text='Upcoming (30 days)', font_size=16, bold=True))
        if not upcoming:
            self._upcoming_panel.add_component(
                Label(text='Nothing due in the next 30 days.', foreground='#9aa0a6', italic=True))
            return
        for a in upcoming:
            row = FlowPanel()
            band = a.get('urgency_band', 'distant')
            row.add_component(Label(text=' ', background=_URGENCY_COLOURS.get(band, '#9aa0a6')))
            row.add_component(Label(text=a.get('due_display') or '', font_size=11, bold=True))
            row.add_component(Label(text=a.get('title') or '(untitled)', font_size=12))
            self._upcoming_panel.add_component(row)

    # --- handlers ----------------------------------------------------------
    def _on_filter_change(self, **event_args):
        self._refresh()

    def _on_parse_click(self, **event_args):
        text = (self._nlp_tb.text or '').strip()
        if not text:
            Notification("Type an assessment first.", style='warning', timeout=4).show()
            return
        try:
            parsed = anvil.server.call('parse_text', text)
        except Exception as e:
            Notification("Couldn't parse: %s" % e, style='danger', timeout=4).show()
            return
        from ..AssessmentEditorForm import AssessmentEditorForm
        result = alert(AssessmentEditorForm(mode='preview', prefill=parsed),
                       title='', large=True, buttons=[])
        if result:
            self._nlp_tb.text = ''
            self._refresh()

    def _on_add_click(self, **event_args):
        from ..AssessmentEditorForm import AssessmentEditorForm
        result = alert(AssessmentEditorForm(mode='create'), title='', large=True, buttons=[])
        if result:
            self._refresh()

    def _on_bulk_click(self, **event_args):
        from ..AssessmentEditorForm import AssessmentEditorForm
        result = alert(AssessmentEditorForm(mode='bulk'), title='', large=True, buttons=[])
        if result:
            self._refresh()

    def _on_edit_click(self, assessment_id):
        from ..AssessmentEditorForm import AssessmentEditorForm
        result = alert(AssessmentEditorForm(mode='edit', assessment_id=assessment_id),
                       title='', large=True, buttons=[])
        if result:
            self._refresh()

    def _on_delete_click(self, assessment_id):
        if not confirm('Delete this assessment?'):
            return
        try:
            anvil.server.call('delete_assessment', assessment_id)
        except Exception as e:
            Notification("Couldn't delete: %s" % e, style='danger', timeout=4).show()
            return
        self._refresh()
