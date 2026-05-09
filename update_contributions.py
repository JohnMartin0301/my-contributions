"""
update_contributions.py
------------------------
This script does 4 things:
  1. Talks to the GitHub API to fetch all your Pull Requests (merged + open)
  2. Reads the ACTUAL languages used in each repo (not just the primary one)
  3. Checks overrides.json for any manual corrections you've made
  4. Rebuilds your README.md with everything categorized and formatted

It runs automatically every day via GitHub Actions (see update-contributions.yml),
but you can also run it manually on your own computer anytime.
"""

import os
import json
import requests
from datetime import datetime
from collections import defaultdict


# ─────────────────────────────────────────────
# STEP 1: CONFIGURATION
# ─────────────────────────────────────────────

GITHUB_USERNAME = "JohnMartin0301"

# Automatically provided by GitHub Actions.
# To run locally: export GITHUB_TOKEN=your_personal_access_token
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

# Path to the overrides file (lives in the same repo)
OVERRIDES_FILE = "overrides.json"


# ─────────────────────────────────────────────
# STEP 2: LANGUAGE DETECTION RULES
#
# GitHub repos can use many languages at once.
# We fetch ALL of them and use these rules to
# build a clean "Tech Stack" label.
# ─────────────────────────────────────────────

# These are framework/library hints we detect from PR titles or repo names.
# They override or extend the raw language GitHub reports.
# Example: GitHub says "Python" but the PR title says "flask" → we show "Python, Flask"
FRAMEWORK_HINTS = {
    "flask":        ("Python", "Python, Flask"),
    "django":       ("Python", "Python, Django"),
    "fastapi":      ("Python", "Python, FastAPI"),
    "react":        ("JavaScript", "React, JavaScript"),
    "vue":          ("JavaScript", "Vue, JavaScript"),
    "next":         ("JavaScript", "Next.js, React"),
    "express":      ("JavaScript", "Node.js, Express"),
    "node":         ("JavaScript", "Node.js"),
    "tailwind":     ("CSS",        "Tailwind CSS"),
    "bootstrap":    ("CSS",        "Bootstrap, CSS"),
    "docker":       ("Shell",      "Docker, Shell"),
    "github actions": ("YAML",     "GitHub Actions, YAML"),
    "shellcheck":   ("Shell",      "Shell, YAML"),
    "yamlint":      ("YAML",       "YAML, Shell"),
    "typescript":   ("TypeScript", "TypeScript"),
    "flutter":      ("Dart",       "Flutter, Dart"),
    "svelte":       ("JavaScript", "Svelte, JavaScript"),
}

# How we label GitHub's language names in the README
LANGUAGE_DISPLAY = {
    "JavaScript": "JavaScript",
    "TypeScript": "TypeScript",
    "Python":     "Python",
    "HTML":       "HTML",
    "CSS":        "CSS",
    "Shell":      "Shell",
    "YAML":       "YAML",
    "Go":         "Go",
    "Rust":       "Rust",
    "Java":       "Java",
    "Ruby":       "Ruby",
    "PHP":        "PHP",
    "C":          "C",
    "C++":        "C++",
    "C#":         "C#",
    "Dart":       "Dart",
    "Kotlin":     "Kotlin",
    "Swift":      "Swift",
}

# Which languages are "secondary" and we always include if present
# e.g. HTML repos almost always have CSS too
ALWAYS_INCLUDE = {"CSS", "HTML", "YAML", "Shell"}

# Which languages to ignore — they're too common and not informative
IGNORE_LANGS = {"Dockerfile", "Makefile", "Batchfile", "PowerShell"}

# Languages that signal a repo belongs to Frontend / Backend / DevOps
FRONTEND_LANGS  = {"JavaScript", "TypeScript", "HTML", "CSS", "Dart"}
BACKEND_LANGS   = {"Python", "Go", "Ruby", "Java", "Rust", "PHP", "C", "C++", "C#"}
DEVOPS_LANGS    = {"Shell", "YAML"}
DEVOPS_TYPES    = {"DevOps"}


