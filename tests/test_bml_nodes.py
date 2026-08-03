"""Tests for the parser's own data model (currently `Node.bidrepr`).

    uv run --with pytest pytest tests/test_bml_nodes.py
"""

import bml


def repr_of(bid):
    return bml.Node(bid, "desc", 0).bidrepr


def test_single_call_is_unchanged():
    assert repr_of("1C") == "1C"
    assert repr_of("(2D)") == "(2D)"
    assert repr_of("1NT") == "1N"
    assert repr_of("2N+") == "2N+"  # a trailing qualifier is part of the call


def test_compound_auction_keeps_every_call():
    """`--` separates successive calls. Stripping the dashes first glued them
    into one run, and re-finding calls with a digit-led regex ate any that were
    not digit-led -- `1C--P--1H` used to come back `1CP (P) 1H`."""
    assert repr_of("1C--P--1H") == "1C P 1H"
    assert repr_of("(1CD)--1H--2C") == "(1CD) 1H 2C"
    assert repr_of("(2D)--P--(2H)") == "(2D) P (2H)"
    assert repr_of("Pass--(1H)--X") == "Pass (1H) X"


def test_implicit_opponent_passes_are_shown():
    """When the token names only our calls, the opponents passed in between."""
    assert repr_of("1H--2C") == "1H (P) 2C"
    assert repr_of("1N--2C--2D") == "1N (P) 2C (P) 2D"
    # ...but not when the auction is already spelled out
    assert "(P)" not in repr_of("1C--P--1H")


def test_single_dashes_separate_only_between_calls():
    """A lone dash is ambiguous: a separator in `1c-2d-3d`, a word joiner in
    `new-suit`. It splits only when every part is itself call-shaped."""
    assert repr_of("1c-2d-3d-3s-4c") == "1c (P) 2d (P) 3d (P) 3s (P) 4c"
    assert repr_of("P-1H") == "P 1H"
    assert repr_of("new-suit") == "newsuit"
    assert repr_of("jump-cue") == "jumpcue"
    assert repr_of("2-Cue") == "2Cue"


def test_alternation_is_not_two_calls():
    """`3S/4C` is one call written as a choice; it used to be rewritten into a
    two-call auction, `3S (P) 4C`."""
    assert repr_of("3S/4C") == "3S/4C"
    assert repr_of("2N/3x") == "2N/3x"
    assert repr_of("P/2S") == "P/2S"


def test_link_bids_are_left_whole():
    """A markdown link's target contains its own `--`, so it is never split."""
    assert repr_of("[1D](#1C--1D)") == "[1D](#1C1D)"


def test_get_sequence_uses_the_representation():
    root = bml.Node("root", "root", -1)
    child = root.add_child("1H--2C", "desc", 2, -1)
    grand = child.add_child("2S", "desc", 4, -1)
    assert grand.get_sequence() == ["1H (P) 2C", "2S"]


def bidtree_rows(text):
    """(indentation, bid, desc) for every node of a parsed bid table."""
    rows = []

    def walk(node):
        for child in node.children:
            rows.append((child.indentation, child.bid, child.desc))
            walk(child)

    walk(bml.create_bidtree(text))
    return rows


def test_lettered_prose_list_is_not_a_bid():
    """`(a)`..`(d)` under a bid is an explanation of it, not four calls.

    Both halves matter: a bracketed single letter is only a call for the call
    letters, and a continuation does not have to be indented -- these sit in
    the same column as the bid they belong to.
    """
    rows = bidtree_rows(
        "3C = artificial, multi-meaning.\n"
        "(a) minimum balanced\n"
        "(d) void hand, any strength\n"
        "  3D = relay\n"
    )
    bids = [bid for _, bid, _ in rows]
    assert bids == ["3C", "3D"]
    # the list lines became description continuations of 3C. Continuations are
    # joined with a literal backslash-n marker, not a newline (see bml.py).
    desc = rows[0][2]
    assert desc.startswith("artificial, multi-meaning.")
    assert "(a) minimum balanced" in desc
    assert "(d) void hand, any strength" in desc
    assert desc.count("\\n") == 2


def test_bracketed_calls_and_pseudo_bids_still_nest():
    rows = bidtree_rows("(2D)\n    (P) = they passed\n    (X) = takeout\n    (any) = interference\n")
    assert [bid for _, bid, _ in rows] == ["(2D)", "(P)", "(X)", "(any)"]
