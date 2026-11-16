import re
from collections import Counter
from datetime import date

from extractors.acord_layout import extract_acord_layout
from reference.endorsement_chart import (
    lookup_form,
    normalize_form_number,
)


# =========================================================
# GENERAL HELPERS
# =========================================================

KNOWN_LABELS = {
    "NAMED INSURED",
    "ENDORSEMENT NUMBER",
    "POLICY SYMBOL",
    "POLICY NUMBER",
    "POLICY PERIOD",
    "EFFECTIVE DATE OF ENDORSEMENT",
    "ISSUED BY",
    "ISSUED BY (NAME OF INSURANCE COMPANY)",
    "PRODUCER",
    "INSURED",
    "CONTACT",
    "NAME:",
    "PHONE",
    "FAX",
    "E-MAIL",
    "ADDRESS:",
    "COVERAGES",
    "CERTIFICATE HOLDER",
    "CANCELLATION",
}


def normalize_lines(text):
    """
    Converts Azure output into a clean list of non-empty lines.
    """

    lines = []

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()

        if line:
            lines.append(line)

    return lines


def normalize_label(value):
    """
    Normalizes labels so that punctuation and spacing differences
    do not prevent a match.
    """

    return re.sub(
        r"[^A-Z0-9]+",
        " ",
        value.upper()
    ).strip()


def find_exact_line(lines, label, start=0):
    """
    Finds a label only when the whole line matches.

    This prevents the word INSURED inside ADDITIONAL INSURED
    from being mistaken for the ACORD INSURED heading.
    """

    target = normalize_label(label)

    for index in range(start, len(lines)):
        if normalize_label(lines[index]) == target:
            return index

    return None


def find_line_containing(lines, phrase, start=0, end=None):
    """
    Finds a line containing a phrase.
    """

    phrase = phrase.upper()

    if end is None:
        end = len(lines)

    for index in range(start, min(end, len(lines))):
        if phrase in lines[index].upper():
            return index

    return None


def is_known_label(value):
    normalized = normalize_label(value)

    return normalized in {
        normalize_label(label)
        for label in KNOWN_LABELS
    }


def clean_value(value):
    if value is None:
        return None

    value = re.sub(r"\s+", " ", value).strip(" :|")

    return value or None


def split_name_and_address(value):
    """
    Separates an organization name from obvious address lines while
    preserving the original extracted block.

    This is intentionally conservative: if no address signal is found,
    the full value remains the name.
    """
    if not value:
        return {"name": None, "address": None, "raw_value": None}

    lines = normalize_lines(value)
    if not lines:
        return {"name": None, "address": None, "raw_value": value}

    address_start = None
    for index, line in enumerate(lines):
        upper = line.upper()
        has_street_number = bool(re.match(r"^\d{1,6}\s+", line))
        has_city_state_zip = bool(re.search(
            r"\b[A-Z .'-]+,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",
            upper
        ))
        if has_street_number or has_city_state_zip:
            address_start = index
            break

    if address_start is None:
        return {
            "name": clean_value(" ".join(lines)),
            "address": None,
            "raw_value": value,
        }

    name_lines = lines[:address_start]
    address_lines = lines[address_start:]

    return {
        "name": clean_value(" ".join(name_lines)) or clean_value(lines[0]),
        "address": clean_value("\n".join(address_lines)),
        "raw_value": value,
    }


def collect_lines_between(
    lines,
    start_label,
    end_labels,
    max_lines=12
):
    """
    Collects lines following an exact heading until another
    specified heading is reached.
    """

    start_index = find_exact_line(lines, start_label)

    if start_index is None:
        return None

    end_targets = {
        normalize_label(label)
        for label in end_labels
    }

    values = []

    for line in lines[
        start_index + 1:
        start_index + 1 + max_lines
    ]:
        if normalize_label(line) in end_targets:
            break

        if not is_known_label(line):
            values.append(line)

    return clean_value("\n".join(values))


def is_date(value):
    return bool(
        re.fullmatch(
            r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
            value.strip()
        )
    )


def is_money(value):
    return bool(
        re.fullmatch(
            r"\$?\s*\d[\d,]*(?:\.\d{2})?",
            value.strip()
        )
    )


def normalize_money(value):
    if not value:
        return None

    digits = re.sub(r"[^\d.]", "", value)

    return digits or None


DATE_PATTERN = r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"


def normalize_date(value):
    """
    Converts MM/DD/YYYY, MM/DD/YY, MM-DD-YYYY to ISO "YYYY-MM-DD".

    Returns None for anything that is not a real calendar date
    (for example the XX/XX/XX placeholders on blank forms), so later
    steps can compare dates and detect expired policies.
    """

    if not value:
        return None

    match = re.fullmatch(
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})",
        str(value).strip()
    )

    if not match:
        return None

    month, day, year = (int(part) for part in match.groups())

    if year < 100:
        year += 2000

    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def find_dates(text):
    """Returns every valid date in the text, normalized to ISO."""

    dates = []

    for raw in re.findall(DATE_PATTERN, text or ""):
        normalized = normalize_date(raw)

        if normalized:
            dates.append(normalized)

    return dates


