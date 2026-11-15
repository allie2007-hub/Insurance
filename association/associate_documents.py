"""Document association for ACORD 25 certificates and endorsements.

The association engine uses policy number, named insured, carrier, dates,
source PDF, page position, form evidence, and folder context. A conservative
single-ACORD fallback permits an identity-light endorsement to remain with the
only ACORD in the same PDF when no contradictory evidence exists.
"""

import re
from datetime import datetime


AUTO_ASSOCIATE_SCORE = 70
REVIEW_SCORE = 40
MINIMUM_WINNING_MARGIN = 15

WEIGHTS = {
    "exact_policy": 100,
    "partial_policy": 70,
    "named_insured": 40,
    "carrier": 30,
    "date_compatible": 30,
    "same_source_pdf": 25,
    "adjacent_page": 20,
    "form_relevant": 15,
    "certificate_mentions_form": 15,
    "same_company_folder": 5,
}

PENALTIES = {
    "policy_conflict": -100,
    "named_insured_conflict": -60,
    "carrier_conflict": -40,
    "date_conflict": -40,
}


def normalize_policy_number(value):
    if not value:
        return None
    normalized = re.sub(r"[^A-Z0-9]", "", str(value).upper())
    return normalized or None


def normalize_name(value):
    if not value:
        return None
    value = re.sub(r"[^A-Z0-9 ]", " ", str(value).upper())
    value = re.sub(r"\s+", " ", value)
    return value.strip() or None


def _meaningful_name_tokens(value):
    normalized = normalize_name(value)
    if not normalized:
        return set()
    ignored = {
        "THE", "AND", "OF", "INC", "INCORPORATED", "LLC", "LLP",
        "LP", "LTD", "LIMITED", "CO", "COMPANY", "CORP",
        "CORPORATION", "INSURANCE", "INS", "GROUP",
    }
    return {
        token for token in normalized.split()
        if token not in ignored and len(token) > 1
    }


def names_are_similar(first_name, second_name):
    first = normalize_name(first_name)
    second = normalize_name(second_name)
    if not first or not second:
        return False
    if first in second or second in first:
        return True
    first_tokens = _meaningful_name_tokens(first)
    second_tokens = _meaningful_name_tokens(second)
    if not first_tokens or not second_tokens:
        return False
    shared = first_tokens & second_tokens
    return len(shared) / min(len(first_tokens), len(second_tokens)) >= 0.75


def policy_numbers_partially_match(first_policy, second_policy):
    first = normalize_policy_number(first_policy)
    second = normalize_policy_number(second_policy)
    if not first or not second:
        return False
    if first == second:
        return True
    if min(len(first), len(second)) < 6:
        return False
    return (
        first.endswith(second)
        or second.endswith(first)
        or first[-6:] == second[-6:]
    )


def parse_date(value):
    if not value:
        return None
    value = str(value).strip()
    for date_format in (
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"
    ):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def get_record_file(record):
    source = record.get("source") or {}
    return (
        record.get("source_file") or record.get("file")
        or record.get("filename") or record.get("file_name")
        or source.get("source_file") or source.get("file")
        or source.get("filename")
    )


def get_record_page(record):
    source = record.get("source") or {}
    page = (
        record.get("page_number") or record.get("page")
        or source.get("page_number") or source.get("page")
    )
    try:
        return int(page)
    except (TypeError, ValueError):
        return None


def get_record_company_folder(record):
    source = record.get("source") or {}
    return (
        record.get("company_name") or record.get("company")
        or record.get("company_folder") or source.get("company_name")
        or source.get("company") or source.get("company_folder")
    )


def _collect_text_values(value):
    values = []
    if isinstance(value, str):
        values.append(value)
    elif isinstance(value, dict):
        for nested in value.values():
            values.extend(_collect_text_values(nested))
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            values.extend(_collect_text_values(nested))
    return values


def get_acord_policy_numbers(acord_schema):
    numbers = set()
    for coverage in (acord_schema.get("coverages") or {}).values():
        if not isinstance(coverage, dict):
            continue
        number = normalize_policy_number(coverage.get("policy_number"))
        if number:
            numbers.add(number)
    return numbers


