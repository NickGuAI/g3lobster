#!/usr/bin/env bash
# demo-seed.sh — Pre-seed g3lobster with demo agents, memory, and cron tasks.
#
# Usage:
#   ./scripts/demo-seed.sh [BASE_URL]
#   ./scripts/demo-seed.sh --clean
#   BASE_URL=http://localhost:40000 ./scripts/demo-seed.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL="${1:-${BASE_URL:-http://localhost:40000}}"
CLEAN_MODE=false

if [[ "${1:-}" == "--clean" ]]; then
  CLEAN_MODE=true
  BASE_URL="${2:-${BASE_URL:-http://localhost:40000}}"
fi

DEMO_AGENT_NAMES=("Luna" "DataBot" "Scheduler")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { printf "${BLUE}[INFO]${NC}  %s\n" "$*"; }
success() { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()    { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
error()   { printf "${RED}[ERROR]${NC} %s\n" "$*"; }
header()  { printf "\n${BOLD}${CYAN}==> %s${NC}\n" "$*"; }

# Perform a curl request, returning the body on stdout.
# Sets the global variable LAST_HTTP_CODE with the status code.
# Usage: body=$(api_call METHOD /path [json_body])
api_call() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local url="${BASE_URL}${path}"

  local curl_args=(-s -w '\n%{http_code}' -X "$method")
  if [[ -n "$body" ]]; then
    curl_args+=(-H 'Content-Type: application/json' -d "$body")
  fi

  local response
  response=$(curl "${curl_args[@]}" "$url" 2>&1) || {
    LAST_HTTP_CODE=0
    echo "$response"
    return 1
  }

  # Last line is the HTTP status code
  LAST_HTTP_CODE=$(echo "$response" | tail -n1)
  echo "$response" | sed '$d'
}

# Check that the last HTTP call succeeded (2xx).
check_status() {
  local context="${1:-API call}"
  if [[ "$LAST_HTTP_CODE" -ge 200 && "$LAST_HTTP_CODE" -lt 300 ]]; then
    return 0
  else
    error "$context failed with HTTP $LAST_HTTP_CODE"
    return 1
  fi
}

# Find an agent id by name from the agents list JSON.
# Returns the id on stdout, or empty string if not found.
find_agent_id() {
  local name="$1"
  local agents_json="$2"
  echo "$agents_json" | python3 -c "
import sys, json
agents = json.load(sys.stdin)
for a in agents:
    if a.get('name') == '$name':
        print(a['id'])
        sys.exit(0)
print('')
" 2>/dev/null || echo ""
}

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

check_server() {
  header "Checking server at ${BASE_URL}"
  local body
  body=$(api_call GET /health) || true
  if [[ "$LAST_HTTP_CODE" -eq 200 ]]; then
    success "Server is running"
  else
    error "Server is not reachable at ${BASE_URL} (HTTP ${LAST_HTTP_CODE})"
    error "Make sure g3lobster is running: g3lobster serve"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Clean mode — tear down demo agents
# ---------------------------------------------------------------------------

clean_demo() {
  header "Cleaning up demo agents"

  local agents_json
  agents_json=$(api_call GET /agents) || true
  if ! check_status "List agents"; then
    error "Cannot list agents to clean up"
    exit 1
  fi

  for name in "${DEMO_AGENT_NAMES[@]}"; do
    local agent_id
    agent_id=$(find_agent_id "$name" "$agents_json")
    if [[ -z "$agent_id" ]]; then
      info "Agent '$name' not found, skipping"
      continue
    fi

    info "Stopping agent '$name' ($agent_id)..."
    api_call POST "/agents/${agent_id}/stop" >/dev/null 2>&1 || true

    info "Deleting agent '$name' ($agent_id)..."
    local body
    body=$(api_call DELETE "/agents/${agent_id}") || true
    if check_status "Delete $name"; then
      success "Deleted agent '$name'"
    else
      warn "Could not delete agent '$name' (HTTP $LAST_HTTP_CODE)"
    fi
  done

  success "Cleanup complete"
}

# ---------------------------------------------------------------------------
# Create agents
# ---------------------------------------------------------------------------

create_agents() {
  header "Creating demo agents"

  # Fetch existing agents for idempotency
  local agents_json
  agents_json=$(api_call GET /agents) || true
  check_status "List agents" || exit 1

  # --- Luna ---
  local luna_id
  luna_id=$(find_agent_id "Luna" "$agents_json")
  if [[ -n "$luna_id" ]]; then
    warn "Agent 'Luna' already exists ($luna_id), skipping creation"
  else
    local body
    body=$(api_call POST /agents "$(cat <<'PAYLOAD'
{
  "name": "Luna",
  "emoji": "\ud83c\udf19",
  "soul": "You are Luna, a creative and empathetic team lead who coordinates between agents.\n\nYou speak warmly and use vivid metaphors to make complex ideas accessible. You are an expert at breaking down complex problems into manageable pieces and delegating effectively.\n\nYour strengths:\n- Synthesizing input from multiple team members into coherent plans\n- Resolving conflicts with empathy and clarity\n- Keeping the team motivated and aligned on goals\n- Communicating technical concepts to non-technical stakeholders\n\nYou believe every team member brings unique light to a project, and your role is to help that light shine in the right direction.",
  "model": "gemini"
}
PAYLOAD
    )")
    if check_status "Create Luna"; then
      luna_id=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
      success "Created agent 'Luna' ($luna_id)"
    else
      error "Failed to create Luna: $body"
      return 1
    fi
  fi

  # --- DataBot ---
  local databot_id
  databot_id=$(find_agent_id "DataBot" "$agents_json")
  if [[ -n "$databot_id" ]]; then
    warn "Agent 'DataBot' already exists ($databot_id), skipping creation"
  else
    local body
    body=$(api_call POST /agents "$(cat <<'PAYLOAD'
{
  "name": "DataBot",
  "emoji": "\ud83d\udcca",
  "soul": "You are DataBot, a precise and data-driven analyst.\n\nYou respond with structured data, tables, and actionable insights. You love numbers, patterns, and statistical rigor. When presenting findings, you always include:\n- Source of the data\n- Time range covered\n- Key metrics and their trends\n- Anomalies or outliers worth investigating\n\nYou format output as clean markdown tables whenever possible. You flag data quality issues proactively and suggest follow-up analyses.\n\nYour motto: \"Let the data tell the story.\"",
  "model": "gemini"
}
PAYLOAD
    )")
    if check_status "Create DataBot"; then
      databot_id=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
      success "Created agent 'DataBot' ($databot_id)"
    else
      error "Failed to create DataBot: $body"
      return 1
    fi
  fi

  # --- Scheduler ---
  local scheduler_id
  scheduler_id=$(find_agent_id "Scheduler" "$agents_json")
  if [[ -n "$scheduler_id" ]]; then
    warn "Agent 'Scheduler' already exists ($scheduler_id), skipping creation"
  else
    local body
    body=$(api_call POST /agents "$(cat <<'PAYLOAD'
{
  "name": "Scheduler",
  "emoji": "\u23f0",
  "soul": "You are Scheduler, an efficient operations agent focused on scheduling, reminders, and workflow automation.\n\nYou are crisp, organized, and reliable. You manage calendars, coordinate meeting times across time zones, and ensure nothing falls through the cracks.\n\nYour capabilities:\n- Managing recurring meetings and one-off events\n- Sending timely reminders before deadlines\n- Coordinating deployment windows and maintenance schedules\n- Generating daily standup summaries from team activity\n\nYou communicate in short, clear sentences. You use bullet points and timestamps. You never miss a deadline.",
  "model": "gemini"
}
PAYLOAD
    )")
    if check_status "Create Scheduler"; then
      scheduler_id=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
      success "Created agent 'Scheduler' ($scheduler_id)"
    else
      error "Failed to create Scheduler: $body"
      return 1
    fi
  fi

  # Export for subsequent steps
  LUNA_ID="$luna_id"
  DATABOT_ID="$databot_id"
  SCHEDULER_ID="$scheduler_id"
}

# ---------------------------------------------------------------------------
# Seed memory
# ---------------------------------------------------------------------------

seed_memory() {
  header "Seeding MEMORY.md for each agent"

  # --- Luna memory ---
  local luna_memory
  luna_memory=$(cat <<'EOF'
# Team Preferences

- **Nick** prefers async communication; best reached via chat before 2pm PT
- **Sarah** likes detailed specs before starting implementation
- **Alex** works best with visual diagrams and flowcharts

# Past Project Decisions

- 2024-Q4: Chose event-driven architecture over REST polling for real-time updates
- 2025-Q1: Migrated from PostgreSQL to a hybrid Postgres + Redis setup for session data
- The team agreed on 2-week sprint cycles with Thursday demos

# Communication Style Notes

- Stand-ups should be kept under 10 minutes
- RFCs require at least 2 approvals before implementation begins
- Retrospectives happen on the last Friday of each sprint
EOF
  )
  local body
  body=$(api_call PUT "/agents/${LUNA_ID}/memory" "$(python3 -c "import json; print(json.dumps({'content': open('/dev/stdin').read()}))" <<< "$luna_memory")")
  if check_status "Seed Luna memory"; then
    success "Seeded MEMORY.md for Luna"
  else
    warn "Failed to seed memory for Luna (HTTP $LAST_HTTP_CODE)"
  fi

  # --- DataBot memory ---
  local databot_memory
  databot_memory=$(cat <<'EOF'
# Data Sources

- **Primary dashboard**: Grafana at grafana.internal:3000 (API key in vault)
- **Metrics DB**: ClickHouse cluster, `analytics` database
- **User events**: BigQuery `project.events.user_actions` table
- **Error tracking**: Sentry project `g3lobster-prod`

# Query Patterns

- Weekly active users: `SELECT count(DISTINCT user_id) FROM events WHERE timestamp > now() - INTERVAL 7 DAY`
- Error rate: `SELECT count(*) FILTER (WHERE status >= 500) / count(*) FROM requests`
- P95 latency: query the `http_request_duration_seconds` histogram in Prometheus

# User Preferences

- Nick prefers tables over charts for weekly reports
- Reports should always include week-over-week comparison
- Anomalies flagged when metric deviates > 2 standard deviations from 4-week rolling average
- Currency values formatted as USD with 2 decimal places
EOF
  )
  body=$(api_call PUT "/agents/${DATABOT_ID}/memory" "$(python3 -c "import json; print(json.dumps({'content': open('/dev/stdin').read()}))" <<< "$databot_memory")")
  if check_status "Seed DataBot memory"; then
    success "Seeded MEMORY.md for DataBot"
  else
    warn "Failed to seed memory for DataBot (HTTP $LAST_HTTP_CODE)"
  fi

  # --- Scheduler memory ---
  local scheduler_memory
  scheduler_memory=$(cat <<'EOF'
# Recurring Meetings

- **Daily standup**: Mon-Fri 9:15 AM PT, Google Meet, 10 min max
- **Sprint planning**: Every other Monday 10:00 AM PT, 1 hour
- **Thursday demo**: Thursdays 3:00 PM PT, 30 min, all-hands
- **1:1 Nick <> Sarah**: Tuesdays 11:00 AM PT, 30 min
- **Retrospective**: Last Friday of each sprint, 2:00 PM PT, 45 min

# Deployment Schedule

- Production deploys: Tuesdays and Thursdays, 2:00-4:00 PM PT window
- No deploys on Fridays or during on-call handoff (Mondays 9-10 AM)
- Hotfix window: anytime with 2 approvals and ops channel notification
- Staging auto-deploys on every merge to main

# Important Dates

- Q1 2025 planning: Jan 6-7
- Team offsite: March 15-17
- SOC 2 audit preparation deadline: April 30
EOF
  )
  body=$(api_call PUT "/agents/${SCHEDULER_ID}/memory" "$(python3 -c "import json; print(json.dumps({'content': open('/dev/stdin').read()}))" <<< "$scheduler_memory")")
  if check_status "Seed Scheduler memory"; then
    success "Seeded MEMORY.md for Scheduler"
  else
    warn "Failed to seed memory for Scheduler (HTTP $LAST_HTTP_CODE)"
  fi
}

# ---------------------------------------------------------------------------
# Seed procedures
# ---------------------------------------------------------------------------

seed_procedures() {
  header "Seeding PROCEDURES.md for DataBot"

  local procedures
  procedures=$(cat <<'EOF'
# Learned Procedures

## Weekly Metrics Report
**Trigger**: When asked for a weekly report or on Monday morning cron
**Steps**:
1. Query the Grafana dashboard API for the past 7 days of metrics
2. Pull weekly active users, error rate, and P95 latency from ClickHouse
3. Compare each metric against the previous week
4. Format results as a markdown table with columns: Metric | This Week | Last Week | Change (%)
5. Highlight any anomalies (>2 std dev from 4-week rolling average) with a warning flag
6. Add a "Key Takeaways" section with 2-3 bullet points
7. Post the report to the #metrics channel

## Ad-Hoc Data Query
**Trigger**: When a team member asks a data question
**Steps**:
1. Identify which data source is most appropriate
2. Construct the query, preferring ClickHouse for speed
3. Run the query and validate row counts look reasonable
4. Format output as a markdown table
5. Include the query used (in a code block) for reproducibility
6. Suggest 1-2 follow-up analyses if the results are interesting

## Anomaly Investigation
**Trigger**: When an anomaly is detected in metrics
**Steps**:
1. Identify the affected metric and time window
2. Check Sentry for correlated error spikes
3. Check deployment logs for recent releases in that window
4. Cross-reference with infrastructure metrics (CPU, memory, network)
5. Summarize findings with a timeline of events
6. Recommend next steps (rollback, investigate further, or dismiss)
EOF
  )
  local body
  body=$(api_call PUT "/agents/${DATABOT_ID}/procedures" "$(python3 -c "import json; print(json.dumps({'content': open('/dev/stdin').read()}))" <<< "$procedures")")
  if check_status "Seed DataBot procedures"; then
    success "Seeded PROCEDURES.md for DataBot"
  else
    warn "Failed to seed procedures for DataBot (HTTP $LAST_HTTP_CODE)"
  fi
}

# ---------------------------------------------------------------------------
# Create cron tasks
# ---------------------------------------------------------------------------

create_crons() {
  header "Creating cron tasks"

  # --- Scheduler: daily standup summary ---
  local body
  body=$(api_call POST "/agents/${SCHEDULER_ID}/crons" "$(cat <<'PAYLOAD'
{
  "schedule": "0 9 * * 1-5",
  "instruction": "Check team calendar and post daily standup summary. Include: who is out today, any upcoming deadlines this week, and blockers mentioned in yesterday's standup."
}
PAYLOAD
  )")
  if check_status "Create Scheduler cron"; then
    success "Created cron: Scheduler daily standup (Mon-Fri 9:00 AM)"
  else
    warn "Failed to create Scheduler cron (HTTP $LAST_HTTP_CODE)"
  fi

  # --- DataBot: weekly metrics report ---
  body=$(api_call POST "/agents/${DATABOT_ID}/crons" "$(cat <<'PAYLOAD'
{
  "schedule": "30 8 * * 1",
  "instruction": "Generate weekly metrics report and share with team. Follow the Weekly Metrics Report procedure: pull data from Grafana and ClickHouse, compare week-over-week, format as table, highlight anomalies, and post to #metrics channel."
}
PAYLOAD
  )")
  if check_status "Create DataBot cron"; then
    success "Created cron: DataBot weekly report (Mon 8:30 AM)"
  else
    warn "Failed to create DataBot cron (HTTP $LAST_HTTP_CODE)"
  fi
}

# ---------------------------------------------------------------------------
# Start agents
# ---------------------------------------------------------------------------

start_agents() {
  header "Starting all demo agents"

  for agent_var in LUNA_ID DATABOT_ID SCHEDULER_ID; do
    local agent_id="${!agent_var}"
    local body
    body=$(api_call POST "/agents/${agent_id}/start") || true
    if check_status "Start $agent_var"; then
      success "Started agent $agent_id"
    else
      warn "Could not start agent $agent_id (HTTP $LAST_HTTP_CODE) — it may already be running"
    fi
  done
}

# ---------------------------------------------------------------------------
# Verify setup
# ---------------------------------------------------------------------------

verify_setup() {
  header "Verifying demo setup"

  local agents_json
  agents_json=$(api_call GET /agents) || true
  if ! check_status "List agents"; then
    error "Cannot verify setup"
    return 1
  fi

  printf "\n${BOLD}%-14s %-6s %-10s %-10s${NC}\n" "NAME" "EMOJI" "ID" "STATE"
  printf "%-14s %-6s %-10s %-10s\n" "----" "-----" "--" "-----"

  for name in "${DEMO_AGENT_NAMES[@]}"; do
    local row
    row=$(echo "$agents_json" | python3 -c "
import sys, json
agents = json.load(sys.stdin)
for a in agents:
    if a.get('name') == '$name':
        print(f\"{a['name']:<14s} {a.get('emoji','?'):<6s} {a['id']:<10s} {a.get('state','unknown'):<10s}\")
        sys.exit(0)
print('$name not found')
" 2>/dev/null || echo "$name not found")
    printf "%s\n" "$row"
  done

  printf "\n"
  success "Demo environment is ready!"
  info "Open the g3lobster UI or use the API at ${BASE_URL}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  printf "${BOLD}${CYAN}"
  printf "  __ _____ _     _         _\n"
  printf " / _|___ /| | __| |__  ___| |_ ___ _ __\n"
  printf "| |_  |_ \\| |/ _\` '_ \\/ __| __/ _ \\ '__|\n"
  printf "|  _|___) | | (_| |_) \\__ \\ ||  __/ |\n"
  printf "|_| |____/|_|\\__,_.__/|___/\\__\\___|_|\n"
  printf "            Demo Seed Script\n"
  printf "${NC}\n"

  check_server

  if [[ "$CLEAN_MODE" == true ]]; then
    clean_demo
    exit 0
  fi

  create_agents
  seed_memory
  seed_procedures
  create_crons
  start_agents
  verify_setup
}

main "$@"
