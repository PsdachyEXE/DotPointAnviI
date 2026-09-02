import anvil.secrets
import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""Every shared constant the DotPoint server reads (spec section 2).

This module is pure DATA: no functions, no @anvil.server.callable, no mutable
module-level state. Importing it therefore cannot have a side effect, and
nothing defined here is reachable from a browser.

WHY THE VALUES LIVE HERE INSTEAD OF BESIDE THE CODE THAT USES THEM
------------------------------------------------------------------
Nearly every table below is read by more than one module, and a few are
mirrored BY HAND into client code, because Anvil form code cannot import a
server module. One definition per idea is what stops those copies drifting
apart, which is exactly what SAT criterion 7.3 ("no inconsistencies are
present") and NFR06 (maintainability: modules separated by concern, no shared
mutable state) are asking for. tests/test_constants_integrity.py parses this
file with `ast` and asserts the client mirrors still agree with it, so a change
made in only one of the two places fails the suite rather than shipping.

WHAT IS DEFINED HERE
--------------------
  Subject catalog  SUBJECT_GROUPS, CANONICAL_SUBJECTS, ENGLISH_GROUP,
                   MATHS_GROUP, LEGACY_SUBJECT_RENAMES
  Parser tables    SUBJECT_ALIASES, AMBIGUOUS_BARE_ALIASES, TYPE_KEYWORDS,
                   STATUS_KEYWORDS (not wired up — see its own note)
  Urgency          URGENCY_THRESHOLDS
  Stored values    VALID_TYPES, VALID_STATUSES, VALID_CONFIDENCE,
                   STATUS_COMPLETED, STATUS_DEFAULT
  Field bounds     the MIN_* / MAX_* block
  Whitelists       ALLOWED_FILTER_KEYS, ALLOWED_SORT_KEYS,
                   EDITABLE_FIELDS_ASSESSMENT, EDITABLE_FIELDS_NOTE
  Misc             APP_BASE_URL (spec section 6)

Every block below records three things, because rubric 7.2 asks for the "use of
ALL data": what the keys and values MEAN, where the values CAME FROM (VCAA, the
client interview, the SRS, or a measured decision), and which modules READ them.

