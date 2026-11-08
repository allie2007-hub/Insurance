# rules/compliance_rules.py
#
# Requirement checks for one candidate submission (one ACORD 25 plus the
# endorsements associated with it).
#
# Source of the requirements: R&O Master Subcontract Agreement, Section 3
# ("2024.10.03 - Insurance Requirements from MSA.docx"), the one-page
# "Insurance Requirements_New MSA's 4.10.24.pdf", and R&O's Copilot
# reviewer instructions. A WAD or Prime Contract can change these limits;
# that is not handled yet, so every check uses the MSA baseline.
#
# Two principles from R&O's "How to Read and Review COIs" guide:
#   1. The certificate is not proof. Additional insured, primary and
#      non-contributory and waiver of subrogation need endorsement wording;
#      an X or a sentence on the ACORD 25 does not count.
#   2. Uncertain evidence goes to a person, it is not passed or failed.

import os
from datetime import date


RULE_SET = {
    "rule_set_id": "RO-MSA-SECTION-3",
    "version": "2024-10-03",
    "source": "R&O MSA Section 3 (2024.10.03) and New MSA requirements 4.10.24",
    "limits": {
        "gl_each_occurrence": 1_000_000,
        "gl_general_aggregate": 2_000_000,
        "gl_products_completed_ops": 2_000_000,
        "auto_combined_single_limit": 1_000_000,
        "umbrella_each_occurrence": 1_000_000,
        "el_each_accident": 1_000_000,
        "el_disease_each_employee": 1_000_000,
        "el_disease_policy_limit": 1_000_000,
    },
}


def safe_int(value):

    if value is None:
        return None

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def check_minimum(value, minimum, label):
    """Shared limit check: missing -> REVIEW_REQUIRED, low -> FAIL."""

    amount = safe_int(value)

    if amount is None:
        return {
            "status": "REVIEW_REQUIRED",
            "reason": f"{label} limit was not extracted",
            "required": minimum,
            "found": None,
        }

    if amount >= minimum:
        return {
            "status": "PASS",
            "reason": f"{label} limit {amount:,} meets {minimum:,}",
            "required": minimum,
            "found": amount,
        }

    return {
        "status": "FAIL",
        "reason": f"{label} limit {amount:,} is below {minimum:,}",
        "required": minimum,
        "found": amount,
    }


# ---------------------------------------------------------
# Limits (from the ACORD 25)
# ---------------------------------------------------------

def validate_general_liability(acord_schema):

    gl = acord_schema["coverages"]["general_liability"]
    limits = RULE_SET["limits"]

    return {
        "each_occurrence": check_minimum(
            gl.get("each_occurrence"),
            limits["gl_each_occurrence"],
            "GL each occurrence"
        ),
        "general_aggregate": check_minimum(
            gl.get("general_aggregate"),
            limits["gl_general_aggregate"],
            "GL general aggregate"
        ),
        "products_completed_operations": check_minimum(
            gl.get("products_completed_operations"),
            limits["gl_products_completed_ops"],
            "GL products/completed operations aggregate"
        ),
    }


def validate_auto_liability(acord_schema):

    auto = acord_schema["coverages"]["automobile_liability"]

    return check_minimum(
        auto.get("combined_single_limit"),
        RULE_SET["limits"]["auto_combined_single_limit"],
        "Auto combined single"
    )


def validate_umbrella(acord_schema):

    umbrella = acord_schema["coverages"]["umbrella_excess"]

    return check_minimum(
        umbrella.get("each_occurrence"),
        RULE_SET["limits"]["umbrella_each_occurrence"],
        "Umbrella/excess each occurrence"
    )


def validate_employers_liability(acord_schema):

    wc = acord_schema["coverages"]["workers_compensation"]
    limits = RULE_SET["limits"]

    if not wc.get("policy_number") and not any(
        wc.get(field) for field in (
            "each_accident", "disease_each_employee", "disease_policy_limit"
        )
    ):
        missing = {
            "status": "MISSING",
            "reason": "No workers' compensation / employers liability "
                      "policy on the certificate",
        }
        return {
            "each_accident": missing,
            "disease_each_employee": missing,
            "disease_policy_limit": missing,
        }

    return {
        "each_accident": check_minimum(
            wc.get("each_accident"),
            limits["el_each_accident"],
            "E.L. each accident"
        ),
        "disease_each_employee": check_minimum(
            wc.get("disease_each_employee"),
            limits["el_disease_each_employee"],
            "E.L. disease each employee"
        ),
        "disease_policy_limit": check_minimum(
            wc.get("disease_policy_limit"),
            limits["el_disease_policy_limit"],
            "E.L. disease policy"
        ),
    }


