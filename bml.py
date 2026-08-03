import re
import copy
from collections import defaultdict

content = []

# where we keep copies
clipboard = {}

vulnerability = "00"
seat = "0"

# meta information about the BML-file, supported:
# TITLE = the name of the system
# DESCRIPTION = a short summary of the system
# AUTHOR = the system's author(s)
# data in meta is only set once, and isn't overwritten
meta = defaultdict(str)


class Diagram:
    """A structure for deal diagrams"""

    # each hand can be None or a tuple of four strings (s, h, d, c)
    north = None
    east = None
    south = None
    west = None
    dealer = None  # can be None or "N", "E", "S", "W"
    vul = None  # can be None or "ALL", "NO", "NS", "EW"
    board = None  # can be None or a string
    lead = None  # can be None or a string tuple ([shdc], [2-9TAKQJ])"
    contract = None  # can be None or a tuple of strings

    def __init__(self, firstrow, hands):
        for h in hands:
            hand = h[0]
            if hand == "N":
                self.north = h[1:]
            elif hand == "E":
                self.east = h[1:]
            elif hand == "S":
                self.south = h[1:]
            elif hand == "W":
                self.west = h[1:]

        dealer = re.search(r"(?:\A|\s)([NESW]),?(?:\Z|\s)", firstrow)
        if dealer:
            self.dealer = dealer.group(1)

        vul = re.search(r"(?:\A|\s)(All|None|EW|NS),?(?:\Z|\s)", firstrow)
        if vul:
            self.vul = vul.group(1)

        board = re.search(r"(?:\A|\s)#?(\d+),?(?:\Z|\s)", firstrow)
        if board:
            self.board = board.group(1)

        lead = re.search(r"(?:\A|\s)([shdc])([2-9AKQJT]),?(?:\Z|\s)", firstrow)
        if lead:
            self.lead = lead.groups()

        contract = re.search(r"(?:\A|\s)(PASS),?(?:\Z|\s)", firstrow)
        if contract:
            self.contract = ("P", None, None, None)
        else:
            contract = re.search(
                r"(?:\A|\s)(\d)([SHDCN])(XX?)?([NESW]),?(?:\Z|\s)", firstrow
            )
            if contract:
                self.contract = contract.groups()


# A bid token may spell out a whole auction, `1C--P--1H` or `(1CD)--1H--2C`,
# with `--` between successive calls. Those separators must be split on before
# anything else touches the text: stripping them first glues the calls into one
# unreadable run (`(1CD)1H2C`), and re-finding calls with a `\d[A-Za-z]+` regex
# loses any that do not start with a digit (`1C--P--1H` came back `1CP (P) 1H`,
# the pass swallowed by the preceding call).
_COMPOUND_SEP_RE = re.compile(r"-{2,}")

# The corpus also writes `1c-2d-3d-3s-4c` with single dashes. A single dash is
# ambiguous, though -- it also joins words inside a pseudo-bid (`new-suit`,
# `jump-cue`, `2-Cue`) -- so it only separates when *every* part it produces is
# itself call-shaped.
_SINGLE_SEP_RE = re.compile(r"-")
_CALL_SHAPED_RE = re.compile(r"^\(?[1-7]")

# A call written as a markdown link, `[1D](#1C--1D)`. Its *target* legitimately
# contains `--`, so such a token is never split.
_LINK_TOKEN_RE = re.compile(r"^\[[^\]]*\]\(")

# Calls that say the auction was spelled out in full rather than abbreviated to
# our side's bids.
_EXPLICIT_CALLS = frozenset({"P", "PASS", "X", "XX", "D", "DBL", "R", "RDBL"})


def _normalise_call(text):
    """One call, tidied: `NT` -> `N`, stray dashes and semicolons dropped."""
    return re.sub(r"[-;]", "", text).replace("NT", "N")


