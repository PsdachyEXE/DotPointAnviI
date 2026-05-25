import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""AssessmentEditorForm - create / edit / bulk-add assessments.

Implementation pending - see IMPLEMENTATION_SPEC.md section 3 (AssessmentEditorForm).
"""

from anvil import ColumnPanel


class AssessmentEditorForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        pass
