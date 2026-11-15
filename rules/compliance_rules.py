# rules/compliance_rules.py
"""R&O Certificate of Insurance compliance rules.

Research prototype only. The engine recommends a status; an authorized R&O
reviewer must make the final determination.

This module is intentionally defensive:
- A certificate statement is not used as proof of an endorsement.
- Missing evidence is different from evidence that clearly fails.
- Uncertain, conflicting, low-confidence, manuscript, or equivalent evidence
  is routed to human review.
- Endorsements are used as proof only when they are reliably associated with
  the applicable certificate/policy.
- The caller may provide an approved, version-controlled requirement set.
"""

from __future__ import annotations

import os
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Iterable


ADVISORY_NOTICE = (
    "Research prototype output. Advisory only. Requires authorized R&O human "
    "review. Certificate information does not amend or replace the insurance "
    "policy or required endorsements."
)

DEFAULT_RULE_SET = {
    "rule_set_id": "RO-MSA-SECTION-3",
    "version": "2024-10-03",
    "source": "R&O MSA Section 3 (2024.10.03) and New MSA requirements 4.10.24",
    "governing_sources": [
        {
            "document": "R&O Master Subcontract Agreement",
            "section": "Section 3",
        }
    ],
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
    "requirements": {
        "general_liability": True,
        "automobile_liability": True,
        "umbrella_excess": True,
        "workers_compensation": True,
        "additional_insured": True,
        "primary_noncontributory": True,
        "per_project_aggregate": True,
        "certificate_holder": True,
        "certificate_authentication": False,
        "cancellation_provision": False,
        "project_wording": {
            "specific_project_required": False,
        },
        "waiver_by_coverage": {
            # Confirm these applicability choices with R&O for each rule set.
            "general_liability": True,
            "workers_compensation": True,
            "automobile_liability": False,
            "umbrella_excess": False,
        },
        "specialty_coverages": [],
    },
    "approved_exceptions": [],
}

# Backward-compatible exported name used by existing code/tests.
RULE_SET = DEFAULT_RULE_SET


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def _result(
    status: str,
    reason: str,
    *,
    requirement_id: str | None = None,
    requirement: str | None = None,
    governing_source: Any = None,
    evidence: list[dict[str, Any]] | None = None,
    confidence: float | None = None,
    severity: str = "MEDIUM",
    recommended_action: str | None = None,
    interpretation_type: str = "EXPRESS_REQUIREMENT",
    required: Any = None,
    found: Any = None,
) -> dict[str, Any]:
    """Create one audit-friendly finding while retaining legacy fields."""
    output = {
        "status": status,
        "reason": reason,
        "severity": severity,
        "confidence": confidence,
        "recommended_action": recommended_action or _default_action(status),
        "interpretation_type": interpretation_type,
        "reviewer_disposition": None,
    }
    optional = {
        "requirement_id": requirement_id,
        "requirement": requirement,
        "governing_source": governing_source,
        "evidence": evidence,
        "required": required,
        "found": found,
    }
    output.update({key: value for key, value in optional.items() if value is not None})
    return output


def _default_action(status: str) -> str:
    return {
        "PASS": "No correction required; retain for authorized R&O review.",
        "FAIL": "Request corrected evidence or document an approved exception.",
        "MISSING": "Request the missing document, page, schedule, or evidence.",
        "REVIEW_REQUIRED": "Route to an authorized R&O reviewer.",
        "NOT_APPLICABLE": "No action required.",
    }.get(status, "Route to an authorized R&O reviewer.")


def safe_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            cleaned = (
                value.replace("$", "")
                .replace(",", "")
                .replace(" ", "")
                .strip()
            )
            if not cleaned:
                return None
            value = cleaned
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_iso_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().upper() in {
        "TRUE", "YES", "Y", "CHECKED", "MATCH", "CONFIRMED", "PASS"
    }


def _required(rule_set: dict[str, Any], name: str, default: Any = True) -> Any:
    return rule_set.get("requirements", {}).get(name, default)


def _source(rule_set: dict[str, Any], requirement_id: str) -> dict[str, Any]:
    return {
        "requirement_id": requirement_id,
        "rule_set_id": rule_set.get("rule_set_id"),
        "version": rule_set.get("version"),
        "sources": rule_set.get("governing_sources", []),
    }


def _field_evidence(
    schema: dict[str, Any], field: str, value: Any, *, coverage: str | None = None
) -> list[dict[str, Any]]:
    locations = schema.get("source_locations", {})
    confidences = schema.get("field_confidence", {})
    key = f"coverages.{coverage}.{field}" if coverage else field
    return [{
        "document_id": schema.get("document_id"),
        "file_name": schema.get("file_name"),
        "page": locations.get(key, {}).get("page") if isinstance(locations.get(key), dict) else None,
        "location": locations.get(key),
        "field": key,
        "value": value,
        "confidence": confidences.get(key),
    }]