def _is_call_shaped(text):
    """A level bid or a bare pass/double/redouble, bracketed or not."""
    return bool(_CALL_SHAPED_RE.match(text)) or text.upper() in _EXPLICIT_CALLS


def bid_representation(bid):
    """The canonical spelling of a bid token: calls separated by spaces.

    A single call is returned as written (bar the tidying above). A compound
    token becomes its calls, space separated, with `(P)` inserted between them
    when the notation left the opponents' passes implicit -- `1H--2C` is our
    side bidding twice, so it reads `1H (P) 2C`, while `1C--P--1H` and
    `(1CD)--1H--2C` already say who called and are left alone.
    """
    if _LINK_TOKEN_RE.match(bid.strip()):
        return _normalise_call(bid)
    parts = [p for p in _COMPOUND_SEP_RE.split(bid) if p.strip("-")]
    if len(parts) < 2:
        single = [p for p in _SINGLE_SEP_RE.split(bid) if p]
        if len(single) > 1 and all(_is_call_shaped(p) for p in single):
            parts = single
    calls = [c for c in (_normalise_call(p) for p in parts) if c]
    if len(calls) < 2:
        return _normalise_call(bid)
    spelled_out = any(
        c.startswith("(") or c.upper() in _EXPLICIT_CALLS for c in calls
    )
    return (" " if spelled_out else " (P) ").join(calls)


class Node:
    """A node in a bidding table"""

    def __init__(self, bid, desc, indentation, parent=None, desc_indentation=-1):
        self.vul = "00"
        self.seat = "0"
        self.export = True
        self.bid = bid
        self.desc = desc
        self.indentation = indentation
        self.desc_indentation = desc_indentation
        self.children = []
        self.parent = parent
        self.bidrepr = bid_representation(bid)

    def add_child(self, bid, desc, indentation, desc_indentation):
        """appends a new child Node to the node"""
        child = Node(bid, desc, indentation, self, desc_indentation)
        child.vul = self.vul
        child.seat = self.seat
        self.children.append(child)
        return self.children[-1]

    def get_sequence(self):
        """List with all the parent, and the current, bids"""

        if self.parent.bidrepr == "root":
            return [self.bidrepr]
        if self.parent:
            ps = self.parent.get_sequence()
            ps.append(self.bidrepr)
            return ps

    def set_children(self, children):
        """Used when copying from another Node"""
        self.children = copy.deepcopy(children)
        for c in self.children:
            c.parent = self
            # c.vul = self.vul
            # c.seat = self.vul

    def __getitem__(self, arg):
        return self.children[arg]


# A bid row starts with a bid token: an opponent bid in parens "(...)" (always
# bracketed, e.g. (2D), (P), (any)), or a level bid (digit + suits/NT, possibly
# "!x" shorthand, "+", alt suits), or a bare pass/double/redouble word. The
# (?![A-Za-z]) stops "P"/"X"/"R" matching the start of prose words like
# "Points" or "Xfer".
_BID_TOKEN_RE = re.compile(
    r"^(?:\(|\d[0-9A-Za-z!+]*|(?:Pass|P|XX|X|Dbl|Rdbl|R)(?![A-Za-z]))"
)

# ...except that "starts with (" also describes a lettered prose list nested in
# a bid table:
#
#     3C = artificial, multi-meaning.
#     (a) minimum (semi)balanced no singleton
#     (d) void hand, any strength
#
# which would become phantom auction positions. A bracketed *single letter* is
# a call only for the call letters, matched case-sensitively -- `(P)`, `(X)`,
# `(D)` are calls, `(a)`..`(d)` are list markers. Bracketed words stay bids:
# `(any)`, `(cue)`, `(higher)`, `(overcall)` are real pseudo-bid rows.
_SINGLE_LETTER_BRACKET_RE = re.compile(r"^\(([A-Za-z])\)")
_CALL_LETTERS = frozenset("PXRD")


def _looks_like_bid(token):
    single = _SINGLE_LETTER_BRACKET_RE.match(token)
    if single and single.group(1) not in _CALL_LETTERS:
        return False
    return bool(_BID_TOKEN_RE.match(token))


