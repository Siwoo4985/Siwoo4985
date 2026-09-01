"""Generate truthful, current statistics for the profile SVGs."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

API_ROOT = "https://api.github.com"
GRAPHQL_URL = f"{API_ROOT}/graphql"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
SVG_FILES = ("dark_mode.svg", "light_mode.svg")

USER_NAME = os.getenv("USER_NAME", "Siwoo4985")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("ACCESS_TOKEN", "")

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "Siwoo4985-profile-stats",
}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {GITHUB_TOKEN}"


def api_request(
    url: str,
    *,
    params: dict[str, object] | None = None,
    payload: dict[str, object] | None = None,
):
    """Return decoded JSON from GitHub and fail on invalid responses."""
    if params:
        url = f"{url}?{urlencode(params)}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = dict(HEADERS)
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def fetch_text(url: str) -> str:
    request = Request(url, headers=HEADERS)
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")


def api_get(path: str, **params):
    return api_request(f"{API_ROOT}{path}", params=params)


def fetch_public_stats(username: str) -> dict[str, int | str]:
    """Fetch profile and public repository statistics."""
    user = api_get(f"/users/{username}")
    repositories: list[dict] = []

    page = 1
    while True:
        batch = api_get(
            f"/users/{username}/repos",
            type="owner",
            sort="updated",
            per_page=100,
            page=page,
        )
        if not isinstance(batch, list):
            raise RuntimeError("GitHub returned an invalid repository response")
        repositories.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    showcase_candidates = [
        repo
        for repo in repositories
        if not repo.get("fork") and repo.get("name") != username
    ]
    best_repo = max(
        showcase_candidates,
        key=lambda repo: (
            int(repo.get("stargazers_count", 0)),
            int(repo.get("forks_count", 0)),
            repo.get("updated_at") or "",
        ),
        default=None,
    )

    return {
        "repos": int(user.get("public_repos", len(repositories))),
        "followers": int(user.get("followers", 0)),
        "stars": sum(int(repo.get("stargazers_count", 0)) for repo in repositories),
        "best_repo": best_repo.get("name", "-") if best_repo else "-",
    }


def fetch_yearly_contributions(username: str) -> int:
    """Fetch the public contribution-calendar total for the current year."""
    year = datetime.now(timezone.utc).year
    document = fetch_text(
        f"https://github.com/users/{username}/contributions"
        f"?from={year}-01-01&to={year}-12-31"
    )
    match = re.search(r"([\d,]+)\s+contributions?\s+in\s+\d{4}", document)
    if not match:
        raise RuntimeError("GitHub contribution total was not found")
    return int(match.group(1).replace(",", ""))


def get_stats(username: str) -> dict[str, int | str]:
    """Combine exact public profile statistics."""
    stats = fetch_public_stats(username)
    stats["contributions"] = fetch_yearly_contributions(username)
    stats["last_sync"] = datetime.now(timezone.utc).strftime("%Y-%m-%d UTC")
    return stats


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def has_class(element: ET.Element, class_name: str) -> bool:
    return class_name in element.get("class", "").split()


def find_row(root: ET.Element, row_id: str, label: str | None = None):
    for element in root.iter():
        if element.get("id") == row_id:
            return element
    if label:
        for element in root.iter():
            if local_name(element) == "text" and label in "".join(element.itertext()):
                element.set("id", row_id)
                return element
    return None


def find_value_node(row: ET.Element):
    for class_name in ("accent", "value"):
        for element in row.iter():
            if has_class(element, class_name):
                return element
    return None


def set_row_value(
    root: ET.Element,
    row_id: str,
    value: int | str,
    label: str | None = None,
):
    row = find_row(root, row_id, label)
    if row is None:
        raise RuntimeError(f"SVG row not found: {row_id}")
    value_node = find_value_node(row)
    if value_node is None:
        raise RuntimeError(f"SVG value node not found: {row_id}")
    value_node.text = f"{value:,}" if isinstance(value, int) else str(value)


def simplify_row(
    root: ET.Element,
    current_id: str,
    new_id: str,
    label: str,
    value: int | str,
):
    """Convert a legacy multi-value row into one truthful metric."""
    row = find_row(root, new_id)
    if row is None:
        row = find_row(root, current_id)
    if row is None:
        raise RuntimeError(f"SVG row not found: {current_id}")
    row.set("id", new_id)

    children = list(row)
    key_node = next((child for child in children if has_class(child, "key")), None)
    value_node = find_value_node(row)
    if key_node is None or value_node is None:
        raise RuntimeError(f"SVG row has an invalid structure: {current_id}")

    key_node.text = label
    key_node.tail = " "
    value_node.text = f"{value:,}" if isinstance(value, int) else str(value)
    value_node.tail = None
    for child in children:
        if child not in (key_node, value_node):
            row.remove(child)


def rewrite_labeled_row(
    root: ET.Element,
    current_label: str,
    new_label: str,
    new_value: str,
):
    row = find_row(root, "unused", current_label)
    if row is None:
        raise RuntimeError(f"SVG profile row not found: {current_label}")
    row.attrib.pop("id", None)
    key_node = next((element for element in row if has_class(element, "key")), None)
    value_node = next((element for element in row if has_class(element, "value")), None)
    if key_node is None or value_node is None:
        raise RuntimeError(f"SVG profile row has an invalid structure: {current_label}")
    key_node.text = new_label
    value_node.text = new_value


def sanitize_public_profile(root: ET.Element):
    """Keep the public card useful without exposing contact or device details."""
    replacements = (
        ("Hardware........", "Role............", "Student & Builder"),
        ("OS..............", "Focus...........", "Math, Science, Software"),
        ("Kernel..........", "Interests.......", "AI Systems, SwiftUI"),
        ("Use.AI..........", "Tools...........", "Python, Swift, VS Code"),
        ("Study.Lang.Prog.", "Learning........", "SwiftUI, Applied AI"),
        ("Lang.Computer...", "Web.............", "HTML, CSS, JavaScript"),
        ("Lang.Real.......", "Language........", "Korean"),
        ("IDE.............", "Environment.....", "Apple Ecosystem"),
        ("Email...........", "Building........", "Micro-Diffusion & more"),
        ("X...............", "Exploring.......", "AI systems and creative tools"),
        ("Instagram.......", "Profile.........", "github.com/Siwoo4985"),
    )
    for current_label, new_label, new_value in replacements:
        # Already-sanitized SVGs use the new label on later runs.
        if any(
            current_label in "".join(element.itertext()) for element in root.iter()
        ):
            rewrite_labeled_row(root, current_label, new_label, new_value)

    for element in root.iter():
        if local_name(element) == "text" and "CONTACT INFORMATION" in (element.text or ""):
            element.text = "┌─ CURRENT SIGNAL ──────────────────────────────┐"


def convert_legacy_rows(root: ET.Element, stats: dict[str, int | str]):
    """Replace fabricated commit/LOC estimates with exact public metrics."""
    simplify_row(
        root,
        "commit_row",
        "contrib_row",
        "Contribs (year).",
        stats["contributions"],
    )
    simplify_row(
        root,
        "loc_row",
        "sync_row",
        "Last Sync.......",
        stats["last_sync"],
    )


def update_svg_file(filepath: str, stats: dict[str, int | str]):
    path = Path(filepath)
    if not path.is_file():
        raise FileNotFoundError(filepath)

    ET.register_namespace("", SVG_NAMESPACE)
    tree = ET.parse(path)
    root = tree.getroot()

    best_repo = str(stats["best_repo"])
    if len(best_repo) > 28:
        best_repo = f"{best_repo[:27]}…"

    set_row_value(root, "best_repo_row", best_repo, "Best Repo")
    set_row_value(root, "repo_row", stats["repos"])
    set_row_value(root, "star_row", stats["stars"])
    set_row_value(root, "follower_row", stats["followers"])
    convert_legacy_rows(root, stats)
    sanitize_public_profile(root)

    tree.write(path, encoding="utf-8", xml_declaration=True)
    ET.parse(path)
    print(f"[Success] Updated and validated {filepath}")


def main() -> None:
    print(f"Fetching GitHub statistics for {USER_NAME}...")
    stats = get_stats(USER_NAME)
    print(f"Fetched statistics: {stats}")
    for svg_file in SVG_FILES:
        update_svg_file(svg_file, stats)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[Error] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
