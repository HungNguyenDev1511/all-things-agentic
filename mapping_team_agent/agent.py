import csv
from pathlib import Path

from google.adk import Agent


MODEL = "gemini-3.5-flash"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ORG_MASTER_PATH = PROJECT_ROOT / "sample_data" / "organization_master.csv"


# ============================================================
# TOOL
# ============================================================

def get_organization_master() -> dict:
    """
    Return the authoritative organization master data.

    The organization mapping agent must use this tool before
    deciding an ORG_ID.
    """

    organizations = []

    with open(
        ORG_MASTER_PATH,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:
        reader = csv.DictReader(f)

        for row in reader:
            organizations.append({
                "org_id": int(row["ORG_ID"]),
                "code": row["CODE"],
                "name": row["NAME"],
            })

    return {
        "organizations": organizations
    }


# ============================================================
# SPECIALIST 1 — ORGANIZATION MAPPING
# ============================================================

organization_mapping_agent = Agent(
    name="organization_mapping_agent",
    model=MODEL,
    mode="single_turn",

    description=(
        "Specialist for mapping legacy department or organization "
        "names/codes to the authoritative organization master."
    ),

    instruction="""
You are the Organization Mapping Specialist for SchemaPilot.

Your task is ONLY to map legacy organization/department values.

Rules:

1. ALWAYS call get_organization_master before making a mapping.
2. The organization master is authoritative.
3. You may reason about:
   - abbreviations
   - synonyms
   - translations
   - legacy naming variations
4. Never invent an ORG_ID that does not exist in the master.
5. If you are not sufficiently confident, return org_id = null.
6. Do not ask the user questions directly.
7. Return a concise structured result.

For every input value report:

- source_value
- org_id
- code
- matched_name
- confidence (0.0 to 1.0)
- status: AUTO or HUMAN_REVIEW
- short_reason

Example:

{
  "source_value": "Finance Dept",
  "org_id": 120,
  "code": "FIN",
  "matched_name": "Finance",
  "confidence": 0.96,
  "status": "AUTO",
  "short_reason": "Semantic equivalent of Finance"
}
""",

    tools=[
        get_organization_master
    ],
)


# ============================================================
# SPECIALIST 2 — DATE INTERPRETATION
# ============================================================

date_interpretation_agent = Agent(
    name="date_interpretation_agent",
    model=MODEL,
    mode="single_turn",

    description=(
        "Specialist for interpreting legacy date values and "
        "identifying ambiguous or invalid date formats."
    ),

    instruction="""
You are the Date Interpretation Specialist for SchemaPilot.

Your task is ONLY to assess legacy date values.

Rules:

1. ISO YYYY-MM-DD is unambiguous if it is a valid date.
2. For slash dates such as 03/04/1994:
   - if both the first and second number are <= 12,
     NEVER guess whether it means DD/MM/YYYY or MM/DD/YYYY.
   - mark it as HUMAN_REVIEW.
3. Invalid calendar dates must be marked INVALID.
4. Do not ask the user questions directly.
5. Do not silently fix invalid dates.

For every value report:

- source_value
- status: AUTO, HUMAN_REVIEW, or INVALID
- normalized_value if safe
- possible_interpretations if ambiguous
- confidence
- short_reason
""",
)


# ============================================================
# COORDINATOR
# ============================================================

root_agent = Agent(
    name="mapping_coordinator",
    model=MODEL,

    description=(
        "Coordinates specialist agents for semantic data migration analysis."
    ),

    instruction="""
You are the SchemaPilot Mapping Coordinator.

You coordinate specialist agents. You are NOT supposed to perform
specialist work yourself when an appropriate specialist exists.

Available specialists:

1. organization_mapping_agent
   - department names
   - organization names
   - organization codes
   - semantic organization mapping

2. date_interpretation_agent
   - date formats
   - ambiguous dates
   - invalid dates

For each user request:

1. Identify which specialist(s) are needed.
2. Delegate only the relevant work.
3. Do not call an unrelated specialist.
4. Multiple independent specialists may be used when needed.
5. After specialist results return, synthesize one concise report.

Final report should contain:

{
  "organization_mappings": [...],
  "date_assessments": [...],
  "human_approval_required": true/false,
  "summary": "..."
}

Do not invent organization IDs.
Do not resolve ambiguous dates without human approval.
""",

    sub_agents=[
        organization_mapping_agent,
        date_interpretation_agent,
    ],
)