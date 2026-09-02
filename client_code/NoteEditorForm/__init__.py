import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""NoteEditorForm - create / edit a single note (FR10).

Opened as an alert(..., large=True) from NotesForm, so this form is dialog
content only: no top bar and no make_page() wrapper, because the alert already
supplies the surface and the centring. Layout is one vertical stack of
make_field() groups (Title, Content, Tags) so every label/control pair lines up
on the same left edge, then the pin checkbox, then a Cancel/Save row.

Tags are edited in place rather than in a sub-dialog: a text box + Add button,
with the current tags rendered underneath as chips that each carry their own
remove button.

Content stays a plain TextArea with no markdown rendering. This header used to
call that an "SRS plain-text constraint", which is the wrong way round: FR10 in
SAT 3_SRS2026 asks for MARKDOWN notes, and it is the design document (§4.2.2)
that specifies plain text only. Plain text is what this build implements - the
decision is recorded as discrepancy 6 in docs/DISCREPANCIES.md, where the doc
governs because it also simplifies substring search and the export round trip
(FR18/FR19). Existing markdown survives as raw text either way. The hint under
the box says so plainly, so a student is never left typing **bold** and
wondering why it stays literal.

Save raises 'x-close-alert' with the note id; Cancel returns None. (The 300ms
edit-mode autosave in the spec is deferred; Save is explicit for now.)