def get_acord_carriers(acord_schema):
    carriers = set()
    for field in (
        acord_schema.get("insurers"), acord_schema.get("carrier"),
        acord_schema.get("insurer"),
    ):
        for value in _collect_text_values(field):
            normalized = normalize_name(value)
            if normalized:
                carriers.add(normalized)
    for coverage in (acord_schema.get("coverages") or {}).values():
        if not isinstance(coverage, dict):
            continue
        for field_name in (
            "carrier", "insurer", "insurer_name", "insurance_company"
        ):
            normalized = normalize_name(coverage.get(field_name))
            if normalized:
                carriers.add(normalized)
    return carriers


def get_acord_policy_periods(acord_schema):
    periods = []
    for coverage in (acord_schema.get("coverages") or {}).values():
        if not isinstance(coverage, dict):
            continue
        effective = parse_date(coverage.get("effective_date"))
        expiration = parse_date(coverage.get("expiration_date"))
        if effective or expiration:
            periods.append({
                "effective_date": effective,
                "expiration_date": expiration,
            })
    return periods


def get_endorsement_carrier(endorsement_schema):
    for field_name in (
        "carrier", "insurer", "insurer_name", "insurance_company",
        "endorsement_carrier",
    ):
        value = endorsement_schema.get(field_name)
        if value:
            return normalize_name(value)
    return None


def get_endorsement_period(endorsement_schema):
    policy_period = endorsement_schema.get("policy_period") or {}
    return {
        "effective_date": parse_date(
            policy_period.get("effective_date")
            or endorsement_schema.get("endorsement_effective_date")
        ),
        "expiration_date": parse_date(policy_period.get("expiration_date")),
    }


def periods_are_compatible(first_period, second_period):
    first_start = first_period.get("effective_date")
    first_end = first_period.get("expiration_date")
    second_start = second_period.get("effective_date")
    second_end = second_period.get("expiration_date")
    if not first_start and not first_end:
        return None
    if not second_start and not second_end:
        return None
    if first_start and first_end and second_start and second_end:
        return first_start <= second_end and second_start <= first_end
    if second_start and first_start and first_end:
        return first_start <= second_start <= first_end
    if first_start and second_start and second_end:
        return second_start <= first_start <= second_end
    if first_start and second_start:
        return first_start == second_start
    if first_end and second_end:
        return first_end == second_end
    return None


def get_endorsement_form_numbers(endorsement_schema):
    forms = []
    primary = endorsement_schema.get("form_number")
    if primary:
        forms.append(str(primary))
    for form in endorsement_schema.get("form_numbers_on_page") or []:
        if form:
            forms.append(str(form))
    return list(dict.fromkeys(forms))


def form_is_relevant(endorsement_schema):
    return bool(
        endorsement_schema.get("ro_chart_entry")
        or get_endorsement_form_numbers(endorsement_schema)
        or endorsement_schema.get("evidence_tags")
    )


def certificate_mentions_endorsement_form(acord_schema, endorsement_schema):
    description = str(acord_schema.get("description_of_operations") or "").upper()
    if not description:
        return False
    normalized_description = re.sub(r"[^A-Z0-9]", "", description)
    for form in get_endorsement_form_numbers(endorsement_schema):
        normalized_form = re.sub(r"[^A-Z0-9]", "", form.upper())
        if normalized_form and normalized_form in normalized_description:
            return True
    return False


