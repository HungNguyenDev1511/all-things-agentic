import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone

from google.adk import Event, Workflow
from google.adk.events import RequestInput
from google.adk.workflow import JoinNode

from .mapping_team import (
    organization_mapping_agent,
    date_interpretation_agent,
)


EXPECTED_COLUMNS = {
    "EMP_CODE",
    "FULL_NAME",
    "DOB",
    "DEPARTMENT",
}


def _normalize_header(value: str) -> str:
    """Normalize CSV headers without changing business values."""
    return str(value or "").replace("\ufeff", "").strip()


def open_csv_auto(file_path: str):
    """
    Open legacy CSV/TSV files robustly.

    Handles:
    - UTF-8 / UTF-8 BOM / cp1258
    - comma / tab / semicolon / pipe
    - malformed exports where the ENTIRE row is wrapped in quotes, for example:
      "EMP_CODE<TAB>FULL_NAME<TAB>DOB<TAB>DEPARTMENT"
    """
    encodings = [
        "utf-8-sig",
        "utf-8",
        "cp1258",
    ]
    delimiters = [
        ",",
        "\t",
        ";",
        "|",
    ]

    last_error = None

    for encoding in encodings:
        try:
            with open(
                file_path,
                "r",
                encoding=encoding,
                newline="",
            ) as f:
                content = f.read()

            if not content.strip():
                raise ValueError(
                    f"Empty source file: {file_path}"
                )

            lines = content.splitlines()
            non_empty_lines = [
                line
                for line in lines
                if line.strip()
            ]

            if not non_empty_lines:
                raise ValueError(
                    f"Empty source file: {file_path}"
                )

            first_line = non_empty_lines[0]

            whole_row_quoted = (
                first_line.startswith('"')
                and first_line.endswith('"')
                and any(
                    delimiter in first_line
                    for delimiter in delimiters
                )
            )

            if whole_row_quoted:
                cleaned_lines = []

                for line in lines:
                    stripped = line.strip()

                    if (
                        stripped.startswith('"')
                        and stripped.endswith('"')
                    ):
                        stripped = stripped[1:-1]

                    stripped = stripped.replace(
                        '""',
                        '"',
                    )

                    cleaned_lines.append(stripped)

                content = "\n".join(cleaned_lines)

            header_line = next(
                line
                for line in content.splitlines()
                if line.strip()
            )

            best_delimiter = None
            best_headers = []
            best_score = -1

            for delimiter in delimiters:
                parsed_header = next(
                    csv.reader(
                        [header_line],
                        delimiter=delimiter,
                    )
                )

                headers = [
                    _normalize_header(h)
                    for h in parsed_header
                ]

                score = len(
                    EXPECTED_COLUMNS.intersection(
                        headers
                    )
                )

                if score > best_score:
                    best_score = score
                    best_delimiter = delimiter
                    best_headers = headers

            if best_score <= 0:
                dialect = csv.Sniffer().sniff(
                    content[:4096],
                    delimiters=",;\t|",
                )
                best_delimiter = dialect.delimiter

            stream = io.StringIO(content)

            reader = csv.DictReader(
                stream,
                delimiter=best_delimiter,
            )

            reader.fieldnames = [
                _normalize_header(h)
                for h in (
                    reader.fieldnames or []
                )
            ]

            return (
                stream,
                reader,
                {
                    "encoding": encoding,
                    "delimiter": best_delimiter,
                    "columns": list(
                        reader.fieldnames or []
                    ),
                    "whole_row_quoted":
                        whole_row_quoted,
                },
            )

        except (
            UnicodeDecodeError,
            csv.Error,
            ValueError,
        ) as exc:
            last_error = exc
            continue

    raise ValueError(
        "Cannot detect CSV encoding/delimiter: "
        f"{file_path}. "
        f"Last error: {last_error}"
    )


# ============================================================
# IDEMPOTENCY / MIGRATION LEDGER
# ============================================================

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(__file__)
)

RUNTIME_DIR = os.path.join(
    os.path.dirname(__file__),
    ".adk",
)

MIGRATION_LEDGER_PATH = os.path.join(
    RUNTIME_DIR,
    "migration_ledger.db",
)


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _file_sha256(file_path: str) -> str:
    digest = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _build_migration_job_id(
    source_file_path: str,
    date_policy: str,
):
    """
    A migration job is uniquely determined by:
    - source file bytes
    - target schema bytes
    - organization master bytes
    - human-approved date policy
    - workflow version

    Re-running the exact same migration produces the same job id.
    """
    target_schema_path = os.path.join(
        PROJECT_ROOT,
        "sample_data",
        "target_schema.json",
    )

    org_master_path = os.path.join(
        PROJECT_ROOT,
        "sample_data",
        "organization_master.csv",
    )

    fingerprints = {
        "source_sha256":
            _file_sha256(source_file_path),
        "target_schema_sha256":
            _file_sha256(target_schema_path),
        "organization_master_sha256":
            _file_sha256(org_master_path),
        "date_policy":
            date_policy,
        "workflow_version":
            "schemapilot-v2-idempotent",
    }

    canonical = json.dumps(
        fingerprints,
        sort_keys=True,
        ensure_ascii=False,
    )

    job_id = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:24]

    return job_id, fingerprints