def looks_like_policy_number(value):
    """
    Applies conservative checks so dates, dollar amounts,
    and headings are not returned as policy numbers.
    """

    value = value.strip()

    if is_date(value):
        return False

    if is_money(value):
        return False

    if len(value) < 6:
        return False

    if " " in value and not any(
        character.isdigit()
        for character in value
    ):
        return False

    if not any(character.isdigit() for character in value):
        return False

    if not re.fullmatch(r"[A-Z0-9\- ]+", value.upper()):
        return False

    rejected = {
        "POLICY NUMBER",
        "POLICY EFF",
        "POLICY EXP",
        "LIMITS",
        "CLAIMS MADE",
        "OCCUR",
    }

    return normalize_label(value) not in {
        normalize_label(item)
        for item in rejected
    }


def get_section(lines, start_phrase, end_phrases):
    """
    Returns the lines for one coverage section of an ACORD 25.
    """

    start_index = find_line_containing(
        lines,
        start_phrase
    )

    if start_index is None:
        return []

    end_index = len(lines)

    for end_phrase in end_phrases:
        possible_end = find_line_containing(
            lines,
            end_phrase,
            start=start_index + 1
        )

        if (
            possible_end is not None
            and possible_end < end_index
        ):
            end_index = possible_end

    return lines[start_index:end_index]


def find_first_money_after_label(
    lines,
    label,
    search_distance=10
):
    """
    Finds the first dollar amount after a coverage-limit label.
    """

    label_index = find_line_containing(lines, label)

    if label_index is None:
        return None

    for line in lines[
        label_index + 1:
        label_index + 1 + search_distance
    ]:
        match = re.search(
            r"\$\s*([\d,]+(?:\.\d{2})?)",
            line
        )

        if match:
            return normalize_money(match.group(1))

    return None


def find_policy_number_in_section(section_lines):
    """
    Finds a likely policy number within one coverage section.
    """

    for line in section_lines:
        # The ADDL INSD / SUBR WVD checkbox marks ("X") often share the
        # policy-number line: "X X K15715". Left in place they break the
        # policy-number match used by association.
        line = re.sub(r"^(?:X\s+)+", "", line.strip())

        if looks_like_policy_number(line):
            return clean_value(line)

    return None


def find_date_pair_in_section(section_lines):
    """
    Finds the first effective-date and expiration-date pair
    within one coverage section.
    """

    dates = []

    for line in section_lines:
        dates.extend(find_dates(line))

    if len(dates) >= 2:
        return {
            "effective_date": dates[0],
            "expiration_date": dates[1],
        }

    return {
        "effective_date": None,
        "expiration_date": None,
    }


# =========================================================
# ENDORSEMENT EXTRACTION
# =========================================================

def extract_endorsement_named_insured(text):
    lines = normalize_lines(text)

    return collect_lines_between(
        lines,
        "NAMED INSURED",
        ["ENDORSEMENT NUMBER"],
        max_lines=5
    )


def extract_endorsement_number(text):
    lines = normalize_lines(text)

    index = find_exact_line(
        lines,
        "ENDORSEMENT NUMBER"
    )

    if index is None or index + 1 >= len(lines):
        return None

    candidate = lines[index + 1]

    # Some endorsements leave this field blank, causing the next
    # label, such as POLICY SYMBOL, to appear immediately afterward.
    if is_known_label(candidate):
        return None

    if re.fullmatch(r"[A-Z0-9\-]+", candidate.upper()):
        return candidate

    return None


def extract_policy_symbol(text):
    lines = normalize_lines(text)

    index = find_exact_line(
        lines,
        "POLICY SYMBOL"
    )

    if index is None or index + 1 >= len(lines):
        return None

    candidate = lines[index + 1]

    if is_known_label(candidate):
        return None

    if re.fullmatch(r"[A-Z0-9\-]+", candidate.upper()):
        return candidate

    return None


def extract_endorsement_policy_number(text):
    lines = normalize_lines(text)

    index = find_exact_line(
        lines,
        "POLICY NUMBER"
    )

    if index is None:
        return None

    for candidate in lines[index + 1:index + 4]:
        if is_known_label(candidate):
            continue

        if looks_like_policy_number(candidate):
            return candidate

    return None


def extract_policy_period(text):
    match = re.search(
        r"POLICY PERIOD\s*\n?\s*"
        r"(\d{1,2}/\d{1,2}/\d{2,4})"
        r"\s*(?:TO|-)\s*"
        r"(\d{1,2}/\d{1,2}/\d{2,4})",
        text,
        re.IGNORECASE
    )

    if match:
        return {
            "effective_date": normalize_date(match.group(1)),
            "expiration_date": normalize_date(match.group(2)),
        }

    return {
        "effective_date": None,
        "expiration_date": None,
    }


