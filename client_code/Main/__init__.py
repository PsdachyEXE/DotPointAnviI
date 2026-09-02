import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Main - hash router and app startup form (spec §4).

A thin custom hash router. On instantiation it reads the URL hash, checks the
auth state, and renders the matching top-level form *inside itself* via
add_component. Logged-out users are forced to LoginForm; logged-in users hitting
'#login' are bounced to the dashboard.

Browser navigation (spec §14): the router also listens for the browser's
`hashchange` event, so the Back/Forward buttons and pasted '#notes'-style deep
links re-route immediately instead of needing a full page reload. The listener
is installed once per session and ignores events raised while a modal dialog is
open, so hitting Back mid-dialog cannot redraw the page behind it.

Onboarding gate (spec §11): a logged-in user with no locked-in subjects is
forced to OnboardingForm whatever hash they hit, so the "What subjects do you
do?" step is truly mandatory. The check reads common.get_session_settings()
(one get_settings round-trip per session, cached), which also lets the router
apply the user's theme on every navigation.

Child forms navigate by calling common.navigate(...), which sets the hash and
lets the listener above re-enter this router.

The Main class docstring below is the whole-system overview — how a typed
sentence becomes a stored row, where the client/server line is drawn, how the
reminder task runs without a browser, and what each of the ten screens is for.
Start there if this is the first file of the project you have opened.

