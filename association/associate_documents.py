import re
from datetime import datetime


# =========================================================
# Association thresholds and weights
# =========================================================

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


# =========================================================
# Normalization helpers
# =========================================================

def normalize_policy_number(value):
    """
    Normalizes a policy number for comparison.
    """

    if not value:
        return None

    normalized = re.sub(
        r"[^A-Z0-9]",
        "",
        str(value).upper()
    )

    return normalized or None


def normalize_name(value):
    """
    Normalizes an insured or carrier name.
    """

    if not value:
        return None

    value = re.sub(
        r"[^A-Z0-9 ]",
        " ",
        str(value).upper()
    )

    value = re.sub(r"\s+", " ", value)

    return value.strip() or None


def _meaningful_name_tokens(value):
    """
    Removes common company words before comparing names.
    """

    normalized = normalize_name(value)

    if not normalized:
        return set()

    ignored_tokens = {
        "THE",
        "AND",
        "OF",
        "INC",
        "INCORPORATED",
        "LLC",
        "LLP",
        "LP",
        "LTD",
        "LIMITED",
        "CO",
        "COMPANY",
        "CORP",
        "CORPORATION",
        "INSURANCE",
        "INS",
        "GROUP",
    }

    return {
        token
        for token in normalized.split()
        if (
            token not in ignored_tokens
            and len(token) > 1
        )
    }


def names_are_similar(first_name, second_name):
    """
    Compares insured or carrier names.

    Allows one value to contain an address or additional wording.
    It also compares meaningful tokens after removing company suffixes.
    """

    first_normalized = normalize_name(first_name)
    second_normalized = normalize_name(second_name)

    if not first_normalized or not second_normalized:
        return False

    if (
        first_normalized in second_normalized
        or second_normalized in first_normalized
    ):
        return True

    first_tokens = _meaningful_name_tokens(
        first_normalized
    )
    second_tokens = _meaningful_name_tokens(
        second_normalized
    )

    if not first_tokens or not second_tokens:
        return False

    shared_tokens = first_tokens & second_tokens

    smaller_count = min(
        len(first_tokens),
        len(second_tokens)
    )

    similarity = (
        len(shared_tokens)
        / smaller_count
    )

    return similarity >= 0.75


def policy_numbers_partially_match(
    first_policy,
    second_policy
):
    """
    Handles formatting or prefix differences conservatively.

    A partial match requires at least six comparable characters.
    """

    first_policy = normalize_policy_number(
        first_policy
    )

    second_policy = normalize_policy_number(
        second_policy
    )

    if not first_policy or not second_policy:
        return False

    if first_policy == second_policy:
        return True

    if min(
        len(first_policy),
        len(second_policy)
    ) < 6:
        return False

    return (
        first_policy.endswith(second_policy)
        or second_policy.endswith(first_policy)
        or first_policy[-6:] == second_policy[-6:]
    )


def parse_date(value):
    """
    Converts supported dates to datetime.date.

    Supports ISO dates and common slash/dash formats.
    """

    if not value:
        return None

    value = str(value).strip()

    formats = (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m-%d-%Y",
        "%m-%d-%y",
    )

    for date_format in formats:
        try:
            return datetime.strptime(
                value,
                date_format
            ).date()

        except ValueError:
            continue

    return None


# =========================================================
# Record helpers
# =========================================================

def get_record_file(record):
    """
    Gets the source filename from the page record.
    """

    source = record.get("source") or {}

    return (
        record.get("source_file")
        or record.get("file")
        or record.get("filename")
        or record.get("file_name")
        or source.get("source_file")
        or source.get("file")
        or source.get("filename")
    )


def get_record_page(record):
    """
    Gets the source page number.
    """

    source = record.get("source") or {}

    page = (
        record.get("page_number")
        or record.get("page")
        or source.get("page_number")
        or source.get("page")
    )

    try:
        return int(page)

    except (TypeError, ValueError):
        return None


def get_record_company_folder(record):
    """
    Gets the carrier/company-folder context.

    Folder context is supporting evidence only.
    """

    source = record.get("source") or {}

    return (
        record.get("company_name")
        or record.get("company")
        or record.get("company_folder")
        or source.get("company_name")
        or source.get("company")
        or source.get("company_folder")
    )


