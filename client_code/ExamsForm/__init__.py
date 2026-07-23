import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""ExamsForm - the student's VCE 2026 written-exam timetable (spec §13).

One get_exam_timetable round-trip renders: a countdown header for the next
exam, a card per written paper (date, session time, days-remaining chip
coloured by the shared urgency bands, greyed once done), any locked subjects
with no VCAA written exam, and the VCAA source link. Subjects come from the
locked user_settings.subjects (English guaranteed server-side).
"""

import anvil
import anvil.server
import datetime
from anvil import ColumnPanel, FlowPanel, Label, Link, Button, Notification

from ..common import make_top_bar, _navigate

# Mirror of _constants.URGENCY_COLOURS plus the 'done' (past exam) grey.
_BAND_COLOURS = {
    'overdue': '#d64550', 'today': '#e8833a', 'soon': '#3b7dd8',
    'distant': '#9aa0a6', 'done': '#c4c9d0',
}

_WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
_MONTHS_ABBR = ('', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _fmt_exam_date(iso):
    """'2026-11-05' -> 'Thu 5 Nov 2026' (manual; avoids Skulpt strftime gaps)."""
    try:
        parts = iso.split('-')
        d = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError, AttributeError, IndexError):
        return iso or ''
    return '%s %d %s %d' % (_WEEKDAYS[d.weekday()], d.day,
                            _MONTHS_ABBR[d.month], d.year)


def _days_chip_text(days):
    if days < 0:
        return 'done'
    if days == 0:
        return 'TODAY'
    if days == 1:
        return 'tomorrow'
    return 'in %d days' % days


class ExamsForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        self.add_component(make_top_bar())
        body = ColumnPanel()
        self.add_component(body)

        body.add_component(Label(text='VCE Written Exams 2026', role='heading',
                                 font_size=24))

        try:
            data = anvil.server.call('get_exam_timetable')
        except Exception as e:
            body.add_component(Label(
                text="Couldn't load the exam timetable: %s" % e,
                foreground='#d64550'))
            return

        if not data.get('onboarded'):
            body.add_component(Label(
                text='Lock in your subjects first — the timetable shows only '
                     'the exams you actually sit.'))
            go = Button(text='Choose my subjects', role='primary')
            go.set_event_handler('click', lambda **e: _navigate('onboarding'))
            body.add_component(go)
            return

        exams = data.get('exams') or []
        nxt = data.get('next_exam')

        if nxt:
            banner = ColumnPanel(role='card')
            banner.add_component(Label(
                text='Next exam: %s (%s)' % (nxt['subject'], nxt['paper']),
                bold=True, font_size=16))
            banner.add_component(Label(
                text='%s, %s–%s — %s' % (
                    _fmt_exam_date(nxt['date']), nxt['start'], nxt['end'],
                    _days_chip_text(nxt['days_remaining'])),
                foreground=_BAND_COLOURS.get(nxt['urgency_band'], '#9aa0a6'),
                bold=True))
            body.add_component(banner)
        elif exams:
            body.add_component(Label(
                text='All your 2026 written exams are done. Nice work.',
                italic=True, foreground='#6b7280'))

        if not exams:
            body.add_component(Label(
                text='None of your subjects has a VCAA written exam in 2026.',
                italic=True, foreground='#6b7280'))

        for e in exams:
            body.add_component(self._exam_card(e))

        no_exam = data.get('no_exam_subjects') or []
        if no_exam:
            body.add_component(Label(
                text='No VCAA written exam on file: %s' % ', '.join(no_exam),
                font_size=12, italic=True, foreground='#9aa0a6'))

        src = Link(text='Source: VCAA 2026 VCE examination timetable',
                   url=data.get('source_url'), font_size=12)
        body.add_component(src)

    def _exam_card(self, e):
        card = ColumnPanel(role='card')
        done = e['urgency_band'] == 'done'

        top = FlowPanel()
        top.add_component(Label(text='●', foreground=_BAND_COLOURS.get(
            e['urgency_band'], '#9aa0a6'), bold=True))
        top.add_component(Label(text=e['subject'], bold=True, font_size=15,
                                foreground='#9aa0a6' if done else None))
        top.add_component(Label(text=e['paper'], role='chip'))
        top.add_component(Label(
            text=_days_chip_text(e['days_remaining']), bold=True,
            foreground=_BAND_COLOURS.get(e['urgency_band'], '#9aa0a6')))
        card.add_component(top)

        card.add_component(Label(
            text='%s · %s–%s' % (_fmt_exam_date(e['date']), e['start'], e['end']),
            font_size=12, foreground='#6b7280'))
        return card
