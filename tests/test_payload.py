from failtriage.classify.payload import truncate_lines, truncate_message


def numbered(count: int) -> str:
    return "\n".join(f"line {n}" for n in range(1, count + 1))


def test_long_text_keeps_head_and_tail_and_counts_the_omitted_lines() -> None:
    result = truncate_lines(numbered(100), head=15, tail=25).splitlines()

    assert result[:15] == numbered(15).splitlines()
    assert result[15] == "... 60 lines omitted ..."
    assert result[16:] == [f"line {n}" for n in range(76, 101)]


def test_text_shorter_than_the_limits_is_unchanged() -> None:
    assert truncate_lines(numbered(10), head=15, tail=25) == numbered(10)


def test_text_exactly_at_the_limits_is_unchanged() -> None:
    assert truncate_lines(numbered(40), head=15, tail=25) == numbered(40)


def test_one_line_over_the_limits_omits_exactly_one_line() -> None:
    result = truncate_lines(numbered(41), head=15, tail=25)

    assert "... 1 lines omitted ..." in result.splitlines()
    assert "line 16" not in result.splitlines()


def test_empty_text_is_unchanged() -> None:
    assert truncate_lines("", head=15, tail=25) == ""


def test_message_over_the_limit_is_cut_and_counts_the_omitted_characters() -> None:
    result = truncate_message("a" * 1005, limit=1000)

    assert result == "a" * 1000 + "... 5 characters omitted"


def test_message_exactly_at_the_limit_is_unchanged() -> None:
    assert truncate_message("a" * 1000, limit=1000) == "a" * 1000


def test_empty_message_is_unchanged() -> None:
    assert truncate_message("", limit=1000) == ""
