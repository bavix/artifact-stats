#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple


FONT_FAMILY = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"
AUTO_BASE_COLORS = (
    "#34d399",
    "#f59e0b",
    "#60a5fa",
    "#f87171",
    "#22d3ee",
    "#a78bfa",
    "#fb7185",
    "#4ade80",
)


@dataclass(frozen=True)
class Point:
    ts: datetime
    pull_count: int


def parse_ts(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def humanize_count(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.2f}k"
    return f"{int(round(value))}"


def parse_repo_list(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_hex_color(value: str) -> str:
    color = value.strip()
    if not color.startswith("#") or len(color) != 7:
        raise ValueError(f"Invalid color '{value}'. Expected #RRGGBB")
    int(color[1:], 16)
    return color.lower()


def adjust_color(hex_color: str, factor: float) -> str:
    color = parse_hex_color(hex_color)
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    r = max(0, min(255, int(r * factor)))
    g = max(0, min(255, int(g * factor)))
    b = max(0, min(255, int(b * factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


def repo_auto_color(repo: str) -> str:
    digest = hashlib.sha1(repo.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(AUTO_BASE_COLORS)
    return AUTO_BASE_COLORS[idx]


def parse_color_specs(values: List[str]) -> Dict[str, Tuple[str, str]]:
    result: Dict[str, Tuple[str, str]] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"Invalid --color format '{item}'. Use repo=#RRGGBB or repo=#RRGGBB,#RRGGBB")
        repo, raw_colors = item.split("=", 1)
        repo = repo.strip()
        parts = [part.strip() for part in raw_colors.split(",") if part.strip()]
        if not repo or not parts:
            raise ValueError(f"Invalid --color format '{item}'")
        if len(parts) == 1:
            start = parse_hex_color(parts[0])
            end = adjust_color(start, 0.72)
        elif len(parts) == 2:
            start = parse_hex_color(parts[0])
            end = parse_hex_color(parts[1])
        else:
            raise ValueError(f"Invalid --color format '{item}'")
        result[repo] = (start, end)
    return result


def build_color_map(repos: List[str], color_specs: Dict[str, Tuple[str, str]]) -> Dict[str, Tuple[str, str, str]]:
    color_map: Dict[str, Tuple[str, str, str]] = {}
    for repo in repos:
        if repo in color_specs:
            start, end = color_specs[repo]
        else:
            start = repo_auto_color(repo)
            end = adjust_color(start, 0.72)
        point = adjust_color(start, 1.15)
        color_map[repo] = (start, end, point)
    return color_map


def load_points(csv_path: Path) -> Dict[str, List[Point]]:
    points_by_repo: Dict[str, List[Point]] = {}

    with csv_path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        for row in reader:
            repo = row.get("repo", "").strip()
            if not repo:
                continue

            ts_raw = row.get("ts", "").strip()
            pull_count_raw = row.get("pull_count", "").strip()
            if not ts_raw or not pull_count_raw:
                continue

            if repo not in points_by_repo:
                points_by_repo[repo] = []

            points_by_repo[repo].append(
                Point(
                    ts=parse_ts(ts_raw),
                    pull_count=int(float(pull_count_raw)),
                )
            )

    if not points_by_repo:
        raise ValueError("No points found in CSV")

    for repo, repo_points in points_by_repo.items():
        repo_points.sort(key=lambda point: point.ts)
        if not repo_points:
            raise ValueError(f"No points found for {repo}")

    return points_by_repo


def build_svg(
    points_by_repo: Dict[str, List[Point]],
    repos: List[str],
    color_map: Dict[str, Tuple[str, str, str]],
    title: str,
    source_label: str,
) -> str:
    width, height = 1100, 560
    margin_left, margin_right = 90, 64
    margin_top, margin_bottom = 90, 92

    plot_left = margin_left
    plot_right = width - margin_right
    plot_top = margin_top
    plot_bottom = height - margin_bottom
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top

    all_points = [point for repo_points in points_by_repo.values() for point in repo_points]
    min_ts = min(point.ts for point in all_points)
    max_ts = max(point.ts for point in all_points)
    ts_span = max((max_ts - min_ts).total_seconds(), 1.0)

    max_count = max(point.pull_count for point in all_points)
    y_max = max(max_count * 1.1, 1)

    def x_coord(ts: datetime) -> float:
        return plot_left + ((ts - min_ts).total_seconds() / ts_span) * plot_width

    def y_coord(count: float) -> float:
        return plot_bottom - (count / y_max) * plot_height

    lines: List[str] = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">')
    lines.append("<defs>")
    lines.append('<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#0b1220"/><stop offset="100%" stop-color="#111827"/></linearGradient>')
    for idx, repo in enumerate(repos):
        start, end, _ = color_map[repo]
        lines.append(f'<linearGradient id="repo-{idx}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{start}"/><stop offset="100%" stop-color="{end}"/></linearGradient>')
    lines.append("</defs>")

    lines.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="url(#bg)" rx="16"/>')
    lines.append(f'<text x="{plot_left}" y="44" fill="#f9fafb" font-size="28" font-family="{FONT_FAMILY}" font-weight="700">{title}</text>')
    lines.append(f'<text x="{plot_left}" y="70" fill="#9ca3af" font-size="16" font-family="{FONT_FAMILY}">Docker Hub pull count by snapshot date</text>')

    y_ticks = 6
    for i in range(y_ticks + 1):
        ratio = i / y_ticks
        y = plot_top + plot_height * ratio
        value = y_max * (1 - ratio)
        lines.append(f'<line x1="{plot_left}" y1="{y:.2f}" x2="{plot_right}" y2="{y:.2f}" stroke="#1f2937" stroke-width="1"/>')
        lines.append(f'<text x="{plot_left - 10}" y="{y + 5:.2f}" text-anchor="end" fill="#9ca3af" font-size="12" font-family="{FONT_FAMILY}">{humanize_count(value)}</text>')

    lines.append(f'<line x1="{plot_left}" y1="{plot_bottom}" x2="{plot_right}" y2="{plot_bottom}" stroke="#4b5563" stroke-width="1.4"/>')
    lines.append(f'<line x1="{plot_left}" y1="{plot_top}" x2="{plot_left}" y2="{plot_bottom}" stroke="#4b5563" stroke-width="1.4"/>')

    tick_count = min(8, max(2, int(plot_width // 140)))
    for i in range(tick_count):
        ratio = i / (tick_count - 1) if tick_count > 1 else 0
        x = plot_left + ratio * plot_width
        tick_dt = min_ts + (max_ts - min_ts) * ratio
        anchor = "middle"
        if i == 0:
            anchor = "start"
        elif i == tick_count - 1:
            anchor = "end"
        lines.append(f'<line x1="{x:.2f}" y1="{plot_bottom}" x2="{x:.2f}" y2="{plot_bottom + 6}" stroke="#6b7280" stroke-width="1"/>')
        lines.append(f'<text x="{x:.2f}" y="{plot_bottom + 26}" text-anchor="{anchor}" fill="#9ca3af" font-size="12" font-family="{FONT_FAMILY}">{tick_dt.strftime("%Y-%m-%d")}</text>')

    for idx, repo in enumerate(repos):
        points = points_by_repo[repo]
        _, _, point_color = color_map[repo]
        gradient_id = f"repo-{idx}"

        polyline_points = " ".join(f"{x_coord(point.ts):.2f},{y_coord(point.pull_count):.2f}" for point in points)
        lines.append(f'<polyline fill="none" stroke="url(#{gradient_id})" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" points="{polyline_points}"/>')

        marker_step = max(1, len(points) // 24)
        for point_index, point in enumerate(points):
            if point_index % marker_step != 0 and point_index != len(points) - 1:
                continue
            lines.append(f'<circle cx="{x_coord(point.ts):.2f}" cy="{y_coord(point.pull_count):.2f}" r="3.8" fill="{point_color}" stroke="#111827" stroke-width="1"/>')

        latest = points[-1]
        lines.append(f'<text x="{x_coord(latest.ts) - 8:.2f}" y="{y_coord(latest.pull_count) - 10:.2f}" text-anchor="end" fill="{point_color}" font-size="12" font-family="{FONT_FAMILY}">{humanize_count(latest.pull_count)}</text>')

    legend_x = plot_left
    legend_y = height - 34
    for idx, repo in enumerate(repos):
        item_x = legend_x + idx * 190
        _, _, point_color = color_map[repo]
        lines.append(f'<rect x="{item_x:.2f}" y="{legend_y - 12:.2f}" width="14" height="14" fill="url(#repo-{idx})" rx="3"/>')
        lines.append(f'<text x="{item_x + 22:.2f}" y="{legend_y:.2f}" fill="{point_color}" font-size="13" font-family="{FONT_FAMILY}">{repo}</text>')

    lines.append(f'<text x="{plot_right:.2f}" y="{legend_y:.2f}" text-anchor="end" fill="#9ca3af" font-size="12" font-family="{FONT_FAMILY}">Source: {source_label}</text>')
    lines.append("</svg>")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Docker Hub download chart from CSV")
    parser.add_argument("--input", default="stats/dockerhub.csv", help="Input CSV path")
    parser.add_argument("--output", default="svg/gripmock-downloads.svg", help="Output SVG path")
    parser.add_argument("--title", required=True, help="Chart title")
    parser.add_argument("--repos", default="", help="Optional comma-separated repositories to draw")
    parser.add_argument(
        "--color",
        action="append",
        default=[],
        help="Repo color map entry: repo=#RRGGBB or repo=#RRGGBB,#RRGGBB (repeatable)",
    )
    parser.add_argument(
        "--colors",
        default="",
        help="Optional semicolon-separated color specs: repo=#RRGGBB,#RRGGBB;repo2=#RRGGBB,#RRGGBB",
    )
    parser.add_argument(
        "--source-label",
        default="stats/dockerhub.csv",
        help="Source label text displayed in SVG",
    )
    args = parser.parse_args()

    all_points_by_repo = load_points(input_path := Path(args.input))
    repos = parse_repo_list(args.repos)
    if repos:
        missing = [repo for repo in repos if repo not in all_points_by_repo]
        if missing:
            raise ValueError(f"Repositories not found in CSV: {', '.join(missing)}")
    else:
        repos = list(all_points_by_repo.keys())

    merged_colors = list(args.color)
    if args.colors.strip():
        merged_colors.extend([item.strip() for item in args.colors.split(";") if item.strip()])

    color_specs = parse_color_specs(merged_colors)
    color_map = build_color_map(repos, color_specs)

    output_path = Path(args.output)
    points_by_repo = {repo: all_points_by_repo[repo] for repo in repos}
    svg = build_svg(points_by_repo, repos, color_map, args.title, args.source_label)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")
    print(f"Saved chart to {output_path}")


if __name__ == "__main__":
    main()
