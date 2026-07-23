import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Module-level immutable constants for the DotPoint server.

Defines: SUBJECT_GROUPS, CANONICAL_SUBJECTS, ENGLISH_GROUP, MATHS_GROUP,
SUBJECT_ALIASES, TYPE_KEYWORDS, STATUS_KEYWORDS, URGENCY_THRESHOLDS,
ALLOWED_FILTER_KEYS, ALLOWED_SORT_KEYS, EDITABLE_FIELDS_ASSESSMENT,
EDITABLE_FIELDS_NOTE, plus APP_BASE_URL (per spec section 6).

No functions, no callables (spec section 2). Landed alongside the Assessments +
NLP slice (spec section 10 steps 2 & 4), which are its first consumers.
"""

# --- Canonical subjects ----------------------------------------------------
# SUBJECT_GROUPS is the onboarding/settings picker catalog: every VCE study the
# app supports, grouped by learning area (source: VCAA "VCE study designs",
# https://www.vcaa.vic.edu.au/curriculum/vce-curriculum/vce-study-designs/vce-study-designs,
# retrieved 2026-07-23). Group and subject order is the display order.
# 'Mathematics' (the generic catch-all the parser maps bare 'maths' onto) is
# deliberately NOT in the picker: students lock in a specific maths study.
SUBJECT_GROUPS = (
    ('English', (
        'English', 'English as an Additional Language', 'English Language',
        'Literature',
    )),
    ('Mathematics', (
        'Foundation Mathematics', 'General Mathematics', 'Mathematical Methods',
        'Specialist Mathematics',
    )),
    ('Sciences', (
        'Biology', 'Chemistry', 'Environmental Science', 'Physics', 'Psychology',
    )),
    ('Humanities', (
        'Classical Studies', 'Geography', 'History: Ancient History',
        'History: Australian History', 'History: Revolutions', 'Philosophy',
        'Politics', 'Religion and Society', 'Sociology', 'Texts and Traditions',
    )),
    ('Business & Economics', (
        'Accounting', 'Business Management', 'Economics',
        'Industry and Enterprise', 'Legal Studies',
    )),
    ('Technologies', (
        'Algorithmics', 'Applied Computing', 'Data Analytics', 'Food Studies',
        'Product Design and Technologies', 'Software Development',
        'Systems Engineering',
    )),
    ('The Arts', (
        'Art Creative Practice', 'Art Making and Exhibiting', 'Dance', 'Drama',
        'Media', 'Music', 'Theatre Studies', 'Visual Communication Design',
    )),
    ('Health & PE', (
        'Health and Human Development', 'Outdoor and Environmental Studies',
        'Physical Education',
    )),
    ('Languages', (
        'Chinese', 'French', 'German', 'Greek', 'Indonesian', 'Italian',
        'Japanese', 'Spanish', 'Vietnamese',
    )),
    ('Other', (
        'Extended Investigation',
    )),
)

# Flat picker catalog, in display order.
CANONICAL_SUBJECTS = tuple(s for _, group in SUBJECT_GROUPS for s in group)

# VCE program rules enforced by notes.set_subjects. The English-group rule is
# VCAA's ("at least three units from the English group, including a Unit 3-4
# sequence" — https://www.vcaa.vic.edu.au/curriculum/vce-curriculum/vce-study-designs/english-requirement-satisfactory-completion-vce).
# The mathematics rule is DotPoint's own (client mandate): VCAA does not
# require maths for the VCE, but this app's users must track one.
ENGLISH_GROUP = (
    'English', 'English as an Additional Language', 'English Language',
    'Literature',
)
MATHS_GROUP = (
    'Mathematics', 'Foundation Mathematics', 'General Mathematics',
    'Mathematical Methods', 'Specialist Mathematics',
)

# SUBJECT_ALIASES maps every lowercased alias the parser might see onto a
# canonical subject (FR16). Multi-word aliases (e.g. 'math methods', 'phys ed')
# are matched as phrases before single tokens by nlp._match_subject. Every
# CANONICAL_SUBJECTS entry has at least its own lowercased name here, so
# assessments._validate_assessment_payload (subject in SUBJECT_ALIASES.values())
# accepts the whole picker catalog. 'Further Mathematics' was renamed to
# 'General Mathematics' by VCAA in 2023; the 'further*' aliases are kept for
# students who still say it.
SUBJECT_ALIASES = {
    # Mathematics family ('math'/'maths' -> the generic catch-all; the parser
    # re-points these at the student's own maths study when they have locked
    # in exactly one — see nlp._match_subject).
    'math': 'Mathematics',
    'maths': 'Mathematics',
    'mathematics': 'Mathematics',
    'methods': 'Mathematical Methods',
    'method': 'Mathematical Methods',
    'math methods': 'Mathematical Methods',
    'maths methods': 'Mathematical Methods',
    'mathematical methods': 'Mathematical Methods',
    'spec': 'Specialist Mathematics',
    'spesh': 'Specialist Mathematics',
    'specialist': 'Specialist Mathematics',
    'specialist maths': 'Specialist Mathematics',
    'specialist mathematics': 'Specialist Mathematics',
    'further': 'General Mathematics',
    'further maths': 'General Mathematics',
    'further mathematics': 'General Mathematics',
    'general maths': 'General Mathematics',
    'general mathematics': 'General Mathematics',
    'gen maths': 'General Mathematics',
    'foundation maths': 'Foundation Mathematics',
    'foundation mathematics': 'Foundation Mathematics',
    # English group
    'eng': 'English',
    'english': 'English',
    'eal': 'English as an Additional Language',
    'english as an additional language': 'English as an Additional Language',
    'english language': 'English Language',
    'eng lang': 'English Language',
    'englang': 'English Language',
    'lit': 'Literature',
    'literature': 'Literature',
    # Sciences
    'chem': 'Chemistry',
    'chemistry': 'Chemistry',
    'bio': 'Biology',
    'biology': 'Biology',
    'phys': 'Physics',
    'physics': 'Physics',
    'psych': 'Psychology',
    'psychology': 'Psychology',
    'enviro': 'Environmental Science',
    'enviro science': 'Environmental Science',
    'environmental science': 'Environmental Science',
    # Technologies
    'swd': 'Software Development',
    'sd': 'Software Development',
    'software': 'Software Development',
    'softdev': 'Software Development',
    'software dev': 'Software Development',
    'software development': 'Software Development',
    'data analytics': 'Data Analytics',
    'applied computing': 'Applied Computing',
    'computing': 'Applied Computing',
    'algorithmics': 'Algorithmics',
    'algos': 'Algorithmics',
    'systems engineering': 'Systems Engineering',
    'sys eng': 'Systems Engineering',
    'product design': 'Product Design and Technologies',
    'product design and technologies': 'Product Design and Technologies',
    'food studies': 'Food Studies',
    'food tech': 'Food Studies',
    'food': 'Food Studies',
    # Humanities
    'geo': 'Geography',
    'geog': 'Geography',
    'geography': 'Geography',
    'revs': 'History: Revolutions',
    'revolutions': 'History: Revolutions',
    'history revolutions': 'History: Revolutions',
    'ancient history': 'History: Ancient History',
    'australian history': 'History: Australian History',
    'aus history': 'History: Australian History',
    'philosophy': 'Philosophy',
    'philo': 'Philosophy',
    'politics': 'Politics',
    'global politics': 'Politics',
    'sociology': 'Sociology',
    'socio': 'Sociology',
    'classics': 'Classical Studies',
    'classical studies': 'Classical Studies',
    'religion and society': 'Religion and Society',
    'religion': 'Religion and Society',
    'texts and traditions': 'Texts and Traditions',
    # Business & Economics
    'accounting': 'Accounting',
    'business': 'Business Management',
    'busman': 'Business Management',
    'business management': 'Business Management',
    'economics': 'Economics',
    'eco': 'Economics',
    'econ': 'Economics',
    'legal': 'Legal Studies',
    'legal studies': 'Legal Studies',
    'industry and enterprise': 'Industry and Enterprise',
    # The Arts
    'media': 'Media',
    'viscom': 'Visual Communication Design',
    'vcd': 'Visual Communication Design',
    'vis com': 'Visual Communication Design',
    'visual communication design': 'Visual Communication Design',
    'art': 'Art Creative Practice',
    'art creative practice': 'Art Creative Practice',
    'art making': 'Art Making and Exhibiting',
    'art making and exhibiting': 'Art Making and Exhibiting',
    'drama': 'Drama',
    'dance': 'Dance',
    'music': 'Music',
    'theatre': 'Theatre Studies',
    'theatre studies': 'Theatre Studies',
    # Health & PE
    'pe': 'Physical Education',
    'phys ed': 'Physical Education',
    'physical education': 'Physical Education',
    'hhd': 'Health and Human Development',
    'health': 'Health and Human Development',
    'health and human development': 'Health and Human Development',
    'outdoor ed': 'Outdoor and Environmental Studies',
    'oes': 'Outdoor and Environmental Studies',
    'outdoor and environmental studies': 'Outdoor and Environmental Studies',
    # Languages
    'chinese': 'Chinese',
    'french': 'French',
    'german': 'German',
    'greek': 'Greek',
    'indonesian': 'Indonesian',
    'indo': 'Indonesian',
    'italian': 'Italian',
    'japanese': 'Japanese',
    'spanish': 'Spanish',
    'vietnamese': 'Vietnamese',
    'viet': 'Vietnamese',
    # Other
    'extended investigation': 'Extended Investigation',
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
# Base URL used in reminder email links (spec section 6): the app's published
# public URL (Anvil Hobby-plan environment).
APP_BASE_URL = 'https://honored-willing-tea.anvil.app'
