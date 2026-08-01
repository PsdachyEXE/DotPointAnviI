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
    make_list_card, make_chip, make_empty_state, toast_error,
)

_MONTHS_ABBR = ('', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')

# A preview any longer than this pushes the actions row off a laptop screen and
# stops the list scanning as a list, so the content is clipped at the card.
_PREVIEW_CHARS = 160


def _fmt_iso(s):
    """'YYYY-MM-DD...'(ISO) -> 'DD Mon YYYY' or '' (manual; Skulpt-safe)."""
    if not s or not isinstance(s, str) or len(s) < 10:
        return ''
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        return '%02d %s %d' % (d, _MONTHS_ABBR[m], y)
    except (ValueError, IndexError):
        return ''


class NotesForm(ColumnPanel):
    def __init__(self, **properties):
        super().__init__(**properties)
        # The top bar must run the full width of the window, so the form itself
        # carries no padding — the centred column below it does.
        self.spacing_above = 'none'
        self.spacing_below = 'none'

        # The live filter state. Held on the form rather than read off the
        # widgets at call time so _refresh() has one source of truth.
        self._search = ''
        self._tag = ''

        self.add_component(make_top_bar(active='notes'))
        body = make_page()
        self.add_component(body)

        body.add_component(make_page_title(
            'Notes', 'Pinned notes stay at the top.'))

        # --- toolbar: search, tag filter, new ---------------------------------
        # Search is the primary control on this screen, so it gets the wide
        # 'bigfield' treatment and sits first; the tag filter narrows whatever
        # search returned. Both re-query the server rather than filtering the
        # list in the browser, so results always match the stored notes.
        self._search_tb = TextBox(placeholder='Search title & content',
                                  role='bigfield')
        self._search_tb.set_event_handler('pressed_enter', self._on_search)
        self._search_tb.set_event_handler('lost_focus', self._on_search)

        self._tag_dd = DropDown(items=[('All tags', '')])
        self._tag_dd.set_event_handler('change', self._on_search)

        new_btn = Button(text='New note', role='primary')
        new_btn.set_event_handler('click', self._on_new_click)

        body.add_component(make_toolbar(self._search_tb, self._tag_dd, new_btn))

        # Filled by _render(); kept as its own panel so a refresh only redraws
        # the list and never rebuilds the toolbar (which would drop focus and
        # wipe what the student had typed).
        self._list_panel = ColumnPanel()
        body.add_component(self._list_panel)

        self._refresh()

    # --- data --------------------------------------------------------------
    def _refresh(self):
        try:
            notes = anvil.server.call('search_notes',
                                      query=self._search or None,
                                      tag=self._tag or None)
        except Exception as e:
            # Show the failure where the list would have been *and* as a toast:
            # the toast catches the eye, the panel message survives after it
            # fades. The tag list is deliberately left as-is, because we have no
            # trustworthy note data to rebuild it from.
            self._list_panel.clear()
            self._list_panel.add_component(
                make_empty_state("Couldn't load notes", str(e)))
            toast_error("Couldn't load notes: %s" % e)
            return
        self._populate_tags(notes)
        self._render(notes)

    def _populate_tags(self, notes):
        """Rebuild the tag filter from the tags actually in use.

        The student's current choice is preserved when it still exists, so
        deleting the last note carrying a tag quietly falls back to 'All tags'
        instead of leaving the list filtered by something that is now gone.
        """
        all_tags = sorted({t for n in notes for t in (n.get('tags') or [])})
        current = self._tag_dd.selected_value
        items = [('All tags', '')] + [(t, t) for t in all_tags]
        self._tag_dd.items = items
        vals = [v for _, v in items]
        self._tag_dd.selected_value = current if current in vals else ''

    def _render(self, notes):
        self._list_panel.clear()
        if not notes:
            self._list_panel.add_component(self._make_empty())
            return
        for n in notes:
            self._list_panel.add_component(self._make_card(n))

    def _make_empty(self):
        """The empty state, worded for the reason the list is empty.

        'No notes yet' next to a search box the student has just typed in would
        be a lie, so a filtered miss says so and points at the fix.
        """
        if self._search or self._tag:
            return make_empty_state(
                'No matching notes',
                'Nothing matches that search or tag. Clear the filters to see '
                'every note.',
                'New note', self._on_new_click)
        return make_empty_state(
            'No notes yet',
            'Notes are for anything that is not an assessment - study plans, '
            'reminders, ideas.',
            'New note', self._on_new_click)

    def _make_card(self, n):
        """One note as a list row: title, tags, preview, date, actions."""
        card = make_list_card()

        # Title first, pinned marker second: the title is what identifies the
        # note, the chip is only a status on it.
        head = make_row(Label(text=n.get('title') or '(untitled)',
                              role='cardtitle'))
        if n.get('is_pinned'):
            head.add_component(make_chip('Pinned', 'accent'))
        card.add_component(head)

        # One chip per tag, not a joined '#a #b' string, so each tag reads as a
        # separate thing and matches the values in the tag filter above.
        tags = n.get('tags') or []
        if tags:
            tag_row = make_row()
            for t in tags:
                tag_row.add_component(make_chip(t))
            card.add_component(tag_row)

        # Newlines are flattened because a list row is a single-line summary;
        # the full formatting is still there when the note is opened.
        preview = (n.get('content') or '').strip().replace('\n', ' ')
        if len(preview) > _PREVIEW_CHARS:
            preview = preview[:_PREVIEW_CHARS] + '…'
        if preview:
            card.add_component(Label(text=preview, role='caption'))
        card.add_component(Label(text='Updated %s' % _fmt_iso(n.get('updated_at')),
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
        self._search = (self._search_tb.text or '').strip()
        self._tag = self._tag_dd.selected_value or ''
        self._refresh()

    def _on_new_click(self, **event_args):
        from ..NoteEditorForm import NoteEditorForm
        if alert(NoteEditorForm(mode='create'), title='', large=True, buttons=[]):
            self._refresh()

    def _on_edit_click(self, note_id):
        from ..NoteEditorForm import NoteEditorForm
        if alert(NoteEditorForm(mode='edit', note_id=note_id), title='', large=True, buttons=[]):
            self._refresh()

    def _on_pin_click(self, note_id):
        try:
            anvil.server.call('toggle_pin', note_id)
        except Exception as e:
            toast_error("Couldn't update: %s" % e)
            return
        self._refresh()

    def _on_delete_click(self, note_id):
        if not confirm('Delete this note?'):
            return
        try:
            anvil.server.call('delete_note', note_id)
        except Exception as e:
            toast_error("Couldn't delete: %s" % e)
            return
        self._refresh()
