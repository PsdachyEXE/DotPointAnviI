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
#
# READ BY: nothing. Re-checked 2026-09-02 by grepping the whole repo for the
# name — the only hits are this definition, the index at the top of this file,
# and the docs. It is the ONE constant in this module with no live consumer, so
# it is called out here rather than left for a reader to discover.
#
# Note that this is NOT the same situation as ALLOWED_FILTER_KEYS' inert
# 'sort_by' member: that table is live and one of its entries is unused, while
# this whole table is dormant.
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
#
# frozensets, not tuples, because every use is a membership test ("is this
# value allowed?") and never a display order — the human-readable labels and
# their order live in the client forms. Frozen so an import cannot mutate them.
#
# VALID_TYPES — the six kinds of assessment FR03 fixes for the type dropdown.
#   'sac'/'sat' are the VCE assessment tasks (School Assessed Coursework /
#   School Assessed Task), 'exam' also covers a class test (see TYPE_KEYWORDS),
#   'project' covers assignments and pracs, 'homework' is a small set task, and
#   'other' is the catch-all the parser falls back to when it recognises no
#   keyword. Every TYPE_KEYWORDS key is a member, so a parse can never produce a
#   type the write path rejects (test_constants_integrity asserts that).
#
# VALID_STATUSES — where the student is up to. 'not_started' is where every new
#   row begins (STATUS_DEFAULT), 'in_progress' is set by hand, and 'completed'
#   is the one status other code branches on (STATUS_COMPLETED).
#
# VALID_CONFIDENCE — how much of a sentence the parser understood (FR17), set by
#   nlp._score. UPPERCASE, unlike the other two, because these are shown to the
#   student as a pill rather than stored as a machine value; a manually created
#   row stores None here, which is why the validators accept None as well.
VALID_TYPES = frozenset(('sac', 'sat', 'exam', 'project', 'homework', 'other'))
VALID_STATUSES = frozenset(('not_started', 'in_progress', 'completed'))
VALID_CONFIDENCE = frozenset(('HIGH', 'MEDIUM', 'LOW'))

# The single status that means "no more reminders, hide from the default list".
# Named so the three modules that use it cannot disagree by a typo: reminders
# skips a completed assessment before it emails, dashboard leaves it out of the
# "upcoming" sidebar panel, and assessments subtracts it from VALID_STATUSES to
# build the query behind FR06's "hide completed by default".
#
# STATUS_DEFAULT is what create_assessment stamps when the client sends no
# status — 'not_started' rather than 'in_progress' because the app has no way of
# knowing the student has begun, and guessing would defeat the reminders.
#
# Both are separate names rather than literals sprinkled through four modules:
# a typo in a bare 'completed' would not raise, it would just quietly stop
# matching, and an assessment would keep emailing after being marked done.
STATUS_COMPLETED = 'completed'
STATUS_DEFAULT = 'not_started'

# --- Field bounds ----------------------------------------------------------
# Every numeric and length limit the validators enforce, in one place, so the
# per-field validation table in docs/VALIDATION.md has a single source to cite,
# and so the client can grey out Save on the same number the server rejects on
# (AssessmentEditorForm and NoteEditorForm mirror several of these by hand —
# Anvil client code cannot import a server module).
#
# NONE OF THESE ARE ROUND NUMBERS FOR THEIR OWN SAKE. Each one is either a
# measured limit of the UI it protects or a bound that stops an input reaching
# arithmetic that would misbehave, and the reason is recorded beside it. Where
# two limits must agree (a parser sentence and the column that stores it), the
# same value is used on purpose and is noted as such.
#
# A caller must never inline one of these numbers. `_validation` takes them as
# arguments so the message the student reads quotes the same limit the check
# used; a hard-coded 200 somewhere would drift the first time this changes.

