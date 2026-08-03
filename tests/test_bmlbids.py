"""Tests for bmlbids, the shared bid-call model.

    uv run --with pytest pytest tests/test_bmlbids.py
"""

from bmlbids import (
    MINORS,
    MAJORS,
    Bid,
    bid_less_than,
    bid_tokens,
    expand_suit_shorthand,
    is_bid_token,
    parse_call,
    parse_call_alternatives,
    parse_calls,
)


def test_parse_plain_bids():
    assert parse_call("1D") == Bid(1, frozenset("D"), "bid", False)
    assert parse_call("3N") == Bid(3, frozenset("N"), "bid", False)
    assert parse_call("  2h  ") == Bid(2, frozenset("H"), "bid", False)


def test_parse_multi_suit_bids():
    # `1HS` means 1H or 1S -- a real bml form, not a typo
    assert parse_call("1HS") == Bid(1, frozenset("HS"), "bid", False)
    assert parse_call("4CDHS") == Bid(4, frozenset("CDHS"), "bid", False)
    assert parse_call("1HS").suits == MAJORS


def test_suit_class_shorthands():
    # bid tables write `3M` for "3 of a major" -- case is significant here.
    # The class is recorded as well as its members, so a consumer can tie
    # `1HS ... 2M` to one major instead of treating the positions separately.
    assert parse_call("3M") == Bid(3, MAJORS, "bid", False, suit_class="M")
    assert parse_call("3m") == Bid(3, MINORS, "bid", False, suit_class="m")
    assert parse_call("3M") != parse_call("3m")
    assert parse_call("(2M)") == Bid(2, MAJORS, "bid", True, suit_class="M")
    # spelling the denominations out is not a class -- just those suits
    assert parse_call("3HS").suit_class == ""
    assert parse_call("3HS").suits == parse_call("3M").suits
    assert is_bid_token("4M")


def test_shorthand_and_nt():
    assert expand_suit_shorthand("1!h") == "1H"
    assert parse_call("1!h") == parse_call("1H")
    assert parse_call("(1!S)") == Bid(1, frozenset("S"), "bid", True)
    assert parse_call("3NT") == parse_call("3N")


def test_brackets_mean_opponents():
    assert parse_call("(1S)").by_opponent
    assert not parse_call("1S").by_opponent
    assert parse_call("(X)") == Bid(None, frozenset(), "double", True)


def test_non_bid_calls():
    assert parse_call("Pass").kind == "pass"
    assert parse_call("p").kind == "pass"
    assert parse_call("Dbl").kind == "double"
    assert parse_call("XX").kind == "redouble"
    # prose in a bid table survives as 'other' rather than blowing up
    assert parse_call("enquiry").kind == "other"
    assert parse_call("shortage").kind == "other"
    assert parse_call("") is None


def test_is_bid_token():
    assert is_bid_token("1C")
    assert is_bid_token("1HS")  # the case a single-suit regex silently drops
    assert is_bid_token("(2D)")
    assert not is_bid_token("Pass")
    assert not is_bid_token("X")
    assert not is_bid_token("enquiry")


def test_parse_calls_flattens():
    calls = parse_calls(["1C (Pass) 1H", "2D"])
    assert [c.kind for c in calls] == ["bid", "pass", "bid", "bid"]
    assert calls[1].by_opponent


def test_bid_tokens_keeps_multi_suit():
    assert bid_tokens(["2H", "(4H)", "5C"]) == ["2H", "(4H)", "5C"]
    assert bid_tokens(["1H (pass) 2S", "3C"]) == ["1H", "2S", "3C"]
    # the regression this module exists for
    assert bid_tokens(["1C", "1HS"]) == ["1C", "1HS"]


def test_bid_less_than_levels_and_suits():
    assert bid_less_than("1H", "2H")
    assert bid_less_than("(1H)", "2H")
    assert bid_less_than("(1H)", "2C")
    assert not bid_less_than("1N", "1S")
    assert not bid_less_than("7C", "1S")
    assert bid_less_than("7C", "7NT")
    assert not bid_less_than("2H", "2H")


def test_bid_less_than_multi_suit_is_strict():
    # true only when every denomination the token allows ranks below
    assert bid_less_than("1HS", "1N")  # both 1H and 1S precede 1N
    assert bid_less_than("1HS", "2C")
    assert bid_less_than("1CD", "1H")
    assert not bid_less_than("1HS", "1S")  # 1S is not below itself
    assert not bid_less_than("1CDHS", "1H")  # 1S is not below 1H