# ─────────────────────────────────────────────
# STEP 3: CONTRIBUTION TYPE & DOMAIN KEYWORDS
# ─────────────────────────────────────────────

TYPE_KEYWORDS = {
    "Bug Fix":       ["fix", "bug", "patch", "repair", "resolve", "broken", "off screen", "consistency"],
    "Feature":       ["feat", "add", "implement", "create", "new", "build", "introduce", "enhance", "search", "filter"],
    "Documentation": ["docs", "readme", "document", "comment", "guide"],
    "DevOps":        ["ci", "cd", "workflow", "action", "pipeline", "docker", "deploy", "lint", "shellcheck", "yamlint"],
    "Maintenance":   ["refactor", "chore", "cleanup", "update", "upgrade", "bump"],
    "Style/UI":      ["style", "css", "ui", "design", "responsive", "layout", "navbar", "footer", "toggle", "hamburger", "responsiveness"],
}

DOMAIN_KEYWORDS = {
    "Web Development":  ["portfolio", "website", "landing", "page", "web", "frontend", "navbar",
                         "footer", "responsive", "css", "html", "tradebull", "betacity", "pendulum"],
    "Developer Tools":  ["devops", "ci", "workflow", "python", "programs", "tool", "cli",
                         "script", "logging", "api", "article", "manager", "json", "game-changing"],
    "AI / ML":          ["ai", "ml", "model", "llm", "neural", "machine", "learning"],
    "Mobile":           ["mobile", "android", "ios", "flutter", "react-native"],
    "Database":         ["db", "database", "sql", "mongo", "postgres"],
}


# ─────────────────────────────────────────────
# STEP 4: HELPER FUNCTIONS
# ─────────────────────────────────────────────

def load_overrides():
    """
    Reads overrides.json from the repo root.
    This file lets you manually correct tech stacks, descriptions, or categories
    for any specific PR. If the file doesn't exist yet, returns an empty dict.

    Example overrides.json:
    {
      "devanprigent/article-manager/pull/23": {
        "tech_stack": "Python, Flask",
        "description": "Add structured logging throughout the API"
      }
    }
    """
    if not os.path.exists(OVERRIDES_FILE):
        print(f"ℹ️  No {OVERRIDES_FILE} found — using auto-detection for everything.")
        return {}
    with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"✅ Loaded {len(data)} override(s) from {OVERRIDES_FILE}.")
    return data


def get_repo_languages(repo_full_name):
    """
    Asks GitHub: 'What languages does this repo actually use?'
    GitHub returns a dict like: {"HTML": 5200, "CSS": 3100, "JavaScript": 800}
    The numbers are bytes of code — bigger = more of that language.

    We use this to build a proper tech stack label, e.g. "HTML, CSS, JavaScript"
    instead of just the single primary language.
    """
    url = f"https://api.github.com/repos/{repo_full_name}/languages"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        return {}
    return response.json()  # e.g. {"HTML": 5200, "CSS": 3100}


