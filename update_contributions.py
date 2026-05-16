"""
update_contributions.py
Fetches all Pull Requests from GitHub and rebuilds README.md.
Auto-detects tech stacks from repo languages and PR title keywords.
Respects overrides.json for manual corrections.
Only includes PRs listed in overrides.json or created after AUTO_INCLUDE_FROM.
Runs on every push via GitHub Actions (update_contributions.yml).
"""

import os
import json
import requests
from datetime import datetime
from collections import defaultdict


# ── Configuration ─────────────────────────────────────────────────
GITHUB_USERNAME = "JohnMartin0301"
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

OVERRIDES_FILE = "overrides.json"

# PRs created on or after this date are auto-included
# PRs before this date must be listed in overrides.json to appear
AUTO_INCLUDE_FROM = "2026-05-09"


# ── Language Detection ────────────────────────────────────────────

# Framework keywords detected from PR title or repo name
# Maps keyword → (base language, display label)
FRAMEWORK_HINTS = {
    "flask":          ("Python",     "Python, Flask"),
    "django":         ("Python",     "Python, Django"),
    "fastapi":        ("Python",     "Python, FastAPI"),
    "react":          ("JavaScript", "React, JavaScript"),
    "vue":            ("JavaScript", "Vue, JavaScript"),
    "next":           ("JavaScript", "Next.js, React"),
    "express":        ("JavaScript", "Node.js, Express"),
    "node":           ("JavaScript", "Node.js"),
    "tailwind":       ("CSS",        "Tailwind CSS"),
    "bootstrap":      ("CSS",        "Bootstrap, CSS"),
    "docker":         ("Shell",      "Docker, Shell"),
    "github actions": ("YAML",       "GitHub Actions, YAML"),
    "shellcheck":     ("Shell",      "Shell, YAML"),
    "yamlint":        ("YAML",       "YAML, Shell"),
    "typescript":     ("TypeScript", "TypeScript"),
    "flutter":        ("Dart",       "Flutter, Dart"),
    "svelte":         ("JavaScript", "Svelte, JavaScript"),
}

# Display names for GitHub language identifiers
LANGUAGE_DISPLAY = {
    "JavaScript": "JavaScript", "TypeScript": "TypeScript",
    "Python": "Python",         "HTML": "HTML",
    "CSS": "CSS",               "Shell": "Shell",
    "YAML": "YAML",             "Go": "Go",
    "Rust": "Rust",             "Java": "Java",
    "Ruby": "Ruby",             "PHP": "PHP",
    "C": "C",                   "C++": "C++",
    "C#": "C#",                 "Dart": "Dart",
    "Kotlin": "Kotlin",         "Swift": "Swift",
}

# Always included in tech stack if present in the repo
ALWAYS_INCLUDE = {"CSS", "HTML", "YAML", "Shell"}

# Ignored — too common to be informative
IGNORE_LANGS = {"Dockerfile", "Makefile", "Batchfile", "PowerShell"}

# Used to sort PRs into technology sections
DEVOPS_TYPES = {"DevOps"}


# ── Contribution Type & Domain Keywords ───────────────────────────

TYPE_KEYWORDS = {
    "Bug Fix":       ["fix", "bug", "patch", "repair", "resolve", "broken", "off screen", "consistency"],
    "Feature":       ["feat", "add", "implement", "create", "new", "build", "introduce", "enhance", "search", "filter"],
    "Documentation": ["docs", "readme", "document", "comment", "guide"],
    "DevOps":        ["ci", "cd", "workflow", "action", "pipeline", "docker", "deploy", "lint", "shellcheck", "yamlint"],
    "Maintenance":   ["refactor", "chore", "cleanup", "update", "upgrade", "bump"],
    "Style/UI":      ["style", "css", "ui", "design", "responsive", "layout", "navbar", "footer", "toggle", "hamburger", "responsiveness"],
}

DOMAIN_KEYWORDS = {
    "Web Development": ["portfolio", "website", "landing", "page", "web", "frontend", "navbar",
                        "footer", "responsive", "css", "html", "tradebull", "betacity", "pendulum"],
    "Developer Tools": ["devops", "ci", "workflow", "python", "programs", "tool", "cli",
                        "script", "logging", "api", "article", "manager", "json", "game-changing"],
    "AI / ML":         ["ai", "ml", "model", "llm", "neural", "machine", "learning"],
    "Mobile":          ["mobile", "android", "ios", "flutter", "react-native"],
    "Database":        ["db", "database", "sql", "mongo", "postgres"],
}


# ── Helper Functions ──────────────────────────────────────────────

def load_overrides():
    # Reads manual corrections from overrides.json
    # Returns empty dict if the file doesn't exist
    if not os.path.exists(OVERRIDES_FILE):
        print(f"ℹ️  No {OVERRIDES_FILE} found — using auto-detection for everything.")
        return {}
    with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"✅ Loaded {len(data)} override(s) from {OVERRIDES_FILE}.")
    return data


