#!/usr/bin/env python
"""Golden-snapshot harness for the bml parser.

Serialises the parse tree of each corpus .bml file into a deterministic text
form, so parser changes show up as a reviewable diff instead of as a surprise
in someone's rendered notes.

We snapshot the *parser* output (bml.content + bid trees) because that is what
the bridge-bidding-system quiz consumes directly; `snapshot_html.py` covers the
rendering side.

Usage:
    python tests/snapshot_parse.py            # regenerate goldens
    python tests/snapshot_parse.py --check    # diff against goldens, non-zero on drift

(`tests/test_snapshots.py` runs the --check half under pytest.)

Corpus lives in the bridge repo; override its location with the BRIDGE_DIR env
var. Missing corpus files are skipped, not failed.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import bml  # noqa: E402
import snapshot_lib  # noqa: E402

GOLDEN_DIR = os.path.join(HERE, "golden")

# label -> filename, resolved inside BRIDGE_DIR.
SOURCES = {
    "squad-system": "squad-system.bml",
    "bidding-system": "bidding-system.bml",
    "competitive-bidding": "competitive-bidding.bml",
}
CORPUS = list(SOURCES)

CONTENT_TYPE_NAMES = {
    v: k for k, v in vars(bml.ContentType).items() if isinstance(v, int)
}


def source_path(label):
    return os.path.join(snapshot_lib.BRIDGE_DIR, SOURCES[label])


def golden_path(label):
    return os.path.join(GOLDEN_DIR, f"{label}.parse.txt")


def walk_node(node, depth, out):
    # skip the synthetic root
    if not (node.bid == "root" and node.desc == "root"):
        seq = node.get_sequence()
        out.append(
            "  " * depth
            + f"[{node.indentation}] bid={node.bid!r} bidrepr={node.bidrepr!r} "
            + f"export={node.export} desc={node.desc!r} seq={seq!r}"
        )
    for child in node.children:
        walk_node(child, depth + 1, out)


def serialize(label):
    content = snapshot_lib.parse_file(source_path(label))
    out = [
        f"# snapshot: {label} ({SOURCES[label]})",
        f"# blocks: {len(content)}",
        "",
    ]
    for i, (ctype, payload) in enumerate(content):
        name = CONTENT_TYPE_NAMES.get(ctype, str(ctype))
        if ctype == bml.ContentType.BIDTABLE:
            out.append(f"{i:04d} BIDTABLE export={payload.export}")
            walk_node(payload, 0, out)
        elif ctype in (
            bml.ContentType.H1,
            bml.ContentType.H2,
            bml.ContentType.H3,
            bml.ContentType.H4,
        ):
            out.append(f"{i:04d} {name} {payload!r}")
        elif ctype in (bml.ContentType.LIST, bml.ContentType.ENUM):
            out.append(f"{i:04d} {name} items={len(payload)} {payload!r}")
        elif ctype == bml.ContentType.PARAGRAPH:
            # paragraphs are prose; keep a length+prefix, not the noise
            text = payload if isinstance(payload, str) else repr(payload)
            out.append(f"{i:04d} PARAGRAPH len={len(text)} {text[:80]!r}")
        else:
            out.append(f"{i:04d} {name} {payload!r}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(snapshot_lib.run_cli(sys.modules[__name__], sys.argv))
