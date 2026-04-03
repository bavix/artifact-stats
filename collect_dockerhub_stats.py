#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_BASE = "https://hub.docker.com/v2"
CSV_HEADER = ["ts", "repo", "pull_count", "star_count", "last_updated"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def http_json(url: str, token: str = "", method: str = "GET", body: Dict | None = None) -> Dict:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, method=method, headers=headers, data=data)
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_token(username: str, pat: str) -> str:
    if not username or not pat:
        return ""

    try:
        data = http_json(
            f"{API_BASE}/auth/token",
            method="POST",
            body={"identifier": username, "secret": pat},
        )
    except (HTTPError, URLError):
        return ""

    token = data.get("access_token", "")
    return token if token and token != "null" else ""


def ensure_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(CSV_HEADER)


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
    pull_count: str,
    star_count: str,
    last_updated: str,
) -> bool:
    for row in reversed(pending_rows):
        if len(row) == len(CSV_HEADER) and row[1] == repo:
            row[0] = now
            row[2] = pull_count
            row[3] = star_count
            row[4] = last_updated
            return False

    for row in reversed(existing):
        if row.get("repo", "") == repo:
            row["ts"] = now
            row["pull_count"] = pull_count
            row["star_count"] = star_count
            row["last_updated"] = last_updated
            return True

    return False


def should_append_row(
    repo_rows: List[Dict[str, str]],
    pull_count: str,
    star_count: str,
) -> bool:
    if len(repo_rows) < 2:
        return True

    last = repo_rows[-1]
    prev = repo_rows[-2]
    same_as_last = (
        last.get("pull_count", "") == pull_count and last.get("star_count", "") == star_count
    )
    same_as_prev = (
        prev.get("pull_count", "") == pull_count and prev.get("star_count", "") == star_count
    )
    return not (same_as_last and same_as_prev)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Docker Hub repository stats into CSV")
    parser.add_argument("--output", default="stats/dockerhub.csv", help="Output CSV path")
    parser.add_argument(
        "--repos",
        default=os.getenv("DOCKERHUB_REPOS", ""),
        help="Comma-separated repo list",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    ensure_csv(output_path)

    existing = read_existing(output_path)
    today = utc_today()
    now = utc_now_iso()

    username = os.getenv("DOCKERHUB_USERNAME", "")
    pat = os.getenv("DOCKERHUB_PAT", "")
    token = get_token(username, pat)

    repos = [repo.strip() for repo in args.repos.split(",") if repo.strip()]
    if not repos:
        raise ValueError("No repositories provided. Use --repos or DOCKERHUB_REPOS")
    pending_rows: List[List[str]] = []
    rewrote_existing = False
    rolled_rows = 0

    for repo in repos:
        if "/" not in repo:
            raise ValueError(f"Invalid repo format: {repo}")

        has_repo = any(row.get("repo", "") == repo for row in existing)
        has_today = any(
            row.get("repo", "") == repo and row.get("ts", "").startswith(today)
            for row in existing
        )
        if has_today:
            print(f"Skip duplicate for {repo} on {today}")
            continue

        namespace, name = repo.split("/", 1)
        payload = http_json(f"{API_BASE}/namespaces/{namespace}/repositories/{name}", token=token)

        pull_count = str(payload.get("pull_count", 0) or 0)
        star_count = str(payload.get("star_count", 0) or 0)
        created_at = str(payload.get("date_registered", "") or "")
        last_updated = str(payload.get("last_updated", "") or "")

        if not has_repo and created_at:
            pending_rows.append([created_at, repo, "0", "0", ""])

        repo_existing = [row for row in existing if row.get("repo", "") == repo]
        repo_pending = [
            dict(zip(CSV_HEADER, row))
            for row in pending_rows
            if len(row) == len(CSV_HEADER) and row[1] == repo
        ]
        repo_rows = repo_existing + repo_pending

        if should_append_row(repo_rows, pull_count, star_count):
            pending_rows.append([now, repo, pull_count, star_count, last_updated])
        else:
            if roll_last_row_timestamp(
                existing,
                pending_rows,
                repo,
                now,
                pull_count,
                star_count,
                last_updated,
            ):
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
