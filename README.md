# Brainbase Agent Platform

A local agent platform with persistent chats and workspaces, model/tool traces, stable deployments, and feedback-driven improvement batches.

For the prepared Python Script Builder presentation, follow [DEMO_QUICKSTART.md](DEMO_QUICKSTART.md). It identifies the single canonical demo workspace and exact prompts; `./demo.sh` starts that saved workspace.

## Agent conversations

Build and shared deployment pages support multi-turn chat. Conversations pin their starting version, retain workspace files, and resume from SQLite after a refresh or server restart. Each turn uses a fresh Docker container and has its own persisted trace and immutable artifact downloads. A session accepts one active turn at a time. New chats use the current draft or deployed version and start separate workspaces.

Output cards offer inline text/code previews with line numbers, copy and download. Previews read the saved artifact snapshot, use the same deployment authorization as downloads, and display up to 128 KB of UTF-8 text. They never execute generated code or render it as HTML.

The model receives up to 12 recent user/final-answer pairs within a 24,000-character budget, including attached/output filenames. Individual messages are truncated; full tool calls remain in traces. Sessions stop at 50 turns. Older files persist even when their messages leave the context window.

Evals and hill climbing remain one-shot. Chat notes are saved for review and excluded from automatic regression-case creation. Conversation-aware replay and optimization are future work.

The session API creates a conversation with `POST /api/agents/{agent_id}/sessions` or `POST /api/deployments/{slug}/sessions`, sends turns to the corresponding `/sessions/{session_id}/runs` route, and resumes with `GET /sessions/{session_id}` under the same prefix. Shared routes require the existing deployment key. The one-shot `/runs` routes remain available.

## Run locally

Requirements: Python 3.11+, Node.js 20+, Docker Desktop, and an OpenRouter or OpenAI API key.

```sh
cp .env.example .env
# Add OPENROUTER_API_KEY to .env, or use MODEL_PROVIDER=openai + OPENAI_API_KEY.
# Set DEFAULT_MODEL to a tool-capable model available to your account if needed.
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
docker pull python:3.12-slim
cd frontend
npm install
npm run build
cd ..
./run.sh
```

