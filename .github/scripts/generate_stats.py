#!/usr/bin/env python3
"""Generate theme-adaptive GitHub profile statistics SVGs."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    repositories(
      first: 100
      after: $cursor
      ownerAffiliations: OWNER
      isFork: false
      privacy: PUBLIC
    ) {
      totalCount
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        stargazerCount
        languages(first: 10, orderBy: { field: SIZE, direction: DESC }) {
          edges {
            size
            node {
              name
              color
            }
          }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalRepositoryContributions
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            contributionCount
            contributionLevel
            date
          }
        }
      }
    }
  }
}
"""

THEMES: dict[str, dict[str, Any]] = {
    "dark": {
        "bg": "#0d1117",
        "panel": "#161b22",
        "border": "#30363d",
        "divider": "#21262d",
        "text_primary": "#f0f6fc",
        "text_secondary": "#c9d1d9",
        "text_muted": "#8b949e",
        "accent": "#58a6ff",
        "accent_alt": "#3fb950",
        "other_lang": "#6e7681",
        "heatmap": {
            "NONE": "#161b22",
            "FIRST_QUARTILE": "#0e4429",
            "SECOND_QUARTILE": "#006d32",
            "THIRD_QUARTILE": "#26a641",
            "FOURTH_QUARTILE": "#39d353",
        },
        "heatmap_stroke": "#21262d",
    },
    "light": {
        "bg": "#ffffff",
        "panel": "#f6f8fa",
        "border": "#d0d7de",
        "divider": "#d8dee4",
        "text_primary": "#1f2328",
        "text_secondary": "#24292f",
        "text_muted": "#57606a",
        "accent": "#0969da",
        "accent_alt": "#1a7f37",
        "other_lang": "#8c959f",
        "heatmap": {
            "NONE": "#ebedf0",
            "FIRST_QUARTILE": "#9be9a8",
            "SECOND_QUARTILE": "#40c463",
            "THIRD_QUARTILE": "#30a14e",
            "FOURTH_QUARTILE": "#216e39",
        },
        "heatmap_stroke": "#e1e4e8",
    },
}


