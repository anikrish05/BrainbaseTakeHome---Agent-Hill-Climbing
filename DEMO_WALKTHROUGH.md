# Python Script Builder: demo and review

**Current presentation instructions:** follow [DEMO_QUICKSTART.md](DEMO_QUICKSTART.md). Use http://127.0.0.1:8001/?agent=agt_32f9710c363d493d for the demo. Persistent chat has since been implemented; evals and hill climbing remain one-shot. The old long-running batch has been stopped. The records below document the earlier live verification and saved improvement evidence; the timing table is historical.

## What the platform improves

The optimizer creates immutable versions with a revised playbook, optionally a different allowed model and turn limit. It does not train model weights. Every comparison executes actual agent code in fresh Docker sandboxes. Scores come from Python tests, semantic CSV/JSON checks, or an optional model judge. The score for a code case is the fraction of its test functions that passed; the suite score weights cases equally.

The execution model reads the task and input files, never that case's hidden tests. The improver sees failed training tasks, tests, and traces. Validation tasks and tests are withheld from the improver. Holdout cases are excluded from selection and assessed separately once.

A generated reference passing its own tests proves internal consistency. It does not establish independent ground truth. Inspect the prompt, assertions, and reference in Evals before trusting the numbers.

## A 20-minute walkthrough

| Minutes | Show | Explain |
| --- | --- | --- |
| 0–2 | Create agent with general Python instructions and 8 starter cases | User defines the job. Start generation early. |
| 2–5 | Build, tools, version config, shared deployment | Agents can write/run code and return scripts; every run freezes a version. |
| 5–8 | Generated Evals: expand one task, its tests, and reference | Reference execution and empty-solution probes reject invalid/trivial checks. Train/validation/holdout have different roles. |
| 8–12 | Improve: baseline, proposals, candidate scores and trace links | Two candidates are evaluated on the same development suite. Repeated validation confirms the finalist; every attempt counts. |
| 12–14 | Review and promote, then run a fresh task at the shared URL | Promotion updates the stable deployment pointer. Already accepted requests keep their original version. |
| 14–17 | Find deployed run in Runs & traces; attach a correction and Python tests | Requests alone are observations. A checkable correction becomes a reusable training case with provenance. |
| 17–20 | Show the next batch and its new evidence; discuss cuts | Three new reviewed requests trigger a batch, or Improve now runs it immediately. Show a saved completed comparison if live work is still running. |

Model latency is variable. Rehearse the flow and keep one real completed comparison available. Do not manufacture a gain or weaken the regression gate if the model already passes a suite. A no-gain result is valid; introduce a genuine new user requirement and measure adaptation separately.

## General Python agent instructions

```
You build reusable Python scripts using the standard library. Follow the requested
filenames, function signatures, command-line interfaces, and data contracts. Read
the inputs, write the scripts, and exercise them before finishing. Save every
requested deliverable under outputs/ so the user can download it. Explain what
you created.
```

Baseline: `openai/gpt-4.1-mini`, 12 turns. The walkthrough narrows candidate model choices to GPT-4.1 mini and GPT-4.1. The platform otherwise supports a wider catalog; a catalog listing is not proof that every model/provider combination supports the selected transport correctly.

## Production teaching example

Ask the shared agent to create `outputs/email_tools.py` with `normalize_emails(values)` for onboarding lists. Then introduce an explicit team policy in feedback:

> Our email identity policy: trim and casefold addresses; remove +tags only for example.com; retain tags at other domains; discard blank values; deduplicate while preserving the first occurrence. Apply this default to email cleanup tasks unless the request says otherwise.

Attach Python checks such as:

```python
def test_team_alias_policy():
    from email_tools import normalize_emails
    assert normalize_emails([
        " Ada+sales@EXAMPLE.COM ", "ada@example.com",
        "Bo+sales@other.org", "", "   "
    ]) == ["ada@example.com", "bo+sales@other.org"]

def test_empty_and_order():
    from email_tools import normalize_emails
    assert normalize_emails([]) == []
    assert normalize_emails(["z@example.com", "a@example.com", "z+tag@example.com"]) == ["z@example.com", "a@example.com"]
```

This is learning a newly supplied business requirement, not accusing the original agent of failing to guess an unstated rule. The new case retains the original request and files, correction, feedback ID and trace; it joins the training split in the next batch. Negative feedback without a check remains `needs_label`. Python feedback checks are exercised in a subsequent comparison; saving a check is not a verified pass on the existing output.

## Budgets and timing

Eight accepted cases normally split 5 train / 2 validation / 1 holdout. Seven development cases × (parent + 2 candidates) = 21 first-pass runs. Two extra trials on each validation case for parent and finalist add 8, for at most 29 development runs. The final holdout comparison adds two independent runs. Fewer accepted cases change those counts.

