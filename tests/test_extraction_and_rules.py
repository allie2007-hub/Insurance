"""
Run from the repository root:   python -m pytest -q tests

These tests use short synthetic text, so they need neither the Azure key
nor R&O's chart file.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractors.extraction_mapper import (  # noqa: E402
    normalize_date,
    extract_certificate_date,
    check_ro_certificate_holder,
    extract_form_numbers,
    find_primary_form_number,
    find_named_projects,
)
from reference.endorsement_chart import (  # noqa: E402
    lookup_form,
    normalize_form_number,
)
from rules.compliance_rules import run_compliance_checks  # noqa: E402
from Submission.multi_document_processor import process_file  # noqa: E402


# ---------------------------------------------------------
# Extraction
# ---------------------------------------------------------

def test_normalize_date_formats():
    assert normalize_date("12/10/2016") == "2016-12-10"
    assert normalize_date("1/5/25") == "2025-01-05"
    assert normalize_date("03-01-2026") == "2026-03-01"
    assert normalize_date("XX/XX/XX") is None
    assert normalize_date("13/45/2020") is None


def test_certificate_date():
    text = "DATE (MM/DD/YYYY)\nCERTIFICATE OF LIABILITY INSURANCE\n06/08/2017"
    assert extract_certificate_date(text) == "2017-06-08"


def test_certificate_holder_variants():
    assert check_ro_certificate_holder(
        "R & O Construction Company\n933 Wall Avenue, Ste 2\nOgden, UT 84404"
    ) == "MATCH"
    assert check_ro_certificate_holder(
        "R&O Construction Co\n933 Wall Ave Suite 2\nOgden UT 84404"
    ) == "MATCH"
    assert check_ro_certificate_holder(
        "R&O Construction Company\n939 Wall Avenue\nOgden, UT 84404"
    ) == "MISMATCH"
    assert check_ro_certificate_holder(None) is None


def test_form_numbers_iso_and_carrier():
    page = (
        "THIS ENDORSEMENT CHANGES THE POLICY. PLEASE READ IT CAREFULLY.\n"
        "ADDITIONAL INSURED - OWNERS, LESSEES OR CONTRACTORS\n"
        "Coverage is no broader than CG 20 10 11 85.\n"
        "Includes copyrighted material of ISO\n"
        "CG 20 10 07 04 © ISO Properties, Inc., 2004"
    )
    numbers = extract_form_numbers(page)
    assert "CG 20 10 07 04" in numbers
    # The cited form must not be taken as this endorsement's number.
    assert find_primary_form_number(page, numbers) == "CG 20 10 07 04"

    assert any(
        n.startswith("U-GL-1081-B CW")
        for n in extract_form_numbers("Form U-GL-1081-B CW (04/13) Page 1 of 2")
    )
    assert extract_form_numbers("Rapid City, SD 57709") == []


def test_form_number_normalization():
    assert normalize_form_number("CG 20 10 (07/04)") == "CG201007" + "04"
    assert lookup_form("CG 20 10 07 04", chart={}) is None


def test_project_wording():
    assert find_named_projects(
        "Additional insured where required by written contract, all jobs"
    )["blanket_wording"]
    assert find_named_projects(
        "Project Name: Ogden Medical Center"
    )["names_specific_project"]


# ---------------------------------------------------------
# Rules
# ---------------------------------------------------------

def make_acord(**overrides):
    acord = {
        "document_type": "ACORD_25",
        # A broker statement on the certificate; must NOT count as proof.
        "evidence_tags": [
            "ADDITIONAL_INSURED",
            "PRIMARY_NONCONTRIBUTORY",
            "WAIVER_OF_SUBROGATION",
        ],
        "certificate_holder": "R&O Construction Company 933 Wall Avenue Ogden, UT 84404",
        "certificate_holder_check": "MATCH",
        "project_wording": {"blanket_wording": True, "names_specific_project": False},
        "coverages": {
            "general_liability": {
                "effective_date": "2026-01-01", "expiration_date": "2027-01-01",
                "each_occurrence": "1000000", "general_aggregate": "2000000",
                "products_completed_operations": "2000000",
                "occurrence_form": "OCCURRENCE", "aggregate_applies_per": "PROJECT",
            },
            "automobile_liability": {
                "effective_date": "2026-01-01", "expiration_date": "2027-01-01",
                "combined_single_limit": "1000000", "any_auto": True,
            },
            "umbrella_excess": {
                "effective_date": "2026-01-01", "expiration_date": "2027-01-01",
                "each_occurrence": "1000000", "aggregate": "1000000",
            },
            "workers_compensation": {
                "effective_date": "2026-01-01", "expiration_date": "2027-01-01",
                "each_accident": "1000000", "disease_each_employee": "1000000",
                "disease_policy_limit": "1000000",
                "policy_number": "WC123456", "per_statute": True,
            },
        },
    }
    acord.update(overrides)
    return acord


def chart_entry(form, ongoing, completed, pnc=False, decision="ACCEPT"):
    return {
        "form_number": form,
        "ongoing_operations": ongoing,
        "completed_operations": completed,
        "completed_operations_conditional": False,
        "primary_noncontributory": pnc,
        "chart_decision": decision,
    }


GOOD_ENDORSEMENTS = [
    {"evidence_tags": ["ADDITIONAL_INSURED"],
     "ro_chart_entry": chart_entry("CG 20 10 07 04", True, False)},
    {"evidence_tags": ["ADDITIONAL_INSURED"],
     "ro_chart_entry": chart_entry("CG 20 37 07 04", False, True)},
    {"evidence_tags": ["PRIMARY_NONCONTRIBUTORY"], "ro_chart_entry": None},
    {"evidence_tags": ["WAIVER_OF_SUBROGATION"], "ro_chart_entry": None},
]


def test_msa_minimums_pass_at_one_million():
    result = run_compliance_checks(
        make_acord(), GOOD_ENDORSEMENTS, review_date="2026-11-05"
    )
    assert result["overall_status"] == "COMPLIANT", result["checks"]


def test_certificate_statements_are_not_proof():
    result = run_compliance_checks(make_acord(), [], review_date="2026-11-05")
    checks = result["checks"]
    assert checks["additional_insured"]["status"] == "MISSING"
    assert checks["waiver_of_subrogation"]["status"] == "MISSING"
    assert checks["primary_non_contributory"]["status"] == "MISSING"
    assert result["overall_status"] == "INCOMPLETE_SUBMISSION"


def test_expired_policy_fails():
    acord = make_acord()
    acord["coverages"]["general_liability"]["expiration_date"] = "2026-06-30"
    result = run_compliance_checks(acord, GOOD_ENDORSEMENTS, review_date="2026-11-05")
    assert result["checks"]["policy_dates.general_liability"]["status"] == "FAIL"
    assert result["overall_status"] == "NON_COMPLIANT"


def test_low_limit_fails():
    acord = make_acord()
    acord["coverages"]["general_liability"]["general_aggregate"] = "1000000"
    result = run_compliance_checks(acord, GOOD_ENDORSEMENTS, review_date="2026-11-05")
    assert result["checks"]["general_liability.general_aggregate"]["status"] == "FAIL"


def test_ongoing_only_is_missing_completed_ops():
    result = run_compliance_checks(
        make_acord(), GOOD_ENDORSEMENTS[:1] + GOOD_ENDORSEMENTS[2:],
        review_date="2026-11-05"
    )
    assert result["checks"]["additional_insured"]["status"] == "MISSING"


def test_rejected_form_fails():
    endorsements = [
        {"evidence_tags": ["ADDITIONAL_INSURED"],
         "ro_chart_entry": chart_entry("CSGA 437 11 08", True, True, decision="REJECT")},
    ] + GOOD_ENDORSEMENTS[2:]
    result = run_compliance_checks(make_acord(), endorsements, review_date="2026-11-05")
    assert result["checks"]["additional_insured"]["status"] == "FAIL"


def test_wrong_certificate_holder_fails():
    result = run_compliance_checks(
        make_acord(certificate_holder_check="MISMATCH"),
        GOOD_ENDORSEMENTS, review_date="2026-11-05"
    )
    assert result["checks"]["certificate_holder"]["status"] == "FAIL"


# ---------------------------------------------------------
# Multi-page endorsements
# ---------------------------------------------------------

def fake_page(number, text):
    lines = [SimpleNamespace(content=line) for line in text.splitlines()]
    return SimpleNamespace(page_number=number, lines=lines)


def test_continuation_page_joins_endorsement(tmp_path):
    pages = [
        fake_page(1, "THIS ENDORSEMENT CHANGES THE POLICY.\nADDITIONAL INSURED\nPolicy Number\nABC123456"),
        fake_page(2, "C. The insurance is primary and non-contributory\nCG 20 10 07 04"),
    ]
    fake_file = tmp_path / "endorsement.pdf"
    fake_file.write_bytes(b"")

    records = process_file(fake_file, lambda _: SimpleNamespace(pages=pages))

    assert len(records) == 1
    assert records[0]["page_numbers"] == [1, 2]
    assert "PRIMARY_NONCONTRIBUTORY" in records[0]["evidence_tags"]


def test_claims_made_gl_fails():
    acord = make_acord()
    acord["coverages"]["general_liability"]["occurrence_form"] = "CLAIMS_MADE"
    result = run_compliance_checks(acord, GOOD_ENDORSEMENTS, review_date="2026-11-05")
    assert result["checks"]["general_liability_form.occurrence_basis"]["status"] == "FAIL"
