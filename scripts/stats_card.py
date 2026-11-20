#!/usr/bin/env python3
"""Write stats.svg: commits, PRs, reviews, issues, a grade, and the total stars of every
public repo the user contributed to (own repos + repos with their commits + repos with
their merged PRs), not only the repos they own.

Env: GITHUB_TOKEN (required), GH_USER (required), OUT (default stats.svg).
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com/graphql"
TOKEN = os.environ["GITHUB_TOKEN"]
USER = os.environ["GH_USER"]
OUT = os.environ.get("OUT", "stats.svg")

REPO_FIELDS = "nameWithOwner stargazerCount isPrivate isFork"


def gql(query, **variables):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)
    if body.get("errors"):
        sys.exit(f"GraphQL error: {body['errors']}")
    return body["data"]


def paginate(query, path, **variables):
    """Yield nodes of a connection; `path` is the key list from data to the connection."""
    cursor = None
    while True:
        conn = gql(query, cursor=cursor, **variables)
        for key in path:
            conn = conn[key]
        yield from conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return
        cursor = conn["pageInfo"]["endCursor"]


def profile():
    return gql(
        """query($login: String!) { user(login: $login) {
             createdAt followers { totalCount } pullRequests { totalCount } issues { totalCount } } }""",
        login=USER,
    )["user"]


def yearly(created_at):
    """All-time commits and reviews, plus every repo with a commit, one year per alias."""
    start = datetime.fromisoformat(created_at.replace("Z", "+00:00")).year
    now = datetime.now(timezone.utc)
    parts = []
    for year in range(start, now.year + 1):
        end = now.isoformat() if year == now.year else f"{year}-12-31T23:59:59Z"
        parts.append(
            f'y{year}: contributionsCollection(from: "{year}-01-01T00:00:00Z", to: "{end}") {{'
            " totalCommitContributions totalPullRequestReviewContributions"
            f" commitContributionsByRepository(maxRepositories: 100) {{ repository {{ {REPO_FIELDS} }} }} }}"
        )
    data = gql(f"query($login: String!) {{ user(login: $login) {{ {' '.join(parts)} }} }}", login=USER)["user"]
    commits = sum(y["totalCommitContributions"] for y in data.values())
    reviews = sum(y["totalPullRequestReviewContributions"] for y in data.values())
    repos = [c["repository"] for y in data.values() for c in y["commitContributionsByRepository"]]
    return commits, reviews, repos


def own_repos():
    return paginate(
        f"""query($login: String!, $cursor: String) {{ user(login: $login) {{
              repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100, after: $cursor) {{
                nodes {{ {REPO_FIELDS} }} pageInfo {{ hasNextPage endCursor }} }} }} }}""",
        ["user", "repositories"],
        login=USER,
    )


def merged_pr_repos():
    # Search returns at most 1000 results; enough for a profile card.
    for pr in paginate(
        f"""query($q: String!, $cursor: String) {{
              search(query: $q, type: ISSUE, first: 100, after: $cursor) {{
                nodes {{ ... on PullRequest {{ repository {{ {REPO_FIELDS} }} }} }}
                pageInfo {{ hasNextPage endCursor }} }} }}""",
        ["search"],
        q=f"author:{USER} is:pr is:merged",
    ):
        if pr:
            yield pr["repository"]


def grade(commits, prs, issues, reviews, stars, followers):
    """Percentile-style grade, same weights and medians as github-readme-stats."""
    exp_cdf = lambda x: 1 - 2**-x  # noqa: E731
    log_cdf = lambda x: x / (1 + x)  # noqa: E731
    terms = [
        (2, exp_cdf(commits / 250)),
        (3, exp_cdf(prs / 50)),
        (1, exp_cdf(issues / 25)),
        (1, exp_cdf(reviews / 2)),
        (4, log_cdf(stars / 50)),
        (1, log_cdf(followers / 10)),
    ]
    rank = 1 - sum(w * v for w, v in terms) / sum(w for w, _ in terms)
    for limit, level in zip([1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100], ["S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C"]):
        if rank * 100 <= limit:
            return level, rank * 100
    return "C", 100.0


def short(n):
    return f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


def svg(rows, level, percentile):
    row_svg = "\n".join(
        f'<g transform="translate(25,{60 + i * 24})"><text class="lbl">{label}</text>'
        f'<text class="val" x="215">{short(value)}</text></g>'
        for i, (label, value) in enumerate(rows)
    )
    circ = 2 * 3.14159 * 40
    filled = circ * (100 - percentile) / 100
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{70 + len(rows) * 24}" viewBox="0 0 420 {70 + len(rows) * 24}" role="img" aria-label="{USER} GitHub stats, grade {level}">
<style>
  text {{ font: 14px -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; fill: #24292f; }}
  .card {{ fill: #ffffff; stroke: #d0d7de; }}
  .title {{ font-size: 17px; font-weight: 600; fill: #0969da; }}
  .lbl {{ fill: #57606a; }}
  .val {{ font-weight: 600; }}
  .grade {{ font-size: 26px; font-weight: 700; }}
  .ring {{ fill: none; stroke: #d0d7de; stroke-width: 6; }}
  .arc {{ fill: none; stroke: #0969da; stroke-width: 6; stroke-linecap: round; }}
  @media (prefers-color-scheme: dark) {{
    text {{ fill: #e6edf3; }}
    .card {{ fill: #0d1117; stroke: #30363d; }}
    .title {{ fill: #58a6ff; }}
    .lbl {{ fill: #8b949e; }}
    .ring {{ stroke: #30363d; }}
    .arc {{ stroke: #58a6ff; }}
  }}
</style>
<rect class="card" x="0.5" y="0.5" width="419" height="{69 + len(rows) * 24}" rx="6"/>
<text class="title" x="25" y="35">{USER}'s GitHub Stats</text>
{row_svg}
<g transform="translate(340,{35 + len(rows) * 12})">
  <circle class="ring" r="40"/>
  <circle class="arc" r="40"
          stroke-dasharray="{filled:.1f} {circ:.1f}" transform="rotate(-90)"/>
  <text class="grade" text-anchor="middle" dominant-baseline="central">{level}</text>
</g>
</svg>
"""


def main():
    user = profile()
    commits, reviews, commit_repos = yearly(user["createdAt"])
    repos = {}
    for repo in [*own_repos(), *commit_repos, *merged_pr_repos()]:
        if not repo["isPrivate"] and not repo["isFork"]:
            repos[repo["nameWithOwner"]] = repo["stargazerCount"]
    stars = sum(repos.values())
    prs, issues, followers = user["pullRequests"]["totalCount"], user["issues"]["totalCount"], user["followers"]["totalCount"]
    level, percentile = grade(commits, prs, issues, reviews, stars, followers)

    rows = [
        ("Total stars (contributed repos)", stars),
        ("Commits", commits),
        ("Pull requests", prs),
        ("PR reviews", reviews),
        ("Issues", issues),
        ("Contributed to", len(repos)),
    ]
    with open(OUT, "w") as f:
        f.write(svg(rows, level, percentile))

    print(f"grade {level} (top {percentile:.1f}%)")
    for label, value in rows:
        print(f"{label}: {value}")
    print("repos counted for stars:")
    for name, count in sorted(repos.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>7}  {name}")


if __name__ == "__main__":
    main()
