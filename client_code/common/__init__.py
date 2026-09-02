import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Client-side shared helpers — everything more than one form needs.

This module is the app's shared toolbox: all nine screens import from it, and
so does the Main router. Six jobs, in the order they appear below.

1. **Shared vocabulary.** `band_role()` turns one of the server's urgency band
   names (FR21) into the stylesheet role that paints it, and `CONF_TONE` maps
   the parser's HIGH/MEDIUM/LOW confidence (FR17) onto a chip tone. Both live
   here so the same value can never read as one colour on the dashboard and a
   different one in the editor.

2. **Date formatting** (NFR08). `from_iso` / `fmt_date` / `to_iso` and
   `MONTHS_ABBR`. Skulpt (the browser-side Python) has gaps in strftime, so
   dates are assembled from their components by hand; sharing the three
   helpers is what makes every screen render "21 Mar 2025" identically.

3. **Navigation** (spec §4). `navigate()` sets the URL hash and re-enters the
   router; Main separately listens for `hashchange` so the browser's Back and
   Forward buttons and pasted deep links reach the same router.
   `make_top_bar()` builds the shared nav and marks the active tab, and
   `_sign_out()` is the one place a session is torn down.

4. **Session state and theme** (spec §11/§12). One `get_settings` round-trip
   per session, cached in `_session`, so the router can gate onboarding and
   apply the theme without a server call per navigation (NFR01).
   `apply_theme()` flips the dark palette.

5. **Toasts and error text** (spec §14.6). `toast` / `toast_error` /
   `toast_warn` are the app's only transient-message call sites, and
   `friendly_error()` decides which server exceptions are fit to show a
   student.

6. **The UI kit** (spec §14). `make_page`, `make_card`, `make_chip`,
   `make_field`, `make_empty_state`, ... plus the per-field validation
   messages (`set_field_error`) and the shared `SubjectPicker`. Every form
   composes these instead of repeating styling, and every colour lives in the
   stylesheet (anvil.yaml native_deps.head_html) as a CSS variable — no form
   hardcodes a hex colour, which is what lets the whole app switch between
   light and dark themes correctly.

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

# --- urgency bands (FR21) ---------------------------------------------------
# Mirror of the band NAMES _datetime._urgency_band can return, in the order
# _constants.URGENCY_THRESHOLDS lists them. Anvil client code cannot import a
# server module, so this is a hand copy — and unlike the subject-group mirrors
# in OnboardingForm and SettingsForm, which tests/test_constants_integrity.py
# reads with `ast` and asserts against server_code/_constants.py, nothing
# checks this one automatically. It is documentation of the vocabulary rather
# than a working constant: no form imports it, because a band always arrives
# from the server inside a row and goes straight into band_role() below.
#
# Only the NAMES cross the client/server boundary. What a band LOOKS like is
# decided entirely by the stylesheet, so all this module does is map a band
# onto the role that paints it — which is why removing every hex colour from
# client code (spec §14.7) did not have to touch the server at all.
URGENCY_BANDS = ('overdue', 'today', 'soon', 'distant')

# The server's band name 'today' would collide with the calendar's "is today"
# styling, so the stylesheet calls that tone 'duetoday'. Renaming it on the
# server instead would have meant changing a value stored in FR21's threshold
# table for the sake of a CSS class name, so the translation lives here.
_BAND_ROLE = {
    'overdue': 'overdue', 'today': 'duetoday',
    'soon': 'soon', 'distant': 'distant',
}


def band_role(band, prefix):
    """Build the stylesheet role that paints an urgency band (FR21).

    `band` is a band name from the server ('overdue' / 'today' / 'soon' /
    'distant'). `prefix` is the component family that carries the tint — the
    four in use are 'chip' (a tag), 'listcard' (a row's left edge), 'calcell'
    (a calendar day) and 't' (coloured text). So band_role('overdue', 'chip')
    returns 'chip-overdue', for which the stylesheet holds a matching
    .anvil-role-chip-overdue rule. Returns a str; raises nothing.

    An unknown band falls back to 'distant' rather than raising, and the
    fallback is a real role rather than a made-up one. Both halves matter: a
    raise here would take out the whole list being drawn, and a role with no
    stylesheet rule renders as unstyled default Anvil, which looks like a
    layout bug instead of a bad band name.
    """
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
    """'YYYY-MM-DD' (or a longer ISO timestamp) -> date, or None.

    `s` is whatever came back in a server dict, so it may be a clean date
    string, a full '2026-03-21T09:00:00' timestamp, None, or something the
    caller did not expect at all. Anything unusable returns None rather than
    raising, because the callers are all mid-render — a single bad cell must
    render as "no date" (see fmt_date) instead of killing the screen.

    Fixed slice positions are used instead of str.split('-') so a timestamp
    is handled by the same three slices as a plain date, and the len < 10
    guard is what makes those slices safe.
    """
    if not s or not isinstance(s, str) or len(s) < 10:
        return None
    try:
        return datetime.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except (ValueError, TypeError):
        # int() on non-digits raises ValueError; a well-formed but impossible
        # date ('2026-02-31') raises it from the date constructor. Both mean
        # the same thing to the caller: there is no date here.
        return None