See IMPLEMENTATION_SPEC.md section 3 (NoteEditorForm) and section 14 (Design
system) - all colour and type here comes from roles, never from Python.
"""

import anvil
import anvil.server
from anvil import ColumnPanel, TextBox, TextArea, CheckBox, Button

from ..common import (
    make_chip, make_divider, make_field, make_page_title, make_row,
    toast, toast_error, set_field_error, clear_field_errors, friendly_error,
)

# --- field bounds (mirrors of server_code/_constants.py) ---------------------
# The client cannot import server_code, so the four bounds this form can check
# before uploading are restated here. The server re-checks all of them and
# remains the authority; these exist so a 30,000-character paste is refused in
# the browser instead of after the round trip, and so the student is told the
# same limit either way. Keep in step with _constants.MAX_TITLE_LENGTH,
# MAX_NOTE_CONTENT_LENGTH, MAX_TAG_LENGTH and MAX_TAGS_PER_NOTE.
MAX_TITLE_LENGTH = 200
MAX_NOTE_CONTENT_LENGTH = 20000
MAX_TAG_LENGTH = 40
MAX_TAGS_PER_NOTE = 20


class NoteEditorForm(ColumnPanel):
    """The create/edit note dialog: title, content, tags, pin (FR10).

    Not a routed screen. NotesForm opens it with
    alert(NoteEditorForm(...), title='', large=True, buttons=[]) — buttons=[]
    because this form draws its own Cancel/Save row, and title='' because the
    make_page_title inside is the heading. That is also why there is no top
    bar and no make_page() wrapper: the alert supplies the surface.

    Construction — NoteEditorForm(mode='create', note_id=None):
      * mode='create' — every field starts empty; Save calls create_note() and
        hands back the id of the new row.
      * mode='edit'   — note_id must be the row id of one of this student's own
        notes. __init__ loads it before the first paint, and Save calls
        update_note(note_id, ...) and hands back that same id.
      note_id is ignored in create mode. An 'edit' with no note_id is not an
      error: the load is skipped and the dialog behaves as a create under an
      'Edit note' heading.

    Server callables, all in server_code/notes.py, all scoped to the signed-in
    user and ownership-checked there (NFR03):
      * search_notes()          — used by _load(); there is no single-note getter
      * create_note(record)     -> new row id (str)
      * update_note(id, fields) -> the saved note as a dict
    `record` is the four-key dict built in _on_save_click: title, content,
    tags, is_pinned. Those four are exactly notes.EDITABLE_FIELDS_NOTE.

    Hands its result back by raising 'x-close-alert', which is what closes the
    alert and becomes alert()'s return value: the note's row id after a
    successful Save, None on Cancel. NotesForm treats any truthy value as
    "something changed, refresh the list".
    """

    def __init__(self, mode='create', note_id=None, **properties):
        """Build the dialog, and in edit mode load the note before first paint.

        Parameters:
          mode      str — 'create' or 'edit'. Anything else falls through to
                    the create behaviour: every test below compares against
                    those two literals rather than validating the argument.
          note_id   str or None — an Anvil row id, the 'id' of one of the
                    dicts search_notes() returns. Read only when mode=='edit'.
          properties — Anvil's component keywords, passed through untouched.
        """
        super().__init__(**properties)
        # 1. No padding of its own: the alert's own surface supplies the inset,
        #    so a second one here would double it.
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        # 2. The three pieces of form state. _mode and _note_id decide which
        #    server call Save makes; _tags is the LIVE tag list and the single
        #    source of truth for the chips — the chip row is only ever redrawn
        #    from it, never read back, so removing a chip is a list operation
        #    rather than a component hunt.
        self._mode = mode
        self._note_id = note_id
        self._tags = []

        # 3. One heading for the dialog; 'create' and 'edit' share this whole
        # form and differ only in this text and in which server call Save
        # makes. Built by the kit's make_page_title so this dialog's heading is
        # the same component every other screen uses, not a hand-rolled Label.
        self.add_component(make_page_title('New note' if mode == 'create'
                                           else 'Edit note'))

        # 4. The three field wrappers are kept on self, not just added and
        # forgotten, because _validate() writes its messages into them
        # (make_field gives each one a hidden error_label). Title is the only
        # one marked required — it is the only field the server refuses to
        # accept blank.
        self._title_tb = TextBox()
        self._title_field = make_field('Title', self._title_tb, required=True)
        self.add_component(self._title_field)

        # 5. 260px, not a role: a note body needs room to write a paragraph in (sizing, not styling).
        self._content_ta = TextArea(height='260px')
        self._content_field = make_field(
            'Content', self._content_ta,
            hint='Plain text — markdown is not rendered.')
        self.add_component(self._content_field)

        # 6. The tag editor is one field group. make_field lays its parts out as
        # caption -> component -> hint, so the add-row AND the chips have to go
        # in together as the component; appending the chips to the group
        # afterwards would drop them below the hint, away from the box that
        # creates them.
        #
        # Enter and the Add button are wired to the SAME handler, because after
        # typing a word the hand is already on the keyboard and reaching for the
        # mouse to commit a three-letter tag is the slow way round. pressed_enter
        # belongs to this TextBox alone, so it adds a tag and nothing else — it
        # is not a submit for the dialog.
        self._tag_tb = TextBox(placeholder='add a tag')
        self._tag_tb.set_event_handler('pressed_enter', self._on_add_tag)
        add_tag_btn = Button(text='Add', role='secondary')
        add_tag_btn.set_event_handler('click', self._on_add_tag)
        # _tag_pills is the row _render_tags() clears and refills; it is kept on
        # self because it is redrawn on every add and every remove.
        self._tag_pills = make_row()
        tag_editor = ColumnPanel()
        tag_editor.add_component(make_row(self._tag_tb, add_tag_btn))
        tag_editor.add_component(self._tag_pills)
        self._tags_field = make_field(
            'Tags', tag_editor,
            hint='Tags are how notes are filtered on the Notes page. '
                 'Up to %d, each %d characters or fewer.'
                 % (MAX_TAGS_PER_NOTE, MAX_TAG_LENGTH))
        self.add_component(self._tags_field)

        # 7. Left as a plain labelled checkbox: the label *is* the question, so
        # wrapping it in a make_field caption would just say it twice. Pinning
        # is what FR10 sorts to the top of the Notes list.
        self._pin_cb = CheckBox(text='Pin this note')
        self.add_component(self._pin_cb)

        # --- footer ---
        # 8. The divider closes off the field stack above, so the two
        # dialog-level buttons read as the footer and not as one more field.
        # Same pattern as AssessmentEditorForm.
        self.add_component(make_divider())
        # Cancel first, Save last, so the confirming action sits on the right
        # where the eye finishes - and Save carries the only primary role.
        cancel_btn = Button(text='Cancel', role='secondary')
        cancel_btn.set_event_handler('click', self._on_cancel_click)
        save_btn = Button(text='Save', role='primary')
        save_btn.set_event_handler('click', self._on_save_click)
        self.add_component(make_row(cancel_btn, save_btn))

        # 9. Load before the first render so an edited note's tags are drawn
        # once, with data, instead of drawn empty and then redrawn.
        # _render_tags runs on BOTH paths — on create it draws an empty row,
        # which makes every later redraw a plain refill, not a first build.
        if mode == 'edit' and note_id:
            self._load()
        self._render_tags()

    # --- data --------------------------------------------------------------
    def _load(self):
        """Fill the fields from the note being edited. Returns None.

        Reads the notes table through search_notes(), which returns every note
        the signed-in user owns as a list of dicts (NFR03 scopes it to them
        server-side). There is no get_note(id) callable, so the row is picked
        out of that list here. It is a whole-table fetch to populate one dialog,
        which is only acceptable because a student's note count is small — the
        SRS budgets for 50 (NFR01).

        Failures are reported and swallowed: the dialog stays open with whatever
        it managed to fill in.
        """
        try:
            notes = anvil.server.call('search_notes')
        except Exception as e:
            # friendly_error keeps the server's own sentences and replaces a
            # transport string; "Close this and try again" is the fallback
            # because there is no retry control inside the dialog.
            toast_error(friendly_error(
                e, "Couldn't load that note. Close this and try again."))
            return
        # 1. Linear scan with a None default rather than an index lookup: the
        #    note may genuinely be gone (deleted in another tab since the list
        #    was drawn), and that is a message, not a crash.
        note = next((n for n in notes if n['id'] == self._note_id), None)
        if note is None:
            toast_error("Note not found.")
            return
        # 2. The `or ''` guards are belt-and-braces: notes._note_row_to_dict
        #    already routes both fields through safe_text, which substitutes ''
        #    for a cell holding anything but a string. They are kept because
        #    this side cannot see that helper, and assigning None to .text
        #    would put the word "None" in the box in front of the student.
        self._title_tb.text = note.get('title') or ''
        self._content_ta.text = note.get('content') or ''
        # 3. list(), not the list itself — self._tags is mutated by add and
        #    remove, and copying keeps that off the dict the server sent.
        self._tags = list(note.get('tags') or [])
        self._pin_cb.checked = bool(note.get('is_pinned'))

    def _render_tags(self):
        """Redraw the chip list from self._tags (the single source of truth).

        Full clear-and-rebuild rather than adding or deleting one chip: the tag
        list is at most MAX_TAGS_PER_NOTE long, so a rebuild is cheap, and it
        removes any chance of the chips drifting out of step with the list that
        actually gets saved.
        """
        self._tag_pills.clear()
        for t in self._tags:
            # Each chip carries its own remove button, so a tag is deleted where
            # it is shown rather than by retyping it into the box.
            remove_tag_btn = Button(text='x', role='iconbtn')
            # tag=t is a default argument, not a closure over t: without it every
            # handler would capture the same final loop value and remove the
            # wrong tag.
            remove_tag_btn.set_event_handler(
                'click', lambda tag=t, **e: self._remove_tag(tag))
            self._tag_pills.add_component(
                make_row(make_chip('#%s' % t), remove_tag_btn))

    def _remove_tag(self, tag):
        """Drop `tag` from the note and redraw the chips.

        `tag` is the exact string the chip was built from, captured as a default
        argument in _render_tags. Comparison is exact (case-sensitive) and
        removes EVERY match, which is safe because _on_add_tag has already
        refused case-insensitive duplicates, so at most one can be there.
        """
        # Rebuild the list rather than .remove(): a comprehension cannot raise
        # ValueError on a tag that has somehow already gone, and this is called
        # from a click handler where an exception would be invisible.
        self._tags = [t for t in self._tags if t != tag]
        # Removing a tag can be the fix for "too many tags", so clear that
        # message rather than leave it contradicting what is now on screen.
        set_field_error(self._tags_field, None)
        self._render_tags()

    # --- validation --------------------------------------------------------
    # Client-side checks are a fast, friendly FIRST pass; notes._validate_note_fields
    # runs the same rules server-side and stays the authority. The wording is copied
    # from there deliberately, so a student who trips a rule in the browser and the
    # same rule on the server is told the same thing twice, not two different things.

    def _validate(self):
        """Check the fields on SUBMIT; show any message beside its own box.

        Returns True when the note is safe to send. Deliberately not run on every
        keystroke — "Title is required." appearing while the student is still
        typing the first letter is worse than nothing.

        Checks title (required, <= MAX_TITLE_LENGTH), content (optional,
        <= MAX_NOTE_CONTENT_LENGTH) and the tag list (via _tag_list_error).
        Writes its messages into the three make_field wrappers; makes no server
        call and touches no table.
        """
        # 1. Wipe last attempt's messages FIRST. Without this, a message the
        #    student has already fixed would sit there beside a now-valid box.
        clear_field_errors(self._title_field, self._content_field,
                           self._tags_field)

        title = (self._title_tb.text or '').strip()
        # Measured on the stripped text because that is what the server stores
        # and therefore what it measures.
        content = (self._content_ta.text or '').strip()

        # 2. first_bad_field remembers the TOP-most offending field so the
        #    cursor can be put there at the end. It is never overwritten once
        #    set (see the `or` below), because the first problem down the page
        #    is the one the student should be looking at.
        first_bad_field = None

        # 3. Every field is checked, not just up to the first failure: all the
        #    messages appear at once, so fixing them is one pass rather than a
        #    round trip through Save per problem.
        if not title:
            set_field_error(self._title_field, 'Title is required.')
            first_bad_field = self._title_field
        elif len(title) > MAX_TITLE_LENGTH:
            set_field_error(self._title_field,
                            'Title is too long — keep it to %d characters or '
                            'fewer (currently %d).'
                            % (MAX_TITLE_LENGTH, len(title)))
            first_bad_field = self._title_field

        if len(content) > MAX_NOTE_CONTENT_LENGTH:
            set_field_error(self._content_field,
                            'Content is too long — keep it to %d characters or '
                            'fewer (currently %d).'
                            % (MAX_NOTE_CONTENT_LENGTH, len(content)))
            first_bad_field = first_bad_field or self._content_field

        # 4. Tags are guarded as they are added, so this is the backstop for a
        # note loaded from a row that already breaks the rule — an import, or a
        # note written before a limit was tightened. Without it the student
        # could only find out from the server's refusal after pressing Save.
        tag_message = self._tag_list_error(self._tags)
        if tag_message:
            set_field_error(self._tags_field, tag_message)
            first_bad_field = first_bad_field or self._tags_field

        # 5. Nothing was set, so every message written at step 1 is still
        #    cleared and the record is safe to send.
        if first_bad_field is None:
            return True
        # 6. The dialog is tall enough to scroll, so put the cursor in the first
        # offending box: the message is no use if it is below the fold.
        # make_field hangs `input_component` on the wrapper for exactly this.
        try:
            first_bad_field.input_component.focus()
        except Exception:
            pass  # focus is a courtesy, never a reason to block the save
        return False

    def _tag_list_error(self, tags):
        """Return the message for an unusable set of tags, or None.

        `tags` is a list of tag strings — normally self._tags. Checks the two
        rules the server's _validate_note_fields also applies: each tag at most
        MAX_TAG_LENGTH characters, and at most MAX_TAGS_PER_NOTE of them.
        Split out of _validate() because _on_add_tag needs the same length rule
        one tag at a time, and the wording has to be identical in both places.
        """
        # Per-tag length is tested BEFORE the count, because a too-long tag is
        # a fault in one specific tag the student can see and fix, whereas
        # "too many tags" asks them to choose which one to lose.
        for tag in tags:
            if len(tag) > MAX_TAG_LENGTH:
                return ('Tag is too long — keep it to %d characters or fewer '
                        '(currently %d).' % (MAX_TAG_LENGTH, len(tag)))
        if len(tags) > MAX_TAGS_PER_NOTE:
            return ('A note can have at most %d tags (this one has %d).'
                    % (MAX_TAGS_PER_NOTE, len(tags)))
        return None

    # --- handlers ----------------------------------------------------------
    def _on_add_tag(self, **event_args):
        """Add the typed tag, or say why it was not added.

        Adding a tag IS a submit for this sub-field, so it is the right moment to
        check one. Silently dropping a too-long or duplicate tag (which is what
        this did before) looks exactly like a broken Add button.

        Fired by the Add button's click and by Enter in the tag box, so
        `event_args` differs between the two and is ignored in both. Appends to
        self._tags and clears the box on success; otherwise writes a message
        into the Tags field and leaves the text where it is, so the student can
        edit it rather than retype it. No server call, no return value.
        """
        # 1. Clear the previous attempt's message before doing anything, so a
        #    successful add visibly removes the complaint about the last one.
        set_field_error(self._tags_field, None)
        tag = (self._tag_tb.text or '').strip()
        if not tag:
            return  # an empty box is a mis-click, not an error worth a message

        # 2. Three refusals in order of how specific they are: this tag is too
        #    long, then this tag is already here, then the note is full. Each
        #    returns rather than falling through, so only one message ever
        #    shows and it is always about the tag just typed.
        if len(tag) > MAX_TAG_LENGTH:
            set_field_error(self._tags_field,
                            'Tag is too long — keep it to %d characters or '
                            'fewer (currently %d).'
                            % (MAX_TAG_LENGTH, len(tag)))
            return
        # Case-insensitive, matching the server's de-duplication rule. Refusing
        # here rather than letting the server quietly drop it means "study" and
        # "Study" are not both shown as chips and then saved as one.
        if tag.lower() in [t.lower() for t in self._tags]:
            set_field_error(self._tags_field,
                            'This note already has the tag "%s".' % tag)
            return
        if len(self._tags) >= MAX_TAGS_PER_NOTE:
            set_field_error(self._tags_field,
                            'A note can have at most %d tags. Remove one before '
                            'adding another.' % MAX_TAGS_PER_NOTE)
            return

        # 3. Accepted: append, empty the box so the next tag can be typed
        #    straight away, and redraw the chips from the list.
        self._tags.append(tag)
        self._tag_tb.text = ''
        self._render_tags()

    def _on_save_click(self, **event_args):
        """'Save' pressed: validate, send, then hand the note id back.

        Builds the four-field record the server accepts, calls create_note or
        update_note depending on the mode, and closes the dialog by raising
        'x-close-alert' with the note's row id. Returns None; a validation
        failure or a server error leaves the dialog open with a message.
        """
        # 1. The guard clause is the whole of criterion 7.3's first pass. A
        #    failure returns without sending anything, so an invalid note never
        #    costs a round trip.
        if not self._validate():
            return
        # 2. The record is assembled fresh from the widgets rather than kept in
        #    step as the student types, so what is sent is exactly what is on
        #    screen at the moment Save was pressed. Only these four keys are
        #    sent; they are exactly notes.EDITABLE_FIELDS_NOTE, so nothing here
        #    can be silently dropped by the server's whitelist.
        record = {
            # Title is stripped to match what _validate measured and what the
            # server stores.
            'title': (self._title_tb.text or '').strip(),
            # Content is NOT stripped: leading indentation and trailing blank
            # lines are part of how a student lays a note out, and _validate
            # only strips its own copy to measure the length.
            'content': self._content_ta.text or '',
            # A copy, so the dialog's live list cannot be handed to the server
            # call and then mutated underneath it by a late chip click.
            'tags': list(self._tags),
            'is_pinned': bool(self._pin_cb.checked),
        }
        # 3. The mode decides the callable. Edit has no return value worth
        #    keeping (update_note returns the saved note), so result_id is the
        #    id we already had; create_note returns the brand new row id. Either
        #    way the caller receives the same kind of value.
        try:
            if self._mode == 'edit':
                anvil.server.call('update_note', self._note_id, record)
                result_id = self._note_id
            else:
                result_id = anvil.server.call('create_note', record)
        except Exception as e:
            # 4. The server's validators speak to the student; anything else (a
            # dropped connection, a platform error) is replaced by a sentence
            # that can actually be acted on. No field is named, because a failure
            # that gets this far is not about one box. Returning without raising
            # 'x-close-alert' is what keeps the dialog open, so the typed note
            # is not lost to a dropped connection.
            toast_error(friendly_error(
                e, "Couldn't save that note. Please try again."))
            return
        toast("Note saved.")
        # 5. The caller (NotesForm) decides what to do with the id; the dialog's
        # only job is to hand it back. Raising 'x-close-alert' both closes the
        # alert and makes `value` the alert() call's return value — a non-empty
        # row id, which NotesForm reads as truthy and refreshes on.
        self.raise_event('x-close-alert', value=result_id)

    def _on_cancel_click(self, **event_args):
        """'Cancel' pressed: close with None, so the caller does not refresh.

        Nothing is written, so the stored note is untouched — but anything
        typed since the dialog opened is discarded, and there is deliberately
        no "discard your changes?" prompt. Save is the only commit in this
        dialog, which is the same bargain every other editor here makes.
        """
        self.raise_event('x-close-alert', value=None)