# ---------------------------------------------------------
# Coverage form (checkboxes on the ACORD 25)
# ---------------------------------------------------------

def validate_gl_form(acord_schema):
    """
    MSA 3.B.3: GL "on an occurrence basis (claims made basis is not
    acceptable)" and the general aggregate must apply "per project".
    """

    gl = acord_schema["coverages"]["general_liability"]
    results = {}

    form = gl.get("occurrence_form")

    if form == "OCCURRENCE":
        results["occurrence_basis"] = {
            "status": "PASS", "reason": "GL written on an occurrence basis"}
    elif form == "CLAIMS_MADE":
        results["occurrence_basis"] = {
            "status": "FAIL",
            "reason": "GL is claims-made; the MSA requires occurrence basis"}
    else:
        results["occurrence_basis"] = {
            "status": "REVIEW_REQUIRED",
            "reason": "Could not read the OCCUR / CLAIMS-MADE checkbox"}

    applies = gl.get("aggregate_applies_per")

    if applies == "PROJECT":
        results["per_project_aggregate"] = {
            "status": "PASS",
            "reason": "General aggregate applies per project"}
    elif applies in ("POLICY", "LOCATION"):
        results["per_project_aggregate"] = {
            "status": "REVIEW_REQUIRED",
            "reason": (
                f"General aggregate applies per {applies.lower()}; MSA "
                "requires per project (may be met by an endorsement)")}
    else:
        results["per_project_aggregate"] = {
            "status": "REVIEW_REQUIRED",
            "reason": "Could not read the aggregate-applies-per checkbox"}

    return results


def validate_auto_scope(acord_schema):
    """MSA 3.B.4: owned, hired and non-owned vehicles."""

    auto = acord_schema["coverages"]["automobile_liability"]

    if auto.get("any_auto"):
        return {"status": "PASS", "reason": "ANY AUTO is checked"}

    covered = {
        "owned": auto.get("owned_autos"),
        "hired": auto.get("hired_autos"),
        "non-owned": auto.get("non_owned_autos"),
    }

    if all(covered.values()):
        return {
            "status": "PASS",
            "reason": "Owned, hired and non-owned autos are checked"}

    if all(value is None for value in covered.values()):
        return {
            "status": "REVIEW_REQUIRED",
            "reason": "Could not read the auto coverage checkboxes"}

    missing = [name for name, value in covered.items() if not value]

    return {
        "status": "REVIEW_REQUIRED",
        "reason": (
            "Auto coverage checkboxes do not show: " + ", ".join(missing)
            + " (may be covered by a scheduled-autos endorsement)")}


def validate_workers_comp_statutory(acord_schema):
    """MSA 3.B.1: Workers' Compensation providing statutory benefits."""

    wc = acord_schema["coverages"]["workers_compensation"]

    if wc.get("per_statute"):
        return {"status": "PASS", "reason": "PER STATUTE is checked"}

    if not wc.get("policy_number"):
        return {
            "status": "MISSING",
            "reason": "No workers' compensation policy on the certificate"}

    return {
        "status": "REVIEW_REQUIRED",
        "reason": "Workers' compensation listed but PER STATUTE not checked"}


# ---------------------------------------------------------
# Policy periods
# ---------------------------------------------------------

def validate_policy_dates(acord_schema, review_date):
    """
    Every required coverage must be in force on the review date.
    Dates come from the extractor as ISO strings (YYYY-MM-DD).
    """

    results = {}

    for coverage_name, coverage in acord_schema.get(
        "coverages", {}
    ).items():

        effective = coverage.get("effective_date")
        expiration = coverage.get("expiration_date")

        # A coverage row left blank on the certificate is reported by its
        # own check (e.g. workers' comp MISSING), not as a date problem.
        if not coverage.get("policy_number") and not expiration:
            continue

        if not expiration:
            results[coverage_name] = {
                "status": "REVIEW_REQUIRED",
                "reason": "Policy expiration date was not extracted",
            }
            continue

        if expiration < review_date:
            results[coverage_name] = {
                "status": "FAIL",
                "reason": (
                    f"Policy expired {expiration} "
                    f"(review date {review_date})"
                ),
            }
            continue

        if effective and effective > review_date:
            results[coverage_name] = {
                "status": "REVIEW_REQUIRED",
                "reason": (
                    f"Policy is not in force until {effective}"
                ),
            }
            continue

        results[coverage_name] = {
            "status": "PASS",
            "reason": f"In force through {expiration}",
        }

    return results


