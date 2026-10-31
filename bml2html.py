import re
import bml
import xml.etree.ElementTree as ET


# --- inline markup -----------------------------------------------------------
#
# Inline bml markup (suit symbols, *strong*, /em/, =code=, dashes, links) is
# parsed into ElementTree nodes *as text enters the tree*, rather than by regex
# passes over the serialized HTML. That means the regexes never have to dodge
# tags, ordering between passes stops mattering, and link targets (normalised
# ids) are never mistaken for suit symbols -- so opponent bids in parens like
# (2D) render their suit glyph instead of being suppressed by a lookahead.

# suit letter -> (css class, unicode glyph). Matches the old &clubs;/&diams;/…
# entities: ♣ U+2663, ♦ U+2666, ♥ U+2665, ♠ U+2660.
_SUIT = {
    "C": ("ccolor", "♣"),
    "D": ("dcolor", "♦"),
    "H": ("hcolor", "♥"),
    "S": ("scolor", "♠"),
}

# Character-level replacements that do not nest: a digit-led suit run (2C, 2N,
# (2D)'s "2D"), the !x suit shorthand, and long dashes.
_PLAIN_RE = re.compile(
    r"(?P<suit>\d(?:[CDHS]|N(?!T))+)"  # 2C, 2H, 2N -> NT, 2CH ...
    r"|!(?P<bang>[cdhs])"  # !c !d !h !s shorthand
    r"|(?P<dash>---|--)"  # em / en dash
)

# Constructs that can contain further inline markup. Boundary lookbehinds mirror
# the old passes: *strong* may start a text node or follow whitespace/">"; /em/
# and =code= must follow whitespace (or start a node) so mid-word "/" and "="
# (e.g. "GF/FG", "count = 15") are left alone.
#
# _underline_ (org-mode's) has NO boundary: articles mark one card inside a holding,
# "A_*K*_JTx", which is mid-word by nature. "_" is rare enough in prose for that to
# be safe - the corpus has it only inside link targets, which match first.
_INLINE_RE = re.compile(
    r"(?P<link>\[(?P<ltext>[^\[\]]*?)\]\((?P<ltarget>[^()]+?)\))"
    r"|(?<![^\s>])\*(?P<strong>\S(?:[^*]*\S)?)\*"
    r"|(?<!\S)/(?P<em>\S(?:[^/]*\S)?)/"
    r"|(?<!\S)=(?P<code>\S(?:[^=]*\S)?)="
    r"|_(?P<u>\S(?:[^_]*\S)?)_"
)


def _suit_span(letter):
    cls, glyph = _SUIT[letter]
    span = ET.Element("span")
    span.set("class", cls)
    span.text = glyph
    return span


# A seat or vulnerability CONDITION in a bid table's description, drawn as a small tag so the reader sees where a
# meaning changes: `(NV)` / `(VUL)`, a bare `NV` / `VUL`, a list of seats `1st/2nd` / `3rd/4th`, `3rd seat`,
# `4th position`, and `passed hand(s)`. A lone `4th` is left alone - `4th suit`, `2nd round` are not seats. ASCII
# word boundaries, as bridge-markup's matcher has.
_COND_RE = re.compile(
    r"\((?:NV|VUL)\)"
    r"|\b(?:NV|VUL)\b"
    r"|\b(?:1st|2nd|3rd|4th)(?:/(?:1st|2nd|3rd|4th))+\b"
    r"|\b(?:1st|2nd|3rd|4th) (?:seat|position)\b"
    r"|\b(?:un)?[Pp]assed hands?\b",
    re.ASCII,
)
# A LONE seat, `3rd`: where it ends the text (`... suit in 3rd.`, `1st NV or 3rd`) - not before a comma or `or`,
# which list other things too (`1st or 2nd round control`, `(2nd, 3rd & 4th honour)`) - or, in a row that
# already names seats (`1st/2nd ..., 3rd both minors`), anywhere but before a word that makes it something else
# (`4th suit`).
_LONE_SEAT_END_RE = re.compile(r"\b(?:1st|2nd|3rd|4th)\b(?!/)(?=$|[.;:)\]]| NV\b| VUL\b)", re.ASCII)
_LONE_SEAT_RE = re.compile(
    r"\b(?:1st|2nd|3rd|4th)\b(?!/)(?! (?:suits?|rounds?|ask|honours?|highest|step|bids?|break|from|shortage|double|cards?|hands?)\b)",
    re.ASCII,
)
_SEAT_CONTEXT_RE = re.compile(r"\b(?:1st|2nd|3rd|4th)(?:/(?:1st|2nd|3rd|4th))+\b|\b(?:1st|2nd|3rd|4th) (?:seat|position)\b", re.ASCII)
# True while a bid table's description is drawn (`_description`): only there are conditions tagged.
_conditions = False
# How a lone seat is found in the row being drawn: `_LONE_SEAT_RE` when the row names seats, else the stricter
# `_LONE_SEAT_END_RE`.
_lone_seat = _LONE_SEAT_END_RE