def fmt_date(d):
    """date -> 'DD Mon YYYY' (NFR08), the app's only user-facing date format.

    `d` is a datetime.date, or None for a record with no due date — which
    renders as the words 'no date' rather than an empty string, so a blank
    cell always reads as deliberate rather than as a missing value.

    Built by hand from .day / .month / .year: strftime('%d %b %Y') would be
    the obvious way, but Skulpt's strftime is incomplete and the locale it
    would consult is the browser's, which is exactly the DD/MM vs MM/DD
    ambiguity NFR08 exists to remove. MONTHS_ABBR is indexed by month number,
    which is why it carries an unused '' at index 0.
    """
    if d is None:
        return 'no date'
    return '%02d %s %d' % (d.day, MONTHS_ABBR[d.month], d.year)


def to_iso(d):
    """date -> 'YYYY-MM-DD', the format every server callable expects.

    The inverse of from_iso, used when a form sends a DatePicker value back.
    Written out rather than using d.isoformat() for the same Skulpt reason as
    fmt_date, and zero-padded explicitly so a single-digit month or day
    cannot produce '2026-3-1' — _validation.require_date parses with
    date.fromisoformat, which rejects an unpadded string outright.
    """
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

    `hash_value` is a route name without the '#' — one of the keys of
    Main._ROUTES ('dashboard', 'notes', 'exams', 'settings', 'import-export',
    'login', 'onboarding'). An unrecognised value is not an error here: Main
    sends an unknown hash to the dashboard. Returns None.
    """
    anvil.set_url_hash(hash_value)
    open_form('Main')


def _sign_out():
    """End the session and return to the login screen (FR20).

    Underscore-prefixed because it is not a UI-kit builder, but it IS used
    outside this module — make_top_bar's button below, and OnboardingForm,
    where signing out is one of only two ways off that screen.

    The order is the point. Every step has to happen before the router runs,
    or the next screen is drawn with the previous student's state:

      1. logout() drops the Anvil session cookie immediately (FR20), so the
         server stops answering as this user.
      2. The cached settings are cleared — they hold the signed-out
         student's subjects and theme, and NOTHING else clears them, since
         get_session_settings only refetches when the cache is empty.
      3. The theme is forced back to light, because the dark class sits on
         document.body and would otherwise outlive the session it belongs to.
      4. Only then navigate, which re-enters Main; its auth gate now finds no
         user and renders LoginForm.

    Returns None.
    """
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

    `active` is the caller's route name ('notes', 'exams', ...), or None on a
    screen that is not in the nav at all; that tab is drawn with the
    'navitem-active' role so the student can always see where they are.
    Returns a FlowPanel with role='topbar' for the form to add as its FIRST
    component, above the centred make_page() column.

    'Sign out' is pushed to the right edge by the stylesheet (it styles the
    last item of the bar), so no spacer component is needed.

    Not shown on LoginForm or OnboardingForm: a student who has not signed in
    or not chosen subjects yet cannot reach any of these pages, and a nav link
    that goes nowhere is worse than no nav at all.
    """
    bar = FlowPanel(role='topbar')

    # The wordmark doubles as the Home link — the convention every web app
    # already teaches — so 'Dashboard' does not have to be the first tab as
    # well as the default route.
    brand = Link(text='DotPoint', role='brand')
    brand.set_event_handler('click', lambda **e: navigate('dashboard'))
    bar.add_component(brand)

    for route, label in _NAV_ITEMS:
        role = 'navitem-active' if route == active else 'navitem'
        link = Link(text=label, role=role)
        # `h=route` binds this iteration's route as a DEFAULT ARGUMENT. A bare
        # `lambda **e: navigate(route)` would close over the loop variable
        # itself, so by the time anything was clicked every link in the bar
        # would navigate to the last route in _NAV_ITEMS.
        link.set_event_handler('click', lambda h=route, **e: navigate(h))
        bar.add_component(link)

    # A Button, not a Link, and last: sign out is the one destructive control
    # in the bar, so it is set apart from the five navigation links rather
    # than sitting among them where a mis-click costs the student a session.
    sign_out = Button(text='Sign out', role='secondary')
    sign_out.set_event_handler('click', lambda **e: _sign_out())
    bar.add_component(sign_out)

    return bar


