from failtriage.classify.payload import select_hunks, truncate_lines, truncate_message


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


DIFF = """\
diff --git a/src/wallet/ledger.py b/src/wallet/ledger.py
index 111..222 100644
--- a/src/wallet/ledger.py
+++ b/src/wallet/ledger.py
@@ -1,2 +1,2 @@
-balance = 0
+balance = 0.0
diff --git a/README.md b/README.md
index 333..444 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""
TRACE = '  File "/work/src/wallet/ledger.py", line 9, in deposit'


def test_diff_keeps_only_files_named_in_the_trace() -> None:
    result = select_hunks(DIFF, TRACE, limit=150)

    assert "src/wallet/ledger.py" in result
    assert "+balance = 0.0" in result
    assert "README.md" not in result


def test_diff_without_a_relevant_file_is_empty() -> None:
    assert select_hunks(DIFF, "nothing here", limit=150) == ""


def test_empty_diff_is_empty() -> None:
    assert select_hunks("", TRACE, limit=150) == ""


def test_relevant_diff_over_the_line_limit_is_cut_with_a_marker() -> None:
    result = select_hunks(DIFF, TRACE, limit=3).splitlines()

    assert result[:3] == DIFF.splitlines()[:3]
    assert result[3] == "... 4 lines omitted ..."
    assert len(result) == 4
