"""
notify_issues.py
-----------------
This script does 3 things:
  1. Searches GitHub for open issues matching your contribution queries
  2. Filters to only show NEW issues from the last 24 hours
  3. Sends you a formatted email summary via Gmail

It runs automatically every morning at 9:00 AM Philippine time
via GitHub Actions (see notify-issues.yml).
"""

import os
import time
import smtplib
import requests
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ─────────────────────────────────────────────
# STEP 1: CONFIGURATION
# All sensitive values (email, password, token)
# are stored as GitHub Secrets — never typed here.
# ─────────────────────────────────────────────

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GMAIL_SENDER  = os.environ.get("GMAIL_SENDER", "")   # your Gmail address
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD", "") # your Gmail App Password
GMAIL_RECEIVER = os.environ.get("GMAIL_RECEIVER", "") # where to send (can be same as sender)

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

# ─────────────────────────────────────────────
# STEP 2: YOUR ISSUE SEARCH QUERIES
#
# These are all your contribution target searches,
# grouped by category. You can add or remove
# queries anytime — the script handles the rest.
# ─────────────────────────────────────────────

QUERIES = {

    "🔧 Infrastructure Automation / DevOps": [
        "is:issue is:open label:\"help wanted\" automation python",
        "is:issue is:open label:\"enhancement\" infrastructure python",
        "is:issue is:open label:\"help wanted\" devops python",
        "is:issue is:open provisioning python label:\"enhancement\"",
        "is:issue is:open ansible label:\"help wanted\"",
        "is:issue is:open terraform python label:\"enhancement\"",
        "is:issue is:open packer automation label:\"help wanted\"",
        "is:issue is:open \"infrastructure automation\" python",
        "is:issue is:open \"server provisioning\" python",
    ],

    "🪟 Windows / System-Level Python": [
        "is:issue is:open windows python label:\"help wanted\"",
        "is:issue is:open \"powershell\" python label:\"enhancement\"",
        "is:issue is:open winrm python",
        "is:issue is:open \"windows server\" automation python",
        "is:issue is:open pywin32",
        "is:issue is:open psutil label:\"good first issue\"",
        "is:issue is:open \"windows automation\" devops",
        "is:issue is:open \"active directory\" python",
        "is:issue is:open \"system monitoring\" python windows",
    ],

    "⚙️ Backend / API Tools (Flask / FastAPI)": [
        "is:issue is:open fastapi label:\"help wanted\"",
        "is:issue is:open fastapi automation",
        "is:issue is:open flask label:\"help wanted\"",
        "is:issue is:open \"flask admin\" enhancement",
        "is:issue is:open \"internal tool\" api python",
        "is:issue is:open \"background job\" python api",
        "is:issue is:open logging python api",
    ],

    "🧰 CLI Tools": [
        "is:issue is:open \"python cli\" label:\"help wanted\"",
        "is:issue is:open \"devops cli\" python",
        "is:issue is:open click python label:\"good first issue\"",
        "is:issue is:open typer python label:\"enhancement\"",
        "is:issue is:open \"automation cli\" python",
        "is:issue is:open \"command line tool\" devops python",
    ],

    "🧠 Pro Niche Combos": [
        "is:issue is:open windows devops python",
        "is:issue is:open fastapi automation devops",
        "is:issue is:open cli automation python windows",
    ],
}


# ─────────────────────────────────────────────
# STEP 3: GITHUB SEARCH
# ─────────────────────────────────────────────

def search_issues(query, since=None):
    """
    Calls the GitHub Search API for a single query.
    Returns a list of matching issues.

    'since' is an optional datetime — if provided,
    only issues created after that time are returned.
    This is how we filter to 'last 24 hours only'.
    """
    # Add date filter to the query if 'since' is provided
    if since:
        since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        full_query = f"{query} created:>{since_str}"
    else:
        full_query = query

    url = f"https://api.github.com/search/issues?q={full_query}&per_page=10&sort=created&order=desc"

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)

        # If we hit the rate limit, wait 60 seconds and retry once
        if response.status_code == 403:
            print("  ⚠️  Rate limit hit — waiting 60 seconds...")
            time.sleep(60)
            response = requests.get(url, headers=HEADERS, timeout=10)

        if response.status_code != 200:
            print(f"  ❌ API error {response.status_code} for query: {query[:50]}")
            return [], 0

        data = response.json()
        return data.get("items", []), data.get("total_count", 0)

    except Exception as e:
        print(f"  ⚠️  Request failed: {e}")
        return [], 0


