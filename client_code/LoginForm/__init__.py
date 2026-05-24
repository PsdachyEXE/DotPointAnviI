"""LoginForm - gate the app behind Anvil Users authentication (FR20).

Implementation pending - see IMPLEMENTATION_SPEC.md section 3 (LoginForm)
and section 5 (Authentication).
"""

from anvil import ColumnPanel


class LoginForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        pass
