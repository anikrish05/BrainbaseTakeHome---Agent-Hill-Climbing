# CLAUDE.md: Project briefing and design log

This file records the design conversation behind this repository: what was built, how it works, the decisions made and why, where the plan changed, real measurements, and known weaknesses. Claude Code loads it automatically as project context. Read it before changing anything.

## Working agreements

- **Codex owns code changes.** Claude's role in the latest phase was explaining and reviewing, not editing. Coordinate before touching code; two agents editing the same files at once can silently overwrite each other.
- **No long-running background jobs** without the user's go-ahead. A long eval batch once blocked the user's other work and had to be stopped.
- **Never restart the server during a batch.** On startup the server marks queued and running work as interrupted.
- **Never commit `.env`**, `.data/`, or anything with API keys. `.env.example` holds only empty placeholders.
- **Batch limits** in `.env` must fit the suite. A 35-case suite with 3 candidates needs about 176 runs. Suggested: `BATCH_MAX_RUNS=250`, `BATCH_MAX_COST_USD=5`, `BATCH_TIMEOUT_SECONDS=2400`, `EVAL_CONCURRENCY=6`.

## The brief

A five-hour on-site build for Brainbase: a platform where users **create** general-purpose agents (they can run and write code, view and edit files), **deploy** them for use by someone or something else, and **improve** them over time. Optional: run agents on an open-source model hosted on GPUs. Deliverables: working software demoed live, at least one agent, a demo of it improving, and a ~20 minute walkthrough. The brief says to go deep rather than wide.

## The product bet

The user is an engineer or ops lead standing up internal agents. The deep part is **automatic hill climbing**: the platform builds its own verifiable eval suite, measures the agent, and a learner proposes better versions. Model choice and cost are first-class levers. Human annotation is one input, not the engine.

## How the system works

### Architecture

- **One FastAPI process** (`backend/app/main.py`) serves the API, the built React UI, the agent harness, and background jobs. Docker containers and OpenRouter are the only things outside it.
- **Frontend:** React 19 + Vite single-page app (`frontend/src/main.jsx`, `frontend/src/styles.css`). Built to `frontend/dist` and served by FastAPI. Not Next.js. Almost everything is in one large file; splitting it per view is a known cleanup.
- **Agent harness** (`backend/app/runtime.py`): the OpenAI Agents SDK loop, calling models through OpenRouter. Tools: `bash`, `read_file`, `write_file`, `list_files`. The loop and API keys stay in the server process.
- **Sandbox** (`backend/app/sandbox.py`): a fresh Docker container per run from a plain `python:3.12-slim` image. No network, read-only root, all capabilities dropped, nonroot user, 512 MB memory, 1 CPU, 64 processes. Only the run's workspace is mounted. Tools run inside via `docker exec`. Outputs are collected from `outputs/` after the container stops. Containers share the host kernel, so this is not VM isolation.
- **Storage** (`backend/app/store.py`): SQLite with a JSON records table and an ordered trace-events table, plus files on disk under `.data/`.
- **Queue:** there is no real queue. Each run is an in-memory asyncio task waiting on a semaphore: 2 slots for deployed and playground runs, 6 for eval runs. Queued work is lost on restart, there is no cap or rate limit, and it cannot scale past one process.

### Agents, versions, deployments

- A **version** is an immutable config: instructions (user), playbook (learner), model (chosen at creation, then the learner), turn limit and derived time limit (learner), fixed tools, sandbox image, harness version.
- Users edit only instructions. The **model and turn limit are owned by the learner**; the website shows them read-only. At creation the user picks a baseline model from every tool-capable OpenRouter model (about 265, with prices).
- A **deployment** is a name, a hashed key, and a pointer to one version. Promotion and rollback move the pointer and keep the URL and key. Only an explicit rotate changes the key.
- **API use:** `POST /api/deployments/{slug}/runs` with `X-Deployment-Key`, then poll `GET /api/runs/{id}?token=...` and download artifacts. Codex added chat sessions: `POST /api/deployments/{slug}/sessions`, then `POST .../sessions/{id}/runs` per message. Runs are asynchronous; there is no streaming or webhook. The server listens on localhost only.
- **Chat history** was requested by the user for the agent itself; evals and hill climbing stay one-shot. Codex implemented sessions after Claude stopped editing.