# --- per-session settings cache (spec §11/§12) -------------------------------
# One get_settings round-trip per session; the router reads this on every
# navigation to gate onboarding and apply the theme. Writers of settings
# (SettingsForm, OnboardingForm) push the server's response back via
# set_session_settings so the cache never goes stale.

# A one-key dict rather than a bare module-level name, because rebinding a
# module global from inside a function would need `global` in three places and
# a stale import of the old value anywhere else would never see the update.
# Mutating one dict that everybody shares has neither problem.
_session = {'settings': None}


def get_session_settings(refresh=False):
    """The signed-in student's settings, fetched at most once per session.

    Returns the dict notes.get_settings builds — {'theme': 'light'|'dark',
    'subjects': [<canonical study>, ...], 'default_reminder_days': [int, ...],
    'notifications_enabled': bool, 'school_year': int|None, 'school_terms':
    [...], 'timezone': str} — reading the caller's own user_settings row and
    no one else's (NFR03).

    Pass refresh=True to force a re-fetch; the default False returns the
    cached copy. Caching matters because Main calls this on EVERY navigation
    to decide the theme and the onboarding gate, and NFR01 budgets a screen
    at about one server round-trip — paying for get_settings on top of
    get_dashboard_data would double that for no new information.

    Raises whatever the server call raises (AuthenticationFailed when the
    session has expired). Main catches it; the cache is only written on
    success, so a failed call leaves the old value alone rather than poisoning
    the cache with None.
    """
    if refresh or _session['settings'] is None:
        _session['settings'] = anvil.server.call('get_settings')
    return _session['settings']


def set_session_settings(settings):
    """Push a settings dict the server just returned into the cache.

    Called by the two screens that WRITE settings — SettingsForm after
    update_settings/set_subjects, and OnboardingForm after set_subjects. Both
    of those callables return the saved row as a dict, so storing their reply
    is free and keeps the cache in step with the database without a second
    round-trip.

    This exists because the alternative is a stale cache: without it, a
    student who switched to the dark theme would be flipped back to light by
    the very next navigation, when Main re-read the pre-save cached copy.
    Returns None.
    """
    _session['settings'] = settings


def clear_session_settings():
    """Forget the cached settings, so the next read refetches.

    Called on sign-out (_sign_out) and before a sign-in (LoginForm), which
    are the two moments the cache could otherwise carry one account's
    subjects and theme into another account's session in the same browser
    tab. Returns None.
    """
    _session['settings'] = None


def apply_theme(theme):
    """Toggle the dark palette by flipping body.dotpoint-dark (the CSS variables
    in anvil.yaml native_deps.head_html do the rest).

    `theme` is the string from user_settings — 'dark', 'light', or None when
    the settings fetch failed. Only the exact string 'dark' turns the dark
    palette on; every other value, None included, means light. Testing FOR
    dark rather than against light is what makes an unknown value degrade to
    the readable default instead of a half-applied dark screen.

    One class on <body> switches the whole app because every colour in the
    app is a CSS variable redefined under that class, so no component has to
    be found and repainted. Returns None (spec §12).
    """
    try:
        from anvil.js.window import document
        if theme == 'dark':
            document.body.classList.add('dotpoint-dark')
        else:
            document.body.classList.remove('dotpoint-dark')
    except Exception:
        # Swallowed on purpose. This is called from Main on EVERY navigation,
        # so an exception here — no browser under the test harness, or a
        # future Anvil that stops exposing document — would stop the router
        # dead. A screen in the wrong colours is still a usable screen.
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
#
# The reference list is not bookkeeping for its own sake. A Notification held
# only by a local variable becomes unreachable the moment show() returns, and a
# toast collected before its own hide timer fired never went away at all — the
# object that owned the timer was gone. Keeping it in _live_toasts is what
# guarantees something is still alive to run the setTimeout below.

