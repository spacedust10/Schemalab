"""English -> ER: the model writes the text format, and only a design that parses is kept."""

import pytest

from schemalab.agent.design import design_from_english
from schemalab.er import ERError
from tests.agent.scripted import ScriptedGateway

GOOD = """```
entity Student { sid int key, name text }
entity Course { cid varchar(10) key, title text }
relationship Enrolls: Student M -- N Course { grade text }
```"""
BAD = "entity Student { sid int }"  # no key

DESCRIPTION = "Students enroll in courses and get a grade."


def test_a_design_that_parses_is_returned() -> None:
    gw = ScriptedGateway(GOOD)
    text, model = design_from_english(DESCRIPTION, gw)
    assert [e.name for e in model.entities] == ["Student", "Course"]
    assert text.startswith("entity Student")
    assert DESCRIPTION in gw.prompt(0)
    assert "relationship Name: EntityA 1 -- N EntityB" in gw.prompt(0)  # the grammar is given


def test_a_parse_error_is_sent_back_once() -> None:
    gw = ScriptedGateway(BAD, GOOD)
    _, model = design_from_english(DESCRIPTION, gw)
    assert len(model.entities) == 2
    assert "Student has no key attribute" in gw.prompt(1)


def test_two_failures_give_up_with_the_error() -> None:
    gw = ScriptedGateway(BAD, BAD)
    with pytest.raises(ERError, match="no key attribute"):
        design_from_english(DESCRIPTION, gw)
    assert gw.replies == []