def _description(text):
    """parse_inline for a bid table's description: the same, with its conditions tagged."""
    global _conditions, _lone_seat
    _conditions = True
    _lone_seat = _LONE_SEAT_RE if _SEAT_CONTEXT_RE.search(text) else _LONE_SEAT_END_RE
    try:
        return parse_inline(text)
    finally:
        _conditions = False


def _plain(text):
    """List of str | span Element for the non-nesting inline bits, with conditions tagged in a description."""
    if not _conditions:
        return _plain_core(text)
    nodes = []
    pos = 0
    while True:
        # The first condition from the left; at the same place a `_COND_RE` one, which is the longer.
        m = _COND_RE.search(text, pos)
        lone = _lone_seat.search(text, pos)
        if lone and (not m or lone.start() < m.start()):
            m = lone
        if not m:
            break
        nodes.extend(_plain_core(text[pos : m.start()]))
        span = ET.Element("span")
        span.set("class", "bml-cond")
        span.text = m.group(0)
        nodes.append(span)
        pos = m.end()
    nodes.extend(_plain_core(text[pos:]))
    return nodes


def _plain_core(text):
    nodes = []
    pos = 0
    for m in _PLAIN_RE.finditer(text):
        if m.start() > pos:
            nodes.append(text[pos : m.start()])
        if m.group("suit") is not None:
            run = m.group("suit")
            nodes.append(run[0])  # the digit
            for ch in run[1:]:
                if ch == "N":
                    nodes.append("NT")
                else:
                    nodes.append(_suit_span(ch))
        elif m.group("bang") is not None:
            nodes.append(_suit_span(m.group("bang").upper()))
        else:  # dash
            nodes.append("—" if m.group("dash") == "---" else "–")
        pos = m.end()
    if pos < len(text):
        nodes.append(text[pos:])
    return nodes


def parse_inline(text):
    """Parse a run of human text into a list of str | ET.Element."""
    nodes = []
    pos = 0
    while True:
        m = _INLINE_RE.search(text, pos)
        if not m:
            nodes.extend(_plain(text[pos:]))
            return nodes
        if m.start() > pos:
            nodes.extend(_plain(text[pos : m.start()]))
        if m.group("link") is not None:
            a = ET.Element("a")
            a.set("href", normalise_header_id(m.group("ltarget").strip()))
            _fill(a, m.group("ltext").strip())
            nodes.append(a)
        elif m.group("strong") is not None:
            el = ET.Element("strong")
            _fill(el, m.group("strong"))
            nodes.append(el)
        elif m.group("em") is not None:
            el = ET.Element("em")
            _fill(el, m.group("em"))
            nodes.append(el)
        elif m.group("u") is not None:
            el = ET.Element("u")
            _fill(el, m.group("u"))
            nodes.append(el)
        else:  # code
            el = ET.Element("code")
            _fill(el, m.group("code"))
            nodes.append(el)
        pos = m.end()