### Evals

- **Case types:** code tests (agent writes Python to `outputs/`, hidden `test_*` functions with plain `assert` grade it), expected file (semantic CSV/JSON comparison), judge rubric (yes/no criteria graded by an LLM judge for text code cannot check).
- **Generation:** a strong model plans categories from the agent's description, then drafts about 5 cases per category in parallel (40 by default). Each case is kept only if its **reference solution passes all its tests** and an **empty solution fails them**. Near-duplicates are dropped. Splits: about 60% train, 20% validation, 20% held out.
- **Splits:** the learner sees failed training cases. Validation cases are scored every batch but hidden from the learner. Held-out cases are used once, in the final audit, and never influence a decision.
- **Grading of code tests:** a second fresh container holds the case's inputs, the agent's `outputs/` files, the hidden tests, and a runner. Each test gets 5 seconds; the whole run 30 seconds. Score = tests passed / total, so partial progress counts.
- **Trust caveat:** the agent's code is imported into the grader's process, so code written to cheat could forge results. Fine for honest agents; the fix is running each test in an isolated child process.

### Hill climbing (one batch)

1. **Score the parent:** the current version runs every train and validation case once.
2. **Propose:** the learner (one structured call, default `anthropic/claude-sonnet-5.5`) receives base instructions, playbook, model, turn limit, the parent's score and cost, failures grouped by signature, up to 12 failed training cases with hidden tests, grader messages and trace tails, any human corrections, the allowed models with prices, and the turn range 4 to 40. It returns 3 candidates; each may rewrite the playbook (short rules, never code), switch model, and change the turn limit.
3. **Score candidates:** each runs every train and validation case once. A candidate whose new model fails at the provider is disqualified without aborting the batch.
4. **Pick a finalist:** the strongest by reward among candidates that pass the gate.
5. **Confirm the finalist:** it and the parent run every validation case 2 more times; every attempt counts. Predictable cost: validation cases x 2 versions x 2 runs.
6. **Gate on averages:** zero regressions (a case score more than 0.1 below the parent's), plus either a fixed case or the same score at 25% lower cost.
7. **Promote:** wait for review, or deploy immediately with auto-promote. If the finalist fails confirmation the batch ends as no gain; it does not fall back to a runner-up.

- **Reward:** `score - cost_weight x $ per run - latency_weight x seconds per run`. Default cost weight 1 (one cent per task costs one point).
- **Failure handling:** an agent that runs out of turns or time scores zero on that case. Only sandbox, provider, or judge outages abort a comparison.
- **Triggers:** "Improve now", eval generation finishing with auto-improve on, or three new human corrections plus a cooldown.
- **Platform models:** generator and learner default to Sonnet 5.5 (strong reasoning, reliable structured output, small share of batch cost). Judge defaults to `google/gemini-3-flash-preview`, a different family to limit self-preference. Override with `GENERATOR_MODEL`, `IMPROVER_MODEL`, `JUDGE_MODEL`.

## Decision log

How the plan evolved, including where the user pushed back.

1. **First plan:** Claude proposed a hand-written agent loop calling Claude directly. **User pushed back:** use OpenRouter to swap models freely, use an agent framework (OpenAI Agents SDK) instead of a custom loop, and build a reward that weighs accuracy against cost with user-set weights.
2. **SDK compatibility:** verified from docs that the SDK works with OpenRouter for tool-capable models; tracing to OpenAI must be disabled; cost comes from OpenRouter usage data. Later verified live: runs, tool calls, structured output, and actual cost capture all work through OpenRouter's Responses API.
3. **Another agent's plan** shifted to human-feedback batches with a strict gate and cut the reward and model sweep. Claude flagged it: rigged demo (the correction handed the answer to version 2), no cost in decisions, single runs, batches aborting on agent failures, rollback rotating keys. Rollback was fixed; the rest led to the redesign below.
4. **User pushed back:** hill climbing must be **automatic**, driven by verifiable checks and an LLM judge. Human annotation becomes one input.
5. **Implemented:** agent failures graded as failures, separate eval worker pool, learner sees suite failures, 3 candidates, cost-aware reward, auto-promote, LLM judge, eval generation at creation.
6. **User pushed back on eval size:** 8 generated cases (3 survived in the first real test) is not enough to climb. Raised to 40 with category planning, parallel drafting, and oracle and do-nothing validation. Added code-test cases with partial credit.
7. **User decided the learner owns model and turn limit**, then asked for a baseline picker covering all of OpenRouter, since only a couple of models were offered. Implemented both; the learner sees the full catalog.
8. **Difficulty profiling** (repeat runs to tag cases easy, learnable, or hard, inspired by a previous project's adaptive sampling idea) was built, then **removed at the user's request**.
9. **Confirmation reruns:** Claude first reran every case where a candidate and parent disagreed. **User shared a review preferring finalist-only confirmation on validation cases** for a predictable budget and an easier defense. Adopted. Agreement on one run does not prove reliability.
10. **User asked to stop long background jobs**, then to stop code changes entirely so Codex could own the code.
11. **Chat history** was initially treated as a reasonable cut; **the user required it** for the agent itself.

## Measured results

- **First end-to-end test** (`openai/gpt-4.1-mini`): an agent run completed with 4 tool calls and actual cost captured (about $0.003). Generation kept 3 of 4 cases. A 9-run batch cost about $0.06 and graded two out-of-turn failures as failures instead of crashing.
- **Deployment test:** wrong or missing keys rejected; a real run completed in about 8 seconds for $0.0013; output downloaded correctly; redeploy kept the key; rotate invalidated the old one.
- **Python Script Builder agent:** 42 validated cases (26 train, 9 validation, 7 held out), mostly code tests, generation about $1.85. Before profiling was removed, about 12 cases passed sometimes and 6 never, so the suite has room to climb.
- **Batch timing:** agent runs took a median of about 11 seconds, about 13 seconds of which is model time and under 1 second tools. The learner switched two candidates to cheaper models (DeepSeek V3.2, Qwen3 Coder 30B) that timed out on most cases at about 210 seconds each, stretching the batch past 30 minutes. That batch was stopped manually and marked interrupted.
- **Bugs found by real runs and fixed:** an output-token cap truncating generation; commas in category names breaking Docker mounts; container start timeouts under load; the learner pasting full programs into the playbook.

## Known weaknesses

- The learner is a single call, not an agent: it cannot open more traces or test a rule before proposing it, and it picks models by price alone.
- No early stopping of clearly losing candidates and no smoke test for model switches.
- Eval suites are still small; two or three runs per case is preliminary evidence. The same model family writes tests and proposes fixes.
- Grading is not tamper-proof against code written to cheat. The judge is uncalibrated.
- Truncated context sent to the learner was silently cut in places (partly fixed with cut markers).
- In-memory queue, single process, localhost only, open admin routes, no rate limits, SQLite.
- No versions timeline in the UI; the only full version list is a rollback dropdown on the Deploy tab.

## With another week

1. **Learner as an agent:** tools to read traces, diff passing and failing runs, check model speed, and trial a rule on training cases; memory of past candidates; smoke-test model switches.
2. **Rigorous measurement:** about 100 cases with fresh cases each round, adaptive repeat runs, early stopping, score ranges, different families for generator and learner.
3. **Trustworthy grading:** per-test process isolation, judge calibration against human labels, human approval of generated cases.
4. **Close the loop:** chat history, automatic grading of production runs, an annotation queue that turns fixed traces into eval cases, multi-turn evals with simulated users.
5. **Infrastructure:** durable queue with separate workers, Postgres and object storage, pre-started microVM sandboxes, rate limits, accounts, budgets.
6. **Product:** versions timeline, eval explorer, cost dashboards, split the frontend, support text and action agents.
7. **Still skip:** fine-tuning and self-hosted GPU models.

## Glossary

- **Case:** one eval: a prompt, input files, and its checks.
- **Playbook:** learned rules appended to the system prompt, rewritten only by the learner.
- **Batch:** one hill-climbing step.
- **Finalist:** the strongest candidate, confirmed with repeated validation runs before promotion.
- **Score:** mean fraction of hidden tests passed across cases.
