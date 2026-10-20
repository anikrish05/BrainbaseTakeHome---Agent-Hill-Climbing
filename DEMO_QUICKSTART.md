# Demo runbook — use this one workspace

**Open exactly:** http://127.0.0.1:8001/?agent=agt_32f9710c363d493d

This is the Python Script Builder with the saved real improvement comparison and the new chat UI. Start from Build. Use the tabs inside this workspace; you do not need any other manually entered URL. The Deploy tab opens its authenticated shared page for you.

The server is already running. If it is stopped, run `./demo.sh` from the project directory. Keep Docker Desktop running and the OpenRouter key in `.env`. Do not run a second copy of the server when the link already works.

## Before presenting

- Keep this page open and confirm “Runtime ready.”
- Keep `.data/demo-walkthrough-20261020/demo-inputs/sales.csv` available for the upload picker. It contains two rows with amounts 10 and 15.
- Click Build → New chat to start a clean conversation, or select the completed Cedar conversation in Conversation history as a fallback.
- Keep the completed comparison in Improve available. Its two batch records are real model executions, not simulated demo metrics.
- Avoid generating a fresh large suite or repeatedly starting batches during the presentation. Generation previously took six minutes. A new model run has variable latency; use the saved conversation if a call stalls.

## Exact 20-minute sequence

### 0–2 minutes: explain the product and show configuration

“My user is an internal builder who creates a code-capable agent, deploys it for teammates, inspects its runs, and improves it using executable evidence.”

Show the base instructions, current model, turn limit, learned playbook, and four tools. Base instructions define the job; a tested learned playbook captures improvements.

Optional live creation: New agent → name and instructions → **uncheck Generate starter evals** → Create. This avoids starting expensive background generation. Return to Python Script Builder in the sidebar for the prepared comparison.

### 2–6 minutes: prove chat history and file continuity

Build → New chat. Attach `sales.csv` using **browse**. Paste:

```
Our project is Cedar. Read sales.csv and create outputs/report.py with a
standard-library function summarize(rows) returning {'count': len(rows),
 'total': sum(float(row['amount']) for row in rows)}.
Do not add a CLI yet. Verify the function using the uploaded rows,
and keep your reply brief.
```

Wait for Completed and show the `report.py` download. Send a follow-up in the **same chat**, without attaching the CSV again:

```
Add a CLI to that script. It should accept an input CSV path and a --pretty
flag, print the summary as JSON, and keep the existing summarize function.
Read the existing file and actually edit it. Run it on the CSV I attached
earlier and assert the JSON output has count 2 and total 25; exit code zero
alone is insufficient. Mention our project name and keep your reply brief.
```

Expected behavior: the agent edits the existing script, runs it on the earlier file, and reports count 2 / total 25. It should remember Cedar. The first download remains the original file; the second is the revised file.

Click **View code** on either `report.py` card to inspect that turn's actual file inline with line numbers. **Copy code** copies the displayed snapshot; **Download** keeps the full file available. The same viewer is available in run details and deployed chat.

Refresh the page. Both messages and replies should still be there. Conversation history resumes prior chats. New chat starts a separate empty workspace.

Say: “Each conversation pins its starting version. Promotion changes new chats; existing chats keep consistent behavior.”

### 6–9 minutes: show observability

Click View trace on a turn, or open Runs & traces and select that run.

In Runs & traces, use the activity filter to separate chats, one-shot requests, baseline evaluations, candidate evaluations and holdout audits. Filter to **Batch 2**, then **Validation cases** and **Candidate evaluations** to see the finalist confirmation attempts. Each row names its case, version and attempt; Checks passed/failed is separate from execution status. The detail header links back to the owning batch. Full task text is under **Task prompt**.

Show the conversation-context event, model calls, tool arguments/results, actual provider usage/cost, status and file downloads. Each chat turn is an ordinary persisted run. The bounded conversational transcript carries user messages and final replies; full tool details remain in the trace. Files live in the conversation workspace, and every turn uses a fresh isolated container.

Do not call an ungraded production run a verified success merely because it completed. Our separate executable checks validate the scripted examples.

### 9–12 minutes: show executable evals

Open Evals. Expand `config_merge_cli` or `slugify_helpers`, then inspect the task, hidden tests, and reference solution. Explain the train/validation/holdout split.

“We generated these before the demo. A reference solution must pass its checks, and an empty solution must fail. That checks consistency and nontriviality; humans still need to review whether generated assertions match the intended task.”

Evals continue to use independent fresh workspaces and one-shot execution. They do not reuse chat files or messages.

### 12–16 minutes: demonstrate measured hill climbing

Open Improve. Select the **Promoted batch beginning batch_9b**.

Show the parent and both candidate rows, then the playbook diff and per-case trace links:

- Same eight development cases: parent **39.06%**, finalist **95.83%** mean test score.
- Six cases improved, zero material per-case regressions.
- The winner kept GPT-4.1 mini, added learned rules, and raised the turn limit from 12 to 20.
- GPT-4.1 was also tested; it regressed cases and did not win. A larger model is not automatically better.
- Twenty-eight trials took 157.5 seconds and recorded about $0.56 of batch charges.
- Parent and finalist each ran the validation case three times. The finalist failed one attempt; confirmation exposed that variability.
- The earlier batch beginning batch_7f had no gain and was correctly rejected.

Say: “This is configuration/playbook optimization, not model-weight training. Only failed training tasks and reviewed one-shot corrections inform the improver; validation content and holdout content are withheld.”

Show the holdout honestly: **both versions failed the single reserved case**. The parent had CSV newline errors; the candidate exhausted its turns. This is evidence of policy adaptation and development improvements, not proof of general reliability.

### 16–18 minutes: show deployed chat

Open Deploy → the displayed shared run page. It uses the same version through a stable URL/API. Do not rotate the access key during the demo.

Start New chat on the shared page. First message:

```
Our project is Cedar. Create outputs/greet.py with greet(name) returning
'Hello, ' + name + '!'. Verify it and keep your reply brief.
```

Follow-up:

```
Add a command-line interface to that script, with one positional name.
Run it for Ada, and mention our project name in your reply.
```

The follow-up should reuse the script and remember Cedar. The owner can inspect the same deployed conversation and each turn's trace in the workspace.

### 18–20 minutes: explain improvement inputs and cuts

The saved example used three distinct one-shot deployed requests with reviewed Python regression checks. The third label triggered exactly one automatic batch. The fresh post-promotion script passed 3/3 independent checks and its reviewed request remains pending (1/3 labels) for a future batch.

Open Runs & traces and select the completed task beginning `Create outputs/customer_email_tools.py` (run beginning `run_b3a8`). Show its saved correction and regression check. Then return to Improve to show the pending evidence count. This demonstrates the recorded feedback path without spending presentation time launching another batch. To try a new independent task later, Build has a collapsed **One-shot test for evaluation feedback** composer; its run detail lets you add a correction plus Python tests or an expected CSV/JSON file.

**Chat feedback is different today:** it is saved for review, but does not automatically become a one-shot regression case. A follow-up such as “add a CLI” depends on earlier messages and files. Conversation-aware replay, annotation and hill climbing are the next step. Do not claim that chat transcripts already train the optimizer.

Close with the practical cuts: trusted localhost administration, in-process jobs, bounded history, small noisy eval suites, generated checks needing semantic review, and Docker isolation that is not a complete hostile multi-tenant boundary.

## Key features to name

1. General-purpose code/file agents using OpenAI Agents SDK and configurable model adapters.
2. Persistent conversations, resumable history, uploaded files and script edits across turns.
3. Version-pinned chats, immutable artifact snapshots, stable deployment URLs and API invocation keys.
4. Per-turn model/tool traces, timing, usage, errors and downloadable files.
5. Generated executable Python evals, structured-file checks, optional rubric judging and a separate holdout report.
6. Bounded one-shot hill climbing, regression/reward gates, finalist confirmation, review/promotion and automatic evidence batching.
7. Explicit separation between chat history and the current one-shot evaluation/improvement pipeline.

## Verification

The automated suite passes **116 tests, with 1 skipped**. The frontend production build passes. Coverage includes text/file continuity, restart recovery, immutable artifacts, version pinning, independent workspaces, overlapping-turn rejection, deployment authorization/key rotation, bounded text previews, preview authorization and keeping one-shot evals separate.

Live OpenRouter checks used GPT-4.1 mini, actual UI uploads, builder chat and deployed chat. The original owner conversation survived a server restart between turns. A clean two-turn rehearsal with the exact prompts above took 5.8 and 9.0 seconds. Independent Docker checks passed 4/4 for that report script (function, JSON CLI, pretty CLI and side-effect-free import), and 2/2 for the deployed greeting script (function and CLI). Both pages restore their conversations after refresh. The original report artifact remains unchanged. The latest Cedar conversation in the history dropdown is the clean successful rehearsal; the older conversation preserves the initial failure and correction. Results are saved in `.data/demo-walkthrough-20261020/chat-verification.json`.

One initial follow-up falsely claimed a CLI edit without changing the file. Its trace and output exposed the error; a corrective message produced a verified script. Chat instructions now explicitly require reading/editing the file and checking stdout. This reduces that failure mode but cannot guarantee model correctness. Use the explicit assertions in the prompt above and keep the saved conversation as a fallback.

Earlier real improvement results are documented in DEMO_WALKTHROUGH.md and `.data/demo-walkthrough-20261020/verification.json`. Do not regenerate those results during the short demo.