def _attach(parent, node):
    """Append a str | Element to parent following ElementTree's text/tail model:
    leading text goes on parent.text, text after a child goes on that child's
    tail."""
    if isinstance(node, str):
        if len(parent) == 0:
            parent.text = (parent.text or "") + node
        else:
            last = parent[-1]
            last.tail = (last.tail or "") + node
    else:
        parent.append(node)


def _fill(parent, text):
    """Set parent's inline content (text + child elements) from human text."""
    for node in parse_inline(text):
        _attach(parent, node)


# --- structural rendering ----------------------------------------------------


# A bid typed in lower case, `1d` `2nt` `1hs`: its strain in capitals, so it gets its suit glyphs. Only a level and
# strain letters that end the word - `3cue`, `6higher`, `1step` are words, and `2m` / `3x` classes, left alone.
_LOWER_BID_RE = re.compile(r"(?<![A-Za-z0-9])([1-7])(nt|n|[cdhs]+)(?![A-Za-z])")


def _bid_caps(bid):
    return _LOWER_BID_RE.sub(lambda m: m.group(1) + m.group(2).upper(), bid)


def html_bidtable(et_element, children, indent_level_is_even):
    # `indent_level_is_even` let's us alternately cycle the css class for every indent of the bid table
    div_class = "start" if indent_level_is_even else "startOddIndent"
    if len(children) > 0:
        ul = ET.SubElement(et_element, "ul")
        for c in children:
            li = ET.SubElement(ul, "li")
            div = ET.SubElement(li, "div")
            div.attrib["class"] = div_class
            desc_rows = c.desc.split("\\n")
            bid = re.sub(r"^P$", "Pass", _bid_caps(c.bid))
            bid = re.sub(r"^R$", "Rdbl", bid)
            bid = re.sub(r"^D$", "Dbl", bid)
            _fill(div, bid)
            # description follows the bid div inside the same li (mixed content)
            for node in _description(desc_rows[0]):
                _attach(li, node)
            # Extra description lines live *inside* this li, before the child
            # bids, so they read directly under the bid they belong to. (As
            # sibling <li>s of the same <ul> they would be emitted after the
            # whole child subtree.) The empty bid div reproduces the bid column
            # at every breakpoint, so no extra css is needed to align them.
            for dr in desc_rows[1:]:
                rowdiv = ET.SubElement(li, "div")
                rowdiv.attrib["class"] = "descRow"
                biddiv = ET.SubElement(rowdiv, "div")
                biddiv.attrib["class"] = div_class
                biddiv.text = " "
                for node in _description(dr):
                    _attach(rowdiv, node)
            html_bidtable(li, c.children, not indent_level_is_even)


# --- the four blocks the parser always recognised and html used to drop -------
#
# Deal diagrams, pipe tables, description lists and auctions were LaTeX-only. Each
# is a plain element tree here, with the inline renderer (`_fill`) doing the suit
# symbols, so `!s` / `4S` come out as they do everywhere else on the page. The
# Odin port (bridge-markup html.odin) builds the same trees, byte for byte.

_SEATS = {"N": "North", "E": "East", "S": "South", "W": "West"}

# An auction's spelled-out calls; anything else is a bid, through the inline renderer.
# How an auction's calls are written; `-`, a seat before the dealer's, is an empty cell.
_CALLS = {"P": "Pass", "D": "Dbl", "X": "Dbl", "R": "Rdbl", "XX": "Rdbl", "AP": "All pass", "-": ""}