def extract_endorsement_effective_date(text):
    match = re.search(
        r"EFFECTIVE DATE OF ENDORSEMENT\s*\n?\s*"
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        text,
        re.IGNORECASE
    )

    return normalize_date(match.group(1)) if match else None


# ---------------------------------------------------------
# Endorsement wording interpretation
# ---------------------------------------------------------

def _text_has_any(text, phrases):
    upper = (text or "").upper()
    return any(phrase in upper for phrase in phrases)


def extract_endorsement_wording(text, evidence_tags=None):
    """Extract coverage meaning without changing association identity fields.

    This helper intentionally does not extract or overwrite named insured,
    policy number, or carrier. Those fields affect document association and
    must remain handled by the existing conservative extractors.
    """
    upper = (text or "").upper()
    tags = set(evidence_tags or [])

    additional_insured = (
        "ADDITIONAL_INSURED" in tags
        or _text_has_any(upper, (
            "ADDITIONAL INSURED",
            "WHO IS AN INSURED IS AMENDED TO INCLUDE",
            "WHO IS AN INSURED IS AMENDED TO ADD",
        ))
    )

    ongoing_operations = (
        "ONGOING_OPERATIONS" in tags
        or _text_has_any(upper, (
            "ONGOING OPERATIONS",
            "PERFORMANCE OF YOUR ONGOING OPERATIONS",
        ))
    )

    completed_operations = (
        "COMPLETED_OPERATIONS" in tags
        or _text_has_any(upper, (
            "PRODUCTS-COMPLETED OPERATIONS HAZARD",
            "PRODUCTS/COMPLETED OPERATIONS",
            "COMPLETED OPERATIONS COVERAGE",
        ))
    )

    primary_confirmed = _text_has_any(upper, (
        "PRIMARY AND NONCONTRIBUTORY",
        "PRIMARY AND NON-CONTRIBUTORY",
        "THIS INSURANCE IS PRIMARY",
        "INSURANCE IS PRIMARY TO",
        "WILL APPLY ON A PRIMARY",
    ))

    noncontributory_confirmed = _text_has_any(upper, (
        "NONCONTRIBUTORY",
        "NON-CONTRIBUTORY",
        "WILL NOT SEEK CONTRIBUTION",
        "SHALL NOT SEEK CONTRIBUTION",
        "NOT SEEK CONTRIBUTION",
    ))

    written_contract_trigger = bool(re.search(
        r"(?:REQUIRED|AGREED|OBLIGATED).*?"
        r"(?:WRITTEN\s+)?(?:CONTRACT|AGREEMENT|PERMIT)",
        upper,
        re.DOTALL,
    ))

    blanket_additional_insured = additional_insured and bool(re.search(
        r"ANY\s+(?:PERSON|ORGANIZATION|PERSON\s+OR\s+ORGANIZATION)",
        upper,
    )) and written_contract_trigger

    direct_contract_required = _text_has_any(upper, (
        "WITH WHOM YOU HAVE AGREED",
        "WITH WHOM THE NAMED INSURED HAS AGREED",
        "DIRECT CONTRACT",
    ))

    if _text_has_any(upper, (
        "CAUSED IN WHOLE OR IN PART",
        "CAUSED, IN WHOLE OR IN PART",
    )):
        negligence_trigger_status = "CONFIRMED"
    elif "ARISING OUT OF" in upper:
        negligence_trigger_status = "REVIEW"
    else:
        negligence_trigger_status = None

    restrictions = []

    # Only treat sole-negligence wording as a restriction when the
    # nearby wording actually limits additional-insured coverage.
    sole_negligence_restriction = re.search(
        r"(?:ADDITIONAL\s+INSURED|INSURANCE\s+AFFORDED)"
        r".{0,700}"
        r"(?:SOLE\s+NEGLIGENCE|SOLELY\s+NEGLIGENT)"
        r"|"
        r"(?:SOLE\s+NEGLIGENCE|SOLELY\s+NEGLIGENT)"
        r".{0,700}"
        r"(?:ADDITIONAL\s+INSURED|INSURANCE\s+AFFORDED)",
        upper,
        re.DOTALL,
    )

    if sole_negligence_restriction:
        restrictions.append(
            "Sole-negligence limitation requires review"
        )

    # A vicarious-liability restriction matters only when it is tied
    # closely to the additional-insured grant or limitation.
    vicarious_restriction = re.search(
        r"(?:ADDITIONAL\s+INSURED|INSURANCE\s+AFFORDED)"
        r".{0,700}"
        r"VICARIOUS\s+LIABILITY"
        r"|"
        r"VICARIOUS\s+LIABILITY"
        r".{0,700}"
        r"(?:ADDITIONAL\s+INSURED|INSURANCE\s+AFFORDED)",
        upper,
        re.DOTALL,
    )

    if vicarious_restriction:
        restrictions.append(
            "Vicarious-liability limitation requires review"
        )

    # Do not connect unrelated appearances of "does not apply" and
    # "professional". Require actual professional-services exclusion
    # wording in one local clause.
    professional_services_exclusion = re.search(
        r"(?:THIS\s+INSURANCE|COVERAGE|INSURANCE\s+AFFORDED)"
        r".{0,500}"
        r"DOES\s+NOT\s+APPLY"
        r".{0,500}"
        r"(?:PROFESSIONAL\s+SERVICES|"
        r"ARCHITECTURAL|ENGINEERING|SURVEYING)"
        r"|"
        r"DOES\s+NOT\s+APPLY"
        r".{0,500}"
        r"(?:PROFESSIONAL\s+SERVICES|"
        r"ARCHITECTURAL|ENGINEERING|SURVEYING)",
        upper,
        re.DOTALL,
    )

    if professional_services_exclusion:
        restrictions.append(
            "Professional-services exclusion is present"
        )

    # Only flag voiding language when "void" is locally connected to
    # additional-insured coverage rather than somewhere else in the policy.
    void_ai_coverage = re.search(
        r"(?:ADDITIONAL\s+INSURED)"
        r".{0,500}"
        r"(?:VOID|VOIDED)"
        r"|"
        r"(?:VOID|VOIDED)"
        r".{0,500}"
        r"(?:ADDITIONAL\s+INSURED)",
        upper,
        re.DOTALL,
    )

    if void_ai_coverage:
        restrictions.append(
            "Additional-insured coverage may be voided by another form"
        )

    protected_parties = []
    if re.search(r"\bR\s*(?:&|AND)\s*O\s+CONSTRUCTION", upper):
        protected_parties.append("R&O Construction Company")
    if re.search(r"\bOWNER(?:S)?\b", upper):
        protected_parties.append("Owner")

    return {
        "additional_insured_status": (
            "CONFIRMED" if additional_insured else None
        ),
        "ongoing_operations_status": (
            "CONFIRMED" if ongoing_operations else None
        ),
        "completed_operations_status": (
            "CONFIRMED" if completed_operations else None
        ),
        "primary_status": (
            "CONFIRMED" if primary_confirmed else None
        ),
        "noncontributory_status": (
            "CONFIRMED" if noncontributory_confirmed else None
        ),
        "blanket_additional_insured": blanket_additional_insured,
        "blanket_applies": (
            True if blanket_additional_insured else None
        ),
        "written_contract_trigger": written_contract_trigger,
        "direct_contract_required": direct_contract_required,
        "direct_contract_confirmed": (
            False if not direct_contract_required else None
        ),
        "negligence_trigger_status": negligence_trigger_status,
        "protected_parties": protected_parties,
        "protected_party_status": None,
        "material_restrictions": restrictions,
        "semantic_extraction_method": "ENDORSEMENT_WORDING_RULES_V1",
    }