def fetch_all_issues():
    """
    Loops through every category and every query.
    For each query:
      - Fetches NEW issues (last 24 hours)
      - Fetches the TOTAL count of all open matching issues

    Returns two dicts:
      new_by_category   → { category: [issue, issue, ...] }
      totals_by_category → { category: total_count }

    Adds a 2-second delay between requests to avoid
    hitting GitHub's rate limit (30 requests/minute).
    """
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    new_by_category    = {}
    totals_by_category = {}

    for category, queries in QUERIES.items():
        print(f"\n📂 {category}")
        new_issues  = []
        total_count = 0
        seen_ids    = set()  # avoid duplicate issues across queries

        for query in queries:
            print(f"  🔍 {query[:60]}...")

            # Fetch new issues (last 24 hours)
            new, _ = search_issues(query, since=since)
            for issue in new:
                if issue["id"] not in seen_ids:
                    seen_ids.add(issue["id"])
                    new_issues.append(issue)

            # Fetch total open count (no date filter)
            _, total = search_issues(query)
            total_count += total

            # Be polite to the GitHub API — wait 2 seconds between requests
            time.sleep(2)

        new_by_category[category]    = new_issues
        totals_by_category[category] = total_count
        print(f"  ✅ {len(new_issues)} new issues | {total_count} total open")

    return new_by_category, totals_by_category


# ─────────────────────────────────────────────
# STEP 4: EMAIL BUILDER
# Formats the results into a clean HTML email.
# ─────────────────────────────────────────────

