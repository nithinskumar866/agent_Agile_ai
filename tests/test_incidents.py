"""docs/incidents.md must stay linked to real tests: an incident without a guard is allowed only
if it says it is OPEN."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTER = (ROOT / "docs" / "incidents.md").read_text(encoding="utf-8")
SECTIONS = re.split(r"\n(?=## INC-)", REGISTER)[1:]


def test_register_lists_incidents():
    assert len(SECTIONS) >= 10 and all(s.startswith("## INC-") for s in SECTIONS)


def test_every_regression_test_named_in_the_register_exists():
    named = re.findall(r"\*\*Regression test:\*\* `([^`]+)`", REGISTER)
    assert len(named) >= 12
    for ref in named:
        path, _, name = ref.partition("::")
        source = (ROOT / path).read_text(encoding="utf-8")
        assert re.search(rf"^(async )?def {re.escape(name)}\(", source, re.M), f"{ref} does not exist"


def test_an_incident_without_a_test_must_be_marked_open():
    for section in SECTIONS:
        title = section.splitlines()[0]
        has_test = "**Regression test:** `" in section
        is_open = "**OPEN**" in section
        assert has_test or is_open, f"{title} has no regression test and is not marked OPEN"
        if is_open:
            assert "**Regression test:** none yet (open)" in section, f"{title}: say 'none yet (open)'"
