import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""LoginForm - gate the app behind authentication (FR20).

WORKAROUND: Anvil's client-initiated login_with_form / signup_with_form raise
"PermissionDenied: Cannot access this table from server code" on the users table
(a Users-service<->table binding issue surfaced after a schema resync). So this
form uses a custom email/password dialog that calls trusted server-module
callables (notes.sign_in_with_email / notes.create_account), which run with this
app's full users-table access and sidestep that path. Revert to
login_with_form/signup_with_form once the binding is fixed (Anvil support / table
permission re-apply).

Layout: this is the only screen with no top bar and no nav — a logged-out user
has nowhere else to go, so the page is a single centred role='authcard' holding
the wordmark, a one-line pitch, and the two ways in. The type scale comes
entirely from the roles ('display' / 'caption' / 'micro'), so the card follows
the light and dark palettes without this form knowing a single colour.

Credentials are collected by _prompt_credentials(), which reuses the shared
make_field() builder so the dialog reads as part of the app rather than as a raw
Bootstrap form.

See IMPLEMENTATION_SPEC.md section 3 (LoginForm) and section 5 (Authentication).
"""

import anvil.server
from anvil import ColumnPanel, Label, Button, Spacer, TextBox, alert

from ..common import (
    navigate, toast_error, make_field, make_divider, clear_session_settings,
)


class LoginForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        # The one deliberate pixel value in this form. The card is the only
        # thing on the page, so it needs to be pushed off the top edge to sit
        # near the optical centre; there is no sibling content to space against.
        self.add_component(Spacer(height=72))

        card = ColumnPanel(role='authcard')

        # Identity block: what the app is called, then what it is, then what it
        # does — three lines of decreasing weight so the eye lands on the
        # wordmark first and the pitch is read only if the user wants it.
        card.add_component(Label(text='DotPoint', align='center',
                                 role='display'))
        card.add_component(Label(text='Assessment Tracker', align='center',
                                 role='caption'))
        card.add_component(Label(
            text='Type "Methods SAC2 due Friday week 5 worth 25%" and it\'s tracked.',
            align='center', role='micro'))

        # A rule, not a gap: it separates "what this is" from "what you do
        # next", which is the only hierarchy this screen needs.
        card.add_component(make_divider())

        sign_in = Button(text='Sign in', role='primary', align='center')
        sign_in.set_event_handler('click', self._on_sign_in_click)
        card.add_component(sign_in)

        sign_up = Button(text='Create an account', role='secondary', align='center')
        sign_up.set_event_handler('click', self._on_sign_up_click)
        card.add_component(sign_up)

        self.add_component(card)

    # --- credential prompt -------------------------------------------------
    def _prompt_credentials(self, title, action_label):
        """Show an email + password dialog. Returns (email, password) or None.

        Both fields go through make_field() so the label/control pairing and
        spacing match every other form in the app. `action_label` is the
        affirmative button's text, so the same dialog serves sign-in and
        sign-up without the caller needing two builders.
        """
        panel = ColumnPanel()
        panel.spacing_above = 'none'
        panel.spacing_below = 'none'

        email_box = TextBox(placeholder='email@example.com')
        panel.add_component(make_field('Email', email_box))

        # hide_text is what makes this a password field; the label already says
        # 'Password', so a placeholder repeating it would just be noise.
        password_box = TextBox(hide_text=True)
        panel.add_component(make_field('Password', password_box))

        confirmed = alert(panel, title=title,
                          buttons=[(action_label, True), ('Cancel', False)])
        if not confirmed:
            return None
        return (email_box.text or '').strip(), (password_box.text or '')

    # --- handlers ----------------------------------------------------------
    def _on_sign_in_click(self, **event_args):
        self._authenticate('sign_in_with_email', 'Sign in', 'Sign in')

    def _on_sign_up_click(self, **event_args):
        self._authenticate('create_account', 'Create an account', 'Create account')

    def _authenticate(self, server_fn, title, action_label):
        creds = self._prompt_credentials(title, action_label)
        if creds is None:
            return  # cancelled
        email, password = creds
        if not email or not password:
            toast_error('Email and password are required.')
            return
        try:
            anvil.server.call(server_fn, email, password)
        except Exception as e:
            # The server raises with a message written for the student
            # ("That email is already registered."), so show it as-is.
            toast_error(str(e))
            return
        # Fresh account context: never reuse a previous session's cached
        # settings (theme / onboarding gate read them on the next route).
        clear_session_settings()
        # navigate() sets the hash and re-enters the router itself. It has to
        # render directly rather than wait for the hashchange it raises: this
        # call happens the instant the sign-in dialog closes, and Main's
        # listener ignores events raised while a dialog is fading out.
        navigate('dashboard')