def _diagram_head(diagram):
    """The header row as one line: board, dealer, vulnerability, contract, lead."""
    parts = []
    if diagram.board:
        parts.append("Board %s" % diagram.board)
    if diagram.dealer:
        parts.append("Dealer %s" % _SEATS[diagram.dealer])
    if diagram.vul:
        parts.append("Vul %s" % diagram.vul)
    if diagram.contract:
        level, strain, doubling, declarer = diagram.contract
        if level == "P":
            parts.append("Passed out")
        else:
            strain = "NT" if strain == "N" else "!" + strain.lower()
            parts.append("%s%s%s by %s" % (level, strain, doubling or "", _SEATS[declarer]))
    if diagram.lead:
        suit, card = diagram.lead
        parts.append("Lead !%s%s" % (suit.lower(), card))
    return " · ".join(parts)


def _hand_cell(row, hand):
    """One seat's cell: four lines of suit symbol and cards, a void as a dash. Empty for a hand not given."""
    cell = ET.SubElement(row, "td")
    if not hand:
        return
    cell.attrib["class"] = "hand"
    for suit, cards in zip("shdc", hand):
        line = ET.SubElement(cell, "div")
        _fill(line, "!%s %s" % (suit, "—" if cards == "-" else cards))


# The seats a vulnerability makes vulnerable.
_VULNERABLE = {"All": "NESW", "NS": "NS", "EW": "EW"}


def _compass(cell, diagram):
    """The middle of a diagram as a hand record draws it: N E S W round the edge, the board number in the middle,
    the dealer's letter marked (`dealer`) and the vulnerable seats in red (`vul`)."""
    vulnerable = _VULNERABLE.get(diagram.vul, "")
    grid = ET.SubElement(cell, "table", {"class": "bml-compass"})

    def seat(row, letter):
        classes = "seat" + (" dealer" if diagram.dealer == letter else "") + (" vul" if letter in vulnerable else "")
        ET.SubElement(row, "td", {"class": classes}).text = letter

    top = ET.SubElement(grid, "tr")
    ET.SubElement(top, "td")
    seat(top, "N")
    ET.SubElement(top, "td")
    middle = ET.SubElement(grid, "tr")
    seat(middle, "W")
    board = ET.SubElement(middle, "td", {"class": "board"})
    if diagram.board:
        board.text = diagram.board
    seat(middle, "E")
    bottom = ET.SubElement(grid, "tr")
    ET.SubElement(bottom, "td")
    seat(bottom, "S")
    ET.SubElement(bottom, "td")


def html_diagram(body, diagram):
    box = ET.SubElement(body, "div")
    box.attrib["class"] = "bml-diagram"
    head = _diagram_head(diagram)
    if head:
        caption = ET.SubElement(box, "div")
        caption.attrib["class"] = "bml-deal-head"
        _fill(caption, head)
    table = ET.SubElement(box, "table")
    table.attrib["class"] = "bml-deal"
    top = ET.SubElement(table, "tr")
    ET.SubElement(top, "td")
    _hand_cell(top, diagram.north)
    ET.SubElement(top, "td")
    middle = ET.SubElement(table, "tr")
    _hand_cell(middle, diagram.west)
    _compass(ET.SubElement(middle, "td", {"class": "compass"}), diagram)
    _hand_cell(middle, diagram.east)
    bottom = ET.SubElement(table, "tr")
    ET.SubElement(bottom, "td")
    _hand_cell(bottom, diagram.south)
    ET.SubElement(bottom, "td")


def _is_rule(cells):
    """A `|---+---|` row: a line, not data."""
    return len(cells) > 0 and re.match(r"[+-]+$", cells[0]) is not None


def html_table(body, rows):
    """Rows before the first rule are the header; a later rule draws a line above the row after it.

    A row with no cell filled in is left out, like an empty one: the editor's tab adds one past the last cell.
    """
    rows = [r for r in rows if any(r)]
    first_rule = next((i for i, r in enumerate(rows) if _is_rule(r)), -1)
    table = ET.SubElement(body, "table")
    table.attrib["class"] = "bml-table"
    ruled = False
    for i, cells in enumerate(rows):
        if _is_rule(cells):
            ruled = i != first_rule
            continue
        tr = ET.SubElement(table, "tr")
        if ruled:
            tr.attrib["class"] = "rule"
            ruled = False
        tag = "th" if i < first_rule else "td"
        for cell in cells:
            _fill(ET.SubElement(tr, tag), cell)