# Stricter bid-start pattern for *block detection* (deciding a paragraph is a
# bidtable). Unlike _looks_like_bid it will not fire on prose that merely opens
# with "(" (e.g. "(Opener's LTC ...)"): an opponent bid in parens must be short
# and bid-shaped. Used together with a " = " assignment check so a stray prose
# line that happens to start bid-like (e.g. "5!h--4!s *intermediate* ...") is
# not misread as a bidtable.
_BIDTABLE_FIRST_RE = re.compile(
    r"^\s*(?:"
    r"\(?\d[!A-Za-z0-9+]*"  # 2H, (2D, 2!c, (2!d, 2N+
    r"|\((?:\d|Pass|P|XX|X|Dbl|Rdbl|R|any|oth)"  # opponent bid: (2D) (P) (any)
    r"|(?:Pass|XX|X|Dbl|Rdbl)(?=--|\s*=)"  # Pass--(1H)..., X = ...
    r")"
)


# A bid may be written as a markdown link, e.g. `[1D](#1C--1D) Negative 0--7`.
# The link label carries the bid; the target may contain spaces (`(#1C Weak
# Strong)`) so it cannot be found by splitting on whitespace. The label itself
# must be a single word, so a prose line that merely opens with a link
# (`[1N system on](#1NT Opening)` as a description continuation) is not
# mistaken for a bid.
_LINK_BID_RE = re.compile(r"^\[([^\]\s]+)\]\([^)]*\)")


def _is_bid_row(stripped_row):
    """A new bid row vs a wrapped-description continuation line.

    Bid rows are `<bid> = <desc>` (or `<bid> <desc>`, or a bare `<bid>`), where
    <bid> may be a real bid, a pseudo-bid word (e.g. `any`, `other`), a
    compound sequence (`Pass--(1H)--X`), or any of those wrapped in a markdown
    link. Pseudo-bids are caught by the " = " separator; genuine continuation
    lines are prose without it and without a bid-like first token.
    """
    link = _LINK_BID_RE.match(stripped_row)
    first_token = link.group(1) if link else stripped_row.split(" ")[0]
    return _looks_like_bid(first_token) or " = " in stripped_row


