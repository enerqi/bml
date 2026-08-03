"""Canonical model of a bridge call, shared by the bml tools and consumers.

`bml.py` parses the *markup*; this parses the *bids* inside it. Both the html
renderers and the bridge-bidding-system quiz were growing their own regexes
for "is this token a bid", "what suit is it", "which of these came first" --
subtly different each time, which is how multi-suit bids like `1HS` ended up
being silently dropped in one place and honoured in another. This is the one
place that knows.

A call is written the way bml writes it:

    1D 2H 3N      a bid: level 1-7 plus one or more suits, C D H S N
    1HS           a multi-suit bid, meaning "1H or 1S"
    2M 3m         suit classes: M = a major {H,S}, m = a minor {C,D}. These
                  are the one place case is significant
    3* 3x         any denomination at that level (tables write `x`, section
                  headers `*`; a bare `X` is still a double)
    oM 3oM        "the other major" -- a variable, resolved against an earlier
                  call in the same auction. `om` is its minor twin
    [1D](#1C--1D) a call written as a markdown link to its own section
    1!h           `!x` suit shorthand, as used in .bml source
    1NT           `NT` is accepted and normalised to `N`
    Pass P        pass
    X Dbl         double        XX Rdbl R    redouble
    (1S) (X)      brackets mean the opponents made this call
    2D/2H 3S/4C   alternatives at ONE position: "2D or 2H". Same-level
                  alternatives collapse into one multi-suit Bid (2D/2H is
                  2DH); use `parse_call_alternatives` for the general case

Anything else that appears in a bid table -- `any`, `cue`, `new`, `Game`,
`others` -- parses as kind 'other' rather than failing, so a sequence
containing one can still be handled as a whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable, Optional

SUITS = "CDHSN"
MAJORS = frozenset("HS")
MINORS = frozenset("CD")

# Ordering of the denominations within a level; notrump is highest.
SUIT_RANK = {"C": 1, "D": 2, "H": 3, "S": 4, "N": 5}

# A bid is a level then one or more denominations. The multi-suit form (`1HS`,
# `4CDHS`) is a real thing in bml tables and headers, not a typo -- it means
# "any of these", so it must be matched here rather than by a single-suit
# regex that quietly rejects it. `M`/`m` are the major/minor shorthands, and
# `*` is "any denomination" (the same wildcard the quiz's filter box takes).
BID_RE = re.compile(r"^\(?([1-7])([CDHSNMm*]+)\)?$")
_SHORTHAND_RE = re.compile(r"!([cdhsCDHS])")

# `3*` and `3x` both mean "any denomination at the 3 level". A bare `X` is a
# double, but a level-prefixed one cannot be, so the two spellings are safe to
# accept together -- the corpus writes `x` inside bid tables and `*` in the
# newer section headers.
_LEVEL_WILDCARD_RE = re.compile(r"^([1-7])[X*]$")

# `oM` is "the other major" -- the major that is not the one already agreed or
# shown. `om` is the same for minors. This is a *variable*, not a suit set: its
# value depends on an earlier call in the same auction, so the token carries
# the class it ranges over and consumers resolve it (see `suit_class`).
_OTHER_CLASS_RE = re.compile(r"^([1-7])?O([Mm])$")
_CLASS_SUITS = {"M": MAJORS, "m": MINORS}

# A bid may be written as a markdown link in the .bml source, e.g.
# `[1D](#1C--1D) Negative 0--7`, where the label carries the call.
_LINK_RE = re.compile(r"^\[([^\]\s]+)\]\([^)]*\)$")

# `/` separates alternatives at a single position: `2D/2H` is one call, not
# two. Splitting it into two positions invents an auction that was never bid.
ALT_SEP = "/"

PASS_TOKENS = frozenset({"P", "PASS"})
DOUBLE_TOKENS = frozenset({"X", "DBL"})
REDOUBLE_TOKENS = frozenset({"XX", "RDBL", "R"})

# `any` / `(any)` is a row covering whatever is called at this point -- the
# interference catch-all. Unlike the other pseudo-bid words it constrains
# nothing at all, so it is a kind of its own rather than 'other': a consumer
# matching auctions can let it stand for any call, where 'other' (which means
# "not my sibling rows") must not.
ANY_TOKENS = frozenset({"ANY"})

# Weaker catch-alls than `any`, and the author's readings of them:
#   `(overcall)` -- any *bid*, so not a pass and not a double
#   `(higher)`   -- the same: "they bid something", with the calls worth naming
#                   broken out into their own sections. Where it really does
#                   mean "above such-and-such" the bound is better written as
#                   `(2C+)`, which resolves exactly
#   `(bid)`      -- the same but doubles count too; anything except a pass
#   `other(s)`   -- another catch-all, not a statement about sibling rows
ANY_BID_TOKENS = frozenset({"OVERCALL", "HIGHER"})
ANY_CALL_TOKENS = frozenset({"BID"})
OTHER_TOKENS = frozenset({"OTHER", "OTHERS"})

# `game` is a game contract: 3N or a major game or a minor game.
GAME_TOKENS = frozenset({"GAME"})
GAME_CALLS = ("3N", "4H", "4S", "5C", "5D")

# `next` is the cheapest bid above the call before it -- the relay step. Like
# `oM` it is a variable, but one over the *auction* rather than a suit class,
# so it parses to its own kind and a consumer with the prefix in hand resolves
# it (see `next_call`).
NEXT_TOKENS = frozenset({"NEXT"})

# A `jump` in these tables is a jump *in a new suit* -- a splinter or a jump
# shift, one level above the cheapest bid available in that suit. It is never a
# jump to notrump. `doubleJump` is the same, one level higher again. The value
# is how many levels are skipped; resolution needs the auction (see
# `cheapest_call` and `bidfilter.resolve_relative`).
JUMP_TOKENS = {"JUMP": 1, "JUMPNEW": 1, "NEWJUMP": 1, "DOUBLEJUMP": 2}

# `cue` is a bid in a suit the opponents bid; unqualified it is the lowest such
# bid available, `3cue` names the level. `new` is a suit nobody has bid --
# neither side -- at the cheapest level, `3new` at the level named. Both need
# the auction, so the token records only which it is and any level.
_CUE_RE = re.compile(r"^([1-7])?CUE$")

# `CueOver` cues the player on your immediate *right* -- the last opponent to
# call -- as opposed to `cue`, which is any of their suits. It is the "sitting
# over" half of a pair: in `(1C)--P--(1x)`, cueing the 1C is "sitting under"
# and cueing the `1x` is `CueOver`.
_CUE_OVER_RE = re.compile(r"^([1-7])?CUEOVER$")

# `cueLow` / `cueHi` (or `cueHigh`) name *which* of the opponents' two suits to
# cue: the lower- or higher-ranking one. Only resolvable when the auction
# actually shows two of their suits -- over a conventional two-suited bid the
# pair is system knowledge, not something the calls record.
_CUE_PICK_RE = re.compile(r"^([1-7])?CUE(LOW|HI|HIGH)$")

# Step responses to an artificial ask: `1step` is the cheapest call above it,
# `2step` the one after that. `xstep` is "whatever the scheme's steps are" --
# an unknown number of them, as in keycard responses. STEP_LIMIT is how many
# `xstep` stands for; the corpus's own EKB rows describe five ("5th step = 2 KC
# + a void") before leaving the ladder with `6x`.
# Both spellings occur: `1step` and `step1`.
_STEP_RE = re.compile(r"^(?:([1-7]|X)STEP|STEP([1-7]))$")

# `raise` supports the last suit partner bid -- the cheapest bid in it, since a
# raise has to be legal over whatever came before. `jumpRaise` is one level
# higher, and `3raise` names the level outright.
_RAISE_RE = re.compile(r"^([1-7])?(JUMP)?RAISE$")

# `slam` bids the agreed suit -- the last one our side named -- at the slam
# level, 6 or 7. `6slam` says which.
_SLAM_RE = re.compile(r"^([1-7])?SLAM$")
SLAM_LEVELS = (6, 7)

# `nextSuit` is the next bid up that is a *suit*: the cheapest call above,
# skipping notrump. `4thSuit` is fourth-suit-forcing -- the one suit still
# unbid, which only means anything when exactly one is left.
NEXT_SUIT_TOKENS = frozenset({"NEXTSUIT"})
FOURTH_SUIT_TOKENS = frozenset({"4THSUIT", "FOURTHSUIT"})

# A denomination with no level -- `NT`, `m`, `major`, `!c/!d` -- is a *simple*
# bid in that strain: non-jump, at whatever level the auction has reached. The
# level is not fixed in advance, which is why the tables write it this way.
# A bare `D` is excluded: it is the double, not diamonds.
# A trailing `+` widens it to *any* level in that strain rather than the
# cheapest -- `!c+` is "clubs, at whatever level it takes", the pass/correct
# sense, where a bare `!c` is the simple bid.
_STRAIN_RE = re.compile(r"^(?:MAJORS?|MINORS?|([CDHSNMm]+))(\+)?$")
STEP_LIMIT = 5
# `suit` is the author's word for the same thing as `new`: a simple new-suit
# bid by us, no notrump, no double, no pass. `newSuit`/`new-suit` likewise, and
# so is `2Y` -- the `y` spelling exists to pin the level, so `2Y` is `2new`.
# (`x` is the *any suit* wildcard, `y` the new-suit one; they are different
# words, not a pair of distinct variables.)
# `(otherSuit)` is the same rule again, said from the opponents' side: a new
# suit, not the one their partner bid, and not notrump.
_NEW_RE = re.compile(r"^(?:([1-7])?(?:NEW(?:SUIT)?|SUIT|OTHERSUIT)|([1-7])Y)$")

# `2N+` is "2N or anything higher". The bound is written out, so this one needs
# nothing from the auction -- just every call at or above it.
_AT_LEAST_RE = re.compile(r"^([1-7])([CDHSNMm*X])\+$")


@dataclass(frozen=True)
class Bid:
    """One call. `suits` holds every denomination the token allows, so a plain
    `1H` is `{H}` and a multi-suit `1HS` is `{H, S}`."""

    level: Optional[int]  # 1..7, or None for pass/double/redouble/other
    suits: frozenset  # subset of C D H S N; empty unless kind == 'bid'
    kind: str  # 'bid'|'pass'|'double'|'redouble'|'any'|'anybid'|'anycall'|'other'|…
    by_opponent: bool
    # Which suit *class* the token named, when it named one rather than listing
    # denominations: 'M'/'m' for a major/minor, 'oM'/'om' for "the other" one.
    # `suits` still holds the class's full membership, so a consumer that does
    # not resolve variables keeps matching as before; one that does (the quiz's
    # filter) can tie `1HS ... 2M` to the same major and `2oM` to the other.
    suit_class: str = ""
    # For kind 'jump': how many levels above the cheapest available bid.
    jump_levels: int = 0

    @property
    def is_bid(self) -> bool:
        return self.kind == "bid"

    @property
    def is_other_class(self) -> bool:
        """`oM`/`om`: the complement, within its class, of the bound suit."""
        return self.suit_class in ("oM", "om")

    @property
    def class_suits(self) -> frozenset:
        """The denominations this token's class ranges over ({H,S} for any of
        M/oM), or its own suits when it named no class."""
        return _CLASS_SUITS.get(self.suit_class.lstrip("o"), self.suits)


def expand_suit_shorthand(text: str) -> str:
    """`!h` / `!H` -> `H`, the shorthand used in .bml source."""
    return _SHORTHAND_RE.sub(lambda m: m.group(1).upper(), text)


def fold_call_case(token: str) -> str:
    """Uppercase a token, keeping a lowercase `m` (minors) distinct from `M`
    (majors). Everything else is a suit letter or a keyword, for which case
    carries no meaning."""
    return "".join(ch if ch == "m" else ch.upper() for ch in token)


def normalise_call_token(token: str) -> str:
    """Case-fold a token and reduce `NT` to `N`, without touching brackets."""
    return fold_call_case(expand_suit_shorthand(token.strip())).replace("NT", "N")


def _expand_denominations(text: str) -> frozenset:
    """`HS` -> {H,S}; `M` -> majors; `m` -> minors; `*` -> every denomination."""
    suits: set = set()
    for ch in text:
        if ch == "M":
            suits |= MAJORS
        elif ch == "m":
            suits |= MINORS
        elif ch == "*":
            suits |= set(SUITS)
        else:
            suits.add(ch)
    return frozenset(suits)


def strip_brackets(token: str) -> tuple[str, bool]:
    """Split `(1S)` into its inner text and "was it the opponents'"."""
    return token.strip("()"), token.strip().startswith("(")


def unwrap_link(token: str) -> str:
    """`[1D](#1C--1D)` -> `1D`. Bid tables may write a call as a markdown link
    to the section that explains it; the label is the call."""
    match = _LINK_RE.match(token.strip())
    return match.group(1) if match else token


def parse_call_alternatives(token: str) -> list[Bid]:
    """Parse one auction position into the calls it allows.

    `2D/2H` is *one* call written as two possibilities, and `(2D/2H)` is the
    opponents making it; the brackets may wrap the whole alternation or each
    alternative. A token without `/` yields a single-element list.
    """
    if not token or not token.strip():
        return []
    outer, outer_opp = strip_brackets(unwrap_link(token.strip()))
    parts = [p for p in outer.split(ALT_SEP) if p.strip()]
    if not parts:
        return []
    calls = []
    for part in parts:
        call = parse_call(part)
        if call is None:
            continue
        # brackets around the whole alternation apply to every alternative
        calls.append(replace(call, by_opponent=call.by_opponent or outer_opp))
    return calls


def _merge_alternatives(calls: list[Bid]) -> Optional[Bid]:
    """One Bid covering every alternative, when they differ only in suit.

    `2D/2H` is exactly `2DH`, so the common case needs no new type and every
    existing consumer keeps working. Alternatives spanning levels (`3S/4C`) or
    kinds cannot be one Bid -- callers wanting those must use
    `parse_call_alternatives`.
    """
    if not calls:
        return None
    first = calls[0]
    if any(
        c.level != first.level
        or c.kind != first.kind
        or c.by_opponent != first.by_opponent
        for c in calls[1:]
    ):
        return None
    suits = frozenset().union(*(c.suits for c in calls))
    return replace(first, suits=suits)


def parse_call(token: str) -> Optional[Bid]:
    """Parse a single call. None only for empty input; unrecognised tokens
    come back as kind 'other' so they can be carried along rather than
    crashing a sequence.

    An alternation whose branches differ only in suit (`2D/2H`) collapses to
    the equivalent multi-suit bid; one spanning levels (`3S/4C`) is 'other'
    here, since a single Bid cannot say it -- see `parse_call_alternatives`.
    """
    if not token or not token.strip():
        return None
    token = unwrap_link(token.strip())
    if ALT_SEP in token:
        merged = _merge_alternatives(parse_call_alternatives(token))
        if merged is not None:
            return merged
        _, opp = strip_brackets(token)
        return Bid(None, frozenset(), "other", opp)
    inner, opp = strip_brackets(normalise_call_token(token))
    if inner in PASS_TOKENS:
        return Bid(None, frozenset(), "pass", opp)
    if inner in DOUBLE_TOKENS:
        return Bid(None, frozenset(), "double", opp)
    if inner in REDOUBLE_TOKENS:
        return Bid(None, frozenset(), "redouble", opp)
    if inner in ANY_TOKENS or inner.upper() in OTHER_TOKENS:
        return Bid(None, frozenset(), "any", opp)
    if inner.upper() in ANY_BID_TOKENS:
        return Bid(None, frozenset(), "anybid", opp)
    if inner.upper() in ANY_CALL_TOKENS:
        return Bid(None, frozenset(), "anycall", opp)
    if inner.upper() in GAME_TOKENS:
        return Bid(None, frozenset(), "game", opp)
    if inner in NEXT_TOKENS:
        return Bid(None, frozenset(), "next", opp)
    if inner.upper() in JUMP_TOKENS:
        return Bid(
            None, frozenset(), "jump", opp, jump_levels=JUMP_TOKENS[inner.upper()]
        )
    # `!d` is diamonds, but a bare `D` written on its own is the double, so the
    # guard looks at the token as written rather than after shorthand expansion
    raw_inner, _ = strip_brackets(token.strip())
    m = _STRAIN_RE.match(inner if _STRAIN_RE.match(inner) else inner.upper())
    if m and raw_inner.upper() != "D":
        word = inner.upper()
        if word.startswith("MAJOR"):
            suits = MAJORS
        elif word.startswith("MINOR"):
            suits = MINORS
        else:
            suits = _expand_denominations(m.group(1))
        return Bid(None, suits, "strainany" if m.group(2) else "strain", opp)
    word = inner.upper()
    m = _SLAM_RE.match(word)
    if m:
        return Bid(
            int(m.group(1)) if m.group(1) else None, frozenset(), "slam", opp
        )
    if word in NEXT_SUIT_TOKENS:
        return Bid(None, frozenset(), "nextsuit", opp)
    if word in FOURTH_SUIT_TOKENS:
        return Bid(None, frozenset(), "fourthsuit", opp)
    m = _RAISE_RE.match(inner.upper())
    if m:
        return Bid(
            int(m.group(1)) if m.group(1) else None,
            frozenset(),
            "raise",
            opp,
            jump_levels=1 if m.group(2) else 0,
        )
    m = _STEP_RE.match(inner.upper())
    if m:
        # level carries which step; None means "any of them"
        which = m.group(1) or m.group(2)
        step = None if which == "X" else int(which)
        return Bid(step, frozenset(), "step", opp)
    m = _CUE_PICK_RE.match(inner.upper())
    if m:
        kind = "cuelow" if m.group(2) == "LOW" else "cuehigh"
        return Bid(int(m.group(1)) if m.group(1) else None, frozenset(), kind, opp)
    m = _CUE_OVER_RE.match(inner.upper())
    if m:
        return Bid(
            int(m.group(1)) if m.group(1) else None, frozenset(), "cueover", opp
        )
    m = _CUE_RE.match(inner.upper())
    if m:
        return Bid(int(m.group(1)) if m.group(1) else None, frozenset(), "cue", opp)
    m = _NEW_RE.match(inner.upper())
    if m:
        level = m.group(1) or m.group(2)
        return Bid(int(level) if level else None, frozenset(), "new", opp)
    m = _AT_LEAST_RE.match(inner)
    if m:
        # `2N+` -- every call from 2N up. Enumerated rather than stored as a
        # bound, so matching stays a plain set intersection.
        return Bid(
            int(m.group(1)),
            _expand_denominations(m.group(2).replace("X", "*")),
            "at_least",
            opp,
        )
    m = _LEVEL_WILDCARD_RE.match(inner)
    if m:
        return Bid(int(m.group(1)), frozenset(SUITS), "bid", opp)
    m = _OTHER_CLASS_RE.match(inner)
    if m:
        # `oM` with no level ("the other major, at whatever level") keeps
        # level None: it still constrains the suit, which is what it is for.
        level = int(m.group(1)) if m.group(1) else None
        klass = m.group(2)
        return Bid(level, _CLASS_SUITS[klass], "bid", opp, suit_class="o" + klass)
    m = BID_RE.match(inner)
    if m:
        denominations = m.group(2)
        suit_class = denominations if denominations in ("M", "m") else ""
        return Bid(
            int(m.group(1)),
            _expand_denominations(denominations),
            "bid",
            opp,
            suit_class=suit_class,
        )
    return Bid(None, frozenset(), "other", opp)


def parse_calls(sequence: Iterable[str]) -> list[Bid]:
    """Flatten strings that may each hold several space-separated calls
    (`'1C (Pass) 1H'`) into one list of calls."""
    calls: list[Bid] = []
    for element in sequence:
        for token in str(element).split():
            call = parse_call(token)
            if call is not None:
                calls.append(call)
    return calls


def is_bid_token(token: str) -> bool:
    """True for a real bid (not pass/double/prose). Multi-suit counts, and so
    does an alternation of bids -- including `3S/4C`, which spans levels and so
    has no single-Bid form to ask `is_bid` of."""
    calls = parse_call_alternatives(token)
    return bool(calls) and all(c.is_bid for c in calls)


def bid_tokens(strings: Iterable[str]) -> list[str]:
    """The bid tokens, as written, out of strings that may hold several calls.

    Non-bids (pass, double, prose) are dropped. Multi-suit bids are kept --
    dropping them is what loses section context like `1C--1HS`.
    """
    return [tok for s in strings for tok in str(s).split() if is_bid_token(tok)]


def _level_and_suits(bid: str | Bid) -> Optional[tuple[int, frozenset]]:
    call = bid if isinstance(bid, Bid) else parse_call(bid)
    if call is None or not call.is_bid or call.level is None:
        return None
    return call.level, call.suits


def next_call(bid: Bid) -> Optional[Bid]:
    """The cheapest bid above `bid` -- one denomination up, or the next level
    starting at clubs when `bid` was notrump.

    Only defined for a bid naming one denomination: after a token that could be
    several calls (`4HS`, `3x`, `any`) there is no single next step, and the
    caller should ask per alternative or give up rather than guess. Returns
    None at the ceiling (7N) and for non-bids.
    """
    if not bid.is_bid or bid.level is None or len(bid.suits) != 1:
        return None
    rank = SUIT_RANK[next(iter(bid.suits))]
    if rank == SUIT_RANK["N"]:
        if bid.level == 7:
            return None
        return replace(bid, level=bid.level + 1, suits=frozenset("C"), suit_class="")
    higher = next(s for s, r in SUIT_RANK.items() if r == rank + 1)
    return replace(bid, suits=frozenset(higher), suit_class="")


def step_call(previous: Bid, steps: int) -> Optional[Bid]:
    """The `steps`-th call above `previous`, counting the cheapest as step 1.

    This is the ladder artificial asks answer on: over a 4N keycard ask, 5C is
    step 1, 5D step 2, and so on. None past the ceiling, or when `previous` is
    not one specific bid.
    """
    call = previous
    for _ in range(steps):
        call = next_call(call)
        if call is None:
            return None
    return call


def cheapest_call(previous: Bid, suit: str) -> Optional[Bid]:
    """The lowest bid in `suit` that is legal over `previous`.

    Same level when `suit` outranks the previous denomination, one level up
    otherwise. Returns None past 7, or when `previous` is not one specific bid
    (`4HS` could be either, and each answer differs).
    """
    if not previous.is_bid or previous.level is None or len(previous.suits) != 1:
        return None
    was = SUIT_RANK[next(iter(previous.suits))]
    level = previous.level if SUIT_RANK[suit] > was else previous.level + 1
    if level > 7:
        return None
    return replace(
        previous, level=level, suits=frozenset({suit}), suit_class="", jump_levels=0
    )


def game_calls(by_opponent: bool = False) -> list[Bid]:
    """The game contracts: 3N, 4H, 4S, 5C, 5D."""
    return [
        replace(parse_call(text), by_opponent=by_opponent) for text in GAME_CALLS
    ]


def calls_at_or_above(bid: Bid) -> list[Bid]:
    """Every bid from `bid` upwards, for an `at_least` token like `2N+`.

    `2N+` is 2N or higher; `2x+` is any 2-level bid or higher. Enumerating them
    keeps matching a set intersection instead of needing a comparison operator
    in the pattern language.
    """
    if bid.level is None or not bid.suits:
        return []
    floor = min(SUIT_RANK[s] for s in bid.suits)
    calls = []
    for level in range(bid.level, 8):
        for suit, rank in SUIT_RANK.items():
            if level == bid.level and rank < floor:
                continue
            calls.append(Bid(level, frozenset({suit}), "bid", bid.by_opponent))
    return calls


def _one_less_than(b1: str | Bid, b2: str | Bid) -> bool:
    a, b = _level_and_suits(b1), _level_and_suits(b2)
    if a is None or b is None:
        return False
    (level1, suits1), (level2, suits2) = a, b
    if level1 != level2:
        return level1 < level2
    # same level: every denomination b1 could be must rank below every one b2
    # could be, so compare b1's highest against b2's lowest
    highest1 = max(SUIT_RANK.get(s, 5) for s in suits1)
    lowest2 = min(SUIT_RANK.get(s, 5) for s in suits2)
    return highest1 < lowest2


def _alternatives_of(bid: str | Bid) -> list[Bid]:
    return [bid] if isinstance(bid, Bid) else parse_call_alternatives(bid)


def bid_less_than(b1: str | Bid, b2: str | Bid) -> bool:
    """Does `b1` rank strictly below `b2` in the auction?

    Strict throughout, because the caller is asking "did this call have to come
    first?" and a wrong yes invents an auction. A multi-suit bid is below
    another call only when *every* denomination it allows is (`1HS` is below
    `1N` because both 1H and 1S are), and an alternation is below only when
    every branch is (`3S/4C` is below `4N`, but not below `4C`). Non-bids
    compare as False.
    """
    left, right = _alternatives_of(b1), _alternatives_of(b2)
    if not left or not right:
        return False
    return all(_one_less_than(x, y) for x in left for y in right)
