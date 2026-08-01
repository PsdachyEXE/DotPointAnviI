import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""DashboardForm - the all-in-view dashboard (FR06, FR07, FR08, FR09, FR21).

Three panels populated by a single get_dashboard_data() round-trip (NFR01):
  - assessment list (filtered/sorted) with an urgency-coloured edge and
    days-remaining,
  - a month calendar grid with per-day urgency tints and VCE exam markers,
  - an "upcoming" 30-day sidebar.

Reading order down the page is deliberate (spec §14): the natural-language
input bar is the hero, because typing a sentence is the app's whole premise;
then the filters that scope what follows; then any banner the student needs to
act on (school terms unset / next exam); then the three panels.

The calendar is a single FlowPanel with role='calgrid'. The stylesheet turns
its inner gutter into a 7-column CSS grid, so 42 day cells laid out in one flat
list wrap into weeks by themselves. The previous implementation used a
Bootstrap GridPanel per week, which cannot divide 12 columns evenly by 7 and so
rendered visibly crooked.

See IMPLEMENTATION_SPEC.md section 3 (DashboardForm) and section 14.
"""

import anvil
import anvil.server
import datetime
from anvil import (
    ColumnPanel, FlowPanel, GridPanel, Label, Link, TextBox, Button, CheckBox,
    DropDown, Spacer, alert, confirm,
)

from ..common import (
    make_top_bar, make_page, make_row, make_toolbar, make_list_card,
    make_banner, make_section_header, make_chip, make_band_chip,
    make_empty_state, band_role, navigate, toast_error,
)

# Mirrors of the server enums (the client cannot import server modules; keep in
# sync — test_constants.py asserts these still match _constants.py).
_TYPES = (('SAC', 'sac'), ('SAT', 'sat'), ('Exam', 'exam'),
          ('Project', 'project'), ('Homework', 'homework'), ('Other', 'other'))
_STATUSES = (('Not started', 'not_started'), ('In progress', 'in_progress'),
             ('Completed', 'completed'))
_TYPE_LABELS = dict((v, k) for k, v in _TYPES)
_SORTS =(('Sort: due date', 'due_date'), ('Sort: weight', 'weight'),
          ('Sort: subject', 'subject'))

# Parser confidence -> chip tone. The colours themselves live in the
# stylesheet, so these are role suffixes, not hex values.
_CONF_TONE = {'HIGH': 'ok', 'MEDIUM': 'warn', 'LOW': 'bad'}

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


def _from_iso(s):
    """'YYYY-MM-DD' -> date, or None (manual; avoids Skulpt isoformat quirks)."""
    if not s or not isinstance(s, str) or len(s) < 10:
        return None
    try:
        return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except (ValueError, TypeError):
        return None


class DashboardForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self._current_month = None   # 'YYYY-MM'; None -> server uses today's month
        self._today = None           # date, from the server payload
        self._day_buckets = {}
        self._exam_days = {}

        self.add_component(make_top_bar(active='dashboard'))
        body = make_page()
        self.add_component(body)

        # --- the hero: type a sentence, get an assessment (FR01/FR02) ---
        self._nlp_tb = TextBox(
            placeholder='Type an assessment, e.g. "Methods SAC2 due Friday week 5 worth 25%"',
            role='bigfield')
        self._nlp_tb.set_event_handler('pressed_enter', self._on_parse_click)
        parse_btn = Button(text='Parse', role='primary')
        parse_btn.set_event_handler('click', self._on_parse_click)
        add_btn = Button(text='Add manually', role='secondary')
        add_btn.set_event_handler('click', self._on_add_click)
        bulk_btn = Button(text='Bulk add', role='secondary')
        bulk_btn.set_event_handler('click', self._on_bulk_click)
        body.add_component(make_toolbar(self._nlp_tb, parse_btn, add_btn, bulk_btn))

        # --- filters (FR07) ---
        # No "Filter:" / "Sort:" captions: each control names itself, which is
        # two fewer words on screen.
        self._subject_dd = DropDown(items=[('All subjects', 'All')])
        self._subject_dd.set_event_handler('change', self._on_filter_change)
        self._status_dd = DropDown(items=[('All status', '')] + list(_STATUSES))
        self._status_dd.set_event_handler('change', self._on_filter_change)
        self._type_dd = DropDown(items=[('All types', '')] + list(_TYPES))
        self._type_dd.set_event_handler('change', self._on_filter_change)
        self._show_completed_cb = CheckBox(text='Show completed', role='pill')
        self._show_completed_cb.set_event_handler('change', self._on_filter_change)
        self._sort_dd = DropDown(items=list(_SORTS))
        self._sort_dd.selected_value = 'due_date'
        self._sort_dd.set_event_handler('change', self._on_filter_change)
        body.add_component(make_toolbar(
            self._subject_dd, self._status_dd, self._type_dd,
            self._show_completed_cb, self._sort_dd))

        # Banner slots: filled only when there is something to say.
        self._hint_panel = ColumnPanel(role='row')
        body.add_component(self._hint_panel)
        self._exam_chip_panel = ColumnPanel(role='row')
        body.add_component(self._exam_chip_panel)

        body.add_component(Spacer(height=8))

        # --- three-panel body (list | calendar | upcoming) ---
        # role='dashgrid' is what the stylesheet's media query targets to stack
        # these three columns on a narrow screen.
        grid = GridPanel(role='dashgrid')
        self._list_panel = ColumnPanel(role='panel')
        self._calendar_panel = ColumnPanel(role='panel')
        self._upcoming_panel = ColumnPanel(role='panel')
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
            self._list_panel.add_component(make_empty_state(
                "Couldn't load your dashboard",
                'Check your connection and try again. (%s)' % e,
                'Retry', self._refresh))
            return
        self._today = _from_iso(data.get('today'))
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
        go = Link(text='Open Settings', role='t-accent')
        go.set_event_handler('click', lambda **e: navigate('settings'))
        self._hint_panel.add_component(make_banner(
            make_chip('Tip', 'accent'),
            Label(text='Set your school terms so dates like "term 3 week 5" resolve '
                       'automatically.', role='caption'),
            go))

    def _render_exam_chip(self, next_exam):
        """Countdown chip for the next VCE written exam (spec §13)."""
        self._exam_chip_panel.clear()
        if not next_exam:
            return
        days = next_exam.get('days_remaining')
        if days == 0:
            when = 'today'
        elif days == 1:
            when = 'tomorrow'
        else:
            when = 'in %d days' % days
        go = Link(text='Exam timetable', role='t-accent')
        go.set_event_handler('click', lambda **e: navigate('exams'))
        self._exam_chip_panel.add_component(make_banner(
            make_chip('Next exam', 'exam'),
            Label(text='%s — %s' % (next_exam.get('subject'), next_exam.get('paper')),
                  role='cardtitle'),
            make_chip(when, 'exam'),
            go))

    def _populate_subjects(self, subjects):
        """Rebuild the subject filter, preserving the current choice."""
        current = self._subject_dd.selected_value
        items = [('All subjects', 'All')] + [(s, s) for s in subjects]
        values = [v for _, v in items]
        self._subject_dd.items = items
        self._subject_dd.selected_value = current if current in values else 'All'

    # --- list panel --------------------------------------------------------
    def _render_list(self, rows):
        self._list_panel.clear()
        count = '%d shown' % len(rows) if rows else None
        self._list_panel.add_component(
            make_section_header('Your assessments', count))
        if not rows:
            self._list_panel.add_component(make_empty_state(
                'Nothing here yet',
                'Type an assessment above and press Parse, or add one manually.',
                'Add manually', self._on_add_click))
            return
        for a in rows:
            self._list_panel.add_component(self._make_card(a))

    def _make_card(self, a):
        """One assessment row. The card's left edge carries the urgency band, so
        the list scans by colour without a decorative dot on every line."""
        band = a.get('urgency_band', 'distant')
        card = make_list_card(band)

        # Row 1: title, then the tags that identify it.
        top = make_row(Label(text=a.get('title') or '(untitled)', role='cardtitle'))
        if a.get('subject'):
            top.add_component(make_chip(a['subject']))
        type_label = _TYPE_LABELS.get(a.get('type'), a.get('type') or '')
        if type_label:
            top.add_component(make_chip(type_label))
        conf = a.get('confidence')
        if conf:
            # FR17 audit trail: this row came from the parser, at this confidence.
            top.add_component(make_chip('parsed · %s' % conf,
                                        _CONF_TONE.get(conf, 'distant')))
        card.add_component(top)

        # Row 2: when it's due, how much it's worth, and the actions.
        bottom = make_row(Label(text=self._due_text(a),
                                role=band_role(band, 't')))
        if a.get('weight') is not None:
            bottom.add_component(Label(text='%g%% of grade' % a.get('weight'),
                                       role='caption'))
        status_dd = DropDown(items=list(_STATUSES))
        status_dd.selected_value = a.get('status') or 'not_started'
        status_dd.set_event_handler(
            'change', lambda aid=a['id'], dd=status_dd, **e:
            self._on_card_status_change(aid, dd.selected_value))
        bottom.add_component(status_dd)
        edit_btn = Button(text='Edit', role='ghost')
        edit_btn.set_event_handler('click', lambda aid=a['id'], **e: self._on_edit_click(aid))
        bottom.add_component(edit_btn)
        del_btn = Button(text='Delete', role='danger')
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
            toast_error("Couldn't update status: %s" % e)
        self._refresh()

    def _due_text(self, a):
        due = a.get('due_display') or 'no date'
        days = a.get('days_remaining')
        if days is None:
            return due
        if days < 0:
            return '%s · %d day%s overdue' % (due, -days, '' if days == -1 else 's')
        if days == 0:
            return '%s · today' % due
        return '%s · in %d day%s' % (due, days, '' if days == 1 else 's')

    # --- calendar panel ----------------------------------------------------
    def _render_calendar(self, cal):
        self._calendar_panel.clear()
        year = cal.get('year')
        month = cal.get('month')
        weeks = cal.get('weeks') or []
        colours = cal.get('cell_colours') or {}
        self._day_buckets = cal.get('day_buckets') or {}
        self._exam_days = cal.get('exam_days') or {}
        # Remember the displayed month for prev/next arithmetic.
        self._displayed_year, self._displayed_month = year, month

        prev_btn = Button(text='‹', role='iconbtn')
        prev_btn.set_event_handler('click', lambda **e: self._change_month(-1))
        next_btn = Button(text='›', role='iconbtn')
        next_btn.set_event_handler('click', lambda **e: self._change_month(1))
        label = '%s %s' % (_MONTH_NAMES[month] if month else '', year or '')
        self._calendar_panel.add_component(make_section_header('Calendar'))
        self._calendar_panel.add_component(make_row(
            prev_btn, Label(text=label.strip(), role='cardtitle'), next_btn))

        header = FlowPanel(role='calhead')
        for name in _WEEKDAY_HEADERS:
            header.add_component(Label(text=name))
        self._calendar_panel.add_component(header)

        # One flat list of cells; the CSS grid wraps them into weeks.
        grid = FlowPanel(role='calgrid')
        for week in weeks:
            for day in week:
                grid.add_component(self._make_day_cell(day, colours, year, month))
        self._calendar_panel.add_component(grid)

    def _make_day_cell(self, day, colours, year, month):
        """One calendar square. Days outside the month are invisible spacers."""
        if day == 0:
            return Label(text=' ', role='calcell-blank')

        band = _cell(colours, day)
        items = _cell(self._day_buckets, day) or []
        exams = _cell(self._exam_days, day) or []

        role = band_role(band, 'calcell') if band else 'calcell'
        cell = Link(role=role)
        cell.set_event_handler('click', lambda d=day, **e: self._on_day_click(d))

        is_today = (self._today is not None and year == self._today.year
                    and month == self._today.month and day == self._today.day)
        cell.add_component(Label(text=str(day),
                                 role='calnum-now' if is_today else 'calnum'))
        if len(items) > 1:
            cell.add_component(Label(text='%d due' % len(items), role='calcount'))
        if exams:
            # Glyph as well as colour, so an exam day never signals by colour alone.
            cell.add_component(Label(text='▲ exam', role='calexam'))
        return cell

    def _on_day_click(self, day):
        """Show the clicked calendar day's assessments and VCE exams."""
        items = _cell(self._day_buckets, day) or []
        exams = _cell(self._exam_days, day) or []
        panel = ColumnPanel()
        if not items and not exams:
            panel.add_component(make_empty_state('Nothing due this day'))
        for label in exams:
            row = make_list_card()
            row.add_component(make_row(make_chip('VCE exam', 'exam'),
                                       Label(text=label, role='cardtitle')))
            panel.add_component(row)
        for a in items:
            band = a.get('urgency_band', 'distant')
            row = make_list_card(band)
            detail = make_row(Label(text=a.get('title') or '(untitled)',
                                    role='cardtitle'))
            if a.get('subject'):
                detail.add_component(make_chip(a['subject']))
            type_label = _TYPE_LABELS.get(a.get('type'), '')
            if type_label:
                detail.add_component(make_chip(type_label))
            row.add_component(detail)
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
        self._upcoming_panel.add_component(make_section_header('Next 30 days'))
        if not upcoming:
            self._upcoming_panel.add_component(make_empty_state(
                'All clear',
                'Nothing is due in the next 30 days.'))
            return
        for a in upcoming:
            band = a.get('urgency_band', 'distant')
            self._upcoming_panel.add_component(make_row(
                make_band_chip(a.get('due_display') or '', band),
                Label(text=a.get('title') or '(untitled)', role='caption')))

    # --- handlers ----------------------------------------------------------
    def _on_filter_change(self, **event_args):
        self._refresh()

    def _on_parse_click(self, **event_args):
        text = (self._nlp_tb.text or '').strip()
        if not text:
            toast_error("Type an assessment first.")
            return
        try:
            parsed = anvil.server.call('parse_text', text)
        except Exception as e:
            toast_error("Couldn't parse: %s" % e)
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
            toast_error("Couldn't delete: %s" % e)
            return
        self._refresh()
