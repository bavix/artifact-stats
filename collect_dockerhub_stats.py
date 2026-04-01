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

        pending_rows.append([now, repo, pull_count, star_count, last_updated])

    append_rows(output_path, pending_rows)
    if pending_rows:
        print(f"Appended {len(pending_rows)} rows to {output_path}")
    else:
        print("No updates")


if __name__ == "__main__":
    main()