def test_bid_less_than_ignores_non_bids():
    assert not bid_less_than("Pass", "1H")
    assert not bid_less_than("1H", "X")
    assert not bid_less_than("any", "1H")


def test_wildcard_denomination():
    """`3*` is any denomination at that level -- the same wildcard the quiz's
    filter box already took, now understood by the shared model."""
    assert parse_call("3*") == Bid(3, frozenset("CDHSN"), "bid", False)
    assert parse_call("(3*)").by_opponent
    assert is_bid_token("3*")
    # matching is by suit-set overlap, so a wildcard subsumes any real bid
    assert parse_call("3H").suits <= parse_call("3*").suits


def test_alternation_same_level_collapses():
    """`2D/2H` is one call written two ways -- exactly the multi-suit `2DH`."""
    assert parse_call("2D/2H") == parse_call("2DH")
    assert parse_call("(2D/2H)") == parse_call("(2DH)")
    assert parse_call("3H/3S/3N") == Bid(3, frozenset("HSN"), "bid", False)
    assert parse_call("3H/4m") != parse_call("3H")  # levels differ, cannot merge


def test_alternation_across_levels():
    """`3S/4C` cannot be one Bid; the alternatives stay a list."""
    assert parse_call_alternatives("3S/4C") == [
        Bid(3, frozenset("S"), "bid", False),
        Bid(4, frozenset("C"), "bid", False),
    ]
    # ...but it is still a bid token, so header/context code keeps it
    assert is_bid_token("3S/4C")
    assert is_bid_token("3*/4*")
    assert not is_bid_token("3S/pass")
    # brackets outside the alternation apply to every branch
    assert all(c.by_opponent for c in parse_call_alternatives("(2D/2H)"))


def test_alternation_is_one_position():
    """The point of all this: a slash must not become two calls."""
    assert len(parse_calls(["1N 2S/2N"])) == 2
    assert bid_tokens(["1C--1N/2C"]) == []  # not split into 1N and 2C either
    assert bid_tokens(["1N/2C"]) == ["1N/2C"]


def test_bid_less_than_alternation_is_strict():
    assert bid_less_than("3S/4C", "4N")  # both 3S and 4C precede 4N
    assert not bid_less_than("3S/4C", "4C")  # 4C is not below itself
    assert bid_less_than("1N/2C", "2D")
    assert not bid_less_than("1N/2C", "2C")


def test_x_is_the_same_wildcard_as_star():
    """Bid tables write `3x`, section headers write `3*`; both mean any
    denomination at that level. A bare `X` is still a double -- only a
    level-prefixed one can be the wildcard."""
    assert parse_call("3x") == parse_call("3*")
    assert parse_call("(3x)") == parse_call("(3*)")
    assert parse_call("X").kind == "double"
    assert parse_call("XX").kind == "redouble"
    assert is_bid_token("2x") and not is_bid_token("X")


def test_other_major_variable():
    """`oM` is the major that is *not* the one already shown."""
    om = parse_call("3oM")
    assert om.is_bid and om.level == 3
    assert om.suit_class == "oM" and om.is_other_class
    assert om.class_suits == MAJORS
    assert parse_call("2om").class_suits == MINORS
    assert parse_call("(2oM)").by_opponent
    # unlevelled `oM` still constrains the suit
    assert parse_call("oM").level is None and parse_call("oM").is_other_class
    assert is_bid_token("3oM")


def test_link_bids_are_unwrapped():
    """Summary tables write the call as a link to the section explaining it."""
    assert parse_call("[1D](#1C--1D)") == parse_call("1D")
    assert parse_call("[4CD](#1C--4CD)") == parse_call("4CD")
    assert is_bid_token("[1HS](#1C--1HS)")
    assert bid_tokens(["[2N](#1C--2N)"]) == ["[2N](#1C--2N)"]  # kept as written


def test_any_is_a_call_class_of_its_own():
    """`any` / `(any)` is the interference catch-all: whatever is called here.

    It constrains nothing, so it is not 'other' (which in a bid table means
    "not my sibling rows" and must not match everything).
    """
    assert parse_call("any").kind == "any"
    assert parse_call("(any)").kind == "any"
    assert parse_call("(Any)").kind == "any"
    assert parse_call("(any)").by_opponent
    # it is not a *bid*, so header context and ordering ignore it as before
    assert not is_bid_token("any")
    assert not bid_less_than("any", "1H")
    # `other`/`others` read as catch-alls too, per the system's author
    assert parse_call("other").kind == "any"
    assert parse_call("others").kind == "any"


