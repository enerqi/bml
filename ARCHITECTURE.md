# BML implementation notes

What the code does, and why it is shaped this way. Aimed at someone who can
read Python but has not seen a compiler pipeline before, and at anyone about to
change the parser without wanting to re-derive its invariants from the regexes.

For *what the markup means* to an author, read `README.org` and `example.txt`.
For known defects and the plan that closed them, see `bugs.md` and
`refactor-plan.md`.

---

## 1. The shape of the whole thing

BML is a small **document compiler**. Like any compiler it has a front end that
turns text into a data structure, and back ends that turn that data structure
into output formats:

```
   .bml source
      │
      │  bml.content_from_file()          ── front end (bml.py) ──
      ├─ 1. #INCLUDE expansion            textual, recursive
      ├─ 2. // comment stripping
      ├─ 3. blank-line split into blocks  "paragraphs"
      └─ 4. per-block classification      get_content_type()
                │
                ├── bid tables ──► create_bidtree() ──► Node tree
                └── everything else ─────────────────► str / list / Diagram
      │
      ▼
   bml.content : list[(ContentType, payload)]      ── the IR ──
      │
      ├─ bml2html.to_html()      ── back ends ──   html (ElementTree)
      ├─ bml2latex.to_latex()                      LaTeX (string writing)
      └─ bml2bss.to_systemdata()                   Bridge System Suite XML
```

The list of `(ContentType, payload)` pairs is the **intermediate
representation** (IR): the one thing front end and back ends agree on. Adding
an output format means reading the IR; adding syntax means producing it. The
bridge-bidding-system quiz app is a fourth consumer — it imports `bml`, calls
`content_from_file`, and walks the bid trees itself instead of rendering them.

Two supporting modules sit beside this:

- `bmlbids.py` — the canonical model of a *bridge call*. Beyond `1H`, `(2D)`
  and `X` it knows multi-suit `1HS`, class shortcuts `2M`/`3m`, the wildcard
  `3*`/`3x`, the alternation `2D/2H`, `2N+`, and the *pseudo-bids* the tables
  use in place of a call: `oM` ("the other major"), `next`, `jump`, `cue`,
  `new`/`suit`/`2Y`, `game`, and the catch-alls `any`/`other`, `(overcall)`,
  `(bid)`. It records which rule applies; the ones that depend on the auction
  are resolved by the consumer that has the auction (the quiz's `bidfilter`,
  see `apps/quiz/PSEUDO_BID_TOKENS.md` in the bridge repo). It exists because
  three consumers had each grown their own "is this token a bid" regex and
  disagreed about `1HS`.
- `tests/` — golden-snapshot harnesses (§7).

### Why a block-at-a-time classifier and not a grammar?

BML has no grammar in the formal (BNF/parser-generator) sense. It is a
**line-oriented, indentation-sensitive, heuristic** format in the family of
Markdown and Org-mode: block kind is inferred from how a block *starts*, and
structure from how its lines are *indented*. That choice is why the source is
~450 lines of regex rather than a lexer + LR parser, and it is also the origin
of most historical bugs — heuristics have ambiguous cases, and a grammar would
have rejected them loudly instead of silently guessing (§6).

---

## 2. Front end, stage by stage (`bml.py`)

### 2.1 Include expansion

`content_from_file` reads the file, then runs `re.sub` over
`^\s*#\s*INCLUDE\s*(\S+)` with `include_file` as the replacement function,
which reads the named file and splices its text in. `re.sub` does not rescan
what a replacement function returns, so expansion is **exactly one level
deep**: an `#INCLUDE` inside an included file is *not* expanded — it survives
as literal text, is later classified by guard 11 (§2.3) as a stray `#`
directive, and that file's content is silently lost. Verified, not theoretical.
The bridge repo stays within one level: root documents include chapters,
chapters include nothing.

Includes are resolved **relative to the current working directory**, not to the
including file. Every consumer therefore `chdir`s to the document directory
before parsing (the quiz does this explicitly; so do the test harnesses).

Then `^//.*\n` comment lines are dropped.

### 2.2 Blocks

```python
paragraphs = re.split(r"([ ]*\n){2,}", text)
```

