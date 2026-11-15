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

from classifiers.document_classifier import (
    classify_page
)

from extractors.extraction_mapper import (  # noqa: E402
    normalize_date,
    extract_certificate_date,
    check_ro_certificate_holder,
    extract_form_numbers,
    find_primary_form_number,
    find_named_projects,
    build_document_schema,
)

from association.associate_documents import (  # noqa: E402
    associate_endorsement_to_acord,
    associate_endorsements,
    associate_batch,
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

GOOD_SUBMISSION_CONTEXT = {
    "named_insured_check": "MATCH",
    "required_protected_parties": [
        "R&O Construction Company",
    ],
}

GOOD_ENDORSEMENTS = [
    {
        "evidence_tags": [
            "ADDITIONAL_INSURED",
        ],
        "ro_chart_entry": chart_entry(
            "CG 20 10 07 04",
            True,
            False,
        ),
        "association_status": "MATCH",
        "protected_parties": [
            "R&O Construction Company",
        ],
        "protected_party_status": "MATCH",
        "material_restrictions": [],
    },
    {
        "evidence_tags": [
            "ADDITIONAL_INSURED",
        ],
        "ro_chart_entry": chart_entry(
            "CG 20 37 07 04",
            False,
            True,
        ),
        "association_status": "MATCH",
        "protected_parties": [
            "R&O Construction Company",
        ],
        "protected_party_status": "MATCH",
        "material_restrictions": [],
    },
    {
        "evidence_tags": [
            "PRIMARY_NONCONTRIBUTORY",
        ],
        "ro_chart_entry": None,
        "association_status": "MATCH",
        "protected_parties": [
            "R&O Construction Company",
        ],
        "protected_party_status": "MATCH",
        "primary_status": "CONFIRMED",
        "noncontributory_status": "CONFIRMED",
        "material_restrictions": [],
    },
    {
        "evidence_tags": [
            "WAIVER_OF_SUBROGATION",
        ],
        "ro_chart_entry": None,
        "association_status": "MATCH",
        "protected_parties": [
            "R&O Construction Company",
        ],
        "protected_party_status": "MATCH",
        "waiver_status": "CONFIRMED",
        "material_restrictions": [],
    },
]


def make_acord_record(
    insured="G2 Consultants, Inc.",
    policy_number="G24324722005",
    carrier="Westchester Surplus Lines Insurance Company",
    effective_date="2016-10-01",
    expiration_date="2017-10-01",
    source_file="certificate.pdf",
):
    return {
        "source_file": source_file,
        "page_number": 1,
        "schema": {
            "document_type": "ACORD_25",
            "named_insured": insured,
            "insurers": {
                "insurer_a": carrier
            },
            "coverages": {
                "general_liability": {
                    "policy_number": policy_number,
                    "effective_date": effective_date,
                    "expiration_date": expiration_date,
                }
            },
        },
    }


def make_endorsement_record(
    insured="G2 Consultants, Inc.",
    policy_number="G24324722005",
    carrier="Westchester Surplus Lines Insurance Company",
    effective_date="2016-10-01",
    expiration_date="2017-10-01",
    source_file="endorsement.pdf",
    page_number=1,
):
    return {
        "source_file": source_file,
        "page_number": page_number,
        "evidence_tags": [
            "ADDITIONAL_INSURED"
        ],
        "schema": {
            "document_type": "ENDORSEMENT",
            "named_insured": insured,
            "policy_number": policy_number,
            "carrier": carrier,
            "policy_period": {
                "effective_date":
                    effective_date,
                "expiration_date":
                    expiration_date,
            },
            "evidence_tags": [
                "ADDITIONAL_INSURED"
            ],
            "form_number":
                "CG 20 10 07 04",
        },
    }

def test_msa_minimums_pass_at_one_million():
    result = run_compliance_checks(
        make_acord(),
        GOOD_ENDORSEMENTS,
        review_date="2026-11-05",
        submission_context=GOOD_SUBMISSION_CONTEXT,
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


def test_unknown_page_does_not_join_endorsement(tmp_path):
    pages = [
        fake_page(
            1,
            "THIS ENDORSEMENT CHANGES THE POLICY.\n"
            "ADDITIONAL INSURED\n"
            "Policy Number\n"
            "ABC123456"
        ),
        fake_page(
            2,
            "This is an unrelated scanned document "
            "with no insurance endorsement information."
        ),
    ]

    fake_file = tmp_path / "mixed_document.pdf"
    fake_file.write_bytes(b"")

    records = process_file(
        fake_file,
        lambda _: SimpleNamespace(
            pages=pages
        )
    )

    assert len(records) == 2

    assert (
        records[0]["page_type"]
        == "ENDORSEMENT"
    )

    assert (
        records[1]["page_type"]
        == "UNKNOWN"
    )



def test_endorsement_continuation_classification():
    result = classify_page(
        "C. The insurance is primary and non-contributory\n"
        "CG 20 10 07 04"
    )

    assert (
        result["page_type"]
        == "ENDORSEMENT_CONTINUATION"
    )

    assert (
        "PRIMARY_NONCONTRIBUTORY"
        in result["evidence_tags"]
    )


def test_endorsement_mentioning_acord_is_not_acord25():
    result = classify_page(
        "THIS ENDORSEMENT CHANGES THE POLICY.\n"
        "ADDITIONAL INSURED\n"
        "Refer to the ACORD certificate for information.\n"
        "Policy Number ABC123456"
    )

    assert (
        result["page_type"]
        == "ENDORSEMENT"
    )


def test_acord25_requires_certificate_structure():
    result = classify_page(
        "CERTIFICATE OF LIABILITY INSURANCE\n"
        "ACORD\n"
        "PRODUCER\n"
        "INSURED\n"
        "INSURER A\n"
        "COVERAGES\n"
        "POLICY NUMBER\n"
        "CERTIFICATE HOLDER"
    )

    assert (
        result["page_type"]
        == "ACORD_25"
    )

    assert (
        result["classification_confidence"]
        == "HIGH"
    )

def test_unknown_gets_safe_generic_schema():
    schema = build_document_schema(
        "UNKNOWN",
        "Unrecognized page text",
        []
    )

    assert schema["document_type"] == "UNKNOWN"

    assert (
        schema["detailed_extraction_supported"]
        is False
    )

    assert (
        schema["extraction_status"]
        == "BASIC_SCHEMA_ONLY"
    )

    assert schema["requires_review"] is True

    assert (
        schema["raw_text"]
        == "Unrecognized page text"
    )


def test_correspondence_evidence_remains_unverified():
    schema = build_document_schema(
        "CORRESPONDENCE",
        "Please find the certificate attached.",
        ["ADDITIONAL_INSURED"]
    )

    assert (
        schema["document_type"]
        == "CORRESPONDENCE"
    )

    assert (
        schema["evidence_status"]
        == "UNVERIFIED"
    )

    assert (
        schema["detailed_extraction_supported"]
        is False
    )

    assert (
        schema["extraction_status"]
        == "BASIC_SCHEMA_ONLY"
    )

def test_policy_extract_preserved_without_full_extraction():
    schema = build_document_schema(
        "POLICY_EXTRACT",
        (
            "COMMERCIAL GENERAL LIABILITY\n"
            "SECTION II - WHO IS AN INSURED"
        ),
        []
    )

    assert (
        schema["document_type"]
        == "POLICY_EXTRACT"
    )

    assert (
        schema["extraction_status"]
        == "BASIC_SCHEMA_ONLY"
    )

    assert (
        schema["detailed_extraction_supported"]
        is False
    )

    assert schema["requires_review"] is True

    assert (
        "COMMERCIAL GENERAL LIABILITY"
        in schema["raw_text"]
    )

def test_matching_endorsement_associates_to_acord():
    acord = make_acord_record()

    endorsement = (
        make_endorsement_record()
    )

    result = (
        associate_endorsement_to_acord(
            acord,
            endorsement
        )
    )

    assert result["associated"] is True
    assert result["status"] == "ASSOCIATED"

    assert (
        result["exact_policy_match"]
        is True
    )

    assert result["insured_match"] is True
    assert result["carrier_match"] is True
    assert result["score"] >= 70

def test_different_insured_and_policy_do_not_associate():
    acord = make_acord_record(
        insured="G2 Consultants, Inc.",
        policy_number="G24324722005",
        effective_date="2016-10-01",
        expiration_date="2017-10-01",
    )

    ellis = make_endorsement_record(
        insured=(
            "Ellis Environmental "
            "Management, Inc."
        ),
        policy_number="G24238702 001",
        effective_date="2011-11-11",
        expiration_date="2013-11-11",
        source_file=(
            "ENV-3225 (10-08).pdf"
        ),
    )

    result = (
        associate_endorsement_to_acord(
            acord,
            ellis
        )
    )

    assert result["associated"] is False

    assert (
        result["status"]
        == "NOT_ASSOCIATED"
    )

    assert (
        "Endorsement policy number conflicts "
        "with the ACORD policy numbers"
        in result["conflicts"]
    )

    assert (
        "Named insured conflicts"
        in result["conflicts"]
    )

def test_related_endorsements_group_together():
    env_3225 = make_endorsement_record(
        insured=(
            "Ellis Environmental "
            "Management, Inc."
        ),
        policy_number="G24238702 001",
        effective_date="2011-11-11",
        expiration_date="2013-11-11",
        source_file=(
            "ENV-3225 (10-08).pdf"
        ),
    )

    env_3226 = make_endorsement_record(
        insured=(
            "Ellis Environmental "
            "Management, Inc."
        ),
        policy_number="G24238702 001",
        effective_date="2011-11-11",
        expiration_date="2013-11-11",
        source_file=(
            "ENV-3226 (10-08).pdf"
        ),
    )

    result = associate_endorsements(
        env_3225,
        env_3226
    )

    assert result["associated"] is True

    assert (
        result["status"]
        == "ASSOCIATED"
    )

    assert (
        result["exact_policy_match"]
        is True
    )

    assert result["insured_match"] is True
    assert result["carrier_match"] is True



def test_batch_separates_g2_and_ellis_groups():
    g2_acord = make_acord_record(
        source_file="ENV-3101(08-04).pdf"
    )

    g2_endorsement = (
        make_endorsement_record(
            source_file=(
                "ENV-3101(08-04).pdf"
            ),
            page_number=2,
        )
    )

    ellis_3225 = (
        make_endorsement_record(
            insured=(
                "Ellis Environmental "
                "Management, Inc."
            ),
            policy_number="G24238702 001",
            effective_date="2011-11-11",
            expiration_date="2013-11-11",
            source_file=(
                "ENV-3225 (10-08).pdf"
            ),
        )
    )

    ellis_3226 = (
        make_endorsement_record(
            insured=(
                "Ellis Environmental "
                "Management, Inc."
            ),
            policy_number="G24238702 001",
            effective_date="2011-11-11",
            expiration_date="2013-11-11",
            source_file=(
                "ENV-3226 (10-08).pdf"
            ),
        )
    )

    result = associate_batch([
        g2_acord,
        g2_endorsement,
        ellis_3225,
        ellis_3226,
    ])

    assert len(result["submissions"]) == 2

    assert (
        result["acord_submission_count"]
        == 1
    )

    assert (
        result[
            "endorsement_only_group_count"
        ]
        == 1
    )

    assert (
        len(result["unassociated_records"])
        == 0
    )

    acord_groups = [
        submission
        for submission
        in result["submissions"]
        if submission["group_type"]
        == "ACORD_BASED"
    ]

    endorsement_groups = [
        submission
        for submission
        in result["submissions"]
        if submission["group_type"]
        == "ENDORSEMENT_ONLY"
    ]

    assert (
        len(
            acord_groups[0][
                "endorsement_records"
            ]
        )
        == 1
    )

    assert (
        len(
            endorsement_groups[0][
                "endorsement_records"
            ]
        )
        == 2
    )


def test_missing_policy_number_can_associate_using_other_signals():
    acord = make_acord_record(
        insured="ABC Construction LLC",
        policy_number="GL12345678",
        carrier="Westchester Insurance",
        effective_date="2026-01-01",
        expiration_date="2027-01-01",
    )

    endorsement = (
        make_endorsement_record(
            insured="ABC Construction LLC",
            policy_number=None,
            carrier="Westchester Insurance",
            effective_date="2026-06-01",
            expiration_date=None,
        )
    )

    result = (
        associate_endorsement_to_acord(
            acord,
            endorsement
        )
    )

    assert result["associated"] is True

    assert (
        result["exact_policy_match"]
        is False
    )

    assert result["insured_match"] is True
    assert result["carrier_match"] is True
    assert result["date_match"] is True

    assert result["score"] >= 70


def test_same_folder_alone_does_not_force_association():
    acord = make_acord_record(
        insured="ABC Construction LLC",
        policy_number="ABC123456",
        carrier="Westchester Insurance",
        effective_date="2026-01-01",
        expiration_date="2027-01-01",
    )

    endorsement = (
        make_endorsement_record(
            insured="XYZ Plumbing LLC",
            policy_number="XYZ999999",
            carrier="Westchester Insurance",
            effective_date="2021-01-01",
            expiration_date="2022-01-01",
        )
    )

    acord["company_name"] = (
        "ACE - Westchester"
    )

    endorsement["company_name"] = (
        "ACE - Westchester"
    )

    result = (
        associate_endorsement_to_acord(
            acord,
            endorsement
        )
    )

    assert (
        result["same_company_folder"]
        is True
    )

    assert result["associated"] is False

    assert (
        result["status"]
        == "NOT_ASSOCIATED"
    )


def test_single_acord_same_pdf_endorsement_without_identity_associates():
    acord = make_acord_record(
        source_file="single_package.pdf"
    )

    endorsement = {
        "source_file": "single_package.pdf",
        "page_number": 2,
        "evidence_tags": [
            "ADDITIONAL_INSURED",
        ],
        "schema": {
            "document_type": "ENDORSEMENT",
            "policy_number": None,
            "named_insured": None,
            "carrier": None,
            "form_number": "CG 20 10 07 04",
            "evidence_tags": [
                "ADDITIONAL_INSURED",
            ],
        },
    }

    result = associate_batch([
        acord,
        endorsement,
    ])

    assert len(result["submissions"]) == 1

    submission = result["submissions"][0]

    assert len(submission["endorsement_records"]) == 1

    association = submission["association_results"][0]

    assert association["associated"] is True

    assert (
        association["association_method"]
        == "SINGLE_ACORD_SAME_PDF_CONTEXT"
    )

    assert (
        submission["endorsement_records"][0]["schema"][
            "association_status"
        ]
        == "MATCH"
    )

def test_identity_missing_endorsement_in_different_pdf_does_not_associate():
    acord = make_acord_record(
        source_file="certificate.pdf"
    )

    endorsement = {
        "source_file": "unrelated_endorsement.pdf",
        "page_number": 1,
        "evidence_tags": [
            "ADDITIONAL_INSURED",
        ],
        "schema": {
            "document_type": "ENDORSEMENT",
            "policy_number": None,
            "named_insured": None,
            "carrier": None,
            "form_number": "CG 20 10 07 04",
            "evidence_tags": [
                "ADDITIONAL_INSURED",
            ],
        },
    }

    result = associate_batch([
        acord,
        endorsement,
    ])

    acord_submission = [
        submission
        for submission in result["submissions"]
        if submission["group_type"] == "ACORD_BASED"
    ][0]

    assert len(
        acord_submission["endorsement_records"]
    ) == 0

def test_acord101_preserves_additional_remarks():
    text = (
        "ACORD 101 ADDITIONAL REMARKS SCHEDULE\n"
        "R&O Construction Company is included where required "
        "by written contract."
    )

    schema = build_document_schema(
        "ACORD_101",
        text,
        ["ADDITIONAL_INSURED"],
    )

    expected_text = (
        "ACORD 101 ADDITIONAL REMARKS SCHEDULE "
        "R&O Construction Company is included where required "
        "by written contract."
    )

    assert schema["document_type"] == "ACORD_101"
    assert schema["additional_remarks"] == expected_text
    assert schema["raw_text"] == expected_text

    assert (
        schema["evidence_status"]
        == "SUPPORTING_STATEMENT_ONLY"
    )

    assert schema["requires_parent_certificate"] is True