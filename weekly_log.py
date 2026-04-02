#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DEFAULT_GITHUB = "stats/github.csv"
DEFAULT_DOCKER = "stats/dockerhub.csv"
DEFAULT_OUTPUT = "WEEKLY.md"


@dataclass(frozen=True)
class Snapshot:
    ts: datetime
    metrics: Dict[str, int]


@dataclass(frozen=True)
class MetricDelta:
    source: str
    repo: str
    metric: str
    previous: int
    current: int

    @property
    def delta(self) -> int:
        return self.current - self.previous

    @property
    def pct(self) -> Optional[float]:
        if self.previous <= 0:
            return None
        return (self.delta / self.previous) * 100.0


def parse_ts(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def sunday_end_utc(today_utc: date) -> datetime:
    # Monday=0 ... Sunday=6
    days_since_sunday = (today_utc.weekday() + 1) % 7
    sunday = today_utc - timedelta(days=days_since_sunday)
    return datetime.combine(sunday, time(23, 59, 59), tzinfo=timezone.utc)


def parse_week_end(value: str) -> datetime:
    day = date.fromisoformat(value)
    return datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc)


def load_snapshots(path: Path, metrics: List[str]) -> Dict[str, List[Snapshot]]:
    by_repo: Dict[str, List[Snapshot]] = {}

    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        for row in reader:
            repo = str(row.get("repo", "") or "").strip()
            ts_raw = str(row.get("ts", "") or "").strip()
            if not repo or not ts_raw:
                continue

            metric_values: Dict[str, int] = {}
            ok = True
            for metric in metrics:
                raw = str(row.get(metric, "") or "").strip()
                if raw == "":
                    ok = False
                    break
                metric_values[metric] = int(float(raw))

            if not ok:
                continue

            if repo not in by_repo:
                by_repo[repo] = []
            by_repo[repo].append(Snapshot(ts=parse_ts(ts_raw), metrics=metric_values))

    for repo in by_repo:
        by_repo[repo].sort(key=lambda item: item.ts)

    return by_repo


def latest_at_or_before(points: List[Snapshot], cutoff: datetime) -> Optional[Snapshot]:
    latest: Optional[Snapshot] = None
    for item in points:
        if item.ts <= cutoff:
            latest = item
        else:
            break
    return latest


def fmt_delta(previous: int, current: int) -> str:
    delta = current - previous
    delta_text = f"{delta:+,d}"
    if previous <= 0:
        return f"{delta_text} (n/a) to {current:,d}"
    pct = (delta / previous) * 100.0
    return f"{delta_text} ({pct:+.2f}%) to {current:,d}"


def metric_summary(metric: str, previous: int, current: int) -> str:
    return f"{metric} {fmt_delta(previous, current)}"


def build_source_section(
    title: str,
    repo_snapshots: Dict[str, List[Snapshot]],
    metrics: List[str],
    current_cutoff: datetime,
    previous_cutoff: datetime,
) -> Tuple[List[str], List[MetricDelta]]:
    lines: List[str] = [f"### {title}"]
    deltas: List[MetricDelta] = []

    repos = sorted(repo_snapshots.keys())
    if not repos:
        lines.append("- No repositories configured.")
        return lines, deltas

    for repo in repos:
        points = repo_snapshots[repo]
        current = latest_at_or_before(points, current_cutoff)
        previous = latest_at_or_before(points, previous_cutoff)

        if current is None:
            lines.append(f"- {repo}: no snapshot for current week.")
            continue

        if previous is None:
            metric_texts = [
                metric_summary(metric, 0, current.metrics[metric]) for metric in metrics
            ]
            lines.append(f"- {repo}: {'; '.join(metric_texts)} (new baseline).")
            for metric in metrics:
                deltas.append(
                    MetricDelta(
                        source=title,
                        repo=repo,
                        metric=metric,
                        previous=0,
                        current=current.metrics[metric],
                    )
                )
            continue

        metric_texts = []
        for metric in metrics:
            pv = previous.metrics[metric]
            cv = current.metrics[metric]
            metric_texts.append(metric_summary(metric, pv, cv))
            deltas.append(
                MetricDelta(
                    source=title,
                    repo=repo,
                    metric=metric,
                    previous=pv,
                    current=cv,
                )
            )

        lines.append(f"- {repo}: {'; '.join(metric_texts)}.")

    return lines, deltas


