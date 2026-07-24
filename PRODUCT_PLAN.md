# Jira Backlog Groomer — Product Plan and Cross-PC Handoff

- Last updated: 24 July 2026
- Repository: https://github.com/ryan12324/jira-backlog-groomer
- Current product version: `0.2.0`
- License: MIT

## 1. Purpose of this document

This document preserves the product intent, technical decisions, delivery state, operating model,
and next actions from the original working session. It is designed to let a contributor clone the
repository on another computer and continue without needing the original chat history.

Do not put Jira credentials, OpenAI keys, production plans, inventory exports, or private backlog
content into this document or the public repository.

## 2. Product summary

Jira Backlog Groomer is a review-first command-line tool for turning a poor Jira Cloud backlog into
a coherent, cross-functional Agile backlog.

The initial target backlog contains roughly 900 mixed Jira work items:

- user stories and generic issues;
- epics;
- subtasks, including frontend-only and backend-only fragments;
- bugs;
- duplicates, stale work, superseded requests, and weakly connected dependencies.

The frontend and backend team work from one shared backlog. The tool must therefore favor vertical
user outcomes over layer-specific tickets.

The tool reads Jira, asks an OpenAI model for typed grooming recommendations, applies deterministic
safety policy, and writes a human-reviewable plan. Jira is changed only by a separate, explicitly
approved command.

## 3. Product vision

Create a backlog in which every committed item:

- describes a clear user, customer, operational, or business outcome;
- is understandable by product, frontend, backend, QA, design, data, and operations;
- has observable and testable acceptance criteria;
- is small enough to estimate and deliver;
- exposes dependencies and duplicate relationships;
- preserves uncertainty as open questions instead of invented requirements;
- can be prioritized using a transparent value/urgency/risk/size model;
- retains an auditable connection to the original Jira content.

The product should accelerate human backlog ownership, not replace it.

## 4. Primary users

### Product owner or product manager

Needs to identify value, remove obsolete work, clarify outcomes, approve vertical splits, and own
the final backlog.

### Engineering lead

Needs one delivery backlog, visible dependencies, realistic technical considerations, and no
automatic destruction of useful context.

### Cross-functional delivery team

Needs stories that can be discussed, estimated, built, tested, demonstrated, released, and
observed as one outcome.

### Jira or site administrator

Needs least-privilege operation, bounded write volume, clear audit state, and separate control of
high-risk actions such as archiving.

## 5. Product principles

1. **Plan before mutation.** AI analysis never writes to Jira.
2. **Humans approve external changes.** Applying requires explicit configuration gates and the
   exact plan run ID.
3. **The model proposes; code constrains.** Issue references, archive eligibility, action counts,
   timestamps, and idempotency are validated deterministically.
4. **Prefer vertical slices.** Do not create separate frontend and backend stories for one outcome.
5. **Do not invent product facts.** Missing information becomes an open question.
6. **Preserve history.** Rewrites retain original notes by default and the JSON plan stores source
   snapshots.
7. **Treat work-item types differently.** Epics, bugs, stories, and subtasks are not interchangeable.
8. **Process large backlogs in waves.** A 900-item mutation plan is not reviewable or safe.
9. **Fail closed.** Changed source issues, invalid plans, disabled write classes, and exceeded
   limits stop application.
10. **Keep secrets and private Jira content local.**

## 6. Definition of a well-groomed item

The quality model uses INVEST:

- Independent
- Negotiable
- Valuable
- Estimable
- Small
- Testable

A good user story includes:

- persona;
- need;
- benefit;
- context;
- observable Given/When/Then acceptance criteria;
- relevant non-functional requirements;
- explicit dependencies;
- out-of-scope boundaries;
- open questions;
- frontend, backend, and shared delivery considerations.

The model should include accessibility, privacy/security, analytics, performance, observability,
failure states, and rollout only when relevant. These must not become generic boilerplate.

### Epics

Epics are outcome containers. They should normally produce multiple small, independently valuable
vertical stories. They must not be split into frontend, backend, database, or testing layers.

### Bugs

Bugs remain bugs. A groomed bug should contain:

- user or operational impact;
- observed behavior;
- expected behavior;
- reproduction steps when known;
- affected environment when known;
- regression-focused acceptance criteria;
- relevant observability and failure evidence.

### Subtasks

Subtasks are excluded from top-level backlog ranking. A layer-only frontend/backend subtask should
be marked as a merge candidate for its parent vertical story. The product does not automatically
merge, delete, convert, or archive subtasks.

## 7. Current end-to-end workflow

### Step 1: Configure locally

Copy `groomer.example.toml` to `groomer.toml` and edit the local copy. The local file is ignored by
Git.

