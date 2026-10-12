"""CLI entry point: `python -m icc_profiler <command> ...`"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from icc_profiler import argyll_tools, stages, state, target_colors
from icc_profiler.config import JobConfig
from icc_profiler.errors import PipelineError

DEFAULT_JOBS_ROOT = Path("jobs")


def _job_dir(args: argparse.Namespace) -> Path:
    return Path(args.jobs_root) / args.job


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
    job_dir = stages.init_job(Path(args.jobs_root), config)
    print(f"Job {config.job_name!r} initialized at {job_dir}")
    return 0


def _run_stage(args: argparse.Namespace, func) -> int:
    config = _load_config(args)
    job_dir = _job_dir(args)
    try:
        func(job_dir, config)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Stage complete: {func.__name__.replace('_', '-')}")
    return 0


def cmd_generate_chart(args):
    return _run_stage(args, stages.generate_chart)


def cmd_build_scanner_profile(args):
    return _run_stage(args, stages.build_scanner_profile)


def cmd_read_chart(args):
    return _run_stage(args, stages.read_chart)


def cmd_import_measurements(args):
    return _run_stage(args, stages.import_measurements)


def cmd_build_profile(args):
    return _run_stage(args, stages.build_profile)


def cmd_verify(args):
    config = _load_config(args)
    job_dir = _job_dir(args)
    try:
        report_path = stages.verify(job_dir, config)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Verification report: {report_path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = _load_config(args)
    job_dir = _job_dir(args)
    try:
        result = stages.run_all(job_dir, config, force=args.force)
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
    st = state.load_state(job_dir)
    for stg in state.stage_order(config.measurement.method):
        marker = "done" if state.is_done(st, stg) else "pending"
        print(f"  {stg:24s} {marker}")
    return 0


def cmd_colors_add(args: argparse.Namespace) -> int:
    job_dir = _job_dir(args)
    device_value = [float(x) for x in args.device_value.split(",")]
    target_lab = [float(x) for x in args.target_lab.split(",")] if args.target_lab else None
    try:
        target_colors.add(job_dir, args.name, device_value, target_lab)
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Added target color {args.name!r}")
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
        ("generate-chart", cmd_generate_chart),
        ("build-scanner-profile", cmd_build_scanner_profile),
        ("read-chart", cmd_read_chart),
        ("import-measurements", cmd_import_measurements),
        ("build-profile", cmd_build_profile),
        ("verify", cmd_verify),
        ("status", cmd_status),
    ]:
        p = sub.add_parser(name)
        p.add_argument("--job", required=True)
        p.set_defaults(func=func)

    p = sub.add_parser("run", help="Run all remaining stages, stopping cleanly at any human step")
    p.add_argument("--job", required=True)
    p.add_argument("--force", action="store_true", help="Re-run every stage from scratch")
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
