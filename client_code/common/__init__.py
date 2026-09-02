import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Client-side shared helpers — navigation, session state, and the UI kit.

Three jobs:

1. **Navigation** (spec §4). `navigate()` sets the URL hash and re-enters the
   router; Main separately listens for `hashchange` so the browser's Back and
   Forward buttons and pasted deep links reach the same router.
   `make_top_bar()` builds the shared nav and marks the active tab.

2. **Session state** (spec §11/§12). One `get_settings` round-trip per session,
   cached, so the router can gate onboarding and apply the theme without a
   server call per navigation. `apply_theme()` flips the dark palette.

3. **The UI kit** (spec §14). `make_card`, `make_chip`, `make_empty_state`,
   `toast`, ... Every form composes these instead of repeating styling, and
   every colour lives in the stylesheet (anvil.yaml native_deps.head_html) as a
   CSS variable — no form hardcodes a hex colour, which is what lets the whole
   app switch between light and dark themes correctly.

See IMPLEMENTATION_SPEC.md section 0 (Common helpers), section 3 (Client Forms)
and section 14 (Design system).
"""

import anvil
import anvil.server
import anvil.users
import datetime
from anvil import (
    ColumnPanel, FlowPanel, Label, Link, Button, CheckBox,
    Notification, open_form,
)

# --- urgency bands ----------------------------------------------------------
# Mirror of the band NAMES _datetime._urgency_band can return (the client cannot
# import server modules; keep in sync). Only the names cross the boundary now —
# what a band LOOKS like is decided by the stylesheet, so all we do here is map
# a band onto the role that paints it.
URGENCY_BANDS = ('overdue', 'today', 'soon', 'distant')

# The server's band name 'today' would collide with the calendar's "is today"
# styling, so the stylesheet calls that tone 'duetoday'.
_BAND_ROLE = {
    'overdue': 'overdue', 'today': 'duetoday',
    'soon': 'soon', 'distant': 'distant',
}


def band_role(band, prefix):
    """'overdue', 'chip' -> 'chip-overdue'. Unknown bands fall back to distant."""
    return '%s-%s' % (prefix, _BAND_ROLE.get(band, 'distant'))


# Parser confidence (FR17) -> chip tone. Shared so the same value never reads
# as one colour on the dashboard and another in the editor.
CONF_TONE = {'HIGH': 'ok', 'MEDIUM': 'warn', 'LOW': 'bad'}


# --- date formatting ---------------------------------------------------------
# Skulpt (the browser-side Python) has gaps in strftime/date.isoformat, so the
# whole app formats dates from their components by hand. These three lived in
# four different forms with three slightly different implementations; they are
# shared here so a date reads identically wherever it is shown.

MONTHS_ABBR = ('', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def from_iso(s):
    """'YYYY-MM-DD' (or a longer ISO timestamp) -> date, or None."""
    if not s or not isinstance(s, str) or len(s) < 10:
        return None
    try:
        return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except (ValueError, TypeError):
        return None


def fmt_date(d):
    """date -> 'DD Mon YYYY'. None renders as 'no date'."""
    if d is None:
        return 'no date'
    return '%02d %s %d' % (d.day, MONTHS_ABBR[d.month], d.year)


def to_iso(d):
    """date -> 'YYYY-MM-DD'."""
    return '%04d-%02d-%02d' % (d.year, d.month, d.day)


# --- navigation -------------------------------------------------------------

def navigate(hash_value):
    """Route to `hash_value` (spec §4).

    Deliberately unconditional: set the hash, then re-enter the router. It would
    be tempting to let Main's hashchange listener do the rendering, but that
    would make every navigation in the app depend on a browser event firing —
    and the listener ignores events raised while a dialog is fading out, so a
    navigation immediately after alert()/confirm() (signing in, finishing
    onboarding) could be dropped with nothing to fall back on.

    Rendering here instead means the listener only has to handle what it is
    actually for: the Back/Forward buttons and pasted #links. Main records the
    hash it last routed to and ignores the echo of our own write, so this does
    not render twice.
    """
    anvil.set_url_hash(hash_value)
    open_form('Main')


def _sign_out():
    anvil.users.logout()
    clear_session_settings()
    apply_theme('light')
    navigate('login')


# Route name -> nav label, in display order.
_NAV_ITEMS = (
    ('dashboard', 'Dashboard'),
    ('notes', 'Notes'),
    ('exams', 'Exams'),
    ('settings', 'Settings'),
    ('import-export', 'Import & export'),
)


def make_top_bar(active=None):
    """The shared top navigation bar.

    `active` is the caller's route name ('notes', 'exams', ...); that tab is
    drawn with the active role so the student can always see where they are.
    'Sign out' is pushed to the right edge by the stylesheet (it styles the
    last item of the bar), so no spacer component is needed.
    """
    bar = FlowPanel(role='topbar')

    brand = Link(text='DotPoint', role='brand')
    brand.set_event_handler('click', lambda **e: navigate('dashboard'))
    bar.add_component(brand)

    for route, label in _NAV_ITEMS:
        role = 'navitem-active' if route == active else 'navitem'
        link = Link(text=label, role=role)
        link.set_event_handler('click', lambda h=route, **e: navigate(h))
        bar.add_component(link)

    sign_out = Button(text='Sign out', role='secondary')
    sign_out.set_event_handler('click', lambda **e: _sign_out())
    bar.add_component(sign_out)

    return bar


# --- per-session settings cache (spec §11/§12) -------------------------------
# One get_settings round-trip per session; the router reads this on every
# navigation to gate onboarding and apply the theme. Writers of settings
# (SettingsForm, OnboardingForm) push the server's response back via
# set_session_settings so the cache never goes stale.

_session = {'settings': None}


def get_session_settings(refresh=False):
    if refresh or _session['settings'] is None:
        _session['settings'] = anvil.server.call('get_settings')
    return _session['settings']


def set_session_settings(settings):
    _session['settings'] = settings


def clear_session_settings():
    _session['settings'] = None


def apply_theme(theme):
    """Toggle the dark palette by flipping body.dotpoint-dark (the CSS variables
    in anvil.yaml native_deps.head_html do the rest)."""
    try:
        from anvil.js.window import document
        if theme == 'dark':
            document.body.classList.add('dotpoint-dark')
        else:
            document.body.classList.remove('dotpoint-dark')
    except Exception:
        pass  # never let theming break navigation


# --- toasts (defect 13) ------------------------------------------------------
# Every transient message in the app goes through toast(). Anvil's Notification
# is a bootstrap-notify element fixed to the top of the window; success toasts
# were observed failing to auto-dismiss and stacking over the top bar, where
# they swallowed nav clicks. Two changes fix that for good:
#   * the stylesheet moves the toast stack to the bottom-right, so even a stuck
#     toast can never cover the navigation, and
#   * we keep a reference to every live toast and dismiss it ourselves on a
#     timer, instead of trusting the notification's own.

_MAX_TOASTS = 3
_live_toasts = []


def _dismiss(note):
    """Hide a toast and forget it. Safe to call twice."""
    if note in _live_toasts:
        _live_toasts.remove(note)
    try:
        note.hide()
    except Exception:
        pass  # already gone


def toast(message, style='success', timeout=4):
    """Show a transient message. The single toast call-site for the whole app.

    style is Anvil's: 'success' | 'danger' | 'warning' | 'info'.
    """
    note = Notification(str(message), style=style, timeout=timeout)
    note.show()
    _live_toasts.append(note)

    # Never let the stack grow without bound.
    while len(_live_toasts) > _MAX_TOASTS:
        _dismiss(_live_toasts[0])

    # Own the dismissal. The small grace period lets Anvil's own timer win when
    # it does work, so a toast is never cut short.
    try:
        from anvil.js.window import setTimeout
        setTimeout(lambda: _dismiss(note), int(timeout * 1000) + 400)
    except Exception:
        pass
    return note


def toast_error(message):
    """Something failed. Shown longer than a confirmation — it has to be read."""
    return toast(message, style='danger', timeout=6)


def toast_warn(message):
    """Nothing failed; the student just has to fill something in first.

    Kept distinct from toast_error on purpose: an empty text box is not an
    error, and colouring routine nudges red teaches the student to ignore red.
    """
    return toast(message, style='warning', timeout=4)


# --- UI kit (spec §14) -------------------------------------------------------
# Small builders so each form reads as composition rather than repeated
# styling. Everything visual is decided by the role names below, which the
# stylesheet paints; nothing here sets a colour or a font size.

def make_page(*components):
    """The centred content column every signed-in page sits in."""
    panel = ColumnPanel(role='page')
    for c in components:
        panel.add_component(c)
    return panel


def make_row(*components):
    """A horizontal, vertically-centred, wrapping row of components."""
    row = FlowPanel(role='row')
    for c in components:
        row.add_component(c)
    return row


def make_toolbar(*components):
    """A row of controls (inputs + buttons) above a list or panel."""
    bar = FlowPanel(role='toolbar')
    for c in components:
        bar.add_component(c)
    return bar


def make_card(*components):
    """A surface panel: white/dark card, soft border, rounded."""
    card = ColumnPanel(role='card')
    for c in components:
        card.add_component(c)
    return card


def make_list_card(band=None):
    """A list row whose left edge carries the urgency colour.

    Returns an empty ColumnPanel for the caller to fill. `band` is an urgency
    band ('overdue' / 'today' / 'soon' / 'distant'); None gives the neutral row.
    """
    role = band_role(band, 'listcard') if band else 'listcard'
    return ColumnPanel(role=role)


def make_banner(*components):
    """A quiet full-width strip (tips, the next-exam countdown)."""
    banner = FlowPanel(role='banner')
    for c in components:
        banner.add_component(c)
    return banner


def make_page_title(title, subtitle=None):
    """The h1 of a screen, with an optional one-line explanation under it.

    Note the role is 'pagehead', not 'row': this stacks its two lines, so it
    must not pick up the horizontal row styling.
    """
    panel = ColumnPanel(role='pagehead')
    panel.add_component(Label(text=title, role='pagetitle'))
    if subtitle:
        panel.add_component(Label(text=subtitle, role='caption'))
    return panel


def make_section_header(title, hint=None):
    """A small upper-case label that opens a section, with an optional hint."""
    row = FlowPanel(role='row')
    row.add_component(Label(text=title, role='sectionhead'))
    if hint:
        row.add_component(Label(text=hint, role='micro'))
    return row


def make_chip(text, tone=None):
    """A small rounded tag. tone: None (neutral) or 'accent' / 'exam' / 'ok' /
    'warn' / 'bad' / an urgency role suffix."""
    role = 'chip' if tone is None else 'chip-%s' % tone
    return Label(text=text, role=role)


def make_band_chip(text, band):
    """A chip tinted by an urgency band ('overdue' / 'today' / ...)."""
    return Label(text=text, role=band_role(band, 'chip'))


def make_empty_state(title, hint=None, action_text=None, action_click=None):
    """What a panel shows when it has nothing to show.

    A dashed, centred block with a heading, an optional explanation and an
    optional call to action — never just a stray sentence.
    """
    panel = ColumnPanel(role='empty')
    panel.add_component(Label(text=title, role='cardtitle', align='center'))
    if hint:
        panel.add_component(Label(text=hint, role='caption', align='center'))
    if action_text and action_click:
        btn = Button(text=action_text, role='secondary', align='center')
        btn.set_event_handler('click', lambda **e: action_click())
        panel.add_component(btn)
    return panel


def make_field(label_text, component, hint=None, required=False):
    """A labelled form control: caption above, control, optional hint below.

    Returns the ColumnPanel wrapper. The wrapper carries two extras the forms use for
    validation feedback (SAT criterion 7.3 — SRS FR04 asks for errors "beside the
    offending field", not in a toast at the corner of the screen):

      * `panel.error_label` — a Label, hidden until something is wrong, that
        set_field_error() below writes into. It is created up front rather than added
        on demand so showing a message never re-flows the dialog.
      * `panel.input_component` — a back-reference to the control itself, so a caller
        holding only the wrapper can still put the cursor in the offending box.

    `required=True` appends a marker to the caption. The marker is the app's only
    signal about which fields must be filled in, so it is applied from the same place
    the label is written and cannot drift out of step with the server's rules.
    """
    panel = ColumnPanel(role='field')

    caption = label_text + ' *' if required else label_text
    panel.add_component(Label(text=caption, role='caption'))
    panel.add_component(component)

    if hint:
        panel.add_component(Label(text=hint, role='micro'))

    # Always present, never visible until there is something to say. role='fielderror'
    # is painted by the stylesheet in the same red as toast_error, so the two error
    # channels read as one system.
    error_label = Label(text='', role='fielderror', visible=False)
    panel.add_component(error_label)

    panel.error_label = error_label
    panel.input_component = component
    return panel


def set_field_error(field_panel, message):
    """Show `message` under a field built by make_field(), or clear it with None.

    Safe to call on a panel that predates this helper (it simply does nothing), so a
    form can adopt per-field errors one field at a time.
    """
    error_label = getattr(field_panel, 'error_label', None)
    if error_label is None:
        return
    error_label.text = message or ''
    # Toggling `visible` rather than the text alone means an empty message collapses
    # the row instead of leaving a blank gap under the control.
    error_label.visible = bool(message)


def clear_field_errors(*field_panels):
    """Wipe every field message before re-validating, so stale ones never linger."""
    for panel in field_panels:
        set_field_error(panel, None)


def friendly_error(exception, fallback='Something went wrong. Please try again.'):
    """Turn an exception from a server call into a sentence worth showing a student.

    The server's validators raise ValueError with text written FOR the student
    (server_code/_validation.py), so those messages are shown as they are. Anything
    else — a network drop, an Anvil platform error, a bug — carries a developer
    string, a class name or a stack fragment that would only confuse the reader, so
    it is replaced by `fallback`.

    This exists because every form used to do `toast_error(str(e))`, which meant a
    student who left the due date empty was shown the literal text
    "invalid date: None". The rubric asks for meaningful messages; this is the single
    place that decides what "meaningful" means.
    """
    message = str(exception or '').strip()

    # A message the app wrote for a person reads as a sentence: it starts with a
    # capital and ends with a full stop. Developer strings ('title required',
    # 'invalid subject') and platform errors do neither, which is a reliable enough
    # test to sort them without tagging every raise site.
    looks_written_for_a_person = (
        message
        and message[0].isupper()
        and message.endswith(('.', '!', '?'))
        and len(message) <= 300
    )
    return message if looks_written_for_a_person else fallback


def make_divider():
    """A hairline rule between two blocks of a card or dialog.

    Built from an empty ColumnPanel rather than a Spacer: Anvil's Spacer does
    not accept a `role`, so it cannot be styled. The stylesheet gives this one
    a top border and the vertical margin either side of it.
    """
    return ColumnPanel(role='divider')


# --- shared subject picker (spec §11) ----------------------------------------

class SubjectPicker(ColumnPanel):
    """Grouped multi-select over the VCE subject catalog.

    Each study is a toggle pill (a CheckBox the stylesheet redraws — see the
    'pill' role), grouped by learning area with a live count per group and a
    running total, so a student choosing 5 subjects out of ~56 can see their
    selection at a glance.

    Dumb component: the caller fetches the catalog (get_subject_catalog) and
    reads get_selection() back; the VCE rules are enforced server-side in
    set_subjects. Used by OnboardingForm and the Settings change-subjects flow.
    """

    def __init__(self, catalog, selected=None, **properties):
        super().__init__(**properties)
        selected = set(selected or [])
        self._checks = []        # [(subject, CheckBox), ...] in catalog order
        self._group_checks = {}  # group name -> [CheckBox, ...]
        self._group_counts = {}  # group name -> Label showing "2 selected"

        self._total = Label(text='', role='caption')
        self.add_component(self._total)

        for group in catalog:
            name = group['group']
            header = FlowPanel(role='row')
            header.add_component(Label(text=name, role='sectionhead'))
            count = Label(text='', role='chip-accent', visible=False)
            self._group_counts[name] = count
            header.add_component(count)
            self.add_component(header)

            row = FlowPanel(role='pickgrid')
            boxes = []
            for subject in group['subjects']:
                cb = CheckBox(text=subject, checked=subject in selected,
                              role='pill')
                cb.set_event_handler('change', self._on_toggle)
                self._checks.append((subject, cb))
                boxes.append(cb)
                row.add_component(cb)
            self._group_checks[name] = boxes
            self.add_component(row)

        self._refresh_counts()

    def _on_toggle(self, **event_args):
        self._refresh_counts()

    def _refresh_counts(self):
        """Keep the running total and each group's badge in step with the ticks."""
        total = len(self.get_selection())
        if total == 0:
            self._total.text = 'No subjects selected yet.'
        elif total == 1:
            self._total.text = '1 subject selected.'
        else:
            self._total.text = '%d subjects selected.' % total

        for name, boxes in self._group_checks.items():
            n = len([cb for cb in boxes if cb.checked])
            badge = self._group_counts[name]
            badge.text = str(n)
            badge.visible = n > 0

    def get_selection(self):
        return [s for s, cb in self._checks if cb.checked]
