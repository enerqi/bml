"""Shared plumbing for the golden-snapshot harnesses.

Both harnesses (`snapshot_parse` = parser trees, `snapshot_html` = rendered
html) follow the same shape:

    serialize(label) -> str        deterministic text for one corpus entry
    check(label) -> str | None     None if it matches its golden, else a diff

Corpus files come partly from the bridge-bidding-system repo, which may not be
present; callers skip those entries rather than fail.
"""

import difflib
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Where a failing check drops what it actually produced, for eyeballing.
# Gitignored; never read back by the harness.
OUT_DIR = os.path.join(HERE, ".snapshot-out")

BRIDGE_DIR = os.environ.get(
    "BRIDGE_DIR",
    os.path.expanduser(r"~/docs/bridge/bridge-bidding-system"),
)

# How many diff lines a failure message carries. The corpus files are thousands
# of lines; the whole diff is unreadable in a pytest report and the full text is
# written to OUT_DIR anyway.
DIFF_LIMIT = 40


def reset_parser_state():
    """Clear bml's module globals.

    `bml.content_from_file` resets them itself now, but the harness must not
    depend on that: it is exactly the behaviour a snapshot is meant to catch
    regressing.
    """
    from collections import defaultdict

    import bml

    bml.content = []
    bml.clipboard = {}
    bml.vulnerability = "00"
    bml.seat = "0"
    bml.meta = defaultdict(str)


def parse_file(path):
    """Parse one .bml file with cwd set to its directory so #INCLUDE resolves.

    Returns bml.content. Restores the previous cwd.
    """
    import bml

    reset_parser_state()
    prev = os.getcwd()
    os.chdir(os.path.dirname(os.path.abspath(path)))
    try:
        return bml.content_from_file(os.path.basename(path))
    finally:
        os.chdir(prev)


def compare(label, actual, golden_path):
    """None when `actual` matches the golden file, else a truncated diff.

    A missing golden is drift too -- goldens are regenerated deliberately, not
    conjured by the first run that notices them absent.
    """
    if not os.path.exists(golden_path):
        _write_actual(label, golden_path, actual)
        return f"{label}: no golden at {golden_path} (run the harness without --check)"
    with open(golden_path, "r", encoding="utf-8") as f:
        expected = f.read()
    if expected == actual:
        return None
    _write_actual(label, golden_path, actual)
    diff = list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            "golden",
            "actual",
            lineterm="",
            n=1,
        )
    )
    head = "\n".join(diff[:DIFF_LIMIT])
    if len(diff) > DIFF_LIMIT:
        head += f"\n... {len(diff) - DIFF_LIMIT} more diff lines"
    return f"{label}: {len(diff)} diff lines\n{head}"


def _write_actual(label, golden_path, actual):
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, os.path.basename(golden_path))
    with open(out, "w", encoding="utf-8") as f:
        f.write(actual)


def run_cli(harness, argv):
    """Shared `--check` / regenerate entry point.

    `harness` supplies CORPUS (list of labels), source_path(label),
    golden_path(label) and serialize(label).
    """
    check = "--check" in argv
    drift = []
    for label in harness.CORPUS:
        source = harness.source_path(label)
        if not os.path.exists(source):
            print(f"SKIP  {label}: {source} not found")
            continue
        text = harness.serialize(label)
        golden = harness.golden_path(label)
        if check:
            problem = compare(label, text, golden)
            if problem:
                print(f"DRIFT {problem}")
                drift.append(label)
            else:
                print(f"OK    {label}")
        else:
            os.makedirs(os.path.dirname(golden), exist_ok=True)
            with open(golden, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"wrote {golden} ({text.count(chr(10))} lines)")
    return 1 if drift else 0