# ---------------------------------------------------------
# Endorsement form numbers
# ---------------------------------------------------------

# Letter-prefixed numbers, with or without an edition date:
# CG 20 10 07 04, CA 99 48, WC 00 03 13, HG 00 01 09 16,
# CSGA 435 11 08, ENV-3101, CG D2 46 08 05
PREFIXED_FORM_PATTERN = re.compile(
    r"\b([A-Z]{2,5})[ -]?"
    r"([A-Z]?\d[0-9A-Z]{0,9}(?:[ -](?:[A-Z]\d|\d{2,3})){0,4})"
    r"(?:\s*\((\d{1,2})\s?[/-]\s?(\d{2,4})\))?"
)

# Numbers followed by a bracketed edition date:
# "U-GL-1081-B CW (04/13)", "97837 (4/08)", "CNA74879XX (1-15)"
EDITION_FORM_PATTERN = re.compile(
    r"\b([A-Z0-9]{1,12}(?:-[A-Z0-9]{1,6}){0,4}(?: [A-Z]{2})?)"
    r"\s*\((\d{1,2})\s?[/-]\s?(\d{2,4})\)"
)

# Two-letter state + ZIP code ("SD 57709") is an address, not a form.
STATE_ZIP_PATTERN = re.compile(r"^[A-Z]{2} ?\d{5}$")


def _looks_like_form_number(candidate):
    digits = sum(character.isdigit() for character in candidate)

    if digits < 4:
        return False

    if STATE_ZIP_PATTERN.match(candidate.split(" (")[0]):
        return False

    return True


