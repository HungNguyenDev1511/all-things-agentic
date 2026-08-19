import {
  AdkEvent,
  HumanRequest,
  RunResult,
  buildStages,
  extractHumanRequest,
  extractSummary
} from "./adk";

const baseUrl = process.env.ADK_BASE_URL?.replace(/\/$/, "");
const appName = process.env.ADK_APP_NAME || "schemapilot_agent";

function requireConfig() {
  if (!baseUrl) {
    throw new Error(
      "ADK_BASE_URL is not configured. Copy .env.example to .env.local."
    );
  }

  return { baseUrl, appName };
}

async function adkFetch(path: string, init?: RequestInit) {
  const { baseUrl } = requireConfig();

  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {})
    }
  });

  const text = await response.text();
  let body: unknown = null;

  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!response.ok) {
    throw new Error(
      `ADK ${response.status}: ${
        typeof body === "string" ? body : JSON.stringify(body)
      }`
    );
  }

  return body;
}

async function createSession(userId: string, sessionId: string) {
  const { appName } = requireConfig();

  await adkFetch(
    `/apps/${encodeURIComponent(appName)}/users/${encodeURIComponent(
      userId
    )}/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: "POST",
      body: JSON.stringify({
        frontend: "schemapilot",
        startedAt: new Date().toISOString()
      })
    }
  );
}

async function runTextMessage(
  userId: string,
  sessionId: string,
  text: string
): Promise<AdkEvent[]> {
  const { appName } = requireConfig();

  const response = await adkFetch("/run", {
    method: "POST",
    body: JSON.stringify({
      appName,
      userId,
      sessionId,
      newMessage: {
        role: "user",
        parts: [{ text }]
      }
    })
  });

  return Array.isArray(response) ? (response as AdkEvent[]) : [];
}

async function resumeRequestInput(
  userId: string,
  sessionId: string,
  request: HumanRequest,
  decision: string
): Promise<AdkEvent[]> {
  const { appName } = requireConfig();

  // This matches ADK's own CLI resume behavior:
  // - same session
  // - same invocationId
  // - FunctionResponse id == RequestInput functionCall id
  // - response: { result: <human text> }
  const response = await adkFetch("/run", {
    method: "POST",
    body: JSON.stringify({
      appName,
      userId,
      sessionId,
      invocationId: request.invocationId,
      newMessage: {
        role: "user",
        parts: [
          {
            functionResponse: {
              id: request.interruptId,
              name: request.functionName,
              response: {
                result: decision
              }
            }
          }
        ]
      }
    })
  });

  return Array.isArray(response) ? (response as AdkEvent[]) : [];
}

function makeResult(
  userId: string,
  sessionId: string,
  events: AdkEvent[]
): RunResult {
  const humanRequest = extractHumanRequest(events);
  const summary = extractSummary(events);

  return {
    userId,
    sessionId,
    events,
    humanRequest,
    summary,
    stages: buildStages(events, Boolean(humanRequest), Boolean(summary))
  };
}

export async function startDemo(): Promise<RunResult> {
  const userId = `web-${crypto.randomUUID()}`;
  const sessionId = `migration-${crypto.randomUUID()}`;

  await createSession(userId, sessionId);

  const events = await runTextMessage(
    userId,
    sessionId,
    "RUN_DEMO"
  );

  return makeResult(userId, sessionId, events);
}

export async function submitHumanDecision(
  userId: string,
  sessionId: string,
  humanRequest: HumanRequest,
  decision: string
): Promise<RunResult> {
  const allowed = new Set([
    "APPROVE_DMY",
    "APPROVE_MDY",
    "REJECT"
  ]);

  if (!allowed.has(decision)) {
    throw new Error("Unsupported human decision.");
  }

  const events = await resumeRequestInput(
    userId,
    sessionId,
    humanRequest,
    decision
  );

  return makeResult(userId, sessionId, events);
}