Set secrets only in environment variables:

```bash
export JIRA_BASE_URL='https://your-company.atlassian.net'
export JIRA_EMAIL='service-account@example.com'
export JIRA_API_TOKEN='...'
export OPENAI_API_KEY='...'
```

Bearer authentication is also supported through `JIRA_BEARER_TOKEN`.

### Step 2: Check connectivity

```bash
jira-groom doctor --config groomer.toml
```

This checks Jira authentication, server details, and the configured JQL without changing data.

### Step 3: Inventory the 900-item backlog

```bash
jira-groom inventory \
  --config groomer.toml \
  --output-dir .grooming \
  --wave-size 75
```

Inventory is read-only and makes no OpenAI request. It produces:

- issue type, status, status-category, and age distributions;
- orphaned subtask identification;
- oversized parent-group warnings;
- a Markdown report;
- a JSON inventory;
- parent-aware `wave-NNN.keys.txt` files.

At a wave size of 75, a synthetic 900-item verification produced 12 waves. The real number depends
on Jira parent groupings.

### Step 4: Plan a wave

```bash
jira-groom plan \
  --config groomer.toml \
  --output-dir .grooming \
  --keys-file .grooming/<inventory-id>.waves/wave-001.keys.txt
```

Planning creates:

- `<run-id>.report.md` for human review;
- `<run-id>.plan.json` containing source snapshots, typed assessments, ranking, warnings, and exact
  proposed actions.

Successful AI batches are cached in `.grooming/.ai-cache`. Retrying an unchanged wave reuses valid
cached batches. `--no-cache` deliberately requests fresh analysis.

### Step 5: Review

The product owner and delivery team should review:

- rewritten outcomes;
- acceptance criteria;
- proposed vertical splits;
- bug evidence;
- merge candidates;
- duplicate and dependency links;
- priority scores;
- archive evidence;
- every warning and open question.

Do not enable writes simply because a plan validates structurally.

### Step 6: Validate write permissions locally

Enable only the mutation classes required by the approved plan:

```toml
[write_policy]
allow_issue_updates = true
allow_issue_creation = false
allow_issue_links = true
allow_ranking = false
allow_archiving = false
```

Then validate:

```bash
jira-groom validate \
  --config groomer.toml \
  --plan .grooming/<run-id>.plan.json \
  --enforce-write-gates
```

### Step 7: Dry-run

```bash
jira-groom apply \
  --config groomer.toml \
  --plan .grooming/<run-id>.plan.json \
  --dry-run
```

The dry-run is offline and makes no Jira request.

### Step 8: Apply an approved wave

```bash
jira-groom apply \
  --config groomer.toml \
  --plan .grooming/<run-id>.plan.json \
  --confirm '<run-id>'
```

Application records progress in `<run-id>.apply.json`, stops at the first failure, and can be
retried. Successful idempotent actions are not repeated.

## 8. Recommended migration plan for 900 items

### Phase A: Read-only discovery

1. Confirm the exact JQL scope.
2. Run `doctor`.
3. Run a complete inventory.
4. Review type/status/age counts against Jira.
5. Inspect orphaned subtasks and parent groups larger than one wave.
6. Confirm that private or regulated data may be sent to the configured OpenAI account.

Exit condition: stakeholders agree on scope, safety policy, product context, and wave size.

### Phase B: Pilot

1. Select one representative wave containing stories, bugs, an epic, and subtasks.
2. Generate the plan.
3. Review every recommendation manually.
4. Measure human edit and rejection rates.
5. Apply only low-risk updates and links.
6. Keep creation, ranking, and archiving disabled.

Exit condition: the team trusts the story format and no policy violation or unexpected Jira write
occurs.

### Phase C: Controlled rollout

1. Process one wave at a time.
2. Apply and verify each wave before planning the next.
3. Enable creation only after the split-story format is accepted.
4. Enable ranking only after priority scores are calibrated against product judgment.
5. Keep archiving disabled until duplicate/obsolete decisions have independent human approval.

Suggested order:

1. Rewrite existing stories and bugs.
2. Add safe links.
3. Create approved vertical split stories.
4. Rank active top-level work.
5. Archive only approved, eligible work.

### Phase D: Archive cleanup

Archiving has a second independent gate and requires:

- `write_policy.allow_archiving = true`;
- `archive.allow_apply = true`;
- AI recommendation `archive_candidate`;
- AI confidence of at least 0.90;
- concrete archive evidence;
- allowed status/status category;
- configured staleness threshold;
- a non-subtask issue;
- a source timestamp that still satisfies policy.