# ---------------------------------------------------------
# Certificate administration
# ---------------------------------------------------------

def validate_certificate_holder(acord_schema):

    check = acord_schema.get("certificate_holder_check")

    if check == "MATCH":
        return {
            "status": "PASS",
            "reason": (
                "Certificate holder is R&O Construction Company, "
                "933 Wall Avenue, Ogden, UT 84404"
            ),
        }

    if check == "MISMATCH":
        return {
            "status": "FAIL",
            "reason": (
                "Certificate holder does not match R&O Construction "
                "Company, 933 Wall Avenue, Ogden, UT 84404: "
                f"{acord_schema.get('certificate_holder')!r}"
            ),
        }

    return {
        "status": "REVIEW_REQUIRED",
        "reason": "Certificate holder was not extracted",
    }


def validate_project_wording(acord_schema):
    """
    R&O's instructions: a named project, owner or job must be confirmed
    by a person; blanket "all jobs" wording needs no confirmation.
    """

    wording = acord_schema.get("project_wording") or {}

    if wording.get("names_specific_project"):
        return {
            "status": "REVIEW_REQUIRED",
            "reason": (
                "Certificate names a specific project or owner "
                "(User Confirmation Required)"
            ),
        }

    return {
        "status": "PASS",
        "reason": (
            "Blanket / all-jobs wording"
            if wording.get("blanket_wording")
            else "No specific project named"
        ),
    }


# ---------------------------------------------------------
# Endorsement evidence (never from the ACORD 25)
# ---------------------------------------------------------

def chart_entries(endorsement_schemas):
    return [
        schema["ro_chart_entry"]
        for schema in endorsement_schemas
        if schema.get("ro_chart_entry")
    ]


def endorsement_tags(endorsement_schemas):
    tags = set()

    for schema in endorsement_schemas:
        tags.update(schema.get("evidence_tags", []))

    return tags


def validate_additional_insured(endorsement_schemas):
    """
    MSA 3.C: coverage at least as broad as ISO CG 20 10 07 04 (ongoing
    operations) together with CG 20 37 07 04 (completed operations).
    Several endorsements may combine to meet this.
    """

    entries = chart_entries(endorsement_schemas)
    tags = endorsement_tags(endorsement_schemas)

    rejected = [
        entry["form_number"]
        for entry in entries
        if entry["chart_decision"] == "REJECT"
        and (
            entry["ongoing_operations"]
            or entry["completed_operations"]
        )
    ]

    if rejected:
        return {
            "status": "FAIL",
            "reason": (
                "R&O's AI chart rejects form(s): "
                + ", ".join(rejected)
            ),
        }

    ongoing = [e for e in entries if e["ongoing_operations"]]
    completed = [e for e in entries if e["completed_operations"]]

    if ongoing and completed:
        needs_review = [
            entry["form_number"]
            for entry in ongoing + completed
            if entry["chart_decision"] == "REVIEW"
            or entry.get("completed_operations_conditional")
        ]

        if needs_review:
            return {
                "status": "REVIEW_REQUIRED",
                "reason": (
                    "Ongoing and completed operations found, but R&O's "
                    "chart flags limitations on: "
                    + ", ".join(sorted(set(needs_review)))
                ),
            }

        return {
            "status": "PASS",
            "reason": (
                "Ongoing operations ("
                + ", ".join(e["form_number"] for e in ongoing)
                + ") and completed operations ("
                + ", ".join(e["form_number"] for e in completed)
                + ") per R&O's chart"
            ),
        }

    if ongoing and not completed:
        return {
            "status": "MISSING",
            "reason": (
                "Ongoing operations endorsement found but no "
                "completed operations (CG 20 37 or equivalent)"
            ),
        }

    if completed and not ongoing:
        return {
            "status": "MISSING",
            "reason": (
                "Completed operations endorsement found but no "
                "ongoing operations (CG 20 10 or equivalent)"
            ),
        }

    if "ADDITIONAL_INSURED" in tags:
        return {
            "status": "REVIEW_REQUIRED",
            "reason": (
                "Additional insured wording found, but the form is not "
                "in R&O's chart; equivalency to CG 20 10 + CG 20 37 "
                "must be confirmed by a reviewer"
            ),
        }

    return {
        "status": "MISSING",
        "reason": "No additional insured endorsement was submitted",
    }


