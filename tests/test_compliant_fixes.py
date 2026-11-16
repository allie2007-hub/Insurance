"""
Fixes found by running R&O's labeled packages (Nov 16, 2026):
no compliant package could ever be predicted COMPLIANT.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractors.extraction_mapper import (  # noqa: E402
    check_ro_certificate_holder,
    ro_certificate_holder_note,
    extract_endorsement_wording,
)
from extractors.acord_layout import extract_acord_layout  # noqa: E402
from rules.compliance_rules import (  # noqa: E402
    validate_named_insured,
    validate_protected_parties,
    validate_project_wording,
    DEFAULT_RULE_SET,
)


def test_holder_zip_typo_still_matches_ro():
    # RC Hunt (84044) and Ron J Peterson (84401) are labeled compliant.
    for text in (
        "R & O Construction Company\n933 Wall Avenue\nOgden UT 84044",
        "R & O CONSTRUCTION INC\n933 WALL AVE\nOgden\nUT 84401",
    ):
        assert check_ro_certificate_holder(text) == "MATCH"
        assert "ZIP" in ro_certificate_holder_note(text)

    # Wrong street is still a mismatch (Supreme Fire Protection).
    assert check_ro_certificate_holder(
        "R & O Construction\n6787 Spring Street\nOgden UT 84404"
    ) == "MISMATCH"


def test_context_checks_do_not_block_without_context():
    assert validate_named_insured({}, {})["status"] == "NOT_APPLICABLE"
    assert validate_protected_parties([], {})["status"] == "NOT_APPLICABLE"
    assert validate_project_wording(
        {"project_wording": {}}, DEFAULT_RULE_SET, {}
    )["status"] == "PASS"
    # A named project still needs a person to confirm it.
    assert validate_project_wording(
        {"project_wording": {"names_specific_project": True}},
        DEFAULT_RULE_SET, {},
    )["status"] == "REVIEW_REQUIRED"


def _wording(text, tags):
    return extract_endorsement_wording(text, tags)


def test_arising_out_of_is_a_broad_trigger_not_a_review():
    text = ("ADDITIONAL INSURED. WHO IS AN INSURED IS AMENDED TO INCLUDE ... "
            "BUT ONLY WITH RESPECT TO LIABILITY ARISING OUT OF YOUR ONGOING OPERATIONS")
    assert _wording(text, ["ADDITIONAL_INSURED"])["negligence_trigger_status"] == "CONFIRMED"


def test_caused_in_whole_or_in_part_split_across_lines():
    text = ("WHO IS AN INSURED IS AMENDED TO INCLUDE AS AN ADDITIONAL INSURED ...\n"
            "INJURY CAUSED, IN\nWHOLE OR IN PART, BY YOUR ONGOING OPERATIONS")
    assert _wording(text, ["ADDITIONAL_INSURED"])["negligence_trigger_status"] == "CONFIRMED"


def test_primary_noncontributory_condition_needs_no_trigger():
    # CG 20 01: mentions additional insureds, grants nothing.
    text = ("PRIMARY AND NONCONTRIBUTORY - OTHER INSURANCE CONDITION. "
            "THIS INSURANCE IS PRIMARY TO AND WILL NOT SEEK CONTRIBUTION FROM ANY OTHER "
            "INSURANCE AVAILABLE TO AN ADDITIONAL INSURED ... PRODUCTS-COMPLETED OPERATIONS HAZARD")
    result = _wording(text, ["ADDITIONAL_INSURED", "COMPLETED_OPERATIONS", "PRIMARY_NONCONTRIBUTORY"])
    assert result["negligence_trigger_status"] is None


def _line(text, x0, y0, x1, y1):
    return SimpleNamespace(content=text, polygon=[x0, y0, x1, y0, x1, y1, x0, y1])


def test_inline_x_checkboxes_are_read():
    # Some brokers print the tick in the same text line as the label.
    page = SimpleNamespace(page_number=1, selection_marks=[], lines=[
        _line("COMMERCIAL GENERAL LIABILITY", 0.75, 4.20, 2.2, 4.30),
        _line("CLAIMS-MADE X OCCUR", 0.94, 4.39, 2.2, 4.48),
        _line("POLICY X PRO-", 0.74, 5.05, 1.6, 5.15),
        _line("AUTOMOBILE LIABILITY", 0.55, 5.43, 1.5, 5.53),
        _line("X ANY AUTO", 0.55, 5.60, 1.2, 5.70),
    ])
    gl = extract_acord_layout(page)["coverages"]["general_liability"]
    assert gl["occurrence_form"] == "OCCURRENCE"
    assert gl["aggregate_applies_per"] == "PROJECT"
    assert extract_acord_layout(page)["coverages"]["automobile_liability"]["any_auto"] is True