def associate_endorsement_to_acord(acord_record, endorsement_record):
    acord_schema = acord_record.get("schema") or {}
    endorsement_schema = endorsement_record.get("schema") or {}
    score = 0
    reasons = []
    conflicts = []

    acord_policies = get_acord_policy_numbers(acord_schema)
    endorsement_policy = normalize_policy_number(
        endorsement_schema.get("policy_number")
    )
    exact_policy_match = bool(
        endorsement_policy and endorsement_policy in acord_policies
    )
    partial_policy_match = False
    if endorsement_policy and acord_policies and not exact_policy_match:
        partial_policy_match = any(
            policy_numbers_partially_match(endorsement_policy, policy)
            for policy in acord_policies
        )
    if exact_policy_match:
        score += WEIGHTS["exact_policy"]
        reasons.append("Exact policy number match")
    elif partial_policy_match:
        score += WEIGHTS["partial_policy"]
        reasons.append("Partial normalized policy number match")
    elif endorsement_policy and acord_policies:
        score += PENALTIES["policy_conflict"]
        conflicts.append(
            "Endorsement policy number conflicts with the ACORD policy numbers"
        )

    acord_insured = acord_schema.get("named_insured")
    endorsement_insured = endorsement_schema.get("named_insured")
    insured_match = names_are_similar(acord_insured, endorsement_insured)
    if insured_match:
        score += WEIGHTS["named_insured"]
        reasons.append("Named insured matches")
    elif acord_insured and endorsement_insured:
        score += PENALTIES["named_insured_conflict"]
        conflicts.append("Named insured conflicts")

    acord_carriers = get_acord_carriers(acord_schema)
    endorsement_carrier = get_endorsement_carrier(endorsement_schema)
    carrier_match = False
    if endorsement_carrier and acord_carriers:
        carrier_match = any(
            names_are_similar(endorsement_carrier, carrier)
            for carrier in acord_carriers
        )
        if carrier_match:
            score += WEIGHTS["carrier"]
            reasons.append("Carrier or insurer matches")
        else:
            score += PENALTIES["carrier_conflict"]
            conflicts.append("Carrier or insurer conflicts")

    acord_periods = get_acord_policy_periods(acord_schema)
    endorsement_period = get_endorsement_period(endorsement_schema)
    date_results = [
        periods_are_compatible(period, endorsement_period)
        for period in acord_periods
    ]
    date_evidence_available = any(item is not None for item in date_results)
    date_match = any(item is True for item in date_results)
    if date_match:
        score += WEIGHTS["date_compatible"]
        reasons.append("Endorsement date is compatible with the ACORD policy period")
    elif date_evidence_available and date_results and all(
        item is False for item in date_results
    ):
        score += PENALTIES["date_conflict"]
        conflicts.append("Endorsement date conflicts with the ACORD policy period")

    acord_file = get_record_file(acord_record)
    endorsement_file = get_record_file(endorsement_record)
    same_source_pdf = bool(
        acord_file and endorsement_file
        and normalize_name(acord_file) == normalize_name(endorsement_file)
    )
    adjacent_page = False
    if same_source_pdf:
        score += WEIGHTS["same_source_pdf"]
        reasons.append("ACORD and endorsement are in the same PDF")
        acord_page = get_record_page(acord_record)
        endorsement_page = get_record_page(endorsement_record)
        adjacent_page = bool(
            acord_page is not None and endorsement_page is not None
            and abs(endorsement_page - acord_page) == 1
        )
        if adjacent_page:
            score += WEIGHTS["adjacent_page"]
            reasons.append("Endorsement is adjacent to the ACORD page")

    if form_is_relevant(endorsement_schema):
        score += WEIGHTS["form_relevant"]
        reasons.append("Recognized endorsement form or evidence tag found")

    certificate_reference = certificate_mentions_endorsement_form(
        acord_schema, endorsement_schema
    )
    if certificate_reference:
        score += WEIGHTS["certificate_mentions_form"]
        reasons.append("Certificate description mentions the endorsement form")

    acord_folder = get_record_company_folder(acord_record)
    endorsement_folder = get_record_company_folder(endorsement_record)
    same_company_folder = bool(
        acord_folder and endorsement_folder
        and normalize_name(acord_folder) == normalize_name(endorsement_folder)
    )
    if same_company_folder:
        score += WEIGHTS["same_company_folder"]
        reasons.append("Documents are in the same carrier folder; supporting context only")

    policy_conflict = bool(
        endorsement_policy and acord_policies
        and not exact_policy_match and not partial_policy_match
    )
    if policy_conflict:
        status, confidence, associated, requires_review = (
            "NOT_ASSOCIATED", "HIGH", False, False
        )
    elif score >= AUTO_ASSOCIATE_SCORE:
        status, confidence, associated, requires_review = (
            "ASSOCIATED", "HIGH", True, False
        )
    elif score >= REVIEW_SCORE:
        status, confidence, associated, requires_review = (
            "REVIEW_REQUIRED", "MEDIUM", False, True
        )
    else:
        status = "NOT_ASSOCIATED"
        confidence = "LOW" if reasons else "NONE"
        associated = False
        requires_review = bool(reasons)

    return {
        "associated": associated,
        "status": status,
        "confidence": confidence,
        "score": score,
        "reason": "; ".join(reasons) if reasons else "No reliable association evidence was found",
        "reasons": reasons,
        "conflicts": conflicts,
        "requires_review": requires_review,
        "policy_number": endorsement_policy,
        "exact_policy_match": exact_policy_match,
        "partial_policy_match": partial_policy_match,
        "insured_match": insured_match,
        "carrier_match": carrier_match,
        "date_match": date_match,
        "date_evidence_available": date_evidence_available,
        "same_source_pdf": same_source_pdf,
        "adjacent_page": adjacent_page,
        "certificate_reference": certificate_reference,
        "same_company_folder": same_company_folder,
    }


