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


def classify_page(page_text):
    text = page_text.upper().strip()

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    # Document-title detection should focus on the top of the page.
    header = "\n".join(lines[:20])

    page_type = "OTHER"
    evidence_tags = []

    # The ACORD logo/footer is often far down in the reading order, so
    # "ACORD" is looked for on the whole page. Checking the certificate
    # title first also stops the certificate's own notice ("...in lieu of
    # such endorsement(s)") from making it look like an endorsement.
    if (
        "CERTIFICATE OF LIABILITY INSURANCE" in header
        and "ACORD" in text
    ):
        page_type = "ACORD_25"

    elif (
        "ADDITIONAL REMARKS SCHEDULE" in header
        and "ACORD 101" in header
    ):
        page_type = "ACORD_101"

    elif (
        "THIS ENDORSEMENT CHANGES THE POLICY" in header
        or "ENDORSEMENT" in header
    ):
        page_type = "ENDORSEMENT"

    if "ADDITIONAL INSURED" in text:
        evidence_tags.append("ADDITIONAL_INSURED")

    if (
        "WAIVER OF SUBROGATION" in text
        or "WAIVER OF TRANSFER OF RIGHTS OF RECOVERY" in text
        or "WE WAIVE ANY RIGHT OF RECOVERY" in text
    ):
        evidence_tags.append("WAIVER_OF_SUBROGATION")

    if (
        "PRIMARY AND NON-CONTRIBUTORY" in text
        or "PRIMARY AND NONCONTRIBUTORY" in text
        or "PRIMARY AND NOT CONTRIBUTING" in text
    ):
        evidence_tags.append("PRIMARY_NONCONTRIBUTORY")

    if (
        "PRODUCTS-COMPLETED OPERATIONS" in text
        or "COMPLETED OPERATIONS" in text
    ):
        evidence_tags.append("COMPLETED_OPERATIONS")

    return {
        "page_type": page_type,
        "evidence_tags": sorted(set(evidence_tags))
    }