def get_repo_languages(repo_full_name):
    # Fetches all languages used in the repo and their byte counts
    url = f"https://api.github.com/repos/{repo_full_name}/languages"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        return {}
    return response.json()


def build_tech_stack(repo_full_name, pr_title, raw_languages):
    # Builds a tech stack label from repo languages and PR title keywords
    # Combines primary language + secondary languages + framework hints
    if not raw_languages:
        return "Unknown"

    sorted_langs = sorted(raw_languages.items(), key=lambda x: x[1], reverse=True)
    langs        = [lang for lang, _ in sorted_langs if lang not in IGNORE_LANGS]

    if not langs:
        return "Unknown"

    primary  = langs[0]
    selected = [primary]

    for lang in langs[1:]:
        if lang in ALWAYS_INCLUDE and lang not in selected:
            selected.append(lang)

    display     = [LANGUAGE_DISPLAY.get(lang, lang) for lang in selected]
    search_text = f"{pr_title} {repo_full_name}".lower()

    for keyword, (base_lang, framework_label) in FRAMEWORK_HINTS.items():
        if keyword in search_text:
            if base_lang in display:
                display = [framework_label if d == base_lang else d for d in display]
            elif base_lang in selected:
                display.append(framework_label)
            break

    seen  = set()
    final = []
    for d in display:
        if d not in seen:
            seen.add(d)
            final.append(d)

    return ", ".join(final)


def detect_category(keyword_map, text):
    # Returns the first category whose keywords appear in the text
    text_lower = text.lower()
    for category, keywords in keyword_map.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "Other"


def get_all_prs():
    # Fetches all merged and open PRs authored by the configured user
    # Paginates through results 100 at a time
    print(f"🔍 Fetching PRs for @{GITHUB_USERNAME}...")
    all_prs = []

    for state in ["merged", "open"]:
        page  = 1
        query = f"is:pr+is:{state}+author:{GITHUB_USERNAME}"
        while True:
            url      = f"https://api.github.com/search/issues?q={query}&per_page=100&page={page}"
            response = requests.get(url, headers=HEADERS)

            if response.status_code == 403:
                print("⚠️  GitHub API rate limit hit. Wait a minute and try again.")
                break
            if response.status_code != 200:
                print(f"❌ API error {response.status_code}: {response.json().get('message')}")
                break

            items = response.json().get("items", [])
            if not items:
                break

            all_prs.extend(items)
            page += 1

    print(f"✅ Found {len(all_prs)} total PRs.\n")
    return all_prs


def parse_pr(pr, overrides, lang_cache):
    # Parses a raw PR into a structured dict
    # Detects tech stack, type, and domain
    # Applies overrides from overrides.json if available
    repo_full_name = pr["pull_request"]["url"].split("/repos/")[1].split("/pulls")[0]
    repo_name      = repo_full_name.split("/")[1]
    pr_number      = pr["number"]
    pr_title       = pr["title"]
    pr_url         = f"https://github.com/{repo_full_name}/pull/{pr_number}"
    override_key   = f"{repo_full_name}/pull/{pr_number}"

    if pr.get("pull_request", {}).get("merged_at"):
        status = "✅ Merged"
    elif pr.get("state") == "open":
        status = "🔄 Open PR"
    else:
        status = "❌ Closed"

    if repo_full_name not in lang_cache:
        lang_cache[repo_full_name] = get_repo_languages(repo_full_name)
    raw_languages = lang_cache[repo_full_name]

    tech_stack        = build_tech_stack(repo_full_name, pr_title, raw_languages)
    combined_text     = f"{repo_name} {pr_title}"
    contribution_type = detect_category(TYPE_KEYWORDS, pr_title)
    domain            = detect_category(DOMAIN_KEYWORDS, combined_text)

    result = {
        "repo_full_name": repo_full_name,
        "repo_name":      repo_name,
        "pr_number":      pr_number,
        "pr_title":       pr_title,
        "pr_url":         pr_url,
        "status":         status,
        "tech_stack":     tech_stack,
        "type":           contribution_type,
        "domain":         domain,
    }

    if override_key in overrides:
        override = overrides[override_key]
        if "tech_stack"  in override: result["tech_stack"] = override["tech_stack"]
        if "description" in override: result["pr_title"]   = override["description"]
        if "type"        in override: result["type"]        = override["type"]
        if "domain"      in override: result["domain"]      = override["domain"]
        print(f"  📝 Override applied for: {override_key}")

    return result


# ── README Generation ─────────────────────────────────────────────

def make_row(pr):
    # Formats a single PR as a markdown table row
    return (
        f"| [{pr['repo_full_name']}]({pr['pr_url']}) "
        f"| {pr['pr_title']} "
        f"| {pr['tech_stack']} "
        f"| {pr['status']} |"
    )


def table_section(title, pr_list):
    # Renders a titled markdown table section for a list of PRs
    if not pr_list:
        return ""
    rows = "\n".join(make_row(p) for p in pr_list)
    return f"""
### {title}

| Repository | What I Contributed | Tech Stack | Status |
|---|---|---|---|
{rows}
"""