def build_email(new_by_category, totals_by_category):
    """
    Builds a nicely formatted HTML email with:
    - A summary table showing totals per category
    - A section for each category listing new issues
    """
    today     = datetime.now().strftime("%A, %B %d, %Y")
    total_new = sum(len(v) for v in new_by_category.values())

    # ── Summary table ──────────────────────────────────────
    summary_rows = ""
    for category, total in totals_by_category.items():
        new_count = len(new_by_category.get(category, []))
        new_badge = f'<span style="color:#2ea44f;font-weight:bold">+{new_count} new</span>' if new_count else '<span style="color:#888">no new</span>'
        summary_rows += f"""
        <tr>
            <td style="padding:8px 12px;border-bottom:1px solid #eee">{category}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center">{total}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center">{new_badge}</td>
        </tr>"""

    # ── New issues per category ────────────────────────────
    category_sections = ""
    for category, issues in new_by_category.items():
        if not issues:
            continue

        issue_rows = ""
        for issue in issues:
            repo      = issue["repository_url"].replace("https://api.github.com/repos/", "")
            title     = issue["title"]
            url       = issue["html_url"]
            created   = issue["created_at"][:10]
            labels    = ", ".join(l["name"] for l in issue.get("labels", [])) or "—"

            issue_rows += f"""
            <tr>
                <td style="padding:8px 12px;border-bottom:1px solid #eee">
                    <a href="{url}" style="color:#0366d6;text-decoration:none;font-weight:500">{title}</a>
                </td>
                <td style="padding:8px 12px;border-bottom:1px solid #eee;color:#555;font-size:13px">{repo}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #eee;color:#555;font-size:13px">{labels}</td>
                <td style="padding:8px 12px;border-bottom:1px solid #eee;color:#888;font-size:12px">{created}</td>
            </tr>"""

        category_sections += f"""
        <h3 style="margin-top:32px;color:#24292e">{category}</h3>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #e1e4e8;border-radius:6px;overflow:hidden">
            <thead>
                <tr style="background:#f6f8fa">
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#586069">Issue</th>
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#586069">Repository</th>
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#586069">Labels</th>
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#586069">Posted</th>
                </tr>
            </thead>
            <tbody>{issue_rows}</tbody>
        </table>"""

    # ── No new issues message ──────────────────────────────
    if total_new == 0:
        category_sections = """
        <div style="text-align:center;padding:40px;color:#888">
            <p style="font-size:18px">😴 No new issues in the last 24 hours.</p>
            <p>Check back tomorrow — or browse open issues on
               <a href="https://github.com/issues" style="color:#0366d6">GitHub</a>.</p>
        </div>"""

    # ── Full HTML email ────────────────────────────────────
    html = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
                 max-width:800px;margin:0 auto;padding:20px;color:#24292e">

        <div style="background:#24292e;color:white;padding:20px 24px;border-radius:8px;margin-bottom:24px">
            <h1 style="margin:0;font-size:20px">🔔 GitHub Issue Digest</h1>
            <p style="margin:6px 0 0;color:#aaa;font-size:14px">{today} · {total_new} new issue(s) found in the last 24 hours</p>
        </div>

        <h2 style="color:#24292e">📊 Summary</h2>
        <table width="100%" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;border:1px solid #e1e4e8;border-radius:6px;overflow:hidden">
            <thead>
                <tr style="background:#f6f8fa">
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#586069">Category</th>
                    <th style="padding:8px 12px;text-align:center;font-size:13px;color:#586069">Total Open</th>
                    <th style="padding:8px 12px;text-align:center;font-size:13px;color:#586069">New (24h)</th>
                </tr>
            </thead>
            <tbody>{summary_rows}</tbody>
        </table>

        <h2 style="margin-top:32px;color:#24292e">🆕 New Issues (Last 24 Hours)</h2>
        {category_sections}

        <hr style="margin-top:40px;border:none;border-top:1px solid #eee">
        <p style="color:#888;font-size:12px;text-align:center">
            Sent automatically by your GitHub Actions bot in
            <a href="https://github.com/JohnMartin0301/my-contributions" style="color:#0366d6">
            JohnMartin0301/my-contributions</a>
        </p>
    </body>
    </html>
    """
    return html


# ─────────────────────────────────────────────
# STEP 5: SEND THE EMAIL
# Uses Gmail's SMTP server to send the email.
# ─────────────────────────────────────────────

def send_email(html_content, total_new):
    """
    Sends the formatted HTML email via Gmail.

    Uses SMTP (Simple Mail Transfer Protocol) —
    the standard way programs send emails.

    GMAIL_SENDER and GMAIL_PASSWORD are stored
    as GitHub Secrets, not typed here.
    """
    today   = datetime.now().strftime("%b %d, %Y")
    subject = f"🔔 GitHub Issues Digest — {total_new} new issue(s) · {today}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_RECEIVER
    msg.attach(MIMEText(html_content, "html"))

    print(f"\n📧 Sending email to {GMAIL_RECEIVER}...")
    try:
        # Connect to Gmail's SMTP server on port 587 (standard secure port)
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()  # Encrypt the connection
            server.login(GMAIL_SENDER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_SENDER, GMAIL_RECEIVER, msg.as_string())
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        raise


# ─────────────────────────────────────────────
# STEP 6: MAIN
# ─────────────────────────────────────────────

def main():
    print("🚀 Starting GitHub Issue Notifier...\n")
    print(f"🕘 Running at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"📅 Looking for issues created in the last 24 hours\n")

    # 1. Fetch all issues from GitHub
    new_by_category, totals_by_category = fetch_all_issues()

    # 2. Build the email
    print("\n📝 Building email...")
    html_content = build_email(new_by_category, totals_by_category)
    total_new    = sum(len(v) for v in new_by_category.values())

    # 3. Send the email
    send_email(html_content, total_new)

    print(f"\n✅ Done! Digest sent with {total_new} new issue(s).")


if __name__ == "__main__":
    main()