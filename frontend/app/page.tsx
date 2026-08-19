"use client";

import { useMemo, useState } from "react";
import type {
  HumanRequest,
  MigrationSummary,
  RunResult,
  StageState
} from "../lib/adk";

type ViewState = "ready" | "running" | "review" | "complete" | "error";

const initialStages: StageState[] = [
  { name: "Ingest", state: "idle", detail: "Read and normalize source" },
  { name: "Profile", state: "idle", detail: "Schema, duplicates and dates" },
  { name: "Map", state: "idle", detail: "Agentic semantic mapping" },
  { name: "Risk", state: "idle", detail: "Deterministic safety gate" },
  { name: "Human review", state: "idle", detail: "Approval only when required" },
  { name: "Transform", state: "idle", detail: "Create safe target records" },
  { name: "Verify", state: "idle", detail: "Reconcile every source row" }
];

function StageIcon({ state }: { state: StageState["state"] }) {
  if (state === "done") return <span className="stageMark done">✓</span>;
  if (state === "attention") return <span className="stageMark attention">!</span>;
  if (state === "running") return <span className="stageMark running">•</span>;
  return <span className="stageMark idle">•</span>;
}

function Metric({
  label,
  value,
  emphasis = false
}: {
  label: string;
  value: number | string;
  emphasis?: boolean;
}) {
  return (
    <div className={`metric ${emphasis ? "emphasis" : ""}`}>
      <div className="metricValue">{value}</div>
      <div className="metricLabel">{label}</div>
    </div>
  );
}