# Three is the visible cap: a fourth toast would push the stack tall enough to
# start covering content even at the bottom-right, and nobody reads four
# messages at once anyway.
_MAX_TOASTS = 3
_live_toasts = []


def _dismiss(note):
    """Hide a toast and forget it. Safe to call twice.

    `note` is an anvil.Notification previously returned by toast(). Being
    callable twice is a requirement, not a nicety: the same toast is dismissed
    both by its own setTimeout and by being pushed off the bottom of the
    _MAX_TOASTS stack, and there is no ordering between those two. Returns
    None; raises nothing.
    """
    # Membership-tested rather than caught, because a missing entry is the
    # NORMAL second call, not an exceptional one.
    if note in _live_toasts:
        _live_toasts.remove(note)
    try:
        note.hide()
    except Exception:
        pass  # already gone


def toast(message, style='success', timeout=4):
    """Show a transient message. The single toast call-site for the whole app.

    `message` is coerced with str(), so an exception object can be passed
    straight in. `style` is Anvil's own vocabulary — 'success' | 'danger' |
    'warning' | 'info' — which decides the colour. `timeout` is in SECONDS,
    matching Anvil's Notification, not the milliseconds the setTimeout below
    wants. Returns the Notification, so a caller that needs to dismiss it
    early can pass it to _dismiss.

    Nothing here names a colour: routing every message through this one
    function is what stopped the same kind of message appearing green on one
    screen and grey on another (spec §14.6, defect 13).
    """
    note = Notification(str(message), style=style, timeout=timeout)
    note.show()
    _live_toasts.append(note)

    # Never let the stack grow without bound. Oldest-first (index 0), so the
    # message the student is most likely still reading is the one that stays.
    while len(_live_toasts) > _MAX_TOASTS:
        _dismiss(_live_toasts[0])

    # Own the dismissal. timeout is seconds and setTimeout wants milliseconds;
    # the extra 400ms is a grace period that lets Anvil's own timer win
    # whenever it does work, so a toast is never cut short — this timer is the
    # backstop for the case where it does not.
    try:
        from anvil.js.window import setTimeout
        setTimeout(lambda: _dismiss(note), int(timeout * 1000) + 400)
    except Exception:
        # No browser (the offline test harness). The toast then relies on
        # Anvil's own timer, which is exactly the pre-fix behaviour — degraded,
        # but not worth an exception on a code path that only reports news.
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
    """The centred content column every signed-in page sits in.

    Returns a ColumnPanel with role='page', which the stylesheet gives a
    max-width and centres. `*components` are added in the order given; a form
    that builds its body incrementally can pass none and add_component later.

    This is what the top bar must NOT be inside — the bar runs the full width
    of the window, this column does not — so every screen is
    make_top_bar() + make_page(), never make_page(make_top_bar(), ...).
    """
    panel = ColumnPanel(role='page')
    for c in components:
        panel.add_component(c)
    return panel


def make_row(*components):
    """A horizontal, vertically-centred, wrapping row of components.

    Returns a FlowPanel with role='row'. FlowPanel, not GridPanel: Anvil's
    grid is 12 Bootstrap columns, which cannot divide evenly by the numbers
    this app actually needs, and a row of chips has no fixed count anyway.
    Wrapping is what keeps a long row usable on a narrow window (spec §14.6).
    """
    row = FlowPanel(role='row')
    for c in components:
        row.add_component(c)
    return row


def make_toolbar(*components):
    """A row of controls (inputs + buttons) above a list or panel.

    Returns a FlowPanel with role='toolbar'. Structurally the same as
    make_row, kept separate because the stylesheet spaces a bar of live
    controls more generously than a row of text — and because a reader
    skimming a form can see at a glance which rows are interactive.
    """
    bar = FlowPanel(role='toolbar')
    for c in components:
        bar.add_component(c)
    return bar


def make_card(*components):
    """A surface panel: white/dark card, soft border, rounded.

    Returns a ColumnPanel with role='card' — vertical, because a card holds
    a section of a page (a heading and its contents), not a line of controls.
    The card is the app's unit of grouping: Settings uses one per decision so
    unrelated controls have a visible boundary between them.
    """
    card = ColumnPanel(role='card')
    for c in components:
        card.add_component(c)
    return card