One or more blank lines separate blocks. This is the *only* block delimiter:
a bid table ends where the blank line is. Consequently indentation inside a
block is free-form, and a stray blank line inside a bid table splits it into
two unrelated tables — a real authoring trap, since the second half then
re-roots at indentation 0.

### 2.3 Classification — `get_content_type(text)`

An **ordered chain of guards**; the first pattern that matches wins, and the
payload is built by that branch. Order is load-bearing (a header line also
matches "starts with `*`", a bid table also matches "has text"), so the chain
runs from most specific to least:

| order | test on the block's start | ContentType | payload |
|---|---|---|---|
| 1 | `****` / `***` / `**` / `*` | H4…H1 | header text (str) |
| 2 | `-` bullets, with ` :: ` | DESCRIPTION | list of items |
| 3 | `-` bullets | LIST | list of items |
| 4 | `#VUL` / `#SEAT` | *(none)* | sets parser state, block disappears |
| 5 | `1.` | ENUM | list of items |
| 6 | four call-ish columns | BIDDING | list of rows (an auction diagram) |
| 7 | bid-shaped first token | **BIDTABLE** | `Node` tree |
| 8 | `\|` | TABLE | list of rows of cells |
| 9 | four `N: … … … …` hand lines | DIAGRAM | `Diagram` |
| 10 | `#+KEY: value` | *(none)* | sets `meta[KEY]`, block disappears |
| 11 | any other `#` directive | BIDTABLE | `Node` tree (macro-only blocks) |
| 12 | any non-whitespace | PARAGRAPH | text (str) |

Guards 4 and 10 return `None`: the block is *consumed* for its side effect
(parser state, document metadata) and contributes nothing to the IR. Guards 7
and 11 also return `None` when the block held nothing but macros — a `#CUT`
block, say, whose entire body has been lifted into the clipboard. A `#HIDE`
block does produce a tree, flagged `export = False`, which back ends skip.

Guard 7 is the interesting one, and the historical trouble spot:

```python
if re.match(r"^\s*\(?\d[A-Za-z]+", text) or (
    _BIDTABLE_FIRST_RE.match(text) and " = " in text
):
```

The first clause is the original test — "optional `(`, a digit, then a letter"
— kept verbatim so every table that used to be detected still is. It cannot see
`(2!d)` (a `!` follows the digit) or a leading `X`/`Pass--…`, and a block it
misses does not degrade gracefully: it falls through to guard 12 and is emitted
as a **paragraph**, `=` signs and all (this was bug 4). The second clause adds
those cases via `_BIDTABLE_FIRST_RE`, conjoined with a ` = ` requirement so
that prose which merely opens bid-shaped ("5!h--4!s *intermediate*…") stays
prose.

### 2.4 Parser state that is not in the IR

`bml.py` keeps module-level globals: `content`, `clipboard` (macro store),
`vulnerability`, `seat`, `meta`. They are part of the **public API** — consumers
read `bml.content` and `bml.meta` after the call — so they cannot be replaced
with a parser object without breaking them.

`content_from_file` resets them on entry and returns `content`, so parsing two
documents in one process (the quiz serves several systems) no longer bleeds the
first document's tables into the second. `clipboard`/`vulnerability`/`seat`
remain stateful *within* a document, which is exactly their semantics: `#VUL`
in one block applies to later blocks.

---

## 3. The bid-table parser — `create_bidtree(text)`

This is the core algorithm and the part worth understanding properly.

Input is one block of text like:

```
1C = 16+ any, or 17+ balanced
  (any) = interference
  1D = 0--7
    1H = my hearts
    1S = my spades
  1H = 8+, 4+ hearts
```

Output is a tree of `Node`, rooted at a synthetic `root` node.

### 3.1 Phase A — text macros

Before any tree is built, four directives are expanded **on the raw text**:

- `#CUT name … #ENDCUT` — store the enclosed lines under `name` and *remove*
  them from the text.
- `#COPY name … #ENDCOPY` — store them and *keep* them.
- `#PASTE name a=b c=d` — splice the stored lines in here, re-indented to the
  paste site, after literal string substitutions (`a`→`b`).
- `#HIDE` — mark the tree `export = False`; it is parsed but back ends skip it.

