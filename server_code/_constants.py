import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Module-level immutable constants for the DotPoint server.

Defines: SUBJECT_ALIASES, TYPE_KEYWORDS, STATUS_KEYWORDS, URGENCY_THRESHOLDS,
ALLOWED_FILTER_KEYS, ALLOWED_SORT_KEYS, EDITABLE_FIELDS_ASSESSMENT,
EDITABLE_FIELDS_NOTE, plus APP_BASE_URL (per spec section 6).

No functions, no callables (spec section 2). Landed alongside the Assessments +
NLP slice (spec section 10 steps 2 & 4), which are its first consumers.
"""

# --- Canonical subjects ----------------------------------------------------
# The 11 canonical names the UI displays. SUBJECT_ALIASES maps every lowercased
# alias the parser might see onto one of these (FR16). Multi-word aliases
# (e.g. 'math methods', 'phys ed') are matched as phrases before single tokens
# by nlp._match_subject.
SUBJECT_ALIASES = {
    # Mathematics family
    'math': 'Mathematics',
    'maths': 'Mathematics',
    'mathematics': 'Mathematics',
    'methods': 'Mathematical Methods',
    'method': 'Mathematical Methods',
    'math methods': 'Mathematical Methods',
    'maths methods': 'Mathematical Methods',
    'mathematical methods': 'Mathematical Methods',
    'spec': 'Specialist Mathematics',
    'specialist': 'Specialist Mathematics',
    'specialist maths': 'Specialist Mathematics',
    'specialist mathematics': 'Specialist Mathematics',
    'further': 'Further Mathematics',
    'further maths': 'Further Mathematics',
    'further mathematics': 'Further Mathematics',
    # English
    'eng': 'English',
    'english': 'English',
    # Sciences
    'chem': 'Chemistry',
    'chemistry': 'Chemistry',
    'bio': 'Biology',
    'biology': 'Biology',
    'phys': 'Physics',
    'physics': 'Physics',
    # Software Development
    'swd': 'Software Development',
    'sd': 'Software Development',
    'software': 'Software Development',
    'software dev': 'Software Development',
    'software development': 'Software Development',
    # Humanities / other
    'geo': 'Geography',
    'geography': 'Geography',
    'pe': 'Physical Education',
    'phys ed': 'Physical Education',
    'physical education': 'Physical Education',
}

# --- Assessment type keywords ----------------------------------------------
# Maps canonical type -> lowercased trigger keywords. 'other' is the fallback:
# it never appears as a keyword match, it is assigned when no keyword fires.
TYPE_KEYWORDS = {
    'sac': ['sac', 'school assessed coursework'],
    'sat': ['sat', 'school assessed task'],
    'exam': ['exam', 'examination', 'test'],
    'project': ['project', 'assignment', 'prac', 'practical'],
    'homework': ['homework', 'hw'],
    'other': [],
}

# --- Status keywords -------------------------------------------------------
STATUS_KEYWORDS = {
    'not_started': ['not started', 'todo', 'to do', 'not begun'],
    'in_progress': ['in progress', 'started', 'ongoing', 'wip'],
    'completed': ['completed', 'complete', 'done', 'finished', 'submitted'],
}

# --- Urgency colour bands (FR21) -------------------------------------------
# Ordered ascending by threshold; walked in order, first threshold >=
# days_remaining wins (see _datetime._urgency_band). Concrete bands:
#   days_remaining < 0          -> 'overdue'
#   0 <= days_remaining <= 3     -> 'today'
#   4 <= days_remaining <= 7     -> 'soon'
#   days_remaining > 7           -> 'distant'
URGENCY_THRESHOLDS = [
    (-1, 'overdue'),
    (3, 'today'),
    (7, 'soon'),
    (9999, 'distant'),
]

# Display colours for each urgency band, reused by the list card border and the
# (later) calendar cell fill so the colour map has a single source of truth.
URGENCY_COLOURS = {
    'overdue': '#d64550',   # red
    'today': '#e8833a',     # orange
    'soon': '#3b7dd8',      # blue
    'distant': '#9aa0a6',   # neutral grey
}

# --- Whitelists ------------------------------------------------------------
ALLOWED_FILTER_KEYS = {'subjects', 'types', 'statuses', 'show_completed', 'sort_by', 'month'}
ALLOWED_SORT_KEYS = {'due_date', 'weight', 'subject'}

# Fields a client is permitted to edit (FR04 / EC-SEC-03). 'confidence',
# 'source_text', 'user', 'created_at' are deliberately excluded so the parser
# audit trail survives edits.
EDITABLE_FIELDS_ASSESSMENT = (
    'title', 'subject', 'type', 'due_date', 'start_date', 'weight',
    'status', 'description', 'reminder_days', 'linked_note_ids', 'term_info',
)

EDITABLE_FIELDS_NOTE = (
    'title', 'content', 'tags', 'is_pinned',
)

# --- Misc ------------------------------------------------------------------
# Base URL used in reminder email links (spec section 6). Update to the app's
# published URL once known.
APP_BASE_URL = 'https://dotpoint.anvil.app'