def make_list_card(band=None):
    """A list row whose left edge carries the urgency colour (FR21).

    Returns an EMPTY ColumnPanel for the caller to fill — unlike the builders
    above, because a list row's contents differ per screen and are usually
    built in a loop with data that is not available at construction time.

    `band` is an urgency band from the server ('overdue' / 'today' / 'soon' /
    'distant'), which selects a 'listcard-*' role. None gives the plain
    'listcard' role with no accent, which is what NotesForm uses: a note is
    not due on a date, so borrowing the assessment colour language there
    would tell the student something untrue.
    """
    role = band_role(band, 'listcard') if band else 'listcard'
    return ColumnPanel(role=role)


def make_banner(*components):
    """A quiet full-width strip (tips, the next-exam countdown).

    Returns a FlowPanel with role='banner'. Deliberately understated: a
    banner carries something the student may want to act on, but never
    something that failed — that is what toast_error is for — so it must not
    compete with the page content for attention.
    """
    banner = FlowPanel(role='banner')
    for c in components:
        banner.add_component(c)
    return banner


def make_page_title(title, subtitle=None):
    """The h1 of a screen, with an optional one-line explanation under it.

    Returns a ColumnPanel with role='pagehead' holding a 'pagetitle' Label
    and, when `subtitle` is given, a 'caption' Label under it. `subtitle` is
    where a screen answers "what is this page for" in one line, so the page
    itself does not need a paragraph of instructions.

    Note the role is 'pagehead', not 'row': this stacks its two lines, so it
    must not pick up the horizontal row styling.
    """
    panel = ColumnPanel(role='pagehead')
    panel.add_component(Label(text=title, role='pagetitle'))
    if subtitle:
        panel.add_component(Label(text=subtitle, role='caption'))
    return panel


def make_section_header(title, hint=None):
    """A small upper-case label that opens a section, with an optional hint.

    Returns a FlowPanel with role='row' holding a 'sectionhead' Label and,
    when `hint` is given, a smaller 'micro' Label beside it. Horizontal on
    purpose: the hint qualifies the heading ("Reminders — days before the due
    date"), so it belongs on the same line rather than reading as the first
    line of the section's content.
    """
    row = FlowPanel(role='row')
    row.add_component(Label(text=title, role='sectionhead'))
    if hint:
        row.add_component(Label(text=hint, role='micro'))
    return row


def make_chip(text, tone=None):
    """A small rounded tag: a subject, a type, a tag, a confidence, a count.

    Returns a Label with role='chip' or 'chip-<tone>'. `tone` is None for the
    neutral grey chip, or one of the stylesheet's tones — 'accent', 'exam',
    'ok' / 'warn' / 'bad' (which is what CONF_TONE maps HIGH/MEDIUM/LOW onto,
    FR17), or an urgency suffix, though make_band_chip below is the tidier
    way to reach those.

    A Label rather than a Button because a chip is a fact, not a control;
    nothing in this app has a clickable chip.
    """
    role = 'chip' if tone is None else 'chip-%s' % tone
    return Label(text=text, role=role)


def make_band_chip(text, band):
    """A chip tinted by an urgency band (FR21).

    `text` is the words shown ('overdue', 'in 3 days'); `band` is the
    server's band name ('overdue' / 'today' / 'soon' / 'distant'). Returns a
    Label with the matching 'chip-*' role.

    The colour is always paired with words, never used alone — FR21 asks for
    colour-coded urgency, but a student who cannot distinguish red from
    orange still has to be able to read the list.
    """
    return Label(text=text, role=band_role(band, 'chip'))