This is a **textual macro system** — closer to C's `#include`/`#define` than to
a function call. Substitution is plain `str.replace`, so `2H=2S` rewrites every
occurrence of `2H` anywhere in the pasted text, description prose included.
Each expander loops until its pattern no longer matches, so pastes of pastes
work; there is no cycle detection, and a self-referential macro would spin
forever.

Note the ordering consequence: because macros expand into text, the tree parser
below only ever sees ordinary indented rows. That keeps the two concerns
separate at the cost of the substitutions being untyped.

### 3.2 Phase B — rows to tree

The tree is built in **one linear pass over the rows**, maintaining a pointer
`lastnode` to the most recently created node. This is the classic
**indentation-stack** technique used by Python's own tokenizer and by YAML —
except the "stack" is walked through parent pointers rather than held in a
list:

```python
while indentation < lastnode.indentation:   # dedent: pop
    lastnode = lastnode.parent
if indentation > lastnode.indentation:      # indent: push
    lastnode = lastnode.add_child(...)
elif indentation == lastnode.indentation:   # same level: sibling
    lastnode = lastnode.parent.add_child(...)
```

Each row costs O(depth) in the worst case (popping), so the pass is O(rows ×
depth) — in practice O(rows), since real tables are a handful of levels deep.
Memory is one `Node` per row.

Invariant: `lastnode.indentation` is always the indentation of the last bid row
seen, and every node's parent has strictly smaller indentation. Note that
indentation is compared, never *quantised* — 2 spaces then 3 spaces then 5
spaces nests three deep just as 2/4/6 would.

### 3.3 The other half: description continuations

A row that is *not* a new bid can be a continuation of the previous
description, letting a long description wrap, or letting one bid carry several
lines:

```
1C  Weak or Strong Club:
    - 14--15 balanced
    - 16+ unbalanced
  (any) = interference
```

Deciding "new bid vs continuation" is the subtle part, and the original code
got it wrong in a way worth recording. It stored, per node, the *character
column* where the description began (`original_row.find(desc)`) and treated any
later row whose leading whitespace equalled that column as a continuation.

That couples the tree shape to **bid-token width**. `X = takeout` puts the
description at column 8; a genuine child indented 8 spaces then collides with
it and is swallowed as a continuation. Widening the token — `Dbl` instead of
`X`, `(2D)` instead of `2D`, `2!d` instead of `2D` — moved the column and
changed the parse, which is why bugs 2 and 3 looked like "brackets destroy the
indentation" (see `bugs.md`).

The rule is now **shape-based**, not column-based (`_is_bid_row`):

> A row is a continuation when it is not shallower than the current bid and
> does not *look like* a bid row — where "looks like a bid row" means its first
> token is bid-shaped (`_BID_TOKEN_RE`: a bracketed opponent call, a level bid,
> a bare `P`/`X`/`XX`/`Dbl`/…) **or** the row contains a ` = ` assignment.

Note there is no "must be indented" clause: a lettered prose list explaining a
top-level bid sits in the *same* column as the bid it explains.

    3C = artificial, multi-meaning.
    (a) minimum balanced
    (d) void hand, any strength

For the same reason `_looks_like_bid` cannot accept everything that starts with
`(`. A bracketed **single letter** counts as a call only for the call letters
`P X R D`, matched case-sensitively, so `(a)`..`(d)` are list markers while
`(any)`, `(cue)` and `(higher)` remain pseudo-bid rows.

The ` = ` disjunct is what admits pseudo-bids that no bid regex can enumerate —
`any`, `other`, `cue`, `Game`. The bid-shape disjunct is what admits rows with
no `=` at all, the style the bridge repo uses in its summary tables
(`1D  Negative 0--7 hcp`). A bid may also be written as a markdown link,
`[1D](#1C--1D) Negative 0--7`, so `_LINK_BID_RE` unwraps a leading link and
tests its *label*; the label must be a single word, or a description line that
merely opens with a link (`[1N system on](#1NT Opening)`) would be promoted to
a bid.

Continuations are joined into `Node.desc` with a literal two-character `\n`
marker (backslash-n, not a newline), because the desc is later split on that
marker by the back ends; a real newline would be indistinguishable from prose
that happens to wrap.

### 3.4 `Node`, `bidrepr`, and sequences