def extract_form_numbers(text):
    """
    Finds endorsement form numbers on a page.

    Form numbers are the most reliable signal of what an endorsement
    does (CG 20 10 = AI ongoing operations, CG 20 37 = AI completed
    operations, CG 24 04 = waiver of subrogation). They usually appear in
    the page footer, so the number that repeats most often comes first.

    This returns candidates; R&O's chart lookup decides which are real.
    Policy numbers can look similar and may appear in the list.
    """

    upper = (text or "").upper()
    upper = upper.replace("‐", "-").replace("‑", "-")
    upper = upper.replace("\xa0", " ")
    found = []

    for match in PREFIXED_FORM_PATTERN.finditer(upper):
        prefix, body, month, year = match.groups()
        candidate = f"{prefix} {body}".strip()

        if month:
            candidate += f" ({month}/{year})"

        found.append(candidate)

    for match in EDITION_FORM_PATTERN.finditer(upper):
        number = match.group(1).strip()
        found.append(f"{number} ({match.group(2)}/{match.group(3)})")

    found = [item for item in found if _looks_like_form_number(item)]

    # Drop numbers that are only a prefix of a longer number found
    # (e.g. "CG 20 10" when "CG 20 10 07 04" is present).
    keys = [normalize_form_number(item) for item in found]
    counts = Counter(keys)
    ordered = []
    seen = set()

    for item, key in sorted(
        zip(found, keys),
        key=lambda pair: -counts[pair[1]]
    ):
        if key in seen:
            continue

        if any(
            other != key and other.startswith(key)
            for other in keys
        ):
            continue

        seen.add(key)
        ordered.append(item)

    return ordered


def lookup_form_numbers(form_numbers):
    """
    Looks each form number up in R&O's Additional Insured chart.
    Returns only the forms the chart knows about.
    """

    matches = []

    for form_number in form_numbers:
        entry = lookup_form(form_number)

        if entry:
            matches.append({
                "extracted_form_number": form_number,
                **entry
            })

    return matches


def find_primary_form_number(text, form_numbers):
    """
    Picks the form number that identifies THIS endorsement.

    Endorsement wording often cites other forms ("no broader than
    CG 20 10 11 85"), so a cited form must not be credited as evidence.
    The page's own number is printed in the header or footer, so a
    candidate found in the first or last lines wins; repetition
    (the order of form_numbers) breaks ties.
    """

    if not form_numbers:
        return None

    lines = normalize_lines((text or "").upper())

    # Footer first (ISO prints "CG 20 10 07 04 © ISO" at the bottom),
    # then the header, then the most repeated number.
    zones = [
        normalize_form_number(" ".join(lines[-1:])),
        normalize_form_number(" ".join(lines[-3:])),
        normalize_form_number(" ".join(lines[-8:])),
        normalize_form_number(" ".join(lines[:6])),
    ]

    for zone in zones:
        for form_number in form_numbers:
            key = normalize_form_number(form_number.split(" (")[0])

            if key and key in zone:
                return form_number

    return form_numbers[0]


def extract_endorsement_carrier(text):
    lines = normalize_lines(text)

    index = None

    for position, line in enumerate(lines):
        if line.upper().startswith("ISSUED BY"):
            index = position
            break

    if index is None or index + 1 >= len(lines):
        return None

    candidate = lines[index + 1]

    if is_known_label(candidate):
        return None

    return clean_value(candidate)


def build_endorsement_schema(text, evidence_tags=None):
    policy_period = extract_policy_period(text)
    form_numbers = extract_form_numbers(text)
    primary_form = find_primary_form_number(text, form_numbers)
    primary_matches = lookup_form_numbers(
        [primary_form] if primary_form else []
    )
    wording = extract_endorsement_wording(
        text,
        evidence_tags=evidence_tags,
    )

    return {
        "document_type": "ENDORSEMENT",
        "evidence_tags": evidence_tags or [],
        # Preserve the existing conservative identity extraction. Semantic
        # wording analysis must not change association identity fields.
        "named_insured": extract_endorsement_named_insured(text),
        "carrier": extract_endorsement_carrier(text),
        "policy_number": extract_endorsement_policy_number(text),
        "policy_symbol": extract_policy_symbol(text),
        "policy_period": policy_period,
        "endorsement_number": extract_endorsement_number(text),
        "endorsement_effective_date":
            extract_endorsement_effective_date(text),
        "form_number": primary_form,
        "ro_chart_entry": (
            primary_matches[0] if primary_matches else None
        ),
        "form_numbers_on_page": form_numbers,
        **wording,
        "detailed_extraction_supported": True,
        "extraction_status": "EXTRACTED",
    }


# =========================================================
# ACORD 25 EXTRACTION
# =========================================================

def extract_acord_named_insured(text):
    """
    Uses the exact standalone INSURED heading.
    """

    lines = normalize_lines(text)

    return collect_lines_between(
        lines,
        "INSURED",
        [
            "CONTACT",
            "INSURER(S) AFFORDING COVERAGE",
        ],
        max_lines=8
    )


