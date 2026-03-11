# Cloud Run Deployment Guide — g3lobster

## 1. Prerequisites

### Tools
- [gcloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated
- Docker (only needed for local builds; Cloud Build handles remote builds)

```bash
gcloud auth login
gcloud auth configure-docker <REGION>-docker.pkg.dev
```

### GCP Project Setup
```bash
export PROJECT_ID=your-project-id
export REGION=us-central1
export REPO=g3lobster
export IMAGE=g3lobster

gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com
```

### Artifact Registry Repository
```bash
gcloud artifacts repositories create $REPO \
  --repository-format=docker \
  --location=$REGION \
  --description="g3lobster container images"
```

### Service Account (for Cloud Run)
```bash
export SA_NAME=g3lobster-run-sa

gcloud iam service-accounts create $SA_NAME \
  --display-name="g3lobster Cloud Run SA"

# Grant access to Secret Manager
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Grant access to GCS (for data persistence)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

---

## 2. Quick Deploy Commands

### Build and Push with Cloud Build
```bash
gcloud builds submit \
  --config=cloudbuild.yaml \
  --substitutions=_REGION=$REGION,_REPO=$REPO,_IMAGE=$IMAGE \
  .
```

The resulting image is pushed to:
```
$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE
```

### Deploy to Cloud Run
```bash
gcloud run services replace service.cloudrun.yaml \
  --region=$REGION
```

Or deploy directly without a manifest:
```bash
gcloud run deploy g3lobster \
  --image=$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE:latest \
  --region=$REGION \
  --service-account=${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com \
  --port=8080 \
  --allow-unauthenticated
```

### One-liner: Build + Deploy
```bash
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=_REGION=$REGION,_REPO=$REPO,_IMAGE=$IMAGE . && \
gcloud run services replace service.cloudrun.yaml --region=$REGION
```

---

## 3. Environment Variables Reference

All g3lobster configuration can be overridden via environment variables following the pattern:

```
G3LOBSTER_<SECTION>_<KEY>=value
```

Set these in `service.cloudrun.yaml` under `spec.template.spec.containers[].env` or via:
```bash
gcloud run services update g3lobster --set-env-vars KEY=VALUE --region=$REGION
```

### Agents
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_AGENTS_DATA_DIR` | Directory for agent data/state files | `./data` |
| `G3LOBSTER_AGENTS_COMPACT_THRESHOLD` | Message count to trigger compaction | `40` |
| `G3LOBSTER_AGENTS_CONTEXT_MESSAGES` | Recent messages kept in agent context | `12` |
| `G3LOBSTER_AGENTS_HEALTH_CHECK_INTERVAL_S` | Health check interval (seconds) | `30` |
| `G3LOBSTER_AGENTS_STUCK_TIMEOUT_S` | Restart stuck agents after N seconds (0=disabled) | `0` |

### Gemini
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_GEMINI_COMMAND` | Path/name of gemini CLI binary | `gemini` |
| `G3LOBSTER_GEMINI_WORKSPACE_DIR` | Working directory for gemini process | `.` |
| `G3LOBSTER_GEMINI_RESPONSE_TIMEOUT_S` | Per-task timeout (0=disabled) | `0` |

### MCP
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_MCP_CONFIG_DIR` | Directory containing MCP server configs | `./config/mcp` |
| `G3LOBSTER_MCP_DEFAULT_SERVERS` | Comma-separated server list (`*` = all) | `*` |

### Chat
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_CHAT_ENABLED` | Enable Google Chat bridge | `false` |
| `G3LOBSTER_CHAT_POLL_INTERVAL_S` | Chat polling interval (seconds) | `2.0` |
| `G3LOBSTER_CHAT_CONCIERGE_ENABLED` | Enable concierge routing agent | `false` |

### Email
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_EMAIL_ENABLED` | Enable email integration | `false` |
| `G3LOBSTER_EMAIL_POLL_INTERVAL_S` | Email polling interval (seconds) | `30.0` |
| `G3LOBSTER_EMAIL_BASE_ADDRESS` | Base email address | _(empty)_ |

### Calendar
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_CALENDAR_ENABLED` | Enable calendar integration | `false` |
| `G3LOBSTER_CALENDAR_POLL_INTERVAL_S` | Calendar polling interval (seconds) | `300.0` |
| `G3LOBSTER_CALENDAR_LOOKAHEAD_MINUTES` | Meeting lookahead window | `15` |

### Cron
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_CRON_ENABLED` | Enable cron/scheduled tasks | `true` |

### Server
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_SERVER_HOST` | Bind host | `0.0.0.0` |
| `G3LOBSTER_SERVER_PORT` | Bind port (overridden by `$PORT` on Cloud Run) | `20001` |

### Alerts
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_ALERTS_ENABLED` | Enable alerting | `false` |
| `G3LOBSTER_ALERTS_WEBHOOK_URL` | Webhook URL for alert delivery | _(empty)_ |
| `G3LOBSTER_ALERTS_MIN_SEVERITY` | Minimum alert severity (warning/error/critical) | `warning` |

### Control Plane
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_CONTROL_PLANE_ENABLED` | Enable control plane | `true` |
| `G3LOBSTER_CONTROL_PLANE_QUEUE_DEPTH` | Max queued tasks per agent | `5` |
| `G3LOBSTER_CONTROL_PLANE_MAX_TASKS` | Global task limit | `5000` |
| `G3LOBSTER_CONTROL_PLANE_TMUX_ENABLED` | Enable tmux sessions (disable on Cloud Run) | `false` |

### Auth
| Variable | Description | Default |
|---|---|---|
| `G3LOBSTER_AUTH_ENABLED` | Enable API key authentication | `false` |
| `G3LOBSTER_AUTH_API_KEY` | API key (use Secret Manager on Cloud Run) | _(empty)_ |

---

## 4. Secrets Management

### Create Secrets in Secret Manager
```bash
# Gemini API key (required)
echo -n "your-gemini-api-key" | \
  gcloud secrets create GOOGLE_API_KEY \
    --data-file=- \
    --replication-policy=automatic

# g3lobster API auth key (optional)
echo -n "your-api-key" | \
  gcloud secrets create G3LOBSTER_AUTH_API_KEY \
    --data-file=- \
    --replication-policy=automatic
```

### Update an Existing Secret
```bash
echo -n "new-value" | \
  gcloud secrets versions add GOOGLE_API_KEY --data-file=-
```

### Reference Secrets in service.cloudrun.yaml
Secrets are injected as environment variables via the `secretKeyRef` field:

```yaml
spec:
  template:
    spec:
      containers:
        - image: ...
          env:
            - name: GOOGLE_API_KEY
              valueFrom:
                secretKeyRef:
                  name: GOOGLE_API_KEY
                  key: latest
            - name: G3LOBSTER_AUTH_API_KEY
              valueFrom:
                secretKeyRef:
                  name: G3LOBSTER_AUTH_API_KEY
                  key: latest
```

Or add/update via CLI:
```bash
gcloud run services update g3lobster \
  --update-secrets=GOOGLE_API_KEY=GOOGLE_API_KEY:latest \
  --update-secrets=G3LOBSTER_AUTH_API_KEY=G3LOBSTER_AUTH_API_KEY:latest \
  --region=$REGION
```

---

## 5. Data Persistence

Cloud Run containers are stateless. Use a GCS bucket mounted via Cloud Storage FUSE to persist agent state, chat history, and cron data.

### Create a GCS Bucket
```bash
export BUCKET=your-project-g3lobster-data

gcloud storage buckets create gs://$BUCKET \
  --location=$REGION \
  --uniform-bucket-level-access
```

### Grant Bucket Access to Service Account
```bash
gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

### Configure GCS FUSE Mount in service.cloudrun.yaml
```yaml
spec:
  template:
    metadata:
      annotations:
        run.googleapis.com/execution-environment: gen2
    spec:
      containers:
        - image: ...
          env:
            - name: G3LOBSTER_AGENTS_DATA_DIR
              value: /data
          volumeMounts:
            - name: gcs-data
              mountPath: /data
      volumes:
        - name: gcs-data
          csi:
            driver: gcsfuse.run.googleapis.com
            volumeAttributes:
              bucketName: your-project-g3lobster-data
```

Note: GCS FUSE requires the `gen2` execution environment and the Storage Object Admin role on the service account.

---

## 6. Health Checks

The app exposes `GET /health` returning `{"status": "ok"}`.

Configure liveness and startup probes in `service.cloudrun.yaml`:

```yaml
spec:
  template:
    spec:
      containers:
        - image: ...
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 30
            failureThreshold: 3
            timeoutSeconds: 5
          startupProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 10
            failureThreshold: 12   # 12 * 10s = 2 min startup budget
            timeoutSeconds: 5
```

Verify health manually after deploy:
```bash
SERVICE_URL=$(gcloud run services describe g3lobster \
  --region=$REGION --format='value(status.url)')

curl -s $SERVICE_URL/health
# Expected: {"status": "ok"}
```

---

## 7. Updating and Rollback

### Deploy a New Revision
```bash
# Rebuild and push
gcloud builds submit --config=cloudbuild.yaml \
  --substitutions=_REGION=$REGION,_REPO=$REPO,_IMAGE=$IMAGE .

# Apply updated service manifest
gcloud run services replace service.cloudrun.yaml --region=$REGION
```

### List Revisions
```bash
gcloud run revisions list \
  --service=g3lobster \
  --region=$REGION \
  --sort-by=~createTime
```

### Rollback to a Previous Revision
```bash
# Route 100% of traffic to a specific revision
gcloud run services update-traffic g3lobster \
  --to-revisions=g3lobster-00010-abc=100 \
  --region=$REGION
```

### Gradual Traffic Migration (Canary)
```bash
# Send 10% to new revision, 90% to previous
gcloud run services update-traffic g3lobster \
  --to-revisions=LATEST=10,g3lobster-00010-abc=90 \
  --region=$REGION

# Promote to 100% when satisfied
gcloud run services update-traffic g3lobster \
  --to-latest \
  --region=$REGION
```

### Update a Single Environment Variable Without Rebuild
```bash
gcloud run services update g3lobster \
  --set-env-vars G3LOBSTER_CHAT_ENABLED=false \
  --region=$REGION
```

### View Logs
```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=g3lobster" \
  --limit=50 \
  --format="value(timestamp, textPayload)"
```
