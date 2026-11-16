from pathlib import Path
import py_compile

path = Path('extractors/extraction_mapper.py')
if not path.exists():
    raise SystemExit(f'Could not find {path.resolve()}')

text = path.read_text(encoding='utf-8')
backup = path.with_suffix('.py.before_wording_only.bak')
backup.write_text(text, encoding='utf-8')

marker = '# ---------------------------------------------------------\n# Endorsement form numbers\n# ---------------------------------------------------------'
if marker not in text:
    raise SystemExit('Could not find endorsement form-number section')

helpers = r'''# ---------------------------------------------------------
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
    if "SOLE NEGLIGENCE" in upper:
        restrictions.append("Sole-negligence limitation requires review")
    if "VICARIOUS LIABILITY" in upper:
        restrictions.append("Vicarious-liability limitation requires review")
    if "DOES NOT APPLY" in upper and "PROFESSIONAL" in upper:
        restrictions.append("Professional-services exclusion is present")
    if "VOID" in upper and "ADDITIONAL INSURED" in upper:
        restrictions.append("Additional-insured coverage may be voided by another form")

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


'''

if 'def extract_endorsement_wording(' not in text:
    text = text.replace(marker, helpers + marker)

start = text.index('def build_endorsement_schema(')
end = text.index('\n\n# =========================================================\n# ACORD 25 EXTRACTION', start)

new_builder = r'''def build_endorsement_schema(text, evidence_tags=None):
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
'''

text = text[:start] + new_builder + text[end:]

# Remove the obsolete duplicate certificate-holder definitions if present.
old_holder = '''RO_HOLDER_STREET = re.compile(r"\\b933\\s+WALL\\s+(AVENUE|AVE)\\b")
RO_HOLDER_CITY = re.compile(r"\\bOGDEN\\b[\\s,]*\\b(UT|UTAH)\\b[\\s,]*84404\\b")
RO_HOLDER_NAME = re.compile(r"\\bR\\s*&\\s*O\\s+CONSTRUCTION\\b")


'''
text = text.replace(old_holder, '')

path.write_text(text, encoding='utf-8')
py_compile.compile(str(path), doraise=True)
print(f'Updated: {path}')
print(f'Backup:  {backup}')
print('Syntax check: PASS')