def extract_acord_producer(text):
    """
    Uses the exact standalone PRODUCER heading.
    """

    lines = normalize_lines(text)

    return collect_lines_between(
        lines,
        "PRODUCER",
        ["INSURED"],
        max_lines=10
    )


def extract_acord_insurers(text):
    """
    Extracts insurer names while requiring actual INSURER A-F labels.
    """

    lines = normalize_lines(text)
    insurers = {}

    for index, line in enumerate(lines):
        match = re.fullmatch(
            r"INSURER\s+([A-F])\s*:?\s*(.*)",
            line,
            re.IGNORECASE
        )

        if not match:
            continue

        letter = match.group(1).lower()
        inline_value = clean_value(match.group(2))

        carrier = inline_value

        if (
            not carrier
            and index + 1 < len(lines)
        ):
            next_line = lines[index + 1]

            # Ignore NAIC codes
            if next_line.isdigit():
                carrier = None

            # Ignore the next INSURER label
            elif next_line.upper().startswith("INSURER"):
                carrier = None

            else:
                carrier = clean_value(next_line)

            if carrier:

                invalid_values = {
                    "COVERAGES",
                    "CERTIFICATE NUMBER",
                    "REVISION NUMBER"
                }

                # Ignore insurer labels
                if carrier.upper().startswith("INSURER"):
                    continue

                # Ignore section headers
                if carrier.upper() in invalid_values:
                    continue

                insurers[f"insurer_{letter}"] = carrier

    return insurers


def extract_acord_certificate_holder(text):
    """
    Tries to extract the certificate-holder block conservatively.

    If Azure's reading order does not place the address after the
    CERTIFICATE HOLDER heading, this function returns None rather
    than returning most of the page incorrectly.
    """

    lines = normalize_lines(text)

    holder_index = find_exact_line(
        lines,
        "CERTIFICATE HOLDER"
    )

    if holder_index is None:
        return None

    values = []

    for line in lines[
        holder_index + 1:
        holder_index + 8
    ]:
        normalized = normalize_label(line)

        if normalized in {
            normalize_label("CANCELLATION"),
            normalize_label("AUTHORIZED REPRESENTATIVE"),
        }:
            break

        if not is_known_label(line):
            values.append(line)

    holder = clean_value("\n".join(values))

    # Avoid returning cancellation language as the holder.
    if holder and (
        "SHOULD ANY OF THE ABOVE" in holder.upper()
        or "POLICIES BE CANCELLED" in holder.upper()
    ):
        return None

    return holder


def extract_certificate_date(text):
    """
    The ACORD 25 issue date sits beside the "DATE (MM/DD/YYYY)" label.
    It is when the broker issued the certificate, not a policy date.
    """

    lines = normalize_lines(text)

    label_index = find_line_containing(lines, "DATE (MM/DD/YYYY)")

    if label_index is None:
        return None

    # Azure sometimes places the title line between the label and value.
    for line in lines[label_index:label_index + 4]:
        dates = find_dates(line)

        if dates:
            return dates[0]

    return None


# R&O's certificate-holder address per the Copilot reviewer
# instructions and "Insurance Requirements_New MSA's 4.10.24".
RO_HOLDER_NAME = re.compile(
    r"\bR\s*(?:&|AND)\s*O\s+CONSTRUCTION(?:\s+COMPANY)?\b"
)

RO_HOLDER_STREET = re.compile(
    r"\b933\s+WALL\s+(?:AVENUE|AVE)\b"
)

RO_HOLDER_CITY = re.compile(
    r"\bOGDEN\b"
)

RO_HOLDER_STATE = re.compile(
    r"\b(?:UT|UTAH)\b"
)

RO_HOLDER_ZIP = re.compile(
    r"\b84404(?:-\d{4})?\b"
)


def check_ro_certificate_holder(holder_text):
    """
    Compares extracted certificate-holder text with R&O's required address.

    Reading order does not matter. Azure may return:
        Ogden, UT 84404

    or:
        UT 84404
        Ogden

    Both should match when all required address components are present.
    """

    if not holder_text:
        return None

    text = re.sub(
        r"\s+",
        " ",
        holder_text.upper()
    )

    if (
        RO_HOLDER_NAME.search(text)
        and RO_HOLDER_STREET.search(text)
        and RO_HOLDER_CITY.search(text)
        and RO_HOLDER_STATE.search(text)
        and RO_HOLDER_ZIP.search(text)
    ):
        return "MATCH"

    return "MISMATCH"


def extract_description_of_operations(text):
    """
    Free-text block where brokers describe additional insureds,
    projects and "all jobs" wording. Kept as evidence only: per R&O's
    guide, a statement here never replaces an endorsement.
    """

    lines = normalize_lines(text)

    start = find_line_containing(lines, "DESCRIPTION OF OPERATIONS")

    if start is None:
        return None

    values = []

    for line in lines[start + 1:start + 15]:
        upper = line.upper()

        if (
            "CERTIFICATE HOLDER" in upper
            or upper.startswith("CANCELLATION")
        ):
            break

        if "ACORD 101" in upper and "ATTACH" in upper:
            continue

        values.append(line)

    return clean_value("\n".join(values))


