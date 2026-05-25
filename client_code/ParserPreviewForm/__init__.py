import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""ParserPreviewForm - confirm / edit nlp.parse_text output before save.

Implementation pending - see IMPLEMENTATION_SPEC.md section 3 (ParserPreviewForm).
"""

from anvil import ColumnPanel


class ParserPreviewForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        pass
