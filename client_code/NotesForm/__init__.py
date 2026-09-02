import anvil.tables as tables
import anvil.tables.query as q
from anvil.tables import app_tables
"""NotesForm - the notes index (FR10, FR11).

Composition, top to bottom: the shared top bar, then a centred page holding a
page title, a toolbar (search box, tag filter, New note) and a list of note
rows. One `search_notes()` call fills the whole screen — the server already
returns the notes pinned-first, so this form never sorts, it only draws.

Each note is a `make_list_card()` with no urgency band: notes are not due on a
date, so borrowing the assessment colour language here would be misleading. The
hierarchy inside a row runs title -> tags -> preview -> updated date -> actions,
which is the order a student scans in when hunting for a note.

All styling is expressed as role='...' names that the stylesheet in
anvil.yaml (native_deps.head_html) paints, so the screen follows the light/dark
theme instead of hardcoding greys.

See IMPLEMENTATION_SPEC.md section 3 (NotesForm) and section 14 (Design system).
"""

import anvil
import anvil.server
from anvil import ColumnPanel, Label, TextBox, Button, DropDown, alert, confirm

from ..common import (
    make_top_bar, make_page, make_page_title, make_toolbar, make_row,
    make_section_header, make_list_card, make_chip, make_empty_state,
    toast_error, from_iso, fmt_date,
)

# A preview any longer than this pushes the actions row off a laptop screen and
# stops the list scanning as a list, so the content is clipped at the card.
_PREVIEW_CHARS = 160