def html_description(body, items):
    dl = ET.SubElement(body, "dl")
    dl.attrib["class"] = "bml-desc"
    for item in items:
        term, sep, text = item.partition(" :: ")
        if sep:
            _fill(ET.SubElement(dl, "dt"), term.strip())
            _fill(ET.SubElement(dl, "dd"), text.strip())
        else:
            _fill(ET.SubElement(dl, "dd"), item.strip())


def _spell_call(call):
    """A call as the auction writes it (`_CALLS`), inside its brackets too: `(X)` is `(Dbl)`."""
    opening = "(" if call.startswith("(") else ""
    rest = call[len(opening) :]
    closing = ")" if rest.endswith(")") else ""
    core = rest[: len(rest) - len(closing)]
    return opening + _CALLS[core] + closing if core in _CALLS else call


# A call's mark, after it: `!` alerted, `*` / `**` / `^1` for a note under the auction.
_CALL_MARK_RE = re.compile(r"(.*?)(!|\*+|\^[0-9]+)?\Z")
# A note's own mark, at its start.
_NOTE_MARK_RE = re.compile(r"(\*+|\^[0-9]+)(.*)\Z")


def _mark(parent, mark):
    """A call's or a note's mark, superscript: `^1` is written 1."""
    sup = ET.SubElement(parent, "sup")
    sup.attrib["class"] = "bml-alert" if mark == "!" else "bml-note"
    _attach(sup, mark.lstrip("^"))


def html_auction(body, rows):
    """The `W N E S` header, the calls - in capitals, since the test (bml.py's `_is_auction`) is case-blind, so
    `1d p 1s` is an auction too - and the notes under it (bml.py's `auction_parts`)."""
    header, calls, notes = bml.auction_parts([r for r in rows if r])
    table = ET.SubElement(body, "table")
    table.attrib["class"] = "bml-auction"
    if header:
        tr = ET.SubElement(table, "tr")
        for seat in header:
            _fill(ET.SubElement(tr, "th"), seat.upper())
    for row in calls:
        tr = ET.SubElement(table, "tr")
        for token in row:
            call, mark = _CALL_MARK_RE.match(token.upper()).groups()
            td = ET.SubElement(tr, "td")
            _fill(td, _spell_call(call))
            if mark:
                _mark(td, mark)
    if not notes:
        return
    # Each note is its mark and its words; a row without a mark continues the note above it.
    items = []
    for row in notes:
        m = _NOTE_MARK_RE.match(row[0])
        if m:
            items.append((m.group(1), ([m.group(2)] if m.group(2) else []) + row[1:]))
        else:
            items[-1][1].extend(row)
    div = ET.SubElement(body, "div")
    div.attrib["class"] = "bml-auction-notes"
    for mark, words in items:
        note = ET.SubElement(div, "div")
        _mark(note, mark)
        _fill(note, " " + " ".join(words))


def normalise_header_id(text):
    # don't want spaces
    # don't want text that can be replaced with html suit special symbols
    def escape_suits(match):
        suit_text = match.group(0)
        return suit_text.lower()

    norm = text.replace(" ", "_").strip()
    norm = re.sub(r"\d[CDHS]", escape_suits, norm)
    return norm


_HEADINGS = (bml.ContentType.H1, bml.ContentType.H2, bml.ContentType.H3, bml.ContentType.H4)
# A callout's label, by its `#BOX` type.
_BOX_LABELS = {"note": "Note", "info": "Info", "tip": "Tip", "warning": "Warning"}
# What a box's left column holds.
_BESIDE = (bml.ContentType.DIAGRAM, bml.ContentType.BIDDING, bml.ContentType.TABLE, bml.ContentType.CAPTION)