def test_next_is_the_relay_step():
    """`next` is the cheapest bid above the previous call. The token itself
    only records that; `next_call` does the maths, the consumer with the
    auction in hand does the resolving."""
    from bmlbids import next_call

    assert parse_call("next").kind == "next"
    assert parse_call("(next)").by_opponent
    assert next_call(parse_call("4H")) == parse_call("4S")
    assert next_call(parse_call("4S")) == parse_call("4N")
    assert next_call(parse_call("4N")) == parse_call("5C")
    assert next_call(parse_call("7N")) is None  # the ceiling
    # undefined where the previous call was not one specific bid
    assert next_call(parse_call("4HS")) is None
    assert next_call(parse_call("3x")) is None
    assert next_call(parse_call("any")) is None
    assert next_call(parse_call("Pass")) is None


def test_jump_tokens():
    """A `jump` in these tables is a jump in a *new suit* -- a splinter or jump
    shift -- so the token records how many levels are skipped and the consumer
    with the auction resolves the rest."""
    from bmlbids import cheapest_call

    assert parse_call("jump").kind == "jump"
    assert parse_call("jump").jump_levels == 1
    assert parse_call("Jump").jump_levels == 1
    assert parse_call("jumpNew").jump_levels == 1  # jump already implies new
    assert parse_call("doubleJump").jump_levels == 2
    # not (yet) modelled, and must not be mistaken for a jump
    assert parse_call("nonJump").kind == "other"
    assert parse_call("jumpcue").kind == "other"

    # the cheapest bid available in a suit: same level if it outranks, else up
    assert cheapest_call(parse_call("2S"), "C") == parse_call("3C")
    assert cheapest_call(parse_call("2S"), "N") == parse_call("2N")
    assert cheapest_call(parse_call("2C"), "S") == parse_call("2S")
    assert cheapest_call(parse_call("7S"), "C") is None  # past the ceiling
    assert cheapest_call(parse_call("4HS"), "C") is None  # which of the two?


def test_cue_and_new_tokens():
    """Both name a suit the auction has to supply, so the token carries only
    which rule applies and the level if one was written."""
    assert parse_call("cue").kind == "cue"
    assert parse_call("(cue)").by_opponent
    assert parse_call("3cue") == Bid(3, frozenset(), "cue", False)
    assert parse_call("new").kind == "new"
    assert parse_call("newSuit").kind == "new"  # spelling variant
    assert parse_call("3new") == Bid(3, frozenset(), "new", False)
    # `cueLow`/`cueHi` say *which* of their two suits
    assert parse_call("cueLow").kind == "cuelow"
    assert parse_call("cueHi").kind == "cuehigh"
    assert parse_call("cueHigh").kind == "cuehigh"
    assert parse_call("3cueLow").level == 3


def test_at_least_tokens():
    """`2N+` is 2N or anything higher, enumerated so matching stays a set
    intersection."""
    from bmlbids import calls_at_or_above

    assert parse_call("2N+") == Bid(2, frozenset("N"), "at_least", False)
    assert parse_call("(2N+)").by_opponent
    calls = calls_at_or_above(parse_call("2N+"))
    assert parse_call("2N") in calls
    assert parse_call("3C") in calls
    assert parse_call("2S") not in calls
    assert len(calls) == 26  # 2N, then every call from 3C to 7N
    # `2x+` starts at the bottom of the level
    assert len(calls_at_or_above(parse_call("2x+"))) == 30


def test_weaker_catch_alls():
    """`(overcall)` promises a bid, `(bid)` anything but a pass, `any` and
    `other(s)` nothing at all."""
    assert parse_call("(overcall)").kind == "anybid"
    assert parse_call("(bid)").kind == "anycall"
    assert parse_call("(overcall)").by_opponent
    assert parse_call("any").kind == "any"
    assert parse_call("(other)").kind == "any"


