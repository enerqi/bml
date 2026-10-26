#!/usr/bin/env python
"""Golden-snapshot harness for the html renderer.

`snapshot_parse.py` locks down the parse tree; this locks down what
`bml2html.to_html` makes of it -- suit glyphs, inline markup, bid-table
nesting, ids and links. Between them a change to either half of the pipeline
has to be looked at before it lands.

Two corpus entries, deliberately:
- `example` renders the repo's own `example.txt`, which exercises every
  documented markup feature and needs nothing outside this repo.
- `competitive-bidding` renders a real, awkward chapter from the
  bridge-bidding-system repo (opponent bids in brackets, deep nesting,
  PASTE macros) -- skipped when that repo is not checked out.

Usage:
    python tests/snapshot_html.py            # regenerate goldens
    python tests/snapshot_html.py --check    # diff against goldens, non-zero on drift

Output is the renderer's exact string, unformatted: goldens are for diffing,
and reformatting would hide whitespace-significant changes.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import bml  # noqa: E402
import bml2html  # noqa: E402
import snapshot_lib  # noqa: E402

GOLDEN_DIR = os.path.join(HERE, "golden_html")

# label -> source file. Absolute for this repo's own fixture, resolved inside
# BRIDGE_DIR otherwise.
SOURCES = {
    "example": os.path.join(snapshot_lib.REPO, "example.txt"),
    # Deal diagrams, pipe tables, description lists and auctions: rendered to html since 2026-10-26, and
    # used nowhere in the corpus, so this fixture is what holds them (bridge-markup's `just parity` too).
    "constructs": os.path.join(snapshot_lib.REPO, "tests", "constructs.bml"),
    "competitive-bidding": os.path.join(
        snapshot_lib.BRIDGE_DIR, "competitive-bidding.bml"
    ),
}
CORPUS = list(SOURCES)


def source_path(label):
    return SOURCES[label]


def golden_path(label):
    return os.path.join(GOLDEN_DIR, f"{label}.html")


def serialize(label):
    content = snapshot_lib.parse_file(source_path(label))
    html = bml2html.to_html(content)
    return html if html.endswith("\n") else html + "\n"


if __name__ == "__main__":
    sys.exit(snapshot_lib.run_cli(sys.modules[__name__], sys.argv))
