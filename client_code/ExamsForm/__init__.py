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

    `iso` is one payload date string, 'YYYY-MM-DD'. Returns the formatted text,
    or the input unchanged when it cannot be read - never raises, because one
    mistyped row in the timetable table must not take the page down with it.

    This is the app's one departure from NFR08's "DD MMM YYYY": the weekday is
    added and the day is not zero-padded. Exams are planned by weekday ("the
    Methods one is on the Thursday"), and this screen shows nothing but exam
    dates, so the exception is contained to it - every other date in the app
    still goes through common.fmt_date.
    """
    try:
        # Split rather than date.fromisoformat: Skulpt's date support is
        # partial, so the three integers are read out by hand. A str with the
        # wrong shape lands in the except below rather than being guessed at.
        parts = iso.split('-')
        d = datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError, AttributeError, IndexError):
        # Four exception types for four ways the value can be wrong: bad digits
        # (ValueError), None (TypeError/AttributeError), too few parts
        # (IndexError). Showing the raw string beats showing nothing.
        return iso or ''
    return '%s %d %s %d' % (_WEEKDAYS[d.weekday()], d.day,
                            MONTHS_ABBR[d.month], d.year)


def _days_chip_text(days):
    """days_remaining -> the countdown words on a chip.

    `days` is the payload's days_remaining: 0 is the day of the paper and
    negative means it is behind them. The three special cases are spelled out
    because 'in 0 days' and 'in 1 days' are not things anyone says.

    Note this reads days_remaining ALONE. A paper that finished earlier today
    is days 0, so it is labelled 'today' here; that it is over is carried by
    the urgency band instead, which is what _exam_card colours the row from.
    """
    # Ordered most-specific-first and returning on the first match, the same
    # shape FR21's urgency bands use, so the three exceptions are settled
    # before the general case can claim them.
    #
    # 'done', not 'overdue': an exam that has been sat is finished, and the red
    # "overdue" wording FR09 gives a missed assessment would be telling the
    # student off for something they cannot act on.
    if days < 0:
        return 'done'
    if days == 0:
        return 'today'
    if days == 1:
        return 'tomorrow'
    # WORDS, NOT A FORMATTED NUMBER. No single template could produce the four
    # results above, because 'today' and 'tomorrow' share no stem with 'in N
    # days' — pluralising an "in %d day(s)" string would still leave both of
    # them wrong. So only the branch that genuinely counts uses a number, and
    # its plural is safe to hard-code: days 0 and 1 have already gone.
    return 'in %d days' % days


class ExamsForm(ColumnPanel):
    """The #exams screen: the student's own VCE 2026 written-exam timetable.

    A whole page, top to bottom: the top bar, a title, a banner naming the next
    paper and how long is left, one card per written paper (soonest first, the
    finished ones still listed), and a closing "About this timetable" card that
    holds the two data caveats and the link to the VCAA page it was copied from.

    WHICH REQUIREMENTS IT IMPLEMENTS
      The timetable itself is spec §13 — an addition to the app, not one of the
      numbered SRS requirements, so no FR is claimed for it here. What it does
      reuse, deliberately, is the conventions those requirements set:
        FR21   urgency is the same first-match-wins band the assessment views
               use (_datetime._urgency_band), shown twice — the card's left
               edge AND a chip — so it is never carried by colour alone, and
               mapped to a stylesheet role rather than a hex value so it
               follows the light/dark theme.
        FR09   'days remaining' is the same (date - today).days the SRS
               specifies for assessments, computed server-side in the
               student's own timezone rather than the browser's.
        NFR03  one callable, which reads the subject list off the current
               user's own settings row; this form never names a user, and
               there is nothing here that could ask for someone else's.
      Dates on this screen do NOT follow NFR08's 'DD MMM YYYY' — see
      _fmt_exam_date, which explains why the weekday is worth the exception.

    HOW IT IS CONSTRUCTED
      ExamsForm() — no arguments of its own, no modes; Main._make_form() builds
      it with none. The whole page is drawn once, in __init__, from a single
      get_exam_timetable payload, and nothing repaints afterwards. That is why
      the failure state's Retry button navigates to 'exams' again: rebuilding
      the form IS the refresh.

    SERVER CALLABLES IT DEPENDS ON
      exams.get_exam_timetable — the only one, called once. Its payload shape
      is documented in __init__ below.

    WHAT IT HANDS BACK
      Nothing: it is a page added as Main's child, not a dialog, so there is no
      return value and no raised event. Its only outward move is navigate(),
      from the Retry and 'Choose my subjects' buttons on the two empty states.
    """

    def __init__(self, **properties):
        """Fetch the timetable and draw the entire page from it.

        THE PAYLOAD. get_exam_timetable returns a plain dict:

          today             'YYYY-MM-DD' in the student's timezone (not read
                            here; every countdown is already worked out).
          onboarded         bool — False means no subjects are locked in, so
                            there is nothing to filter the timetable by.
          exams             list of paper dicts, sorted soonest first:
                            {'subject': canonical name,
                             'paper': e.g. 'Exam 1' / 'Written examination',
                             'date': 'YYYY-MM-DD',
                             'start': 'HH:MM', 'end': 'HH:MM',
                             'days_remaining': int, negative once past,
                             'urgency_band': 'overdue' | 'today' | 'soon' |
                                             'distant' | 'done'}
          next_exam         one of those dicts, or None when every paper is
                            over or there are none.
          no_exam_subjects  list of the student's subjects that genuinely have
                            no written paper on the VCAA timetable.
          not_covered       list of their subjects DotPoint has no timetable
                            data for. A DIFFERENT thing from the line above,
                            and the reason both lists exist separately.
          source_url        the VCAA page the timetable was transcribed from.

        'done' is not an urgency band, it is a fifth state the server adds for
        a paper that is over — including one that finished earlier TODAY, which
        it can tell because it compares the clock in the student's timezone
        against the paper's end time.
        """
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

        # 1. One round trip builds the whole screen. Everything below reads out
        #    of `data`; there is no second call and no per-card lookup.
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

        # 2. The onboarding gate. `onboarded` is False whenever the subject
        #    list is empty, and this screen is a FILTER over that list — with
        #    nothing to filter by, showing the entire VCAA timetable would be
        #    worse than showing nothing, so the page stops here.
        if not data.get('onboarded'):
            # Nothing can be shown until subjects are locked, so the empty state
            # carries the fix rather than just describing the problem.
            body.add_component(make_empty_state(
                'No subjects locked in yet',
                'The timetable shows only the exams you actually sit.',
                'Choose my subjects',
                lambda: navigate('onboarding')))
            return

        # 3. The two values the rest of the page is drawn from. `or []` and a
        #    plain get() rather than indexing, because both keys are honestly
        #    empty or None for a student whose papers are all behind them.
        exams = data.get('exams') or []
        nxt = data.get('next_exam')

        # 4. One banner answers the question this screen is opened for: what is
        # next, and how long have I got? It is a banner rather than a card so it
        # reads as a status line, not as the first item of the list below it.
        if nxt:
            # nxt is never a finished paper — _find_next_exam skips those — so
            # its band is always a live urgency band and make_band_chip is
            # safe here, where the per-card version below has to handle 'done'.
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

        # 5. The list itself. An empty `exams` here is not a failure: it is a
        #    student every one of whose studies is in NO_WRITTEN_EXAM, so the
        #    empty state says that rather than offering an action.
        if not exams:
            body.add_component(make_empty_state(
                'No written exams in 2026',
                'None of your subjects has a VCAA written exam.'))
        else:
            # The count in the section header is the one number worth putting in
            # the hierarchy: done papers stay listed, so "still to sit" is not
            # obvious from the list length alone.
            #
            # Counted on the BAND, not on days_remaining >= 0: a paper that
            # finished earlier today still has days_remaining == 0, and counting
            # it as "still to sit" would tell the student they have an exam left
            # that they walked out of this morning.
            remaining = len([x for x in exams if x['urgency_band'] != 'done'])
            body.add_component(make_section_header(
                'Your papers',
                '%d of %d still to sit' % (remaining, len(exams))))
            for exam in exams:
                body.add_component(self._exam_card(exam))

        # 6. These three notes used to float at the bottom as stray sentences.
        # Grouping them under one header keeps the caveats available without
        # letting them compete with the timetable itself. The card is added
        # unconditionally, because the source link inside it is always worth
        # showing even when neither caveat list has anything in it.
        info = make_card()
        info.add_component(make_section_header('About this timetable'))

        # 7. "You have no written exam in this study" — a fact about the VCAA
        #    timetable, and the reason a subject the student has worked at all
        #    year is missing from the list above. Without it, a study like
        #    Applied Computing simply vanishes off this screen and the student
        #    is left wondering whether DotPoint lost it.
        no_exam = data.get('no_exam_subjects') or []
        if no_exam:
            info.add_component(Label(
                text='No written exam on the VCAA timetable: %s' % ', '.join(no_exam),
                role='caption'))

        # 8. A gap in DotPoint's own data is NOT the same as "no exam", so it is
        #    toned as a warning - the student has to go and check VCAA itself.
        not_covered = data.get('not_covered') or []
        if not_covered:
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

        `exam` is one paper dict from the payload (see __init__); every key it
        reads - subject, paper, date, start, end, days_remaining, urgency_band -
        is always present, so the values are indexed rather than .get()'d: a
        missing one is a bug in the server, not a state to paper over.
        Returns the card panel for the caller to add.
        """
        band = exam['urgency_band']
        done = band == 'done'
        countdown = _days_chip_text(exam['days_remaining'])

        # band_role() would map the unknown name 'done' onto the 'distant'
        # colour, which is why 'done' is turned into None here rather than being
        # passed through: a finished paper would otherwise be tinted as though
        # it were still weeks away.
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