def graphql_request(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    """Execute a GitHub GraphQL request and return the parsed data payload."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "blycr-profile-stats",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {detail}") from exc

    if body.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {body['errors']}")
    return body["data"]


def fetch_profile_stats(login: str, token: str) -> dict[str, Any]:
    """Fetch and aggregate GitHub metrics for the given user."""
    cursor: str | None = None
    repos: list[dict[str, Any]] = []
    user_data: dict[str, Any] | None = None

    while True:
        data = graphql_request(QUERY, {"login": login, "cursor": cursor}, token)
        user = data.get("user")
        if not user:
            raise RuntimeError(f"GitHub user '{login}' not found")
        user_data = user
        repo_conn = user["repositories"]
        repos.extend(repo_conn["nodes"])
        page_info = repo_conn["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    assert user_data is not None
    contrib = user_data["contributionsCollection"]
    calendar = contrib["contributionCalendar"]
    weeks = calendar["weeks"][-52:]

    lang_totals: dict[str, dict[str, Any]] = {}
    total_stars = 0
    for repo in repos:
        total_stars += repo["stargazerCount"]
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            color = edge["node"]["color"] or "#8b949e"
            entry = lang_totals.setdefault(name, {"size": 0, "color": color})
            entry["size"] += edge["size"]

    total_bytes = sum(v["size"] for v in lang_totals.values())
    sorted_langs = sorted(
        ((name, info["size"], info["color"]) for name, info in lang_totals.items()),
        key=lambda item: -item[1],
    )

    top_langs: list[dict[str, Any]] = []
    if total_bytes > 0:
        primary = sorted_langs[:5]
        for name, size, color in primary:
            top_langs.append({"name": name, "pct": size / total_bytes * 100.0, "color": color})
        remainder = sum(size for _, size, _ in sorted_langs[5:])
        if remainder > 0:
            top_langs.append({"name": "Other", "pct": remainder / total_bytes * 100.0, "color": None})

    span_start = weeks[0]["contributionDays"][0]["date"][:7] if weeks and weeks[0]["contributionDays"] else ""
    span_end = weeks[-1]["contributionDays"][-1]["date"][:7] if weeks and weeks[-1]["contributionDays"] else ""

    return {
        "login": login,
        "public_repos": user_data["repositories"]["totalCount"],
        "total_stars": total_stars,
        "total_contributions": calendar["totalContributions"],
        "total_commits": contrib["totalCommitContributions"],
        "total_prs": contrib["totalPullRequestContributions"],
        "languages": top_langs,
        "weeks": weeks,
        "span": f"{span_start} -> {span_end}" if span_start and span_end else "52W",
    }


def render_kpi_cards(stats: dict[str, Any], theme: dict[str, Any]) -> str:
    """Render the 2x2 KPI metric cards on the top-left."""
    kpis = [
        ("Contributions (1Y)", f"{stats['total_contributions']:,}", theme["accent"]),
        ("Commits (1Y)", f"{stats['total_commits']:,}", theme["accent_alt"]),
        ("Original Repos", f"{stats['public_repos']:,}", theme["text_primary"]),
        ("Stars · PRs", f"{stats['total_stars']:,} · {stats['total_prs']:,}", theme["text_primary"]),
    ]
    parts: list[str] = []
    for idx, (label, value, val_color) in enumerate(kpis):
        col = idx % 2
        row = idx // 2
        x = 24 + col * 200
        y = 64 + row * 61
        parts.append(
            f'<g transform="translate({x}, {y})">'
            f'<rect width="188" height="51" rx="8" fill="{theme["panel"]}" stroke="{theme["divider"]}" stroke-width="1"/>'
            f'<text x="14" y="20" fill="{theme["text_muted"]}" font-size="11" font-weight="500">{html.escape(label)}</text>'
            f'<text x="14" y="40" fill="{val_color}" font-size="17" font-weight="700" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace">{html.escape(value)}</text>'
            f"</g>"
        )
    return "\n  ".join(parts)


def render_language_panel(stats: dict[str, Any], theme: dict[str, Any]) -> str:
    """Render the language distribution panel on the top-right."""
    langs = stats["languages"]
    bar_width = 354.0
    segments: list[str] = []
    cursor_x = 0.0
    for idx, item in enumerate(langs):
        color = item["color"] or theme["other_lang"]
        seg_w = bar_width - cursor_x if idx == len(langs) - 1 else max(2.0, bar_width * (item["pct"] / 100.0))
        segments.append(
            f'<rect x="{cursor_x:.2f}" y="0" width="{seg_w:.2f}" height="9" fill="{color}"/>'
        )
        cursor_x += seg_w

    legend_items: list[str] = []
    for idx, item in enumerate(langs):
        col = idx % 2
        row = idx // 2
        lx = 20 + col * 182
        ly = 62 + row * 19
        pct_x = lx + (158 if col == 0 else 172)
        color = item["color"] or theme["other_lang"]
        label = f"{item['name']}"
        pct_text = f"{item['pct']:.1f}%"
        legend_items.append(
            f'<circle cx="{lx}" cy="{ly - 3.5}" r="4" fill="{color}"/>'
            f'<text x="{lx + 11}" y="{ly}" fill="{theme["text_secondary"]}" font-size="11.5" font-weight="500">{html.escape(label)}</text>'
            f'<text x="{pct_x}" y="{ly}" text-anchor="end" fill="{theme["text_muted"]}" font-size="11" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace">{pct_text}</text>'
        )

    return f"""<g transform="translate(432, 64)">
    <rect width="394" height="112" rx="8" fill="{theme['panel']}" stroke="{theme['divider']}" stroke-width="1"/>
    <text x="16" y="21" fill="{theme['text_muted']}" font-size="11" font-weight="600">Language Distribution (Original Public Repos)</text>
    <defs>
      <clipPath id="lang-bar-clip">
        <rect x="0" y="0" width="354" height="9" rx="4.5"/>
      </clipPath>
    </defs>
    <g transform="translate(20, 32)" clip-path="url(#lang-bar-clip)">
      {''.join(segments)}
    </g>
    {''.join(legend_items)}
  </g>"""


def render_heatmap_panel(stats: dict[str, Any], theme: dict[str, Any]) -> str:
    """Render the 52-week contribution heatmap panel across the bottom."""
    weeks = stats["weeks"]
    cells: list[str] = []
    for w_idx, week in enumerate(weeks):
        x = 18.0 + w_idx * 14.8
        for d_idx, day in enumerate(week["contributionDays"]):
            y = 34.0 + d_idx * 13.0
            level = day.get("contributionLevel", "NONE")
            fill = theme["heatmap"].get(level, theme["heatmap"]["NONE"])
            stroke_attr = f' stroke="{theme["heatmap_stroke"]}" stroke-width="0.6"' if level == "NONE" else ""
            cells.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="10.5" height="10.5" rx="2.2" fill="{fill}"{stroke_attr}/>')

    legend_levels = ["NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"]
    legend_rects = []
    for idx, level in enumerate(legend_levels):
        lx = 682 + idx * 14
        fill = theme["heatmap"][level]
        stroke_attr = f' stroke="{theme["heatmap_stroke"]}" stroke-width="0.6"' if level == "NONE" else ""
        legend_rects.append(f'<rect x="{lx}" y="12" width="10" height="10" rx="2" fill="{fill}"{stroke_attr}/>')

    return f"""<g transform="translate(24, 190)">
    <rect width="802" height="134" rx="8" fill="{theme['panel']}" stroke="{theme['divider']}" stroke-width="1"/>
    <text x="18" y="21" fill="{theme['text_muted']}" font-size="11" font-weight="600">52-Week Contribution Activity</text>
    <text x="674" y="21" text-anchor="end" fill="{theme['text_muted']}" font-size="10.5">Less</text>
    {''.join(legend_rects)}
    <text x="756" y="21" fill="{theme['text_muted']}" font-size="10.5">More</text>
    {''.join(cells)}
  </g>"""


def render_svg(stats: dict[str, Any], theme_name: str) -> str:
    """Render a complete themed stats dashboard SVG."""
    theme = THEMES[theme_name]
    kpis_svg = render_kpi_cards(stats, theme)
    langs_svg = render_language_panel(stats, theme)
    heatmap_svg = render_heatmap_panel(stats, theme)
    font_sans = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
    font_mono = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="850" height="340" viewBox="0 0 850 340" role="img" aria-label="GitHub Stats for {html.escape(stats['login'])}">
  <style>
    text {{ font-family: {font_sans}; }}
  </style>
  <rect x="0.5" y="0.5" width="849" height="339" rx="14" fill="{theme['bg']}" stroke="{theme['border']}" stroke-width="1"/>
  <text x="24" y="31" fill="{theme['text_primary']}" font-size="14" font-weight="600">GitHub Engineering Telemetry</text>
  <text x="826" y="31" text-anchor="end" fill="{theme['text_muted']}" font-size="11" font-family="{font_mono}">@{html.escape(stats['login'])} · {html.escape(stats['span'])}</text>
  <line x1="24" y1="47" x2="826" y2="47" stroke="{theme['divider']}" stroke-width="1"/>
  {kpis_svg}
  {langs_svg}
  {heatmap_svg}
</svg>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate GitHub profile stats SVGs.")
    parser.add_argument("--username", default=os.environ.get("GITHUB_USERNAME", "blycr"))
    parser.add_argument("--output-dir", default="assets")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN or GH_TOKEN environment variable is required.", file=sys.stderr)
        return 1

    stats = fetch_profile_stats(args.username, token)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for theme_name in ("dark", "light"):
        svg_path = out_dir / f"stats-{theme_name}.svg"
        svg_path.write_text(render_svg(stats, theme_name), encoding="utf-8")
        print(f"Wrote {svg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
