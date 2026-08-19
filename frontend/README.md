# SchemaPilot Frontend MVP

Purpose-built hackathon UI for the deployed SchemaPilot ADK workflow.

## Current scope

- Creates a fresh ADK session.
- Sends `RUN_DEMO` to the deployed Cloud Run backend.
- Shows a product-style migration pipeline.
- Detects ADK `RequestInput` events.
- Renders DD/MM/YYYY, MM/DD/YYYY and reject choices.
- Sends the decision back to the same ADK session.
- Shows final migration and reconciliation metrics.
- Includes expandable raw ADK events as technical evidence.

The current MVP intentionally uses the packaged Cloud Run demo dataset.
Real browser file upload needs a storage / ingestion endpoint and is the next integration step.

## Configure

Copy `.env.example` to `.env.local`.

```env
ADK_BASE_URL=https://YOUR-CLOUD-RUN-SERVICE.run.app
ADK_APP_NAME=schemapilot_agent
```

No trailing slash.

## Run

```powershell
npm install
npm run dev
```

Open:

```text
http://localhost:3000
```

Click **Run cloud demo**.

## API flow

The frontend uses the official ADK REST flow:

1. `POST /apps/{app_name}/users/{user_id}/sessions/{session_id}`
2. `POST /run` with `RUN_DEMO`
3. When a `RequestInput` event is returned, send the selected human decision to `/run` on the same session.
4. Parse the final workflow event `output` into migration metrics.

The browser talks only to Next.js `/api/migration/*`; the Next.js server proxies to Cloud Run, which avoids coupling the browser to backend CORS settings.

## Next frontend milestone

- Real CSV upload via Cloud Storage or a dedicated backend ingestion endpoint.
- Persistent artifact download URLs.
- Authentication.