def build_highlights(deltas: List[MetricDelta]) -> List[str]:
    lines = ["### Highlights"]
    if not deltas:
        lines.append("- No deltas available.")
        return lines

    positives = [item for item in deltas if item.delta > 0]
    negatives = [item for item in deltas if item.delta < 0]
    pct_candidates = [item for item in positives if item.pct is not None]

    if positives:
        abs_best = max(positives, key=lambda item: item.delta)
        lines.append(
            f"- Biggest absolute increase: {abs_best.repo} {abs_best.source} {abs_best.metric} ({abs_best.delta:+,d})."
        )
    else:
        lines.append("- No positive growth detected this week.")

    if pct_candidates:
        rel_best = max(pct_candidates, key=lambda item: item.pct or -10**9)
        lines.append(
            f"- Highest relative growth: {rel_best.repo} {rel_best.source} {rel_best.metric} ({(rel_best.pct or 0.0):+.2f}%)."
        )
    else:
        lines.append("- Relative growth is n/a (missing baseline or zero previous values).")

    if negatives:
        lines.append(f"- Declines detected: {len(negatives)} metric(s).")
    else:
        lines.append("- No declines detected across tracked metrics.")

    return lines


def build_week_section(
    week_end: datetime,
    github_rows: Dict[str, List[Snapshot]],
    docker_rows: Dict[str, List[Snapshot]],
) -> Tuple[str, str]:
    previous_week_end = week_end - timedelta(days=7)
    iso_year, iso_week, _ = week_end.isocalendar()
    week_tag = f"{iso_year}-W{iso_week:02d}"
    week_end_date = week_end.date().isoformat()

    lines: List[str] = [f"## Week {week_tag} (ending {week_end_date}, UTC)"]

    docker_section, docker_deltas = build_source_section(
        title="Docker Hub",
        repo_snapshots=docker_rows,
        metrics=["pull_count", "star_count"],
        current_cutoff=week_end,
        previous_cutoff=previous_week_end,
    )
    github_section, github_deltas = build_source_section(
        title="GitHub",
        repo_snapshots=github_rows,
        metrics=["stars", "forks"],
        current_cutoff=week_end,
        previous_cutoff=previous_week_end,
    )

    lines.append("")
    lines.extend(docker_section)
    lines.append("")
    lines.extend(github_section)
    lines.append("")
    lines.extend(build_highlights(docker_deltas + github_deltas))

    return week_tag, "\n".join(lines)


def update_changelog(path: Path, week_tag: str, section: str) -> bool:
    intro = (
        "# Weekly Metrics Changelog\n\n"
        "Automated weekly digest for GitHub and Docker Hub metrics.\n"
    )

    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = intro

    header_re = re.compile(
        rf"^## Week {re.escape(week_tag)} \(ending \d{{4}}-\d{{2}}-\d{{2}}, UTC\)\n(?:.*\n)*?(?=^## Week |\Z)",
        flags=re.MULTILINE,
    )

    if header_re.search(text):
        return False

    first_week_idx = text.find("## Week ")
    if first_week_idx >= 0:
        new_text = text[:first_week_idx] + section + "\n\n" + text[first_week_idx:]
    else:
        new_text = text.rstrip() + "\n\n" + section + "\n"

    if new_text == text:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate weekly metrics changelog")
    parser.add_argument("--github", default=DEFAULT_GITHUB, help="GitHub CSV path")
    parser.add_argument("--docker", default=DEFAULT_DOCKER, help="Docker Hub CSV path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Weekly markdown path")
    parser.add_argument(
        "--week-end",
        default="",
        help="Week end date in YYYY-MM-DD (UTC Sunday recommended)",
    )
    args = parser.parse_args()

    week_end = (
        parse_week_end(args.week_end)
        if args.week_end.strip()
        else sunday_end_utc(datetime.now(timezone.utc).date())
    )

    github_rows = load_snapshots(Path(args.github), ["stars", "forks"])
    docker_rows = load_snapshots(Path(args.docker), ["pull_count", "star_count"])

    week_tag, section = build_week_section(
        week_end=week_end,
        github_rows=github_rows,
        docker_rows=docker_rows,
    )

    changed = update_changelog(Path(args.output), week_tag=week_tag, section=section)
    if changed:
        print(f"Updated {args.output} for {week_tag}")
    else:
        print("No changes")


if __name__ == "__main__":
    main()