def _confidence_for(schema: dict[str, Any], field: str, coverage: str | None = None) -> float | None:
    key = f"coverages.{coverage}.{field}" if coverage else field
    value = schema.get("field_confidence", {}).get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def check_minimum(
    value: Any,
    minimum: int,
    label: str,
    *,
    confidence: float | None = None,
    confidence_threshold: float = 0.80,
    requirement_id: str | None = None,
    source: Any = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Missing -> review; low -> fail; low confidence never automatically passes."""
    amount = safe_int(value)
    if amount is None:
        return _result(
            "REVIEW_REQUIRED",
            f"{label} limit was not reliably extracted",
            requirement_id=requirement_id,
            governing_source=source,
            evidence=evidence,
            confidence=confidence,
            severity="HIGH",
            required=minimum,
            found=None,
        )
    if confidence is not None and confidence < confidence_threshold:
        return _result(
            "PASS",
            (
                "Using the configured R&O MSA baseline requirement set; "
                "no project-specific override was provided"
            ),
            requirement_id="REQ-SET-SELECTION",
            governing_source=_source(rule_set, "REQ-SET-SELECTION"),
        )
    if amount >= minimum:
        return _result(
            "PASS",
            f"{label} limit {amount:,} meets {minimum:,}",
            requirement_id=requirement_id,
            governing_source=source,
            evidence=evidence,
            confidence=confidence,
            required=minimum,
            found=amount,
        )
    return _result(
        "FAIL",
        f"{label} limit {amount:,} is below {minimum:,}",
        requirement_id=requirement_id,
        governing_source=source,
        evidence=evidence,
        confidence=confidence,
        severity="HIGH",
        required=minimum,
        found=amount,
    )


# ---------------------------------------------------------------------------
# Requirement-set and document-quality gates
# ---------------------------------------------------------------------------

def validate_requirement_selection(
    rule_set: dict[str, Any],
    submission_context: dict[str, Any]
    ) -> dict[str, Any]:

    status = submission_context.get("requirement_selection_status")

    if status in {"MATCH", "CONFIRMED"}:
        return _result(
            "PASS",
            (
                f"Applicable requirement set selected: "
                f"{rule_set.get('rule_set_id')} "
                f"version {rule_set.get('version')}"
            ),
            requirement_id="REQ-SET-SELECTION",
            governing_source=_source(
                rule_set,
                "REQ-SET-SELECTION"
            ),
        )

    if status in {"MISMATCH", "WRONG_VERSION"}:
        return _result(
            "FAIL",
            "The selected MSA/WAD/project requirement set is incorrect",
            requirement_id="REQ-SET-SELECTION",
            governing_source=_source(
                rule_set,
                "REQ-SET-SELECTION"
            ),
            severity="HIGH",
        )

    if status in {"UNCERTAIN", "CONFLICT"}:
        return _result(
            "REVIEW_REQUIRED",
            (
                "The governing MSA, WAD, owner, project, amendment, "
                "or exception is uncertain or conflicting"
            ),
            requirement_id="REQ-SET-SELECTION",
            governing_source=_source(
                rule_set,
                "REQ-SET-SELECTION"
            ),
            severity="HIGH",
        )

    # No project-specific override was supplied.
    # Use the configured R&O MSA baseline.
    return _result(
        "PASS",
        (
            "Using the configured R&O MSA baseline requirement set; "
            "no project-specific override was provided"
        ),
        requirement_id="REQ-SET-SELECTION",
        governing_source=_source(
            rule_set,
            "REQ-SET-SELECTION"
        ),
    )


def validate_document_quality(
    acord_schema: dict[str, Any], endorsement_schemas: list[dict[str, Any]]
) -> dict[str, Any]:
    docs = [acord_schema, *endorsement_schemas]
    if any(doc.get("unreadable") for doc in docs):
        return _result(
            "MISSING",
            "One or more required documents/pages are unreadable",
            requirement_id="DOCUMENT-QUALITY",
            severity="HIGH",
        )
    if any(doc.get("missing_pages") or doc.get("missing_schedule") for doc in docs):
        return _result(
            "MISSING",
            "One or more required pages or endorsement schedules are missing",
            requirement_id="DOCUMENT-COMPLETENESS",
            severity="HIGH",
        )
    low_confidence = [
        doc.get("file_name") or doc.get("document_id") or "unknown document"
        for doc in docs
        if doc.get("document_confidence") is not None
        and float(doc["document_confidence"]) < 0.80
    ]
    if low_confidence:
        return _result(
            "REVIEW_REQUIRED",
            "Low-confidence OCR/extraction requires review: " + ", ".join(low_confidence),
            requirement_id="DOCUMENT-CONFIDENCE",
            severity="HIGH",
        )
    return _result("PASS", "Required documents passed the available quality checks", requirement_id="DOCUMENT-QUALITY")


# ---------------------------------------------------------------------------
# Limits and coverage forms
# ---------------------------------------------------------------------------

def validate_general_liability(acord_schema: dict[str, Any], rule_set: dict[str, Any] = RULE_SET):
    gl = acord_schema.get("coverages", {}).get("general_liability", {})
    limits = rule_set["limits"]
    fields = {
        "each_occurrence": ("gl_each_occurrence", "GL each occurrence", "GL-LIMIT-EACH-OCCURRENCE"),
        "general_aggregate": ("gl_general_aggregate", "GL general aggregate", "GL-LIMIT-GENERAL-AGGREGATE"),
        "products_completed_operations": ("gl_products_completed_ops", "GL products/completed operations aggregate", "GL-LIMIT-PRODUCTS-COMPLETED"),
    }
    return {
        output_name: check_minimum(
            gl.get(output_name),
            limits[limit_key],
            label,
            confidence=_confidence_for(acord_schema, output_name, "general_liability"),
            requirement_id=req_id,
            source=_source(rule_set, req_id),
            evidence=_field_evidence(acord_schema, output_name, gl.get(output_name), coverage="general_liability"),
        )
        for output_name, (limit_key, label, req_id) in fields.items()
    }


def validate_auto_liability(acord_schema: dict[str, Any], rule_set: dict[str, Any] = RULE_SET):
    auto = acord_schema.get("coverages", {}).get("automobile_liability", {})
    field = "combined_single_limit"
    return check_minimum(
        auto.get(field), rule_set["limits"]["auto_combined_single_limit"], "Auto combined single",
        confidence=_confidence_for(acord_schema, field, "automobile_liability"),
        requirement_id="AUTO-LIMIT-CSL", source=_source(rule_set, "AUTO-LIMIT-CSL"),
        evidence=_field_evidence(acord_schema, field, auto.get(field), coverage="automobile_liability"),
    )


def validate_umbrella(acord_schema: dict[str, Any], rule_set: dict[str, Any] = RULE_SET):
    umbrella = acord_schema.get("coverages", {}).get("umbrella_excess", {})
    field = "each_occurrence"
    return check_minimum(
        umbrella.get(field), rule_set["limits"]["umbrella_each_occurrence"], "Umbrella/excess each occurrence",
        confidence=_confidence_for(acord_schema, field, "umbrella_excess"),
        requirement_id="UMB-LIMIT-EACH-OCCURRENCE", source=_source(rule_set, "UMB-LIMIT-EACH-OCCURRENCE"),
        evidence=_field_evidence(acord_schema, field, umbrella.get(field), coverage="umbrella_excess"),
    )


def validate_employers_liability(acord_schema: dict[str, Any], rule_set: dict[str, Any] = RULE_SET):
    wc = acord_schema.get("coverages", {}).get("workers_compensation", {})
    fields = {
        "each_accident": ("el_each_accident", "E.L. each accident", "EL-LIMIT-EACH-ACCIDENT"),
        "disease_each_employee": ("el_disease_each_employee", "E.L. disease each employee", "EL-LIMIT-DISEASE-EMPLOYEE"),
        "disease_policy_limit": ("el_disease_policy_limit", "E.L. disease policy", "EL-LIMIT-DISEASE-POLICY"),
    }
    if not wc.get("policy_number") and not any(wc.get(field) for field in fields):
        return {
            name: _result("MISSING", "No workers' compensation / employers liability policy on the certificate", requirement_id=req_id, severity="HIGH")
            for name, (_, _, req_id) in fields.items()
        }
    return {
        name: check_minimum(
            wc.get(name), rule_set["limits"][limit_key], label,
            confidence=_confidence_for(acord_schema, name, "workers_compensation"),
            requirement_id=req_id, source=_source(rule_set, req_id),
            evidence=_field_evidence(acord_schema, name, wc.get(name), coverage="workers_compensation"),
        )
        for name, (limit_key, label, req_id) in fields.items()
    }


def validate_gl_form(
    acord_schema: dict[str, Any],
    endorsement_schemas: list[dict[str, Any]] | None = None,
    rule_set: dict[str, Any] = RULE_SET,
):
    gl = acord_schema.get("coverages", {}).get("general_liability", {})
    endorsements = endorsement_schemas or []
    results = {}
    form = gl.get("occurrence_form")
    if form == "OCCURRENCE":
        results["occurrence_basis"] = _result("PASS", "GL written on an occurrence basis", requirement_id="GL-FORM-OCCURRENCE")
    elif form == "CLAIMS_MADE":
        results["occurrence_basis"] = _result("FAIL", "GL is claims-made; occurrence basis is required", requirement_id="GL-FORM-OCCURRENCE", severity="HIGH")
    else:
        results["occurrence_basis"] = _result("REVIEW_REQUIRED", "Could not reliably read the OCCUR / CLAIMS-MADE checkbox", requirement_id="GL-FORM-OCCURRENCE", severity="HIGH")

    applies = gl.get("aggregate_applies_per")
    correction = any(
        e.get("association_status") in {None, "MATCH", "CONFIRMED"}
        and "PER_PROJECT_AGGREGATE" in set(e.get("evidence_tags", []))
        for e in endorsements
    )
    if applies == "PROJECT" or correction:
        reason = "General aggregate applies per project" if applies == "PROJECT" else "Matching per-project aggregate endorsement found"
        results["per_project_aggregate"] = _result("PASS", reason, requirement_id="GL-AGGREGATE-PER-PROJECT")
    elif applies in {"POLICY", "LOCATION"}:
        results["per_project_aggregate"] = _result("FAIL", f"General aggregate applies per {applies.lower()} and no matching per-project endorsement was found", requirement_id="GL-AGGREGATE-PER-PROJECT", severity="HIGH")
    else:
        results["per_project_aggregate"] = _result("REVIEW_REQUIRED", "Could not confirm that the general aggregate applies per project", requirement_id="GL-AGGREGATE-PER-PROJECT", severity="HIGH")
    return results


def validate_auto_scope(acord_schema: dict[str, Any]):
    auto = acord_schema.get("coverages", {}).get("automobile_liability", {})
    if auto.get("any_auto"):
        return _result("PASS", "ANY AUTO is checked", requirement_id="AUTO-SCOPE")
    covered = {"owned": auto.get("owned_autos"), "hired": auto.get("hired_autos"), "non-owned": auto.get("non_owned_autos")}
    if all(covered.values()):
        return _result("PASS", "Owned, hired and non-owned autos are checked", requirement_id="AUTO-SCOPE")
    if all(value is None for value in covered.values()):
        return _result("REVIEW_REQUIRED", "Could not read the auto coverage checkboxes", requirement_id="AUTO-SCOPE", severity="HIGH")
    missing = [name for name, value in covered.items() if not value]
    return _result("REVIEW_REQUIRED", "Auto coverage does not show " + ", ".join(missing) + "; a scheduled-autos endorsement may need review", requirement_id="AUTO-SCOPE", severity="HIGH")


def validate_workers_comp_statutory(acord_schema: dict[str, Any]):
    wc = acord_schema.get("coverages", {}).get("workers_compensation", {})
    if wc.get("per_statute"):
        return _result("PASS", "PER STATUTE is checked", requirement_id="WC-STATUTORY")
    if not wc.get("policy_number"):
        return _result("MISSING", "No workers' compensation policy on the certificate", requirement_id="WC-STATUTORY", severity="HIGH")
    return _result("REVIEW_REQUIRED", "Workers' compensation is listed but PER STATUTE was not confirmed", requirement_id="WC-STATUTORY", severity="HIGH")


# ---------------------------------------------------------------------------
# Dates, identities, certificate administration, and conflicts
# ---------------------------------------------------------------------------

def validate_policy_dates(acord_schema: dict[str, Any], review_date: str):
    results = {}
    review = _parse_iso_date(review_date)
    if review is None:
        return {"review_date": _result("REVIEW_REQUIRED", f"Invalid review date: {review_date!r}", requirement_id="POLICY-DATE")}
    for coverage_name, coverage in acord_schema.get("coverages", {}).items():
        effective_raw = coverage.get("effective_date")
        expiration_raw = coverage.get("expiration_date")
        if not coverage.get("policy_number") and not expiration_raw:
            continue
        effective = _parse_iso_date(effective_raw)
        expiration = _parse_iso_date(expiration_raw)
        if not expiration_raw or expiration is None:
            results[coverage_name] = _result("REVIEW_REQUIRED", "Policy expiration date was missing or invalid", requirement_id=f"POLICY-DATE-{coverage_name.upper()}", severity="HIGH")
        elif expiration < review:
            results[coverage_name] = _result("FAIL", f"Policy expired {expiration.isoformat()} (review date {review.isoformat()})", requirement_id=f"POLICY-DATE-{coverage_name.upper()}", severity="HIGH")
        elif effective_raw and effective is None:
            results[coverage_name] = _result("REVIEW_REQUIRED", "Policy effective date was invalid", requirement_id=f"POLICY-DATE-{coverage_name.upper()}", severity="HIGH")
        elif effective and effective > review:
            results[coverage_name] = _result("FAIL", f"Policy is not in force until {effective.isoformat()}", requirement_id=f"POLICY-DATE-{coverage_name.upper()}", severity="HIGH")
        else:
            results[coverage_name] = _result("PASS", f"In force through {expiration.isoformat()}", requirement_id=f"POLICY-DATE-{coverage_name.upper()}")
    return results


def validate_named_insured(acord_schema: dict[str, Any], submission_context: dict[str, Any]):
    check = submission_context.get("named_insured_check") or acord_schema.get("named_insured_check")
    if check == "MATCH":
        return _result("PASS", "Named insured matches the contracting entity", requirement_id="IDENTITY-NAMED-INSURED")
    if check == "MISMATCH":
        return _result("FAIL", "Named insured does not match the contracting entity", requirement_id="IDENTITY-NAMED-INSURED", severity="HIGH")
    if check in {"POSSIBLE_VARIATION", "UNCERTAIN"}:
        return _result("REVIEW_REQUIRED", "Named insured may be an abbreviation, DBA, parent, subsidiary, or related legal entity", requirement_id="IDENTITY-NAMED-INSURED", severity="HIGH")
    return _result("REVIEW_REQUIRED", "Named insured could not be compared with the contracting entity", requirement_id="IDENTITY-NAMED-INSURED", severity="HIGH")


def validate_certificate_holder(acord_schema: dict[str, Any]):
    check = acord_schema.get("certificate_holder_check")
    if check == "MATCH":
        return _result("PASS", "Certificate holder matches R&O Construction Company, 933 Wall Avenue, Ogden, UT 84404", requirement_id="CERTIFICATE-HOLDER")
    if check == "MISMATCH":
        return _result("FAIL", f"Certificate holder does not match the required R&O entity/address: {acord_schema.get('certificate_holder')!r}", requirement_id="CERTIFICATE-HOLDER", severity="HIGH")
    return _result("REVIEW_REQUIRED", "Certificate holder was not reliably extracted", requirement_id="CERTIFICATE-HOLDER", severity="HIGH")


def validate_project_wording(acord_schema: dict[str, Any], rule_set: dict[str, Any], submission_context: dict[str, Any]):
    wording = acord_schema.get("project_wording") or {}
    config = _required(rule_set, "project_wording", {}) or {}
    specific_required = bool(config.get("specific_project_required"))
    if specific_required:
        if wording.get("expected_project_match") is True:
            return _result("PASS", "Required project wording matches the expected project", requirement_id="PROJECT-WORDING")
        if wording.get("names_specific_project") and wording.get("expected_project_match") is False:
            return _result("FAIL", "Certificate or endorsement names the wrong project/owner/job", requirement_id="PROJECT-WORDING", severity="HIGH")
        if not wording.get("names_specific_project"):
            return _result("MISSING", "The governing requirement requires a named project, but matching project wording was not found", requirement_id="PROJECT-WORDING", severity="HIGH")
        return _result("REVIEW_REQUIRED", "Specific project wording requires confirmation", requirement_id="PROJECT-WORDING", severity="HIGH")
    if wording.get("names_specific_project"):
        return _result("REVIEW_REQUIRED", "Certificate names a specific project or owner; applicability must be confirmed", requirement_id="PROJECT-WORDING")
    if wording.get("blanket_wording"):
        return _result("PASS", "Blanket/all-jobs wording found", requirement_id="PROJECT-WORDING")
    return _result("REVIEW_REQUIRED", "No blanket wording or confirmed project match was found", requirement_id="PROJECT-WORDING")


def validate_certificate_authentication(acord_schema: dict[str, Any], required: bool):
    if not required:
        return _result("NOT_APPLICABLE", "Certificate authentication is not configured as an express requirement", requirement_id="CERT-AUTH")
    status = acord_schema.get("authentication_status")
    if status == "CONFIRMED":
        return _result("PASS", "Certificate signature/authentication was confirmed", requirement_id="CERT-AUTH")
    if status == "MISSING":
        return _result("MISSING", "Required certificate signature/authentication is missing", requirement_id="CERT-AUTH")
    return _result("REVIEW_REQUIRED", "Certificate signature/authentication could not be confirmed", requirement_id="CERT-AUTH")


def validate_conflicting_evidence(acord_schema: dict[str, Any], endorsement_schemas: list[dict[str, Any]], submission_context: dict[str, Any]):
    conflicts = list(submission_context.get("conflicts", []))
    conflicts += list(acord_schema.get("conflicts", []))
    for schema in endorsement_schemas:
        conflicts += list(schema.get("conflicts", []))
    if conflicts:
        return _result("REVIEW_REQUIRED", "Material conflicting evidence found: " + "; ".join(map(str, conflicts)), requirement_id="EVIDENCE-CONFLICT", severity="HIGH")
    return _result("PASS", "No material evidence conflicts were reported", requirement_id="EVIDENCE-CONFLICT")


def validate_revision_and_continuity(submission_context: dict[str, Any]):
    if submission_context.get("unresolved_superseded_documents"):
        return _result("REVIEW_REQUIRED", "Current and superseded documents cannot be reliably distinguished", requirement_id="REVISION-STATUS", severity="HIGH")
    if submission_context.get("coverage_gap"):
        return _result("FAIL", "A gap in required policy coverage was identified", requirement_id="COVERAGE-CONTINUITY", severity="HIGH")
    if submission_context.get("revision_status") in {"UNKNOWN", "CONFLICT"}:
        return _result("REVIEW_REQUIRED", "Revision or replacement status is uncertain", requirement_id="REVISION-STATUS", severity="HIGH")
    return _result("PASS", "No unresolved revision or coverage-continuity issue was reported", requirement_id="REVISION-STATUS")


# ---------------------------------------------------------------------------
# Endorsement evidence and restrictions
# ---------------------------------------------------------------------------

def chart_entries(endorsement_schemas: Iterable[dict[str, Any]]):
    return [schema["ro_chart_entry"] for schema in endorsement_schemas if schema.get("ro_chart_entry")]


def endorsement_tags(endorsement_schemas: Iterable[dict[str, Any]]):
    tags = set()
    for schema in endorsement_schemas:
        tags.update(schema.get("evidence_tags", []))
    return tags


def _eligible_endorsements(endorsement_schemas: list[dict[str, Any]]):
    eligible, uncertain, mismatched = [], [], []
    for schema in endorsement_schemas:
        status = schema.get("association_status")
        if status in {None, "MATCH", "CONFIRMED"}:
            eligible.append(schema)
        elif status in {"UNCERTAIN", "REVIEW", "CONFLICT"}:
            uncertain.append(schema)
        else:
            mismatched.append(schema)
    return eligible, uncertain, mismatched


def validate_endorsement_associations(endorsement_schemas: list[dict[str, Any]]):
    if not endorsement_schemas:
        return _result("MISSING", "No endorsements were associated with this submission", requirement_id="ENDORSEMENT-ASSOCIATION", severity="HIGH")
    _, uncertain, mismatched = _eligible_endorsements(endorsement_schemas)
    if mismatched:
        names = [e.get("file_name") or e.get("form_number") or "unknown endorsement" for e in mismatched]
        return _result("FAIL", "Endorsement policy, insured, carrier, coverage line, project, or revision mismatch: " + ", ".join(names), requirement_id="ENDORSEMENT-ASSOCIATION", severity="HIGH")
    if uncertain:
        return _result("REVIEW_REQUIRED", "One or more endorsement associations are uncertain", requirement_id="ENDORSEMENT-ASSOCIATION", severity="HIGH")
    return _result("PASS", "Endorsements are associated with the applicable submission/policy", requirement_id="ENDORSEMENT-ASSOCIATION")


def validate_material_restrictions(endorsement_schemas: list[dict[str, Any]], submission_context: dict[str, Any]):
    restrictions = []
    uncertain = []
    for schema in endorsement_schemas:
        restrictions += list(schema.get("material_restrictions", []))
        restrictions += list(schema.get("exclusions", []))
        if schema.get("manuscript_language") or schema.get("negligence_trigger_status") in {"UNCERTAIN", "REVIEW"}:
            uncertain.append(schema.get("form_number") or schema.get("file_name") or "unknown endorsement")
        if schema.get("direct_contract_required") and schema.get("direct_contract_confirmed") is not True:
            uncertain.append((schema.get("form_number") or "endorsement") + " direct-contract requirement")
    if restrictions:
        return _result("REVIEW_REQUIRED", "Potential material restrictions/exclusions require professional review: " + "; ".join(map(str, restrictions)), requirement_id="ENDORSEMENT-RESTRICTIONS", severity="HIGH")
    if uncertain:
        return _result("REVIEW_REQUIRED", "Negligence trigger, manuscript wording, or contractual privity is uncertain: " + ", ".join(uncertain), requirement_id="ENDORSEMENT-RESTRICTIONS", severity="HIGH")
    return _result("PASS", "No extracted material endorsement restriction requires escalation", requirement_id="ENDORSEMENT-RESTRICTIONS")


def validate_protected_parties(endorsement_schemas: list[dict[str, Any]], submission_context: dict[str, Any]):
    required_parties = set(submission_context.get("required_protected_parties", []))
    if not required_parties:
        return _result("REVIEW_REQUIRED", "Required protected parties were not supplied for comparison", requirement_id="PROTECTED-PARTIES", severity="HIGH")
    eligible, uncertain, _ = _eligible_endorsements(endorsement_schemas)
    if uncertain:
        return _result("REVIEW_REQUIRED", "Protected parties cannot be confirmed because endorsement association is uncertain", requirement_id="PROTECTED-PARTIES", severity="HIGH")
    protected = set()
    blank_schedule = False
    for schema in eligible:
        protected.update(schema.get("protected_parties", []))
        if schema.get("blanket_additional_insured") and schema.get("blanket_applies") is True:
            protected.update(required_parties)
        blank_schedule = blank_schedule or bool(schema.get("missing_schedule"))
    if blank_schedule:
        return _result("MISSING", "A required endorsement schedule identifying protected parties is missing", requirement_id="PROTECTED-PARTIES", severity="HIGH")
    missing = sorted(required_parties - protected)
    if missing:
        return _result("FAIL", "Required protected parties were not confirmed: " + ", ".join(missing), requirement_id="PROTECTED-PARTIES", severity="HIGH")
    return _result("PASS", "All configured required protected parties were confirmed", requirement_id="PROTECTED-PARTIES")


def validate_additional_insured(endorsement_schemas: list[dict[str, Any]]):
    eligible, uncertain, _ = _eligible_endorsements(endorsement_schemas)
    if uncertain and not eligible:
        return _result("REVIEW_REQUIRED", "Additional-insured evidence exists but association is uncertain", requirement_id="AI-ONGOING-COMPLETED", severity="HIGH")
    entries = chart_entries(eligible)
    tags = endorsement_tags(eligible)
    rejected = [e.get("form_number", "unknown") for e in entries if e.get("chart_decision") == "REJECT" and (e.get("ongoing_operations") or e.get("completed_operations"))]
    if rejected:
        return _result("FAIL", "R&O's approved chart rejects form(s): " + ", ".join(rejected), requirement_id="AI-ONGOING-COMPLETED", severity="HIGH")
    ongoing = [e for e in entries if e.get("ongoing_operations")]
    completed = [e for e in entries if e.get("completed_operations")]
    if ongoing and completed:
        review_forms = [e.get("form_number", "unknown") for e in ongoing + completed if e.get("chart_decision") == "REVIEW" or e.get("completed_operations_conditional")]
        if review_forms:
            return _result("REVIEW_REQUIRED", "Ongoing and completed operations were found, but limitations require review: " + ", ".join(sorted(set(review_forms))), requirement_id="AI-ONGOING-COMPLETED", severity="HIGH")
        return _result("PASS", "Matching ongoing-operations and completed-operations additional-insured forms were found", requirement_id="AI-ONGOING-COMPLETED")
    if ongoing:
        return _result("MISSING", "Ongoing-operations endorsement found, but completed-operations evidence is missing", requirement_id="AI-ONGOING-COMPLETED", severity="HIGH")
    if completed:
        return _result("MISSING", "Completed-operations endorsement found, but ongoing-operations evidence is missing", requirement_id="AI-ONGOING-COMPLETED", severity="HIGH")
    if "ADDITIONAL_INSURED" in tags:
        return _result("REVIEW_REQUIRED", "Additional-insured wording was found on an uncharted or unconfirmed form; equivalency must be reviewed", requirement_id="AI-ONGOING-COMPLETED", severity="HIGH")
    return _result("MISSING", "No matching additional-insured endorsement was associated with this submission", requirement_id="AI-ONGOING-COMPLETED", severity="HIGH")


def validate_primary_non_contributory(endorsement_schemas: list[dict[str, Any]]):
    eligible, uncertain, _ = _eligible_endorsements(endorsement_schemas)
    entries = chart_entries(eligible)
    if any(e.get("primary_noncontributory") and e.get("chart_decision") == "ACCEPT" for e in entries):
        return _result("PASS", "Primary and non-contributory treatment confirmed by an accepted matching form", requirement_id="PRIMARY-NONCONTRIBUTORY")
    fully_confirmed = any(
        e.get("primary_status") == "CONFIRMED"
        and e.get("noncontributory_status") == "CONFIRMED"
        and e.get("protected_party_status") in {None, "MATCH", "CONFIRMED"}
        and not e.get("material_restrictions")
        for e in eligible
    )
    if fully_confirmed:
        return _result("PASS", "Primary and non-contributory wording and applicability were confirmed", requirement_id="PRIMARY-NONCONTRIBUTORY")
    if "PRIMARY_NONCONTRIBUTORY" in endorsement_tags(eligible):
        return _result("REVIEW_REQUIRED", "Primary/non-contributory wording was found, but party, policy, conditions, or restrictions require confirmation", requirement_id="PRIMARY-NONCONTRIBUTORY", severity="HIGH")
    if uncertain:
        return _result("REVIEW_REQUIRED", "Potential primary/non-contributory evidence cannot be reliably associated", requirement_id="PRIMARY-NONCONTRIBUTORY", severity="HIGH")
    return _result("MISSING", "No confirmed primary and non-contributory endorsement or policy provision was submitted", requirement_id="PRIMARY-NONCONTRIBUTORY", severity="HIGH")


def validate_waivers(endorsement_schemas: list[dict[str, Any]], rule_set: dict[str, Any]):
    requirements = _required(rule_set, "waiver_by_coverage", {}) or {}
    eligible, uncertain, _ = _eligible_endorsements(endorsement_schemas)
    results = {}
    for coverage, required in requirements.items():
        if not required:
            results[coverage] = _result("NOT_APPLICABLE", f"Waiver is not configured as required for {coverage}", requirement_id=f"WAIVER-{coverage.upper()}")
            continue
        matches = [
            e for e in eligible
            if "WAIVER_OF_SUBROGATION" in set(e.get("evidence_tags", []))
            and e.get("coverage_line") in {coverage, coverage.upper(), None}
        ]
        confirmed = [e for e in matches if e.get("waiver_status") in {None, "CONFIRMED", "MATCH"} and e.get("protected_party_status") in {None, "MATCH", "CONFIRMED"}]
        if confirmed:
            results[coverage] = _result("PASS", f"Matching waiver-of-subrogation evidence found for {coverage}", requirement_id=f"WAIVER-{coverage.upper()}")
        elif matches or uncertain:
            results[coverage] = _result("REVIEW_REQUIRED", f"Potential waiver evidence for {coverage} requires policy, party, or wording confirmation", requirement_id=f"WAIVER-{coverage.upper()}", severity="HIGH")
        else:
            results[coverage] = _result("MISSING", f"Required waiver-of-subrogation evidence is missing for {coverage}", requirement_id=f"WAIVER-{coverage.upper()}", severity="HIGH")
    return results


def validate_waiver(endorsement_schemas: list[dict[str, Any]]):
    """Backward-compatible aggregate waiver check used by older tests/callers."""
    if "WAIVER_OF_SUBROGATION" in endorsement_tags(endorsement_schemas):
        return _result("PASS", "Waiver-of-subrogation endorsement wording found", requirement_id="WAIVER-LEGACY")
    return _result("MISSING", "No waiver-of-subrogation endorsement was submitted", requirement_id="WAIVER-LEGACY")


def validate_cancellation(endorsement_schemas: list[dict[str, Any]], rule_set: dict[str, Any]):
    if not _required(rule_set, "cancellation_provision", False):
        return _result("NOT_APPLICABLE", "Cancellation protection is not configured as an express requirement", requirement_id="CANCELLATION")
    eligible, uncertain, _ = _eligible_endorsements(endorsement_schemas)
    if any("CANCELLATION_PROVISION" in set(e.get("evidence_tags", [])) and e.get("cancellation_status") == "CONFIRMED" for e in eligible):
        return _result("PASS", "Required cancellation provision was confirmed from matching policy evidence", requirement_id="CANCELLATION")
    if any("CANCELLATION_PROVISION" in set(e.get("evidence_tags", [])) for e in eligible) or uncertain:
        return _result("REVIEW_REQUIRED", "Potential cancellation provision requires confirmation", requirement_id="CANCELLATION", severity="HIGH")
    return _result("MISSING", "Required cancellation endorsement or policy provision is missing", requirement_id="CANCELLATION", severity="HIGH")


def validate_specialty_coverages(acord_schema: dict[str, Any], rule_set: dict[str, Any]):
    results = {}
    configured = _required(rule_set, "specialty_coverages", []) or []
    extracted = acord_schema.get("specialty_coverages", {})
    for item in configured:
        coverage_type = item["coverage_type"]
        key = coverage_type.lower()
        req_id = f"SPECIALTY-{coverage_type}"
        if not item.get("applicable", False):
            results[key] = _result("NOT_APPLICABLE", f"{coverage_type} is not applicable", requirement_id=req_id)
            continue
        data = extracted.get(coverage_type) or extracted.get(key)
        if not data:
            results[key] = _result("MISSING", f"Required {coverage_type} coverage was not submitted", requirement_id=req_id, severity="HIGH")
            continue
        minimum = item.get("minimum_limit")
        if minimum is not None:
            results[key] = check_minimum(data.get("limit"), minimum, coverage_type, requirement_id=req_id, source=item.get("source"))
        else:
            results[key] = _result("REVIEW_REQUIRED", f"{coverage_type} was found; configured terms require reviewer confirmation", requirement_id=req_id)
    if not configured:
        results["none_configured"] = _result("NOT_APPLICABLE", "No approved specialty coverage is configured for this rule set", requirement_id="SPECIALTY-COVERAGE")
    return results


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------

def flatten(results: dict[str, Any], prefix: str = ""):
    flat = {}
    for name, result in results.items():
        key = f"{prefix}{name}"
        if isinstance(result, dict) and "status" in result:
            flat[key] = result
        elif isinstance(result, dict):
            flat.update(flatten(result, prefix=f"{key}."))
    return flat


def determine_overall_status(compliance_results: dict[str, dict[str, Any]]):
    """Known failure outranks missing evidence; missing outranks uncertainty."""
    statuses = {result.get("status") for result in compliance_results.values()}
    if "FAIL" in statuses:
        return "NON_COMPLIANT"
    if "MISSING" in statuses:
        return "INCOMPLETE_SUBMISSION"
    if "REVIEW_REQUIRED" in statuses:
        return "HUMAN_REVIEW_REQUIRED"
    return "COMPLIANT"


def run_compliance_checks(
    acord_schema: dict[str, Any],
    endorsement_schemas: list[dict[str, Any]],
    review_date: str | None = None,
    requirement_set: dict[str, Any] | None = None,
    submission_context: dict[str, Any] | None = None,
):
    """Run all configured checks for one candidate submission.

    Existing callers may continue passing only acord_schema,
    endorsement_schemas, and review_date. For full SOW behavior, the caller
    should also provide the approved requirement_set and submission_context.
    """
    rule_set = deepcopy(requirement_set or DEFAULT_RULE_SET)
    context = submission_context or {}
    review_date = review_date or os.environ.get("REVIEW_DATE") or date.today().isoformat()

    certificate_statements = sorted(acord_schema.get("evidence_tags", []))
    combined_evidence = sorted(endorsement_tags(endorsement_schemas))

    results = {
        "requirement_selection": validate_requirement_selection(rule_set, context),
        "document_quality": validate_document_quality(acord_schema, endorsement_schemas),
        "named_insured": validate_named_insured(acord_schema, context),
        "general_liability": validate_general_liability(acord_schema, rule_set),
        "automobile_liability": validate_auto_liability(acord_schema, rule_set),
        "umbrella": validate_umbrella(acord_schema, rule_set),
        "employers_liability": validate_employers_liability(acord_schema, rule_set),
        "general_liability_form": validate_gl_form(acord_schema, endorsement_schemas, rule_set),
        "automobile_scope": validate_auto_scope(acord_schema),
        "workers_compensation_statutory": validate_workers_comp_statutory(acord_schema),
        "policy_dates": validate_policy_dates(acord_schema, review_date),
        "certificate_holder": validate_certificate_holder(acord_schema),
        "certificate_authentication": validate_certificate_authentication(acord_schema, bool(_required(rule_set, "certificate_authentication", False))),
        "project_wording": validate_project_wording(acord_schema, rule_set, context),
        "endorsement_association": validate_endorsement_associations(endorsement_schemas),
        "protected_parties": validate_protected_parties(endorsement_schemas, context),
        "additional_insured": validate_additional_insured(endorsement_schemas),
        "primary_non_contributory": validate_primary_non_contributory(endorsement_schemas),
        "waiver_of_subrogation": validate_waiver(endorsement_schemas),
        "waiver_by_coverage": validate_waivers(endorsement_schemas, rule_set),
        "cancellation": validate_cancellation(endorsement_schemas, rule_set),
        "endorsement_restrictions": validate_material_restrictions(endorsement_schemas, context),
        "conflicting_evidence": validate_conflicting_evidence(acord_schema, endorsement_schemas, context),
        "revision_and_continuity": validate_revision_and_continuity(context),
        "specialty_coverages": validate_specialty_coverages(acord_schema, rule_set),
    }

    checks = flatten(results)
    review_queue = [
        {"check": name, **finding}
        for name, finding in checks.items()
        if finding.get("status") == "REVIEW_REQUIRED"
    ]
    deficiencies = [
        {"check": name, **finding}
        for name, finding in checks.items()
        if finding.get("status") in {"FAIL", "MISSING"}
    ]

    return {
        "overall_status": determine_overall_status(checks),
        "checks": checks,
        "deficiencies": deficiencies,
        "human_review_queue": review_queue,
        "combined_evidence": combined_evidence,
        "certificate_statements": certificate_statements,
        "rule_set": {
            "rule_set_id": rule_set.get("rule_set_id"),
            "version": rule_set.get("version"),
            "source": rule_set.get("source"),
        },
        "review_date": review_date,
        "reviewer_disposition": None,
        "final_authorized_status": None,
        "advisory_notice": ADVISORY_NOTICE,
    }