def make_empty_state(title, hint=None, action_text=None, action_click=None):
    """What a panel shows when it has nothing to show (FR07).

    A dashed, centred block with a heading, an optional explanation and an
    optional call to action — never just a stray sentence. `title` is the
    headline ('No assessments match'), `hint` the sentence under it, and
    `action_text` / `action_click` an optional button and the zero-argument
    function it calls. Returns a ColumnPanel with role='empty'.

    FR07 requires that an empty result set says so rather than rendering a
    blank panel, and this is the one place that decides what "says so" looks
    like — so every empty list in the app reads the same way.
    """
    panel = ColumnPanel(role='empty')
    panel.add_component(Label(text=title, role='cardtitle', align='center'))
    if hint:
        panel.add_component(Label(text=hint, role='caption', align='center'))
    # Both halves are required before a button is drawn: text with no handler
    # would be a dead button, and a handler with no text would be invisible.
    if action_text and action_click:
        btn = Button(text=action_text, role='secondary', align='center')
        # The handler is wrapped rather than passed straight through, because
        # Anvil calls a click handler with keyword arguments (sender, event_name)
        # and the callers here all pass plain zero-argument functions.
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

    # ColumnPanel, so the four parts stack in a fixed order: caption, control,
    # hint, error. The error is LAST because a message that pushed the control
    # down the screen would move the box the student is about to click on.
    caption = label_text + ' *' if required else label_text
    panel.add_component(Label(text=caption, role='caption'))
    panel.add_component(component)

    # The hint is what the field wants BEFORE anything goes wrong (a format, a
    # minimum length). set_field_error's message is what it says afterwards.
    # Both can be visible at once, which is deliberate: an error should not
    # hide the rule the student is trying to satisfy.
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

    `field_panel` is the ColumnPanel make_field() returned. `message` is the
    sentence to show, or None/'' to clear the field. Returns None.

    Safe to call on a panel that predates this helper (it simply does nothing), so a
    form can adopt per-field errors one field at a time.
    """
    # getattr with a default rather than hasattr-then-read, and an early return
    # rather than an else block: a panel built some other way is not an error
    # worth raising over, it just has nowhere to put a message.
    error_label = getattr(field_panel, 'error_label', None)
    if error_label is None:
        return
    error_label.text = message or ''
    # Toggling `visible` rather than the text alone means an empty message collapses
    # the row instead of leaving a blank gap under the control.
    error_label.visible = bool(message)


def clear_field_errors(*field_panels):
    """Wipe every field message before re-validating, so stale ones never linger.

    `*field_panels` are make_field() wrappers. Called at the TOP of every
    validate-and-save handler, not at the bottom of the previous one: a form
    that only cleared the field it was about to complain about would leave
    yesterday's message sitting under a box the student has since fixed.
    Returns None.
    """
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

    `exception` is whatever a `except Exception as e` caught — it is str()'d,
    so None and a bare '' are both handled. `fallback` is the sentence used
    when the exception's own text is not fit to show; a caller can override it
    to say something more specific about what was being attempted. Always
    returns a str, and never raises: it is called from inside except blocks,
    where a second exception would lose the first one entirely.
    """
    message = str(exception or '').strip()

    # A message the app wrote for a person reads as a sentence: it starts with a
    # capital and ends with a full stop. Developer strings ('title required',
    # 'invalid subject') and platform errors do neither, which is a reliable enough
    # test to sort them without tagging every raise site.
    #
    # The 300-character ceiling is the last of the four tests and catches what
    # the first three cannot: a stack trace or an Anvil platform error that
    # happens to start with a capital and end in a full stop. The validators'
    # own messages are one or two sentences and nowhere near this long, so the
    # ceiling only ever rejects text no student should have been shown.
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
    """Grouped multi-select over the VCE subject catalog (spec §11).

    The screen it draws: a running total in a caption at the very top, then
    one learning area after another — a heading with a small count badge
    beside it, and under that a wrapping grid of toggle pills, one per study.
    A pill is a CheckBox the stylesheet redraws as a rounded tag (role='pill'),
    so ticking a subject looks like selecting it rather than filling in a
    form. The two counts exist because the catalog runs to 56 studies
    across ten learning areas: without them a student choosing five subjects would
    have to scroll the whole list back to see what they had already ticked.

    Constructed as SubjectPicker(catalog, selected=None, **properties):
      * `catalog` is exactly what notes.get_subject_catalog() returns —
        [{'group': <learning area>, 'subjects': [<canonical name>, ...]}, ...]
        — and its order is the display order.
      * `selected` is the studies to start ticked: the stored
        user_settings.subjects when re-opening the picker, or None/[] on a
        first run.
      * `**properties` go to ColumnPanel, as for any Anvil component.

    A deliberately dumb component. It calls no server function itself: the
    caller fetches the catalog and reads get_selection() back, and the VCE
    program rules (at least one mathematics study; an English-group study
    always present) are enforced by notes.set_subjects on the server, with
    the two forms checking the same rules first only so the student is told
    before the round-trip. It raises no events and returns nothing to anyone
    — the caller asks it for the selection when the student presses the
    button. Used by OnboardingForm and the Settings change-subjects flow.
    """

    def __init__(self, catalog, selected=None, **properties):
        """Build the whole picker from `catalog` in one pass. See the class
        docstring for the argument shapes."""
        super().__init__(**properties)
        # A set, not the list as given: `subject in selected` runs once per
        # pill, so ~56 times, and a stored list can legitimately hold a
        # duplicate that a set silently absorbs.
        selected = set(selected or [])
        # Three parallel indexes, each built here and read nowhere else.
        # _checks is the flat catalog-order list get_selection() walks;
        # _group_checks and _group_counts are keyed by learning-area name so
        # _refresh_counts can pair each group's pills with its own badge.
        self._checks = []        # [(subject, CheckBox), ...] in catalog order
        self._group_checks = {}  # group name -> [CheckBox, ...]
        self._group_counts = {}  # group name -> the count badge Label

        # Added before the groups, so the running total sits at the top of the
        # picker where it stays on screen while the student scrolls the list.
        self._total = Label(text='', role='caption')
        self.add_component(self._total)

        for group in catalog:
            name = group['group']
            header = FlowPanel(role='row')
            header.add_component(Label(text=name, role='sectionhead'))
            # Built visible=False and kept: creating the badge up front means
            # ticking the first pill in a group only changes a label's text,
            # rather than inserting a component and re-flowing the header.
            count = Label(text='', role='chip-accent', visible=False)
            self._group_counts[name] = count
            header.add_component(count)
            self.add_component(header)

            # 'pickgrid' is a FlowPanel the stylesheet lays out as a wrapping
            # grid, so a group of 12 studies and a group of 3 both look right
            # without this code knowing how many columns fit.
            row = FlowPanel(role='pickgrid')
            boxes = []
            for subject in group['subjects']:
                cb = CheckBox(text=subject, checked=subject in selected,
                              role='pill')
                # The CheckBox itself is the state — nothing here mirrors
                # which subjects are ticked into a second variable that could
                # drift out of step with what the student can see.
                cb.set_event_handler('change', self._on_toggle)
                self._checks.append((subject, cb))
                boxes.append(cb)
                row.add_component(cb)
            self._group_checks[name] = boxes
            self.add_component(row)

        # Called once with nothing ticked (or with `selected` restored) so the
        # total reads correctly before the student touches anything.
        self._refresh_counts()

    def _on_toggle(self, **event_args):
        """Any pill changed: re-count. Bound to every CheckBox's 'change'.

        `**event_args` is Anvil's handler bag (sender, event_name); it is
        ignored because the counts are recomputed from all the boxes anyway,
        which is what keeps them right no matter which pill was clicked.
        """
        self._refresh_counts()

    def _refresh_counts(self):
        """Keep the running total and each group's badge in step with the ticks.

        Recomputes from the CheckBoxes rather than tracking a delta on each
        toggle: 56 boolean reads is nothing, and a delta would slowly drift
        out of step with the screen after a missed event. Writes only Label
        text and visibility; returns None.
        """
        total = len(self.get_selection())
        # Three phrasings rather than one with a plural suffix, because the
        # zero case is not "0 subjects selected." — it is the sentence that
        # tells a student on their first run what they are meant to do.
        if total == 0:
            self._total.text = 'No subjects selected yet.'
        elif total == 1:
            self._total.text = '1 subject selected.'
        else:
            self._total.text = '%d subjects selected.' % total

        for name, boxes in self._group_checks.items():
            n = len([cb for cb in boxes if cb.checked])
            badge = self._group_counts[name]
            # Just the number: the badge sits immediately after the group
            # heading, so "Mathematics 2" already reads as two chosen, and
            # spelling out "2 selected" on ten headings at once is noise.
            badge.text = str(n)
            # Hidden at zero, so the badges that ARE showing are exactly the
            # areas the student has picked something in.
            badge.visible = n > 0

    def get_selection(self):
        """The ticked studies, in catalog order, as a list of canonical names.

        The component's only output, and the thing OnboardingForm and
        SettingsForm hand to notes.set_subjects. Read live off the
        CheckBoxes every time, so it cannot report anything other than what
        is on screen at the moment it is called.
        """
        return [s for s, cb in self._checks if cb.checked]
