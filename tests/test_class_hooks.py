"""Class hooks: `{.name}` at the end of a heading, a `#BOX` line or a `#+CAPTION:` line.

The hook gives what its line makes those CSS classes and is not part of the text, so anchors and every other
reader of the heading see it without the hook. The Odin port is held to the same output (`html_test.odin`).

    uv run --with pytest pytest tests/test_class_hooks.py
"""

import xml.etree.ElementTree as ET

import bml
import bml2html


def test_the_hook_comes_off_the_text():
    assert bml._class_hook("Defence {.danger .wide}") == ("Defence", "danger wide")
    assert bml._class_hook("Tab\t{ .a.b  .c-d_9 }  ") == ("Tab", "a b c-d_9")
    assert bml._class_hook("{.only}") == ("", "only")
    # the last block of a file keeps its newline
    assert bml._class_hook("Last {.end}\n") == ("Last", "end")


def test_what_is_not_a_hook_stays_text():
    for text in ("Glued{.x}", "Bad {.9x}", "Bad {. x}", "Empty {}", "Not {a hook}", "Two {.a} words"):
        assert bml._class_hook(text) == (text, "")


def test_a_heading_keeps_plain_text_and_carries_its_classes():
    kind, text = bml.get_content_type("** Defence to 1NT {.danger}")
    assert kind == bml.ContentType.H2
    assert text == "Defence to 1NT"
    assert text.classes == "danger"
    _, plain = bml.get_content_type("** Defence to 1NT")
    assert getattr(plain, "classes", "") == ""


def html_of(*content):
    html = bml2html.to_html(list(content))
    return html if isinstance(html, str) else ET.tostring(html, encoding="unicode")


def test_the_classes_reach_the_html():
    html = html_of(
        bml.get_content_type("* Opening {.big}"),
        bml.get_content_type("#BOX tip Systems on {.compact}"),
        (bml.ContentType.PARAGRAPH, "Text."),
        (bml.ContentType.ENDBOX, None),
        bml.get_content_type("#BOX {.plain}"),
        (bml.ContentType.PARAGRAPH, "More."),
        (bml.ContentType.ENDBOX, None),
    )
    assert '<h1 id="Opening" class="big">' in html
    assert '<div class="bml-callout bml-tip compact">' in html
    assert '<div class="bml-box plain">' in html
    assert '<div class="bml-box">' in html  # the callout's own box takes no copy of the classes


def test_a_caption_carries_its_classes():
    assert bml.get_content_type("#+CAPTION: Board 3 {.small}") == (bml.ContentType.CAPTION, "Board 3")
    html = html_of(bml.get_content_type("#+CAPTION: Board 3 {.small}"))
    assert '<div class="bml-caption small">Board 3</div>' in html


def test_own_stylesheets_are_linked_after_bml_css(tmp_path, monkeypatch):
    """`#+CSS: a.css b.css`: the author's stylesheets, linked after bml.css so their rules win."""
    monkeypatch.setitem(bml.meta, "CSS", "mine.css  extra/b.css")
    html = html_of((bml.ContentType.PARAGRAPH, "Text."))
    links = [part.split('href="')[1].split('"')[0] for part in html.split("<link")[1:]]
    assert links[0] == "bml.css"
    assert links[-2:] == ["mine.css", "extra/b.css"]
    assert '<link rel="stylesheet" type="text/css" href="mine.css" />' in html
