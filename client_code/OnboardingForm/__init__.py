import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""OnboardingForm - mandatory "What subjects do you do?" step (spec §11).

Shown by the Main router to any logged-in user whose user_settings.subjects
is empty, so it runs exactly once after signup (and for accounts created
before this feature shipped). The student multi-selects from the VCE catalog
(common.SubjectPicker); Confirm calls notes.set_subjects, which enforces the
program rules server-side: at least one mathematics study (client mandate),
and an English-group study always present (VCAA rule — 'English' is appended
automatically if none was chosen; the form warns first so it's never a
surprise). After locking in, subjects drive the editor dropdown, dashboard
filter, parser alias priority and the Exams view; they change only via the
deliberate Settings flow.

Structure (spec §14): the page title sits on the page body, then a single
centred card — make_page() > make_page_title() + make_card() — opened by
make_section_header('Your studies') and holding the two program rules as a
micro line, the pill picker and the two actions. That is the same shape as
every other screen (title on the page, card opened by its own section
header), so onboarding no longer reads as a one-off. There is deliberately NO
top bar: the router re-renders this form for every hash until subjects exist,
so nav links would promise pages the student cannot reach yet. The only ways
out are 'Lock in my subjects' and 'Sign out' — which is also why the
catalog-load failure below keeps both a retry and a sign out.
"""

import anvil
import anvil.server
from anvil import ColumnPanel, Label, Button, confirm, open_form

from ..common import (
    SubjectPicker, set_session_settings, apply_theme, _sign_out,
    navigate, toast, toast_error, toast_warn, friendly_error,
    make_page, make_card, make_page_title, make_section_header, make_row,
    make_empty_state,
)

# --- mirrors of server constants --------------------------------------------
# Anvil client code cannot import a server module, so these two tuples are hand
# copies. Each one names the constant it copies so the original is findable:
#
#   ENGLISH_GROUP  copies  server_code/_constants.py  ENGLISH_GROUP
#   MATHS_GROUP    copies  server_code/_constants.py  MATHS_GROUP
#
# They must hold EXACTLY what the server holds, because notes._clean_subjects
# applies the same two membership tests to the same selection: any entry missing
# here is a selection this form rejects and the server would have accepted.
# MATHS_GROUP was missing 'Mathematics' (the parser's generic maths study), which
# is exactly that bug. Copying 'Mathematics' cannot put a non-study in front of
# the student — the picker is built from the server's SUBJECT_GROUPS, which
# deliberately omits it — it only makes the client's answer to "have you picked
# a maths study?" identical to the server's.
ENGLISH_GROUP = ('English', 'English as an Additional Language',
                 'English Language', 'Literature')
MATHS_GROUP = ('Mathematics', 'Foundation Mathematics', 'General Mathematics',
               'Mathematical Methods', 'Specialist Mathematics')

# The two VCE program rules, worded ONCE. The same sentences appear in
# SettingsForm, and the maths sentence is word-for-word the one
# notes._clean_subjects raises, so a student meets one wording for one rule no
# matter which screen they are on and no matter which side caught them.
MATHS_RULE_MESSAGE = ('Select at least one mathematics study '
                      '(Foundation, General, Methods or Specialist).')
ENGLISH_RULE_MESSAGE = ("You haven't picked an English-group study. Every VCE "
                        "program includes one, so 'English' will be added "
                        "automatically. Continue?")
NO_SELECTION_MESSAGE = 'Pick your subjects first.'


class OnboardingForm(ColumnPanel):
    """The mandatory "What subjects do you do?" gate, shown once after signup.

    Main renders this INSTEAD of the route the student asked for whenever
    their user_settings.subjects is empty, so the dashboard, notes and exams
    are all unreachable until studies are locked in.

    The one case where the gate does NOT fire is a failed settings fetch: the
    router cannot then tell whether subjects exist, and a '#onboarding' hash
    in that state is sent to the dashboard instead. That is the safe way to be
    wrong — this form's Confirm OVERWRITES user_settings.subjects, so drawing
    an empty picker over a selection that is already saved could wipe it. The
    gate re-fires on the next navigation, once settings load.

    Two VCE program rules are enforced, and _on_confirm checks them in this
    order because it is the order a student would fix them in:
      1. at least one mathematics study (a DotPoint client mandate) — a hard
         block, MATHS_RULE_MESSAGE;
      2. an English-group study is always present (VCAA) — NOT a block. The
         server appends 'English' itself, so this form only warns first
         (ENGLISH_RULE_MESSAGE) and lets the student continue, so the extra
         subject is never a surprise.
    Both are re-applied by notes._clean_subjects, which stays the authority.

    No FR covers subject selection — it is spec §11, added after the SRS was
    written — but the locked-in list is what afterwards feeds FR03's subject
    dropdown, FR06's subject filter, the Exams view and the parser's alias
    priority (FR16, nlp._match_subject takes user_subjects).

    Construction: no arguments of its own; the router builds it with a bare
    OnboardingForm() and `properties` is only Anvil's component keyword set.
    No modes — but there are two layouts, because a failed catalog fetch
    replaces the picker and Confirm with a retry/sign-out pair.

    Server callables (both server_code/notes.py):
      * get_subject_catalog() -> [{'group': str, 'subjects': [str, ...]}, ...],
        fetched in __init__ to build the picker;
      * set_subjects(selection) -> the saved user_settings as a dict, called
        by Confirm. Sole writer of user_settings.subjects.

    Hands nothing back to a caller. On success it seeds the session settings
    cache from set_subjects' reply, applies the theme and navigates to the
    dashboard; the only other way off this screen is Sign out.
    """

    def __init__(self, **properties):
        """Build the page and fetch the subject catalog.

        The one server call this screen makes on load. It is made HERE rather
        than in a later _load() step because there is nothing to draw without
        it — the picker is the screen — and because a failure has to replace
        the layout rather than empty it.
        """
        super().__init__(**properties)
        # 1. No padding on the form itself: make_page() below is the centred
        #    column that carries the page inset on every screen.
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        body = make_page()
        self.add_component(body)

        # 2. One hierarchy: page title -> what it's for -> rules -> the choice.
        # The title belongs to the page, not to the card, so it lines up with
        # every other screen; the card below is opened by its own section
        # header instead of borrowing the h1.
        body.add_component(make_page_title(
            'What subjects do you do?',
            'Your studies drive the parser, dashboard and exam timetable. '
            'Change them any time in Settings.'))

        card = make_card()
        body.add_component(card)
        card.add_component(make_section_header('Your studies'))

        # 3. The rules sit in a micro line rather than a paragraph because the
        # student only needs them while they are actually picking. Both rules
        # are stated up front so the maths block at _on_confirm is a reminder
        # rather than the first the student has heard of it.
        card.add_component(Label(
            text='One mathematics study is required, and an English-group '
                 'study is always kept (VCAA rule).',
            role='micro'))

        # 4. The catalog is the ~56 VCE studies grouped by learning area. It
        #    comes from the server rather than a client constant so the picker
        #    and set_subjects' membership test can never offer and reject
        #    different lists.
        try:
            catalog = anvil.server.call('get_subject_catalog')
        except Exception as e:
            # friendly_error, not str(e): this is the very first screen a new
            # student sees, and the raw failure here is an Anvil transport
            # string, never a sentence written for them. Anything the server
            # DID write for them still passes straight through.
            #
            # The router re-renders this form for every hash while the user is
            # un-onboarded, so a dead-end here would trap them behind the
            # onboarding gate: keep BOTH escape hatches — retry and sign out.
            toast_error(friendly_error(
                e, "Couldn't load the subject list. Check your connection and "
                   "try again."))
            card.add_component(make_empty_state(
                "Couldn't load the subject list",
                'Check your connection and try again.',
                'Retry',
                lambda: open_form('Main')))
            card.add_component(self._sign_out_row())
            # Early return, so self._picker is never created on this path —
            # which is safe only because the Confirm button that reads it is
            # not created either. The card ends with retry + sign out.
            return

        # 5. The picker is a dumb component: it renders the catalog as toggle
        # pills with live per-group counts and hands back a plain list of
        # names. All VCE rules are checked below and again server-side in
        # set_subjects. Kept on self because _on_confirm reads the selection.
        self._picker = SubjectPicker(catalog)
        card.add_component(self._picker)

        # 6. Confirm is the commit; there is no autosave and no draft, so a
        #    student can toggle freely until they press it.
        confirm_btn = Button(text='Lock in my subjects', role='primary')
        confirm_btn.set_event_handler('click', self._on_confirm)
        # Sign out is a quiet ghost button beside the primary action: it must be
        # reachable (this screen is a gate) without competing with it.
        card.add_component(make_row(confirm_btn, self._sign_out_button()))

    # --- shared bits -------------------------------------------------------
    def _sign_out_button(self):
        """The escape hatch, built once so both the normal and error layouts
        offer exactly the same way out.

        Returns a fresh Button each call — a component can only be parented
        once, so the two layouts need two objects, not one shared one. The
        click handler defers to common._sign_out, which logs out, clears the
        session settings cache, resets the theme and routes to '#login'.
        """
        btn = Button(text='Sign out', role='ghost')
        btn.set_event_handler('click', lambda **e: _sign_out())
        return btn

    def _sign_out_row(self):
        """Sign out on its own row, for the catalog-failure layout where there
        is no primary action to sit beside."""
        return make_row(self._sign_out_button())

    # --- handlers ----------------------------------------------------------
    def _on_confirm(self, **event_args):
        """'Lock in my subjects' pressed: check the two VCE rules, then save.

        Reads the picker, refuses or warns per the rules, and on success calls
        set_subjects (the only write on this screen — user_settings.subjects).
        Returns None; the outcome is a toast, or a navigation to the dashboard.

        These checks are the client's FIRST pass for criterion 7.3. They exist
        to answer instantly and in the student's own words; notes._clean_subjects
        applies the same rules to the same list server-side and stays the
        authority, so nothing here can let a bad selection through.
        """
        # 1. selection is a plain list of canonical subject names, in catalog
        #    order, for the pills currently ticked. The picker holds no other
        #    state, so this is the whole of the student's answer.
        selection = self._picker.get_selection()

        # 2. Validate in the order the student would fix things: something
        # chosen, then maths, then the English warning (a confirm, not a block,
        # because the server can repair it by appending 'English'). Same three
        # checks, same order, same sentences as the Settings change-subjects
        # flow — the rule must not read differently depending on the screen.
        # There is no field to hang a message on (the picker is one component
        # covering the whole card), so these are toasts by necessity.
        if not selection:
            toast_warn(NO_SELECTION_MESSAGE)
            return
        if not any(s in MATHS_GROUP for s in selection):
            toast_error(MATHS_RULE_MESSAGE)
            return
        if not any(s in ENGLISH_GROUP for s in selection):
            # A confirm(), not a toast: the student is being told what the
            # server is about to add on their behalf, and declining has to be
            # possible so they can go back and pick EAL or Literature instead.
            # `selection` is sent UNCHANGED either way — this form never adds
            # 'English' itself, because then two places would be appending it.
            if not confirm(ENGLISH_RULE_MESSAGE):
                return

        # 3. The single write. set_subjects re-runs every rule above, appends
        #    'English' when needed and returns the whole saved settings row.
        try:
            settings = anvil.server.call('set_subjects', selection)
        except Exception as e:
            # The server's subject rules raise sentences written for the
            # student, so friendly_error shows them unchanged; a dropped
            # connection gets the fallback instead of a transport string.
            toast_error(friendly_error(
                e, "Couldn't lock in those subjects. Check your connection "
                   "and try again."))
            return

        # 4. set_subjects returns the saved settings row, so push it straight
        # into the session cache: the router reads it on the next navigation to
        # decide that onboarding is done, with no second round-trip. Skipping
        # this would not just cost a fetch — the gate would still see the
        # cached empty subjects list and bounce straight back to this screen.
        set_session_settings(settings)
        # 5. The theme comes from the same row, so apply it here rather than
        #    leave the dashboard to flash light and then repaint dark.
        apply_theme(settings.get('theme'))
        toast("Subjects locked in — welcome to DotPoint!")
        # 6. navigate() writes the hash and re-enters Main, which now finds a
        #    non-empty subjects list and lets the dashboard through.
        navigate('dashboard')