def _ensure_ledger_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            date_policy TEXT NOT NULL,
            migrated_output_path TEXT,
            rejected_output_path TEXT,
            report_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_rows (
            idempotency_key TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            emp_code TEXT,
            result_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(job_id)
                REFERENCES migration_jobs(job_id)
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_migration_rows_job_id
        ON migration_rows(job_id)
        """
    )


def _open_ledger():
    os.makedirs(
        RUNTIME_DIR,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        MIGRATION_LEDGER_PATH,
        timeout=30,
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )
    conn.execute(
        "PRAGMA foreign_keys=ON"
    )

    _ensure_ledger_schema(conn)

    return conn


def _make_row_idempotency_key(
    job_id: str,
    source_row: int,
    emp_code: str,
    result_type: str,
) -> str:
    raw = (
        f"{job_id}|"
        f"{source_row}|"
        f"{emp_code}|"
        f"{result_type}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()



def normalize_input(node_input: str):
    """Validate the input path and persist it for downstream nodes."""
    file_path = str(node_input).strip().strip('"').strip("'")
    file_path = os.path.abspath(file_path)

    if not os.path.exists(file_path):
        return Event(
            output={
                "error": f"File not found: {file_path}",
            },
            message=f"File not found: {file_path}",
        )

    return Event(
        output=file_path,
        state={
            "source_file_path": file_path,
        },
    )


def scan_schema(node_input: str):
    """Inspect input columns."""
    file_path = node_input

    f, reader, file_info = open_csv_auto(file_path)

    with f:
        columns = set(reader.fieldnames or [])

    return Event(
        output={
            "columns": sorted(columns),
            "missing_columns": sorted(EXPECTED_COLUMNS - columns),
            "schema_valid": EXPECTED_COLUMNS.issubset(columns),
            "file_info": file_info,
        }
    )


def scan_duplicates(node_input: str):
    """Detect duplicate employee codes."""
    file_path = node_input

    seen = {}
    duplicates = []

    f, reader, file_info = open_csv_auto(file_path)

    with f:
        for row_number, row in enumerate(reader, start=2):
            code = (row.get("EMP_CODE") or "").strip()

            if not code:
                continue

            if code in seen:
                duplicates.append(
                    {
                        "employee_code": code,
                        "first_row": seen[code],
                        "duplicate_row": row_number,
                    }
                )
            else:
                seen[code] = row_number

    return Event(
        output={
            "duplicate_count": len(duplicates),
            "duplicates": duplicates,
            "file_info": file_info,
        }
    )


def scan_dates(node_input: str):
    """Detect invalid and ambiguous DOB values."""
    file_path = node_input

    invalid = []
    ambiguous = []

    f, reader, file_info = open_csv_auto(file_path)

    with f:
        for row_number, row in enumerate(reader, start=2):
            value = (row.get("DOB") or "").strip()

            if not value:
                continue

            # ISO YYYY-MM-DD
            try:
                datetime.strptime(value, "%Y-%m-%d")
                continue
            except ValueError:
                pass

            # DD/MM/YYYY or MM/DD/YYYY
            match = re.fullmatch(
                r"(\d{1,2})/(\d{1,2})/(\d{4})",
                value,
            )

            if match:
                first = int(match.group(1))
                second = int(match.group(2))

                if first <= 12 and second <= 12:
                    ambiguous.append(
                        {
                            "row": row_number,
                            "value": value,
                            "reason": "Could be DD/MM/YYYY or MM/DD/YYYY",
                        }
                    )
                    continue

                try:
                    datetime.strptime(value, "%d/%m/%Y")
                    continue
                except ValueError:
                    pass

            invalid.append(
                {
                    "row": row_number,
                    "value": value,
                }
            )

    return Event(
        output={
            "invalid_count": len(invalid),
            "ambiguous_count": len(ambiguous),
            "invalid_dates": invalid,
            "ambiguous_dates": ambiguous,
            "file_info": file_info,
        }
    )


join_scanners = JoinNode(name="join_scanners")
join_mapping_specialists = JoinNode(name="join_mapping_specialists")


def prepare_mapping_input(
    node_input: dict,
    source_file_path: str,
):
    """
    Convert deterministic profiling results + source values into one compact
    JSON payload consumed by both specialist agents.
    """
    profile_results = node_input

    project_root = os.path.dirname(
        os.path.dirname(__file__)
    )

    target_schema_path = os.path.join(
        project_root,
        "sample_data",
        "target_schema.json",
    )

    with open(
        target_schema_path,
        "r",
        encoding="utf-8",
    ) as f:
        target_schema = json.load(f)

    f, reader, file_info = open_csv_auto(source_file_path)

    departments = []
    dates = []

    with f:
        for row in reader:
            department = (row.get("DEPARTMENT") or "").strip()
            dob = (row.get("DOB") or "").strip()

            if department and department not in departments:
                departments.append(department)

            if dob and dob not in dates:
                dates.append(dob)

    payload = {
        "departments": departments,
        "dates": dates,
        "target_schema": target_schema,
        "profile_summary": {
            "schema": profile_results.get("scan_schema", {}),
            "duplicates": profile_results.get("scan_duplicates", {}),
            "dates": profile_results.get("scan_dates", {}),
        },
        "file_info": file_info,
    }

    return Event(
        output=json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        state={
            "profile_results": profile_results,
        },
    )


def _parse_json_text(value):
    """Parse an agent result defensively."""
    if isinstance(value, dict):
        return value

    raw = str(value or "").strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "parse_error": True,
            "raw_output": raw,
        }


def combine_mapping_results(node_input: dict):
    """
    JoinNode emits a mapping keyed by predecessor node name. Parse the two
    specialist outputs and create one deterministic combined result.
    """
    org_raw = node_input.get("organization_mapping_agent", {})
    date_raw = node_input.get("date_interpretation_agent", {})

    org_result = _parse_json_text(org_raw)
    date_result = _parse_json_text(date_raw)

    organization_mappings = org_result.get(
        "organization_mappings",
        [],
    )

    date_assessments = date_result.get(
        "date_assessments",
        [],
    )

    human_required = bool(
        org_result.get("parse_error")
        or date_result.get("parse_error")
    )

    for mapping in organization_mappings:
        if mapping.get("status") != "AUTO":
            human_required = True
            break

    for assessment in date_assessments:
        if assessment.get("status") in {
            "HUMAN_REVIEW",
            "INVALID",
        }:
            human_required = True
            break

    combined = {
        "organization_mappings": organization_mappings,
        "date_assessments": date_assessments,
        "human_approval_required": human_required,
        "specialist_parse_errors": {
            "organization_mapping_agent": bool(
                org_result.get("parse_error")
            ),
            "date_interpretation_agent": bool(
                date_result.get("parse_error")
            ),
        },
    }

    return Event(output=combined)


def calculate_final_risk(
    node_input: dict,
    profile_results: dict,
):
    """Combine deterministic data-quality risk with semantic-agent confidence."""
    mapping_result = node_input

    schema = profile_results.get("scan_schema", {})
    duplicates = profile_results.get("scan_duplicates", {})
    dates = profile_results.get("scan_dates", {})

    confidence = 1.0

    # Deterministic profiling risk.
    missing_columns = len(schema.get("missing_columns", []))
    confidence -= missing_columns * 0.30

    duplicate_count = duplicates.get("duplicate_count", 0)
    confidence -= duplicate_count * 0.15

    invalid_count = dates.get("invalid_count", 0)
    confidence -= invalid_count * 0.25

    ambiguous_count = dates.get("ambiguous_count", 0)
    confidence -= ambiguous_count * 0.10

    # Semantic specialist confidence can only lower the overall score.
    specialist_confidences = []

    for mapping in mapping_result.get(
        "organization_mappings",
        [],
    ):
        try:
            specialist_confidences.append(
                float(mapping.get("confidence", 1.0))
            )
        except (TypeError, ValueError):
            pass

    for assessment in mapping_result.get(
        "date_assessments",
        [],
    ):
        try:
            specialist_confidences.append(
                float(assessment.get("confidence", 1.0))
            )
        except (TypeError, ValueError):
            pass

    if specialist_confidences:
        confidence = min(
            confidence,
            min(specialist_confidences),
        )

    confidence = max(
        0.0,
        min(1.0, confidence),
    )

    result = {
        "confidence": round(confidence, 2),
        "human_approval_required": bool(
            mapping_result.get(
                "human_approval_required",
                False,
            )
        ),
        "profile": {
            "schema": schema,
            "duplicates": duplicates,
            "dates": dates,
        },
        "mapping": mapping_result,
    }

    return Event(output=result)


def risk_router(node_input: dict):
    """
    Deterministic policy gate.

    REVIEW and HUMAN both require an approval checkpoint in this MVP, so they
    share one graph route (NEEDS_HUMAN). We preserve the severity separately
    in `risk_level` to avoid duplicate graph edges.
    """
    confidence = node_input["confidence"]

    if node_input.get(
        "human_approval_required",
        False,
    ):
        return Event(
            output={
                **node_input,
                "risk_level": "HUMAN",
            },
            route="NEEDS_HUMAN",
        )

    if confidence >= 0.90:
        return Event(
            output={
                **node_input,
                "risk_level": "AUTO",
            },
            route="AUTO",
        )

    if confidence >= 0.60:
        return Event(
            output={
                **node_input,
                "risk_level": "REVIEW",
            },
            route="NEEDS_HUMAN",
        )

    return Event(
        output={
            **node_input,
            "risk_level": "HUMAN",
        },
        route="NEEDS_HUMAN",
    )


def auto_route(node_input: dict):
    """Safe path: continue directly to deterministic transformation."""
    result = {
        "status": "AUTO_MIGRATION_ALLOWED",
        "date_policy": "AUTO_SAFE_ONLY",
        "invalid_row_policy": "REJECT_INVALID_ROWS",
        "ready_for_transformation": True,
        "migration_context": node_input,
    }

    return Event(output=result)


def prepare_human_review(node_input: dict):
    """
    Persist the risky migration context before pausing for a human decision.
    REVIEW and HUMAN routes both arrive here in the MVP.
    """
    pending_review = {
        "status": "HUMAN_APPROVAL_REQUIRED",
        **node_input,
    }

    return Event(
        output=pending_review,
        state={
            "pending_human_review": pending_review,
        },
    )


def request_human_decision(node_input: dict):
    """
    Pause the graph and ask the user how ambiguous slash dates should be handled.

    For this MVP checkpoint we intentionally use a small, explicit decision
    vocabulary. Later the frontend will render these as buttons.
    """
    profile = node_input.get("profile", {})
    mapping = node_input.get("mapping", {})

    ambiguous_dates = (
        profile.get("dates", {}).get("ambiguous_dates", [])
    )
    invalid_dates = (
        profile.get("dates", {}).get("invalid_dates", [])
    )

    org_reviews = [
        item
        for item in mapping.get("organization_mappings", [])
        if item.get("status") != "AUTO"
    ]

    message = (
        "SchemaPilot paused because human approval is required.\n\n"
        f"Confidence: {node_input.get('confidence')}\n"
        f"Ambiguous dates: {len(ambiguous_dates)}\n"
        f"Invalid dates: {len(invalid_dates)}\n"
        f"Organization mappings needing review: {len(org_reviews)}\n\n"
        "Choose exactly ONE response:\n"
        "APPROVE_DMY  -> interpret ambiguous slash dates as DD/MM/YYYY; "
        "invalid rows remain rejected.\n"
        "APPROVE_MDY  -> interpret ambiguous slash dates as MM/DD/YYYY; "
        "invalid rows remain rejected.\n"
        "REJECT       -> cancel this migration.\n"
    )

    yield RequestInput(
        message=message,
        payload=node_input,
        response_schema=str,
    )


def apply_human_decision(
    node_input: str,
    pending_human_review: dict,
):
    """
    Continue after RequestInput. The human reply is the node_input on resume;
    the pre-pause migration context is recovered from session state.
    """
    if isinstance(node_input, dict):
        raw_decision = (
            node_input.get("user_response")
            or node_input.get("response")
            or node_input.get("text")
            or ""
        )
    else:
        raw_decision = str(node_input or "")

    decision = raw_decision.strip().upper()

    if decision == "REJECT":
        return Event(
            output={
                "status": "MIGRATION_CANCELLED_BY_HUMAN",
                "human_decision": decision,
                "ready_for_transformation": False,
                "migration_context": pending_human_review,
            }
        )

    if decision not in {
        "APPROVE_DMY",
        "APPROVE_MDY",
    }:
        return Event(
            output={
                "status": "INVALID_HUMAN_DECISION",
                "human_decision": raw_decision,
                "expected_values": [
                    "APPROVE_DMY",
                    "APPROVE_MDY",
                    "REJECT",
                ],
                "ready_for_transformation": False,
                "migration_context": pending_human_review,
            }
        )

    date_policy = (
        "DD/MM/YYYY"
        if decision == "APPROVE_DMY"
        else "MM/DD/YYYY"
    )

    return Event(
        output={
            "status": "HUMAN_APPROVED",
            "human_decision": decision,
            "date_policy": date_policy,
            "invalid_row_policy": "REJECT_INVALID_ROWS",
            "ready_for_transformation": True,
            "migration_context": pending_human_review,
        },
        state={
            "approved_date_policy": date_policy,
            "human_decision": decision,
        },
    )


def post_human_router(node_input: dict):
    """Continue only after a valid human approval."""
    if node_input.get("ready_for_transformation"):
        return Event(output=node_input, route="CONTINUE")

    return Event(output=node_input, route="STOP")


def cancelled_route(node_input: dict):
    """Final response for rejected/invalid human decisions."""
    return Event(
        output=node_input,
        message=json.dumps(
            node_input,
            ensure_ascii=False,
            indent=2,
        ),
    )


def _normalize_date_for_migration(
    value: str,
    date_policy: str,
    date_assessment_lookup: dict,
):
    """
    Normalize one legacy DOB. Returns (normalized_iso, error_reason).
    AI assessments are advisory; final parsing is deterministic Python code.
    """
    value = (value or "").strip()

    if not value:
        return None, "MISSING_DOB"

    # Prefer a safe normalized value already identified by the date specialist.
    assessment = date_assessment_lookup.get(value, {})
    if assessment.get("status") == "INVALID":
        return None, "INVALID_DOB"

    normalized = assessment.get("normalized_value")
    if assessment.get("status") == "AUTO" and normalized:
        try:
            parsed = datetime.strptime(normalized, "%Y-%m-%d")
            return parsed.strftime("%Y-%m-%d"), None
        except (TypeError, ValueError):
            pass

    # ISO source is always deterministic.
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
        return parsed.strftime("%Y-%m-%d"), None
    except ValueError:
        pass

    match = re.fullmatch(
        r"(\d{1,2})/(\d{1,2})/(\d{4})",
        value,
    )

    if not match:
        return None, "INVALID_DOB"

    first = int(match.group(1))
    second = int(match.group(2))

    formats_to_try = []

    if first <= 12 and second <= 12:
        if date_policy == "DD/MM/YYYY":
            formats_to_try = ["%d/%m/%Y"]
        elif date_policy == "MM/DD/YYYY":
            formats_to_try = ["%m/%d/%Y"]
        else:
            return None, "AMBIGUOUS_DOB_WITHOUT_POLICY"
    elif first > 12 and second <= 12:
        # First component cannot be a month -> DD/MM/YYYY.
        formats_to_try = ["%d/%m/%Y"]
    elif second > 12 and first <= 12:
        # Second component cannot be a month -> MM/DD/YYYY.
        formats_to_try = ["%m/%d/%Y"]
    else:
        return None, "INVALID_DOB"

    for fmt in formats_to_try:
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.strftime("%Y-%m-%d"), None
        except ValueError:
            continue

    return None, "INVALID_DOB"


def transform_records(
    node_input: dict,
    source_file_path: str,
):
    """
    Transform source rows deterministically and assign stable idempotency keys.

    The node itself has no external side effects. If ADK re-runs it during
    resume/retry, the same input produces the same job id and row keys.
    """
    context = node_input.get(
        "migration_context",
        {},
    )
    mapping = context.get(
        "mapping",
        {},
    )
    date_policy = node_input.get(
        "date_policy",
        "AUTO_SAFE_ONLY",
    )

    job_id, fingerprints = (
        _build_migration_job_id(
            source_file_path,
            date_policy,
        )
    )

    org_lookup = {}

    for item in mapping.get(
        "organization_mappings",
        [],
    ):
        source_value = str(
            item.get("source_value") or ""
        ).strip()

        if source_value:
            org_lookup[source_value] = item

    date_assessment_lookup = {}

    for item in mapping.get(
        "date_assessments",
        [],
    ):
        source_value = str(
            item.get("source_value") or ""
        ).strip()

        if source_value:
            date_assessment_lookup[
                source_value
            ] = item

    migrated_rows = []
    rejected_rows = []
    row_ledger_entries = []
    seen_employee_codes = set()

    f, reader, file_info = open_csv_auto(
        source_file_path
    )

    detected_columns = set(
        reader.fieldnames or []
    )

    missing_source_columns = sorted(
        EXPECTED_COLUMNS
        - detected_columns
    )

    if missing_source_columns:
        f.close()

        result = {
            "status":
                "SOURCE_SCHEMA_ERROR",
            "migration_job_id":
                job_id,
            "input_fingerprints":
                fingerprints,
            "date_policy":
                date_policy,
            "source_file":
                source_file_path,
            "source_file_info":
                file_info,
            "missing_source_columns":
                missing_source_columns,
            "source_row_count": 0,
            "migrated_row_count": 0,
            "rejected_row_count": 0,
            "migrated_rows": [],
            "rejected_rows": [],
            "row_ledger_entries": [],
            "fatal_error": (
                "Source file could not be "
                "parsed into the expected "
                "columns. Migration was "
                "stopped instead of silently "
                "rejecting every row."
            ),
        }

        return Event(
            output=result,
            message=json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
            ),
        )

    with f:
        for row_number, row in enumerate(
            reader,
            start=2,
        ):
            emp_code = (
                row.get("EMP_CODE") or ""
            ).strip()

            full_name = (
                row.get("FULL_NAME") or ""
            ).strip()

            dob = (
                row.get("DOB") or ""
            ).strip()

            department = (
                row.get("DEPARTMENT")
                or ""
            ).strip()

            reasons = []

            if not emp_code:
                reasons.append(
                    "MISSING_EMP_CODE"
                )
            elif (
                emp_code
                in seen_employee_codes
            ):
                reasons.append(
                    "DUPLICATE_EMP_CODE"
                )
            else:
                seen_employee_codes.add(
                    emp_code
                )

            if not full_name:
                reasons.append(
                    "MISSING_FULL_NAME"
                )

            org_mapping = org_lookup.get(
                department
            )
            org_id = None

            if not department:
                reasons.append(
                    "MISSING_DEPARTMENT"
                )
            elif not org_mapping:
                reasons.append(
                    "ORG_MAPPING_NOT_FOUND"
                )
            elif (
                org_mapping.get("status")
                != "AUTO"
            ):
                reasons.append(
                    "ORG_MAPPING_REQUIRES_REVIEW"
                )
            elif (
                org_mapping.get("org_id")
                is None
            ):
                reasons.append(
                    "ORG_ID_MISSING"
                )
            else:
                org_id = org_mapping.get(
                    "org_id"
                )

            (
                normalized_dob,
                dob_error,
            ) = _normalize_date_for_migration(
                dob,
                date_policy,
                date_assessment_lookup,
            )

            if dob_error:
                reasons.append(
                    dob_error
                )

            if reasons:
                rejected_row = {
                    "SOURCE_ROW":
                        row_number,
                    "EMP_CODE":
                        emp_code,
                    "FULL_NAME":
                        full_name,
                    "DOB":
                        dob,
                    "DEPARTMENT":
                        department,
                    "REJECTION_REASON":
                        "|".join(reasons),
                }

                rejected_rows.append(
                    rejected_row
                )

                key = (
                    _make_row_idempotency_key(
                        job_id,
                        row_number,
                        emp_code,
                        "REJECTED",
                    )
                )

                row_ledger_entries.append(
                    {
                        "idempotency_key":
                            key,
                        "source_row":
                            row_number,
                        "emp_code":
                            emp_code,
                        "result_type":
                            "REJECTED",
                        "payload":
                            rejected_row,
                    }
                )
                continue

            migrated_row = {
                "employee_code":
                    emp_code,
                "full_name":
                    full_name,
                "date_of_birth":
                    normalized_dob,
                "org_id":
                    int(org_id),
            }

            migrated_rows.append(
                migrated_row
            )

            key = _make_row_idempotency_key(
                job_id,
                row_number,
                emp_code,
                "MIGRATED",
            )

            row_ledger_entries.append(
                {
                    "idempotency_key":
                        key,
                    "source_row":
                        row_number,
                    "emp_code":
                        emp_code,
                    "result_type":
                        "MIGRATED",
                    "payload":
                        migrated_row,
                }
            )

    result = {
        "status":
            "TRANSFORMATION_COMPLETED",
        "migration_job_id":
            job_id,
        "input_fingerprints":
            fingerprints,
        "date_policy":
            date_policy,
        "source_file":
            source_file_path,
        "source_file_info":
            file_info,
        "source_row_count":
            len(migrated_rows)
            + len(rejected_rows),
        "migrated_row_count":
            len(migrated_rows),
        "rejected_row_count":
            len(rejected_rows),
        "migrated_rows":
            migrated_rows,
        "rejected_rows":
            rejected_rows,
        "row_ledger_entries":
            row_ledger_entries,
    }

    return Event(output=result)



def _atomic_write_csv(
    final_path: str,
    fieldnames: list,
    rows: list,
):
    """
    Write to a temporary file in the same directory, then atomically replace
    the destination. A crash never leaves a half-written final CSV.
    """
    output_dir = os.path.dirname(
        final_path
    )

    os.makedirs(
        output_dir,
        exist_ok=True,
    )

    fd, temp_path = tempfile.mkstemp(
        prefix=".schemapilot-",
        suffix=".tmp",
        dir=output_dir,
        text=True,
    )

    os.close(fd)

    try:
        with open(
            temp_path,
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )

            writer.writeheader()
            writer.writerows(rows)

        os.replace(
            temp_path,
            final_path,
        )

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def write_migration_output(
    node_input: dict
):
    """
    Idempotent external write.

    ADK resumability is at-least-once for tool/effect execution, so this node
    must tolerate being executed more than once. A stable migration_job_id and
    SQLite ledger prevent duplicate side effects.
    """
    if (
        node_input.get("status")
        == "SOURCE_SCHEMA_ERROR"
    ):
        return Event(output=node_input)

    job_id = node_input[
        "migration_job_id"
    ]

    fingerprints = node_input.get(
        "input_fingerprints",
        {},
    )

    output_dir = os.path.join(
        PROJECT_ROOT,
        "migration_output",
        job_id,
    )

    os.makedirs(
        output_dir,
        exist_ok=True,
    )

    migrated_path = os.path.join(
        output_dir,
        "migrated_employees.csv",
    )

    rejected_path = os.path.join(
        output_dir,
        "rejected_rows.csv",
    )

    conn = _open_ledger()

    try:
        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            """
            SELECT
                status,
                migrated_output_path,
                rejected_output_path
            FROM migration_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()

        if existing:
            (
                existing_status,
                existing_migrated_path,
                existing_rejected_path,
            ) = existing

            terminal = (
                existing_status
                not in {
                    "WRITING",
                    "OUTPUT_WRITTEN",
                }
            )

            outputs_exist = (
                existing_migrated_path
                and os.path.exists(
                    existing_migrated_path
                )
                and existing_rejected_path
                and os.path.exists(
                    existing_rejected_path
                )
            )

            if (
                terminal
                and outputs_exist
            ):
                conn.commit()

                return Event(
                    output={
                        **node_input,
                        "status":
                            "IDEMPOTENT_REPLAY_SKIPPED",
                        "idempotent_replay":
                            True,
                        "migrated_output_path":
                            existing_migrated_path,
                        "rejected_output_path":
                            existing_rejected_path,
                        "migration_ledger_path":
                            MIGRATION_LEDGER_PATH,
                    }
                )

        now = _utc_now()

        conn.execute(
            """
            INSERT INTO migration_jobs (
                job_id,
                status,
                source_file,
                source_sha256,
                date_policy,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                job_id,
                "WRITING",
                node_input.get(
                    "source_file",
                    "",
                ),
                fingerprints.get(
                    "source_sha256",
                    "",
                ),
                node_input.get(
                    "date_policy",
                    "",
                ),
                now,
                now,
            ),
        )

        for entry in node_input.get(
            "row_ledger_entries",
            [],
        ):
            conn.execute(
                """
                INSERT OR IGNORE INTO
                migration_rows (
                    idempotency_key,
                    job_id,
                    source_row,
                    emp_code,
                    result_type,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry[
                        "idempotency_key"
                    ],
                    job_id,
                    int(
                        entry["source_row"]
                    ),
                    entry.get(
                        "emp_code",
                        "",
                    ),
                    entry[
                        "result_type"
                    ],
                    json.dumps(
                        entry["payload"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                ),
            )

        migrated_fields = [
            "employee_code",
            "full_name",
            "date_of_birth",
            "org_id",
        ]

        rejected_fields = [
            "SOURCE_ROW",
            "EMP_CODE",
            "FULL_NAME",
            "DOB",
            "DEPARTMENT",
            "REJECTION_REASON",
        ]

        _atomic_write_csv(
            migrated_path,
            migrated_fields,
            node_input.get(
                "migrated_rows",
                [],
            ),
        )

        _atomic_write_csv(
            rejected_path,
            rejected_fields,
            node_input.get(
                "rejected_rows",
                [],
            ),
        )

        conn.execute(
            """
            UPDATE migration_jobs
            SET
                status = ?,
                migrated_output_path = ?,
                rejected_output_path = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (
                "OUTPUT_WRITTEN",
                migrated_path,
                rejected_path,
                _utc_now(),
                job_id,
            ),
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return Event(
        output={
            **node_input,
            "status":
                "MIGRATION_OUTPUT_WRITTEN",
            "idempotent_replay":
                False,
            "migrated_output_path":
                migrated_path,
            "rejected_output_path":
                rejected_path,
            "migration_ledger_path":
                MIGRATION_LEDGER_PATH,
        }
    )



def verify_migration(node_input: dict):
    """
    Reconcile source/migrated/rejected counts and validate migrated output.

    Important distinction:
    - Reconciliation means every source row is accounted for.
    - Migration success additionally requires at least one migrated row and
      valid migrated output.
    """
    project_root = os.path.dirname(
        os.path.dirname(__file__)
    )

    # A structural parser/schema error is fatal and must never be reported as
    # MIGRATION_COMPLETED.
    if node_input.get("status") == "SOURCE_SCHEMA_ERROR":
        report = {
            "status": "MIGRATION_FAILED_SOURCE_SCHEMA",
            "migration_successful": False,
            "reconciliation_ok": False,
            "output_valid": False,
            "fatal_error": node_input.get("fatal_error"),
            "missing_source_columns": node_input.get(
                "missing_source_columns",
                [],
            ),
            "source_file_info": node_input.get(
                "source_file_info",
                {},
            ),
        }

        output_dir = os.path.join(
            project_root,
            "migration_output",
        )
        os.makedirs(output_dir, exist_ok=True)

        report_path = os.path.join(
            output_dir,
            "migration_report.json",
        )

        with open(
            report_path,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                report,
                f,
                ensure_ascii=False,
                indent=2,
            )

        report["report_path"] = report_path

        return Event(
            output=report,
            message=json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
        )

    source_count = int(
        node_input.get("source_row_count", 0)
    )
    migrated_rows = node_input.get(
        "migrated_rows",
        [],
    )
    rejected_rows = node_input.get(
        "rejected_rows",
        [],
    )

    migrated_count = len(migrated_rows)
    rejected_count = len(rejected_rows)
    accounted_count = (
        migrated_count + rejected_count
    )
    data_loss_count = (
        source_count - accounted_count
    )

    validation_errors = []
    seen_codes = set()

    org_master_path = os.path.join(
        project_root,
        "sample_data",
        "organization_master.csv",
    )

    valid_org_ids = set()

    with open(
        org_master_path,
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        reader = csv.DictReader(f)

        for row in reader:
            try:
                valid_org_ids.add(
                    int(row["ORG_ID"])
                )
            except (
                TypeError,
                ValueError,
                KeyError,
            ):
                continue

    for index, row in enumerate(
        migrated_rows,
        start=1,
    ):
        code = (
            row.get("employee_code") or ""
        ).strip()

        if not code:
            validation_errors.append(
                f"Migrated row {index}: "
                "missing employee_code"
            )
        elif code in seen_codes:
            validation_errors.append(
                f"Migrated row {index}: "
                f"duplicate employee_code {code}"
            )
        else:
            seen_codes.add(code)

        if not (
            row.get("full_name") or ""
        ).strip():
            validation_errors.append(
                f"Migrated row {index}: "
                "missing full_name"
            )

        dob = row.get("date_of_birth")

        try:
            datetime.strptime(
                str(dob),
                "%Y-%m-%d",
            )
        except (
            TypeError,
            ValueError,
        ):
            validation_errors.append(
                f"Migrated row {index}: "
                f"invalid ISO date {dob}"
            )

        try:
            org_id = int(row.get("org_id"))
        except (
            TypeError,
            ValueError,
        ):
            org_id = None

        if org_id not in valid_org_ids:
            validation_errors.append(
                f"Migrated row {index}: "
                f"unknown org_id "
                f"{row.get('org_id')}"
            )

    reconciliation_ok = (
        source_count == accounted_count
        and data_loss_count == 0
    )

    output_valid = (
        len(validation_errors) == 0
    )

    migration_successful = (
        source_count > 0
        and migrated_count > 0
        and reconciliation_ok
        and output_valid
    )

    if not reconciliation_ok or not output_valid:
        final_status = (
            "MIGRATION_COMPLETED_WITH_WARNINGS"
        )
    elif migrated_count == 0:
        final_status = (
            "MIGRATION_FAILED_ALL_ROWS_REJECTED"
        )
    elif rejected_count > 0:
        final_status = (
            "MIGRATION_COMPLETED_WITH_REJECTIONS"
        )
    else:
        final_status = "MIGRATION_COMPLETED"

    rejection_rate = (
        round(
            rejected_count / source_count,
            4,
        )
        if source_count
        else 0.0
    )

    report = {
        "status": final_status,
        "migration_job_id":
            node_input.get(
                "migration_job_id"
            ),
        "idempotent_replay":
            bool(
                node_input.get(
                    "idempotent_replay",
                    False,
                )
            ),
        "migration_ledger_path":
            node_input.get(
                "migration_ledger_path"
            ),
        "migration_successful":
            migration_successful,
        "source_rows": source_count,
        "migrated_rows": migrated_count,
        "rejected_rows": rejected_count,
        "rejection_rate": rejection_rate,
        "accounted_rows": accounted_count,
        "data_loss_rows": data_loss_count,
        "reconciliation_ok":
            reconciliation_ok,
        "output_valid": output_valid,
        "validation_errors":
            validation_errors,
        "date_policy":
            node_input.get("date_policy"),
        "migrated_output_path":
            node_input.get(
                "migrated_output_path"
            ),
        "rejected_output_path":
            node_input.get(
                "rejected_output_path"
            ),
    }

    job_id = node_input.get(
        "migration_job_id"
    )

    output_dir = os.path.join(
        project_root,
        "migration_output",
        job_id,
    )

    os.makedirs(
        output_dir,
        exist_ok=True,
    )

    report_path = os.path.join(
        output_dir,
        "migration_report.json",
    )

    with open(
        report_path,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            report,
            f,
            ensure_ascii=False,
            indent=2,
        )

    report["report_path"] = report_path

    if job_id:
        conn = _open_ledger()

        try:
            conn.execute(
                """
                UPDATE migration_jobs
                SET
                    status = ?,
                    report_path = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    final_status,
                    report_path,
                    _utc_now(),
                    job_id,
                ),
            )

            conn.commit()

        finally:
            conn.close()

    return Event(
        output=report,
        message=json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
    )


root_agent = Workflow(
    name="schemapilot_agent",
    description=(
        "SchemaPilot autonomous data profiling, semantic mapping, "
        "human approval, transformation and reconciliation workflow."
    ),
    edges=[
        # Input.
        ("START", normalize_input),

        # Pillar 1A: deterministic parallel profiling.
        (normalize_input, scan_schema, join_scanners),
        (normalize_input, scan_duplicates, join_scanners),
        (normalize_input, scan_dates, join_scanners),

        # Semantic mapping input.
        (join_scanners, prepare_mapping_input),

        # Multi-agent reasoning in parallel.
        (
            prepare_mapping_input,
            organization_mapping_agent,
            join_mapping_specialists,
        ),
        (
            prepare_mapping_input,
            date_interpretation_agent,
            join_mapping_specialists,
        ),

        # Combine specialist results and apply deterministic risk policy.
        (join_mapping_specialists, combine_mapping_results),
        (combine_mapping_results, calculate_final_risk),
        (calculate_final_risk, risk_router),

        # AUTO goes directly to transformation.
        # REVIEW and HUMAN share a single NEEDS_HUMAN approval route in this MVP.
        (
            risk_router,
            {
                "AUTO": auto_route,
                "NEEDS_HUMAN": prepare_human_review,
            },
        ),

        # Human-in-the-loop path.
        (
            prepare_human_review,
            request_human_decision,
            apply_human_decision,
            post_human_router,
        ),
        (
            post_human_router,
            {
                "CONTINUE": transform_records,
                "STOP": cancelled_route,
            },
        ),

        # Safe AUTO path merges into the same deterministic transformation node.
        (auto_route, transform_records),

        # Finish the actual migration task.
        (transform_records, write_migration_output, verify_migration),
    ],
)