Landed alongside the Assessments + NLP slice (spec section 10 steps 2 & 4),
which were its first consumers.
"""

# --- Canonical subjects ----------------------------------------------------
# SUBJECT_GROUPS is the onboarding/settings picker catalog: every VCE study the
# app supports, grouped by learning area (source: VCAA "VCE study designs",
# https://www.vcaa.vic.edu.au/curriculum/vce-curriculum/vce-study-designs/vce-study-designs,
# retrieved 2026-07-23). Group and subject order is the display order.
#
# SHAPE: a tuple of (group heading, tuple of study names). Both levels are
# tuples rather than lists so nothing can append to the catalog at runtime — a
# subject that is not in this table must never become selectable, because
# notes.set_subjects validates the student's picks against it.
#
# READ BY: notes.set_subjects (server-side validation of a subject list), and
# mirrored by hand into client_code/OnboardingForm and client_code/SettingsForm,
# which build the actual checkbox lists. Those two mirrors are the ones
# tests/test_constants_integrity.py re-reads with `ast` and compares.
#
# 'Mathematics' (the generic catch-all the parser maps bare 'maths' onto) is
# deliberately NOT in the picker: students lock in a specific maths study.
# It appears BELOW only as a group HEADING — the word above the four maths
# studies — which is why the same string can be a parser sentinel and still
# never be a thing a student can tick. test_constants_integrity asserts both
# halves of that: the sentinel is a heading, and it is not in
# CANONICAL_SUBJECTS.
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

# Flat picker catalog, in display order: the 56 study names from SUBJECT_GROUPS
# with the group headings dropped. Derived rather than typed out a second time,
# so adding a study to SUBJECT_GROUPS cannot leave this list behind — and so the
# catch-all heading 'Mathematics' is structurally unable to appear in it.
#
# This is the membership test the rest of the app means when it says "a real
# subject". READ BY: notes._clean_subjects (validating what the picker sends)
# and notes._safe_subjects (filtering what the column gives back), dashboard
# (filter cleaning), exams (its read guard), nlp._is_canonical_subject
# (filtering the student's locked subjects before alias ranking), and
# AssessmentEditorForm's subject dropdown.
CANONICAL_SUBJECTS = tuple(s for _, group in SUBJECT_GROUPS for s in group)

# The two membership lists behind the VCE program rules that notes._clean_subjects
# enforces on the way in to notes.set_subjects. Each is a flat tuple of study
# names, used only for "did the student pick one of these?" — never for display,
# which is what SUBJECT_GROUPS is for.
#
# The English-group rule is VCAA's ("at least three units from the English group,
# including a Unit 3-4 sequence" —
# https://www.vcaa.vic.edu.au/curriculum/vce-curriculum/vce-study-designs/english-requirement-satisfactory-completion-vce).
# A selection with no English study is not rejected: _clean_subjects appends plain
# 'English' for the student, because the rule is VCAA's and not a mistake they made.
# The mathematics rule is DotPoint's own (client mandate): VCAA does not
# require maths for the VCE, but this app's users must track one, so a selection
# with no maths study IS rejected with a message naming the four choices.
#
# READ BY: notes._clean_subjects (both), exams._get_exam_subjects (English only —
# an exam timetable always includes an English paper), and mirrored by hand into
# OnboardingForm and SettingsForm so the client can grey out Save before a round
# trip. test_constants_integrity checks the mirrors.
#
# MATHS_GROUP deliberately contains 'Mathematics', the parser catch-all, even
# though that is not a selectable study: including it costs nothing (a student's
# stored list is filtered against CANONICAL_SUBJECTS, which excludes it, so it can
# never actually appear there) and it lets nlp._match_subject use the one list to
# ask "is this canonical name a maths study?" of a parser result as well as of a
# student's picks.
ENGLISH_GROUP = (
    'English', 'English as an Additional Language', 'English Language',
    'Literature',
)
MATHS_GROUP = (
    'Mathematics', 'Foundation Mathematics', 'General Mathematics',
    'Mathematical Methods', 'Specialist Mathematics',
)

# Studies VCAA has renamed. KEY = the retired name that may still be sitting in
# a row or an export file; VALUE = the name the app uses now, which must always
# be a member of CANONICAL_SUBJECTS (test_constants_integrity asserts this — a
# rename pointing at a name the catalog no longer holds would "fix" a legacy row
# into a subject the picker cannot show).
#
# Applied at validation time, BEFORE the catalog filter, on every path that
# accepts a subject name: assessments (a payload, including one from an import
# file), dashboard (a filter), notes._safe_subjects (a stored settings list).
# Doing the coercion first is the whole point — a row written in 2022 keeps its
# subject instead of being silently dropped as unknown, so old data stays
# editable and old exports stay importable (FR19).
#
# One entry so far: VCAA renamed Further Mathematics to General Mathematics for
# the 2023-2027 study design. SUBJECT_ALIASES still carries the 'further*'
# aliases separately, because students go on saying the old word out loud.
LEGACY_SUBJECT_RENAMES = {
    'Further Mathematics': 'General Mathematics',
}

# SUBJECT_ALIASES maps every lowercased alias the parser might see onto a
# canonical subject (FR16). KEY = what a student types, always lowercase and
# never punctuated, because nlp._match_subject lowercases the sentence and then
# looks each key up as a whole-word regex (r'\b' + alias + r'\b'), so a
# multi-word key like 'maths methods' matches only that exact spacing.
# VALUE = a CANONICAL_SUBJECTS name, or the 'Mathematics' catch-all.
#
# WHERE THE ALIASES CAME FROM: the canonical name of every study (so the table
# doubles as the accepted-subject list, below), plus the shorthands Will used in
# the client interview and observation — 'methods', 'spesh', 'swd', 'chem',
# 'busman', 'revs'. FR16 asks for at least 13; there are ~125 keys for 56
# studies. Aliases are cheap and a missed one costs a whole parse.
#
# THE RANKING IS NOT IN THIS TABLE. Order here is grouping for a human reader
# only. nlp._match_subject collects EVERY alias hit with its position and then
# picks the winner (longer phrases beat contained tokens, unambiguous aliases
# beat AMBIGUOUS_BARE_ALIASES, locked subjects beat non-locked, then earliest
# mention) — see that function for the algorithm.
#
# READ BY: nlp._match_subject (the parse itself); assessments, which builds
# frozenset(SUBJECT_ALIASES.values()) as the set of subject names a write may
# carry; and dashboard, which unions those values with CANONICAL_SUBJECTS and
# LEGACY_SUBJECT_RENAMES to decide which filter values are worth querying.
# Those two both depend on the invariant that every CANONICAL_SUBJECTS entry
# appears in SUBJECT_ALIASES.values() (via its own name or a shorthand): it is
# what makes the alias table accept the whole picker catalog. Adding a study to
# SUBJECT_GROUPS without adding an alias for it here would make that study
# unsaveable, so test_constants_integrity checks the reverse direction too —
# every alias must resolve to a canonical study or to the catch-all.
#
# 'Further Mathematics' was renamed to 'General Mathematics' by VCAA in 2023;
# the 'further*' aliases are kept for students who still say it.
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
    'general': 'General Mathematics',
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

# The weak tier of the alias table: SUBJECT_ALIASES keys that are also ordinary
# English words, so seeing one is much weaker evidence than seeing 'spesh'.
# Every member must be a key of SUBJECT_ALIASES — this set never adds a way to
# match a subject, it only demotes one that already exists. A frozenset because
# nlp._match_subject only ever asks "is this alias in here?" once per candidate.
#
# WHY EACH ONE IS IN HERE: 'health survey for PE', 'business case study for
# Economics', 'media analysis for Literature', 'general revision for Chemistry' —
# in each of those the weak word appears EARLIER than the real subject, so
# without this tier the earliest-mention rule would pick the wrong study. The
# language names are here for the same reason: 'french revolution' and 'greek
# mythology' use the word as an adjective at least as often as a subject name.
#
# WHAT DEMOTION ACTUALLY COSTS: in nlp._match_subject's sort key, ambiguity is
# the FIRST element, ahead of both the locked-subject preference and position.
# So an unambiguous alias wins from anywhere in the line, and — deliberately —
# it wins even when the ambiguous word names one of the student's own locked
# subjects and the unambiguous one does not. A word that is only sometimes a
# subject name should not outrank a word that always is.
# A weak alias still parses normally when it is the only subject mentioned, so
# 'health essay due Friday' is still Health and Human Development.
#
# READ BY: nlp._match_subject only.
AMBIGUOUS_BARE_ALIASES = frozenset((
    'health', 'business', 'media', 'music', 'art', 'food', 'politics',
    'legal', 'religion', 'eco', 'dance', 'drama', 'computing', 'general',
    'theatre',
    # Languages read as adjectives at least as often as subjects
    # ("french revolution", "greek mythology").
    'chinese', 'french', 'german', 'greek', 'indonesian', 'indo', 'italian',
    'japanese', 'spanish', 'vietnamese', 'viet',
))

# --- Assessment type keywords ----------------------------------------------
# KEY = a canonical assessment type, and every key here is a member of
# VALID_TYPES below, so a parse can never produce a type the write path then
# rejects. VALUE = the lowercased words that trigger it. The vocabulary is the
# one FR03 fixes for the manual form's type dropdown, so the parser and the
# dropdown offer the same six things.
#
# INSERTION ORDER IS THE PRECEDENCE RULE, not a tidy-looking list. nlp._match_type
# walks this dict with a plain `for` and returns on the FIRST keyword that fires
# anywhere in the sentence — and dicts have kept insertion order since Python 3.7,
# so re-sorting these six lines would silently change how a sentence mentioning
# two of them is typed. A line naming both a SAC and a SAT is filed as a SAC.
#
# 'other' carries an EMPTY keyword list, which is not an oversight — it makes
# the fallback unreachable through the loop, so the only way to get 'other' is
# nlp._match_type's explicit `return 'other', None` after the loop. That
# distinction is what the confidence score reads: a type that came from a real
# keyword counts as a detected field, a type that fell through to 'other' does
# not (FR17).
#
# 'test' is folded into 'exam' rather than given a type of its own, because FR03
# fixes the dropdown at these six values: a seventh type would have to be added
# to the dropdown, the dashboard filters and VALID_TYPES before a single row
# could store it.
#
# READ BY: nlp._match_type only.
TYPE_KEYWORDS = {
    'sac': ['sac', 'school assessed coursework'],
    'sat': ['sat', 'school assessed task'],
    'exam': ['exam', 'examination', 'test'],
    'project': ['project', 'assignment', 'prac', 'practical'],
    'homework': ['homework', 'hw'],
    'other': [],
}

# --- Status keywords -------------------------------------------------------
# NOT CURRENTLY WIRED UP — read this before using it. Same shape as
# TYPE_KEYWORDS (KEY = a VALID_STATUSES member, VALUE = the lowercased words
# that would trigger it), and it was written for a parser rule that would set an
# assessment's status from the sentence. That rule was never built: nlp.py does
# not import this table, and a grep of the whole repo finds no reader outside
# this file. Every new assessment therefore starts at STATUS_DEFAULT and the
# student changes it in the editor.
#
# Kept rather than deleted because it is the design for a real planned feature
# and the vocabulary took a while to settle. Anyone wiring it up should note
# that the phrase order inside each list matters the same way TYPE_KEYWORDS'
# does: 'completed' is listed ahead of 'complete' precisely because a walk that
# met the shorter word first would match the front of the longer one.
STATUS_KEYWORDS = {
    'not_started': ['not started', 'todo', 'to do', 'not begun'],
    'in_progress': ['in progress', 'started', 'ongoing', 'wip'],
    'completed': ['completed', 'complete', 'done', 'finished', 'submitted'],
}

# --- Urgency colour bands (FR21) -------------------------------------------
# A list of (threshold, band name) pairs. THE ORDER IS THE RULE, not decoration:
# _datetime._urgency_band walks the list from the top and returns the first band
# whose threshold is >= days_remaining, so the pairs must stay sorted ascending
# by threshold or an overdue assessment would come back as merely 'soon'.
# test_constants_integrity asserts the sort, because nothing about the code
# reading it would fail loudly if someone re-ordered these four lines.
#
# THRESHOLD = the largest days_remaining that still belongs to this band, where
# days_remaining is (due_date - today).days (FR09). Written out concretely:
#   days_remaining < 0           -> 'overdue'
#   0 <= days_remaining <= 3     -> 'today'
#   4 <= days_remaining <= 7     -> 'soon'
#   days_remaining > 7           -> 'distant'
# The numbers are FR21's: overdue, today-or-within-3-days, within-7-days, and
# everything else. FR21 describes the walk as descending and this table is
# ascending; the two produce identical bands, because "first threshold >=
# days_remaining, going up" and "first band that fits, going down" select the
# same row. 9999 is not a real deadline horizon — it is a catch-all large enough
# that the last row always matches, so _urgency_band's return after the loop
# stays unreachable in practice.
#
# READ BY: _datetime._urgency_band, which is the only thing that walks it, and
# whose answer reaches assessments, dashboard and exams. client_code/common
# keeps a hand-written mirror of just the four NAMES (Anvil client code cannot
# import a server module).
URGENCY_THRESHOLDS = [
    (-1, 'overdue'),
    (3, 'today'),
    (7, 'soon'),
    (9999, 'distant'),
]

# Historical note (spec §14): the display colour for each urgency band used to
# live here and be mirrored by hand into the forms. It now lives in the
# stylesheet (anvil.yaml native_deps.head_html) as the CSS variables
# --dp-overdue / --dp-duetoday / --dp-soon / --dp-distant, because a hex value
# baked into Python cannot change with the light/dark theme. The server's job
# is to say WHICH band an assessment is in; how that band looks is the client's.
# The band names above are therefore the whole contract between the two.

# --- Stored value sets (the enums) -----------------------------------------
# These are the permitted contents of the assessments.type, assessments.status and
# assessments.confidence columns. They live here, rather than privately inside
# assessments.py where they used to, because FOUR modules need to agree about them:
# assessments.py validates writes against them, reminders.py tests a stored status
# before emailing, dashboard.py filters on them, and the import path checks them.
# One definition means those four can never drift apart — which is exactly what SAT
# criterion 7.3's "no inconsistencies are present" is asking for.
#
# NOTE ON CASE: these are lowercase, while SAT 5 section 4.2.1 shows Title Case
# ({SAC, SAT, Test, ...}). That divergence is deliberate and is recorded in
# docs/DISCREPANCIES.md — the values are persisted in every existing row and mirrored
# in both client forms, so the code governs. Do not "correct" them toward the
# document; it would invalidate every stored record and every export file.
VALID_TYPES = frozenset(('sac', 'sat', 'exam', 'project', 'homework', 'other'))
VALID_STATUSES = frozenset(('not_started', 'in_progress', 'completed'))
VALID_CONFIDENCE = frozenset(('HIGH', 'MEDIUM', 'LOW'))

# The single status that means "no more reminders, hide from the default list".
# Named so the two modules that test for it cannot disagree by a typo.
STATUS_COMPLETED = 'completed'
STATUS_DEFAULT = 'not_started'

# --- Field bounds ----------------------------------------------------------
# Every numeric and length limit the validators enforce, in one place, so the
# per-field validation table in docs/VALIDATION.md has a single source to cite.
MAX_TITLE_LENGTH = 200           # assessments.title and notes.title
MAX_DESCRIPTION_LENGTH = 2000    # assessments.description — one screen of context
MAX_NOTE_CONTENT_LENGTH = 20000  # notes.content — a full page of study notes
MAX_TAG_LENGTH = 40              # one notes.tags entry
MAX_TAGS_PER_NOTE = 20
MAX_SOURCE_TEXT_LENGTH = 500     # the raw sentence the parser was given
MIN_WEIGHT = 0.0                 # assessments.weight, a percentage
MAX_WEIGHT = 100.0
# Reminder offsets are "days before due". One day is the shortest useful warning and
# a year is longer than any VCE assessment is set in advance; the upper bound exists
# because an unbounded value (e.g. 999999) made every assessment permanently "due
# soon" and emailed the student about all of them on the first scheduler tick.
MIN_REMINDER_DAY = 1
MAX_REMINDER_DAY = 365
MAX_REMINDER_DAYS_PER_ASSESSMENT = 6
MAX_BULK_LINES = 100             # one paste of the bulk-add box
MAX_PARSER_INPUT_LENGTH = 500    # one sentence into the parser box
MAX_SUBJECTS_PER_STUDENT = 12    # a VCE program is 4-6 studies; 12 is generous
MIN_TERM_NUMBER = 1              # Victorian school year has four terms
MAX_TERM_NUMBER = 4

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