export default function Home() {
  const [view, setView] = useState<ViewState>("ready");
  const [stages, setStages] = useState<StageState[]>(initialStages);
  const [sessionId, setSessionId] = useState("");
  const [userId, setUserId] = useState("");
  const [humanRequest, setHumanRequest] = useState<HumanRequest | undefined>();
  const [summary, setSummary] = useState<MigrationSummary | undefined>();
  const [decision, setDecision] = useState("APPROVE_DMY");
  const [error, setError] = useState("");
  const [showTechnical, setShowTechnical] = useState(false);
  const [events, setEvents] = useState<Record<string, unknown>[]>([]);

  const completedCount = useMemo(
    () => stages.filter((s) => s.state === "done").length,
    [stages]
  );

  async function startMigration() {
    setView("running");
    setError("");
    setSummary(undefined);
    setHumanRequest(undefined);
    setEvents([]);
    setStages(
      initialStages.map((s, i) => ({
        ...s,
        state: i === 0 ? "running" : "idle"
      }))
    );

    try {
      const response = await fetch("/api/migration/start", { method: "POST" });
      const body = (await response.json()) as RunResult & { error?: string };

      if (!response.ok) throw new Error(body.error || "Migration failed.");

      setSessionId(body.sessionId);
      setUserId(body.userId);
      setStages(body.stages);
      setHumanRequest(body.humanRequest);
      setSummary(body.summary);
      setEvents(body.events);

      if (body.humanRequest) setView("review");
      else if (body.summary) setView("complete");
      else setView("running");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to start migration.");
      setView("error");
    }
  }

  async function approve() {
    if (!sessionId || !userId) return;

    setView("running");
    setError("");

    try {
      const response = await fetch("/api/migration/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          userId,
          sessionId,
          decision,
          humanRequest
        })
      });

      const body = (await response.json()) as RunResult & { error?: string };

      if (!response.ok) throw new Error(body.error || "Approval failed.");

      setStages(body.stages);
      setHumanRequest(body.humanRequest);
      setSummary(body.summary);
      setEvents((old) => [...old, ...body.events]);

      if (body.humanRequest) setView("review");
      else if (body.summary) setView("complete");
      else setView("running");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to continue migration.");
      setView("error");
    }
  }

  function reset() {
    setView("ready");
    setStages(initialStages);
    setSessionId("");
    setUserId("");
    setHumanRequest(undefined);
    setSummary(undefined);
    setError("");
    setEvents([]);
    setDecision("APPROVE_DMY");
  }

  const isBusy = view === "running";

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brandMark"><span /><span /><span /></div>
          <div>
            <div className="brandName">SchemaPilot</div>
            <div className="brandTag">Agentic migration control plane</div>
          </div>
        </div>

        <div className="cloudBadge">
          <span className="liveDot" />
          Google Cloud · Live backend
        </div>
      </header>

      <section className="hero">
        <div className="eyebrow">LEGACY DATA → VERIFIED TARGET</div>
        <h1>Move messy enterprise data<br />without losing control.</h1>
        <p>
          SchemaPilot profiles legacy exports, coordinates specialist agents,
          escalates uncertainty to a human, and verifies every migrated row.
        </p>

        <div className="heroActions">
          <button
            className="primaryButton"
            onClick={startMigration}
            disabled={isBusy || view === "review"}
          >
            {isBusy ? (
              <>
                <span className="spinner" /> Running agent workflow
              </>
            ) : (
              <>▶ Run cloud demo</>
            )}
          </button>

          {view !== "ready" && (
            <button className="ghostButton" onClick={reset} disabled={isBusy}>
              New migration
            </button>
          )}
        </div>
      </section>

      <section className="workspace">
        <div className="panel pipelinePanel">
          <div className="panelHeader">
            <div>
              <div className="kicker">LIVE ORCHESTRATION</div>
              <h2>Migration pipeline</h2>
            </div>
            <div className="progressText">{completedCount}/{stages.length} stages</div>
          </div>

          <div className="stages">
            {stages.map((stage, index) => (
              <div className="stageRow" key={stage.name}>
                <div className="stageRail">
                  <StageIcon state={stage.state} />
                  {index < stages.length - 1 && <div className="railLine" />}
                </div>
                <div className="stageCopy">
                  <div className="stageName">{stage.name}</div>
                  <div className="stageDetail">{stage.detail}</div>
                </div>
                <div className={`stageStatus ${stage.state}`}>
                  {stage.state === "done" && "Complete"}
                  {stage.state === "running" && "Running"}
                  {stage.state === "attention" && "Action needed"}
                  {stage.state === "idle" && "Pending"}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="rightColumn">
          {view === "ready" && (
            <div className="panel sourcePanel">
              <div className="kicker">DEMO SOURCE</div>
              <h2>Employee migration sample</h2>
              <div className="fileCard">
                <div className="fileIcon">CSV</div>
                <div>
                  <div className="fileName">employees.csv</div>
                  <div className="fileMeta">5 synthetic records · malformed legacy TSV</div>
                </div>
                <div className="fileCheck">✓</div>
              </div>

              <div className="sourceFacts">
                <div><span>Target</span><strong>Normalized employee schema</strong></div>
                <div><span>Agents</span><strong>Organization + Date specialists</strong></div>
                <div><span>Safety</span><strong>Risk gate + HITL + reconciliation</strong></div>
              </div>

              <div className="notice">
                Real file upload is the next integration step. This frontend
                currently runs the packaged Cloud Run demo end-to-end.
              </div>
            </div>
          )}

          {view === "running" && (
            <div className="panel activePanel">
              <div className="pulseOrb"><span /></div>
              <div className="kicker">AGENTS AT WORK</div>
              <h2>SchemaPilot is reasoning across the migration.</h2>
              <p>
                Deterministic profiling runs first. Gemini specialists are only
                invoked for semantic interpretation.
              </p>
              <div className="activityLines">
                <div><span /> Reading source structure</div>
                <div><span /> Comparing organization master data</div>
                <div><span /> Evaluating ambiguous date values</div>
              </div>
            </div>
          )}

          {view === "review" && (
            <div className="panel reviewPanel">
              <div className="reviewFlag">HUMAN REVIEW REQUIRED</div>
              <h2>SchemaPilot found a decision it should not guess.</h2>
              <p className="reviewMessage">
                {humanRequest?.message ||
                  "Choose how ambiguous slash-formatted dates should be interpreted."}
              </p>

              <div className="decisionGroup">
                <label className={`decisionCard ${decision === "APPROVE_DMY" ? "selected" : ""}`}>
                  <input
                    type="radio"
                    value="APPROVE_DMY"
                    checked={decision === "APPROVE_DMY"}
                    onChange={(e) => setDecision(e.target.value)}
                  />
                  <div>
                    <strong>DD/MM/YYYY</strong>
                    <span>01/04/1990 → 1 April 1990</span>
                  </div>
                  <div className="radioDot" />
                </label>

                <label className={`decisionCard ${decision === "APPROVE_MDY" ? "selected" : ""}`}>
                  <input
                    type="radio"
                    value="APPROVE_MDY"
                    checked={decision === "APPROVE_MDY"}
                    onChange={(e) => setDecision(e.target.value)}
                  />
                  <div>
                    <strong>MM/DD/YYYY</strong>
                    <span>01/04/1990 → January 4, 1990</span>
                  </div>
                  <div className="radioDot" />
                </label>

                <label className={`decisionCard reject ${decision === "REJECT" ? "selected" : ""}`}>
                  <input
                    type="radio"
                    value="REJECT"
                    checked={decision === "REJECT"}
                    onChange={(e) => setDecision(e.target.value)}
                  />
                  <div>
                    <strong>Reject migration</strong>
                    <span>Stop before any unsafe transformation</span>
                  </div>
                  <div className="radioDot" />
                </label>
              </div>

              <button className="primaryButton full" onClick={approve}>
                Approve & continue migration
              </button>

              <div className="safetyNote">
                The model proposes. Deterministic policy and humans authorize.
              </div>
            </div>
          )}

          {view === "complete" && summary && (
            <div className="panel resultPanel">
              <div className="resultHeadline">
                <div className="successIcon">✓</div>
                <div>
                  <div className="kicker">MIGRATION VERIFIED</div>
                  <h2>Completed with controlled rejections.</h2>
                </div>
              </div>

              <div className="metricsGrid">
                <Metric label="Source" value={summary.source_rows ?? "—"} />
                <Metric label="Migrated" value={summary.migrated_rows ?? "—"} emphasis />
                <Metric label="Rejected" value={summary.rejected_rows ?? "—"} />
                <Metric label="Data loss" value={summary.data_loss_rows ?? "—"} emphasis />
              </div>

              <div className="checks">
                <div className={summary.reconciliation_ok ? "pass" : "fail"}>
                  <span>{summary.reconciliation_ok ? "✓" : "!"}</span>
                  Reconciliation {summary.reconciliation_ok ? "passed" : "failed"}
                </div>
                <div className={summary.output_valid ? "pass" : "fail"}>
                  <span>{summary.output_valid ? "✓" : "!"}</span>
                  Output {summary.output_valid ? "validated" : "needs review"}
                </div>
                <div className="pass">
                  <span>✓</span>
                  Date policy: {summary.date_policy || "—"}
                </div>
              </div>

              <div className="jobMeta">
                <span>Migration job</span>
                <code>{summary.migration_job_id || "not reported"}</code>
                {summary.idempotent_replay && <span className="replayBadge">safe replay</span>}
              </div>

              <button className="ghostButton full" onClick={reset}>
                Run another migration
              </button>
            </div>
          )}

          {view === "error" && (
            <div className="panel errorPanel">
              <div className="errorIcon">!</div>
              <div className="kicker">MIGRATION INTERRUPTED</div>
              <h2>The frontend could not complete the request.</h2>
              <p>{error}</p>
              <button className="primaryButton" onClick={reset}>Reset</button>
            </div>
          )}
        </div>
      </section>

      {(events.length > 0 || sessionId) && (
        <section className="technical">
          <button
            className="technicalToggle"
            onClick={() => setShowTechnical((v) => !v)}
          >
            {showTechnical ? "Hide" : "Show"} technical evidence
            <span>{showTechnical ? "−" : "+"}</span>
          </button>

          {showTechnical && (
            <div className="technicalBody">
              <div className="technicalMeta">
                <div><span>Session</span><code>{sessionId}</code></div>
                <div><span>Events received</span><code>{events.length}</code></div>
              </div>
              <pre>{JSON.stringify(events, null, 2)}</pre>
            </div>
          )}
        </section>
      )}

      <footer>
        <span>SchemaPilot</span>
        <span>Gemini · Google ADK · Vertex AI · Cloud Run</span>
      </footer>
    </main>
  );
}
