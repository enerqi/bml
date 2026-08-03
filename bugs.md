# usage

this project is used in ~/dev/bridge-bidding-system

the justfile watch recipe for example, which uses doit and dodo.py to trigger html generation from bml files

# testing?

non-initially, but building up a corpus of tests for the html rendering may help the user end to end experience


# bugs?

## 1. (2D) opponent bid does not render the suit symbol (the real bug behind the !x workaround)

e.g. competitive-bidding.bml, "Opponents open Multi 2D" section

The actual want: `(2D)` should show the diamond glyph. It does not - a
parenthesised bid stays literal `(2D)`. `(2!d)` is only a workaround to force
the symbol, and that workaround then trips bug 4 (block -> paragraph), which
is what looked like "indentation breaks".

Root cause in `bml2html.py`: the suit-replace regex (lines 156-167) has a
negative lookahead `(?![^\(]*\))` that skips any `\d[CDHS]` sitting inside
parentheses. It was added to protect markdown link *targets* like
`[text](Multi 2D)` (raw, still uppercase at that point, must stay literal so
`normalise_header_id` can turn it into `href="Multi_2d"`). But it fires for
*every* paren pair, so opponent bids `(2D)` are collaterally suppressed.

Confirmed: `(2D)` in a paragraph -> literal `(2D)`; the same `2D` in a header
or bare text -> renders the diamond.

Fix direction: only skip the link-target case, not all parens. Cleanest is to
run the markdown-link substitution (line 196) *before* the suit replace: once
targets are already `href="multi_2d"` (lowercase, safe) and link text keeps
its uppercase for symbol replacement, the paren lookahead can be dropped and
`(2D)` will render the diamond. Guard against touching text inside `href="..."`.

## 2. X instead of Dbl (meaning double) rendering

same section e.g. competitive-bidding.bml, "Opponents open Multi 2D" section

(2D)
    P = maybe 4 !h and opening values
    Dbl = 4 or 5 poor spades

Flipping to X instead of X tends to break everything under it

## 3. bracketed (opponents bids) can destroy indent

Confirmed. Same root cause as bug 2: the "line-break in a description"
feature is driven by a raw character *column* number, not by tree depth.

In `bml.py`:
- `create_bidtree` line 212 stores `desc_indentation = original_row.find(desc)`
  i.e. the column where the description text begins on that row.
- line 206 then treats any later row whose leading whitespace equals that
  column as a *continuation* of the previous description (adds `\n`), NOT as
  a new/child bid.

A bid's *token width* decides that column. Bracketing widens the token
(`2D` -> `(2D)`, +2 chars), so the description column moves. When a child
row's indentation happens to line up with a parent's shifted description
column, the child is swallowed as a description line-break instead of
nesting.

Minimal repro (child rows collapse into empty-bid desc rows):

    (2D)
        X = takeout
            P = pass it        <- indent 8 == column of "takeout" (col 8)
            2H = my hearts         so both become desc-continuations of X

`X` (1 char) puts "takeout" at column 8, colliding with the 8-space child
indent. `Dbl` (3 chars) pushes it to column 10, no collision, nests fine.
So the trigger is bid-token length lining up with child indentation - any
change that shifts columns (brackets, `!x` suits, `X` vs `Dbl`) can set it
off or fix it by accident.

Fix direction: nest by indentation depth relative to the parent bid, and
mark description continuations explicitly (e.g. require them to be indented
*past* the bid, tracked separately) rather than matching an absolute column.

## 4. suit shorthand / non-digit first bid token downgrades whole block to a paragraph

This is the real mechanism behind bug 1 (it is NOT an indentation bug).

`get_content_type` in `bml.py` line 288 decides a block is a bidtable with:

    re.match(r"^\s*\(?\d[A-Za-z]+", text)

The first bid token must be optional `(`, a digit, then a *letter*. So:
- `(2!d)` -> after `2` comes `!`, not a letter -> no match
- `(2D)`  -> matches (`2D`)

When the first line fails this test the block falls through all the other
content-type checks and is emitted as a `<p>` PARAGRAPH: the bid tree is
never built, so every row (`P = ...`, `Dbl = ...`) is flattened into one
paragraph with literal `=` signs and lost indentation. That is why bug 1
looks like "indentation breaks" - the table stopped being a table.

Repro: `(2!d)` as the first row renders as a paragraph; `(2D)` renders as a
bidtable. (An `X` or any non-`\d[A-Za-z]` first token trips the same check.)

Note the `!x` suit shorthand only expands late, as a plain string replace on
the final HTML (`bml2html.py` lines 181-184), so it works *inside*
descriptions and non-first bids but cannot appear in the first bid token
that gates bidtable detection.

Fix direction: extend the detection regex (line 288) to accept `!`-suit
shorthand and bare doubles (`X`, `Dbl`) as a leading bid token, or normalise
`!x` -> a letter before content-type detection.

(Note: `(2D)--P--(2!h)` may be ok because the *first* token `(2D)` passes the
detection regex; `(2!d)--P--(2!h)` would break since the first token fails it.)

## 5. nesting of complex trees often mis renders without correct indentation

same section e.g. competitive-bidding.bml, "Opponents open Multi 2D" section

trying to inline the section `(2D)--P--(2!h)--P--(2!s)` into the main `(2D)` tree would break
