"""Runs the golden snapshots under pytest.

    uv run --with pytest python -m pytest tests

A failure means the parser or the renderer changed its output. Read the diff in
the failure message (the whole output is dropped in `tests/.snapshot-out/`); if
the change is intended, regenerate:

    python tests/snapshot_parse.py
    python tests/snapshot_html.py

Corpus entries that live in the bridge-bidding-system repo are skipped when it
is not checked out, so this suite still passes standalone.
"""

import os

import pytest

import snapshot_html
import snapshot_lib
import snapshot_parse


def _run(harness, label):
    source = harness.source_path(label)
    if not os.path.exists(source):
        pytest.skip(f"corpus file not available: {source}")
    problem = snapshot_lib.compare(
        label, harness.serialize(label), harness.golden_path(label)
    )
    if problem:
        pytest.fail(problem, pytrace=False)


@pytest.mark.parametrize("label", snapshot_parse.CORPUS)
def test_parse_snapshot(label):
    _run(snapshot_parse, label)


@pytest.mark.parametrize("label", snapshot_html.CORPUS)
def test_html_snapshot(label):
    _run(snapshot_html, label)
