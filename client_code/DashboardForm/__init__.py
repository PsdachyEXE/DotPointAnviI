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
its inner gutter into a 7-column CSS grid, so the day cells go in as one flat
list and wrap into weeks by themselves. How many there are varies with the
month: calendar.monthcalendar() returns 4, 5 or 6 week rows, so the grid is
handed 28, 35 or 42 cells and is never told how many weeks to draw. The
previous implementation used a Bootstrap GridPanel per week, which cannot
divide 12 columns evenly by 7 and so rendered visibly crooked.

See IMPLEMENTATION_SPEC.md section 3 (DashboardForm) and section 14.
"""

import anvil
import anvil.server
from anvil import (
    ColumnPanel, FlowPanel, GridPanel, Label, Link, TextBox, Button, CheckBox,
    DropDown, alert, confirm,
)

from ..common import (
    make_top_bar, make_page, make_row, make_toolbar, make_list_card,
    make_banner, make_section_header, make_chip, make_band_chip,
    make_empty_state, band_role, navigate, toast_error, toast_warn, from_iso, CONF_TONE,
)

# Mirrors of the server enums (the client cannot import server modules, so the
# option lists have to be duplicated and kept in sync by hand). Each entry is a
# (label the student reads, value the server stores) pair, which is the shape
# Anvil's DropDown.items wants.
#
# CAUTION: the offline constants-integrity suite (docs/TESTING.md §1) means to
# assert these still match server_code/_constants.py, but it looks the constants
# up under the un-prefixed names AssessmentEditorForm uses (TYPES / STATUSES)
# and skips a file that does not define them. The underscored names below are
# therefore NOT covered — change one and no test fails.
_TYPES = (('SAC', 'sac'), ('SAT', 'sat'), ('Exam', 'exam'),
          ('Project', 'project'), ('Homework', 'homework'), ('Other', 'other'))
_STATUSES = (('Not started', 'not_started'), ('In progress', 'in_progress'),
             ('Completed', 'completed'))
# Reverse map so a stored value ('sac') can be drawn as its label ('SAC') on a
# card. Built from _TYPES rather than written out again, so the two can never
# disagree about what 'homework' is called.
_TYPE_LABELS = dict((v, k) for k, v in _TYPES)
# The sort keys the server whitelists (_constants.ALLOWED_SORT_KEYS). FR07 makes
# due date the default, which __init__ sets below; the other two are the
# alternatives it allows.
_SORTS = (('Sort: due date', 'due_date'),
          ('Sort: weight', 'weight'),
          ('Sort: subject', 'subject'))

# Monday-first, matching calendar.monthcalendar()'s column order on the server.
_WEEKDAY_HEADERS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
# Index 0 is a deliberate empty string so a 1-12 month number indexes straight
# in, with no off-by-one arithmetic at the one place that reads it.
_MONTH_NAMES = ('', 'January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December')


# --- the server payload ------------------------------------------------------
# EVERY panel on this screen is drawn from one dict, returned by
# server_code/dashboard.get_dashboard_data(month, filters, sort). This form
# makes no other read call, so the list below is the whole of the data it has:
#
#   'today'            'YYYY-MM-DD'. The user's today in THEIR timezone, not the
#                      browser's. _refresh turns it into a date with from_iso
#                      and keeps it in self._today, solely to mark today's cell.
#   'assessment_list'  [assessment, ...] already filtered and sorted server-side
#                      to match the filters/sort that were sent. The left panel
#                      renders it in the order given and never re-sorts it, so
#                      what is on screen always matches what was asked for.
#   'calendar'         {'year':  int,
#                       'month': 1-12,
#                       'weeks': [[int x 7], ...],  4, 5 or 6 rows; 0 means a
#                                                   square outside the month,
#                       'day_buckets':  {'<day>': [assessment, ...]},
#                       'cell_colours': {'<day>': urgency band},
#                       'exam_days':    {'<day>': ['Subject — paper', ...]}}
#                      The three dicts are keyed by the day number as a STRING
#                      (see _cell). cell_colours already holds the MOST urgent
#                      band in that day's bucket, so the tint is decided
#                      server-side and this form only paints it (FR08, FR21).
#   'upcoming'         [assessment, ...] not completed, due within 30 days,
#                      soonest first. The right-hand sidebar (FR09).
#   'subjects'         [str, ...] the student's locked subjects first, then any
#                      subject that only older rows still use. Fills the subject
#                      filter, and holds only values the server would accept
#                      back, so the dropdown cannot offer a dead choice.
#   'next_exam'        The soonest unfinished VCE written paper, or None when
#                      the student has no examinable subject left this year:
#                      {'subject', 'paper', 'date', 'start', 'end',
#                       'days_remaining', 'urgency_band'} (spec §13).
#   'settings'         The user_settings dict. Only 'school_terms' is read here,
#                      to decide whether the FR15 tip banner is still needed.
#
# An `assessment` is the same guarded dict wherever it appears above — the list,
# a day bucket and the sidebar all hand back rows built by the one server
# function (dashboard._safe_assessment_view):
#   'id'             str, the Data Tables row id. The only value this form ever
#                    sends back to the server.
#   'title'          str; '' when the row has no usable title.
#   'subject'        str; '' when unset.
#   'type'           one of _TYPES' stored values; 'other' when the stored value
#                    has fallen off the enum.
#   'status'         one of _STATUSES' stored values.
#   'confidence'     'HIGH' / 'MEDIUM' / 'LOW' for a row the parser produced,
#                    None for one typed in by hand (FR17).
#   'weight'         float 0-100, or None when the row carries no weighting.
#   'due_date'       'YYYY-MM-DD', or None.
#   'due_display'    that same date already formatted 'DD MMM YYYY' (NFR08); ''
#                    when there is no date. This form never formats a due date
#                    itself, which is why every screen shows an identical one.
#   'days_remaining' int, negative when overdue; None when the date is missing
#                    or so implausible the server suppressed the countdown, in
#                    which case _due_text prints the date on its own.
#   'urgency_band'   'overdue' / 'today' / 'soon' / 'distant' (FR21), derived
#                    from days_remaining server-side. band_role() turns it into
#                    the stylesheet role that paints it, so no colour is chosen
#                    in this file.


def _cell(dct, day):
    """Look up a day key tolerant of int- or str-keyed dicts (Anvil transport).

    dct: one of the calendar's day-keyed dicts (day_buckets, cell_colours,
    exam_days), or None when the payload had nothing for that month.
    day: the day of the month as an int, 1-31.
    Returns whatever was stored for that day, or None when the day is absent —
    which every caller reads as "nothing on this date".

    Two lookups rather than one because the key type depends on how the dict
    travelled. The server stringifies these keys before returning them (Anvil
    refuses to serialize a dict whose keys are not strings), so the str branch
    is what fires in the running app; the int branch keeps the helper correct
    for a dict built locally, and costs one dictionary probe.
    """
    if dct is None:
        return None
    if day in dct:
        return dct[day]
    return dct.get(str(day))


class DashboardForm(ColumnPanel):
    """The home screen: everything the student has on, in one view.

    Down the page: the natural-language input bar, the filter/sort row, two
    banner slots, then a three-panel body —

      * LEFT, "Your assessments": one card per assessment, filtered by subject,
        status and type (FR06) and sorted by due date, weight or subject (FR07).
        Each card's left edge carries its urgency colour and its due line reads
        "21 Mar 2026 · in 4 days" (FR09, FR21), and each card has a status
        dropdown that saves on the spot and a pair of Edit/Delete buttons
        (FR04, FR05).
      * CENTRE, "Calendar": the displayed month as a 7-column grid, each day
        tinted by the most urgent thing due on it, today's number ringed, a
        count badge for how many, and ▲ for a VCE exam day (FR08, FR21).
        Clicking a day that has something on it opens a read-only dialog.
      * RIGHT, "Next 30 days": the not-completed assessments due inside the next
        30 days, soonest first (FR09).

    The input bar carries FR01 (parse one sentence) and reaches FR02 (bulk add)
    and FR03 (manual add) through AssessmentEditorForm. The FR15 tip banner
    appears until school terms are configured, and the next-exam countdown chip
    is the spec §13 exam overlay.

    Construction: takes no arguments of its own — Main's router builds it as
    DashboardForm() and it fetches its own data. `**properties` is Anvil's
    standard passthrough to ColumnPanel. There are no modes; __init__ builds the
    static layout once and then calls _refresh(), and from that point every
    change on screen goes through _refresh() rather than being patched in place,
    so the three panels can never disagree with each other.

    Server callables used:
      get_dashboard_data(month, filters, sort)   every refresh — the one read.
      parse_text(text)                           the Parse button (FR01).
      update_assessment(id, {'status': ...})     the card's status dropdown.
      delete_assessment(id)                      the card's Delete button.
    Everything else is reached by opening AssessmentEditorForm as an alert; that
    form does its own saving and hands back the id it wrote (or, in bulk mode,
    how many rows it inserted), which this form treats only as "something
    changed, refresh".

    Hands nothing back to its caller: it is a full-page form, not a dialog, and
    it leaves only by navigate()ing to another route.

    See IMPLEMENTATION_SPEC.md section 3 (DashboardForm) and section 14.
    """

    def __init__(self, **properties):
        """Build the static layout once, then load it with a single _refresh()."""
        super().__init__(**properties)
        # This form is the whole page, so it must not add the vertical padding
        # Anvil gives a ColumnPanel by default — the top bar has to sit flush
        # against the top of the window.
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        # --- form state ---
        # What this form remembers between refreshes. Everything else on screen
        # is rebuilt from the payload each time, which is what stops a stale
        # card outliving the data behind it. Two more attributes,
        # _displayed_year and _displayed_month, are added by _render_calendar
        # once a payload has arrived; _change_month reads them through getattr
        # because until then they do not exist.
        self._current_month = None   # 'YYYY-MM'; None -> server uses today's month
        self._today = None           # date, from the server payload
        # The clicked-day dialog is drawn from the payload the CURRENT month was
        # rendered from, so these two are kept out of _render_calendar's local
        # scope and held on the form: _on_day_click fires long after that call
        # has returned. _render_calendar replaces both on every month change.
        self._day_buckets = {}       # {'<day>': [assessment, ...]}
        self._exam_days = {}         # {'<day>': ['Subject — paper', ...]}

        self.add_component(make_top_bar(active='dashboard'))
        # Everything below the top bar sits in the centred page column, so the
        # dashboard lines up with every other screen in the app.
        body = make_page()
        self.add_component(body)

        # --- 1. the hero: type a sentence, get an assessment (FR01) ---
        # Top of the page because typing a sentence is the app's whole premise;
        # the two "proper form" buttons sit beside it rather than above it, so
        # the sentence box is the thing the eye lands on first.
        self._nlp_tb = TextBox(
            placeholder='Type an assessment, e.g. "Methods SAC2 due Friday week 5 worth 25%"',
            role='bigfield')
        # Enter and the Parse button run the same handler, because a student
        # who has just finished typing should not have to reach for the mouse.
        self._nlp_tb.set_event_handler('pressed_enter', self._on_parse_click)
        parse_btn = Button(text='Parse', role='primary')
        parse_btn.set_event_handler('click', self._on_parse_click)
        # The two escape hatches from the parser: one blank form (FR03) and one
        # paste-many-lines dialog (FR02). Both are AssessmentEditorForm in a
        # different mode, so nothing about a saved assessment is defined twice.
        add_btn = Button(text='Add manually', role='secondary')
        add_btn.set_event_handler('click', self._on_add_click)
        bulk_btn = Button(text='Bulk add', role='secondary')
        bulk_btn.set_event_handler('click', self._on_bulk_click)
        body.add_component(make_toolbar(self._nlp_tb, parse_btn, add_btn, bulk_btn))

        # --- 2. filters (FR06) and sort (FR07) ---
        # No "Filter:" captions: an option list reading "All subjects" /
        # "All types" already says what the control does, so a caption would
        # only repeat it. The sort dropdown is the exception — its options are
        # bare nouns, so each one is prefixed "Sort: " in _SORTS above,
        # which is the caption, carried inside the control instead of beside it.
        #
        # Every control below shares one handler. Filtering and sorting are both
        # done by the server (the list arrives ready to render), so a change to
        # any of them means the same thing: ask again.
        self._subject_dd = DropDown(items=[('All subjects', 'All')])
        self._subject_dd.set_event_handler('change', self._on_filter_change)
        # Each of these three carries a first entry that means "do not restrict
        # this column": '' for status and type, 'All' for subject (its list is
        # rebuilt from the payload by _populate_subjects, which needs a value
        # it can compare against). _build_filters is the one place that maps
        # either sentinel back onto "leave this column alone".
        self._status_dd = DropDown(items=[('All status', '')] + list(_STATUSES))
        self._status_dd.set_event_handler('change', self._on_filter_change)
        self._type_dd = DropDown(items=[('All types', '')] + list(_TYPES))
        self._type_dd.set_event_handler('change', self._on_filter_change)
        # Unticked by default, matching the server's default: FR06 says
        # finished work is hidden until the student asks for it.
        self._show_completed_cb = CheckBox(text='Show completed', role='pill')
        self._show_completed_cb.set_event_handler('change', self._on_filter_change)
        self._sort_dd = DropDown(items=list(_SORTS))
        # FR07's default, set here rather than left to the server's fallback so
        # the control on screen agrees with the order actually rendered.
        self._sort_dd.selected_value = 'due_date'
        self._sort_dd.set_event_handler('change', self._on_filter_change)
        body.add_component(make_toolbar(
            self._subject_dd, self._status_dd, self._type_dd,
            self._show_completed_cb, self._sort_dd))

        # --- 3. banner slots ---
        # Two empty panels, added now and filled (or left empty) on every
        # refresh. Reserving the slot here rather than inserting a banner into
        # `body` later keeps the ordering fixed: the tip always sits above the
        # exam chip, whichever of them happens to be showing.
        self._hint_panel = ColumnPanel()
        body.add_component(self._hint_panel)
        self._exam_chip_panel = ColumnPanel()
        body.add_component(self._exam_chip_panel)

        # --- 4. three-panel body (list | calendar | upcoming) ---
        # role='dashgrid' is what the stylesheet's media query targets to stack
        # these three columns on a narrow screen.
        # Widths 5/4/3 of Bootstrap's 12 columns: the list is the panel a
        # student reads, the calendar needs enough room for seven cells side by
        # side, and the 30-day sidebar is a glance.
        grid = GridPanel(role='dashgrid')
        self._list_panel = ColumnPanel(role='panel')
        self._calendar_panel = ColumnPanel(role='panel')
        self._upcoming_panel = ColumnPanel(role='panel')
        grid.add_component(self._list_panel, row='main', col_xs=0, width_xs=5)
        grid.add_component(self._calendar_panel, row='main', col_xs=5, width_xs=4)
        grid.add_component(self._upcoming_panel, row='main', col_xs=9, width_xs=3)
        body.add_component(grid)

        # The three panels are empty until this runs. Loading from __init__
        # rather than from a form_show handler means the one round-trip starts
        # as early as it can, which is the whole of NFR01's render budget.
        self._refresh()

    # --- data --------------------------------------------------------------
    def _build_filters(self):
        """Read the four filter controls into the dict get_dashboard_data wants.

        Returns {'show_completed': bool} plus, for each control that is not on
        its "all" entry, a ONE-ITEM list under 'subjects' / 'statuses' /
        'types'. Lists rather than bare values because the server's filter
        contract takes a list per column and ORs its members; these controls are
        single-select, so each list has one member, and the shape still matches
        what _safe_filters and _list_assessments_impl expect (they combine the
        three columns with AND — FR06).

        A key is OMITTED rather than sent empty when its control is on "all":
        an empty list would reach q.any_of() and match nothing, which would show
        the student an empty dashboard instead of everything.

        Note what is NOT sent: 'month'. The calendar's prev/next arrows move the
        grid only. The list and the sidebar deliberately keep showing the whole
        picture, because "what have I got on" is not a question about the month
        that happens to be on screen.
        """
        # 1. show_completed is the one key always sent, because its two states
        #    are both instructions ("hide completed" / "show them"), not
        #    "filter" and "no filter". bool() rather than the raw .checked so a
        #    CheckBox that has never been touched sends False, not None.
        f = {'show_completed': bool(self._show_completed_cb.checked)}
        # 2. Subject. 'All' is this dropdown's "no restriction" sentinel, and
        #    the empty test in front of it covers a dropdown with no selection
        #    at all — which _populate_subjects can leave behind for one moment
        #    while it is rebuilding the option list.
        subj = self._subject_dd.selected_value
        if subj and subj != 'All':
            f['subjects'] = [subj]
        # 3. Status and type, whose sentinel is '' instead. Same shape, so all
        #    three columns are narrowed the same way and the server can apply
        #    one rule to the lot.
        stat = self._status_dd.selected_value
        if stat:
            f['statuses'] = [stat]
        typ = self._type_dd.selected_value
        if typ:
            f['types'] = [typ]
        return f

    def _refresh(self):
        """Re-fetch the whole dashboard and redraw every part of it.

        The single entry point for putting data on this screen: __init__ calls
        it once, and every control, dialog and month arrow ends by calling it
        again. Nothing on this form patches a component in place, so the list,
        the calendar and the sidebar are always three views of one payload and
        cannot drift apart.

        Reads self._current_month (the month the arrows have walked to, or None
        for "the server picks today's"), the filter controls and the sort
        dropdown. Writes self._today, and through _render_calendar the day
        buckets and exam days. Returns nothing.

        On failure it shows the error and leaves the page empty rather than
        raising: a dropped connection is an ordinary event on a school network,
        and the student needs a Retry button, not a stack trace.
        """
        try:
            data = anvil.server.call('get_dashboard_data',
                                     month=self._current_month,
                                     filters=self._build_filters(),
                                     # The server whitelists this key and falls
                                     # back to due_date itself; the 'or' just
                                     # covers a dropdown with nothing selected.
                                     sort={'by': self._sort_dd.selected_value or 'due_date'})
        except Exception as e:
            # Bare `except Exception` on purpose. Anything the call can raise —
            # a network drop, an Anvil platform error, a ValueError from the
            # server's own guards — has the same answer on this screen, and
            # none of them should leave a half-drawn dashboard behind.
            # Clear ALL three panels: leaving the calendar and the 30-day list
            # showing pre-failure data next to an error message reads as though
            # part of the page is still live.
            self._list_panel.clear()
            self._calendar_panel.clear()
            self._upcoming_panel.clear()
            toast_error("Couldn't load your dashboard: %s" % e)
            # The toast fades; the empty state does not, so the retry is still
            # reachable a minute later. It is put in the list panel because
            # that is the one the eye goes to first.
            self._list_panel.add_component(make_empty_state(
                "Couldn't load your dashboard",
                'Check your connection and try again.',
                'Retry', self._refresh))
            return
        # From here on the payload is trusted: every value in it has already
        # been through the server's safe_* guards, so each renderer below reads
        # with .get() and a documented default and never re-validates.

        # 1. Today first — _render_calendar needs it to ring the right cell,
        #    and it is the user's today (their timezone), not the browser's.
        self._today = from_iso(data.get('today'))
        # 2. The subject filter's options come from the data, so a subject the
        #    student no longer has cannot stay in the list once it is gone.
        self._populate_subjects(data.get('subjects', []))
        # 3. Then the two banner slots, in the order they sit on screen, and
        #    finally the three panels.
        self._render_hint(data.get('settings', {}))
        self._render_exam_chip(data.get('next_exam'))
        self._render_list(data.get('assessment_list', []))
        self._render_calendar(data.get('calendar', {}))
        self._render_upcoming(data.get('upcoming', []))

    def _render_hint(self, settings):
        """FR15 discoverability: nudge until school terms are configured.

        settings: the payload's 'settings' dict; only 'school_terms' is read,
        which holds the term start dates or is empty when they have never been
        entered. Draws nothing once they have been, and returns nothing.

        The banner exists because "term 3 week 5" is the phrasing Will actually
        uses, and the parser can only resolve it once the terms are known — so
        the feature has to advertise the setting it depends on.
        """
        # Cleared unconditionally, before the test: this runs on every refresh,
        # so the banner has to disappear the moment the terms are saved.
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
        """Countdown chip for the next VCE written exam (spec §13).

        next_exam: the payload's 'next_exam' — {'subject', 'paper',
        'days_remaining' (int >= 0), 'urgency_band', ...} — or None when the
        student has no examinable subject, has not finished onboarding, or has
        sat every paper. All three are ordinary states, so None simply draws
        nothing rather than being treated as missing data.

        Returns nothing; it fills (or empties) self._exam_chip_panel.
        """
        self._exam_chip_panel.clear()
        if not next_exam:
            return
        days = next_exam.get('days_remaining')
        # Words for the two counts a number reads badly for: "in 0 days" and
        # "in 1 days". Everything past that is a plain count, which is what a
        # student wants when the exam is weeks away.
        if days == 0:
            when = 'today'
        elif days == 1:
            when = 'tomorrow'
        else:
            when = 'in %d days' % days
        go = Link(text='Exam timetable', role='t-accent')
        go.set_event_handler('click', lambda **e: navigate('exams'))
        self._exam_chip_panel.add_component(make_banner(
            Label(text='Next exam', role='sectionhead'),
            Label(text='%s — %s' % (next_exam.get('subject'), next_exam.get('paper')),
                  role='cardtitle'),
            make_band_chip(when, next_exam.get('urgency_band')),
            go))

    def _populate_subjects(self, subjects):
        """Rebuild the subject filter, preserving the current choice.

        subjects: the payload's 'subjects' list — the student's locked subjects
        first, then any subject only older rows still use. Returns nothing.

        Rebuilt on every refresh because the list is derived from the data: a
        subject added in Settings has to appear here without a reload, and one
        that no longer exists has to go.

        Assigning to .items resets the DropDown's selection, so the current
        choice is read first and put back afterwards — otherwise every refresh
        would silently drop the student back to "All subjects" mid-filter. The
        `in values` test is what handles the case that makes this awkward: the
        subject being filtered on is the one that just disappeared, and there is
        nothing to restore, so it falls back to 'All' rather than leaving the
        control showing a value the server would no longer accept.
        """
        current = self._subject_dd.selected_value
        # 'All' is prepended here rather than lived in the payload, because it
        # is a UI sentinel (see _build_filters), not a subject.
        items = [('All subjects', 'All')] + [(s, s) for s in subjects]
        values = [v for _, v in items]
        self._subject_dd.items = items
        self._subject_dd.selected_value = current if current in values else 'All'

    # --- list panel --------------------------------------------------------
    def _render_list(self, rows):
        """Draw the left panel: a header, then one card per assessment.

        rows: the payload's 'assessment_list' — already filtered and sorted by
        the server. Rendered in the order given, because re-sorting here could
        disagree with the sort dropdown the student can see. Returns nothing.
        """
        self._list_panel.clear()
        # "12 shown", not "12 assessments": the number is of what survived the
        # filters, and saying "shown" is what stops it being read as a total.
        # Suppressed entirely on an empty list, where the empty state says it.
        count = '%d shown' % len(rows) if rows else None
        self._list_panel.add_component(
            make_section_header('Your assessments', count))
        if not rows:
            # FR07 asks for a message rather than a blank panel when nothing
            # matches. The wording assumes a new student rather than an
            # over-filtered one, because with the filters left alone (the
            # default view) that is the only way this branch is reached.
            self._list_panel.add_component(make_empty_state(
                'Nothing here yet',
                'Type an assessment above and press Parse, or add one manually.',
                'Add manually', self._on_add_click))
            return
        for a in rows:
            self._list_panel.add_component(self._make_card(a))

    def _make_card(self, a):
        """One assessment row. The card's left edge carries the urgency band, so
        the list scans by colour without a decorative dot on every line.

        a: one assessment dict from the payload (see the shape documented at the
        top of this file). Returns the ColumnPanel; the caller adds it.

        Two rows: what it is, then when it is due and what can be done to it.
        The actions live on the card rather than behind a menu because the SAT's
        usability criterion counts clicks — changing a status is one interaction
        from the dashboard, with no dialog at all.
        """
        band = a.get('urgency_band', 'distant')
        # 'distant' is the neutral band, so a row with no usable due date falls
        # back to no colour rather than to an alarming one.
        card = make_list_card(band)

        # Row 1: title, then the tags that identify it.
        # Each chip is added only when there is something to put in it — an
        # empty chip is a grey blob that says nothing.
        top = make_row(Label(text=a.get('title') or '(untitled)', role='cardtitle'))
        if a.get('subject'):
            top.add_component(make_chip(a['subject']))
        # The stored value ('sac') drawn as its label ('SAC'). The second
        # argument is the raw value: if a row somehow holds a type outside the
        # enum, the card shows what is actually stored rather than hiding it.
        type_label = _TYPE_LABELS.get(a.get('type'), a.get('type') or '')
        if type_label:
            top.add_component(make_chip(type_label))
        conf = a.get('confidence')
        if conf:
            # FR17 audit trail: this row came from the parser, at this confidence.
            # None on a hand-typed row, which is why the chip is conditional —
            # its absence is what says "a person entered this".
            top.add_component(make_chip('parsed · %s' % conf,
                                        CONF_TONE.get(conf)))
        card.add_component(top)

        # Row 2: when it's due, how much it's worth, and the actions.
        # The due line is tinted with the same band as the card edge (role
        # 't-overdue', 't-soon', ...), so the colour and the words agree — FR21
        # asks for colour, FR09 for the count, and neither carries it alone.
        bottom = make_row(Label(text=self._due_text(a),
                                role=band_role(band, 't')))
        if a.get('weight') is not None:
            # '%g' rather than '%s' or '%.1f': it prints 25 for 25.0 and 12.5
            # for 12.5, so a whole-number weighting is not shown as "25.0%".
            # Guarded on `is not None` because a weight of 0 is a real value.
            bottom.add_component(Label(text='%g%% of grade' % a.get('weight'),
                                       role='caption'))
        status_dd = DropDown(items=list(_STATUSES))
        status_dd.selected_value = a.get('status') or 'not_started'
        # Every handler on this card binds the row id as a DEFAULT ARGUMENT
        # rather than closing over `a`. The loop in _render_list rebinds `a` on
        # each pass, so a plain closure would leave all of these pointing at
        # the last assessment drawn — the classic late-binding bug, and here it
        # would mean the Delete button on any card deleting the bottom one.
        # `dd` is bound the same way so the handler reads its own dropdown.
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
        """Single-action status change from the card (EC-UX-05).

        assessment_id: str, the row id bound to this card's dropdown.
        new_status: one of _STATUSES' stored values ('not_started',
        'in_progress', 'completed'). Returns nothing.

        Saves immediately, with no dialog and no Save button: marking something
        done is the most frequent thing a student does on this screen, and
        making it a two-step flow is what stops the list being kept up to date.
        The server re-checks ownership and the value itself (FR04, NFR03), so
        nothing is trusted just because it came from a dropdown this form drew.
        """
        if not new_status:
            return
        try:
            anvil.server.call('update_assessment', assessment_id, {'status': new_status})
        except Exception as e:
            toast_error("Couldn't update status: %s" % e)
        # Deliberately OUTSIDE the except, so it runs on failure too. The
        # dropdown has already moved to the new value in the browser; if the
        # save did not happen, only a refresh puts it back to what is actually
        # stored, and a control showing a status the database does not hold is
        # worse than a slow update. On success the refresh is needed anyway,
        # because completing something can drop it out of the filtered list.
        self._refresh()

    def _due_text(self, a):
        """The due line for a card: '21 Mar 2026 · in 4 days' (FR09).

        a: one assessment dict. Returns a string, never None.

        The date half is 'due_display', already formatted 'DD MMM YYYY' by the
        server (NFR08) — this form does not format dates, so the same date reads
        identically here, in the day dialog and on the exams page.

        The countdown half comes from 'days_remaining', which is None when the
        row has no due date or one the server judged implausible. That case
        returns the date alone rather than inventing a count, so a row with a
        bad date still shows what it does know.
        """
        due = a.get('due_display') or 'no date'
        days = a.get('days_remaining')
        if days is None:
            return due
        # Ordered soonest-first to match FR21's band order, and each branch
        # picks its own words: a negative count is stated as overdue rather than
        # printed as "in -3 days", and 0 is "today" rather than "in 0 days".
        if days < 0:
            # -days flips the sign for display; the '' / 's' picks the singular
            # for exactly one day, so it never reads "1 days overdue".
            return '%s · %d day%s overdue' % (due, -days, '' if days == -1 else 's')
        if days == 0:
            return '%s · today' % due
        return '%s · in %d day%s' % (due, days, '' if days == 1 else 's')

    # --- calendar panel ----------------------------------------------------
    def _render_calendar(self, cal):
        """Draw the centre panel: the month grid (FR08) with urgency tints (FR21).

        cal: the payload's 'calendar' dict — 'year', 'month' (1-12), 'weeks'
        (the rows calendar.monthcalendar() produced, 0 for a square outside the
        month), 'cell_colours', 'day_buckets' and 'exam_days' (all keyed by the
        day number as a string). Returns nothing.

        Side effect worth knowing about: it stores day_buckets and exam_days on
        the form, because _on_day_click fires long after this call has returned
        and needs the data the grid was drawn from.

        The whole month is rebuilt from scratch on every refresh. Redrawing 30-odd
        small cells is cheaper than working out which of them changed, and it
        means a cell can never keep a tint the data no longer justifies.
        """
        self._calendar_panel.clear()
        # 1. Unpack the payload, defaulting each part to empty. A month with
        #    nothing in it is normal — a January with no SACs is not an error —
        #    so every one of these degrades to "draw the grid, draw no markers"
        #    rather than being treated as missing data.
        year = cal.get('year')
        month = cal.get('month')
        weeks = cal.get('weeks') or []
        colours = cal.get('cell_colours') or {}
        self._day_buckets = cal.get('day_buckets') or {}
        self._exam_days = cal.get('exam_days') or {}
        # 2. Remember the displayed month for prev/next arithmetic. Taken from
        #    the payload rather than from self._current_month, because on the
        #    first load that is still None ("server, pick today's month") and
        #    only the response knows which month that turned out to be.
        self._displayed_year, self._displayed_month = year, month

        # 3. The month header: ‹ November 2026 ›. Both arrows share
        #    _change_month with a delta of -1/+1, so the wrap from December to
        #    January is worked out in one place instead of two.
        prev_btn = Button(text='‹', role='iconbtn')
        prev_btn.set_event_handler('click', lambda **e: self._change_month(-1))
        next_btn = Button(text='›', role='iconbtn')
        next_btn.set_event_handler('click', lambda **e: self._change_month(1))
        # _MONTH_NAMES is 1-indexed, so `month` drops straight in; the guards
        # and the .strip() cover the empty payload, where the header reads
        # 'Calendar' with a blank line under it rather than 'None None'.
        label = '%s %s' % (_MONTH_NAMES[month] if month else '', year or '')
        self._calendar_panel.add_component(make_section_header('Calendar'))
        self._calendar_panel.add_component(make_row(
            prev_btn, Label(text=label.strip(), role='cardtitle'), next_btn))

        # 4. The weekday strip. role='calhead' is styled by the SAME 7-column
        #    grid rule as the cells below it, which is what keeps Mon..Sun
        #    lined up with the columns rather than merely near them.
        header = FlowPanel(role='calhead')
        for name in _WEEKDAY_HEADERS:
            header.add_component(Label(text=name))
        self._calendar_panel.add_component(header)

        # 5. One flat list of cells; the CSS grid wraps them into weeks.
        #    role='calgrid' is the whole trick: the stylesheet turns this
        #    FlowPanel's inner gutter into `grid-template-columns:repeat(7,1fr)`,
        #    so the cells are laid out seven to a row by CSS. Nothing here has to
        #    know how many weeks the month has — monthcalendar() returns 4, 5 or
        #    6 rows depending on which weekday the 1st falls on and how long the
        #    month is, and the loop simply empties all of them into one panel.
        #    A February that starts on a Monday gives 28 cells, a long month that
        #    starts on a Sunday gives 42, and the grid draws either correctly.
        grid = FlowPanel(role='calgrid')
        for week in weeks:
            for day in week:
                grid.add_component(self._make_day_cell(day, colours, year, month))
        self._calendar_panel.add_component(grid)

    def _make_day_cell(self, day, colours, year, month):
        """One calendar square. Days outside the month are invisible spacers.

        day: int from a monthcalendar() row — 1-31, or 0 for a square that
        belongs to the previous or next month.
        colours: the payload's 'cell_colours', {'<day>': urgency band}. A day
        with nothing due is simply absent from it.
        year, month: the month being drawn, needed only to decide whether `day`
        is genuinely today rather than the same number in some other month.

        Returns the component for that square: a Link when the day has
        something on it, otherwise a plain ColumnPanel. Reads self._day_buckets,
        self._exam_days and self._today; touches no table of its own.
        """
        # A 0 still has to occupy its column, or the days after it would shuffle
        # left and every weekday in the month would be wrong. It is a Label
        # with a space rather than nothing at all because an empty component
        # collapses — and the 'calcell-blank' role is `visibility:hidden`, not
        # `display:none`, for exactly the same reason: hold the column, show
        # nothing in it.
        if day == 0:
            return Label(text=' ', role='calcell-blank')

        # What this day holds. The server has already reduced the bucket to the
        # MOST urgent band it contains, so the tint is not recomputed here — a
        # day with one overdue essay and four distant ones reads as overdue,
        # which is the whole point of colouring the month (FR21).
        band = _cell(colours, day)
        items = _cell(self._day_buckets, day) or []
        exams = _cell(self._exam_days, day) or []

        # Only a day with something on it is clickable. A Link on every square
        # would give the whole month a hover affordance that mostly leads to a
        # dialog saying "nothing here" — the pointer should promise something.
        # band_role() maps 'overdue' -> 'calcell-overdue' etc.; `band` is None
        # for an empty day, which gets the plain 'calcell' and no tint.
        role = band_role(band, 'calcell') if band else 'calcell'
        if items or exams:
            cell = Link(role=role)
            # `day` bound as a default argument: this lambda outlives the loop
            # in _render_calendar, and without the binding every cell would
            # open the last day of the month.
            cell.set_event_handler('click', lambda d=day, **e: self._on_day_click(d))
        else:
            cell = ColumnPanel(role=role)

        # Year, month AND day all have to match before a cell is "today".
        # Comparing the day number alone would mark the 14th of every month the
        # arrows walk to. self._today is None only when the payload carried no
        # date at all, and then no cell is marked rather than the wrong one.
        is_today = (self._today is not None and year == self._today.year
                    and month == self._today.month and day == self._today.day)
        # The 'calnum-now' role draws the number on a filled accent pill, so
        # today is found at a glance without a second marker competing for the
        # ~40px the square has.
        cell.add_component(Label(text=str(day),
                                 role='calnum-now' if is_today else 'calnum'))
        # A cell is only ~40px wide, so the markers have to be single glyphs.
        # The count badge says how much is due; the tint says how urgent.
        if items:
            cell.add_component(Label(text=str(len(items)), role='calcount'))
        if exams:
            # A glyph as well as colour, so an exam day never signals by colour
            # alone (the purple tint is invisible to some students).
            cell.add_component(Label(text='▲', role='calexam'))
        return cell

    def _on_day_click(self, day):
        """Show the clicked calendar day's assessments and VCE exams.

        day: int, 1-31, bound to the cell that was clicked.

        A read-only dialog: it exists because a ~40px square can show a count
        but not what the count is of. Nothing here can be edited — the student
        closes it and uses the card in the list — so it returns nothing and the
        dashboard behind it does not need refreshing afterwards.

        Reads the buckets from the form rather than being passed them, because
        this fires from a click, long after _render_calendar returned. That also
        means it shows the month currently on screen, which is the only month
        whose cells are clickable.
        """
        items = _cell(self._day_buckets, day) or []
        exams = _cell(self._exam_days, day) or []
        panel = ColumnPanel()
        if not items and not exams:
            # Only reachable from a stale cell — _make_day_cell makes an empty
            # day unclickable — but the dialog still has to say something
            # rather than open blank.
            panel.add_component(make_empty_state(
                'Nothing due this day',
                'No assessments or exams fall on this date.'))
        # Exams first, and above the assessments: a VCE written exam is fixed
        # by VCAA and is the thing on the day that cannot be moved.
        for label in exams:
            # No band on this card: an exam's date is not negotiable, so an
            # urgency colour would add nothing. `label` is already the whole
            # 'Subject — paper' string, built server-side.
            row = make_list_card()
            row.add_component(make_row(make_chip('VCE exam', 'exam'),
                                       Label(text=label, role='cardtitle')))
            panel.add_component(row)
        for a in items:
            # Same band and same chips as the list card, minus the controls, so
            # a row is recognisably the same row in both places.
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
        # The dialog title is the day's own formatted date, lifted from the
        # first assessment on it (they all fall on the same date, so any of them
        # would do). It is borrowed rather than formatted here so the dialog
        # says exactly what the card behind it says (NFR08). Falls back to
        # 'This day' when there is nothing to borrow from — an exam-only day, or
        # a row whose date the server could not format.
        title = (items[0].get('due_display') if items else '') or 'This day'
        # large=False: it holds a handful of one-line rows. The single Close
        # button returns None, which no caller looks at — this dialog changes
        # nothing.
        alert(panel, title=title, large=False, buttons=[('Close', None)])

    def _change_month(self, delta):
        """Step the calendar a month back (-1) or forward (+1) and reload.

        delta: int, -1 or +1 from the ‹ / › buttons. Returns nothing; it sets
        self._current_month to 'YYYY-MM' and calls _refresh().

        Only the CALENDAR follows the arrows. The list and the sidebar are not
        month-scoped (_build_filters sends no 'month' key), so walking to next
        March does not hide what is due this week.
        """
        # getattr with a None default because these two are created by
        # _render_calendar, not by __init__: an arrow clicked before the first
        # payload has landed has no month to step from, so it does nothing
        # rather than raising AttributeError.
        y = getattr(self, '_displayed_year', None)
        m = getattr(self, '_displayed_month', None)
        if y is None or m is None:
            return
        m += delta
        # while, not if: correct for any delta, so a future "jump six months"
        # button could reuse this untouched. Rolling the year here rather than
        # letting the server sort it out keeps the arrows' behaviour visible in
        # one place — and the server range-checks the result anyway, refusing a
        # year outside 2000-2100.
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        # Zero-padded to 'YYYY-MM', the same form the server normalises a month
        # back to. _parse_month would accept '2026-3' as well, but writing it
        # one way here means the value held on the form always looks like the
        # one the server echoes.
        self._current_month = '%04d-%02d' % (y, m)
        self._refresh()

    # --- upcoming panel ----------------------------------------------------
    def _render_upcoming(self, upcoming):
        """Draw the right-hand sidebar: what is due in the next 30 days (FR09).

        upcoming: the payload's 'upcoming' list — already narrowed server-side
        to not-completed rows due between today and 30 days out, soonest first.
        Returns nothing.

        Deliberately NOT affected by the filter row: this column answers "what
        is coming", and a subject filter set for the list should not be able to
        hide a SAC that is due on Thursday.
        """
        self._upcoming_panel.clear()
        self._upcoming_panel.add_component(make_section_header('Next 30 days'))
        if not upcoming:
            # "All clear" rather than "Nothing found": an empty next-30-days is
            # good news, and the panel should read that way.
            self._upcoming_panel.add_component(make_empty_state(
                'All clear',
                'Nothing is due in the next 30 days.'))
            return
        # This column is the narrowest on the page, so the date and the title
        # are stacked rather than laid side by side — side by side they wrap
        # mid-row and the dates stop lining up.
        for a in upcoming:
            band = a.get('urgency_band', 'distant')
            item = make_list_card(band)
            item.add_component(Label(text=a.get('due_display') or '', role='micro'))
            item.add_component(Label(text=a.get('title') or '(untitled)',
                                     role='caption'))
            self._upcoming_panel.add_component(item)

    # --- handlers ----------------------------------------------------------
    # The four dialog handlers below share one shape: import the editor form
    # inside the function, open it with alert(), and refresh if it returns
    # something truthy.
    #
    #   * The import is LOCAL, not at the top of the file — the same convention
    #     NotesForm uses for NoteEditorForm. A form that is only ever opened as
    #     a dialog is not loaded until something opens one, which keeps it off
    #     the dashboard's first-paint path (NFR01) and leaves the two modules
    #     with no import relationship in either direction to go circular.
    #   * buttons=[] means the dialog has no buttons of Anvil's. The form draws
    #     its own Save and Cancel and closes itself by raising 'x-close-alert',
    #     whose value becomes alert()'s return.
    #   * That return is the id of the assessment written, or in bulk mode the
    #     NUMBER of rows inserted. Neither is used for anything except its
    #     truthiness — it only has to answer "did anything change?". Cancel
    #     returns None and the dashboard is left alone, which is what stops a
    #     glance-and-close costing a round-trip.

    def _on_filter_change(self, **event_args):
        """Any filter or sort control changed: ask the server again (FR06, FR07).

        One handler for all five controls, because filtering and sorting both
        happen server-side — the client has no unfiltered copy to narrow, so
        every change means the same round-trip. `**event_args` is Anvil's event
        payload, which nothing here needs.
        """
        self._refresh()

    def _on_parse_click(self, **event_args):
        """Parse the sentence in the input bar and open it for confirmation (FR01).

        Bound to both the Parse button and Enter in the text box.

        parse_text never writes: it returns the fields it recognised, and the
        student confirms or corrects them in the editor's 'preview' mode before
        anything is stored (FR17). That is the point of the two-step flow — the
        parser is allowed to be wrong.
        """
        text = (self._nlp_tb.text or '').strip()
        if not text:
            # A warning, not an error: an empty box is the student not having
            # typed yet, and colouring that red teaches them to ignore red.
            # Returning early also saves a round-trip the server would refuse.
            toast_warn("Type an assessment first.")
            return
        try:
            parsed = anvil.server.call('parse_text', text)
        except Exception as e:
            # parse_text raises for an empty or over-long input with a message
            # written for the student, so it is shown as it is.
            toast_error("Couldn't parse: %s" % e)
            return
        from ..AssessmentEditorForm import AssessmentEditorForm
        result = alert(AssessmentEditorForm(mode='preview', prefill=parsed),
                       title='', large=True, buttons=[])
        if result:
            # The box is cleared only on a SAVE. If the student cancelled, their
            # sentence is still there to be edited and re-parsed rather than
            # retyped.
            self._nlp_tb.text = ''
            self._refresh()

    def _on_add_click(self, **event_args):
        """Open the blank manual-entry form (FR03).

        Also the action button on the list panel's empty state, which is why it
        takes Anvil's event kwargs and is called with none from there.
        """
        from ..AssessmentEditorForm import AssessmentEditorForm
        result = alert(AssessmentEditorForm(mode='create'), title='', large=True, buttons=[])
        if result:
            self._refresh()

    def _on_bulk_click(self, **event_args):
        """Open the paste-many-lines dialog (FR02).

        Bulk mode may have written rows even when the student then cancels, so
        it returns the count inserted rather than None in that case — which is
        why the refresh below is driven by the return value and not by which
        button was pressed.
        """
        from ..AssessmentEditorForm import AssessmentEditorForm
        result = alert(AssessmentEditorForm(mode='bulk'), title='', large=True, buttons=[])
        if result:
            self._refresh()

    def _on_edit_click(self, assessment_id):
        """Open one assessment for editing (FR04).

        assessment_id: str, the row id bound to the card's Edit button. The
        editor loads the row itself; only the id crosses, and the server
        re-checks that the row belongs to this user before returning it (NFR03).
        """
        from ..AssessmentEditorForm import AssessmentEditorForm
        result = alert(AssessmentEditorForm(mode='edit', assessment_id=assessment_id),
                       title='', large=True, buttons=[])
        if result:
            self._refresh()

    def _on_delete_click(self, assessment_id):
        """Delete one assessment after confirming (FR05).

        assessment_id: str, the row id bound to the card's Delete button.

        FR05 requires the prompt, and it is asked BEFORE the call so a mis-click
        costs nothing. Deletion is the only irreversible thing this screen can
        do — linked notes survive it, but the assessment itself does not.
        """
        if not confirm('Delete this assessment?'):
            return
        try:
            anvil.server.call('delete_assessment', assessment_id)
        except Exception as e:
            # Returns without refreshing: nothing changed, so redrawing the
            # whole dashboard would only make the failure look like an update.
            toast_error("Couldn't delete: %s" % e)
            return
        self._refresh()
