#!/usr/bin/env python3
"""Count how many auto_log runs have a valid audience video.

Success rule:
- run directory contains an `audience_video` directory
- at least one `.mp4` file under it has duration > 5 seconds
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Iterable


DEFAULT_LOG_DIR = Path(
    "/home/dex/workspace/sources/EmbodiChain/gym_project/action_agent_pipeline/auto_logs"
)


def run_ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")

    payload = json.loads(result.stdout or "{}")
    duration_raw = payload.get("format", {}).get("duration")
    if duration_raw is None:
        raise RuntimeError("ffprobe output missing duration")

    duration = float(duration_raw)
    if not (duration >= 0):
        raise RuntimeError("invalid duration")
    return duration


def iter_run_dirs(log_root: Path) -> Iterable[Path]:
    if not log_root.exists():
        raise FileNotFoundError(f"log root does not exist: {log_root}")
    for child in sorted(log_root.iterdir()):
        if child.is_dir():
            yield child


def evaluate_run(run_dir: Path, min_seconds: float):
    audience_dir = run_dir / "audience_video"
    if not audience_dir.is_dir():
        return False, "missing audience_video directory", None, None

    mp4s = sorted(audience_dir.glob("*.mp4"))
    if not mp4s:
        return False, "audience_video exists but no mp4 file", None, None

    parsed_videos: list[tuple[Path, float]] = []
    parse_errors: list[tuple[Path, str]] = []
    max_duration = -1.0
    max_video: Path | None = None

    for mp4 in mp4s:
        try:
            duration = run_ffprobe_duration(mp4)
            parsed_videos.append((mp4, duration))
            if duration > min_seconds:
                return True, f"{duration:.3f}s", mp4, duration
            if duration > max_duration:
                max_duration = duration
                max_video = mp4
        except Exception as exc:
            parse_errors.append((mp4, str(exc)))

    if not parsed_videos:
        if parse_errors:
            reason = "; ".join(f"{p.name}: {msg}" for p, msg in parse_errors[:5])
            if len(parse_errors) > 5:
                reason += "; ..."
            return False, reason, None, None
        return False, "no readable mp4 in audience_video", None, None

    if max_duration < 0:
        return False, "all mp4 parsing failed", max_video, None

    return (
        False,
        f"max duration {max_duration:.3f}s <= {min_seconds}s",
        max_video,
        max_duration,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Count successful runs in auto_logs by audience_video length.")
    parser.add_argument(
        "log_dir",
        nargs="?",
        default=str(DEFAULT_LOG_DIR),
        help="Path to auto_logs directory.",
    )
    parser.add_argument(
        "--min-seconds",
        type=float,
        default=5.0,
        help="Minimum audience_video duration to count as success.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-run result line including failure reasons.",
    )
    parser.add_argument(
        "--failed-only",
        action="store_true",
        help="When verbose, show only failed runs.",
    )

    args = parser.parse_args()

    log_root = Path(args.log_dir)
    total = 0
    success = 0
    failures = []

    for run_dir in iter_run_dirs(log_root):
        total += 1
        is_ok, detail, video_path, duration = evaluate_run(run_dir, args.min_seconds)
        if is_ok:
            success += 1
            if args.verbose and not args.failed_only:
                v = video_path.name if video_path else "-"
                d = f"{duration:.3f}s" if duration is not None else "-"
                print(f"[PASS] {run_dir.name:8} | {v:30} | {d}")
        else:
            failures.append((run_dir.name, detail))
            if args.verbose:
                if args.failed_only:
                    print(f"[FAIL] {run_dir.name}: {detail}")
                else:
                    print(f"[FAIL] {run_dir.name}: {detail}")

    if total == 0:
        print("No run directories found.")
        return 1

    fail_count = total - success
    rate = (success / total) * 100.0

    print(f"\nTotal runs:  {total}")
    print(f"Success:     {success}")
    print(f"Fail:        {fail_count}")
    print(f"Success rate:{rate:.2f}%")

    if failures and not args.verbose:
        print("\nFailing sample (first 10):")
        for name, reason in failures[:10]:
            print(f"- {name}: {reason}")
        if len(failures) > 10:
            print(f"... and {len(failures) - 10} more failures")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