See IMPLEMENTATION_SPEC.md section 4 (Routing) and section 5 (Authentication).
"""

import anvil
import anvil.users
from anvil import ColumnPanel, open_form

from ..common import get_session_settings, apply_theme

# hash -> form name (spec §4). The empty string is the app's front door
# (dotpoint.anvil.app with no '#...' on the end), so it has to resolve to the
# same place as '#dashboard' rather than falling through to the default below.
# Every value here is a form NAME, not a class: _make_form is where the import
# actually happens, and keeping this table free of imports is what stops one
# broken form from taking the router down with it.
_ROUTES = {
    '': 'DashboardForm',
    'dashboard': 'DashboardForm',
    'login': 'LoginForm',
    'settings': 'SettingsForm',
    'import-export': 'ImportExportForm',
    'notes': 'NotesForm',
    'exams': 'ExamsForm',
    'onboarding': 'OnboardingForm',
}

# Module-level so the browser listener is attached exactly once per session,
# however many times Main is re-opened. `hash` remembers the route this module
# last rendered, which is how a hashchange raised by the app's own
# set_url_hash() is told apart from the user pressing Back.
_listener = {'installed': False, 'hash': None}


def _modal_is_open():
    """True while an Anvil alert()/confirm() dialog is showing.

    Anvil builds those on Bootstrap modals, which carry the class 'in' while
    visible. Re-routing underneath an open dialog would leave the user looking
    at a stale page once they dismissed it, so hashchange is ignored then.
    """
    try:
        from anvil.js.window import document
        return document.querySelector('.modal.in') is not None
    except Exception:
        # Anything that goes wrong here — no browser at all, or an Anvil
        # release that stops exposing document — has to read as "no dialog is
        # open". Answering True on failure would make _on_hash_change refuse
        # every Back press for the rest of the session, which is a far worse
        # bug than the redraw this check exists to prevent.
        return False


def _install_hash_listener():
    """Attach the browser's hashchange handler, exactly once per page load.

    Main is re-constructed on every navigation, so this is called dozens of
    times in a session; the guard is what stops a second addEventListener
    stacking up and running _on_hash_change twice for one Back press. The
    flag lives in the module-level _listener dict rather than on the form,
    because the form instance is thrown away and rebuilt each time.

    Returns None. Off-browser (the offline test harness has no
    anvil.js.window) it quietly does nothing — routing on open still works,
    only Back/Forward is unavailable, and there is no browser there to press
    them with.
    """
    if _listener['installed']:
        return
    try:
        from anvil.js.window import window
    except Exception:
        return  # no browser (e.g. a test harness) — routing still works on open
    window.addEventListener('hashchange', _on_hash_change)
    _listener['installed'] = True


def _on_hash_change(event):
    """Re-route on a hash change the app did not make itself.

    common.navigate() and _route_to_current() both write the hash and then
    render, so the hashchange those writes raise arrives *after* the right form
    is already on screen. Re-routing on it would build the same form a second
    time and repeat its server calls (NFR01 is one round-trip per screen), so
    an event whose hash matches what we last rendered is ignored. What is left
    is exactly what this listener is for: Back, Forward, and a #link the user
    pasted into the address bar.

    `event` is the browser's HashChangeEvent. It is accepted because
    addEventListener always passes one, and ignored because its newURL is the
    whole address; anvil.get_url_hash() below gives just the route.
    Returns None; the effect is the open_form at the end.
    """
    current = anvil.get_url_hash()
    # A hash shaped like a query string ('#a=1&b=2') comes back as a dict, and
    # a dict can never equal the string in _listener['hash'] — it would look
    # like a genuine Back press forever. Flatten it to '' (the dashboard).
    if not isinstance(current, str):
        current = ''
    if current == _listener['hash']:
        return  # the echo of our own write; the right form is already drawn

    if _modal_is_open():
        # Re-routing now would redraw the page behind an open dialog. Put the
        # hash back instead, so the address bar keeps naming what is actually
        # on screen. (That write raises another hashchange, but by then it
        # matches what we last drew and is caught by the check above.)
        _restore_hash()
        return

    open_form('Main')


def _restore_hash():
    """Put the URL back to the route currently on screen, without adding a
    history entry — otherwise the user would have to press Back twice.

    Only called from _on_hash_change when a dialog is open. Does nothing
    before the first render (_listener['hash'] is still None), since there is
    no route to go back to yet. Returns None.
    """
    if _listener['hash'] is None:
        return
    try:
        anvil.set_url_hash(_listener['hash'], set_in_history=False)
    except TypeError:
        # set_in_history is a newer keyword; an older Anvil client runtime
        # raises TypeError on it. Falling back to the plain call costs one
        # extra history entry, which is much better than an exception
        # escaping into a browser event handler where nothing catches it.
        anvil.set_url_hash(_listener['hash'])


class Main(ColumnPanel):
    """Startup form and hash router — the shell the whole app renders inside.

    Anvil shows one form at a time, and for DotPoint that form is always
    Main. Main paints nothing of its own: it reads the URL hash, works out
    which screen this student is allowed to see right now, and adds that
    form as its single child. Every screen in the app is reached this way,
    so this header doubles as the map of the system.

    TURNING A TYPED SENTENCE INTO A TRACKED ASSESSMENT
    -------------------------------------------------
    This is the app's whole premise, and it is deliberately four steps with
    the student in the middle:

      1. The student types a sentence — "Methods SAC2 due Friday week 5
         worth 25%" — into the parser bar at the top of DashboardForm.
      2. DashboardForm calls the server's nlp.parse_text (FR01). The parser
         is PURE: it reads user_settings for the school terms and the locked
         subject list, and writes nothing at all. It hands back a draft
         record, a confidence of HIGH / MEDIUM / LOW (FR17), and a per-field
         "why" line naming the words it matched.
      3. That draft opens AssessmentEditorForm in mode='preview' as a modal
         alert. Nothing has been stored yet. The student sees every field
         filled in, the confidence chip and the provenance, and can correct
         anything the parser guessed wrong; Cancel throws the parse away.
         Will asked for exactly this in the interview — show me what it got
         and let me fix it before it saves — so there are no silent commits.
      4. Only on Save does the form call assessments.create_assessment,
         which re-validates the record server-side and writes ONE row to the
         assessments table, stamped with the calling user.

    A multi-line paste takes the same road through AssessmentEditorForm in
    mode='bulk' (FR02); mode='create' is that same form with nothing
    prefilled, for when the parser is the wrong tool (FR03).

    THE CLIENT / SERVER SPLIT
    -------------------------
    Every Data Table in anvil.yaml is declared with client permission
    "none". That is not tidiness, it is the security model: the browser
    physically cannot read or write a row, so no amount of poking at the
    JavaScript console reaches another student's data. All data crosses the
    boundary through @anvil.server.callable functions, and each one begins
    by resolving the caller with _auth._require_user() and scoping its query
    to that user; anything fetched by row id is re-checked with
    _auth._own_or_raise(). That is NFR03 — "every Data Table query scoped to
    current_user" — implemented as a rule the client cannot opt out of.

    The same wall is why several server constants are hand-copied into the
    forms: client code cannot import server_code at all. The copies are not
    left on trust — tests/test_constants_integrity.py reads the client files
    with `ast` and asserts they still match server_code/_constants.py.

    Display is shared too. common.fmt_date is the app's "DD MMM YYYY"
    formatter (NFR08), so a due date reads identically on every screen no
    matter what locale the browser is set to. ExamsForm is the one documented
    exception: an exam line names the weekday as well, so it formats from the
    same shared common.MONTHS_ABBR rather than duplicating the month names.

    REMINDERS HAPPEN WITHOUT A BROWSER
    ----------------------------------
    reminders.run_reminder_check is an @anvil.server.background_task that
    Anvil's Scheduled Tasks runs every 30 minutes whether or not anyone has
    the app open (FR13) — 30 minutes is the platform minimum, not a choice.
    It walks every account, compares (due_date - today).days against each
    assessment's reminder_days list, and emails the student when a threshold
    is hit (FR14). The reminder_logs table keys on
    (assessment, user, sent_date, reminder_type), so the same reminder can
    never go out twice in one day even though the task ticks 48 times
    (NFR02). It is the one place in the codebase that reads rows belonging
    to somebody other than the caller, because it runs as the app rather
    than as a signed-in user.

    THE TEN SCREENS
    ---------------
      Main                  this router; owns no UI of its own.
      LoginForm             email/password sign-in and sign-up (FR20).
      OnboardingForm        the one-off "what subjects do you do?" gate
                            (spec §11).
      DashboardForm         home: the parser bar, filters, the assessment
                            list, a month calendar and the 30-day upcoming
                            panel (FR06, FR07, FR08, FR09, FR21).
      AssessmentEditorForm  the modal editor, in four modes — create, edit,
                            parser preview, bulk (FR02, FR03, FR04, FR17).
      NotesForm             the notes index, with search and tag filter
                            (FR10, FR11).
      NoteEditorForm        modal create/edit for one note (FR10).
      ExamsForm             the student's VCE written-exam timetable
                            (spec §13).
      SettingsForm          reminders, school terms, timezone, theme, and
                            the deliberate change-subjects flow (spec §12).
      ImportExportForm      JSON export and import of the student's own
                            data (FR18, FR19).

    THE ROUTING MODEL
    -----------------
    Routes are plain URL hashes ('#notes', '#exams'), mapped to form names
    by _ROUTES above. Two gates run before any route is honoured:

      * the auth gate — no logged-in user means LoginForm, whatever the
        hash says (FR20); and
      * the onboarding gate — a logged-in user with no locked-in subjects
        is held on OnboardingForm until they choose, because the editor
        dropdown, the dashboard filter, the parser's alias ranking and the
        Exams screen all read that list (spec §11).

    Child forms never construct each other. They call common.navigate(),
    which sets the hash and re-opens Main, so building this class is the
    app's ONLY render path — which is what makes the two gates unavoidable
    rather than merely usual.

    Constructed with no arguments of its own; `properties` is Anvil's layout
    keyword bag. It returns nothing to anybody, because there is nobody
    above it — Main is the page.
    """

    def __init__(self, **properties):
        """Build the empty shell, then immediately route to the current hash.

        `properties` is passed straight through to ColumnPanel; Main is
        always opened as open_form('Main') with nothing else supplied.
        """
        super().__init__(**properties)
        # The top bar inside each screen has to reach both window edges, so
        # the router contributes no padding of its own. The centred
        # common.make_page() column inside each form does the insetting.
        self.spacing_above = 'none'
        self.spacing_below = 'none'
        # Installed before the first render so Back/Forward is live from the
        # very first screen, and so an event arriving while the server calls
        # in _route_to_current suspend the client is still delivered.
        _install_hash_listener()
        self._route_to_current()

    def _route_to_current(self):
        """Work out what this student may see right now, and render it.

        Reads the hash, applies the auth gate and then the onboarding gate,
        looks whatever survives up in _ROUTES, and hands the winning form
        name to _render. Redirects rewrite the hash as they go, so the
        address bar never names a screen the student is not looking at.

        Data it touches: the URL hash (a str, or a dict for a query-style
        hash); anvil.users.get_user(); and the cached settings dict from
        common.get_session_settings() — {'theme': 'light'|'dark',
        'subjects': [...], ...}, the shape notes.get_settings returns. It
        writes no table itself; every table read behind get_settings happens
        server-side. Returns None: the whole effect is the _render it ends
        on, and every branch ends on exactly one _render.
        """
        hash_value = anvil.get_url_hash()
        # 1. get_url_hash() returns a DICT when the hash looks like a query
        #    string ('#a=1&b=2'), which a mangled or pasted link can produce.
        #    This app has only plain string routes, so anything else is
        #    flattened to '' and lands on the dashboard rather than crashing
        #    the .get() lookup below on an unhashable key.
        if not isinstance(hash_value, str):
            hash_value = ''

        # 2. Claim this route BEFORE doing anything that can block. Deciding
        #    the route below calls get_settings(), and an Anvil server call
        #    suspends the client — which lets the browser deliver the
        #    hashchange raised by the navigate() that got us here. If the
        #    route were only recorded at render time, that event would arrive
        #    while this still said 'login', look like a genuine Back press,
        #    and build the whole screen a second time (two get_dashboard_data
        #    round-trips, against NFR01).
        _listener['hash'] = hash_value

        # 3. The auth gate (FR20). allow_remembered=True honours the "remember
        #    me" cookie, so a student who signed in last week is not asked
        #    again. Checked before the settings fetch, because a signed-out
        #    visitor has no settings row to fetch and get_settings would only
        #    raise AuthenticationFailed.
        user = anvil.users.get_user(allow_remembered=True)

        if user is None:
            # 4. Force the light palette on the way out. The dark class sits
            #    on document.body, which survives a sign-out, so without this
            #    the login screen would keep the previous student's theme —
            #    and the next person to sign in is not necessarily them.
            apply_theme('light')
            # Rewritten only when it differs, so a student who came straight
            # to '#login' does not collect a duplicate history entry to press
            # Back through.
            if hash_value != 'login':
                anvil.set_url_hash('login')
            self._render('LoginForm')
            return

        # 5. Session settings drive both the theme and the onboarding gate,
        #    and come from ONE cached get_settings round-trip per session
        #    (common.get_session_settings), not one per navigation — NFR01
        #    budgets the dashboard at a single call. The try is deliberate: a
        #    dropped network call must not brick navigation, so `settings`
        #    becomes None, meaning "unknown", and both branches below have a
        #    defined answer for that.
        try:
            settings = get_session_settings()
        except Exception:
            settings = None

        if settings is not None:
            apply_theme(settings.get('theme'))
            # 6. The onboarding gate (spec §11). An empty subjects list means
            #    the student has never locked in their studies, and the editor
            #    dropdown, the dashboard filter, the parser's alias ranking
            #    and the Exams screen all read that list — so the app is not
            #    usable until it exists. Held here, on every hash, rather than
            #    nagged with a banner: that is what makes the step mandatory
            #    instead of merely recommended.
            if not settings.get('subjects'):
                if hash_value != 'onboarding':
                    anvil.set_url_hash('onboarding')
                self._render('OnboardingForm')
                return

        # 7. Normal routing. An unknown hash is not worth a 404 screen in an
        #    app this size — a typo in the address bar lands on the dashboard.
        target = _ROUTES.get(hash_value, 'DashboardForm')
        if target == 'LoginForm':
            # 8. Already authenticated; don't show the login screen. Reached
            #    by a signed-in student pressing Back onto the '#login' entry
            #    their own sign-in left in the history.
            anvil.set_url_hash('dashboard')
            target = 'DashboardForm'
        if target == 'OnboardingForm' and (settings is None or settings.get('subjects')):
            # 9. Already onboarded — or settings unknown after a fetch failure.
            #    Fail CLOSED to the dashboard: rendering OnboardingForm blind
            #    would offer an empty picker that could overwrite locked
            #    subjects. The gate re-fires on the next navigation once the
            #    settings fetch succeeds.
            anvil.set_url_hash('dashboard')
            target = 'DashboardForm'
        self._render(target)

    def _render(self, form_name):
        """Replace the router's content with a fresh instance of `form_name`.

        `form_name` is one of the values in _ROUTES ('DashboardForm',
        'LoginForm', ...) — a name rather than a class, because _make_form
        defers the import. Returns None.

        Re-records the route: _route_to_current claims it up front, but the
        decisions in between can redirect (a signed-in user hitting '#login',
        or the onboarding gate), so this is the last word on what is on screen.
        """
        # Re-read rather than take the caller's value: the redirects above
        # rewrote the hash, and the address bar is now the truth about which
        # screen this is. Same dict-hash coercion as step 1 — _listener['hash']
        # is compared to a string in _on_hash_change and must never be a dict.
        current = anvil.get_url_hash()
        _listener['hash'] = current if isinstance(current, str) else ''
        # clear() then add: every navigation builds a FRESH form instance
        # rather than re-showing a cached one, so a screen can never come back
        # holding the previous route's rows — including the previous USER's,
        # after a sign-out and a sign-in in the same browser tab.
        self.clear()
        self.add_component(self._make_form(form_name))

    def _make_form(self, form_name):
        """Instantiate `form_name` and return the component. Nothing else in
        the app constructs a top-level form.

        `form_name` is a value from _ROUTES; anything unrecognised falls
        through to DashboardForm, which is the same "unknown route goes home"
        rule _route_to_current applies to an unknown hash. Every form is
        constructed with no arguments — a top-level screen fetches its own
        data; only the two modal editors take constructor arguments, and they
        are opened by their parent form, not from here.

        The imports sit inside the branches on purpose. A module-level import
        of all seven forms would make opening Main pull in the entire client at
        once, so a route whose form did not exist yet (the app was built in
        slices) or a form with an import error anywhere in it would break
        routing altogether rather than breaking just its own screen. An
        if-chain is used instead of a dict of classes for the same reason: a
        dict would have to name every class at module level, which is exactly
        the import being avoided.
        """
        if form_name == 'LoginForm':
            from ..LoginForm import LoginForm
            return LoginForm()
        if form_name == 'SettingsForm':
            from ..SettingsForm import SettingsForm
            return SettingsForm()
        if form_name == 'ImportExportForm':
            from ..ImportExportForm import ImportExportForm
            return ImportExportForm()
        if form_name == 'NotesForm':
            from ..NotesForm import NotesForm
            return NotesForm()
        if form_name == 'ExamsForm':
            from ..ExamsForm import ExamsForm
            return ExamsForm()
        if form_name == 'OnboardingForm':
            from ..OnboardingForm import OnboardingForm
            return OnboardingForm()
        # default / 'dashboard'
        from ..DashboardForm import DashboardForm
        return DashboardForm()
