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


def write_rows(path: Path, rows: List[Dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(CSV_HEADER)
        for row in rows:
            writer.writerow([row.get(col, "") for col in CSV_HEADER])


def roll_last_row_timestamp(
    existing: List[Dict[str, str]],
    pending_rows: List[List[str]],
    repo: str,
    now: str,
    stars: str,
    forks: str,
) -> bool:
    for row in reversed(pending_rows):
        if len(row) == len(CSV_HEADER) and row[1] == repo:
            row[0] = now
            row[2] = stars
            row[3] = forks
            return False

    for row in reversed(existing):
        if row.get("repo", "") == repo:
            row["ts"] = now
            row["stars"] = stars
            row["forks"] = forks
            return True

    return False


def should_append_row(
    repo_rows: List[Dict[str, str]],
    stars: str,
    forks: str,
) -> bool:
    if len(repo_rows) < 2:
        return True

    last = repo_rows[-1]
    prev = repo_rows[-2]
    same_as_last = last.get("stars", "") == stars and last.get("forks", "") == forks
    same_as_prev = prev.get("stars", "") == stars and prev.get("forks", "") == forks
    return not (same_as_last and same_as_prev)


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
    rewrote_existing = False
    rolled_rows = 0

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

        repo_existing = [row for row in existing if row.get("repo", "") == repo]
        repo_pending = [
            dict(zip(CSV_HEADER, row))
            for row in pending_rows
            if len(row) == len(CSV_HEADER) and row[1] == repo
        ]
        repo_rows = repo_existing + repo_pending

        if should_append_row(repo_rows, stars, forks):
            pending_rows.append([now, repo, stars, forks])
        else:
            if roll_last_row_timestamp(existing, pending_rows, repo, now, stars, forks):
                rewrote_existing = True
            rolled_rows += 1
            print(f"Rolled timestamp forward for {repo}")

    if rewrote_existing:
        pending_as_dicts = [
            dict(zip(CSV_HEADER, row))
            for row in pending_rows
            if len(row) == len(CSV_HEADER)
        ]
        write_rows(output_path, existing + pending_as_dicts)
    else:
        append_rows(output_path, pending_rows)

    if pending_rows or rolled_rows:
        print(f"Updated {output_path}: appended {len(pending_rows)} row(s), rolled {rolled_rows} row(s)")
    else:
        print("No updates")


if __name__ == "__main__":
    main()
