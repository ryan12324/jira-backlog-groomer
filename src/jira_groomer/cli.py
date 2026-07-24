from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .ai import OpenAIBacklogAnalyst
from .apply import PlanApplier, validate_plan
from .config import JiraCredentials, Settings, planning_config_sha256
from .errors import GroomerError, PolicyError
from .inventory import build_inventory, inventory_to_markdown
from .jira import JiraClient
from .models import GroomingPlan
from .planner import build_candidate_map, build_plan
from .report import plan_to_markdown


def _settings(path: str) -> Settings:
    return Settings.load(path)


def _jira(settings: Settings) -> JiraClient:
    credentials = JiraCredentials.from_environment(settings.jira.auth_mode)
    assert settings.jira.base_url
    return JiraClient(settings.jira.base_url, credentials)


def _load_plan(path: str | Path) -> GroomingPlan:
    plan_path = Path(path)
    try:
        return GroomingPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PolicyError(f"Cannot load plan {plan_path}: {exc}") from exc


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _read_keys_file(path: str | Path) -> list[str]:
    key_path = Path(path)
    try:
        keys = [
            line.strip()
            for line in key_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except OSError as exc:
        raise PolicyError(f"Cannot read key file {key_path}: {exc}") from exc
    if not keys:
        raise PolicyError(f"Key file {key_path} contains no issue keys")
    if len(keys) != len(set(keys)):
        raise PolicyError(f"Key file {key_path} contains duplicate issue keys")
    return keys


def _cmd_doctor(args: argparse.Namespace) -> int:
    settings = _settings(args.config)
    with _jira(settings) as jira:
        identity = jira.myself()
        server = jira.server_info()
        sample = jira.search_issues(
            settings.jira.jql,
            settings.jira.fields,
            max_issues=1,
        )
    print(f"Jira: connected to {server.get('baseUrl', settings.jira.base_url)}")
    print(f"Jira deployment: {server.get('deploymentType', 'unknown')}")
    print(f"Jira identity: {identity.get('displayName', identity.get('accountId', 'unknown'))}")
    print(f"JQL: valid ({'at least one issue' if sample else 'no matching issues'})")
    print(f"OpenAI key: {'present' if os.getenv('OPENAI_API_KEY') else 'missing'}")
    print("No data was changed.")
    return 0


def _cmd_inventory(args: argparse.Namespace) -> int:
    settings = _settings(args.config)
    with _jira(settings) as jira:
        issues = jira.search_issues(
            settings.jira.jql,
            settings.jira.fields,
            max_issues=args.max_issues,
        )
    if not issues:
        raise PolicyError("The configured JQL returned no issues")
    inventory = build_inventory(
        issues,
        source_jql=settings.jira.jql,
        wave_size=args.wave_size,
    )
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory_id = f"inventory-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    json_path = output_dir / f"{inventory_id}.json"
    report_path = output_dir / f"{inventory_id}.md"
    waves_dir = output_dir / f"{inventory_id}.waves"
    waves_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(inventory.model_dump_json(indent=2), encoding="utf-8")
    report_path.write_text(inventory_to_markdown(inventory), encoding="utf-8")
    for wave in inventory.waves:
        wave_path = waves_dir / f"wave-{wave.wave_number:03d}.keys.txt"
        wave_path.write_text("\n".join(wave.keys) + "\n", encoding="utf-8")
    print(
        f"Inventory created for {inventory.issue_count} issues in "
        f"{len(inventory.waves)} review waves."
    )
    print(f"Inventory JSON: {json_path}")
    print(f"Review report: {report_path}")
    print(f"Wave key files: {waves_dir}")
    print("No AI request was made and no Jira data was changed.")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    settings = _settings(args.config)
    if args.keys_file and args.max_issues:
        raise PolicyError("--keys-file and --max-issues cannot be used together")
    with _jira(settings) as jira:
        fetched_issues = jira.search_issues(
            settings.jira.jql,
            settings.jira.fields,
            max_issues=None if args.keys_file else args.max_issues,
        )
    if args.keys_file:
        requested_keys = _read_keys_file(args.keys_file)
        fetched_by_key = {issue.key: issue for issue in fetched_issues}
        missing = [key for key in requested_keys if key not in fetched_by_key]
        if missing:
            raise PolicyError(
                "Wave keys are not present in the current JQL result: " + ", ".join(missing[:20])
            )
        issues = [fetched_by_key[key] for key in requested_keys]
    else:
        issues = fetched_issues
    if not issues:
        raise PolicyError("The configured JQL returned no issues")
    candidate_map = build_candidate_map(
        issues,
        threshold=settings.quality.candidate_similarity_threshold,
        limit=settings.quality.duplicate_candidates_per_issue,
    )
    analyst = OpenAIBacklogAnalyst(settings.ai, settings.product)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = None if args.no_cache else output_dir / ".ai-cache"
    assessments = analyst.analyze(
        issues,
        candidate_map,
        cache_dir=cache_dir,
        progress=lambda completed, total: print(
            f"AI analysis batches: {completed}/{total}",
            flush=True,
        ),
    )
    plan = build_plan(
        settings,
        issues,
        assessments,
        config_sha256=planning_config_sha256(settings),
    )

    json_path = output_dir / f"{plan.run_id}.plan.json"
    report_path = output_dir / f"{plan.run_id}.report.md"
    json_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    report_path.write_text(plan_to_markdown(plan), encoding="utf-8")
    print(f"Plan created with {len(plan.actions)} proposed actions.")
    print(f"JSON plan: {json_path}")
    print(f"Review report: {report_path}")
    print("No Jira data was changed.")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    settings = _settings(args.config)
    plan = _load_plan(args.plan)
    validate_plan(plan, settings, enforce_write_gates=args.enforce_write_gates)
    print(f"Plan {plan.run_id} is structurally valid.")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    settings = _settings(args.config)
    plan = _load_plan(args.plan)
    if not args.dry_run and args.confirm != plan.run_id:
        raise PolicyError(
            f"Refusing Jira writes. Pass --confirm {plan.run_id} after reviewing the plan."
        )
    plan_path = Path(args.plan).resolve()
    state_path = (
        Path(args.state).resolve()
        if args.state
        else plan_path.with_name(plan_path.name.replace(".plan.json", ".apply.json"))
    )
    if args.dry_run:
        report = PlanApplier(settings, None).apply(
            plan,
            state_path=state_path,
            dry_run=True,
        )
    else:
        with _jira(settings) as jira:
            report = PlanApplier(settings, jira).apply(
                plan,
                state_path=state_path,
                dry_run=False,
            )
    latest_results = {result.action_id: result for result in report.results}
    failed = [result for result in latest_results.values() if result.status == "failed"]
    applied = [result for result in latest_results.values() if result.status == "applied"]
    if args.dry_run:
        print(f"Dry run passed for {len(report.results)} actions. No Jira requests were made.")
    else:
        print(f"Applied {len(applied)} actions. State: {state_path}")
    if failed:
        print(f"Stopped after failure: {failed[-1].detail}", file=sys.stderr)
        return 1
    return 0


def _cmd_unarchive(args: argparse.Namespace) -> int:
    settings = _settings(args.config)
    if args.confirm != "UNARCHIVE":
        raise PolicyError("Pass --confirm UNARCHIVE to restore the specified issues")
    if not settings.write_policy.allow_archiving or not settings.archive.allow_apply:
        raise PolicyError(
            "Unarchive requires write_policy.allow_archiving=true and archive.allow_apply=true"
        )
    with _jira(settings) as jira:
        result = jira.unarchive_issues(args.issue_keys)
    print(f"Jira reports {result.get('numberOfIssuesUpdated', 0)} restored issues.")
    if result.get("errors"):
        print(f"Jira reported errors: {result['errors']}", file=sys.stderr)
        return 1
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jira-groom",
        description="Policy-gated AI grooming for a cross-functional Jira Cloud backlog",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check Jira authentication and JQL")
    doctor.add_argument("--config", default="groomer.toml")
    doctor.set_defaults(handler=_cmd_doctor)

    inventory = subparsers.add_parser(
        "inventory",
        help="Inventory the backlog and generate parent-aware review waves",
    )
    inventory.add_argument("--config", default="groomer.toml")
    inventory.add_argument("--output-dir", default=".grooming")
    inventory.add_argument("--wave-size", type=_positive_int, default=100)
    inventory.add_argument("--max-issues", type=_positive_int)
    inventory.set_defaults(handler=_cmd_inventory)

    plan = subparsers.add_parser("plan", help="Read Jira and produce a reviewable plan")
    plan.add_argument("--config", default="groomer.toml")
    plan.add_argument("--output-dir", default=".grooming")
    plan.add_argument("--max-issues", type=_positive_int)
    plan.add_argument("--keys-file")
    plan.add_argument("--no-cache", action="store_true")
    plan.set_defaults(handler=_cmd_plan)

    validate = subparsers.add_parser("validate", help="Validate a saved plan offline")
    validate.add_argument("--config", default="groomer.toml")
    validate.add_argument("--plan", required=True)
    validate.add_argument("--enforce-write-gates", action="store_true")
    validate.set_defaults(handler=_cmd_validate)

    apply = subparsers.add_parser("apply", help="Apply an approved saved plan")
    apply.add_argument("--config", default="groomer.toml")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--confirm")
    apply.add_argument("--state")
    apply.add_argument("--dry-run", action="store_true")
    apply.set_defaults(handler=_cmd_apply)

    unarchive = subparsers.add_parser("unarchive", help="Restore archived Jira issues")
    unarchive.add_argument("--config", default="groomer.toml")
    unarchive.add_argument("--confirm")
    unarchive.add_argument("issue_keys", nargs="+")
    unarchive.set_defaults(handler=_cmd_unarchive)
    return parser


def _main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (GroomerError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def main() -> None:
    raise SystemExit(_main())


if __name__ == "__main__":
    main()