def _classes(text):
    """The classes of a class hook on `text` (bml's `Classed`), "" for none."""
    return getattr(text, "classes", "")


def _with(base, classes):
    """A class attribute: `base` and the hook's classes after it."""
    return base + " " + classes if classes else base


def _heading_attrib(id_attr, text):
    """A heading's attributes: its anchor, then the classes of a hook on it."""
    classes = _classes(text)
    return {"id": id_attr, "class": classes} if classes else {"id": id_attr}


def to_html(content):
    html = ET.Element("html")
    head = ET.SubElement(html, "head")
    link = ET.SubElement(head, "link")
    link.attrib["rel"] = "stylesheet"
    link.attrib["type"] = "text/css"
    link.attrib["href"] = "bml.css"

    open_sans_font_link = ET.SubElement(head, "link")
    open_sans_font_link.attrib["href"] = (
        "https://fonts.googleapis.com/css?family=Open Sans"
    )
    open_sans_font_link.attrib["rel"] = "stylesheet"

    # `#+CSS: mine.css` (several, space separated): the author's own stylesheets, after bml.css so their rules
    # win - aimed, usually, at the classes of `{.name}` hooks. Paths are the page's, relative to it.
    for href in bml.meta["CSS"].split():
        ET.SubElement(head, "link", {"rel": "stylesheet", "type": "text/css", "href": href})

    meta_charset = ET.SubElement(head, "meta")
    meta_charset.attrib["charset"] = "UTF-8"

    meta_keywords = ET.SubElement(head, "meta")
    meta_keywords.attrib["name"] = "keywords"
    meta_keywords.attrib["content"] = "contract, bridge, bidding"

    body = ET.SubElement(html, "body")
    body.attrib["class"] = "content"

    h1_id_text_pairs = []

    # `#BOX`: the blocks go into its left column (`side`) while they are what sits beside text - a diagram, an
    # auction, a table, a caption - and the rest into its right column. `#ENDBOX`, a heading, or another
    # `#BOX` closes it. The box and each column are made when a block goes in: an empty one is written
    # `<div ... />`, which html does not close, so what follows would land inside it.
    #
    # `#BOX note|info|tip|warning Title` (or `#BOX Title`) puts the box in a CALLOUT frame (`frame`): a head with the
    # type's label and the title, made at once since the head is never empty, then the box inside it.
    #
    # A class hook (`{.name}`, bml's `Classed`) adds its classes to what its line makes: the heading, the
    # caption, the callout frame - or, for a `#BOX {.name}` with no type or title, the box itself.
    parent = body
    box = None
    box_classes = ""
    boxing = False
    frame = body
    for c in content:
        content_type, text = c
        if content_type in (bml.ContentType.BOX, bml.ContentType.ENDBOX) or content_type in _HEADINGS:
            parent, box, boxing, frame = body, None, content_type == bml.ContentType.BOX, body
            box_classes = _classes(text[1]) if content_type == bml.ContentType.BOX else ""
        if content_type == bml.ContentType.BOX and (text[0] or text[1]):
            kind, box_title = text
            frame_class = "bml-callout bml-" + kind if kind else "bml-callout"
            frame = ET.SubElement(body, "div", {"class": _with(frame_class, box_classes)})
            box_classes = ""
            frame_head = ET.SubElement(frame, "div", {"class": "bml-callout-head"})  # not `head`: the page's
            if kind:
                label = ET.SubElement(frame_head, "span", {"class": "bml-callout-label"})
                label.text = _BOX_LABELS[kind]
                if box_title:
                    label.tail = " "  # a real space: the workbench's preview ignores a margin on the label
            if box_title:
                _fill(ET.SubElement(frame_head, "span", {"class": "bml-callout-title"}), box_title)
        if content_type in (bml.ContentType.BOX, bml.ContentType.ENDBOX):
            continue
        if content_type == bml.ContentType.BIDTABLE and not text.export:
            continue
        if boxing:
            beside = content_type in _BESIDE
            if box is None:
                box = ET.SubElement(frame, "div", {"class": _with("bml-box", box_classes)})
                parent = ET.SubElement(box, "div", {"class": "bml-box-side" if beside else "bml-box-text"})
            elif parent.get("class") == "bml-box-side" and not beside:
                parent = ET.SubElement(box, "div", {"class": "bml-box-text"})
        if content_type == bml.ContentType.PARAGRAPH:
            element = ET.SubElement(parent, "p")
            _fill(element, text)
        elif content_type == bml.ContentType.BIDTABLE:
            if not text.export:
                continue
            element = ET.SubElement(parent, "div")
            element.attrib["class"] = "bidtable"
            html_bidtable(element, text.children, indent_level_is_even=True)
        elif content_type == bml.ContentType.H1:
            id_attr = normalise_header_id(text)
            h1_id_text_pairs.append((id_attr, text))
            element = ET.SubElement(body, "h1", _heading_attrib(id_attr, text))
            _fill(element, text)
        elif content_type == bml.ContentType.H2:
            element = ET.SubElement(body, "h2", _heading_attrib(normalise_header_id(text), text))
            _fill(element, text)
        elif content_type == bml.ContentType.H3:
            element = ET.SubElement(body, "h3", _heading_attrib(normalise_header_id(text), text))
            _fill(element, text)
        elif content_type == bml.ContentType.H4:
            element = ET.SubElement(body, "h4", _heading_attrib(normalise_header_id(text), text))
            _fill(element, text)
        elif content_type == bml.ContentType.LIST:
            element = ET.SubElement(parent, "ul")
            for l in text:
                li = ET.SubElement(element, "li")
                _fill(li, l)
        elif content_type == bml.ContentType.ENUM:
            element = ET.SubElement(parent, "ol")
            for l in text:
                li = ET.SubElement(element, "li")
                _fill(li, l)
        elif content_type == bml.ContentType.DIAGRAM:
            html_diagram(parent, text)
        elif content_type == bml.ContentType.TABLE:
            html_table(parent, text)
        elif content_type == bml.ContentType.DESCRIPTION:
            html_description(parent, text)
        elif content_type == bml.ContentType.BIDDING:
            html_auction(parent, text)
        elif content_type == bml.ContentType.CAPTION:
            element = ET.SubElement(parent, "div", {"class": _with("bml-caption", _classes(text))})
            _fill(element, text)

    top_link = ET.SubElement(body, "a")
    top_link.attrib["class"] = "top-link"
    top_link.attrib["href"] = "#"
    top_link.text = "Top"

    links_div = ET.SubElement(body, "div")
    links_div.attrib["class"] = "nav-links"

    for header_id, header_text in h1_id_text_pairs:
        list_item = ET.SubElement(links_div, "ul")
        li = ET.SubElement(list_item, "li")
        link = ET.SubElement(li, "a")
        link.attrib["href"] = f"#{header_id}"
        _fill(link, header_text)

    title = ET.SubElement(head, "title")
    title.text = bml.meta["TITLE"]
    return str(ET.tostring(html), "UTF8")


if __name__ == "__main__":
    import sys
    import os

    outputfile = ""
    if len(sys.argv) < 2:
        print("What's the name of the file you want to convert?")
        outputfile = input()
        if not os.path.exists(outputfile):
            sys.exit("ERROR: File %s was not found!" % outputfile)
        bml.content_from_file(outputfile)
        outputfile = outputfile.split(".")[0]
    else:
        if not os.path.exists(sys.argv[1]):
            sys.exit("ERROR: File %s was not found!" % sys.argv[1])

        bml.content_from_file(sys.argv[1])
        outputfile = os.path.basename(sys.argv[1]).split(".")[0]

    h = to_html(bml.content)
    with open(outputfile + ".html", "w", encoding="utf-8") as f:
        f.write(h)
