# Agent Platform: Build Plan

Implementation update: agents now support persistent, version-pinned chat sessions with reusable files, per-turn traces and immutable downloads. Evals and hill climbing remain one-shot; chat feedback is saved for future review only. Use [DEMO_QUICKSTART.md](DEMO_QUICKSTART.md) for the current presentation, rather than this original build schedule.

Brainbase on-site build. Five hours total, including implementation, evaluation, and rehearsal. This is a revised scope for an active build window; rebase the milestones against time remaining.

> **Pitch:** Create and deploy agents that run code and work with files. Inspect every run, turn real feedback into regression cases, and improve the agent in bounded batches before publishing a new version.

## 1. Product decision

The user is an engineer or ops lead building an internal agent for a team. Their first questions are: Can I make it do my job? What happened when it ran? Can I correct it once so future users benefit?

The main product loop is:

**Create → deploy → observe → correct → evaluate a candidate → promote → observe again.**

Observability and production feedback are core requirements. Improvement is automatic candidate generation and evaluation, with explicit human promotion. Changes persist as versioned configuration; this is prompt/playbook optimization, not weight training.

Cost and latency remain visible and bounded. The implemented extension adds generated code tests, rubric judging, and bounded model/turn-limit search. The demo favors eight cases and two candidates; GPU hosting and broad model sweeps remain outside the live walkthrough.

## 2. Minimum deliverable

| Capability | Must work in the demo |
|---|---|
| Create | Name, instructions, fixed tool set, saved immutable version, and a trial run |
| Execute | Agent reads files, writes and executes code in a sandbox, and returns downloadable artifacts |
| Deploy | Stable shareable run page and API pointing to a version |
| Observe | Persistent run history and an inspectable model/tool timeline for every run |
| Correct | Attach feedback and an expected result to a deployed run |
| Improve | Batch new evidence, propose a playbook revision, run a regression comparison |
| Publish | Review the diff and evidence, promote or reject, and roll back |

One Python script-building agent demonstrates the platform. The runtime is general-purpose: arbitrary instructions, prompts, files, and code/file tools. Evaluators support Python hidden tests, structured file outputs, and optional rubric judging. Production corrections can supply Python tests as future regression cases.

## 3. Architecture and stack

```text
Builder / shared run page / run history / candidate comparison
                         |
                    FastAPI API
                  /             \
          SQLite + artifacts    bounded job runner
                                  |          |
                            agent runtime   improvement job
                             /       \       |
                       model API    sandbox  candidate version
                                       |     |
                                  output files
                                       |
                               trusted evaluator

All runs -> application trace events -> SQLite -> history + trace viewer
New feedback/checker outcomes -> evidence inbox -> improvement batches
```

- **Backend:** Python, FastAPI, SQLite. Store artifact bytes in a local directory and metadata in SQLite. Use WAL mode and short transactions.
- **Frontend:** Vite + React. One agent detail page with Build, Runs, Improve, and Deploy views; reuse components for the shared run page.
- **Agent loop:** OpenAI Agents SDK, with a pinned release and an explicit model adapter. Prefer the Responses adapter for OpenAI and smoke-test OpenRouter's Responses endpoint for the selected tool-capable models. Keep Chat Completions as a compatibility adapter when a selected model/provider requires it, rather than a platform-wide requirement. If only a direct provider key is available, use that provider rather than adding a billing dependency.
- **Sandbox:** Docker with a pinned image and fresh workspace per run. Check availability immediately. If unavailable, choose a working hosted sandbox early. A host subprocess in a temporary folder is not an equivalent isolation fallback.
- **Jobs:** One bounded in-process worker pool for runs and evaluations. Persist job state; a restart marks unfinished work interrupted rather than pretending jobs survived. No Redis/Celery requirement in this build.
- **Observability:** Application-owned structured events persisted locally. SDK hooks and wrapped tools/model calls feed this event schema; no external observability product is required.