# Text lengths, in characters, all measured AFTER stripping.
# 200 is roughly two lines of the assessment card's heading at the dashboard's
# width — past that the card grows and the list stops scanning cleanly (NFR01's
# render budget is about the whole list). READ BY: assessments (title),
# notes (title), nlp._extract_title (truncates so a parsed title can never be
# rejected for a length the parser itself produced), and both editor forms.
MAX_TITLE_LENGTH = 200           # assessments.title and notes.title
# 2000 is about one screen of context — a description is meant to be the task
# instructions in brief, not the task itself. READ BY: assessments.
MAX_DESCRIPTION_LENGTH = 2000    # assessments.description — one screen of context
# 20000 is a full page of study notes, generous because this IS the content
# rather than a label on it; the cap exists only to keep one row from bloating
# the table and the export file. READ BY: notes, NoteEditorForm.
MAX_NOTE_CONTENT_LENGTH = 20000  # notes.content — a full page of study notes
# A tag is a filter chip, so 40 chars is already longer than one should be; the
# limit stops a pasted paragraph becoming an unusable chip. READ BY: notes,
# NoteEditorForm.
MAX_TAG_LENGTH = 40              # one notes.tags entry
# 20 tags is past the point where a tag filter helps at all (FR11), and the
# chips would wrap over the note itself. Counted AFTER de-duplication, so
# repeating a tag cannot use up the allowance. READ BY: notes, NoteEditorForm.
MAX_TAGS_PER_NOTE = 20
# Deliberately EQUAL to MAX_PARSER_INPUT_LENGTH below: this column stores the
# sentence that box accepted, so a shorter cap here would silently truncate an
# input the parser had just called valid. Trimmed rather than rejected on write,
# because the audit trail must never block a save. READ BY: assessments.
MAX_SOURCE_TEXT_LENGTH = 500     # the raw sentence the parser was given
# Weight is a percentage of the study score, so 0-100 is the definition rather
# than a chosen limit. Floats, not ints, because half-marks are real ("12.5%").
# 0 is allowed: an ungraded practice task still belongs on the dashboard.
# READ BY: assessments (validating a write), dashboard (cleaning a stored
# value), AssessmentEditorForm.
MIN_WEIGHT = 0.0                 # assessments.weight, a percentage
MAX_WEIGHT = 100.0
# Reminder offsets are "days before due". One day is the shortest useful warning and
# a year is longer than any VCE assessment is set in advance; the upper bound exists
# because an unbounded value (e.g. 999999) made every assessment permanently "due
# soon" and emailed the student about all of them on the first scheduler tick.
# READ BY: assessments, notes (the default list on the settings row),
# _validation.is_valid_reminder_day, AssessmentEditorForm.
MIN_REMINDER_DAY = 1
MAX_REMINDER_DAY = 365
# Six offsets per assessment. Each one is a separate email (FR14), so this caps
# how much mail one assessment can generate — six is well clear of the two the
# app defaults to (assessments._DEFAULT_REMINDER_DAYS is 7 and 2 days before)
# while stopping a student from setting a daily countdown they would learn to
# ignore. READ BY: assessments (per assessment) and notes (the same cap on the
# settings row's default_reminder_days list).
MAX_REMINDER_DAYS_PER_ASSESSMENT = 6
# 100 lines is more than a term's worth of assessments pasted at once, and it
# bounds the work one call can ask for: the check runs before any parsing, so an
# oversized paste costs one message rather than 5,000 regex chains (NFR01).
# READ BY: nlp.parse_bulk (parsing) and assessments.create_bulk_assessments
# (writing) — the same limit on both halves of FR02, so the client cannot parse
# more rows than the server will store.
MAX_BULK_LINES = 100             # one paste of the bulk-add box
# 500 characters is far more than the one sentence the parser is for; it is a
# guard on the regex chain rather than a stylistic limit. See
# MAX_SOURCE_TEXT_LENGTH, which must stay equal to it. READ BY: nlp (both
# callables), and nlp._MAX_BULK_TEXT_LENGTH derives the whole-paste cap from
# this times MAX_BULK_LINES so the two can never contradict each other.
MAX_PARSER_INPUT_LENGTH = 500    # one sentence into the parser box
# A VCE program is 4-6 studies; 12 leaves room for a student repeating a unit or
# carrying an extra, while still refusing an accidental select-all of the 56
# study catalog. READ BY: notes._clean_subjects, SettingsForm.
MAX_SUBJECTS_PER_STUDENT = 12    # a VCE program is 4-6 studies; 12 is generous
# The Victorian school year has exactly four terms, so this pair is a fact about
# the calendar rather than a policy. READ BY: notes._validate_school_terms (on
# the way in, from the Settings page) and nlp._is_well_formed_term (on the way
# back out, guarding the simpleObject column) — the same bound on the write and
# the read, which is what stops the two rules drifting apart.
MIN_TERM_NUMBER = 1              # Victorian school year has four terms
MAX_TERM_NUMBER = 4

