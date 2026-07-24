# Jira Backlog Groomer

A review-first Python CLI that reads a Jira Cloud backlog, uses OpenAI to turn weak tickets
into strong cross-functional user stories, and produces an auditable mutation plan. It can
rewrite, split/create, link, rank, and archive issues, but it never writes to Jira while planning
and all write classes are disabled by default.

This is deliberately not an autonomous “let the model loose on Jira” bot. The model produces
typed recommendations. Deterministic code constrains references, age/status archive rules,
action counts, optimistic concurrency, and idempotency. A human reviews the resulting Markdown
and JSON before a separately confirmed apply.

## What “good” means here

The grooming prompt and output schema are designed for a frontend/backend team sharing one
backlog:

- One vertical story per user outcome, not separate FE and BE delivery tickets.
- INVEST scoring: independent, negotiable, valuable, estimable, small, and testable.
- A clear persona, need, benefit, context, and observable Given/When/Then acceptance criteria.
- FE, BE, and shared delivery considerations in the same story.
- Relevant non-functional behavior such as accessibility, security, analytics, observability,
  failure states, and rollout.
- Unknowns are recorded as open questions instead of being invented.
- Splits are independently valuable vertical slices.
- Priority is a transparent WSJF-style score from business value, time criticality,
  risk reduction, and size.

## Safety model

Planning is read-only. Applying requires all of the following:

1. Review the generated report and JSON.
2. Enable each needed mutation class in `write_policy`.
3. For archiving, enable both `write_policy.allow_archiving` and `archive.allow_apply`.
4. Pass the exact plan run ID to `--confirm`.
5. Keep the source issues unchanged since planning, unless you deliberately disable that check.

The apply command:

- validates all issue references before the first write;
- refuses semantic config drift after planning (write gates may be changed);
- stops on the first failed action;
- writes an atomic apply-state file after every successful action;
- uses per-create idempotency labels and checks existing updates/links on retry;
- rechecks issue timestamps and archive eligibility immediately before mutation.

The JSON plan contains the source snapshot and before-values for rewrites. Jira retains its own
issue history. New issues are not automatically deleted on failure.

## Requirements

- Python 3.11+
- Jira Cloud REST API v3
- Jira Software Cloud if applying rank changes
- An OpenAI API key
- A Jira identity with only the permissions you intend the script to use

Actual issue archiving is a Jira Premium/Enterprise admin feature. If your plan or permissions do
not support it, leave archive writes off and use the report as a triage queue.

## Install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp groomer.example.toml groomer.toml
```

Set secrets in the environment, not the TOML file:

```bash
export JIRA_BASE_URL='https://your-company.atlassian.net'
export JIRA_EMAIL='service-account@example.com'
export JIRA_API_TOKEN='...'
export OPENAI_API_KEY='...'
```

For a personal/ad-hoc Jira Cloud script, basic authentication uses an Atlassian account email and
API token, not a password. For a longer-lived organizational integration, prefer OAuth 2.0 and set
`jira.auth_mode = "bearer"` plus `JIRA_BEARER_TOKEN`.

## Configure

Copy and edit `groomer.example.toml`. The most important choices are:

- `jira.jql`: the exact bounded backlog in scope;
- `product`: vision, personas, constraints, and Definition of Done;
- `jira.story_issue_type`: the issue type used for split stories;
- `jira.create_fields`: static custom fields required by your Jira create screen;
- archive status/age allow-lists;
- maximum action counts;
- `ranking.rank_anchor_issue`: optional stable issue immediately after the desired ordered set.

Use a small pilot JQL first, such as one component or 10–20 issues. Do not include secrets in
the config.

## Run

Check Jira authentication and JQL without changing data:

```bash
jira-groom doctor --config groomer.toml
```

Generate a plan:

```bash
jira-groom plan --config groomer.toml --output-dir .grooming --max-issues 20
```

This produces:

- `<run-id>.report.md`: human-readable quality review, stories, ordering, and warnings;
- `<run-id>.plan.json`: source snapshot and exact machine-readable actions.

No Jira mutation occurs during `plan`.

Validate the saved plan offline:

```bash
jira-groom validate \
  --config groomer.toml \
  --plan .grooming/<run-id>.plan.json
```

After review, enable only the required write gates in `groomer.toml`. Confirm that those gates
cover every action type in the plan:

```bash
jira-groom validate \
  --config groomer.toml \
  --plan .grooming/<run-id>.plan.json \
  --enforce-write-gates
```

Exercise the apply path without credentials or network writes:

```bash
jira-groom apply \
  --config groomer.toml \
  --plan .grooming/<run-id>.plan.json \
  --dry-run
```

Apply the reviewed plan:

```bash
jira-groom apply \
  --config groomer.toml \
  --plan .grooming/<run-id>.plan.json \
  --confirm '<run-id>'
```

If a run stops, inspect its `.apply.json` state, resolve the cause, and rerun the same command.
Completed idempotent actions are not repeated.

Restore archived issues:

```bash
jira-groom unarchive \
  --config groomer.toml \
  --confirm UNARCHIVE \
  WEB-123 WEB-456
```

## Data handling

By default, the selected Jira summary, description, labels, component names, status, links, and
timestamps are sent to OpenAI for analysis. Reporter, assignee, comments, and attachments are not
requested. Descriptions can still contain personal or confidential data.

Before production use:

- ensure this processing is permitted by your organization and OpenAI account controls;
- narrow `jira.fields` and JQL to the minimum necessary scope;
- set `ai.send_original_descriptions = false` if summaries and metadata are sufficient;
- use a dedicated least-privilege Jira service identity;
- keep `.env`, `.grooming`, and apply-state files out of source control;
- evaluate prompt quality on a representative, human-scored ticket set.

## Jira-specific notes

- Jira Cloud descriptions use Atlassian Document Format; the CLI renders generated stories to ADF.
- Link type names and workflows vary. Configure the link names exactly as they appear in your site.
- Ranking uses Jira Software’s `/rest/agile/1.0/issue/rank` endpoint and requires an anchor.
- Archiving uses Jira Cloud’s issue archive API and is separately double-gated.
- Required custom fields vary by project. Put static values in `jira.create_fields`.
- Jira Data Center has different APIs and is not supported by this version.

Relevant official references:

- [Jira Cloud REST v3 introduction](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/)
- [Jira issue search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/)
- [Jira issue operations and archiving](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/)
- [Jira issue links](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-links/)
- [Jira Software ranking](https://developer.atlassian.com/cloud/jira/software/rest/api-group-issue/)
- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [OpenAI Python SDK](https://github.com/openai/openai-python)

## Tests

```bash
pytest
ruff check .
```

The test suite covers ADF rendering, similarity candidates, archive gates, plan drift, write
gates, and Jira enhanced-search pagination. Live Jira/OpenAI integration tests are intentionally
not run without your credentials and instance.