def build_tech_stack(repo_full_name, pr_title, raw_languages):
    """
    Combines 3 sources of info to produce the best possible tech stack label:

    1. raw_languages — actual language bytes from the GitHub API
       e.g. {"HTML": 5200, "CSS": 3100, "JavaScript": 800}

    2. pr_title — we scan it for framework keywords
       e.g. "feat(backend): add flask logging" → we add Flask

    3. ALWAYS_INCLUDE — secondary langs like CSS/HTML are included if present

    Returns a clean string like "HTML, CSS, JavaScript" or "Python, Flask"
    """
    if not raw_languages:
        return "Unknown"

    # Sort languages by bytes used (most used first)
    sorted_langs = sorted(raw_languages.items(), key=lambda x: x[1], reverse=True)

    # Filter out noise languages
    langs = [lang for lang, _ in sorted_langs if lang not in IGNORE_LANGS]

    if not langs:
        return "Unknown"

    # Start with the top language (the main one)
    primary = langs[0]
    selected = [primary]

    # Always include important secondary languages if present
    for lang in langs[1:]:
        if lang in ALWAYS_INCLUDE and lang not in selected:
            selected.append(lang)

    # Convert to display names
    display = [LANGUAGE_DISPLAY.get(lang, lang) for lang in selected]

    # Check PR title and repo name for framework hints
    # This detects things like "flask", "react", "docker" in the PR title
    search_text = f"{pr_title} {repo_full_name}".lower()
    for keyword, (base_lang, framework_label) in FRAMEWORK_HINTS.items():
        if keyword in search_text:
            # If the base language is already in our stack, replace with framework label
            if base_lang in display:
                display = [framework_label if d == base_lang else d for d in display]
            elif base_lang in selected:
                display.append(framework_label)
            break  # Only apply the first matching framework hint

    # Remove duplicates while preserving order
    seen = set()
    final = []
    for d in display:
        if d not in seen:
            seen.add(d)
            final.append(d)

    return ", ".join(final)


def detect_category(keyword_map, text):
    """
    Checks if any keyword from each category appears in the given text.
    Returns the first matching category name, or 'Other' if nothing matches.
    """
    text_lower = text.lower()
    for category, keywords in keyword_map.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "Other"


def get_all_prs():
    """
    Fetches ALL pull requests you've made (both merged and open)
    by calling the GitHub Search API.

    GitHub returns results 100 at a time (paginated),
    so we keep looping until there are no more pages.
    """
    print(f"🔍 Fetching PRs for @{GITHUB_USERNAME}...")
    all_prs = []

    for state in ["merged", "open"]:
        page = 1
        query = f"is:pr+is:{state}+author:{GITHUB_USERNAME}"
        while True:
            url = f"https://api.github.com/search/issues?q={query}&per_page=100&page={page}"
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
    """
    Processes one raw PR from the GitHub API into a clean structured dict.

    Steps:
    1. Extract repo name, PR number, title, URL, and status
    2. Fetch the repo's actual languages (cached so we don't re-fetch)
    3. Build the tech stack string
    4. Auto-detect contribution type and domain
    5. Apply any overrides from overrides.json

    lang_cache is a dict we pass around to avoid calling the GitHub API
    multiple times for the same repo (saves time and API quota).
    """
    repo_full_name = pr["pull_request"]["url"].split("/repos/")[1].split("/pulls")[0]
    repo_name      = repo_full_name.split("/")[1]
    pr_number      = pr["number"]
    pr_title       = pr["title"]
    pr_url         = f"https://github.com/{repo_full_name}/pull/{pr_number}"
    override_key   = f"{repo_full_name}/pull/{pr_number}"

    # Determine status
    if pr.get("pull_request", {}).get("merged_at"):
        status = "✅ Merged"
    elif pr.get("state") == "open":
        status = "🔄 Open PR"
    else:
        status = "❌ Closed"

    # Fetch repo languages — use cache to avoid duplicate API calls
    if repo_full_name not in lang_cache:
        lang_cache[repo_full_name] = get_repo_languages(repo_full_name)
    raw_languages = lang_cache[repo_full_name]

    # Build tech stack from real language data + framework hints
    tech_stack = build_tech_stack(repo_full_name, pr_title, raw_languages)

    # Auto-detect type and domain
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

    # Apply overrides — these win over everything else
    # If you've set something in overrides.json, it will always be used.
    if override_key in overrides:
        override = overrides[override_key]
        if "tech_stack"   in override: result["tech_stack"]  = override["tech_stack"]
        if "description"  in override: result["pr_title"]    = override["description"]
        if "type"         in override: result["type"]         = override["type"]
        if "domain"       in override: result["domain"]       = override["domain"]
        print(f"  📝 Override applied for: {override_key}")

    return result