def _collect_text_values(value):
    """
    Recursively collects text values from nested structures.
    """

    values = []

    if isinstance(value, str):
        values.append(value)

    elif isinstance(value, dict):
        for nested_value in value.values():
            values.extend(
                _collect_text_values(
                    nested_value
                )
            )

    elif isinstance(
        value,
        (list, tuple, set)
    ):
        for nested_value in value:
            values.extend(
                _collect_text_values(
                    nested_value
                )
            )

    return values


# =========================================================
# ACORD evidence helpers
# =========================================================

def get_acord_policy_numbers(acord_schema):
    """
    Collects every policy number found on an ACORD 25.
    """

    policy_numbers = set()

    coverages = (
        acord_schema.get("coverages")
        or {}
    )

    for coverage_data in coverages.values():

        if not isinstance(
            coverage_data,
            dict
        ):
            continue

        policy_number = normalize_policy_number(
            coverage_data.get(
                "policy_number"
            )
        )

        if policy_number:
            policy_numbers.add(
                policy_number
            )

    return policy_numbers


def get_acord_carriers(acord_schema):
    """
    Collects insurer or carrier names from an ACORD schema.
    """

    carriers = set()

    candidate_fields = [
        acord_schema.get("insurers"),
        acord_schema.get("carrier"),
        acord_schema.get("insurer"),
    ]

    for field in candidate_fields:

        for value in _collect_text_values(
            field
        ):
            normalized = normalize_name(value)

            if normalized:
                carriers.add(normalized)

    coverages = (
        acord_schema.get("coverages")
        or {}
    )

    for coverage in coverages.values():

        if not isinstance(coverage, dict):
            continue

        for field_name in (
            "carrier",
            "insurer",
            "insurer_name",
            "insurance_company",
        ):
            normalized = normalize_name(
                coverage.get(field_name)
            )

            if normalized:
                carriers.add(normalized)

    return carriers


def get_acord_policy_periods(acord_schema):
    """
    Collects effective and expiration periods from ACORD coverages.
    """

    periods = []

    coverages = (
        acord_schema.get("coverages")
        or {}
    )

    for coverage in coverages.values():

        if not isinstance(coverage, dict):
            continue

        effective_date = parse_date(
            coverage.get(
                "effective_date"
            )
        )

        expiration_date = parse_date(
            coverage.get(
                "expiration_date"
            )
        )

        if effective_date or expiration_date:
            periods.append({
                "effective_date":
                    effective_date,
                "expiration_date":
                    expiration_date,
            })

    return periods


# =========================================================
# Endorsement evidence helpers
# =========================================================

def get_endorsement_carrier(
    endorsement_schema
):
    """
    Gets the best available endorsement carrier.
    """

    for field_name in (
        "carrier",
        "insurer",
        "insurer_name",
        "insurance_company",
        "endorsement_carrier",
    ):
        value = endorsement_schema.get(
            field_name
        )

        if value:
            return normalize_name(value)

    return None


def get_endorsement_period(
    endorsement_schema
):
    """
    Gets the endorsement policy period.
    """

    policy_period = (
        endorsement_schema.get(
            "policy_period"
        )
        or {}
    )

    effective_date = parse_date(
        policy_period.get(
            "effective_date"
        )
        or endorsement_schema.get(
            "endorsement_effective_date"
        )
    )

    expiration_date = parse_date(
        policy_period.get(
            "expiration_date"
        )
    )

    return {
        "effective_date":
            effective_date,
        "expiration_date":
            expiration_date,
    }


def periods_are_compatible(
    first_period,
    second_period
):
    """
    Checks whether two policy periods overlap or align.

    Returns True, False, or None when there is not enough
    date evidence for a decision.
    """

    first_start = first_period.get(
        "effective_date"
    )

    first_end = first_period.get(
        "expiration_date"
    )

    second_start = second_period.get(
        "effective_date"
    )

    second_end = second_period.get(
        "expiration_date"
    )

    if (
        not first_start
        and not first_end
    ):
        return None

    if (
        not second_start
        and not second_end
    ):
        return None

    if (
        first_start
        and first_end
        and second_start
        and second_end
    ):
        return (
            first_start <= second_end
            and second_start <= first_end
        )

    if (
        second_start
        and first_start
        and first_end
    ):
        return (
            first_start
            <= second_start
            <= first_end
        )

    if (
        first_start
        and second_start
        and second_end
    ):
        return (
            second_start
            <= first_start
            <= second_end
        )

    if first_start and second_start:
        return first_start == second_start

    if first_end and second_end:
        return first_end == second_end

    return None