def find_named_projects(description):
    """
    Flags wording that names a specific project/owner, which R&O's
    instructions mark as "User Confirmation Required", versus blanket
    "all jobs / all projects / as required by written contract" wording.
    """

    if not description:
        return {
            "blanket_wording": False,
            "names_specific_project": False,
        }

    upper = description.upper()

    blanket = bool(re.search(
        r"ALL (JOBS|PROJECTS|OPERATIONS|LOCATIONS)"
        r"|(REQUIRED|AGREED) (BY|IN) (A )?WRITTEN (CONTRACT|AGREEMENT)"
        r"|WHERE REQUIRED BY (WRITTEN )?CONTRACT",
        upper
    ))

    specific = bool(re.search(
        r"\b(PROJECT|JOB|RE:|LOCATION)\s*(NAME|#|NO\.?|:)",
        upper
    ))

    return {
        "blanket_wording": blanket,
        "names_specific_project": specific,
    }


def extract_acord_coverages(text):
    lines = normalize_lines(text)

    gl_section = get_section(
        lines,
        "COMMERCIAL GENERAL LIABILITY",
        [
            "AUTOMOBILE LIABILITY",
            "UMBRELLA LIAB",
            "WORKERS COMPENSATION",
            "POLLUTION LIABILITY",
        ]
    )

    auto_section = get_section(
        lines,
        "AUTOMOBILE LIABILITY",
        [
            "UMBRELLA LIAB",
            "EXCESS LIAB",
            "WORKERS COMPENSATION",
            "POLLUTION LIABILITY",
        ]
    )

    umbrella_section = get_section(
        lines,
        "UMBRELLA LIAB",
        [
            "WORKERS COMPENSATION",
            "POLLUTION LIABILITY",
            "PROFESSIONAL LIABILITY",
        ]
    )

    workers_comp_section = get_section(
        lines,
        "WORKERS COMPENSATION",
        [
            "POLLUTION LIABILITY",
            "PROFESSIONAL LIABILITY",
            "DESCRIPTION OF OPERATIONS",
        ]
    )

    gl_dates = find_date_pair_in_section(gl_section)
    auto_dates = find_date_pair_in_section(auto_section)
    umbrella_dates = find_date_pair_in_section(
        umbrella_section
    )
    wc_dates = find_date_pair_in_section(
        workers_comp_section
    )

    return {
        "general_liability": {
            "policy_number":
                find_policy_number_in_section(gl_section),
            "effective_date":
                gl_dates["effective_date"],
            "expiration_date":
                gl_dates["expiration_date"],
            "each_occurrence":
                find_first_money_after_label(
                    gl_section,
                    "EACH OCCURRENCE",
                    search_distance=8
                ),
            "general_aggregate":
                find_first_money_after_label(
                    gl_section,
                    "GENERAL AGGREGATE",
                    search_distance=4
                ),
            "products_completed_operations":
                find_first_money_after_label(
                    gl_section,
                    "PRODUCTS - COMP/OP AGG",
                    search_distance=4
                ),
        },

        "automobile_liability": {
            "policy_number":
                find_policy_number_in_section(auto_section),
            "effective_date":
                auto_dates["effective_date"],
            "expiration_date":
                auto_dates["expiration_date"],
            "combined_single_limit":
                find_first_money_after_label(
                    auto_section,
                    "(EA ACCIDENT)",
                    search_distance=5
                ),
        },

        "umbrella_excess": {
            "policy_number":
                find_policy_number_in_section(
                    umbrella_section
                ),
            "effective_date":
                umbrella_dates["effective_date"],
            "expiration_date":
                umbrella_dates["expiration_date"],
            "each_occurrence":
                find_first_money_after_label(
                    umbrella_section,
                    "EACH OCCURRENCE",
                    search_distance=4
                ),
            "aggregate":
                find_first_money_after_label(
                    umbrella_section,
                    "AGGREGATE",
                    search_distance=4
                ),
        },

        "workers_compensation": {
            "policy_number":
                find_policy_number_in_section(
                    workers_comp_section
                ),
            "effective_date":
                wc_dates["effective_date"],
            "expiration_date":
                wc_dates["expiration_date"],
            "each_accident":
                find_first_money_after_label(
                    workers_comp_section,
                    "E.L. EACH ACCIDENT",
                    search_distance=4
                ),
            "disease_each_employee":
                find_first_money_after_label(
                    workers_comp_section,
                    "E.L. DISEASE - EA EMPLOYEE",
                    search_distance=4
                ),
            "disease_policy_limit":
                find_first_money_after_label(
                    workers_comp_section,
                    "E.L. DISEASE - POLICY LIMIT",
                    search_distance=4
                ),
        },
    }


