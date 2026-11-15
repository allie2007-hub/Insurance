import re


# =========================================================
# Text preparation
# =========================================================

def get_page_text(page):
    """
    Combines the lines Azure identified on one page.
    """

    if not page.lines:
        return ""

    return "\n".join(
        line.content
        for line in page.lines
        if line.content
    )


def normalize_text(value):
    """
    Normalizes OCR text for more consistent matching.
    """

    if not value:
        return ""

    value = str(value).upper()

    value = (
        value
        .replace("–", "-")
        .replace("—", "-")
        .replace("‐", "-")
        .replace("‑", "-")
        .replace("\xa0", " ")
    )

    value = re.sub(
        r"[ \t]+",
        " ",
        value
    )

    return value.strip()


def contains_any(text, phrases):
    """
    Returns True when any phrase appears in the text.
    """

    return any(
        phrase in text
        for phrase in phrases
    )


def count_matches(text, phrases):
    """
    Counts how many expected phrases appear in the text.
    """

    return sum(
        1
        for phrase in phrases
        if phrase in text
    )


# =========================================================
# Document-type detection
# =========================================================

def is_acord_25(header, text):
    """
    Identifies an ACORD 25 certificate.

    The certificate title must appear in the header.
    The ACORD identifier may appear anywhere because Azure OCR
    sometimes reads the footer/logo later in the page order.
    """

    certificate_title = (
        "CERTIFICATE OF LIABILITY INSURANCE"
        in header
    )

    acord_identifier = (
        "ACORD" in text
    )

    certificate_fields = count_matches(
        text,
        (
            "PRODUCER",
            "INSURED",
            "INSURER",
            "COVERAGES",
            "CERTIFICATE HOLDER",
            "POLICY NUMBER",
            "POLICY EFF",
            "POLICY EXP",
        )
    )

    return (
        certificate_title
        and acord_identifier
        and certificate_fields >= 3
    )


def is_certificate_equivalent(header, text):
    """
    Identifies a liability certificate that does not show
    the ACORD identifier.

    These documents remain separate from ACORD_25 because an
    approved equivalent may require human confirmation.
    """

    certificate_title = contains_any(
        header,
        (
            "CERTIFICATE OF LIABILITY INSURANCE",
            "CERTIFICATE OF INSURANCE",
            "EVIDENCE OF INSURANCE",
        )
    )

    certificate_fields = count_matches(
        text,
        (
            "PRODUCER",
            "INSURED",
            "INSURER",
            "COVERAGES",
            "CERTIFICATE HOLDER",
            "POLICY NUMBER",
            "EFFECTIVE DATE",
            "EXPIRATION DATE",
        )
    )

    return (
        certificate_title
        and certificate_fields >= 3
        and "ACORD" not in text
    )


def is_acord_101(header, text):
    """
    Identifies an ACORD 101 Additional Remarks Schedule.
    """

    return (
        "ADDITIONAL REMARKS SCHEDULE"
        in header
        and (
            "ACORD 101" in header
            or "ACORD 101" in text
        )
    )


def is_continuation_page(header, text):
    """
    Identifies continuation or remarks pages that are not
    clearly labeled as ACORD 101.
    """

    continuation_title = contains_any(
        header,
        (
            "CONTINUATION PAGE",
            "CONTINUATION OF",
            "ADDITIONAL REMARKS",
            "ADDITIONAL INFORMATION",
            "REMARKS SCHEDULE",
            "SCHEDULE OF FORMS",
        )
    )

    certificate_reference = contains_any(
        text,
        (
            "CERTIFICATE OF LIABILITY INSURANCE",
            "CERTIFICATE NUMBER",
            "CERTIFICATE HOLDER",
            "NAMED INSURED",
            "POLICY NUMBER",
        )
    )

    return (
        continuation_title
        and certificate_reference
    )


def is_endorsement(header, text):
    """
    Identifies a policy endorsement.

    Detection requires policy-changing or endorsement-specific
    wording rather than any isolated use of the word endorsement.
    """

    strong_phrases = (
        "THIS ENDORSEMENT CHANGES THE POLICY",
        "THIS ENDORSEMENT MODIFIES INSURANCE",
        "THIS ENDORSEMENT CHANGES THE POLICY. "
        "PLEASE READ IT CAREFULLY",
        "SCHEDULE OF ENDORSEMENTS",
    )

    if contains_any(
        text,
        strong_phrases
    ):
        return True

    endorsement_title = (
        "ENDORSEMENT" in header
    )

    policy_context = contains_any(
        text,
        (
            "POLICY NUMBER",
            "POLICY PERIOD",
            "NAMED INSURED",
            "COVERAGE PART",
            "INSURANCE COMPANY",
            "EFFECTIVE DATE OF ENDORSEMENT",
            "ALL OTHER TERMS AND CONDITIONS",
        )
    )

    return (
        endorsement_title
        and policy_context
    )