The isolated verification server permits 64 batch runs, 900 seconds, and $2 of observed known charges. This leaves room for new production regressions. The main server permits 250 runs, 2400 seconds, $5 and 2 candidates for larger offline experiments. Admission reserves confirmation trials before making any model calls. Dollar checks occur between calls and cannot guarantee a precise spend ceiling with unknown/in-flight charges.

Production and evaluation have separate concurrency pools (2 and 6). Increasing a run limit permits more work; it does not reduce runtime. Small-suite generation drafts two cases per parallel category to reduce the longest response. A strong generator can still take minutes. Generate a larger suite before the live walkthrough.

## Code review: strengths and remaining limits

Good decisions: one execution path for playground, deployment and eval; immutable versions; trace and artifact persistence; deterministic checks before rubric judging; distinct training/validation/holdout roles; explicit promotion and stable deployment URLs; bounded batches; evaluation traffic cannot become recursive production feedback.

Corrected during review: .env reload overwriting process settings; startup recovery touching a separate CLI worker; Python regression feedback unavailable in the UI/API; confirmation silently skipped at the run cap; quality improvements qualifying despite lower reward; repeated validation biasing cost averages; duplicate rubric criteria accepted by the judge; holdout cap too small for seven cases; judge spend omitted from audit budget; stale UI copy and oversized default suite. Explicit requested Python scripts misplaced in the workspace root are published with a trace event; unrelated temporary scripts and inputs stay excluded.

Remaining cuts: creator endpoints are trusted localhost endpoints without user authentication; jobs are in-process without worker leases; model/provider support is validated per combination; generated labels need semantic review; Python tests share a process with imported solution code inside the grading container and are not a tamper-proof grader. Runtime behavior can change across releases, so traces record the actual runtime version. No weights, GPU deployment, production multitenancy, or statistical proof of reliability is claimed.

## Recorded live verification — 2026-10-20

All results below used real OpenRouter calls and Docker execution.

| Check | Observed result |
| --- | --- |
| Site-created agent and generated suite | 6 accepted of 8 requested; generation took 360.3 seconds and recorded $0.489 |
| Initial automatically generated comparison | No gain; a fix accompanied a regression, so the gate rejected it |
| New production policy | Three distinct deployed requests were annotated with executable Python checks |
| Trigger | Two labels did not start another batch; the third started exactly one automatic batch |
| Second comparison on the same 8 development cases | Parent 39.06% → finalist 95.83%; 6 improved, 0 material regressions |
| Confirmation | Parent and finalist each ran the one validation case three times total; finalist failed once |
| Work and spend | 28 trials, 157.5 seconds, $0.563 recorded batch cost |
| Winner | GPT-4.1 mini retained; learned playbook added; turn limit 12 → 20 |
| Untouched holdout | Both failed the one reserved case; parent had CSV line-ending errors, finalist exhausted turns |
| Promotion | Same deployment URL and invocation key; new runs use version v4 |
| Fresh deployed request | Different module filename and unseen addresses; 3/3 independent checks passed; 10.3 seconds, about $0.003 |
| Browser download | Downloaded customer_email_tools.py matches the persisted artifact hash |
| Future improvement | Fresh reviewed deployed request is eligible; inbox shows 1/3 pending labels |

The first and second batches used different suites because the second added three policy cases. Compare parent and finalist **within the second batch**, never the initial 77.5% against the later 95.8%. The holdout does not establish generalization: neither version passed it. This is a convincing example of measured business-policy adaptation and several code-contract fixes, with remaining reliability problems visible. Keep the failed holdout visible in the demo.

The observed six-minute generation and variable long-tail scripts make a fully fresh loop risky in a short presentation. Use the saved real comparison for the central explanation, show a fresh deployed request live, and run fresh generation or a new bounded batch while discussing architecture. Do not make the entire walkthrough wait on a provider response. The two-case parallel generation configuration was added afterward; its timing has not been benchmarked live.

Detailed evidence: `.data/demo-walkthrough-20261020/verification.json` and `post-promotion-verification.json`. The earlier review database is preserved in `.data/live-review-20261020`; port 8001 now serves the new walkthrough database.

Final checks: 102 tests passed, 1 skipped; frontend production build passed. Both servers were restarted with the reviewed code and returned healthy/ready. The promoted version, 28 completed comparison runs, holdout report, and pending evidence survived the restart. The comparison table wraps to fit the normal compact browser window.

Cost limitation: batch run/time/spend settings apply to improvement batches, not eval generation. Generation is bounded by requested case count and per-call deadlines, but has no separate spend stop. Its displayed cost is the sum of reported successful-call charges; failed or unknown provider charges can be missing. Budget generation separately for larger suites.