def get_endorsement_form_numbers(
    endorsement_schema
):
    """
    Gets endorsement form numbers.
    """

    form_numbers = []

    primary_form = endorsement_schema.get(
        "form_number"
    )

    if primary_form:
        form_numbers.append(
            str(primary_form)
        )

    for form_number in (
        endorsement_schema.get(
            "form_numbers_on_page"
        )
        or []
    ):
        if form_number:
            form_numbers.append(
                str(form_number)
            )

    return list(
        dict.fromkeys(form_numbers)
    )


def form_is_relevant(
    endorsement_schema
):
    """
    Determines whether recognized endorsement evidence exists.
    """

    if endorsement_schema.get(
        "ro_chart_entry"
    ):
        return True

    if get_endorsement_form_numbers(
        endorsement_schema
    ):
        return True

    return bool(
        endorsement_schema.get(
            "evidence_tags"
        )
    )


def certificate_mentions_endorsement_form(
    acord_schema,
    endorsement_schema
):
    """
    Checks whether the certificate description mentions
    an endorsement form.
    """

    description = str(
        acord_schema.get(
            "description_of_operations"
        )
        or ""
    ).upper()

    if not description:
        return False

    normalized_description = re.sub(
        r"[^A-Z0-9]",
        "",
        description
    )

    for form_number in (
        get_endorsement_form_numbers(
            endorsement_schema
        )
    ):
        normalized_form = re.sub(
            r"[^A-Z0-9]",
            "",
            form_number.upper()
        )

        if (
            normalized_form
            and normalized_form
            in normalized_description
        ):
            return True

    return False


# =========================================================
# ACORD-to-endorsement association scoring
# =========================================================

