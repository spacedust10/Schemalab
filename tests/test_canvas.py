"""Canvas CSV cells never reach a spreadsheet as formulas."""

import pytest

from schemalab.canvas import defuse


@pytest.mark.parametrize(
    "cell",
    ["=1+2", " =1+2", "\t=1", "​=HYPERLINK(1)", "﻿+1", "-2+3", "@SUM(1)", "|cmd", "%x"],
)
def test_cells_a_spreadsheet_could_run_are_prefixed(cell: str) -> None:
    assert defuse(cell) == "'" + cell


@pytest.mark.parametrize(
    "cell", ["Ada", "ada@umich.edu", "Lovelace, Ada", "CIS 545 001", "U1001", ""]
)
def test_ordinary_cells_are_left_alone(cell: str) -> None:
    assert defuse(cell) == cell


EXPORT = (
    "Student,ID,SIS User ID,SIS Login ID,Section,Customers without an email (77),Quiz 1 (78)\n"
    "    Points Possible,,,,,100,10\n"
    '"Lovelace, Ada",501,U1001,ada@umich.edu,CIS 545 001,,9\n'
)


def test_the_export_s_own_column_is_reused_so_canvas_updates_that_assignment() -> None:
    from schemalab.canvas import gradebook

    text, _ = gradebook("Customers without an email", [("ada@umich.edu", 100)], EXPORT)
    assert text.splitlines()[0].endswith(",Customers without an email (77)")


def test_without_a_matching_column_the_bare_title_makes_a_new_assignment() -> None:
    from schemalab.canvas import gradebook

    text, _ = gradebook("Top earners", [("ada@umich.edu", 100)], EXPORT)
    assert text.splitlines()[0].endswith(",Top earners")