# --- Whitelists ------------------------------------------------------------
# The four sets below are all the same idea: a @anvil.server.callable is
# reachable by anything holding a session cookie, so the SERVER decides which
# keys mean anything and every other key is dropped in silence. Dropped rather
# than refused, because an unrecognised key is a bug or a probe, and neither
# deserves an error message that describes the schema back to the sender.
#
# The two key whitelists are sets because every use is a membership test; the
# two EDITABLE_FIELDS tuples are tuples because nothing may append a fifth
# editable column to them. Note the sets are ordinary `set` literals, not
# frozensets — they are not structurally protected the way VALID_TYPES and its
# neighbours are, so treat them as read-only by convention.

# The filter keys list_assessments recognises (FR06 + FR07's sort_by). Anything
# else in the filters dict is discarded before the query is built, so a stale
# client sending a renamed key gets the unfiltered list rather than an error.
# READ BY: dashboard._safe_filters, which is the one consumer — it intersects
# the incoming dict against this set and then validates each surviving VALUE
# separately (a permitted key does not make its value trustworthy).
#
# ONE MEMBER IS INERT: 'sort_by' is whitelisted but read by nothing. Sorting
# travels in list_assessments' separate `sort` argument and is checked against
# ALLOWED_SORT_KEYS below, so a 'sort_by' entry survives _safe_filters and is
# then ignored by _list_assessments_impl. It is left in because dropping it
# would make a client that still sends the key look like it was rejected, and
# because the key is the obvious name for the feature if the two arguments are
# ever merged. Do not read this as evidence that sorting goes through here.
ALLOWED_FILTER_KEYS = {'subjects', 'types', 'statuses', 'show_completed', 'sort_by', 'month'}

# The three columns FR07 permits sorting by. FR07 makes 'due_date' the default
# and names the other two as alternatives; a sort key outside this set falls
# back to that default rather than raising, because a bad sort is not worth
# refusing to show the student their assessments over.
#
# A whitelist and not a passthrough because the value ends up naming a column in
# a Data Tables query: accepting an arbitrary string would let a caller sort by
# — and so learn about — columns the client is never shown.
#
# READ BY: assessments (validating the sort argument to list_assessments) and
# mirrored by hand into DashboardForm._SORTS, which builds the sort dropdown.
ALLOWED_SORT_KEYS = {'due_date', 'weight', 'subject'}

# Fields a client is permitted to edit (FR04 / EC-SEC-03). Read by
# assessments.update_assessment, which filters the incoming patch against this
# tuple before validating anything, and mirrored by AssessmentEditorForm, which
# only ever sends these keys.
#
# FOUR COLUMNS ARE EXCLUDED ON PURPOSE, and the exclusions are the point of the
# whitelist rather than an afterthought:
#   user                    re-assigning it would hand the row to another
#                           account, which is the NFR03 hole the check exists
#                           to close;
#   created_at              the audit stamp of when the record was made;
#   confidence, source_text the PARSER'S audit trail (FR17). Storing "this was
#                           parsed with LOW confidence from 'methods sac
#                           friday'" is pointless if correcting the date can
#                           quietly rewrite it, so the trail is written once at
#                           create time and never again.
# 'term_info' IS editable, unlike the other two parser fields, because it is a
# description of the input rather than the input itself and the student may
# need to correct a term phrase the parser misread.
EDITABLE_FIELDS_ASSESSMENT = (
    'title', 'subject', 'type', 'due_date', 'start_date', 'weight',
    'status', 'description', 'reminder_days', 'linked_note_ids', 'term_info',
)

# The same rule applied to notes (FR10). Short because a note has little else:
# 'user' and 'created_at' are excluded for the reasons above, and 'updated_at'
# is stamped by the server on every write rather than sent by the client.
# READ BY: notes.update_note; mirrored by NoteEditorForm.
EDITABLE_FIELDS_NOTE = (
    'title', 'content', 'tags', 'is_pinned',
)

# --- Misc ------------------------------------------------------------------
# Base URL used in reminder email links (spec section 6): the app's published
# public URL (Anvil Hobby-plan environment). No trailing slash, so a caller
# always joins with an explicit '/'.
#
# It is a constant rather than a computed value because a reminder email is
# built by the SCHEDULED TASK (FR13), which runs with no browser attached —
# there is no request to read a host name off, so the address has to be written
# down somewhere. Here rather than in reminders.py so that the one thing which
# changes when the app is republished under a different Anvil name is findable.
#
# The value is Anvil's auto-generated hobby-plan hostname; it changes if the app
# is ever moved to a custom domain, and every link in every future email changes
# with it. Nothing validates it at runtime — a wrong URL sends mail with dead
# links rather than failing loudly, so it is worth checking after a republish.
#
# READ BY: reminders only.
APP_BASE_URL = 'https://honored-willing-tea.anvil.app'
