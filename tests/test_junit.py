"""JUnit parsing: the two real roots, failure/error kinds, the gotchas."""

import xml.etree.ElementTree as ET

import pytest

from slowdit.junit import parse_junit_xml

WRAPPED = """<?xml version="1.0"?>
<testsuites>
  <testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1" time="2.5">
    <testcase classname="tests.test_a" name="test_ok"></testcase>
    <testcase classname="tests.test_a" name="test_ok2"></testcase>
    <testcase classname="tests.test_b" name="test_fail">
      <failure message="assert 1 == 2">traceback…</failure>
    </testcase>
    <testcase classname="tests.test_b" name="test_err">
      <error message="fixture exploded">traceback…</error>
    </testcase>
  </testsuite>
</testsuites>
"""

BARE = """<testsuite tests="1" failures="0" errors="0" skipped="0" time="0.1">
  <testcase classname="x" name="y"></testcase>
</testsuite>
"""


def _write(tmp_path, name: str, content: str):
    p = tmp_path / name
    p.write_text(content)
    return p


def test_wrapped_root(tmp_path):
    r = parse_junit_xml(_write(tmp_path, "a.xml", WRAPPED))
    assert (r.total, r.failures, r.errors, r.skipped) == (4, 1, 1, 1)
    assert r.passed == 1  # derived — JUnit has no "passed" attribute
    assert r.duration_seconds == 2.5
    assert [f.nodeid for f in r.failing] == [
        "tests.test_b::test_fail",
        "tests.test_b::test_err",
    ]
    assert {f.kind for f in r.failing} == {"failure", "error"}
    assert r.failing[0].message == "assert 1 == 2"
    assert not r.ok


def test_bare_root(tmp_path):
    r = parse_junit_xml(_write(tmp_path, "b.xml", BARE))
    assert (r.total, r.failures, r.errors) == (1, 0, 0)
    assert r.ok
    assert r.failing == []


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_junit_xml(tmp_path / "nope.xml")


def test_malformed_raises(tmp_path):
    with pytest.raises(ET.ParseError):
        parse_junit_xml(_write(tmp_path, "bad.xml", "<testsuites><testsuite>"))


def test_no_testsuite_element_raises(tmp_path):
    with pytest.raises(ValueError):
        parse_junit_xml(_write(tmp_path, "none.xml", "<foo/>"))