def is_policy_extract(header, text):
    """
    Identifies pages that appear to be excerpts from an
    insurance policy rather than standalone endorsements.

    This labels the document only. Detailed policy extraction
    and interpretation remain separate downstream tasks.
    """

    policy_titles = contains_any(
        header,
        (
            "COMMERCIAL GENERAL LIABILITY",
            "BUSINESS AUTO COVERAGE FORM",
            "COMMERCIAL AUTO COVERAGE",
            "WORKERS COMPENSATION",
            "EMPLOYERS LIABILITY",
            "UMBRELLA LIABILITY",
            "EXCESS LIABILITY",
            "DECLARATIONS",
            "POLICY DECLARATIONS",
            "COVERAGE FORM",
        )
    )

    policy_structure = count_matches(
        text,
        (
            "SECTION I",
            "SECTION II",
            "COVERAGES",
            "EXCLUSIONS",
            "DEFINITIONS",
            "CONDITIONS",
            "LIMIT OF INSURANCE",
            "WHO IS AN INSURED",
            "POLICY NUMBER",
            "NAMED INSURED",
        )
    )

    return (
        policy_titles
        and policy_structure >= 2
    )


def is_correspondence(header, text):
    """
    Identifies typical email, letter, or transmittal correspondence.

    Correspondence should not be treated as policy evidence without
    reviewer confirmation.
    """

    email_signals = count_matches(
        text,
        (
            "FROM:",
            "TO:",
            "SENT:",
            "SUBJECT:",
            "CC:",
        )
    )

    letter_signals = contains_any(
        header,
        (
            "DEAR ",
            "TO WHOM IT MAY CONCERN",
            "RE:",
            "SUBJECT:",
            "TRANSMITTAL",
            "COVER LETTER",
        )
    )

    communication_language = contains_any(
        text,
        (
            "PLEASE FIND ATTACHED",
            "PLEASE SEE ATTACHED",
            "ATTACHED IS",
            "WE ARE PROVIDING",
            "REQUESTED CERTIFICATE",
            "REVISED CERTIFICATE",
            "CORRECTED CERTIFICATE",
        )
    )

    return (
        email_signals >= 3
        or (
            letter_signals
            and communication_language
        )
    )


# =========================================================
# Evidence tagging
# =========================================================

def detect_evidence_tags(text):
    """
    Detects insurance evidence mentioned on the page.

    Evidence tags do not replace document classification and do
    not automatically prove compliance.
    """

    evidence_tags = []

    if "ADDITIONAL INSURED" in text:
        evidence_tags.append(
            "ADDITIONAL_INSURED"
        )

    if contains_any(
        text,
        (
            "ONGOING OPERATIONS",
            "ONGOING OPERATION",
            "YOUR WORK FOR THE ADDITIONAL INSURED",
            "LIABILITY ARISING OUT OF YOUR ONGOING OPERATIONS",
        )
    ):
        evidence_tags.append(
            "ONGOING_OPERATIONS"
        )

    if contains_any(
        text,
        (
            "PRODUCTS-COMPLETED OPERATIONS",
            "PRODUCTS - COMPLETED OPERATIONS",
            "COMPLETED OPERATIONS",
            "PRODUCTS/COMPLETED OPERATIONS",
        )
    ):
        evidence_tags.append(
            "COMPLETED_OPERATIONS"
        )

    if contains_any(
        text,
        (
            "WAIVER OF SUBROGATION",
            "WAIVER OF TRANSFER OF RIGHTS OF RECOVERY",
            "WE WAIVE ANY RIGHT OF RECOVERY",
            "TRANSFER OF RIGHTS OF RECOVERY AGAINST OTHERS",
        )
    ):
        evidence_tags.append(
            "WAIVER_OF_SUBROGATION"
        )

    if contains_any(
        text,
        (
            "PRIMARY AND NON-CONTRIBUTORY",
            "PRIMARY AND NONCONTRIBUTORY",
            "PRIMARY AND NOT CONTRIBUTING",
            "PRIMARY AND NON CONTRIBUTORY",
            "PRIMARY AND SHALL NOT SEEK CONTRIBUTION",
        )
    ):
        evidence_tags.append(
            "PRIMARY_NONCONTRIBUTORY"
        )

    return sorted(
        set(evidence_tags)
    )


# =========================================================
# Revision indicators
# =========================================================

def detect_revision_indicators(header, text):
    """
    Detects wording that suggests a revised, renewed, corrected,
    or replacement document.

    This does not conclusively classify the relationship.
    Confirmation requires comparison with another submission.
    """

    indicators = []

    revision_phrases = {
        "REVISED": (
            "REVISED",
            "REVISION",
            "AMENDED",
        ),
        "CORRECTED": (
            "CORRECTED",
            "CORRECTION",
        ),
        "REPLACEMENT": (
            "REPLACEMENT",
            "REPLACES",
            "SUPERSEDES",
            "SUPERSEDED",
        ),
        "RENEWAL": (
            "RENEWAL",
            "RENEWED",
        ),
    }

    search_area = (
        header
        + "\n"
        + text[:4000]
    )

    for indicator, phrases in (
        revision_phrases.items()
    ):
        if contains_any(
            search_area,
            phrases
        ):
            indicators.append(
                indicator
            )

    return sorted(
        set(indicators)
    )