Open [the local platform](http://127.0.0.1:8000). Start Docker Desktop first. The status banner distinguishes missing credentials from an unavailable sandbox. Keys saved to `.env` become available on the next API request. Restart the server to change an already-loaded setting.

For frontend development, run the API on port 8000 and `npm run dev` in `frontend/`; Vite proxies `/api` to the API.

## Demo the complete loop

1. Load the customer-export demo. This creates an agent and eight evaluation definitions; it creates **no simulated runs or metrics**.
2. Inspect the instructions and run the provided customer CSV. Follow its model/tool timeline and download `clean.csv`.
3. Deploy a stable slug. Open its shared run URL or use the displayed invocation key with the API.
4. Add corrective feedback with the expected `clean.csv`. The demo introduces an explicit new policy: newest customer record per normalized email, with deterministic tie/order rules. This is business-policy adaptation, not a claim that the initial agent should have guessed the policy.
5. Start an improvement batch. The improver sees the parent's failed training cases (verifier message plus trace) and any reviewed corrections, and proposes several candidate playbooks. Every candidate runs the same suite in fresh sandboxes; the finalist and parent rerun every validation case twice. Validation and holdout cases are never shown to the improver.
6. Inspect the candidate table, playbook diff, fixed/regressed counts, traces, and observed costs. A candidate qualifies with no material case regression, a higher measured reward, and either a fixed case or the same score at 25% lower cost. The best qualifying candidate by reward is chosen. With auto-promote on it deploys automatically; otherwise it waits for review.
7. Promote the candidate. Invoke the same deployment URL on fresh input. Roll back by deploying a previous immutable version.
8. Enable automatic batching: three distinct new reviewed run corrections trigger one bounded batch. Pending candidates block overlapping work. Evaluation traffic cannot recursively trigger improvement.

A negative rating without an expected output or regression tests is an item needing a label, not automatically a verified failure. Provider/sandbox errors are operational failures, not prompt-training targets. Untouched holdout definitions are excluded from improvement batches.

## Automatic hill climbing

Human annotation is one input, not the engine. The loop can run without anyone in it.

**Eval generation.** "Generate evals" (or the option when creating an agent) asks a strong model to plan categories for the agent's job, then drafts up to 5 cases per category in parallel, 8 total by default. Each case picks the most objective check:

- **Code tests.** The agent writes Python into `outputs/`. Hidden `test_*` functions run against it in a fresh, network-disabled sandbox. The score is the fraction of tests passed, so partial progress counts.
- **Expected file.** The agent writes a `.csv` or `.json` file that is compared semantically to the reference output.
- **Judge rubric.** Yes/no criteria graded by an LLM judge, for text that code cannot check.

A generated case is kept only if its reference solution passes all of its tests and an empty solution fails them. Near-duplicates are dropped. Kept cases are split about 60% train, 20% validation, 20% held out.

**One batch.** The current version runs every train and validation case. The improver sees the failed training cases, grouped by failure signature, along with the current score, cost, and allowed models. It proposes two candidates by default. Each may rewrite the playbook, switch to any tool-capable OpenRouter model (about 265, with prices; `CLIMB_MODELS` narrows the list), or change the turn limit (4 to 40). Users choose the baseline model; a candidate whose new model fails at the provider is disqualified without aborting the batch. Every candidate runs every case once, and the strongest by reward becomes the finalist. The finalist and the parent then run each validation case two more times, every attempt counts, and the gate is re-checked on the averages, so a single lucky run cannot promote a candidate. With 9 validation cases that adds a predictable 36 runs. Two or three runs are still preliminary evidence, not proof of reliability. A candidate qualifies with no material per-case regression (a score drop greater than 0.099), a higher reward, and either a fixed case or the same score at 25% lower cost. Missing cost blocks reward-based promotion when cost has a nonzero weight. The winner has the highest reward:

```
reward = score − cost weight × $ per run − latency weight × seconds per run
```

With auto-promote on, the winner deploys immediately.

**Failure handling.** An agent that runs out of turns or time scores zero on that case. Only sandbox, provider, or judge outages abort a comparison.

## API example

```sh
curl -X POST http://127.0.0.1:8000/api/deployments/YOUR_SLUG/runs \
  -H 'Content-Type: application/json' \
  -H 'X-Deployment-Key: YOUR_KEY' \
  -d '{"prompt":"Create a JSON file with the first ten prime numbers.","files":[]}'
```

The response contains a run ID. Poll `/api/runs/RUN_ID?token=YOUR_KEY` for status, events, and artifacts. The shared URL contains an invocation credential; treat that URL as a secret. Publishing, promotion, and rollback preserve the invocation key. An explicit `rotate_key: true` on the deploy endpoint creates a new key and invalidates previous links. Interactive API documentation is at `/docs`.

## Architecture and ownership

- **Pulled in:** OpenAI Agents SDK (Python), OpenAI/OpenRouter model APIs, Docker, FastAPI, SQLite, React and Vite.
- **Written here:** workspace tool adapters and isolation limits, persistent application events and trace UI, artifact collection, immutable versions, deployment/promotion/rollback, feedback inbox, semantic CSV/JSON grading, bounded improvement jobs, and candidate comparisons.
- **Model adapters:** Responses is preferred. `MODEL_TRANSPORT=chat_completions` is available for a specific compatibility need. Model capabilities must be tested rather than inferred from API compatibility. `SUPPORTED_MODELS` is a comma-separated allowlist for the picker; `IMPROVER_MODEL` optionally chooses a stronger proposer within the configured provider.
- **One execution path:** playground, deployment and evaluation call the same runtime. Each run freezes its version. User code executes only in a fresh, nonroot, network-disabled Docker container with a read-only root filesystem and resource limits. Only the run's workspace (or its conversation's persistent workspace) is mounted; model keys stay in the API process.
- **Trusted evaluation:** stopped-container outputs are collected as regular, size-limited files. CSV and JSON are parsed as data in trusted server code. Nothing generated by an agent is imported into the server. CSV schema and row order are significant; JSON object key order is ignored and array order is significant. Numeric tolerance is 1e-6, with identifiers preserved as text.
- **Trace organization:** history is grouped by conversation or comparison batch, with search and filters for purpose, batch, case split and execution status. Baseline/candidate labels are scoped to that batch; validation confirmation and holdout audit runs are distinct. New evaluation runs snapshot their case name, split, phase and attempt so later case edits do not rewrite history. Older records derive labels from saved relationships.
- **Observability:** events include paired model/tool starts and ends, visible output/tool arguments, results/errors, usage, artifacts, feedback and evaluations. Provider costs are actual only when returned; missing costs remain unknown. Private reasoning is not collected.