def generate_readme(prs):
    # Groups PRs by technology, type, and domain
    # Returns the full README.md content as a string
    total    = len(prs)
    merged   = sum(1 for p in prs if "Merged" in p["status"])
    open_prs = sum(1 for p in prs if "Open"   in p["status"])
    today    = datetime.now().strftime("%B %Y")

    by_type   = defaultdict(list)
    by_domain = defaultdict(list)

    backend_prs  = []
    devops_prs   = []
    frontend_prs = []
    other_prs    = []

    for pr in prs:
        by_type[pr["type"]].append(pr)
        by_domain[pr["domain"]].append(pr)

        if pr["type"] in DEVOPS_TYPES or any(l in pr["tech_stack"] for l in ["Shell", "YAML", "GitHub Actions"]):
            devops_prs.append(pr)
        elif any(lang in pr["tech_stack"] for lang in ["JavaScript", "TypeScript", "HTML", "CSS", "React", "Vue", "Svelte"]) \
                or pr["type"] == "Style/UI":
            frontend_prs.append(pr)
        elif any(lang in pr["tech_stack"] for lang in ["Python", "Go", "Ruby", "Java", "Rust", "PHP", "Flask", "Django", "Node"]):
            backend_prs.append(pr)
        else:
            other_prs.append(pr)

    readme = f"""# ✈️ ContribPilot - Open Source Contribution Automation System

### 🗂️ my-contributions — A tracked & auto-updated index of my open-source contributions

> A running log of every open-source project I've contributed to, automatically organized and updated.
>
> ⚡ **This file is auto-generated** by a GitHub Actions workflow. Do not edit it manually.
> To correct a tech stack or description, edit `overrides.json` instead.

---

## 📌 Quick Stats

![Contributions](https://img.shields.io/badge/contributions-{total}-blue)
![Merged](https://img.shields.io/badge/merged-{merged}-brightgreen)
![Open](https://img.shields.io/badge/open-{open_prs}-yellow)

---

## 📁 Table of Contents

- [By Technology](#-by-technology)
- [By Contribution Type](#-by-contribution-type)
- [By Domain](#-by-domain)
- [Status Legend](#-status-legend)

---

## 🛠️ By Technology
{table_section("Backend", backend_prs)}
{table_section("DevOps / Tooling", devops_prs)}
{table_section("Frontend", frontend_prs)}
{table_section("Other", other_prs)}

---

## 🏷️ By Contribution Type
{"".join(table_section(t, p) for t, p in sorted(by_type.items()) if p)}

---

## 🌐 By Domain
{"".join(table_section(d, p) for d, p in sorted(by_domain.items()) if p)}

---

## 📊 Status Legend

| Badge | Meaning |
|---|---|
| ✅ Merged | PR was accepted and merged |
| 🔄 Open PR | Pull request is still open |
| 🚧 In Progress | Currently working on it |
| ❌ Closed | PR was closed without merging |

---

## 📬 Connect with Me

- GitHub: [@{GITHUB_USERNAME}](https://github.com/{GITHUB_USERNAME})

---

*Last auto-updated: {today}*
"""
    return readme


# ── Filtering ─────────────────────────────────────────────────────

def is_included(pr, overrides):
    # Returns True if the PR is in overrides.json
    # or was created on or after AUTO_INCLUDE_FROM
    repo_full_name = pr["pull_request"]["url"].split("/repos/")[1].split("/pulls")[0]
    pr_number      = pr["number"]
    override_key   = f"{repo_full_name}/pull/{pr_number}"

    if override_key in overrides:
        return True

    cutoff     = datetime.strptime(AUTO_INCLUDE_FROM, "%Y-%m-%d")
    created_at = pr.get("created_at", "")
    if created_at:
        pr_date = datetime.strptime(created_at[:10], "%Y-%m-%d")
        if pr_date >= cutoff:
            return True

    return False


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("🚀 Starting contributions update...\n")

    overrides = load_overrides()
    raw_prs   = get_all_prs()

    print(f"🔎 Filtering PRs (whitelisted OR created after {AUTO_INCLUDE_FROM})...")
    filtered_prs = [pr for pr in raw_prs if is_included(pr, overrides)]
    skipped      = len(raw_prs) - len(filtered_prs)
    print(f"  ✔ Keeping {len(filtered_prs)} PRs, skipping {skipped} old/uncurated PRs.\n")

    print("🔎 Parsing and categorizing PRs...")
    lang_cache = {}
    parsed_prs = []

    for pr in filtered_prs:
        try:
            parsed = parse_pr(pr, overrides, lang_cache)
            parsed_prs.append(parsed)
            print(f"  ✔ [{parsed['status']}] {parsed['repo_full_name']} #{parsed['pr_number']} — {parsed['tech_stack']}")
        except Exception as e:
            print(f"  ⚠ Skipped a PR due to error: {e}")

    print("\n📝 Generating README.md...")
    readme_content = generate_readme(parsed_prs)

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)

    print(f"\n✅ Done! README.md updated with {len(parsed_prs)} contributions.")


if __name__ == "__main__":
    main()