def associate_endorsement_to_acord(
    acord_record,
    endorsement_record
):
    """
    Scores whether an endorsement belongs to an ACORD 25.

    Policy number is strongest, but named insured, carrier,
    dates, page relationships, form evidence, and folder context
    can also contribute.

    Folder context cannot establish association by itself.
    """

    acord_schema = (
        acord_record.get("schema")
        or {}
    )

    endorsement_schema = (
        endorsement_record.get("schema")
        or {}
    )

    score = 0
    reasons = []
    conflicts = []

    # -----------------------------------------------------
    # Policy number
    # -----------------------------------------------------

    acord_policies = (
        get_acord_policy_numbers(
            acord_schema
        )
    )

    endorsement_policy = (
        normalize_policy_number(
            endorsement_schema.get(
                "policy_number"
            )
        )
    )

    exact_policy_match = bool(
        endorsement_policy
        and endorsement_policy
        in acord_policies
    )

    partial_policy_match = False

    if (
        endorsement_policy
        and acord_policies
        and not exact_policy_match
    ):
        partial_policy_match = any(
            policy_numbers_partially_match(
                endorsement_policy,
                acord_policy
            )
            for acord_policy
            in acord_policies
        )

    if exact_policy_match:
        score += WEIGHTS["exact_policy"]

        reasons.append(
            "Exact policy number match"
        )

    elif partial_policy_match:
        score += WEIGHTS[
            "partial_policy"
        ]

        reasons.append(
            "Partial normalized policy "
            "number match"
        )

    elif (
        endorsement_policy
        and acord_policies
    ):
        score += PENALTIES[
            "policy_conflict"
        ]

        conflicts.append(
            "Endorsement policy number "
            "conflicts with the ACORD "
            "policy numbers"
        )

    # -----------------------------------------------------
    # Named insured
    # -----------------------------------------------------

    acord_insured = acord_schema.get(
        "named_insured"
    )

    endorsement_insured = (
        endorsement_schema.get(
            "named_insured"
        )
    )

    insured_match = names_are_similar(
        acord_insured,
        endorsement_insured
    )

    if insured_match:
        score += WEIGHTS[
            "named_insured"
        ]

        reasons.append(
            "Named insured matches"
        )

    elif (
        acord_insured
        and endorsement_insured
    ):
        score += PENALTIES[
            "named_insured_conflict"
        ]

        conflicts.append(
            "Named insured conflicts"
        )

    # -----------------------------------------------------
    # Carrier
    # -----------------------------------------------------

    acord_carriers = get_acord_carriers(
        acord_schema
    )

    endorsement_carrier = (
        get_endorsement_carrier(
            endorsement_schema
        )
    )

    carrier_match = False

    if (
        endorsement_carrier
        and acord_carriers
    ):
        carrier_match = any(
            names_are_similar(
                endorsement_carrier,
                acord_carrier
            )
            for acord_carrier
            in acord_carriers
        )

        if carrier_match:
            score += WEIGHTS["carrier"]

            reasons.append(
                "Carrier or insurer matches"
            )

        else:
            score += PENALTIES[
                "carrier_conflict"
            ]

            conflicts.append(
                "Carrier or insurer conflicts"
            )

    # -----------------------------------------------------
    # Dates
    # -----------------------------------------------------

    acord_periods = (
        get_acord_policy_periods(
            acord_schema
        )
    )

    endorsement_period = (
        get_endorsement_period(
            endorsement_schema
        )
    )

    date_results = [
        periods_are_compatible(
            acord_period,
            endorsement_period
        )
        for acord_period in acord_periods
    ]

    date_evidence_available = any(
        result is not None
        for result in date_results
    )

    date_match = any(
        result is True
        for result in date_results
    )

    if date_match:
        score += WEIGHTS[
            "date_compatible"
        ]

        reasons.append(
            "Endorsement date is compatible "
            "with the ACORD policy period"
        )

    elif (
        date_evidence_available
        and date_results
        and all(
            result is False
            for result in date_results
        )
    ):
        score += PENALTIES[
            "date_conflict"
        ]

        conflicts.append(
            "Endorsement date conflicts "
            "with the ACORD policy period"
        )

    # -----------------------------------------------------
    # Source PDF and adjacent page
    # -----------------------------------------------------

    acord_file = get_record_file(
        acord_record
    )

    endorsement_file = get_record_file(
        endorsement_record
    )

    same_source_pdf = bool(
        acord_file
        and endorsement_file
        and normalize_name(acord_file)
        == normalize_name(endorsement_file)
    )

    if same_source_pdf:
        score += WEIGHTS[
            "same_source_pdf"
        ]

        reasons.append(
            "ACORD and endorsement are "
            "in the same PDF"
        )

        acord_page = get_record_page(
            acord_record
        )

        endorsement_page = get_record_page(
            endorsement_record
        )

        if (
            acord_page is not None
            and endorsement_page is not None
            and abs(
                endorsement_page
                - acord_page
            ) == 1
        ):
            score += WEIGHTS[
                "adjacent_page"
            ]

            reasons.append(
                "Endorsement is adjacent "
                "to the ACORD page"
            )

    # -----------------------------------------------------
    # Form evidence
    # -----------------------------------------------------

    if form_is_relevant(
        endorsement_schema
    ):
        score += WEIGHTS[
            "form_relevant"
        ]

        reasons.append(
            "Recognized endorsement form "
            "or evidence tag found"
        )

    if certificate_mentions_endorsement_form(
        acord_schema,
        endorsement_schema
    ):
        score += WEIGHTS[
            "certificate_mentions_form"
        ]

        reasons.append(
            "Certificate description "
            "mentions the endorsement form"
        )

    # -----------------------------------------------------
    # Folder context
    # -----------------------------------------------------

    acord_folder = (
        get_record_company_folder(
            acord_record
        )
    )

    endorsement_folder = (
        get_record_company_folder(
            endorsement_record
        )
    )

    same_company_folder = bool(
        acord_folder
        and endorsement_folder
        and normalize_name(acord_folder)
        == normalize_name(
            endorsement_folder
        )
    )

    if same_company_folder:
        score += WEIGHTS[
            "same_company_folder"
        ]

        reasons.append(
            "Documents are in the same "
            "carrier folder; supporting "
            "context only"
        )

    # -----------------------------------------------------
    # Final decision
    # -----------------------------------------------------

    policy_conflict = bool(
        endorsement_policy
        and acord_policies
        and not exact_policy_match
        and not partial_policy_match
    )

    if policy_conflict:
        status = "NOT_ASSOCIATED"
        confidence = "HIGH"
        associated = False
        requires_review = False

    elif score >= AUTO_ASSOCIATE_SCORE:
        status = "ASSOCIATED"
        confidence = "HIGH"
        associated = True
        requires_review = False

    elif score >= REVIEW_SCORE:
        status = "REVIEW_REQUIRED"
        confidence = "MEDIUM"
        associated = False
        requires_review = True

    else:
        status = "NOT_ASSOCIATED"

        confidence = (
            "LOW"
            if reasons
            else "NONE"
        )

        associated = False
        requires_review = bool(reasons)

    if reasons:
        reason = "; ".join(reasons)

    else:
        reason = (
            "No reliable association "
            "evidence was found"
        )

    return {
        "associated":
            associated,
        "status":
            status,
        "confidence":
            confidence,
        "score":
            score,
        "reason":
            reason,
        "reasons":
            reasons,
        "conflicts":
            conflicts,
        "requires_review":
            requires_review,
        "policy_number":
            endorsement_policy,
        "exact_policy_match":
            exact_policy_match,
        "partial_policy_match":
            partial_policy_match,
        "insured_match":
            insured_match,
        "carrier_match":
            carrier_match,
        "date_match":
            date_match,
        "date_evidence_available":
            date_evidence_available,
        "same_source_pdf":
            same_source_pdf,
        "same_company_folder":
            same_company_folder,
    }