def is_endorsement_continuation(header, text):
    """
    Identifies a page that looks like a continuation of an endorsement.

    This does NOT establish the relationship by itself.
    The multi-document processor must confirm that the page directly
    follows an ENDORSEMENT in the same source file.
    """

    # Common insurance wording that can appear on continuation pages.
    continuation_phrases = (
        "PRIMARY AND NON-CONTRIBUTORY",
        "PRIMARY AND NONCONTRIBUTORY",
        "PRIMARY AND NOT CONTRIBUTING",
        "WAIVER OF SUBROGATION",
        "WAIVER OF TRANSFER OF RIGHTS OF RECOVERY",
        "ADDITIONAL INSURED",
        "COMPLETED OPERATIONS",
        "ONGOING OPERATIONS",
        "WHO IS AN INSURED",
        "ALL OTHER TERMS AND CONDITIONS",
        "THE INSURANCE AFFORDED",
        "COVERAGE PROVIDED",
    )

    evidence_count = count_matches(
        text,
        continuation_phrases
    )

    # Common endorsement form-number structures.
    form_number_found = bool(
        re.search(
            r"\b(?:CG|CA|WC|CU|IL)\s*"
            r"[0-9A-Z]{2}\s*"
            r"[0-9A-Z]{2}\b",
            text
        )
    )

    explicit_continuation = contains_any(
        header,
        (
            "CONTINUED",
            "CONTINUATION",
            "CONTINUED FROM PREVIOUS PAGE",
        )
    )

    # Require something meaningful.
    # A completely unrelated UNKNOWN page should not qualify.
    return (
        explicit_continuation
        or (
            evidence_count >= 1
            and form_number_found
        )
        or evidence_count >= 2
    )

# =========================================================
# Main classifier
# =========================================================

def classify_page(page_text):
    """
    Classifies one page and returns explainable results.

    Duplicate, replacement, and superseded status must be
    confirmed later through document-to-document comparison.
    """

    text = normalize_text(page_text)

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    header = "\n".join(
        lines[:20]
    )

    page_type = "UNKNOWN"
    confidence = "LOW"
    reasons = []

    # Order matters. Specific certificate/continuation types
    # are checked before broader endorsement/policy rules.

    if is_acord_25(header, text):
        page_type = "ACORD_25"
        confidence = "HIGH"

        reasons.extend([
            "Certificate of Liability Insurance "
            "title found in page header",
            "ACORD identifier found",
            "Certificate-specific fields found",
        ])

    elif is_acord_101(header, text):
        page_type = "ACORD_101"
        confidence = "HIGH"

        reasons.extend([
            "Additional Remarks Schedule "
            "title found",
            "ACORD 101 identifier found",
        ])

    elif is_continuation_page(
        header,
        text
    ):
        page_type = "CONTINUATION"
        confidence = "MEDIUM"

        reasons.extend([
            "Continuation or remarks title found",
            "Certificate or policy reference found",
        ])

    elif is_certificate_equivalent(
        header,
        text
    ):
        page_type = "CERTIFICATE_EQUIVALENT"
        confidence = "MEDIUM"

        reasons.extend([
            "Certificate title found",
            "Certificate-specific fields found",
            "ACORD identifier not found",
        ])

    elif is_endorsement(header, text):
        page_type = "ENDORSEMENT"
        confidence = "HIGH"

        reasons.extend([
            "Endorsement or policy-changing "
            "language found",
            "Policy context found",
        ])

    elif is_endorsement_continuation(header,text):
        page_type = "ENDORSEMENT_CONTINUATION"
        confidence = "MEDIUM"

        reasons.extend([
            "Endorsement-related wording found",
            "Page appears to continue endorsement content",
        ])

    elif is_policy_extract(
        header,
        text
    ):
        page_type = "POLICY_EXTRACT"
        confidence = "MEDIUM"

        reasons.extend([
            "Policy or coverage-form title found",
            "Policy section structure found",
        ])

    elif is_correspondence(
        header,
        text
    ):
        page_type = "CORRESPONDENCE"
        confidence = "MEDIUM"

        reasons.append(
            "Email, letter, or transmittal "
            "structure found"
        )

    elif not text:
        page_type = "UNKNOWN"
        confidence = "NONE"

        reasons.append(
            "No readable text was extracted"
        )

    else:
        page_type = "UNKNOWN"
        confidence = "LOW"

        reasons.append(
            "No supported document type "
            "was identified confidently"
        )

    evidence_tags = (
        detect_evidence_tags(text)
    )

    revision_indicators = (
        detect_revision_indicators(
            header,
            text
        )
    )

    return {
        "page_type":
            page_type,
        "classification_confidence":
            confidence,
        "classification_reasons":
            reasons,
        "evidence_tags":
            evidence_tags,
        "revision_indicators":
            revision_indicators,
    }