Jira Cloud issue archiving also requires a suitable Premium/Enterprise license and administrator
permissions.

## 9. Current implemented capabilities

| Capability | Status |
|---|---|
| Jira Cloud REST v3 search | Implemented |
| Basic API-token and bearer authentication | Implemented |
| Read-only inventory | Implemented |
| Parent-aware review waves | Implemented |
| Structured OpenAI Responses API analysis | Implemented |
| Resumable AI batch cache | Implemented |
| INVEST quality scoring | Implemented |
| User-story ADF rendering | Implemented |
| Bug-specific ADF rendering | Implemented |
| Epic vertical split proposals | Implemented |
| Subtask merge-candidate handling | Implemented |
| Similarity candidate generation | Implemented |
| Duplicate/dependency/related links | Implemented |
| WSJF-style priority order | Implemented |
| Jira Software ranking | Implemented with configured anchor |
| Issue rewrites and split-story creation | Implemented |
| Double-gated issue archiving | Implemented |
| Unarchive command | Implemented |
| Optimistic concurrency checks | Implemented |
| Per-action apply state and retry | Implemented |
| Markdown and JSON audit artifacts | Implemented |
| GitHub Actions CI | Implemented |

## 10. Technical architecture

The project is a Python 3.11+ package with no database.

| File | Responsibility |
|---|---|
| `src/jira_groomer/cli.py` | CLI commands and artifact orchestration |
| `src/jira_groomer/config.py` | TOML configuration, environment credentials, semantic config hash |
| `src/jira_groomer/jira.py` | Jira Cloud REST client, retries, normalization, mutation methods |
| `src/jira_groomer/ai.py` | OpenAI prompt, structured responses, parallel batches, cache |
| `src/jira_groomer/inventory.py` | Backlog statistics and parent-aware wave generation |
| `src/jira_groomer/models.py` | Strict Pydantic schemas for issues, assessments, plans, actions |
| `src/jira_groomer/planner.py` | Similarity candidates, policy-gated actions, ranking |
| `src/jira_groomer/adf.py` | Atlassian Document Format parsing and generation |
| `src/jira_groomer/apply.py` | Structural validation, write gates, concurrency, idempotent apply |
| `src/jira_groomer/report.py` | Human-readable grooming report |
| `tests/` | Unit tests for policy, Jira pagination, ADF, inventory, caching, and apply |

The current AI default is `gpt-5.6-sol` with medium reasoning. The Responses API uses Pydantic
structured output; the model never receives a Jira mutation tool.

## 11. Safety and audit design

### Plan integrity

- The plan records a semantic configuration hash.
- Write gates may change after planning; planning semantics may not.
- The JQL, source count, source fingerprint, snapshots, assessments, before-values, references,
  action types, and configured link types are validated before application.
- Plans expire after a configured number of hours.

### Concurrency

- Updates and archives compare Jira's current `updated` timestamp with the approved snapshot.
- Self-induced link timestamp changes are recorded in apply state.
- A later unexpected timestamp change stops the run.

### Idempotency

- Created stories receive action-specific labels.
- Retries search for an existing issue with that label.
- Existing desired updates and links are detected.
- Apply state is atomically written after each action.

### Destructive operations

- Archiving is double-gated.
- Subtasks are never directly archived.
- New issues are not automatically deleted after a partial failure.
- Original Jira text is preserved by default.
- Unarchive is explicit and separately confirmed.

## 12. Data handling

By default the AI receives:

- summary and description;
- issue type and status;
- priority and labels;
- component names;
- parent, subtask, and existing-link keys;
- created and updated timestamps;
- short excerpts for possible related issues.

Reporter, assignee, comments, attachments, and changelog are not requested by default. Descriptions
may still contain personal, confidential, or regulated content.

`.env`, `groomer.toml`, `.grooming/`, virtual environments, and local Codex/ChatGPT metadata are
ignored by Git. Never override those exclusions in the public repository.

## 13. Success metrics

Establish a human-scored baseline before setting hard targets.

Suggested product metrics:

- percentage of active top-level items with persona, outcome, benefit, and acceptance criteria;
- median and distribution of INVEST scores;
- percentage of items requiring material human rewrite after AI;
- recommendation acceptance rate by type;
- duplicate-link precision;
- number of orphaned or layer-only subtasks;
- number of stale items with an explicit disposition;
- plan-to-apply cycle time;
- apply failure rate;
- count of concurrency stops;
- number of unintended archives or creations, with a target of zero;
- delivery-team satisfaction with backlog clarity.

## 14. Known limitations and risks