def create_bidtree(text):
    global clipboard, vulnerability, seat
    root = Node("root", "root", -1)
    root.vul = vulnerability
    root.seat = seat
    lastnode = root

    # breaks when no more CUT in bidtable
    while True:
        cut = re.search(
            r"^(\s*)#\s*CUT\s+(\S+)\s*\n(.*)#ENDCUT[ ]*\n?",
            text,
            flags=re.DOTALL | re.MULTILINE,
        )
        if not cut:
            break
        value = cut.group(3).split("\n")
        for i in range(len(value)):
            value[i] = value[i][len(cut.group(1)) :]
        value = "\n".join(value)
        clipboard[cut.group(2)] = value  # group2=key
        text = text[: cut.start()] + text[cut.end() :]

    # breaks when no more COPY in bidtable
    while True:
        copy = re.search(
            r"^(\s*)#\s*COPY\s+(\S+)\s*\n(.*)#ENDCOPY[ ]*\n?",
            text,
            flags=re.DOTALL | re.MULTILINE,
        )
        if not copy:
            break
        value = copy.group(3).split("\n")
        for i in range(len(value)):
            value[i] = value[i][len(copy.group(1)) :]
        value = "\n".join(value)
        clipboard[copy.group(2)] = value  # group2=key
        text = text[: copy.end(3)] + text[copy.end() :]
        text = text[: copy.start()] + text[copy.start(3) :]

    # breaks when no more PASTE in bidtable
    while True:
        paste = re.search(
            r"^(\s*)#\s*PASTE\s+(\S+)[^\S\n]*(.*)\n?", text, flags=re.MULTILINE
        )

        if not paste:
            break
        indentation = paste.group(1)
        lines = clipboard[paste.group(2)]
        for r in paste.group(3).split():
            target, replacement = r.split("=")
            lines = lines.replace(target, replacement)
        lines = lines.split("\n")
        for l in range(len(lines)):
            lines[l] = indentation + lines[l]
        text = text[: paste.start()] + "\n".join(lines) + "\n" + text[paste.end() :]

    hide = re.search(r"^\s*#\s*HIDE\s*\n", text, flags=re.MULTILINE)
    if hide:
        root.export = False
        text = text[: hide.start()] + text[hide.end() :]

    text = re.sub(r"^\s*#\s*BIDTABLE\s*\n", "", text)

    if text.strip() == "":
        return None

    for row in text.split("\n"):
        original_row = row
        if row.strip() == "":
            continue  # could perhaps be nicer by stripping spaces resulting from copy/paste
        indentation = len(row) - len(row.lstrip())

        # A line that is indented under the current bid and is not itself a bid
        # row is a description continuation (a line break within the desc).
        # We key off row *shape* (bid-like first token, or a " = " separator),
        # not an absolute text column, so short bids like `X` no longer make a
        # genuine child row collide with the parent's description column.
        # Continuations do not have to be indented: a lettered prose list under
        # a top-level bid sits in the same column as the bid it explains.
        stripped_row = row.strip()
        if (
            indentation >= lastnode.indentation
            and lastnode.desc != "root"
            and not _is_bid_row(stripped_row)
        ):
            lastnode.desc += "\\n" + stripped_row
            continue
        row = row.strip()
        bid = row.split(" ")[0]
        desc = " ".join(row.split(" ")[1:]).strip()
        desc_indentation = original_row.find(desc)
        # removes equal signs at the beginning of the description
        new_desc = re.sub(r"^=\s*", "", desc)
        desc_indentation += len(desc) - len(new_desc)
        desc = new_desc
        while indentation < lastnode.indentation:
            lastnode = lastnode.parent
        if indentation > lastnode.indentation:
            lastnode = lastnode.add_child(bid, desc, indentation, desc_indentation)
        elif indentation == lastnode.indentation:
            lastnode = lastnode.parent.add_child(
                bid, desc, indentation, desc_indentation
            )
    return root


class ContentType:
    BIDTABLE = 1
    PARAGRAPH = 2
    H1 = 3
    H2 = 4
    H3 = 5
    H4 = 6
    LIST = 7
    ENUM = 8
    DIAGRAM = 9
    TABLE = 10
    DESCRIPTION = 11
    BIDDING = 12


