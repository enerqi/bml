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
_INLINE_RE = re.compile(
    r"(?P<link>\[(?P<ltext>[^\[\]]*?)\]\((?P<ltarget>[^()]+?)\))"
    r"|(?<![^\s>])\*(?P<strong>\S(?:[^*]*\S)?)\*"
    r"|(?<!\S)/(?P<em>\S(?:[^/]*\S)?)/"
    r"|(?<!\S)=(?P<code>\S(?:[^=]*\S)?)="
)


def _suit_span(letter):
    cls, glyph = _SUIT[letter]
    span = ET.Element("span")
    span.set("class", cls)
    span.text = glyph
    return span


def _plain(text):
    """List of str | span Element for the non-nesting inline bits."""
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
            bid = re.sub(r"^P$", "Pass", c.bid)
            bid = re.sub(r"^R$", "Rdbl", bid)
            bid = re.sub(r"^D$", "Dbl", bid)
            _fill(div, bid)
            # description follows the bid div inside the same li (mixed content)
            for node in parse_inline(desc_rows[0]):
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
                for node in parse_inline(dr):
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
_CALLS = {"P": "Pass", "D": "Dbl", "R": "Rdbl", "AP": "All pass"}


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
    ET.SubElement(middle, "td").attrib["class"] = "compass"
    _hand_cell(middle, diagram.east)
    bottom = ET.SubElement(table, "tr")
    ET.SubElement(bottom, "td")
    _hand_cell(bottom, diagram.south)
    ET.SubElement(bottom, "td")


def _is_rule(cells):
    """A `|---+---|` row: a line, not data."""
    return len(cells) > 0 and re.match(r"[+-]+$", cells[0]) is not None


def html_table(body, rows):
    """Rows before the first rule are the header; a later rule draws a line above the row after it."""
    rows = [r for r in rows if r]
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


def html_auction(body, rows):
    table = ET.SubElement(body, "table")
    table.attrib["class"] = "bml-auction"
    for calls in rows:
        if not calls:
            continue
        tr = ET.SubElement(table, "tr")
        for call in calls:
            _fill(ET.SubElement(tr, "td"), _CALLS.get(call, call))


def normalise_header_id(text):
    # don't want spaces
    # don't want text that can be replaced with html suit special symbols
    def escape_suits(match):
        suit_text = match.group(0)
        return suit_text.lower()

    norm = text.replace(" ", "_").strip()
    norm = re.sub(r"\d[CDHS]", escape_suits, norm)
    return norm


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

    meta_charset = ET.SubElement(head, "meta")
    meta_charset.attrib["charset"] = "UTF-8"

    meta_keywords = ET.SubElement(head, "meta")
    meta_keywords.attrib["name"] = "keywords"
    meta_keywords.attrib["content"] = "contract, bridge, bidding"

    body = ET.SubElement(html, "body")
    body.attrib["class"] = "content"

    h1_id_text_pairs = []

    for c in content:
        content_type, text = c
        if content_type == bml.ContentType.PARAGRAPH:
            element = ET.SubElement(body, "p")
            _fill(element, text)
        elif content_type == bml.ContentType.BIDTABLE:
            if not text.export:
                continue
            element = ET.SubElement(body, "div")
            element.attrib["class"] = "bidtable"
            html_bidtable(element, text.children, indent_level_is_even=True)
        elif content_type == bml.ContentType.H1:
            id_attr = normalise_header_id(text)
            h1_id_text_pairs.append((id_attr, text))
            element = ET.SubElement(body, "h1", {"id": id_attr})
            _fill(element, text)
        elif content_type == bml.ContentType.H2:
            element = ET.SubElement(body, "h2", {"id": normalise_header_id(text)})
            _fill(element, text)
        elif content_type == bml.ContentType.H3:
            element = ET.SubElement(body, "h3", {"id": normalise_header_id(text)})
            _fill(element, text)
        elif content_type == bml.ContentType.H4:
            element = ET.SubElement(body, "h4", {"id": normalise_header_id(text)})
            _fill(element, text)
        elif content_type == bml.ContentType.LIST:
            element = ET.SubElement(body, "ul")
            for l in text:
                li = ET.SubElement(element, "li")
                _fill(li, l)
        elif content_type == bml.ContentType.ENUM:
            element = ET.SubElement(body, "ol")
            for l in text:
                li = ET.SubElement(element, "li")
                _fill(li, l)
        elif content_type == bml.ContentType.DIAGRAM:
            html_diagram(body, text)
        elif content_type == bml.ContentType.TABLE:
            html_table(body, text)
        elif content_type == bml.ContentType.DESCRIPTION:
            html_description(body, text)
        elif content_type == bml.ContentType.BIDDING:
            html_auction(body, text)

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
