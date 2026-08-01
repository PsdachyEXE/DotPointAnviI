import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""ExamsForm - the student's VCE 2026 written-exam timetable (spec §13).

One get_exam_timetable round-trip renders the whole screen, composed out of
the shared UI kit (spec §14) in a single top-to-bottom hierarchy:

    top bar -> page title -> next-exam banner -> a list card per written paper
            -> an "About this timetable" card (data gaps + the VCAA source).

Urgency is shown twice, never by colour alone: the list card's left edge and a
matching chip are both driven by the server's urgency band, and the band is
mapped to a stylesheet role rather than a hex colour, so the screen follows the
light/dark theme. A paper that is already over has no urgency left to
communicate, so it is drawn with no left accent and a neutral chip - the list
then reads at a glance as "what is still coming". Subjects come from the locked
user_settings.subjects (English guaranteed server-side).
"""

import anvil.server
import datetime
from anvil import ColumnPanel, Label, Link

from ..common import (
    navigate, toast_error, make_top_bar, make_page, make_page_title,
    make_row, make_card, make_list_card, make_banner, make_section_header,
    make_chip, make_band_chip, make_empty_state, MONTHS_ABBR,
)

# Only the weekday names are local now: the month abbreviations are the shared
# common.MONTHS_ABBR, so a month reads identically here and everywhere else.
_WEEKDAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def _fmt_exam_date(iso):
    """'2026-11-05' -> 'Thu 5 Nov 2026' (manual; avoids Skulpt strftime gaps).

    This form keeps its own formatter because an exam line names the weekday
    ("Thu 5 Nov 2026") - common.fmt_date gives '05 Nov 2026' with no weekday,
    and on this screen the weekday is the whole point of showing the date.
    """
    try:
        parts = iso.split('-')
        d = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError, AttributeError, IndexError):
        return iso or ''
    return '%s %d %s %d' % (_WEEKDAYS[d.weekday()], d.day,
                            MONTHS_ABBR[d.month], d.year)


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

        # The top bar spans the window; every other component sits inside the
        # centred page column, so the timetable lines up with the rest of the app.
        self.add_component(make_top_bar(active='exams'))
        body = make_page()
        self.add_component(body)

        # Sentence case, like every other page title in the app - Title Case here
        # was the only heading that did not match.
        body.add_component(make_page_title(
            'VCE written exams 2026',
            'Every VCAA written paper for your locked subjects, soonest first.'))

        try:
            data = anvil.server.call('get_exam_timetable')
        except Exception as e:
            # The toast carries the actual error text; the empty state is what
            # stops the page being left blank with no explanation.
            toast_error("Couldn't load the exam timetable: %s" % e)
            # A failure state with no button is a dead end, so it offers the same
            # Retry as the dashboard's: re-navigating to 'exams' rebuilds the
            # form and repeats the server call.
            body.add_component(make_empty_state(
                "Couldn't load the exam timetable",
                'Check your connection and reload the page.',
                'Retry',
                lambda: navigate('exams')))
            return

        if not data.get('onboarded'):
            # Nothing can be shown until subjects are locked, so the empty state
            # carries the fix rather than just describing the problem.
            body.add_component(make_empty_state(
                'No subjects locked in yet',
                'The timetable shows only the exams you actually sit.',
                'Choose my subjects',
                lambda: navigate('onboarding')))
            return

        exams = data.get('exams') or []
        nxt = data.get('next_exam')

        # One banner answers the question this screen is opened for: what is
        # next, and how long have I got? It is a banner rather than a card so it
        # reads as a status line, not as the first item of the list below it.
        if nxt:
            body.add_component(make_banner(
                Label(text='Next exam', role='sectionhead'),
                # Em dash between subject and paper, and a middot between the
                # date and the time window - the same two separators the
                # dashboard and _exam_card use for these exact pairs.
                Label(text='%s — %s' % (nxt['subject'], nxt['paper']),
                      role='cardtitle'),
                make_band_chip(_days_chip_text(nxt['days_remaining']),
                               nxt['urgency_band']),
                Label(text='%s · %s–%s' % (_fmt_exam_date(nxt['date']),
                                           nxt['start'], nxt['end']),
                      role='caption'),
            ))
        elif exams:
            # Every paper is behind them - keep the same banner slot so the page
            # does not visibly change shape once exams finish.
            # 'caption' is the role every other banner sentence in the app uses;
            # 'muted' is reserved for a single item that has receded (see
            # _exam_card), not for a whole line of banner copy.
            body.add_component(make_banner(Label(
                text='All your 2026 written exams are done. Nice work.',
                role='caption')))

        if not exams:
            body.add_component(make_empty_state(
                'No written exams in 2026',
                'None of your subjects has a VCAA written exam.'))
        else:
            # The count in the section header is the one number worth putting in
            # the hierarchy: done papers stay listed, so "still to sit" is not
            # obvious from the list length alone.
            remaining = len([x for x in exams if x['urgency_band'] != 'done'])
            body.add_component(make_section_header(
                'Your papers',
                '%d of %d still to sit' % (remaining, len(exams))))
            for exam in exams:
                body.add_component(self._exam_card(exam))

        # These three notes used to float at the bottom as stray sentences.
        # Grouping them under one header keeps the caveats available without
        # letting them compete with the timetable itself.
        info = make_card()
        info.add_component(make_section_header('About this timetable'))

        no_exam = data.get('no_exam_subjects') or []
        if no_exam:
            info.add_component(Label(
                text='No written exam on the VCAA timetable: %s' % ', '.join(no_exam),
                role='caption'))

        not_covered = data.get('not_covered') or []
        if not_covered:
            # A gap in DotPoint's own data is NOT the same as "no exam", so it is
            # toned as a warning - the student has to go and check VCAA themselves.
            info.add_component(Label(
                text='Not in DotPoint\'s timetable data yet (check the VCAA '
                     'timetable): %s' % ', '.join(not_covered),
                role='t-warn'))

        # 't-accent', not 'caption': 'caption' only styles .label-text, and a
        # Link's text sits in .link-text, so a captioned Link kept Anvil's
        # default blue and ignored the theme.
        info.add_component(Link(
            text='Source: VCAA 2026 VCE examination timetable',
            url=data.get('source_url'), role='t-accent'))
        body.add_component(info)

    def _exam_card(self, exam):
        """One written paper as a list row.

        The urgency band drives the left edge and the countdown chip together.
        The server's extra 'done' state is not an urgency band at all, so it is
        deliberately passed as band=None (no accent) with a neutral chip and a
        muted subject: finished papers recede instead of shouting grey.
        """
        band = exam['urgency_band']
        done = band == 'done'
        countdown = _days_chip_text(exam['days_remaining'])

        card = make_list_card(None if done else band)
        card.add_component(make_row(
            Label(text=exam['subject'], role='muted' if done else 'cardtitle'),
            make_chip(exam['paper']),
            make_chip(countdown) if done else make_band_chip(countdown, band),
        ))
        card.add_component(Label(
            text='%s · %s–%s' % (_fmt_exam_date(exam['date']), exam['start'],
                                 exam['end']),
            role='caption'))
        return card
