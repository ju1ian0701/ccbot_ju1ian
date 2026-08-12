"""Binary-safe diff staging in propose.py (ISS-016).

Staging must be byte-exact: text-mode staging strips CR and breaks
git-format headers and '\\ No newline at end of file' hunks on Windows git.
"""

import propose
import pytest


def _git_format_diff() -> bytes:
    return (
        b"diff --git a/f.py b/f.py\n"
        b"index 1111111..2222222 100644\n"
        b"--- a/f.py\n"
        b"+++ b/f.py\n"
        b"@@ -1,3 +1,3 @@\n"
        b" a\n"
        b"-b\n"
        b"+bb\n"
        b" c\n"
    )


def _crlf_diff() -> bytes:
    return _git_format_diff().replace(b"\n", b"\r\n")


def _eof_marker_diff() -> bytes:
    return (
        b"--- a/g.py\n"
        b"+++ b/g.py\n"
        b"@@ -1,2 +1,2 @@\n"
        b" l1\n"
        b"-]\n"
        b"\\ No newline at end of file\n"
        b"+]\n"
    )


SAMPLES = {
    "lf": _git_format_diff(),
    "crlf": _crlf_diff(),
    "eof_marker": _eof_marker_diff(),
    "no_trailing_newline": _git_format_diff()[:-1],
}


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_write_temp_diff_is_byte_exact(name):
    tmp_path = propose._write_temp_diff(SAMPLES[name])
    try:
        assert tmp_path.read_bytes() == SAMPLES[name]
    finally:
        tmp_path.unlink(missing_ok=True)


def test_write_bytes_creates_parents_and_preserves_bytes(tmp_path):
    target = tmp_path / "nested" / "proposal.diff"

    propose.write_bytes(target, b"a\r\nb\n]")

    assert target.read_bytes() == b"a\r\nb\n]"


def test_check_diff_applies_rejects_markdown_fence_with_crlf():
    bad = b"```\r\n" + _crlf_diff()

    with pytest.raises(RuntimeError, match="markdown fence"):
        propose.check_diff_applies(bad)


def test_check_diff_applies_rejects_comment_with_crlf():
    bad = b"# staged\r\n" + _crlf_diff()

    with pytest.raises(RuntimeError, match="comment"):
        propose.check_diff_applies(bad)


def test_validate_proposal_diff_empty_gate():
    with pytest.raises(RuntimeError, match="Empty proposal.diff"):
        propose.validate_proposal_diff(b"")

    propose.validate_proposal_diff(b"", allow_empty=True)