### One runtime contract

Playground, deployment, and evaluation all call the same execution service:

```text
execute(version_snapshot, prompt, input_artifacts, limits) -> run_id
```

Resolve the deployment pointer once when accepting a run and store the immutable version ID. Promotion never changes an in-flight run. The caller receives a run ID immediately, then polls or subscribes to progress.

Evaluation attaches a checker result to a normal run; it does not use a separate agent harness with different tools or prompts.

## 4. Agent versions, workspaces, and deployment

An agent is a named logical resource, not a long-lived container. A version contains:

- Base instructions, bounded learned playbook, model ID, supported model settings, enabled tools, and execution limits.
- Parent version, author (`user` or `improver`), change explanation, and source improvement batch.
- Runtime/tool schema version and sandbox image identifier for reproducibility.

Freeze base instructions, tools, checker, and batch budgets during optimization. Model choice and turn limit may change within the allowed search space. The improver may add, replace, or remove playbook rules within a size limit. Manual edits also create versions. Do not endlessly append memory or let the improver change the checker or budgets.

Tools: `bash`, `read_file`, `write_file`, and `list_files`; add `edit_file` only if needed. All tools execute in the run's sandbox. Bound runtime, tool output, artifact size, turns, and model tokens. A tool timeout terminates the command/process group; a run deadline terminates the sandbox. Do not mount host secrets, the Docker socket, or project source into the agent workspace. Use network-disabled sandboxes for the demo's preinstalled dependencies.

A deployment is a stable slug pointing to a version. Promotion is an atomic pointer update with recorded old/new version IDs. Record rollback the same way.

### Minimal API

- `POST /agents`, `POST /agents/{id}/versions`: create agent/version.
- `POST /deployments/{slug}/runs`: authenticated API, prompt and optional files; returns `202` with run ID and status URL.
- `GET /runs/{id}`, `GET /runs/{id}/events?after_seq=...`, artifact download route.
- `POST /runs/{id}/feedback`: rating, correction, optional expected artifact.
- `POST /agents/{id}/improvements`: run the same batch job on demand.
- Promote/reject candidate and deployment rollback routes.
- `/d/{slug}`: shareable file-and-prompt run page. A deployment-scoped invocation token proves access by someone besides the creator. Keep creator history and feedback controls separate from shared invocation access.

No conversational session memory is required. The deployed experience is a clear input → execution → output flow.

## 5. Minimal observability: core, not stretch

### Run history

Show runs for the selected agent, newest first. Each row includes:

- Time, source (`playground`, `deployment`, `eval`), deployment/version, and prompt preview.
- Execution status, elapsed time, model cost, and tool-call count.
- Separate outcome (`passed`, `failed`, `ungraded`) and feedback indicator.

A run completing successfully does not establish correctness. Production runs are ungraded unless a checker or reviewed correction provides an outcome. Thumbs-up is user feedback, not a verified test pass.

### Trace viewer

Selecting a run opens an ordered timeline with expandable events:

| Event | Recorded fields |
|---|---|
| Run start/end | Version, input artifact references, limits, status, error category, total duration |
| Model call start/end | Call ID, model, start/end times, visible response/tool requests, token usage, provider generation ID, cost when available |
| Tool call start/end | Call ID, tool name, arguments, bounded stdout/stderr or file-operation result, exit code, duration |
| Artifact produced | Name, size, content hash, preview/download reference |
| Evaluation / feedback | Checker version and result, failure summary, user correction reference |

Store `run_id`, per-run sequence, event type, timestamp, call/parent ID where relevant, and a JSON payload. Pair start/end events so stalled calls are visible. Show only model-visible response content and tool activity; do not depend on private reasoning access.

Persist events as execution happens. Poll events after the last sequence number for the first implementation; add SSE only if useful. Refreshing the page must preserve history and the trace. Missing usage/cost is displayed as unknown; token-based estimates must be labeled. Redact credentials and cap payloads before storing them. Save inputs and outputs needed to replay the run as artifacts rather than unbounded event blobs.