class NotesForm(ColumnPanel):
    """The Notes screen: search, tag filter, and the list of notes (FR10, FR11).

    Routed at '#notes' by Main and reachable from the top bar. Every note the
    student owns is created, edited, deleted and pinned from here (FR10),
    though the create/edit half of that happens in NoteEditorForm, which this
    form opens as a dialog.

    FR11's two filters are combined with AND, and the combining is done ONCE,
    server-side: _refresh sends `query` and `tag` together on a single
    search_notes() call, and notes.search_notes applies them in sequence to the
    same row list, so a query of "study" with the tag 'methods' selected means
    the notes matching BOTH, not either. Filtering in the browser instead would
    let the screen disagree with what is stored, and would need every note
    downloaded to filter over.

    The pinned-first order is the server's too (FR10 — "pinned notes always
    sort to the top"): search_notes sorts on (not pinned, -updated_at) before
    filtering, so this form never sorts. It only draws.

    Construction: no arguments of its own — the router builds it with a bare
    NotesForm(). No modes. Two pieces of state are held on the form, _search
    and _tag, which are the filters _refresh() sends.

    Server callables (all server_code/notes.py, all scoped to the signed-in
    user with an ownership check on every row — NFR03):
      * search_notes(query, tag) -> [note dict, ...] — the one call that fills
        the whole screen, re-run after every change
      * toggle_pin(id) / delete_note(id) — the two in-row actions
    A note dict carries id, title, content, tags, is_pinned, created_at and
    updated_at; the two timestamps are ISO strings, or None where the stored
    cell was not a date.

    Hands nothing back to a caller — it is a routed page, not a dialog.
    """

    def __init__(self, **properties):
        """Build the page and load the notes.

        Ends with the screen's single server call, so one _refresh() paints
        everything: the tag filter's options and the list are both derived
        from that one response (NFR01's one round-trip per screen).
        """
        super().__init__(**properties)
        # 1. The top bar must run the full width of the window, so the form
        # itself carries no padding — the centred column below it does.
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        # 2. The live filter state. Held on the form rather than read off the
        # widgets at call time so _refresh() has one source of truth. Both are
        # '' when unset, never None, so `self._search or None` below is the
        # only place the empty-means-no-filter translation happens.
        self._search = ''
        self._tag = ''

        self.add_component(make_top_bar(active='notes'))
        body = make_page()
        self.add_component(body)

        body.add_component(make_page_title(
            'Notes', 'Pinned notes stay at the top.'))

        # --- toolbar: search, tag filter, new ---------------------------------
        # 3. Search is the primary control on this screen, so it gets the wide
        # 'bigfield' treatment and sits first; the tag filter narrows whatever
        # search returned. Both re-query the server rather than filtering the
        # list in the browser, so results always match the stored notes.
        #
        # Two triggers on the search box, not one: pressed_enter is the
        # deliberate search, lost_focus catches the student who types and then
        # reaches for the tag dropdown instead of pressing Enter. There is no
        # per-keystroke handler, which would fire a server call per letter.
        self._search_tb = TextBox(placeholder='Search title & content',
                                  role='bigfield')
        self._search_tb.set_event_handler('pressed_enter', self._on_search)
        self._search_tb.set_event_handler('lost_focus', self._on_search)

        # 4. The dropdown starts with only the 'All tags' option — its real
        #    contents are not known until the notes arrive, so _populate_tags
        #    fills it from the first response. '' is the "no filter" value.
        self._tag_dd = DropDown(items=[('All tags', '')])
        self._tag_dd.set_event_handler('change', self._on_search)

        new_btn = Button(text='New note', role='primary')
        new_btn.set_event_handler('click', self._on_new_click)

        body.add_component(make_toolbar(self._search_tb, self._tag_dd, new_btn))

        # 5. Filled by _render(); kept as its own panel so a refresh only redraws
        # the list and never rebuilds the toolbar (which would drop focus and
        # wipe what the student had typed).
        self._list_panel = ColumnPanel()
        body.add_component(self._list_panel)

        # 6. Load last, once the panel it writes into exists.
        self._refresh()

    # --- data --------------------------------------------------------------
    def _refresh(self):
        """Re-fetch the notes under the current filters and redraw the list.

        The one data path on this screen: __init__, every filter change and
        every successful pin/delete/save all end here, so there is a single
        place where the list can be out of step with the database.

        Sends self._search and self._tag as `query` and `tag`; '' is turned
        into None so an empty box reads as "no filter" rather than as a search
        for the empty string. Returns None — it redraws _list_panel in place.
        Reads the notes table only, via search_notes.
        """
        try:
            notes = anvil.server.call('search_notes',
                                      query=self._search or None,
                                      tag=self._tag or None)
        except Exception as e:
            # Split the two audiences, the same way the other screens do. The
            # toast carries the raw exception (the detail I need when marking or
            # debugging) and fades; the panel keeps a sentence a student can act
            # on plus a Retry button, so a dropped connection is recoverable
            # without navigating away. The tag list is deliberately left as-is,
            # because we have no trustworthy note data to rebuild it from.
            self._list_panel.clear()
            toast_error("Couldn't load notes: %s" % e)
            self._list_panel.add_component(
                make_empty_state("Couldn't load notes",
                                 'Check your connection and try again.',
                                 'Retry', self._refresh))
            return
        # Tags first, then the list: the filter's options are derived from the
        # same response that fills the list, so the two can never describe
        # different sets of notes.
        self._populate_tags(notes)
        self._render(notes)

    def _populate_tags(self, notes):
        """Rebuild the tag filter from the tags actually in use.

        `notes` is the list of note dicts just returned by search_notes. Sets
        self._tag_dd.items to [('All tags', '')] plus one (label, value) pair
        per distinct tag, sorted. Returns None; makes no server call.

        The student's current choice is preserved when it still exists, so
        deleting the last note carrying a tag quietly falls back to 'All tags'
        instead of leaving the list filtered by something that is now gone.
        """
        # A set comprehension over every note's tags, then sorted: the set is
        # what de-duplicates a tag used on twenty notes down to one option, and
        # sorting makes the dropdown's order stable between refreshes rather
        # than following whatever order the notes came back in.
        all_tags = sorted({t for n in notes for t in (n.get('tags') or [])})
        # Read the selection BEFORE replacing items — assigning .items resets
        # the dropdown, so the old choice has to be captured first to be
        # restored afterwards.
        current = self._tag_dd.selected_value
        items = [('All tags', '')] + [(t, t) for t in all_tags]
        self._tag_dd.items = items
        # Restore only if the tag survived; otherwise fall back to 'All tags'.
        # Setting selected_value to something not in items would leave the
        # dropdown showing nothing at all.
        vals = [v for _, v in items]
        self._tag_dd.selected_value = current if current in vals else ''

    def _render(self, notes):
        """Draw `notes` into the list panel: header plus one card each.

        `notes` is the server's list, already in pinned-first order, so this
        walks it as given. An empty list is a state in its own right, not just
        a short list — see _make_empty. Returns None.
        """
        # Clear first: this is a full redraw every time, which is what keeps the
        # screen honest after a delete or a pin without any per-row bookkeeping.
        self._list_panel.clear()
        if not notes:
            self._list_panel.add_component(self._make_empty())
            return
        # Every other list screen labels its list and shows how many rows are
        # under the current filters, so this one does too. It is only drawn when
        # there are notes: the empty state above already names its own situation,
        # and 'Your notes / 0 shown' over it would just repeat that badly.
        self._list_panel.add_component(
            make_section_header('Your notes', '%d shown' % len(notes)))
        for n in notes:
            self._list_panel.add_component(self._make_card(n))

    def _make_empty(self):
        """The empty state, worded for the reason the list is empty.

        Returns a make_empty_state panel. 'No notes yet' next to a search box
        the student has just typed in would be a lie, so a filtered miss says
        so and points at the fix.
        """
        # The filters — not the note count — are what tells the two situations
        # apart: an account with 200 notes and an account with none both arrive
        # here with an empty list, and only self._search / self._tag say which.
        if self._search or self._tag:
            return make_empty_state(
                'No matching notes',
                'Nothing matches that search or tag. Clear the filters to see '
                'every note.',
                'New note', self._on_new_click)
        return make_empty_state(
            'No notes yet',
            'Notes are for anything that is not an assessment — study plans, '
            'reminders, ideas.',
            'New note', self._on_new_click)

    def _make_card(self, n):
        """One note as a list row: title, tags, preview, date, actions.

        `n` is one note dict from search_notes: id (str), title (str),
        content (str), tags (list of str), is_pinned (bool), updated_at (an
        ISO timestamp string or None). Returns the finished ColumnPanel;
        nothing is added to the page here, _render does that.

        make_list_card() is called with no band, so the row has no urgency
        colour: FR21's red/orange/blue language belongs to things with a due
        date, and borrowing it for a note would say something untrue.
        """
        card = make_list_card()

        # Title first, pinned marker second: the title is what identifies the
        # note, the chip is only a status on it.
        head = make_row(Label(text=n.get('title') or '(untitled)',
                              role='cardtitle'))
        if n.get('is_pinned'):
            head.add_component(make_chip('Pinned', 'accent'))
        card.add_component(head)

        # One chip per tag, not a joined '#a #b' string, so each tag reads as a
        # separate thing and matches the values in the tag filter above. The
        # '#' is display only — the stored tag, and the value the filter sends,
        # carry no hash.
        tags = n.get('tags') or []
        if tags:
            tag_row = make_row()
            for t in tags:
                tag_row.add_component(make_chip('#%s' % t))
            card.add_component(tag_row)

        # Newlines are flattened because a list row is a single-line summary;
        # the full formatting is still there when the note is opened.
        preview = (n.get('content') or '').strip().replace('\n', ' ')
        if len(preview) > _PREVIEW_CHARS:
            preview = preview[:_PREVIEW_CHARS] + '…'
        if preview:
            card.add_component(Label(text=preview, role='caption'))
        # updated_at is a server-produced ISO timestamp string, so the shared
        # from_iso + fmt_date pair gives exactly the 'DD Mon YYYY' the old local
        # helper produced — which is the format NFR08 fixes for every date in
        # the app, whatever the browser's locale. (NFR08's note says the format
        # is applied server-side; DotPoint instead sends the raw ISO string and
        # formats it in common.fmt_date, so one helper decides how a date looks
        # on every screen. The rendered result is the one NFR08 mandates.)
        # The one difference is the missing case: fmt_date(None)
        # says 'no date', which reads as a fact about the note rather than a gap
        # in the data, so an unparseable timestamp still renders as nothing here.
        updated = from_iso(n.get('updated_at'))
        card.add_component(
            Label(text='Updated %s' % (fmt_date(updated) if updated else ''),
                  role='micro'))

        # Quiet 'ghost' actions so the row stays readable; Delete is the only
        # destructive one, so it is the only one that reddens on hover.
        # nid=n['id'] is a default argument on purpose: it captures this note's
        # id now, instead of every button closing over the last loop value.
        actions = make_row()
        pin_btn = Button(text='Unpin' if n.get('is_pinned') else 'Pin',
                         role='ghost')
        pin_btn.set_event_handler('click', lambda nid=n['id'], **e: self._on_pin_click(nid))
        actions.add_component(pin_btn)
        edit_btn = Button(text='Edit', role='ghost')
        edit_btn.set_event_handler('click', lambda nid=n['id'], **e: self._on_edit_click(nid))
        actions.add_component(edit_btn)
        del_btn = Button(text='Delete', role='danger')
        del_btn.set_event_handler('click', lambda nid=n['id'], **e: self._on_delete_click(nid))
        actions.add_component(del_btn)
        card.add_component(actions)
        return card

    # --- handlers ----------------------------------------------------------
    def _on_search(self, **event_args):
        """Either filter changed: re-read BOTH controls, then re-query (FR11).

        Wired to three events — Enter and lost_focus on the search box, change
        on the tag dropdown — so `event_args` differs each time and is ignored.
        Both filters are read on every one of them, not just the one that
        fired, which is what keeps _search and _tag describing the same moment
        and is why the two filters can be ANDed on the server in one call.
        """
        self._search = (self._search_tb.text or '').strip()
        self._tag = self._tag_dd.selected_value or ''
        self._refresh()

    def _on_new_click(self, **event_args):
        """'New note' pressed: open the editor as a modal, refresh if it saved.

        The import is inside the function on purpose — NoteEditorForm imports
        nothing from here, but keeping the two forms' imports lazy is the
        pattern the router uses and it keeps a form from being loaded until a
        screen actually needs it.

        buttons=[] because the dialog draws its own Cancel/Save row, and
        title='' because its own make_page_title is the heading. alert()
        returns whatever the dialog raised with 'x-close-alert': the new note's
        row id on save (truthy) or None on cancel, so the truth test IS the
        "did anything change?" test and a cancel costs no server call.
        """
        from ..NoteEditorForm import NoteEditorForm
        if alert(NoteEditorForm(mode='create'), title='', large=True, buttons=[]):
            self._refresh()

    def _on_edit_click(self, note_id):
        """'Edit' pressed on one row: open that note in the editor.

        `note_id` is the row id captured as a default argument when the button
        was built, NOT taken from any current selection — the list has no
        selection. Same dialog and same truth test as _on_new_click; only the
        mode and the id differ.
        """
        from ..NoteEditorForm import NoteEditorForm
        if alert(NoteEditorForm(mode='edit', note_id=note_id), title='', large=True, buttons=[]):
            self._refresh()

    def _on_pin_click(self, note_id):
        """'Pin'/'Unpin' pressed: flip the note's pinned state (FR10).

        `note_id` is the row id captured when the button was built. The server
        decides the new value (toggle_pin reads the stored one and returns the
        flip), so this does not track state locally — a full _refresh is what
        redraws the row's label and moves it to or from the top of the list.

        No confirm: pinning is reversible by pressing the same button again.
        """
        try:
            anvil.server.call('toggle_pin', note_id)
        except Exception as e:
            # str(e), not friendly_error: toggle_pin's own refusals are already
            # written for the student ("That note no longer exists — it may
            # have already been deleted."), and the list is still on screen
            # behind the toast, so a raw message here is recoverable rather
            # than a dead end.
            toast_error("Couldn't update: %s" % e)
            return
        self._refresh()

    def _on_delete_click(self, note_id):
        """'Delete' pressed: confirm, then remove the note (FR10).

        `note_id` is the row id captured when the button was built. The confirm
        comes FIRST, before any server call, because deletion is the only
        irreversible action on this screen — there is no undo and no trash.
        delete_note also unlinks the note from any assessment that referenced
        it (FR12's linked_note_ids), so no dangling id is left behind.
        """
        if not confirm('Delete this note?'):
            return
        try:
            anvil.server.call('delete_note', note_id)
        except Exception as e:
            toast_error("Couldn't delete: %s" % e)
            return
        # Refresh rather than removing the row by hand: the deleted note may
        # have been the last one carrying a tag, and only a re-query rebuilds
        # the tag filter without it.
        self._refresh()