Persistent local state is in `.data/` (SQLite plus artifacts). `.env`, `.data`, virtual environments and dependency/build directories are ignored. Stop the server before deleting `.data/` to reset the demo.

## Verification

```sh
.venv/bin/python -m pytest -q
cd frontend && npm run build
```

Tests use explicit deterministic fixtures/mocks where a provider is unavailable; those fixtures never create production demo runs. Real sandbox/model smoke-test results should be reported separately from these tests.

## Limits and five-hour cuts

This is a **localhost demonstration**, not a public multi-tenant service. Creator/admin endpoints are trusted local endpoints without user authentication; do not bind this server publicly. Deployment-scoped invocation keys protect shared calls but are not organization/RBAC infrastructure.

Jobs and cooldown timers are process-local. The database persists state; restarting marks unfinished work interrupted, and an explicit retry is required after batch errors or exhausted limits. No distributed queue, scheduling service, fine-tuning, GPU deployment, or model sweep is claimed. Each batch is one hill-climbing step with one trial per case plus finalist/parent confirmation on validation cases; small suites remain noisy, and generated cases are only as correct as their reference solutions.

Batch limits cap trials, turns/tokens and wall-clock duration. The dollar stop condition uses observed known charges between calls; unknown provider charges mean an exact dollar ceiling cannot be guaranteed. Set the provider account spending cap too. Small suites provide preliminary evidence, not statistical significance. Repeated validation can overfit. Docker is not a complete hostile multi-tenant security boundary; workspace disk quotas and durable container cleanup need production infrastructure.

With another week: authenticated teams and trace retention/access controls, durable workers with leases/cancellation, object storage/Postgres, stronger sandboxes, larger independent audits, better feedback labeling, and cost-aware model search.

See [PLAN.md](PLAN.md) for the product decisions and build scope.

## Python-script demo and local worker coordination

See [DEMO_WALKTHROUGH.md](DEMO_WALKTHROUGH.md) for the tested Python-agent flow and a 20-minute presentation sequence. Production feedback accepts `test_code` containing Python `test_*` functions, alongside the correction. This creates a training case linked to the original production request, files, feedback, and trace. Tests execute only inside Docker during evaluation; a thumbs-down alone is not a correctness label.

Batch admission reserves all configured finalist confirmation trials before starting. The small demo uses 8 cases, 2 candidates, and 2 extra parent/finalist runs on each validation case: normally at most 29 development trials, plus 2 holdout trials. More runs increase the possible work; they do not make a batch faster. The isolated verification instance uses a 64-run, 15-minute, $2 observed-cost limit; the main instance permits larger offline batches (250 runs, 40 minutes, $5).

Ordinary startup recovers unfinished jobs as interrupted. If a separate CLI worker is actively using the same SQLite database, start the UI/API with `RECOVER_JOBS_ON_START=0` to avoid recovering that worker's live jobs. This temporary local coordination flag does not provide worker leases or distributed ownership. Do not launch overlapping work for the same agent from independent processes.

Python hidden tests run in a separate grading container but import delivered code into the grader's process. They measure cooperative script correctness; they are not resistant to a deliberately malicious solution attacking the checker. Generated reference/test agreement is useful validation, not independent proof that the labels match the intended task. Inspect the tests and reference solutions before relying on a suite.