For latency and cost, aggregate recorded calls and distinguish completed duration from elapsed time on running jobs. Minimal summary: run count, execution failure count, labeled pass count/denominator, and cost. Do not display an accuracy percentage over unlabeled production traffic.

### Capture strategy

The application event log is the canonical product record. Tool wrappers and runtime/model hooks write to it. If using SDK trace processors, replace the default export destination while keeping trace generation enabled. Alternatively disable SDK tracing and emit application events independently. Disabling SDK tracing globally and expecting a custom SDK trace processor to receive spans is not a valid combination.

Smoke-test persistence with an actual model call and tool call; then refresh and verify both still appear. A tracing failure must be surfaced, not silently produce an empty successful-looking timeline.

## 6. How traces feed continuous improvement

**Continuously collect; improve in bounded batches.** Start with event-triggered batching, not a daemon repeatedly optimizing every request.

### Evidence inbox

Every deployed run can enter an inbox with its trace and artifacts. Select evidence by:

1. Reviewed correction plus an expected output or trusted checker: eligible regression case.
2. Existing checker failure: eligible regression case, if the failure concerns agent behavior.
3. Negative feedback without a checkable expectation: needs labeling.
4. Provider failure, rate limit, sandbox outage: operational diagnosis; not automatically an instruction change.

Unlabeled traces help explain a failure but do not become ground truth. An LLM may draft a case or diagnose patterns; the user reviews the expectation before it gates a candidate. Treat trace contents as untrusted task data when constructing the improver's instructions.

### Trigger and limits

- **Manual:** "Improve now" snapshots eligible evidence and starts one batch. This is also the live-demo control.
- **Automatic:** enable a per-agent toggle. On a new eligible case, enqueue a batch when at least **three new eligible cases** have accumulated since the last batch and a short cooldown has elapsed.
- Recheck eligibility when feedback/checker events arrive and when a batch finishes. If a cooldown blocks an otherwise-ready batch, schedule a single delayed eligibility check in the app process so evidence does not wait forever for another request. No recurring external scheduler is required.
- Allow one active batch and one unresolved candidate per agent. New evidence stays pending while either exists; never launch overlapping climbs or overwrite a pending candidate.
- Only deployment/playground evidence enters this trigger. Evaluation and improver runs are visible in history but do not recursively trigger new batches.
- Deduplicate source runs/corrections. Claim a fixed evidence snapshot transactionally. Mark it processed only when a batch reaches a terminal result; retain links to rejected/no-gain batches. Interrupted/error batches release claims for an explicit retry within budget.
- Each batch has limits for model spend, agent runs, wall-clock time, and one candidate. Persist usage and stop with a reason when a limit is reached. Retry a failed batch explicitly rather than launching an endless automatic retry loop.

The threshold and cooldown are simple configuration, not a scheduling product. Automatic candidate creation/evaluation is core; publishing remains explicit. For the demo, three distinct labeled runs prove the automatic trigger, while "Improve now" makes a smaller batch possible.

### Batch lifecycle

```text
queued -> analyzing -> proposing -> evaluating
       -> awaiting_review | no_gain | budget_exceeded | error | interrupted
awaiting_review -> promoted | rejected | stale
```

A batch records its parent version, source run/feedback IDs, suite snapshot, candidate, evaluation results, spend, and explanation. If manual edits or promotion change the current parent while it runs, mark the candidate stale and require a new comparison against the current version before promotion.

### What hill climbing does

1. Snapshot the current deployed version, new labeled evidence, and regression suite revision.
2. Evaluate the parent on that same suite when a matching baseline is unavailable.
3. Give the improver selected training traces, reviewed corrections, current playbook, and budget context. Keep hidden validation cases and checker implementation out of its input.
4. Propose one concise playbook revision with a hypothesis and source evidence links.
5. Create an immutable candidate and evaluate it using fresh workspaces and conversations.
6. Compare paired parent/candidate outcomes. Keep the parent if the candidate fails the gate.
7. If the gate passes, show the diff and comparison for promotion. Subsequent deployment runs use the promoted version and generate new evidence.