# =========================================================
# Endorsement-to-endorsement grouping
# =========================================================

def associate_endorsements(
    first_record,
    second_record
):
    """
    Scores whether two endorsements belong to the same
    insured-policy group.

    A policy-number conflict or named-insured conflict
    prevents automatic grouping.
    """

    first_schema = (
        first_record.get("schema")
        or {}
    )

    second_schema = (
        second_record.get("schema")
        or {}
    )

    score = 0
    reasons = []
    conflicts = []

    # -----------------------------------------------------
    # Policy number
    # -----------------------------------------------------

    first_policy = normalize_policy_number(
        first_schema.get(
            "policy_number"
        )
    )

    second_policy = normalize_policy_number(
        second_schema.get(
            "policy_number"
        )
    )

    exact_policy_match = bool(
        first_policy
        and second_policy
        and first_policy == second_policy
    )

    partial_policy_match = False

    if (
        first_policy
        and second_policy
        and not exact_policy_match
    ):
        partial_policy_match = (
            policy_numbers_partially_match(
                first_policy,
                second_policy
            )
        )

    if exact_policy_match:
        score += WEIGHTS["exact_policy"]

        reasons.append(
            "Exact endorsement policy "
            "number match"
        )

    elif partial_policy_match:
        score += WEIGHTS[
            "partial_policy"
        ]

        reasons.append(
            "Partial endorsement policy "
            "number match"
        )

    elif first_policy and second_policy:
        score += PENALTIES[
            "policy_conflict"
        ]

        conflicts.append(
            "Endorsement policy numbers "
            "conflict"
        )

    # -----------------------------------------------------
    # Named insured
    # -----------------------------------------------------

    first_insured = first_schema.get(
        "named_insured"
    )

    second_insured = second_schema.get(
        "named_insured"
    )

    insured_match = names_are_similar(
        first_insured,
        second_insured
    )

    if insured_match:
        score += WEIGHTS[
            "named_insured"
        ]

        reasons.append(
            "Named insured matches"
        )

    elif (
        first_insured
        and second_insured
    ):
        score += PENALTIES[
            "named_insured_conflict"
        ]

        conflicts.append(
            "Named insured conflicts"
        )

    # -----------------------------------------------------
    # Carrier
    # -----------------------------------------------------

    first_carrier = (
        get_endorsement_carrier(
            first_schema
        )
    )

    second_carrier = (
        get_endorsement_carrier(
            second_schema
        )
    )

    carrier_match = names_are_similar(
        first_carrier,
        second_carrier
    )

    if carrier_match:
        score += WEIGHTS["carrier"]

        reasons.append(
            "Carrier matches"
        )

    elif first_carrier and second_carrier:
        score += PENALTIES[
            "carrier_conflict"
        ]

        conflicts.append(
            "Carrier conflicts"
        )

    # -----------------------------------------------------
    # Dates
    # -----------------------------------------------------

    first_period = get_endorsement_period(
        first_schema
    )

    second_period = get_endorsement_period(
        second_schema
    )

    date_match = periods_are_compatible(
        first_period,
        second_period
    )

    if date_match is True:
        score += WEIGHTS[
            "date_compatible"
        ]

        reasons.append(
            "Policy periods are compatible"
        )

    elif date_match is False:
        score += PENALTIES[
            "date_conflict"
        ]

        conflicts.append(
            "Policy periods conflict"
        )

    # -----------------------------------------------------
    # Source PDF
    # -----------------------------------------------------

    first_file = get_record_file(
        first_record
    )

    second_file = get_record_file(
        second_record
    )

    same_source_pdf = bool(
        first_file
        and second_file
        and normalize_name(first_file)
        == normalize_name(second_file)
    )

    if same_source_pdf:
        score += WEIGHTS[
            "same_source_pdf"
        ]

        reasons.append(
            "Endorsements are in the same PDF"
        )

        first_page = get_record_page(
            first_record
        )

        second_page = get_record_page(
            second_record
        )

        if (
            first_page is not None
            and second_page is not None
            and abs(
                first_page - second_page
            ) == 1
        ):
            score += WEIGHTS[
                "adjacent_page"
            ]

            reasons.append(
                "Endorsement pages are adjacent"
            )

    # -----------------------------------------------------
    # Folder context
    # -----------------------------------------------------

    first_folder = (
        get_record_company_folder(
            first_record
        )
    )

    second_folder = (
        get_record_company_folder(
            second_record
        )
    )

    same_company_folder = bool(
        first_folder
        and second_folder
        and normalize_name(first_folder)
        == normalize_name(second_folder)
    )

    if same_company_folder:
        score += WEIGHTS[
            "same_company_folder"
        ]

        reasons.append(
            "Documents are in the same "
            "carrier folder; supporting "
            "context only"
        )

    # -----------------------------------------------------
    # Final decision
    # -----------------------------------------------------

    hard_conflict = bool(
        conflicts
        and (
            "Endorsement policy numbers conflict"
            in conflicts
            or "Named insured conflicts"
            in conflicts
        )
    )

    associated = bool(
        not hard_conflict
        and score >= AUTO_ASSOCIATE_SCORE
    )

    if associated:
        status = "ASSOCIATED"
        confidence = "HIGH"

    elif score >= REVIEW_SCORE:
        status = "REVIEW_REQUIRED"
        confidence = "MEDIUM"

    else:
        status = "NOT_ASSOCIATED"

        confidence = (
            "LOW"
            if reasons
            else "NONE"
        )

    return {
        "associated":
            associated,
        "status":
            status,
        "confidence":
            confidence,
        "score":
            score,
        "reasons":
            reasons,
        "conflicts":
            conflicts,
        "policy_number":
            first_policy or second_policy,
        "named_insured":
            first_insured or second_insured,
        "carrier":
            first_carrier or second_carrier,
        "exact_policy_match":
            exact_policy_match,
        "partial_policy_match":
            partial_policy_match,
        "insured_match":
            insured_match,
        "carrier_match":
            carrier_match,
        "date_match":
            date_match,
        "same_source_pdf":
            same_source_pdf,
        "same_company_folder":
            same_company_folder,
    }