def associate_endorsements(first_record, second_record):
    first_schema = first_record.get("schema") or {}
    second_schema = second_record.get("schema") or {}
    score = 0
    reasons = []
    conflicts = []

    first_policy = normalize_policy_number(first_schema.get("policy_number"))
    second_policy = normalize_policy_number(second_schema.get("policy_number"))
    exact_policy_match = bool(
        first_policy and second_policy and first_policy == second_policy
    )
    partial_policy_match = False
    if first_policy and second_policy and not exact_policy_match:
        partial_policy_match = policy_numbers_partially_match(
            first_policy, second_policy
        )
    if exact_policy_match:
        score += WEIGHTS["exact_policy"]
        reasons.append("Exact endorsement policy number match")
    elif partial_policy_match:
        score += WEIGHTS["partial_policy"]
        reasons.append("Partial endorsement policy number match")
    elif first_policy and second_policy:
        score += PENALTIES["policy_conflict"]
        conflicts.append("Endorsement policy numbers conflict")

    first_insured = first_schema.get("named_insured")
    second_insured = second_schema.get("named_insured")
    insured_match = names_are_similar(first_insured, second_insured)
    if insured_match:
        score += WEIGHTS["named_insured"]
        reasons.append("Named insured matches")
    elif first_insured and second_insured:
        score += PENALTIES["named_insured_conflict"]
        conflicts.append("Named insured conflicts")

    first_carrier = get_endorsement_carrier(first_schema)
    second_carrier = get_endorsement_carrier(second_schema)
    carrier_match = names_are_similar(first_carrier, second_carrier)
    if carrier_match:
        score += WEIGHTS["carrier"]
        reasons.append("Carrier matches")
    elif first_carrier and second_carrier:
        score += PENALTIES["carrier_conflict"]
        conflicts.append("Carrier conflicts")

    date_match = periods_are_compatible(
        get_endorsement_period(first_schema),
        get_endorsement_period(second_schema),
    )
    if date_match is True:
        score += WEIGHTS["date_compatible"]
        reasons.append("Policy periods are compatible")
    elif date_match is False:
        score += PENALTIES["date_conflict"]
        conflicts.append("Policy periods conflict")

    first_file = get_record_file(first_record)
    second_file = get_record_file(second_record)
    same_source_pdf = bool(
        first_file and second_file
        and normalize_name(first_file) == normalize_name(second_file)
    )
    if same_source_pdf:
        score += WEIGHTS["same_source_pdf"]
        reasons.append("Endorsements are in the same PDF")
        first_page = get_record_page(first_record)
        second_page = get_record_page(second_record)
        if (
            first_page is not None and second_page is not None
            and abs(first_page - second_page) == 1
        ):
            score += WEIGHTS["adjacent_page"]
            reasons.append("Endorsement pages are adjacent")

    first_folder = get_record_company_folder(first_record)
    second_folder = get_record_company_folder(second_record)
    same_company_folder = bool(
        first_folder and second_folder
        and normalize_name(first_folder) == normalize_name(second_folder)
    )
    if same_company_folder:
        score += WEIGHTS["same_company_folder"]
        reasons.append("Documents are in the same carrier folder; supporting context only")

    hard_conflict = (
        "Endorsement policy numbers conflict" in conflicts
        or "Named insured conflicts" in conflicts
    )
    associated = not hard_conflict and score >= AUTO_ASSOCIATE_SCORE
    if associated:
        status, confidence = "ASSOCIATED", "HIGH"
    elif score >= REVIEW_SCORE:
        status, confidence = "REVIEW_REQUIRED", "MEDIUM"
    else:
        status = "NOT_ASSOCIATED"
        confidence = "LOW" if reasons else "NONE"

    return {
        "associated": associated,
        "status": status,
        "confidence": confidence,
        "score": score,
        "reasons": reasons,
        "conflicts": conflicts,
        "policy_number": first_policy or second_policy,
        "named_insured": first_insured or second_insured,
        "carrier": first_carrier or second_carrier,
        "exact_policy_match": exact_policy_match,
        "partial_policy_match": partial_policy_match,
        "insured_match": insured_match,
        "carrier_match": carrier_match,
        "date_match": date_match,
        "same_source_pdf": same_source_pdf,
        "same_company_folder": same_company_folder,
    }