1. Jira Data Center is not supported.
2. OAuth acquisition/refresh is not implemented; a bearer token must be supplied externally.
3. Required Jira custom fields must be configured manually in `jira.create_fields`.
4. Relationship candidates are selected inside the current wave, so cross-wave semantic
   dependencies may be missed.
5. Planning a wave currently fetches the full configured JQL result and filters it locally.
6. Jira issue-type conversion and automatic subtask merging are intentionally unsupported.
7. No live Jira/OpenAI integration test has been run in this repository.
8. AI prioritization is decision support and needs calibration against product judgment.
9. Preserving a very long original description can approach Jira field-size limits.
10. Jira workflows, screens, link names, required fields, and permissions vary by instance.
11. The product has no UI; review occurs in Markdown/JSON and the terminal.
12. There is no built-in token/cost forecast yet.

## 15. Prioritized roadmap

### P0 — Before the first real write

- Run a real inventory and compare its counts with Jira.
- Confirm actual Jira issue type and link type names.
- Configure required create fields.
- Run a 10–20 issue read-only quality evaluation.
- Create a human scoring rubric and expected outputs.
- Verify the service identity has only intended permissions.
- Confirm organizational approval for sending selected Jira fields to OpenAI.

### P1 — Production hardening

- Add targeted bulk fetch for wave keys rather than reading the full JQL each time.
- Add a cost/token estimate before AI requests.
- Add Jira edit/create metadata discovery and preflight required-field checks.
- Add cross-wave semantic candidate indexing.
- Add configurable custom-field mapping for story points, teams, products, and epic relationships.
- Add machine-readable evaluation fixtures for stories, bugs, epics, duplicates, and subtasks.
- Add structured logging with secret and Jira-content redaction.
- Add a command that generates an executive migration progress report across completed waves.

### P2 — Product experience

- Add a local review UI for accepting, editing, or rejecting individual actions.
- Support approved partial-plan export without manual JSON editing.
- Visualize parent groups, duplicates, blockers, and split stories.
- Track quality trends across waves.
- Add provider-neutral AI support only if structured-output quality remains equivalent.

### P3 — Broader deployment

- Add OAuth authorization-code and token-refresh support.
- Evaluate Jira Data Center through a separate adapter.
- Package signed releases and publish to PyPI if there is demand.
- Add organization policy templates and reusable evaluation packs.

## 16. Decisions from the original working session

- The repository is public and hosted under `ryan12324/jira-backlog-groomer`.
- The project uses the MIT license.
- The implementation is Python and distributed as a CLI.
- Jira Cloud REST v3 is the supported Jira platform.
- OpenAI Responses API structured outputs are used.
- The model cannot call Jira directly.
- Dry-run planning and explicit apply are separate.
- All write classes default to disabled.
- Archive application has two gates.
- The current backlog scale is approximately 900 mixed items.
- The rollout uses parent-aware waves, recommended at 50–100 items.
- Successful AI batches are cached for retry.
- FE and BE work stays in one vertical story wherever it serves one outcome.
- Bugs, epics, and subtasks receive type-specific handling.
- Synced ChatGPT project files and local metadata are excluded from Git.
- CI runs lint and unit tests on every push and pull request.

## 17. Resume on another PC

### Clone and install

```bash
gh repo clone ryan12324/jira-backlog-groomer
cd jira-backlog-groomer
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

On Windows PowerShell:

```powershell
gh repo clone ryan12324/jira-backlog-groomer
cd jira-backlog-groomer
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

### Verify the checkout

```bash
ruff check .
pytest
jira-groom --help
```

At the time of this handoff, the expected test result is 16 passing tests.

### Create local configuration

```bash
cp groomer.example.toml groomer.toml
```

Obtain secrets through the organization's approved secret-sharing method. Do not copy them into
chat, email, Git, or this product plan.

### Check remote state

```bash
git status
git remote -v
git pull --ff-only
gh run list --limit 5
```

### Continue development

1. Read `README.md` and this document.
2. Run the tests.
3. Review open GitHub issues and recent commits.
4. Choose one item from the P0/P1 roadmap.
5. Make a focused change with tests.
6. Run `ruff check .` and `pytest`.
7. Commit and push through the normal GitHub workflow.

## 18. Immediate next action

The next real-world action is not a Jira write. It is:

1. create a local `groomer.toml`;
2. set credentials locally;
3. run `jira-groom doctor`;
4. run a complete read-only inventory at wave size 75;
5. review the inventory with the product owner and engineering lead;
6. select a representative pilot wave;
7. generate and score that plan without enabling writes.

That pilot should produce the evidence needed to calibrate prompts, policy thresholds, custom Jira
fields, and the human review process before touching the remaining backlog.
