#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from urllib.error import HTTPError
from urllib.request import Request, urlopen


API_BASE = "https://api.github.com"
CSV_HEADER = ["ts", "repo", "stars", "forks"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def http_json(url: str, token: str = "", accept: str = "application/vnd.github+json"):
    headers = {
        "Accept": accept,
        "User-Agent": "artifact-stats-bot",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, method="GET", headers=headers)
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def ensure_csv(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(CSV_HEADER)
    return True


def read_existing(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def append_rows(path: Path, rows: List[List[str]]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8", newline="") as target:
        writer = csv.writer(target)
        writer.writerows(rows)


def parse_repos(value: str) -> List[str]:
    repos = [repo.strip() for repo in value.split(",") if repo.strip()]
    if not repos:
        raise ValueError("No repositories provided. Use --repos")
    return repos


def fetch_paged(url: str, token: str, accept: str) -> List[Dict]:
    page = 1
    per_page = 100
    out: List[Dict] = []
    while True:
        chunk = http_json(f"{url}{'&' if '?' in url else '?'}per_page={per_page}&page={page}", token=token, accept=accept)
        if not isinstance(chunk, list) or not chunk:
            break
        out.extend(chunk)
        if len(chunk) < per_page:
            break
        page += 1
    return out


def day(value: str) -> str:
    return value[:10]


def build_history_rows(repo: str, created_at: str, token: str) -> List[List[str]]:
    star_events = fetch_paged(
        f"{API_BASE}/repos/{repo}/stargazers",
        token=token,
        accept="application/vnd.github.star+json",
    )
    fork_events = fetch_paged(
        f"{API_BASE}/repos/{repo}/forks?sort=oldest",
        token=token,
        accept="application/vnd.github+json",
    )

    stars_by_day: Dict[str, int] = {}
    forks_by_day: Dict[str, int] = {}

    for event in star_events:
        ts = str(event.get("starred_at", "") or "")
        if not ts:
            continue
        d = day(ts)
        stars_by_day[d] = stars_by_day.get(d, 0) + 1

    for event in fork_events:
        ts = str(event.get("created_at", "") or "")
        if not ts:
            continue
        d = day(ts)
        forks_by_day[d] = forks_by_day.get(d, 0) + 1

    rows: List[List[str]] = [[created_at, repo, "0", "0"]]
    days = sorted(set(stars_by_day) | set(forks_by_day))
    stars = 0
    forks = 0
    for d in days:
        stars += stars_by_day.get(d, 0)
        forks += forks_by_day.get(d, 0)
        rows.append([f"{d}T00:00:00Z", repo, str(stars), str(forks)])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect GitHub stars/forks into CSV")
    parser.add_argument("--output", default="stats/github.csv", help="Output CSV path")
    parser.add_argument("--repos", default=os.getenv("GITHUB_REPOS", ""), help="Comma-separated repo list")
    args = parser.parse_args()

    output_path = Path(args.output)
    created_new = ensure_csv(output_path)

    existing = read_existing(output_path)
    today = utc_today()
    now = utc_now_iso()
    token = os.getenv("GITHUB_TOKEN", "") or os.getenv("GH_TOKEN", "")
    repos = parse_repos(args.repos)

    pending_rows: List[List[str]] = []

    for repo in repos:
        if "/" not in repo:
            raise ValueError(f"Invalid repo format: {repo}")

        has_repo = any(row.get("repo", "") == repo for row in existing)
        has_today = any(row.get("repo", "") == repo and row.get("ts", "").startswith(today) for row in existing)

        try:
            info = http_json(f"{API_BASE}/repos/{repo}", token=token)
        except HTTPError as exc:
            if exc.code == 404:
                print(f"Skip missing repo: {repo}")
                continue
            raise

        stars = str(info.get("stargazers_count", 0) or 0)
        forks = str(info.get("forks_count", 0) or 0)
        created_at = str(info.get("created_at", "") or "")

        if created_new and not has_repo and created_at:
            history = build_history_rows(repo, created_at, token)
            pending_rows.extend(history)

        if has_today:
            print(f"Skip duplicate for {repo} on {today}")
            continue

        pending_rows.append([now, repo, stars, forks])

    append_rows(output_path, pending_rows)
    if pending_rows:
        print(f"Appended {len(pending_rows)} rows to {output_path}")
    else:
        print("No updates")


if __name__ == "__main__":
    main()