def merge_layout_coverages(text_coverages, layout_coverages):
    """
    Position-based values win; text-based values fill any gaps.
    Dates from the layout reader are normalized to ISO here.
    """

    merged = {}

    for name in set(text_coverages) | set(layout_coverages):
        coverage = dict(text_coverages.get(name) or {})

        for field, value in (layout_coverages.get(name) or {}).items():
            if field in ("effective_date", "expiration_date"):
                value = normalize_date(value)

            if value is not None or field not in coverage:
                coverage[field] = value

        merged[name] = coverage

    return merged


def build_acord25_schema(text, evidence_tags=None, page=None):
    layout = extract_acord_layout(page) if page is not None else None

    certificate_holder = (
        (layout or {}).get("certificate_holder")
        or extract_acord_certificate_holder(text)
    )
    description = extract_description_of_operations(text)

    coverages = extract_acord_coverages(text)
    insurers = extract_acord_insurers(text)
    insured_block = extract_acord_named_insured(text)
    insured_identity = split_name_and_address(insured_block)

    if layout:
        coverages = merge_layout_coverages(coverages, layout["coverages"])
        insurers = {**insurers, **layout["insurers"]}

    return {
        "document_type": "ACORD_25",
        # Tags found on the certificate are statements, not proof.
        # They are kept for the report but the rules ignore them.
        "evidence_tags": evidence_tags or [],
        "certificate_date":
            extract_certificate_date(text),
        "named_insured":
            insured_identity["name"],
        "insured_address":
            insured_identity["address"],
        "named_insured_raw":
            insured_identity["raw_value"],
        "producer":
            extract_acord_producer(text),
        "insurers": insurers,
        "certificate_holder": certificate_holder,
        "certificate_holder_check":
            check_ro_certificate_holder(certificate_holder),
        "description_of_operations": description,
        "project_wording":
            find_named_projects(description),
        "form_numbers_mentioned":
            extract_form_numbers(description or ""),
        "coverages": coverages,
        "extraction_method": "layout" if layout else "text",
        "detailed_extraction_supported": True,
        "extraction_status": "EXTRACTED",
    }


# =========================================================
# ACORD 101 PLACEHOLDER
# =========================================================

def build_acord101_schema(
    text,
    evidence_tags=None
):
    """
    Preserves an ACORD 101 Additional Remarks Schedule.

    ACORD 101 text is supplemental certificate information.
    It can be associated with a parent ACORD 25, but it is not
    automatically treated as policy endorsement proof.
    """

    raw_text = clean_value(text)

    return {
        "document_type": "ACORD_101",
        "evidence_tags": evidence_tags or [],
        "evidence_status": "SUPPORTING_STATEMENT_ONLY",
        "additional_remarks": raw_text,
        "raw_text": raw_text,
        "text_preview": (
            raw_text[:500]
            if raw_text
            else None
        ),
        "requires_parent_certificate": True,
        "detailed_extraction_supported": True,
        "extraction_status": "EXTRACTED",
    }


# =========================================================
# GENERIC / UNSUPPORTED DOCUMENT SCHEMA
# =========================================================

def build_generic_schema(document_type, text, evidence_tags=None):
    """
    Preserves a classified page when detailed extraction for that document
    type has not yet been implemented. Evidence tags remain unverified and
    are not standalone compliance proof.
    """
    raw_text = clean_value(text)
    review_types = {
        "CERTIFICATE_EQUIVALENT",
        "POLICY_EXTRACT",
        "UNKNOWN",
        "ENDORSEMENT_CONTINUATION",
    }

    return {
        "document_type": document_type,
        "extraction_status": "BASIC_SCHEMA_ONLY",
        "detailed_extraction_supported": False,
        "evidence_tags": evidence_tags or [],
        "evidence_status": "UNVERIFIED",
        "requires_review": document_type in review_types,
        "raw_text": raw_text,
        "text_preview": raw_text[:500] if raw_text else None,
    }


# =========================================================
# ROUTER
# =========================================================

def build_document_schema(
    document_type,
    text,
    evidence_tags=None,
    page=None
):
    """
    Routes a classified page to detailed extraction where supported.
    Unsupported types receive a safe basic schema rather than being forced
    through the wrong extractor.
    """
    if document_type == "ACORD_25":
        return build_acord25_schema(
            text,
            evidence_tags,
            page=page
        )

    if document_type == "ACORD_101":
        return build_acord101_schema(
            text,
            evidence_tags
        )

    if document_type == "ENDORSEMENT":
        return build_endorsement_schema(
            text,
            evidence_tags
        )

    return build_generic_schema(
        document_type or "UNKNOWN",
        text,
        evidence_tags
    )