def group_has_identity(group):
    """
    Determines whether an endorsement-only group has enough
    identity information to become an incomplete submission.
    """

    for record in group.get(
        "endorsement_records",
        []
    ):
        schema = record.get("schema") or {}

        if schema.get("policy_number"):
            return True

        if schema.get("named_insured"):
            return True

        if get_endorsement_carrier(schema):
            return True

    return False


def get_group_identity(group):
    """
    Collects the best identity values from an endorsement group.
    """

    named_insured = None
    policy_number = None
    carrier = None

    for record in group.get(
        "endorsement_records",
        []
    ):
        schema = record.get("schema") or {}

        if not named_insured:
            named_insured = schema.get(
                "named_insured"
            )

        if not policy_number:
            policy_number = schema.get(
                "policy_number"
            )

        if not carrier:
            carrier = (
                get_endorsement_carrier(
                    schema
                )
            )

    return {
        "named_insured":
            named_insured,
        "policy_number":
            policy_number,
        "carrier":
            carrier,
    }


def build_endorsement_only_groups(
    endorsement_records,
    starting_number
):
    """
    Groups endorsements that relate to one another but do not
    match an available ACORD.

    Each resulting group becomes an incomplete submission
    because the matching ACORD certificate is missing.
    """

    groups = []
    unresolved_records = []

    for endorsement_record in (
        endorsement_records
    ):
        candidate_groups = []

        for group in groups:

            comparison_results = []

            for existing_record in group[
                "endorsement_records"
            ]:
                result = associate_endorsements(
                    existing_record,
                    endorsement_record
                )

                comparison_results.append(
                    result
                )

            associated_results = [
                result
                for result
                in comparison_results
                if result["associated"]
            ]

            if associated_results:
                best_result = max(
                    associated_results,
                    key=lambda result:
                        result["score"]
                )

                candidate_groups.append({
                    "group":
                        group,
                    "result":
                        best_result,
                })

        candidate_groups.sort(
            key=lambda candidate:
                candidate["result"]["score"],
            reverse=True
        )

        if not candidate_groups:
            groups.append({
                "submission_id":
                    None,
                "group_type":
                    "ENDORSEMENT_ONLY",
                "acord_record":
                    None,
                "endorsement_records": [
                    endorsement_record
                ],
                "association_results": [],
                "missing_documents": [
                    "ACORD_25"
                ],
                "requires_review":
                    False,
                "group_reason": (
                    "No matching ACORD was "
                    "found for this endorsement "
                    "group"
                ),
            })

            continue

        best_candidate = candidate_groups[0]
        second_score = None

        if len(candidate_groups) > 1:
            second_score = candidate_groups[
                1
            ]["result"]["score"]

        winning_margin = (
            best_candidate["result"]["score"]
            - second_score
            if second_score is not None
            else best_candidate[
                "result"
            ]["score"]
        )

        clear_winner = (
            second_score is None
            or winning_margin
            >= MINIMUM_WINNING_MARGIN
        )

        if clear_winner:
            group = best_candidate["group"]

            group[
                "endorsement_records"
            ].append(
                endorsement_record
            )

            group[
                "association_results"
            ].append({
                **best_candidate["result"],
                "association_type":
                    "ENDORSEMENT_TO_ENDORSEMENT",
                "winning_margin":
                    winning_margin,
            })

        else:
            unresolved_records.append({
                "record":
                    endorsement_record,
                "reason": (
                    "Endorsement matched more "
                    "than one endorsement-only "
                    "group with similar scores"
                ),
                "requires_review":
                    True,
                "candidate_results": [
                    {
                        "score":
                            candidate[
                                "result"
                            ]["score"],
                        "status":
                            candidate[
                                "result"
                            ]["status"],
                        "confidence":
                            candidate[
                                "result"
                            ]["confidence"],
                        "reasons":
                            candidate[
                                "result"
                            ]["reasons"],
                        "conflicts":
                            candidate[
                                "result"
                            ]["conflicts"],
                    }
                    for candidate
                    in candidate_groups
                ],
            })

    valid_groups = []

    for group in groups:

        if not group_has_identity(group):

            for record in group[
                "endorsement_records"
            ]:
                unresolved_records.append({
                    "record":
                        record,
                    "reason": (
                        "Endorsement could not "
                        "be grouped because no "
                        "policy number, named "
                        "insured, or carrier "
                        "was extracted"
                    ),
                    "requires_review":
                        True,
                    "candidate_results": [],
                })

            continue

        identity = get_group_identity(
            group
        )

        group.update(identity)

        valid_groups.append(group)

    for submission_number, group in enumerate(
        valid_groups,
        start=starting_number
    ):
        group["submission_id"] = (
            f"SUB-{submission_number:03d}"
        )

    return {
        "groups":
            valid_groups,
        "unresolved_records":
            unresolved_records,
    }