def group_has_identity(group):
    for record in group.get("endorsement_records", []):
        schema = record.get("schema") or {}
        if (
            schema.get("policy_number") or schema.get("named_insured")
            or get_endorsement_carrier(schema)
        ):
            return True
    return False


def get_group_identity(group):
    named_insured = policy_number = carrier = None
    for record in group.get("endorsement_records", []):
        schema = record.get("schema") or {}
        named_insured = named_insured or schema.get("named_insured")
        policy_number = policy_number or schema.get("policy_number")
        carrier = carrier or get_endorsement_carrier(schema)
    return {
        "named_insured": named_insured,
        "policy_number": policy_number,
        "carrier": carrier,
    }


def build_endorsement_only_groups(endorsement_records, starting_number):
    groups = []
    unresolved_records = []
    for record in endorsement_records:
        candidates = []
        for group in groups:
            comparisons = [
                associate_endorsements(existing, record)
                for existing in group["endorsement_records"]
            ]
            associated = [item for item in comparisons if item["associated"]]
            if associated:
                candidates.append({
                    "group": group,
                    "result": max(associated, key=lambda item: item["score"]),
                })
        candidates.sort(key=lambda item: item["result"]["score"], reverse=True)
        if not candidates:
            groups.append({
                "submission_id": None,
                "group_type": "ENDORSEMENT_ONLY",
                "acord_record": None,
                "endorsement_records": [record],
                "association_results": [],
                "missing_documents": ["ACORD_25"],
                "requires_review": False,
                "group_reason": "No matching ACORD was found for this endorsement group",
            })
            continue
        best = candidates[0]
        second_score = candidates[1]["result"]["score"] if len(candidates) > 1 else None
        margin = (
            best["result"]["score"] - second_score
            if second_score is not None else best["result"]["score"]
        )
        if second_score is None or margin >= MINIMUM_WINNING_MARGIN:
            best["group"]["endorsement_records"].append(record)
            best["group"]["association_results"].append({
                **best["result"],
                "association_type": "ENDORSEMENT_TO_ENDORSEMENT",
                "winning_margin": margin,
            })
        else:
            unresolved_records.append({
                "record": record,
                "reason": "Endorsement matched more than one endorsement-only group with similar scores",
                "requires_review": True,
                "candidate_results": [item["result"] for item in candidates],
            })

    valid_groups = []
    for group in groups:
        if not group_has_identity(group):
            for record in group["endorsement_records"]:
                unresolved_records.append({
                    "record": record,
                    "reason": "Endorsement could not be grouped because no policy number, named insured, or carrier was extracted",
                    "requires_review": True,
                    "candidate_results": [],
                })
            continue
        group.update(get_group_identity(group))
        valid_groups.append(group)

    for number, group in enumerate(valid_groups, start=starting_number):
        group["submission_id"] = f"SUB-{number:03d}"
    return {"groups": valid_groups, "unresolved_records": unresolved_records}


def qualifies_for_single_acord_package_association(
    acord_record,
    endorsement_record,
    association_result,
    acord_count,
    already_associated_endorsements=None,
):
    """Conservative same-PDF fallback for identity-light endorsements.

    The first endorsement must be close to the only ACORD or be referenced by
    it. After that anchor is established, later recognized endorsement pages
    in the same PDF may inherit package context, provided no explicit policy,
    insured, carrier, or date conflict exists.
    """
    if acord_count != 1:
        return False
    if not association_result.get("same_source_pdf"):
        return False
    if association_result.get("conflicts"):
        return False

    endorsement_schema = endorsement_record.get("schema") or {}
    if not form_is_relevant(endorsement_schema):
        return False

    acord_page = get_record_page(acord_record)
    endorsement_page = get_record_page(endorsement_record)
    if acord_page is None or endorsement_page is None:
        return False
    if endorsement_page <= acord_page:
        return False

    # Initial anchor: endorsement is close to the only ACORD, or the ACORD
    # explicitly references its form.
    follows_nearby = endorsement_page - acord_page <= 2
    if follows_nearby or association_result.get("certificate_reference"):
        return True

    # Package-chain fallback: at least one earlier endorsement has already
    # been safely associated to this ACORD in the same PDF. Processed records
    # are ordered by page, so this extends the confirmed chain forward.
    earlier_associated = []
    for record in already_associated_endorsements or []:
        record_file = get_record_file(record)
        record_page = get_record_page(record)
        if (
            record_file
            and normalize_name(record_file)
            == normalize_name(get_record_file(endorsement_record))
            and record_page is not None
            and acord_page < record_page < endorsement_page
        ):
            earlier_associated.append(record_page)

    return bool(earlier_associated)