# ─────────────────────────────────────────────
# STEP 5: README GENERATION
# ─────────────────────────────────────────────

def make_row(pr):
    """Formats one PR as a single markdown table row."""
    return (
        f"| [{pr['repo_full_name']}]({pr['pr_url']}) "
        f"| {pr['pr_title']} "
        f"| {pr['tech_stack']} "
        f"| {pr['status']} |"
    )


def table_section(title, pr_list):
    """
    Renders a titled section with a markdown table.
    Returns empty string if there are no PRs for this section.
    """
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
    """
    Groups all PRs into Frontend / Backend / DevOps / Other,
    then by contribution type, then by domain.
    Assembles and returns the full README.md as a string.
    """
    total    = len(prs)
    merged   = sum(1 for p in prs if "Merged" in p["status"])
    open_prs = sum(1 for p in prs if "Open"   in p["status"])
    today    = datetime.now().strftime("%B %Y")

    by_type   = defaultdict(list)
    by_domain = defaultdict(list)

    frontend_prs = []
    backend_prs  = []
    devops_prs   = []
    other_prs    = []

    for pr in prs:
        by_type[pr["type"]].append(pr)
        by_domain[pr["domain"]].append(pr)

        # Categorize into technology buckets
        # DevOps type or Shell/YAML stack → DevOps section
        if pr["type"] in DEVOPS_TYPES or any(l in pr["tech_stack"] for l in ["Shell", "YAML", "GitHub Actions"]):
            devops_prs.append(pr)
        # Frontend languages or Style/UI type → Frontend section
        elif any(lang in pr["tech_stack"] for lang in ["JavaScript", "TypeScript", "HTML", "CSS", "React", "Vue", "Svelte"]) \
                or pr["type"] == "Style/UI":
            frontend_prs.append(pr)
        # Backend languages → Backend section
        elif any(lang in pr["tech_stack"] for lang in ["Python", "Go", "Ruby", "Java", "Rust", "PHP", "Flask", "Django", "Node"]):
            backend_prs.append(pr)
        else:
            other_prs.append(pr)

    readme = f"""# 🗂️ my-contributions

> A curated index of open-source repositories I've contributed to — organized by technology, contribution type, and domain.
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
{table_section("Frontend", frontend_prs)}
{table_section("Backend", backend_prs)}
{table_section("DevOps / Tooling", devops_prs)}
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

## 🚀 About This Repo

This repo contains **no source code** — it's a living index of my open-source contributions.
Each link goes directly to the Pull Request on the original repository.

---

## 📬 Connect with Me

- GitHub: [@{GITHUB_USERNAME}](https://github.com/{GITHUB_USERNAME})

---

*Last auto-updated: {today}*
"""
    return readme


# ─────────────────────────────────────────────
# STEP 6: MAIN — runs everything in order
# ─────────────────────────────────────────────

def main():
    print("🚀 Starting contributions update...\n")

    # Load manual overrides first
    overrides = load_overrides()

    # Fetch all PRs from GitHub
    raw_prs = get_all_prs()

    # Parse each PR (with shared language cache to avoid duplicate API calls)
    print("🔎 Parsing and categorizing PRs...")
    lang_cache = {}  # { "owner/repo": {"HTML": 5200, "CSS": 3100, ...} }
    parsed_prs = []

    for pr in raw_prs:
        try:
            parsed = parse_pr(pr, overrides, lang_cache)
            parsed_prs.append(parsed)
            print(f"  ✔ [{parsed['status']}] {parsed['repo_full_name']} #{parsed['pr_number']} — {parsed['tech_stack']}")
        except Exception as e:
            print(f"  ⚠ Skipped a PR due to error: {e}")

    # Generate and write the README
    print("\n📝 Generating README.md...")
    readme_content = generate_readme(parsed_prs)

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)

    print(f"\n✅ Done! README.md updated with {len(parsed_prs)} contributions.")


if __name__ == "__main__":
    main()