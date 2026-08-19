export type AdkEvent = Record<string, unknown>;

export type HumanRequest = {
  message: string;
  payload?: unknown;
  interruptId: string;
  invocationId?: string;
  functionName: "adk_request_input";
};

export type MigrationSummary = {
  status?: string;
  migration_job_id?: string;
  migration_successful?: boolean;
  source_rows?: number;
  migrated_rows?: number;
  rejected_rows?: number;
  rejection_rate?: number;
  accounted_rows?: number;
  data_loss_rows?: number;
  reconciliation_ok?: boolean;
  output_valid?: boolean;
  validation_errors?: unknown[];
  date_policy?: string;
  idempotent_replay?: boolean;
};

export type StageName =
  | "Ingest"
  | "Profile"
  | "Map"
  | "Risk"
  | "Human review"
  | "Transform"
  | "Verify";

export type StageState = {
  name: StageName;
  state: "idle" | "running" | "done" | "attention";
  detail: string;
};

export type RunResult = {
  sessionId: string;
  userId: string;
  events: AdkEvent[];
  humanRequest?: HumanRequest;
  summary?: MigrationSummary;
  stages: StageState[];
};

const orderedStages: StageName[] = [
  "Ingest",
  "Profile",
  "Map",
  "Risk",
  "Human review",
  "Transform",
  "Verify"
];

const nodeToStage: Array<[string, StageName]> = [
  ["normalize_input", "Ingest"],

  ["scan_schema", "Profile"],
  ["scan_duplicates", "Profile"],
  ["scan_dates", "Profile"],
  ["join_scanners", "Profile"],

  ["prepare_mapping_input", "Map"],
  ["organization_mapping_agent", "Map"],
  ["date_interpretation_agent", "Map"],
  ["join_mapping_specialists", "Map"],
  ["combine_mapping_results", "Map"],

  ["calculate_final_risk", "Risk"],
  ["risk_router", "Risk"],

  ["prepare_human_review", "Human review"],
  ["request_human_decision", "Human review"],
  ["apply_human_decision", "Human review"],
  ["post_human_router", "Human review"],

  ["transform_records", "Transform"],
  ["write_migration_output", "Transform"],

  ["verify_migration", "Verify"]
];

const baseStages: StageState[] = [
  { name: "Ingest", state: "idle", detail: "Read and normalize source" },
  { name: "Profile", state: "idle", detail: "Schema, duplicates and dates" },
  { name: "Map", state: "idle", detail: "Agentic semantic mapping" },
  { name: "Risk", state: "idle", detail: "Deterministic safety gate" },
  { name: "Human review", state: "idle", detail: "Approval only when required" },
  { name: "Transform", state: "idle", detail: "Create safe target records" },
  { name: "Verify", state: "idle", detail: "Reconcile every source row" }
];

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function getNodeName(event: AdkEvent): string | undefined {
  const nodeInfo =
    (event.nodeInfo as Record<string, unknown> | undefined) ??
    (event.node_info as Record<string, unknown> | undefined);

  // ADK 2.x Workflow REST events expose nodeInfo.path, e.g.
  // "schemapilot_agent@1/normalize_input@1".
  const path = nodeInfo?.path;
  if (typeof path === "string" && path.length > 0) {
    const lastSegment = path.split("/").at(-1) ?? path;
    return lastSegment.replace(/@\d+$/, "");
  }

  // Newer ADK versions may expose a direct node name.
  const direct =
    event.nodeName ??
    event.node_name ??
    nodeInfo?.nodeName ??
    nodeInfo?.node_name;

  return typeof direct === "string" ? direct : undefined;
}

function getStageForNode(nodeName?: string): StageName | undefined {
  if (!nodeName) return undefined;

  const normalized = nodeName.toLowerCase();
  return nodeToStage.find(([needle]) =>
    normalized.includes(needle.toLowerCase())
  )?.[1];
}

function getContentParts(event: AdkEvent): Record<string, unknown>[] {
  const content = event.content;
  if (!isObject(content)) return [];

  return asArray(content.parts).filter(isObject);
}