def _mark_associated_schema(endorsement_record, final_result):
    schema = endorsement_record.get("schema") or {}
    schema["association_status"] = "MATCH"
    schema["association_method"] = final_result["association_method"]
    schema["association_confidence"] = final_result["confidence"]
    schema["association_reasons"] = final_result.get("reasons", [])
    endorsement_record["schema"] = schema


def associate_batch(page_records):
    acord_records = [
        record for record in page_records
        if (record.get("schema") or {}).get("document_type") == "ACORD_25"
    ]
    endorsement_records = [
        record for record in page_records
        if (record.get("schema") or {}).get("document_type") == "ENDORSEMENT"
    ]
    other_records = [
        record for record in page_records
        if (record.get("schema") or {}).get("document_type")
        not in {"ACORD_25", "ENDORSEMENT"}
    ]

    submissions = []
    for number, acord_record in enumerate(acord_records, start=1):
        acord_schema = acord_record.get("schema") or {}
        submissions.append({
            "submission_id": f"SUB-{number:03d}",
            "group_type": "ACORD_BASED",
            "acord_record": acord_record,
            "endorsement_records": [],
            "association_results": [],
            "named_insured": acord_schema.get("named_insured"),
            "policy_number": None,
            "carrier": None,
            "missing_documents": [],
            "requires_review": False,
            "group_reason": "Submission was initialized from an ACORD 25",
        })

    unmatched = []
    endorsement_records = sorted(
        endorsement_records,
        key=lambda record: (
            normalize_name(get_record_file(record)) or "",
            get_record_page(record) if get_record_page(record) is not None else 10**9,
        ),
    )
    for endorsement_record in endorsement_records:
        candidates = []
        for submission in submissions:
            result = associate_endorsement_to_acord(
                submission["acord_record"], endorsement_record
            )
            candidates.append({"submission": submission, "result": result})
        candidates.sort(key=lambda item: item["result"]["score"], reverse=True)
        if not candidates:
            unmatched.append(endorsement_record)
            continue

        best = candidates[0]
        best_result = best["result"]
        second_score = candidates[1]["result"]["score"] if len(candidates) > 1 else None
        winning_margin = (
            best_result["score"] - second_score
            if second_score is not None else best_result["score"]
        )
        clear_winner = (
            second_score is None or winning_margin >= MINIMUM_WINNING_MARGIN
        )
        package_context_match = (
            clear_winner
            and qualifies_for_single_acord_package_association(
                best["submission"]["acord_record"],
                endorsement_record,
                best_result,
                len(acord_records),
                already_associated_endorsements=(
                    best["submission"]["endorsement_records"]
                ),
            )
        )

        if clear_winner and (best_result["associated"] or package_context_match):
            method = (
                "SCORED_EVIDENCE"
                if best_result["associated"]
                else "SINGLE_ACORD_SAME_PDF_CONTEXT"
            )
            final_result = {
                **best_result,
                "associated": True,
                "status": "ASSOCIATED",
                "confidence": (
                    best_result["confidence"]
                    if best_result["associated"] else "MEDIUM"
                ),
                "requires_review": False,
                "association_type": "ENDORSEMENT_TO_ACORD",
                "association_method": method,
                "winning_margin": winning_margin,
            }
            _mark_associated_schema(endorsement_record, final_result)
            best["submission"]["endorsement_records"].append(endorsement_record)
            best["submission"]["association_results"].append(final_result)
        else:
            unmatched.append(endorsement_record)

    endorsement_result = build_endorsement_only_groups(
        unmatched, starting_number=len(submissions) + 1
    )
    submissions.extend(endorsement_result["groups"])
    unassociated_records = endorsement_result["unresolved_records"]

    return {
        "submissions": submissions,
        "unassociated_records": unassociated_records,
        "other_records": other_records,
        "acord_count": len(acord_records),
        "endorsement_count": len(endorsement_records),
        "acord_submission_count": sum(
            item["group_type"] == "ACORD_BASED" for item in submissions
        ),
        "endorsement_only_group_count": sum(
            item["group_type"] == "ENDORSEMENT_ONLY" for item in submissions
        ),
    }