def test_game_and_suit_words():
    from bmlbids import game_calls

    assert parse_call("game").kind == "game"
    assert [(c.level, sorted(c.suits)) for c in game_calls()] == [
        (3, ["N"]),
        (4, ["H"]),
        (4, ["S"]),
        (5, ["C"]),
        (5, ["D"]),
    ]
    # `suit` is the same rule as `new`: a simple new suit, never notrump
    assert parse_call("suit").kind == "new"
    assert parse_call("suit") == parse_call("newSuit")
    assert parse_call("3suit") == parse_call("3new")


def test_level_y_is_a_new_suit_at_that_level():
    """`2Y` is `2new`: the `y` spelling exists to pin the level. `x` is the
    any-suit wildcard, `y` the new-suit one -- two words, not two variables."""
    assert parse_call("2Y") == parse_call("2new")
    assert parse_call("1y") == parse_call("1new")
    assert parse_call("(1y)").by_opponent
    assert parse_call("1x") != parse_call("1y")  # any suit vs a new one
    assert parse_call("1x").kind == "bid"
    # bare `y` says nothing about the level, and does not occur; leave it alone
    assert parse_call("y").kind == "other"


def test_cue_over_token():
    """`CueOver` cues the player on our immediate right, as opposed to `cue`,
    which is any of their suits."""
    assert parse_call("CueOver").kind == "cueover"
    assert parse_call("cueover").kind == "cueover"
    assert parse_call("2CueOver") == Bid(2, frozenset(), "cueover", False)
    assert parse_call("cue").kind == "cue"  # still the general one


def test_step_tokens():
    """Artificial asks are answered up a ladder of steps: `1step` is the
    cheapest call above the ask, `xstep` is "a step response" without saying
    which."""
    from bmlbids import STEP_LIMIT, step_call

    assert parse_call("1step") == Bid(1, frozenset(), "step", False)
    assert parse_call("2step").level == 2
    assert parse_call("xstep").kind == "step"
    assert parse_call("xstep").level is None  # any of them
    assert parse_call("step1") == parse_call("1step")  # both spellings occur
    assert parse_call("step").kind == "other"  # no step number, not a token

    ask = parse_call("4N")
    assert [step_call(ask, n) for n in range(1, 6)] == [
        parse_call(c) for c in ("5C", "5D", "5H", "5S", "5N")
    ]
    assert step_call(parse_call("7N"), 1) is None  # the ceiling
    assert step_call(parse_call("4HS"), 1) is None  # which of the two?
    assert STEP_LIMIT == 5


def test_raise_tokens():
    """`raise` supports the last suit partner bid; `jumpRaise` is a level
    higher; `3raise` names the level."""
    assert parse_call("raise") == Bid(None, frozenset(), "raise", False)
    assert parse_call("jumpRaise").jump_levels == 1
    assert parse_call("JumpRaise").kind == "raise"
    assert parse_call("3raise").level == 3
    assert parse_call("(3raise)").by_opponent
    assert parse_call("raises").kind == "other"  # prose


def test_denomination_without_a_level():
    """`NT`, `m`, `major`, `!c` name a strain and leave the level to the
    auction: a simple, non-jump bid in it."""
    assert parse_call("NT") == Bid(None, frozenset("N"), "strain", False)
    assert parse_call("m").suits == MINORS
    assert parse_call("major").suits == MAJORS
    assert parse_call("Major") == parse_call("major")
    assert parse_call("minor") == parse_call("m")
    assert parse_call("!c").suits == frozenset("C")
    assert parse_call("!c/!d").suits == frozenset("CD")
    # a trailing `+` widens it from the simple bid to any level in that strain
    assert parse_call("!c+").kind == "strainany"
    assert parse_call("m+").suits == MINORS
    assert parse_call("!c").kind == "strain"
    # a bare `D` is the double, not diamonds -- the guard reads the token as
    # written, so `!d` is still diamonds
    assert parse_call("D").kind == "other"
    assert parse_call("!d").suits == frozenset("D")


def test_other_suit_is_new():
    assert parse_call("(otherSuit)").kind == "new"
    assert parse_call("(otherSuit)").by_opponent
    assert parse_call("2otherSuit").level == 2


def test_slam_next_suit_and_fourth_suit_tokens():
    assert parse_call("slam") == Bid(None, frozenset(), "slam", False)
    assert parse_call("6slam").level == 6  # which slam level
    assert parse_call("nextSuit").kind == "nextsuit"
    assert parse_call("4thSuit").kind == "fourthsuit"
    assert parse_call("fourthSuit").kind == "fourthsuit"