function getLongRunningIds(event: AdkEvent): string[] {
  const value = event.longRunningToolIds ?? event.long_running_tool_ids;
  return asArray(value).filter((v): v is string => typeof v === "string");
}

export function extractHumanRequest(
  events: AdkEvent[]
): HumanRequest | undefined {
  // RequestInput is serialized by ADK as a synthetic long-running
  // function call named "adk_request_input".
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const event = events[i];
    const longRunningIds = new Set(getLongRunningIds(event));

    for (const part of getContentParts(event)) {
      const functionCall =
        (part.functionCall as Record<string, unknown> | undefined) ??
        (part.function_call as Record<string, unknown> | undefined);

      if (!isObject(functionCall)) continue;

      const id = functionCall.id;
      const name = functionCall.name;
      const args = functionCall.args;

      if (
        typeof id !== "string" ||
        name !== "adk_request_input" ||
        (longRunningIds.size > 0 && !longRunningIds.has(id))
      ) {
        continue;
      }

      const argObject = isObject(args) ? args : {};
      const message = argObject.message;
      const invocationId =
        event.invocationId ?? event.invocation_id;

      return {
        interruptId: id,
        functionName: "adk_request_input",
        invocationId:
          typeof invocationId === "string" ? invocationId : undefined,
        message:
          typeof message === "string"
            ? message
            : "Human review is required before migration can continue.",
        payload: argObject.payload
      };
    }
  }

  return undefined;
}

function extractOutput(event: AdkEvent): unknown {
  if ("output" in event) return event.output;
  if ("Output" in event) return event.Output;
  return undefined;
}

export function extractSummary(
  events: AdkEvent[]
): MigrationSummary | undefined {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const output = extractOutput(events[i]);
    if (!isObject(output)) continue;

    const status = output.status;
    const hasMigrationSignals =
      (typeof status === "string" && status.includes("MIGRATION_")) ||
      "reconciliation_ok" in output ||
      "migration_successful" in output;

    if (hasMigrationSignals) return output as MigrationSummary;
  }

  return undefined;
}

export function buildStages(
  events: AdkEvent[],
  hasHumanRequest: boolean,
  hasSummary: boolean
): StageState[] {
  const result = baseStages.map((stage) => ({ ...stage }));

  let furthestIndex = -1;

  for (const event of events) {
    const stageName = getStageForNode(getNodeName(event));
    if (!stageName) continue;

    furthestIndex = Math.max(
      furthestIndex,
      orderedStages.indexOf(stageName)
    );
  }

  // A RequestInput event itself is enough to prove the workflow reached HITL.
  if (hasHumanRequest) {
    furthestIndex = Math.max(
      furthestIndex,
      orderedStages.indexOf("Human review")
    );
  }

  // Mark every stage before the furthest observed stage complete.
  // This fixes the old UI where Map could show COMPLETE while Ingest/Profile
  // were still shown as PENDING even though a DAG cannot reach Map first.
  for (let i = 0; i < result.length; i += 1) {
    if (i < furthestIndex) result[i].state = "done";
  }

  if (hasSummary) {
    for (const stage of result) stage.state = "done";
    return result;
  }

  if (hasHumanRequest) {
    const humanIndex = orderedStages.indexOf("Human review");

    for (let i = 0; i < humanIndex; i += 1) {
      result[i].state = "done";
    }

    result[humanIndex].state = "attention";
    result[humanIndex].detail = "Decision required to continue";

    result[orderedStages.indexOf("Transform")].detail =
      "Waiting for approval";
    result[orderedStages.indexOf("Verify")].detail =
      "Waiting for approval";

    return result;
  }

  if (furthestIndex >= 0) {
    result[furthestIndex].state = "done";

    const nextIndex = furthestIndex + 1;
    if (nextIndex < result.length) {
      result[nextIndex].state = "running";
    }
  } else if (events.length > 0) {
    result[0].state = "running";
  }

  return result;
}