def get_content_type(text):
    global meta, vulnerability, seat

    if text.startswith("****"):
        return (ContentType.H4, text[4:].lstrip())
    if text.startswith("***"):
        return (ContentType.H3, text[3:].lstrip())
    if text.startswith("**"):
        return (ContentType.H2, text[2:].lstrip())
    if text.startswith("*"):
        return (ContentType.H1, text[1:].lstrip())

    # The first element is empty, therefore [1:]
    if re.match(r"^\s*-", text):
        if text.find(" :: ") >= 0:
            return (
                ContentType.DESCRIPTION,
                re.split(r"^\s*-\s*", text, flags=re.MULTILINE)[1:],
            )
        return (ContentType.LIST, re.split(r"^\s*-\s*", text, flags=re.MULTILINE)[1:])

    if re.match(r"^\s*#VUL", text):
        vulnerability = text.split()[1]
        return None

    if re.match(r"^\s*#SEAT", text):
        seat = text.split()[1]
        return None

    if re.match(r"^\s*1\.", text):
        return (
            ContentType.ENUM,
            re.split(r"^\s*\d*\.\s*", text, flags=re.MULTILINE)[1:],
        )

    if re.match(
        r"^\s*\(?[1-7]?[NTPDRCDHS]\)?\s+\(?[1-7]?[NTPDRCDHS]\)?\s+\(?[1-7]?[NTPDRCDHS]\)?\s+\(?[1-7]?[NTPDRCDHS]\)?\s*",
        text,
    ):
        table = []
        for r in text.split("\n"):
            if r:
                table.append(r.split())
        return (ContentType.BIDDING, table)

    # First clause preserves the original detection exactly (all existing
    # bidtables). Second clause adds blocks whose first bid uses "!x" shorthand
    # or a non-digit lead (Pass--..., (2!d), 2!c = ...); the " = " requirement
    # keeps prose that merely starts bid-like out.
    if re.match(r"^\s*\(?\d[A-Za-z]+", text) or (
        _BIDTABLE_FIRST_RE.match(text) and " = " in text
    ):
        bidtree = create_bidtree(text)
        if bidtree:
            return (ContentType.BIDTABLE, bidtree)
        return None

    # Tables
    if re.match(r"^\s*\|", text):
        table = []
        rows = text.split("\n")
        for r in rows:
            table.append([c.strip() for c in re.findall(r"(?<=\|)[^\|]+", r)])
        return (ContentType.TABLE, table)

    # diagrams
    hands = re.findall(
        r"""^\s*([NESW]):?\s*
                           ([2-9AKQJTx-]+)\s+
                           ([2-9AKQJTx-]+)\s+
                           ([2-9AKQJTx-]+)\s+
                           ([2-9AKQJTx-]+)""",
        text,
        flags=re.MULTILINE | re.VERBOSE,
    )

    if hands and len(hands) + 2 >= len(text.split("\n")):
        return (ContentType.DIAGRAM, Diagram(text.split("\n")[0], hands))

    metamatch = re.match(r"^\s*#\+(\w+):\s*(.*)", text)

    if metamatch:
        keyword = metamatch.group(1)
        if keyword in meta:
            return None
        value = metamatch.group(2)
        meta[keyword] = value
        return None

    if re.match(r"^\s*#", text):
        bidtree = create_bidtree(text)
        if bidtree:
            return (ContentType.BIDTABLE, bidtree)
        return None

    if re.search(r"\S", text):
        text = re.sub(r"\n +", "\n", text.strip())
        return (ContentType.PARAGRAPH, text)

    return None


def include_file(matchobj):
    filename = matchobj.group(1)
    text = ""
    with open(filename, "r", encoding="utf-8") as f:
        text = f.read()
    return "\n" + text + "\n"


def content_from_file(filename):
    """Parse a bml file into `content` and return it.

    Resets per-file module state on entry so parsing two files in one process
    (e.g. the quiz serving two systems) does not leak the first file's tables,
    clipboard, vulnerability/seat, or meta into the second. `clipboard`,
    `vulnerability` and `seat` stay stateful *within* a file (across blocks) —
    they are only reset here, between files. `meta` keeps its "first value per
    key wins" semantics within a file.
    """
    global content, clipboard, vulnerability, seat
    content = []
    clipboard = {}
    vulnerability = "00"
    seat = "0"
    meta.clear()
    paragraphs = []
    with open(filename, "r", encoding="utf-8") as f:
        text = f.read()
        text = re.sub(
            r"^\s*#\s*INCLUDE\s*(\S+)\s*\n?", include_file, text, flags=re.MULTILINE
        )
        text = re.sub(r"^//.*\n", "", text, flags=re.MULTILINE)

        # WARN: this strips real links "blah blah [link text](#https://foo.com)" is chopped to
        # "blah blah [link text](#https:/" not sure what other problem it solves and what may break by removing it
        # text = re.sub(r'//.*', '', text)

        paragraphs = re.split(r"([ ]*\n){2,}", text)

    for c in paragraphs:
        content_type = get_content_type(c)
        if content_type:
            content.append(content_type)
    return content


if __name__ == "__main__":
    # print('To use BML, use the subprograms bml2html, bml2latex or bml2bss')
    content_from_file("test.bml")