def validate_primary_non_contributory(endorsement_schemas):

    entries = chart_entries(endorsement_schemas)
    tags = endorsement_tags(endorsement_schemas)

    if any(entry["primary_noncontributory"] for entry in entries):
        return {
            "status": "PASS",
            "reason": "Primary and non-contributory per R&O's chart",
        }

    if "PRIMARY_NONCONTRIBUTORY" in tags:
        return {
            "status": "PASS",
            "reason": "Primary and non-contributory endorsement wording found",
        }

    return {
        "status": "MISSING",
        "reason": (
            "No primary and non-contributory endorsement was submitted"
        ),
    }


def validate_waiver(endorsement_schemas):

    if "WAIVER_OF_SUBROGATION" in endorsement_tags(endorsement_schemas):
        return {
            "status": "PASS",
            "reason": "Waiver-of-subrogation endorsement wording found",
        }

    return {
        "status": "MISSING",
        "reason": "No waiver-of-subrogation endorsement was submitted",
    }


# ---------------------------------------------------------
# Roll-up
# ---------------------------------------------------------

def flatten(results, prefix=""):
    """Turns nested check groups into {name: result}."""

    flat = {}

    for name, result in results.items():
        key = f"{prefix}{name}"

        if "status" in result:
            flat[key] = result
        else:
            flat.update(flatten(result, prefix=f"{key}."))

    return flat


def run_compliance_checks(
    acord_schema,
    endorsement_schemas,
    review_date=None
):
    # REVIEW_DATE lets a demo check old sample certificates as of the
    # date they were issued, e.g.  REVIEW_DATE=2017-06-01 python3 main.py
    review_date = (
        review_date
        or os.environ.get("REVIEW_DATE")
        or date.today().isoformat()
    )

    # Reported for transparency only; not used as proof.
    certificate_statements = sorted(
        acord_schema.get("evidence_tags", [])
    )

    combined_evidence = sorted(
        endorsement_tags(endorsement_schemas)
    )

    results = {
        "general_liability":
            validate_general_liability(acord_schema),
        "automobile_liability":
            validate_auto_liability(acord_schema),
        "umbrella":
            validate_umbrella(acord_schema),
        "employers_liability":
            validate_employers_liability(acord_schema),
        "general_liability_form":
            validate_gl_form(acord_schema),
        "automobile_scope":
            validate_auto_scope(acord_schema),
        "workers_compensation_statutory":
            validate_workers_comp_statutory(acord_schema),
        "policy_dates":
            validate_policy_dates(acord_schema, review_date),
        "certificate_holder":
            validate_certificate_holder(acord_schema),
        "project_wording":
            validate_project_wording(acord_schema),
        "additional_insured":
            validate_additional_insured(endorsement_schemas),
        "primary_non_contributory":
            validate_primary_non_contributory(endorsement_schemas),
        "waiver_of_subrogation":
            validate_waiver(endorsement_schemas),
    }

    checks = flatten(results)

    return {
        "overall_status": determine_overall_status(checks),
        "checks": checks,
        "combined_evidence": combined_evidence,
        "certificate_statements": certificate_statements,
        "rule_set": {
            "rule_set_id": RULE_SET["rule_set_id"],
            "version": RULE_SET["version"],
        },
        "review_date": review_date,
    }


def determine_overall_status(
    compliance_results
):

    statuses = [
        result["status"]
        for result
        in compliance_results.values()
    ]

    if "REVIEW_REQUIRED" in statuses:
        return "HUMAN_REVIEW_REQUIRED"

    if "MISSING" in statuses:
        return "INCOMPLETE_SUBMISSION"

    if "FAIL" in statuses:
        return "NON_COMPLIANT"

    return "COMPLIANT"
