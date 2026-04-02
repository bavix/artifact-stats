# artifact-stats

Collect Docker Hub and GitHub stats into CSV files and render SVG charts.

## What this repo does

- appends Docker Hub snapshots to `stats/dockerhub.csv`
- appends GitHub snapshots (stars/forks) to `stats/github.csv`
- generates SVG charts into `svg/`
- generates a weekly markdown changelog in `WEEKLY.md`
- runs automatically via GitHub Actions and commits updates

CSV compaction:

- collectors automatically collapse flat runs of 3+ identical snapshots per repo/metric into 2 boundary points (first + latest), reducing noise and chart markers

## Scripts

- `collect_dockerhub_stats.py` - fetches Docker Hub stats and appends rows
- `generate_dockerhub_chart.py` - renders Docker Hub pull chart SVG
- `collect_github_stats.py` - fetches GitHub stars/forks and appends rows
- `generate_github_charts.py` - generates 2 SVG files (stars, forks)
- `weekly_log.py` - generates weekly report section in `WEEKLY.md`

## Local usage

Collect stats:

```bash
python3 collect_dockerhub_stats.py \
  --output stats/dockerhub.csv \
  --repos "bavix/gripmock,tkpd/gripmock"
```

Generate chart:

```bash
python3 generate_dockerhub_chart.py \
  --input stats/dockerhub.csv \
  --output svg/gripmock-downloads.svg \
  --title "GripMock Docker Hub Downloads" \
  --source-label "https://github.com/bavix/artifact-stats/blob/master/stats/dockerhub.csv" \
  --color "bavix/gripmock=#34d399,#059669" \
  --color "tkpd/gripmock=#f59e0b,#d97706"
```

Generate GitHub charts (2 SVG files):

```bash
python3 collect_github_stats.py \
  --output stats/github.csv \
  --repos "bavix/gripmock,tokopedia/gripmock"

python3 generate_github_charts.py \
  --input stats/github.csv \
  --output-dir svg \
  --output-prefix github \
  --title "GripMock GitHub Metrics" \
  --source-label "https://github.com/bavix/artifact-stats/blob/master/stats/github.csv" \
  --colors "bavix/gripmock=#34d399,#059669;tokopedia/gripmock=#f59e0b,#d97706"
```

Generate weekly report:

```bash
python3 weekly_log.py \
  --github stats/github.csv \
  --docker stats/dockerhub.csv \
  --output WEEKLY.md
```

Behavior:

- if `WEEKLY.md` does not exist, script creates it
- if weekly report for the same ISO week already exists, script skips
- otherwise the new section is inserted at the top (latest first)

`collect_github_stats.py` skips repositories that return `404` from GitHub API (for example, if the repo does not exist or is private and token has no access).
History for stars/forks is backfilled only when the CSV file does not exist yet (first run).

Color notes:

- `--color repo=#RRGGBB,#RRGGBB` sets gradient start/end
- `--color repo=#RRGGBB` sets only base, end color is auto-derived
- if `--color` is omitted, colors are auto-assigned deterministically per repo
- `--repos` is optional for chart generation; by default all repos from the CSV are rendered

## Configure for your own repos

Update `.github/workflows/dockerhub-stats.yml` job env values:

- `DOCKERHUB_REPOS` - comma-separated repos
- `DOCKERHUB_CHART_TITLE` - SVG title
- `DOCKERHUB_CHART_COLORS` - optional `repo=#RRGGBB,#RRGGBB;repo2=#RRGGBB,#RRGGBB`
- `STATS_CSV` / `STATS_SVG` - output files
- `DOCKERHUB_SOURCE_URL` - source label shown in SVG

Update `.github/workflows/github-stats.yml` job env values:

- `GITHUB_REPOS` - comma-separated repos
- `GITHUB_STATS_CSV` - output CSV path
- `GITHUB_SVG_DIR` / `GITHUB_SVG_PREFIX` - output SVG files
- `GITHUB_CHART_TITLE` - base title for all GitHub SVG charts
- `GITHUB_CHART_COLORS` - optional `repo=#RRGGBB,#RRGGBB;repo2=#RRGGBB,#RRGGBB`
- `GITHUB_SOURCE_URL` - source label shown in SVG

Also set secrets if you need authenticated Docker Hub API access:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_PAT`
- `BOT_TOKEN`, `GPG_BOT`, `GPG_PASSPHRASE`, `GPG_FINGERPRINT` for signed auto-commits

Weekly automation is configured in `.github/workflows/weekly.yml`.
