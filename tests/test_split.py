from __future__ import annotations

import re

import pytest

from ask_me.channel import MESSAGE_LIMIT, split_message

MARKER = re.compile(r"^\((\d+)/(\d+)\) ")


def unmark(parts: list[str]) -> str:
    return "".join(MARKER.sub("", part, count=1) if index else part for index, part in enumerate(parts))


def test_short_text_is_a_single_untouched_part() -> None:
    assert split_message("hello") == ["hello"]
    assert split_message("y" * MESSAGE_LIMIT) == ["y" * MESSAGE_LIMIT]


def test_long_text_splits_into_numbered_parts_that_fit() -> None:
    text = "\n\n".join(f"paragraph {i}: " + "w" * 900 for i in range(11))  # about 10k chars
    parts = split_message(text)
    assert len(parts) == 3
    assert all(len(part) <= MESSAGE_LIMIT for part in parts)
    assert not MARKER.match(parts[0])
    assert parts[1].startswith("(2/3) ")
    assert parts[2].startswith("(3/3) ")
    assert unmark(parts) == text


def test_prefers_the_last_paragraph_break_before_the_limit() -> None:
    text = "a" * 3000 + "\n\n" + "b" * 3000 + "\nline " + "c" * 100
    first, second = split_message(text)
    assert first == "a" * 3000 + "\n\n"
    assert second == "(2/2) " + "b" * 3000 + "\nline " + "c" * 100


def test_falls_back_to_the_last_line_break_then_space_then_hard_split() -> None:
    lines = "\n".join("l" * 99 for _ in range(60))  # no blank lines, 6000 chars
    first, second = split_message(lines)
    assert first.endswith("\n") and len(first) <= MESSAGE_LIMIT
    assert unmark([first, second]) == lines

    words = " ".join("word" * 3 for _ in range(500))  # no newlines
    first, second = split_message(words)
    assert first.endswith(" ") and len(first) <= MESSAGE_LIMIT
    assert unmark([first, second]) == words

    solid = "z" * 9000
    parts = split_message(solid)
    assert [len(p) <= MESSAGE_LIMIT for p in parts] == [True, True, True]
    assert unmark(parts) == solid


def test_a_short_leading_line_is_not_sent_alone() -> None:
    parts = split_message("📁 proj\n❓ " + "q" * 5000)
    assert len(parts[0]) == 4096 and parts[0].startswith("📁 proj\n❓ qqq")


def test_parts_beyond_the_cap_are_replaced_by_a_truncation_note() -> None:
    text = "q" * 30000
    parts = split_message(text, max_parts=2)
    assert len(parts) == 2
    assert all(len(part) <= MESSAGE_LIMIT for part in parts)
    kept = len(unmark(parts[:1])) + len(MARKER.sub("", parts[1]).split("… (truncated, ")[0])
    note = re.search(r"… \(truncated, (\d+) more characters\)$", parts[1])
    assert note is not None
    assert kept + int(note.group(1)) == len(text)


def test_cap_of_one_truncates_the_only_part() -> None:
    parts = split_message("k" * 5000, max_parts=1)
    assert len(parts) == 1 and len(parts[0]) <= MESSAGE_LIMIT
    note = re.search(r"… \(truncated, (\d+) more characters\)$", parts[0])
    assert note and parts[0].startswith("k" * 100)
    assert len(parts[0]) - len(note.group(0)) + int(note.group(1)) == 5000


def test_text_that_fits_the_cap_has_no_truncation_note() -> None:
    parts = split_message("m" * 9000, max_parts=3)
    assert len(parts) == 3
    assert "truncated" not in parts[-1]


@pytest.mark.parametrize("size", [4097, 8192, 12288, 20000])
def test_every_part_fits_for_awkward_sizes(size: int) -> None:
    parts = split_message("é" * size, max_parts=10)
    assert all(len(part) <= MESSAGE_LIMIT for part in parts)
    assert unmark(parts) == "é" * size


def test_trailing_whitespace_never_becomes_an_empty_part() -> None:
    parts = split_message("a" * 4000 + "\n\n" + " " * 2000 + "\n")
    assert all(part.strip() for part in parts)
