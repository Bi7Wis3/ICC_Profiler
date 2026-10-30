"""CLI entry point: `python -m icc_profiler <command> ...`"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from icc_profiler import argyll_tools, check_chart, stages, state, target_colors
from icc_profiler.config import JobConfig
from icc_profiler.errors import PipelineError
from icc_profiler.names import safe_name

DEFAULT_JOBS_ROOT = Path("jobs")


def _job_dir(args: argparse.Namespace) -> Path:
    return Path(args.jobs_root).resolve() / safe_name(args.job, "Job name")


def _load_config(args: argparse.Namespace) -> JobConfig:
    return JobConfig.load(_job_dir(args) / "config.json")


def cmd_doctor(args: argparse.Namespace) -> int:
    results = argyll_tools.doctor(bin_dir=args.bin_dir)
    all_found = True
    for tool, info in results.items():
        if info["found"]:
            print(f"[ok]   {tool:12s} {info['path']}  ({info['banner']})")
        else:
            all_found = False
            print(f"[MISS] {tool:12s} {info['error']}")
    return 0 if all_found else 1


def cmd_init_job(args: argparse.Namespace) -> int:
    template = json.loads(Path(args.config).read_text())
    config = JobConfig.from_dict(template)
    job_dir = stages.init_job(Path(args.jobs_root).resolve(), config)
    print(f"Job {config.job_name!r} initialized at {job_dir}")
    return 0


def _run_stage(args: argparse.Namespace, stage: str) -> int:
    try:
        config = _load_config(args)
        job_dir = _job_dir(args)
        result = stages.run_stage(job_dir, config, stage)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if stage == "verify":
        print(f"Verification report: {result}")
    else:
        print(f"Stage complete: {stage}")
    return 0


def cmd_stage(args):
    return _run_stage(args, args.command)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
        job_dir = _job_dir(args)
        result = stages.run_all(
            job_dir, config, force=args.force, regenerate_chart=args.regenerate_chart
        )
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result["ran"]:
        print(f"Ran: {', '.join(result['ran'])}")
    if result["stopped_at"]:
        print(f"\nWaiting on you before '{result['stopped_at']}' can run:")
        print(f"  {result['message']}")
        print("\nRe-run `run` once that's done.")
    else:
        print(result["message"])
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _load_config(args)
    job_dir = _job_dir(args)
    order = stages.order_for(config)
    st = state.load_state(job_dir, order)
    stale = state.stale_stages(job_dir, order)
    for stg in order:
        marker = "done" if state.is_done(st, stg) else "pending"
        if stg in stale:
            marker += "  (was done, but an earlier stage ran after it -- out of date)"
        print(f"  {stg:24s} {marker}")
    return 0


def cmd_check_chart(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
        job_dir = _job_dir(args)
        check_chart.generate(job_dir, config, args.patches)
        info = check_chart.status(job_dir, config)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Check chart ({info['patch_count']} patches, {info['first_loc']} ... {info['last_loc']}) "
          f"in {check_chart.check_dir(job_dir)}")
    return 0


def cmd_check_profile(args: argparse.Namespace) -> int:
    try:
        config = _load_config(args)
        job_dir = _job_dir(args)
        export = Path(args.export)
        check_chart.save_export(job_dir, config, export.read_bytes(), export.suffix)
        result = check_chart.run(job_dir, config)
    except (PipelineError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Check chart: average dE {result['average_de']}, largest {result['maximum_de']} "
          f"on {result['patch_count']} patches. Report: {check_chart.report_path(job_dir, config)}")
    return 0


def cmd_colors_add(args: argparse.Namespace) -> int:
    try:
        job_dir = _job_dir(args)
        device_value = [float(x) for x in args.device_value.split(",")]
        target_lab = [float(x) for x in args.target_lab.split(",")] if args.target_lab else None
        target_colors.add(job_dir, args.name, device_value, target_lab)
        cleared = stages.invalidate_from(job_dir, _load_config(args), "generate-chart")
    except (PipelineError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Added target color {args.name!r}")
    if cleared:
        print("The chart no longer includes all custom colors -- re-run generate-chart (and reprint).")
    return 0


def cmd_colors_list(args: argparse.Namespace) -> int:
    job_dir = _job_dir(args)
    for c in target_colors.load(job_dir):
        print(f"  {c['name']:20s} device={c['device_value']} target_lab={c['target_lab']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="icc_profiler")
    parser.add_argument("--jobs-root", default=str(DEFAULT_JOBS_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="Check that all required ArgyllCMS tools resolve")
    p.add_argument("--bin-dir", default=None)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("init-job", help="Create a new job from a config JSON file")
    p.add_argument("--config", required=True)
    p.set_defaults(func=cmd_init_job)

    for name, func in [
        ("generate-chart", cmd_stage),
        ("build-scanner-profile", cmd_stage),
        ("read-chart", cmd_stage),
        ("import-measurements", cmd_stage),
        ("build-profile", cmd_stage),
        ("verify", cmd_stage),
        ("status", cmd_status),
    ]:
        p = sub.add_parser(name)
        p.add_argument("--job", required=True)
        p.set_defaults(func=func)

    p = sub.add_parser("check-chart", help="Make a small second chart to test a finished profile (colorimeter jobs)")
    p.add_argument("--job", required=True)
    p.add_argument("--patches", type=int, default=check_chart.DEFAULT_PATCH_COUNT)
    p.set_defaults(func=cmd_check_chart)

    p = sub.add_parser("check-profile", help="Compare the profile with the measured check chart")
    p.add_argument("--job", required=True)
    p.add_argument("--export", required=True, help="Colorimeter export (.csv/.xlsx) of the check chart")
    p.set_defaults(func=cmd_check_profile)

    p = sub.add_parser("run", help="Run all remaining stages, stopping cleanly at any human step")
    p.add_argument("--job", required=True)
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-run every stage after the chart (keeps the printed chart valid)",
    )
    p.add_argument(
        "--regenerate-chart",
        action="store_true",
        help="Also regenerate the chart. Anything already printed/measured must be redone.",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("colors-add", help="Add a custom target color to a job")
    p.add_argument("--job", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--device-value", required=True, help="Comma-separated, e.g. 200,40,40 for RGB")
    p.add_argument("--target-lab", default=None, help="Optional comma-separated L,a,b for reference")
    p.set_defaults(func=cmd_colors_add)

    p = sub.add_parser("colors-list", help="List a job's custom target colors")
    p.add_argument("--job", required=True)
    p.set_defaults(func=cmd_colors_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