This is hill climbing because a measured candidate must improve on its parent. It may return `no_gain`; do not force a change or call every proposal an improvement. Later batches start from the promoted version. Keep successful cases in the regression suite so lessons accumulate and regressions remain visible.

## 7. Evaluation and demo domain

### Demo agent

A data-transformation agent accepts CSV/JSON files and a task, writes and executes code, and returns a declared output file. Use explicit policies for dates, duplicate handling, currency units, and missing values. Learning a newly supplied business rule is valid adaptation; do not claim the model should have guessed an unspecified policy.

Example: the agent mishandles duplicate customer records. The user supplies the intended rule, "retain the record with the newest timestamp." The platform persists that rule and tests other identifiers, row orders, timestamps, and no-duplicate inputs. Describe this as incorporating a business correction. For a pure execution-error example, include the rule in the task from the start.

### Suite

Start with **8–12 small development cases** and **about four untouched final cases**. Each has input files, prompt, expected structured output, comparison rules, tags, and a revision/content hash.

- Validate reference outputs and ensure representative incorrect outputs fail.
- Training cases and their feedback may inform the improver. Keep a few development validation cases out of its prompt while selecting candidates; repeated selection can still overfit them.
- Final cases are used once for the final parent/candidate report and never for candidate selection or recurring deployment gates.
- When feedback changes the intended policy, update and review the affected suite expectations explicitly. Compare both versions under the same resulting suite and user inputs.

### Trusted grading boundary

Stop the agent sandbox and collect only declared, size-limited regular output files; reject unsafe paths and symlinks. Parse output as data using trusted evaluator code outside the agent container. Compare CSV/JSON semantically with explicit rules for ordering, numeric tolerance, and schema. Keep expected outputs inaccessible to the agent.

Do not copy hidden tests into the agent's already-modified environment and trust its pytest exit code. If executable solution grading is added later, use a fresh restricted execution environment and keep authoritative scoring outside the generated process. Never import generated code into the API process.

### Candidate gate and measurements

- Require a strict increase in passed development cases, no loss of cases the parent passed, and compliance with configured cost/latency budgets. An equally accurate cheaper candidate is a later extension.
- Report a per-case comparison: fixed, regressed, unchanged pass, unchanged fail, and infrastructure error. An infrastructure error makes the comparison incomplete and ineligible for promotion until rerun; it is not evidence of a prompt regression.
- Begin with one trial per version/case and concurrency around two. Repeat the final comparison if time permits. Show counts and describe the evidence as preliminary, not statistically significant.
- Persist distinct trial IDs. A repeat must perform a fresh execution. Result reuse keys cover agent config, task revision, checker revision, runtime/image, and trial identity. Do not cache solely by config/task and accidentally reuse all repeated trials.
- Runtime limits enforce per-run bounds. Report observed latency and cost separately from aggregate averages; p50 is not a hard latency guarantee.

A 12-case parent/candidate comparison is 24 agent runs, plus proposal and final cases. At 30 seconds per agent run and concurrency two, execution alone is about six minutes. Measure real timings early and reduce the suite if needed. Persist results from development; the live walkthrough runs a bounded cycle and clearly labels historical results.

## 8. Storage model

Use these logical records; prefer JSON fields to adding infrastructure:

- `agents`: identity, description, automatic-improvement config.
- `agent_versions`: immutable config, parent, provenance, runtime/image version.
- `deployments`, `deployment_changes`: current pointer, invocation-key hash, promotion/rollback history.
- `runs`: resolved version, source, input references, execution status/error, timing, usage and cost with `actual|estimated|unknown` provenance.
- `trace_events`: run ID, sequence, call/parent IDs, event type, timestamp, bounded payload.
- `artifacts`: run, role (`input|output|expected`), filename, storage path, size/hash; expected artifacts stay out of runtime input manifests.
- `feedback`: source run, rating/correction, labeling status, expected-artifact references, revision.
- `eval_cases`, `suite_revisions`: immutable case contents/checker config and a fixed list of case revisions for each suite snapshot.
- `eval_results`: evaluated run, case revision, comparison/batch, trial ID, checker revision, outcome and failure summary.
- `improvement_batches`: trigger, parent/candidate IDs, suite revision, evidence references/claims, lifecycle, budgets/usage, rationale, error.

Keep run execution status separate from feedback and checker outcomes. A single `runs.passed` boolean cannot represent an ungraded production run, a disputed correction, and several evaluation trials correctly.

## 9. UI and acceptance checks

### Build and deploy

Create an agent, try it with a file, save its version, publish a slug, and invoke it through the shared page or curl. The user sees output files and a trace link. The API never exposes the creator's model credentials.

### Runs and trace

Filter history by version, source, execution status, and feedback. Open a run to inspect model/tool events, errors, costs, artifacts, and submit a correction. Refresh must preserve the view. Demonstrate one failed tool call and its visible error.

### Improve

Show new evidence count, cases needing labels, automatic batching toggle, "Improve now," and the current batch stage. A candidate comparison shows:

- Source corrections and trace links.
- Playbook diff and proposed reason.
- Fixed/regressed/unchanged counts with per-case drilldown.
- Parent/candidate cost, latency, suite revision, and trial counts.
- Gate result and promote/reject controls; stale candidates cannot be promoted.

The user can follow one correction from its production trace to a tested version and then to a subsequent deployed run. This is the primary UX acceptance test.

## 10. Build order and time allocation

Fit this order to the time remaining. Reserve the last 30 minutes for evaluation completion, rehearsal, and fixes.

| Original elapsed target | Work | Exit criterion |
|---|---|---|
| 0:00–0:45 | One model, SDK, Docker, tools, application event capture | Real file/code run returns an artifact and persisted trace |
| 0:45–1:45 | Agent/version storage, job/status routes, minimal builder, shared deployment page | Another caller invokes the same runtime; history and trace survive refresh |
| 1:45–2:30 | Feedback form, evidence inbox, trusted output comparison, small suite | Correction becomes a checkable case; baseline measured |
| 2:30–3:30 | One-candidate improver, regression gate, manual batch, comparison/promotion | One complete observe → correct → improve → deploy loop |
| 3:30–4:15 | Automatic threshold trigger, cooldown, deduplication, bounds | Three new labeled cases trigger one batch; eval traffic cannot retrigger it |
| 4:15–4:30 | Finish evaluations, final cases, rollback and failure checks | Honest comparison and working rollback |
| 4:30–5:00 | Rehearse twice, fix, record limitations and dependencies | Reproducible live walkthrough |

If behind: reduce suite size and trace UI styling; use polling instead of SSE; remove optional tool/settings controls. Keep durable traces, feedback, one bounded improvement batch, and deployment. If automatic triggering cannot be completed, disclose that collection is continuous and batching is manual; do not present manual-only behavior as automatic.

## 11. Walkthrough: approximately 20 minutes

1. **User and scope, 2 min:** team agent with inspectable execution and corrections that benefit future users.
2. **Create/deploy, 3 min:** instructions, code/file tools, stable URL/API; invoke as a caller.
3. **Observe, 4 min:** real artifact, model/tool timeline, one failure, cost/latency, persisted history.
4. **Correct/batch, 3 min:** expected output, evidence inbox, automatic threshold and "Improve now" using the same job.
5. **Improve, 4 min:** live bounded proposal/evaluation, diff, paired results, honest historical/fresh distinction. Start the job early enough to overlap explanation with execution.
6. **Promote, 2 min:** publish the candidate, rerun through the unchanged URL on fresh input, show rollback.
7. **Trade-offs, 2 min:** small sample, isolation limits, process-local workers, next week.