# =========================================================
# Batch association
# =========================================================

def associate_batch(page_records):
    """
    Creates one submission group per insured and policy.

    ACORD-based submissions receive matching endorsements.

    Endorsements that do not belong to an available ACORD are
    compared with one another. Related endorsements become an
    endorsement-only incomplete submission.

    Only records without sufficient identity or with ambiguous
    group matches remain unassociated.
    """

    acord_records = [
        record
        for record in page_records
        if (
            record.get("schema")
            or {}
        ).get("document_type")
        == "ACORD_25"
    ]

    endorsement_records = [
        record
        for record in page_records
        if (
            record.get("schema")
            or {}
        ).get("document_type")
        == "ENDORSEMENT"
    ]

    other_records = [
        record
        for record in page_records
        if (
            record.get("schema")
            or {}
        ).get("document_type")
        not in {
            "ACORD_25",
            "ENDORSEMENT",
        }
    ]

    submissions = []

    # -----------------------------------------------------
    # Create one starting submission per ACORD
    # -----------------------------------------------------

    for submission_number, acord_record in enumerate(
        acord_records,
        start=1
    ):
        acord_schema = (
            acord_record.get("schema")
            or {}
        )

        submissions.append({
            "submission_id":
                f"SUB-{submission_number:03d}",
            "group_type":
                "ACORD_BASED",
            "acord_record":
                acord_record,
            "endorsement_records": [],
            "association_results": [],
            "named_insured":
                acord_schema.get(
                    "named_insured"
                ),
            "policy_number":
                None,
            "carrier":
                None,
            "missing_documents": [],
            "requires_review":
                False,
            "group_reason": (
                "Submission was initialized "
                "from an ACORD 25"
            ),
        })

    # -----------------------------------------------------
    # Associate endorsements to ACORD submissions
    # -----------------------------------------------------

    unmatched_endorsements = []

    for endorsement_record in (
        endorsement_records
    ):
        candidate_matches = []

        for submission in submissions:

            result = (
                associate_endorsement_to_acord(
                    submission[
                        "acord_record"
                    ],
                    endorsement_record
                )
            )

            candidate_matches.append({
                "submission":
                    submission,
                "result":
                    result,
            })

        candidate_matches.sort(
            key=lambda candidate:
                candidate["result"]["score"],
            reverse=True
        )

        if not candidate_matches:
            unmatched_endorsements.append(
                endorsement_record
            )

            continue

        best_candidate = candidate_matches[0]
        best_result = best_candidate[
            "result"
        ]

        second_score = None

        if len(candidate_matches) > 1:
            second_score = candidate_matches[
                1
            ]["result"]["score"]

        winning_margin = (
            best_result["score"]
            - second_score
            if second_score is not None
            else best_result["score"]
        )

        clear_winner = (
            second_score is None
            or winning_margin
            >= MINIMUM_WINNING_MARGIN
        )

        if (
            best_result["associated"]
            and clear_winner
        ):
            best_candidate["submission"][
                "endorsement_records"
            ].append(
                endorsement_record
            )

            best_candidate["submission"][
                "association_results"
            ].append({
                **best_result,
                "association_type":
                    "ENDORSEMENT_TO_ACORD",
                "winning_margin":
                    winning_margin,
            })

        else:
            unmatched_endorsements.append(
                endorsement_record
            )

    # -----------------------------------------------------
    # Group remaining endorsements with one another
    # -----------------------------------------------------

    endorsement_group_result = (
        build_endorsement_only_groups(
            unmatched_endorsements,
            starting_number=(
                len(submissions) + 1
            )
        )
    )

    submissions.extend(
        endorsement_group_result["groups"]
    )

    unassociated_records = (
        endorsement_group_result[
            "unresolved_records"
        ]
    )

    # -----------------------------------------------------
    # Counts and final batch result
    # -----------------------------------------------------

    acord_submission_count = len([
        submission
        for submission in submissions
        if submission["group_type"]
        == "ACORD_BASED"
    ])

    endorsement_only_group_count = len([
        submission
        for submission in submissions
        if submission["group_type"]
        == "ENDORSEMENT_ONLY"
    ])

    return {
        "submissions":
            submissions,
        "unassociated_records":
            unassociated_records,
        "other_records":
            other_records,
        "acord_count":
            len(acord_records),
        "endorsement_count":
            len(endorsement_records),
        "acord_submission_count":
            acord_submission_count,
        "endorsement_only_group_count":
            endorsement_only_group_count,
    }