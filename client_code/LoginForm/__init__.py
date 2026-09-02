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
Bootstrap form, and re-opens with a message beside the offending box until the
details pass the client checks below (SAT criterion 7.3).

See IMPLEMENTATION_SPEC.md section 3 (LoginForm) and section 5 (Authentication).
"""

import anvil.server
from anvil import ColumnPanel, Label, Button, Spacer, TextBox, alert

from ..common import (
    navigate, toast_error, make_field, make_divider, clear_session_settings,
    set_field_error, friendly_error,
)

# --- credential rules (mirrors of the server's) ------------------------------
# The client cannot import server_code, so these three rules are restated here.
# They must keep saying exactly what the server says, because the server is still
# the authority and a student who is told two different things about one box has
# been told nothing. Sources: notes.create_account (_MIN_PASSWORD_LENGTH = 8) and
# _validation.require_email / require_text(max_length=254).
_MIN_PASSWORD_LENGTH = 8
_MAX_EMAIL_LENGTH = 254

# Shown BEFORE the student types, not after they get it wrong: there is no
# "confirm password" box on this dialog, so the requirement has to be readable at
# the moment the password is being chosen.
_PASSWORD_HINT = ('At least %d characters. There is no confirm box, so check it '
                  'before you continue.' % _MIN_PASSWORD_LENGTH)
_EMAIL_HINT = ('You sign in with this address, and the app cannot change it '
               'later — check it carefully.')


def _looks_like_email(text):
    """Mirror of _validation._EMAIL_PATTERN (^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$).

    Written out rather than compiled from the same pattern string because the two
    halves of the app cannot share a module. Deliberately permissive for the same
    reason the server's is: the job is to catch "sam@gmail" or "sam gmail.com",
    not to police the RFC.
    """
    if not text or any(character.isspace() for character in text):
        return False
    parts = text.split('@')
    if len(parts) != 2:
        return False           # no @ at all, or more than one
    local_part, domain = parts
    if not local_part or not domain:
        return False           # "@example.com" / "sam@"
    # The pattern's domain half is [^@\s]+\.[^@\s]+ — a dot with at least one
    # character on each side of it. Slicing off the first and last characters
    # leaves exactly the positions such a dot could occupy, so this is the same
    # test rather than a near-enough one; being STRICTER than the server here
    # would refuse an address the server would have accepted.
    return '.' in domain[1:-1]


def _email_error(email):
    """Return the message for a bad email address, or None when it is usable."""
    if not email:
        return 'Email address is required.'
    if len(email) > _MAX_EMAIL_LENGTH:
        return ('Email address is too long — keep it to %d characters or fewer '
                '(currently %d).' % (_MAX_EMAIL_LENGTH, len(email)))
    if not _looks_like_email(email):
        # Word for word what require_email raises, so the client's first pass and
        # the server's authoritative one can never contradict each other.
        return ('That does not look like an email address. '
                'Check it looks like name@example.com.')
    return None


def _password_error(password, is_sign_up):
    """Return the message for a bad password, or None when it is usable.

    The length rule applies to SIGN-UP only, matching the server: enforcing it on
    sign-in would lock out any account made before the rule existed, and would
    answer a wrong-length password differently from a wrong one, which tells a
    stranger something about the account.
    """
    if not password:
        return 'Password is required.'
    if is_sign_up and len(password) < _MIN_PASSWORD_LENGTH:
        return ('Your password needs to be at least %d characters long.'
                % _MIN_PASSWORD_LENGTH)
    return None


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
    def _prompt_credentials(self, title, action_label, is_sign_up):
        """Collect a usable email + password. Returns (email, password) or None.

        Both fields go through make_field() so the label/control pairing and
        spacing match every other form in the app. `action_label` is the
        affirmative button's text, so the same dialog serves sign-in and
        sign-up without the caller needing two builders.

        The dialog is shown in a loop because Anvil's alert() cannot be told to
        stay open: a submission that fails the checks below re-opens the same
        dialog with what was typed still in the boxes and the message sitting
        under the box it is about (SRS FR04), rather than closing the dialog and
        firing a toast at the far corner of the screen. Cancel always exits.
        """
        email_text = ''
        password_text = ''
        email_message = None
        password_message = None

        while True:
            panel = ColumnPanel()
            panel.spacing_above = 'none'
            panel.spacing_below = 'none'

            # Rebuilt each pass rather than re-shown, so re-opening can never
            # depend on Anvil letting a component be re-parented into a second
            # alert. The text is carried over by hand instead.
            email_box = TextBox(placeholder='email@example.com', text=email_text)
            email_field = make_field('Email', email_box, required=True,
                                     hint=_EMAIL_HINT if is_sign_up else None)
            panel.add_component(email_field)

            # hide_text is what makes this a password field; the label already says
            # 'Password', so a placeholder repeating it would just be noise. The
            # hint is sign-up only: on sign-in the password already exists, so
            # stating the rule would only invite the student to doubt a correct one.
            password_box = TextBox(hide_text=True, text=password_text)
            password_field = make_field('Password', password_box, required=True,
                                        hint=_PASSWORD_HINT if is_sign_up else None)
            panel.add_component(password_field)

            set_field_error(email_field, email_message)
            set_field_error(password_field, password_message)

            confirmed = alert(panel, title=title,
                              buttons=[(action_label, True), ('Cancel', False)])
            if not confirmed:
                return None

            email_text = (email_box.text or '').strip()
            password_text = password_box.text or ''
            email_message = _email_error(email_text)
            password_message = _password_error(password_text, is_sign_up)
            if email_message is None and password_message is None:
                return email_text, password_text

    # --- handlers ----------------------------------------------------------
    def _on_sign_in_click(self, **event_args):
        self._authenticate('sign_in_with_email', 'Sign in', 'Sign in',
                           is_sign_up=False)

    def _on_sign_up_click(self, **event_args):
        self._authenticate('create_account', 'Create an account',
                           'Create account', is_sign_up=True)

    def _authenticate(self, server_callable_name, title, action_label, is_sign_up):
        """Prompt, then call the named server callable.

        `server_callable_name` is a STRING — the registered name of a callable in
        notes.py — not a function object, because anvil.server.call takes the name.
        `is_sign_up` selects the stricter rules and the hints: only account
        creation can fix a bad address or a short password.
        """
        creds = self._prompt_credentials(title, action_label, is_sign_up)
        if creds is None:
            return  # cancelled
        # No blank check here: _prompt_credentials only returns once both fields
        # have passed, and the server checks them again regardless.
        email, password = creds
        try:
            anvil.server.call(server_callable_name, email, password)
        except Exception as e:
            # EVERY failure lands here, not just a refused credential — a dropped
            # connection and an Anvil platform error raise through the same path.
            # The server's own refusals are written for the student ("An account
            # with that email already exists — try signing in.", "Incorrect email
            # or password."), and friendly_error() is what lets those through
            # while replacing anything else with a sentence worth reading.
            # The dialog has closed by now, so a toast is the only place left to
            # put the message.
            toast_error(friendly_error(e))
            return
        # Fresh account context: never reuse a previous session's cached
        # settings (theme / onboarding gate read them on the next route).
        clear_session_settings()
        # navigate() sets the hash and re-enters the router itself. It has to
        # render directly rather than wait for the hashchange it raises: this
        # call happens the instant the sign-in dialog closes, and Main's
        # listener ignores events raised while a dialog is fading out.
        navigate('dashboard')