Each node stores the bid as written (`bid`), the description (`desc`), its
indentation, its children and its parent. Back ends render `bid`; `bidrepr` is
the canonical spelling, built by `bid_representation`: `NT` → `N`, stray
hyphens and semicolons dropped, and a token holding a whole auction split into
its calls, space separated.

That split happens **first**, before anything strips the separators. The old
code stripped the dashes and then re-found calls with a `\d[A-Za-z]+` regex,
which glued bracketed auctions (`(1CD)--1H--2C` → `(1CD)1H2C`), swallowed calls
that do not start with a digit (`1C--P--1H` → `1CP (P) 1H`), dropped trailing
qualifiers (`2N+` → `2N`) and rewrote the alternation `3S/4C` into a two-call
auction.

`(P)` is inserted between the calls only when the token named just our side's
bids — `1H--2C` is `1H (P) 2C`, while `1C--P--1H` and `(1CD)--1H--2C` already
say who called. A single dash is ambiguous (a separator in `1c-2d-3d`, a word
joiner in `new-suit`), so it splits only when every part is call-shaped.

`get_sequence()` walks parent pointers to the root and returns the list of
`bidrepr`s: the auction that reaches this node. That is what the quiz uses to
say "you are in the `1C — 1D — 1H` branch".

---

## 4. HTML back end (`bml2html.py`)

### 4.1 Inline markup: parse into the tree, not over the output

Inline markup is suit symbols (`2D`, `!h`), `*strong*`, `/em/`, `=code=`,
`--`/`---` dashes and `[text](target)` links.

The original implementation built an ElementTree, serialised it with
`ET.tostring`, and then ran ~10 `re.sub`/`str.replace` passes **over the HTML
string**. Every regex then had to avoid matching inside tags (hence patterns
like `[^<>]`), pass order was load-bearing and undocumented, and the
link-vs-suit collision needed a lookahead — `(?![^\(]*\))`, "skip anything
inside parentheses" — that also suppressed suit glyphs in opponent bids, which
was bug 1.

Now a single function turns human text into tree nodes *before* it enters the
document:

```python
parse_inline(text) -> list[str | ET.Element]
```

Two tiers:

- `_INLINE_RE` matches the constructs that can **contain further markup** —
  links, `*strong*`, `/em/`, `=code=`. Each match recurses through `_fill`, so
  a suit symbol inside bold text still renders. Boundary lookbehinds (`(?<!\S)`)
  keep mid-word `/` and `=` (as in "GF/FG", "count = 15") from opening an
  emphasis span.
- `_PLAIN_RE` matches the **non-nesting** character-level things — a digit-led
  suit run, `!x` shorthand, long dashes — and emits `<span class="…color">`
  elements for suits. `2N` becomes `2NT`; `2CH` becomes two glyphs.