An unfavorable candidate is shown honestly as rejected. Rehearse with real saved runs to establish that the chosen workload offers a plausible improvement opportunity; do not weaken a working agent or falsify results to manufacture a success.

## 12. Integration notes and verification status

Only `PLAN.md` existed at review time. SDK compatibility, keys, Docker, model IDs, event capture, timings, and candidate gains still need live verification.

- Pin one available tool-capable model and its supported settings. Do not assume illustrative model IDs exist or that every model accepts temperature/reasoning settings.
- Non-OpenAI provider setup uses the SDK adapter surface; exact configuration is release-specific. [Official OpenAI provider documentation](https://developers.openai.com/api/docs/guides/agents/models).
- Chat Completions remains supported; OpenAI recommends Responses for new projects. [Official migration guidance](https://developers.openai.com/api/docs/guides/migrate-to-responses). OpenRouter also supports Responses across multiple models, so multi-model support does not require Chat Completions. Its Responses endpoint is stateless: send conversation history, use `store: false`, and omit `previous_response_id`. Verify SDK tool-call continuation and reasoning-item handling with the chosen models before claiming compatibility. [OpenRouter Responses documentation](https://openrouter.ai/docs/api_reference/responses/overview).
- SDK tracing captures model and tool activity, but product history is our local event log. [Official OpenAI tracing documentation](https://developers.openai.com/api/docs/guides/agents/integrations-observability).
- OpenRouter documents automatic usage accounting including `usage.cost`. Capture the provider response's actual charge when exposed, or retain generation IDs for later lookup. If the SDK hides cost, label token-price estimates rather than presenting them as billed amounts. [OpenRouter usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).
- Start with non-streaming model calls; the UI can still show tool/model event progress. Token streaming and provider-specific delta handling are optional.
- Catch model/provider errors and persist a terminal run status and trace error. Separate unsupported settings, timeouts, and infrastructure failures from evaluated task failures.

**Pulled in:** agent SDK, model provider, Docker, FastAPI, SQLite, React/Vite.

**Written here:** sandbox tool adapters, application event log and viewer, version/deployment lifecycle, feedback-to-case workflow, trusted artifact comparison, bounded batch improver, candidate comparison, promotion and rollback.

## 13. Explicit cuts, limitations, and next week

### Cut from five-hour scope

Multi-model search, reward-exchange-rate UI, Pareto charts, unrestricted prompt/model/tool optimization, LLM judges, fine-tuning, GPU hosting, chat sessions, multi-agent orchestration, full tenancy/RBAC, durable distributed workers, and automatic promotion.

### Honest limitations

Small evaluation suites provide preliminary evidence and can be overfit. Feedback can be incomplete or wrong. New business rules can conflict with old ones and require suite review. Longer playbooks can add latency and contradict instructions. Docker reduces exposure but is not a complete security boundary for hostile multi-tenant workloads. Local jobs/cooldown checks do not survive restarts; startup reconciles interrupted state, and the next event or manual action can resume eligibility checks. Local disk and SQLite suffice for a demo, not a multi-host platform.

### Another week

Move work to a durable queue with cancellation, retries, leases, tenant budgets, and persisted scheduling. Add Postgres and object storage, access controls/retention for traces, and stronger sandbox infrastructure. Improve evidence labeling, feedback clustering, recurring validation sets, and larger untouched audits. Add cost-aware candidate selection and model comparison after the quality loop is dependable. Consider an open model served on GPUs only after the required product loop is complete.

### Questions for the interviewers

Which supplied key/provider and spending limit should we use? Is Docker available? Does a shared invocation page plus API satisfy deployment, or must the service be externally hosted? Do you have a representative internal workload or correction we can use to make the feedback loop realistic?
