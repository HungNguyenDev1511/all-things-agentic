import csv
from pathlib import Path

from google.adk import Agent


MODEL = "gemini-3.5-flash"

AGENT_ROOT = Path(__file__).resolve().parent
ORG_MASTER_PATH = AGENT_ROOT / "sample_data" / "organization_master.csv"


def get_organization_master() -> dict:
    """Return the authoritative organization master data."""
    organizations = []

    with open(
        ORG_MASTER_PATH,
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        for row in reader:
            organizations.append(
                {
                    "org_id": int(row["ORG_ID"]),
                    "code": row["CODE"],
                    "name": row["NAME"],
                }
            )

    return {"organizations": organizations}


organization_mapping_agent = Agent(
    name="organization_mapping_agent",
    model=MODEL,
    mode="single_turn",
    description=(
        "Maps legacy department and organization values to the authoritative "
        "organization master."
    ),
    instruction="""
You are the Organization Mapping Specialist for SchemaPilot.

The input is a JSON payload that may contain:
- departments
- dates
- target_schema
- profile_summary

Process ONLY the `departments` values. Ignore dates.

Rules:
1. ALWAYS call get_organization_master before deciding an ORG_ID.
2. The organization master is authoritative.
3. You may reason about abbreviations, synonyms, translations, and legacy names.
4. Never invent an ORG_ID that does not exist in the organization master.
5. If a value cannot be mapped safely, set org_id to null and status to HUMAN_REVIEW.
6. Never ask the user questions.
7. Return ONLY valid JSON. Do not use Markdown fences.

Required output shape:
{
  "organization_mappings": [
    {
      "source_value": "Finance Dept",
      "org_id": 120,
      "code": "FIN",
      "matched_name": "Finance",
      "confidence": 0.96,
      "status": "AUTO",
      "short_reason": "Semantic equivalent of Finance"
    }
  ]
}
""",
    tools=[get_organization_master],
)


date_interpretation_agent = Agent(
    name="date_interpretation_agent",
    model=MODEL,
    mode="single_turn",
    description=(
        "Interprets legacy date values and identifies ambiguous or invalid dates."
    ),
    instruction="""
You are the Date Interpretation Specialist for SchemaPilot.

The input is a JSON payload that may contain:
- departments
- dates
- target_schema
- profile_summary

Process ONLY the `dates` values. Ignore departments.

Rules:
1. Valid ISO YYYY-MM-DD is AUTO and can be normalized safely.
2. For slash dates such as 03/04/1994:
   - if both the first and second components are <= 12,
     NEVER guess DD/MM/YYYY versus MM/DD/YYYY.
   - mark the value HUMAN_REVIEW.
3. Invalid calendar dates must be marked INVALID.
4. Never silently repair an invalid date.
5. Never ask the user questions.
6. Return ONLY valid JSON. Do not use Markdown fences.

Required output shape:
{
  "date_assessments": [
    {
      "source_value": "1992-05-12",
      "status": "AUTO",
      "normalized_value": "1992-05-12",
      "possible_interpretations": [],
      "confidence": 1.0,
      "short_reason": "Valid ISO date"
    }
  ]
}
""",
)


# Keep this coordinator for the standalone collaborative-agent demo/tests.
# The production SchemaPilot graph below deliberately uses the two specialists
# as explicit graph nodes so their execution is visible and deterministic.
mapping_coordinator = Agent(
    name="mapping_coordinator",
    model=MODEL,
    description=(
        "Coordinates semantic migration specialists and delegates only relevant work."
    ),
    instruction="""
You are the SchemaPilot Mapping Coordinator.

Delegate organization values to organization_mapping_agent.
Delegate date values to date_interpretation_agent.
Do not perform specialist mapping yourself.

Use only the specialists needed by the request. After they return, synthesize
their outputs into one concise result.
""",
    sub_agents=[
        organization_mapping_agent,
        date_interpretation_agent,
    ],
)