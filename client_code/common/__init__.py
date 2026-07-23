import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Client-side shared helpers.

Slice 1 (§10 step 1) provides the shared top navigation bar used by every
top-level form. The subjects/theming slice (§11-§12) adds a per-session
settings cache (so the router can gate onboarding and apply the theme without
a server round-trip per navigation), apply_theme, and the SubjectPicker panel
shared by OnboardingForm and the Settings change-subjects flow.

See IMPLEMENTATION_SPEC.md section 0 (Common helpers) and section 3 (Client Forms).
"""

import anvil
import anvil.server
import anvil.users
from anvil import (
    ColumnPanel, FlowPanel, Label, Link, Button, CheckBox, open_form,
)


def _navigate(hash_value):
    """Set the URL hash and re-enter the Main router (spec §4 navigation)."""
    anvil.set_url_hash(hash_value)
    open_form('Main')


def _sign_out():
    anvil.users.logout()
    clear_session_settings()
    apply_theme('light')
    anvil.set_url_hash('login')
    open_form('Main')


def make_top_bar():
    """Build the shared top navigation bar (spec §3 DashboardForm top bar).

    'DotPoint' is the home link (-> dashboard); Notes / Exams / Settings /
    Import-Export route via the Main hash router; Sign out logs out and
    returns to login.
    """
    bar = FlowPanel(role='topbar')

    title = Link(text='DotPoint', role='heading')
    title.set_event_handler('click', lambda **e: _navigate('dashboard'))
    bar.add_component(title)

    for label, hash_value in (('Notes', 'notes'),
                              ('Exams', 'exams'),
                              ('Settings', 'settings'),
                              ('Import/Export', 'import-export')):
        link = Link(text=label)
        link.set_event_handler('click', lambda h=hash_value, **e: _navigate(h))
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
    """Toggle the dark palette by flipping body.dotpoint-dark (CSS variables
    in anvil.yaml native_deps.head_html do the rest)."""
    try:
        from anvil.js.window import document
        if theme == 'dark':
            document.body.classList.add('dotpoint-dark')
        else:
            document.body.classList.remove('dotpoint-dark')
    except Exception:
        pass  # never let theming break navigation


# --- shared subject picker (spec §11) ----------------------------------------

class SubjectPicker(ColumnPanel):
    """Grouped multi-select over the VCE subject catalog.

    Dumb component: the caller fetches the catalog (get_subject_catalog) and
    reads get_selection() back; validation happens server-side in
    set_subjects. Used by OnboardingForm and the Settings change flow.
    """

    def __init__(self, catalog, selected=None, **properties):
        super().__init__(**properties)
        selected = set(selected or [])
        self._checks = []   # [(subject, CheckBox), ...] in catalog order
        for group in catalog:
            self.add_component(Label(text=group['group'], role='heading',
                                     spacing_above='small'))
            row = FlowPanel()
            for subject in group['subjects']:
                cb = CheckBox(text=subject, checked=subject in selected)
                self._checks.append((subject, cb))
                row.add_component(cb)
            self.add_component(row)

    def get_selection(self):
        return [s for s, cb in self._checks if cb.checked]