`_attach` appends the resulting `str | Element` items following ElementTree's
`text`/`tail` model (text before the first child goes on `parent.text`; text
after a child goes on that child's `tail`) — the one piece of ElementTree API
that reliably confuses people.

Link *targets* are normalised here (`normalise_header_id`: spaces → `_`, suit
letters lowercased) at the moment the `href` is set, so they can never be
mistaken for suit symbols later. The paren lookahead is gone, and `(2D)` gets
its diamond.

### 4.2 Structure

`to_html` walks the IR and appends to the tree: `p`, `h1`–`h4` (with ids from
`normalise_header_id`, collected for the nav list), `ul`, `ol`, and for a
BIDTABLE a call to `html_bidtable`, which recurses over `Node.children`.

Each bid row is `<li><div class="start">BID</div>description…</li>`, with the
alternating `start` / `startOddIndent` class giving each indent level its own
tint; children go in a nested `<ul>` inside the `li`, which `bml.css` indents
by the width of the bid column.

Extra description lines (the `\n` marker of §3.3) are emitted as
`<div class="descRow">` **inside the same `li`, before the children `<ul>`**.
They used to be sibling `<li>`s of the parent's `<ul>`, which put them *after*
the entire child subtree — invisible while multi-line descriptions only
appeared on childless bids, obvious the moment a top-level opening bid used
one. Each contains an empty bid `div`, which reproduces the bid column at every
CSS breakpoint without a new rule.

### 4.3 The other back ends

`bml2latex.py` and `bml2bss.py` read the same IR but predate the inline
pipeline: they still do string-level replacement (`latex_replace_characters`
and friends). They are unaffected by §4.1 and equally unimproved by it. If they
matter to you, they are the obvious next candidates for the same treatment.

---

## 5. Data flow summary

| stage | code | input | output |
|---|---|---|---|
| include/comment | `content_from_file` | file text | text |
| block split | `content_from_file` | text | list of block texts |
| classify | `get_content_type` | block text | `(ContentType, payload)` or None |
| macro expand | `create_bidtree` phase A | block text | block text |
| tree build | `create_bidtree` phase B | rows | `Node` tree |
| inline parse | `parse_inline` | human text | `list[str \| Element]` |
| render | `to_html` / `to_latex` / `to_systemdata` | IR | output document |

---

## 6. Known limits and sharp edges

Things to keep in mind before "fixing" something here.

1. **Classification is heuristic and order-dependent.** A block is whatever the
   first matching guard says. There is no error reporting: a malformed bid
   table silently becomes a paragraph. Any new guard must be inserted at the
   right point in the chain, and measured against the golden snapshots.
2. **The bid-row test is a heuristic too.** `_BID_TOKEN_RE`, `_LINK_BID_RE` and
   the ` = ` fallback cover the corpus, not the space of all inputs. A
   continuation line beginning with a bare word that looks like a bid ("Pass it
   back to partner") will be misread. Prefer ` = ` in tables, and indent
   continuations.
3. **Blank lines end tables.** No warning; the following rows re-root.
4. **Macros are textual.** `#PASTE x 2H=2S` substitutes in descriptions too.
   No cycle detection.
5. **Module globals are the API.** They are reset per file but shared per
   process; concurrent parses in threads would race. The quiz parses serially.
6. **Include resolution is cwd-relative.** Always `chdir` first.
7. **Includes do not nest** (§2.1). One level only, and the failure is silent:
   the inner file's content vanishes.
8. **`bidrepr` is a spelling, not a parse** (§3.4). Use `bmlbids` when you
   need the calls themselves.
9. **`\n` in a description is the literal two characters.** Do not "fix" it to
   a real newline; back ends split on it.

---

## 7. Tests

There are no unit tests of the parser's internals — deliberately. The parser's
contract is "the corpus renders the way it rendered yesterday, except where I
meant to change it", so the safety net is **golden snapshots**:

```
tests/test_bmlbids.py     unit tests, the one module with a clean API (12)
tests/snapshot_parse.py   serialises the parse tree of each corpus document
tests/snapshot_html.py    renders each corpus document to html
tests/test_snapshots.py   runs both under pytest
tests/snapshot_lib.py     shared: state reset, cwd handling, diffing
```

Run everything:

```shell
uv run --with pytest python -m pytest tests
```

Regenerate goldens after an intended change (review the diff first):

```shell
python tests/snapshot_parse.py          # or --check, exits non-zero on drift
python tests/snapshot_html.py
```

The parse snapshot covers `squad-system.bml`, `bidding-system.bml` (the whole
include tree) and `competitive-bidding.bml`; the html snapshot covers this
repo's `example.txt` (every documented markup feature, no external dependency)
and `competitive-bidding.bml` (brackets, deep nesting, macros). Corpus entries
from the bridge repo are **skipped** when it is not checked out — set
`BRIDGE_DIR` if yours lives elsewhere. A failing check writes what it actually
produced to `tests/.snapshot-out/` for inspection.

What a snapshot diff is telling you: a *parse* diff means block classification
or tree shape moved (§2.3, §3); an *html* diff with no parse diff means only
rendering moved (§4).

---

## 8. Extending it

- **New inline markup** — add an alternative to `_PLAIN_RE` (non-nesting) or
  `_INLINE_RE` (can contain markup) in `bml2html.py`, emit the element in
  `_plain`/`parse_inline`. Nothing downstream changes.
- **New block type** — add a `ContentType`, insert a guard in
  `get_content_type` at the right place in the chain, handle it in each back
  end you care about. Back ends ignore types they do not know, so this is
  additive.
- **New back end** — read `bml.content`; the IR is all you need. Use `bmlbids`
  for anything that reasons about the calls themselves rather than the text.
- **Touching `create_bidtree`** — regenerate nothing until the snapshots are
  green or the diff is one you can explain line by line. That is what they are
  for.
