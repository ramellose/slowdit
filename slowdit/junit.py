"""JUnit XML parsing — the ONE place that knows the JUnit shape.

Tolerant of the two common roots: a <testsuites> wrapper (pytest's
--junitxml) or a bare <testsuite>. Raises on malformed input; the caller
decides what that means (NO_RESULTS / RUN_FAILED) — this module never
swallows a parse error into a fake "0 tests" result.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .models import TestFailure, TestResult


def parse_junit_xml(path: str | Path) -> TestResult:
    """Build a TestResult from a JUnit XML file.

    Raises FileNotFoundError if absent, ET.ParseError/ValueError if malformed.
    """
    tree = ET.parse(path)
    root = tree.getroot()

    if root.tag == "testsuites":
        suite = root.find("testsuite")
        if suite is None:
            raise ValueError(f"No <testsuite> element found in {path}")
    elif root.tag == "testsuite":
        suite = root
    else:
        raise ValueError(f"unexpected JUnit root element <{root.tag}> in {path}")

    failing: list[TestFailure] = []
    for tc in suite.iter("testcase"):
        # A testcase is failing iff it has a <failure> or <error> child.
        failure_el = tc.find("failure")
        error_el = tc.find("error")
        problem = failure_el if failure_el is not None else error_el
        if problem is None:
            continue  # passed (or skipped) — no child element
        failing.append(
            TestFailure(
                nodeid=f"{tc.get('classname')}::{tc.get('name')}",
                message=problem.get("message", ""),
                kind="error" if error_el is not None else "failure",
            )
        )

    return TestResult(
        total=int(suite.get("tests", 0)),
        failures=int(suite.get("failures", 0)),
        errors=int(suite.get("errors", 0)),
        skipped=int(suite.get("skipped", 0)),
        duration_seconds=float(suite.get("time", 0.0)),
        failing=failing,
    )
