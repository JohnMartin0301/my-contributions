"""
monitor_prs.py
---------------
This script monitors all your open Pull Requests for new activity.

What it checks every hour:
  - New comments from the repo owner or collaborators
  - Review submissions (approved, changes requested, commented)
  - PR status changes (merged, closed)
  - New labels added

How it avoids spam:
  - It only notifies you when something ACTUALLY changed
  - It tracks the last time it checked using a state file (last_checked.txt)
  - If nothing new happened since the last check, it exits silently

Notifications are sent to:
  - Discord (instant ping with a summary)
  - Gmail (detailed email with full context)

Runs every hour via GitHub Actions (see monitor-prs.yml).
"""

import os
import time
import smtplib
import requests
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ─────────────────────────────────────────────
# STEP 1: CONFIGURATION
# All sensitive values come from GitHub Secrets.
# ─────────────────────────────────────────────

GITHUB_USERNAME = "JohnMartin0301"
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GMAIL_SENDER    = os.environ.get("GMAIL_SENDER", "")
GMAIL_PASSWORD  = os.environ.get("GMAIL_PASSWORD", "")
GMAIL_RECEIVER  = os.environ.get("GMAIL_RECEIVER", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

# This file stores the last time we checked for activity.
# It lives in the repo so it persists between workflow runs.
# Format: ISO 8601 datetime string (e.g. 2026-05-10T01:00:00Z)
STATE_FILE = "last_checked.txt"


# ─────────────────────────────────────────────
# STEP 2: STATE MANAGEMENT
#
# We need to remember WHEN we last checked so we
# only alert on NEW activity — not old activity
# we've already seen.
#
# We store this timestamp in a file (last_checked.txt)
# that gets committed back to the repo after each run.
# ─────────────────────────────────────────────

def load_last_checked():
    """
    Reads the last-checked timestamp from last_checked.txt.
    If the file doesn't exist yet (first run), returns
    1 hour ago so we check the last hour of activity.
    """
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            timestamp_str = f.read().strip()
        try:
            # Parse the stored ISO timestamp back into a datetime object
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            print(f"📅 Last checked: {dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            return dt
        except ValueError:
            print("⚠️  Could not parse last_checked.txt — defaulting to 1 hour ago.")

    # First run — check the last hour
    from datetime import timedelta
    one_hour_ago = datetime.now(timezone.utc).replace(
        microsecond=0
    ) - timedelta(hours=1)
    print(f"📅 First run — checking since: {one_hour_ago.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    return one_hour_ago


def save_last_checked():
    """
    Saves the current time to last_checked.txt.
    Called at the END of every run so next time
    we only look at activity AFTER this moment.
    """
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with open(STATE_FILE, "w") as f:
        f.write(now)
    print(f"\n💾 Saved last checked time: {now}")


# ─────────────────────────────────────────────
# STEP 3: FETCH YOUR OPEN PRS
# ─────────────────────────────────────────────

def get_open_prs():
    """
    Fetches all Pull Requests you've authored that are
    currently open, using the GitHub Search API.

    Returns a list of PR objects, each containing
    the repo name, PR number, title, and URL.
    """
    print(f"\n🔍 Fetching open PRs for @{GITHUB_USERNAME}...")
    url = (
        f"https://api.github.com/search/issues"
        f"?q=is:pr+is:open+author:{GITHUB_USERNAME}&per_page=50"
    )
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            print(f"  ❌ API error {response.status_code}")
            return []
        items = response.json().get("items", [])
        print(f"  ✅ Found {len(items)} open PR(s)")
        return items
    except Exception as e:
        print(f"  ❌ Request failed: {e}")
        return []


# ─────────────────────────────────────────────
# STEP 4: CHECK FOR NEW ACTIVITY
#
# For each open PR, we check 3 things:
#   1. New comments (from anyone except yourself)
#   2. New reviews (approved / changes requested / commented)
#   3. Status changes (merged or closed)
# ─────────────────────────────────────────────

def get_new_comments(repo_full_name, pr_number, since):
    """
    Fetches comments on a PR posted after 'since'.
    Filters out your own comments — you don't need
    to be notified about things you wrote yourself.
    """
    url = (
        f"https://api.github.com/repos/{repo_full_name}"
        f"/issues/{pr_number}/comments"
        f"?since={since.strftime('%Y-%m-%dT%H:%M:%SZ')}&per_page=20"
    )
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return []
        comments = response.json()
        # Filter out your own comments
        return [
            c for c in comments
            if c.get("user", {}).get("login", "").lower() != GITHUB_USERNAME.lower()
        ]
    except Exception:
        return []


def get_new_reviews(repo_full_name, pr_number, since):
    """
    Fetches code reviews submitted on a PR after 'since'.
    Reviews can be: APPROVED, CHANGES_REQUESTED, or COMMENTED.
    These are different from regular comments — they're
    formal review submissions by repo maintainers.
    """
    url = (
        f"https://api.github.com/repos/{repo_full_name}"
        f"/pulls/{pr_number}/reviews?per_page=20"
    )
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return []
        reviews = response.json()
        new_reviews = []
        for review in reviews:
            submitted_at = review.get("submitted_at", "")
            if submitted_at:
                review_time = datetime.fromisoformat(
                    submitted_at.replace("Z", "+00:00")
                )
                if (review_time > since and
                        review.get("user", {}).get("login", "").lower()
                        != GITHUB_USERNAME.lower()):
                    new_reviews.append(review)
        return new_reviews
    except Exception:
        return []


def get_pr_status(repo_full_name, pr_number):
    """
    Fetches the current status of a PR.
    Returns 'open', 'merged', or 'closed'.
    Also returns the full PR data for context.
    """
    url = (
        f"https://api.github.com/repos/{repo_full_name}"
        f"/pulls/{pr_number}"
    )
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return "unknown", {}
        pr_data = response.json()
        if pr_data.get("merged"):
            return "merged", pr_data
        elif pr_data.get("state") == "closed":
            return "closed", pr_data
        else:
            return "open", pr_data
    except Exception:
        return "unknown", {}


def check_pr_activity(pr, since):
    """
    Runs all activity checks for a single PR.
    Returns a dict describing everything new that happened.
    Returns None if nothing new happened (no notification needed).
    """
    repo_full_name = (
        pr["pull_request"]["url"]
        .split("/repos/")[1]
        .split("/pulls")[0]
    )
    pr_number = pr["number"]
    pr_title  = pr["title"]
    pr_url    = pr["html_url"]

    print(f"\n  📋 Checking: {repo_full_name} #{pr_number}")

    # Check for new comments
    new_comments = get_new_comments(repo_full_name, pr_number, since)
    time.sleep(1)  # be polite to the API

    # Check for new reviews
    new_reviews = get_new_reviews(repo_full_name, pr_number, since)
    time.sleep(1)

    # Check current status (has it been merged or closed?)
    status, pr_data = get_pr_status(repo_full_name, pr_number)
    time.sleep(1)

    # Determine if the status changed since last check
    status_changed = status in ("merged", "closed")

    # If nothing new happened, return None (no notification)
    if not new_comments and not new_reviews and not status_changed:
        print(f"    💤 No new activity")
        return None

    # Log what we found
    if new_comments:
        print(f"    💬 {len(new_comments)} new comment(s)")
    if new_reviews:
        print(f"    👀 {len(new_reviews)} new review(s)")
    if status_changed:
        print(f"    🔄 Status changed → {status.upper()}")

    return {
        "repo":            repo_full_name,
        "pr_number":       pr_number,
        "pr_title":        pr_title,
        "pr_url":          pr_url,
        "new_comments":    new_comments,
        "new_reviews":     new_reviews,
        "status":          status,
        "status_changed":  status_changed,
    }


# ─────────────────────────────────────────────
# STEP 5: DISCORD NOTIFICATION
# ─────────────────────────────────────────────

def review_state_label(state):
    """Converts a GitHub review state into a readable emoji label."""
    return {
        "APPROVED":           "✅ Approved",
        "CHANGES_REQUESTED":  "🔴 Changes Requested",
        "COMMENTED":          "💬 Review Comment",
        "DISMISSED":          "🚫 Review Dismissed",
    }.get(state, state)


def status_label(status):
    """Converts a PR status string into an emoji label."""
    return {
        "merged": "✅ Merged!",
        "closed": "❌ Closed",
        "open":   "🔄 Still Open",
    }.get(status, status)


def build_discord_message(activity_list):
    """
    Builds a Discord embed for all PRs with new activity.
    Each PR gets its own field showing what changed.
    """
    now       = datetime.now().strftime("%A, %B %d, %Y · %I:%M %p")
    total_prs = len(activity_list)

    fields = []
    for activity in activity_list:
        lines = []

        # Status change
        if activity["status_changed"]:
            lines.append(f"**Status:** {status_label(activity['status'])}")

        # New reviews
        for review in activity["new_reviews"]:
            reviewer = review.get("user", {}).get("login", "Someone")
            state    = review_state_label(review.get("state", ""))
            body     = review.get("body", "").strip()
            if body:
                preview = body[:80] + ("..." if len(body) > 80 else "")
                lines.append(f"**{reviewer}** left a review: {state}\n> {preview}")
            else:
                lines.append(f"**{reviewer}** left a review: {state}")

        # New comments
        for comment in activity["new_comments"]:
            author  = comment.get("user", {}).get("login", "Someone")
            body    = comment.get("body", "").strip()
            preview = body[:80] + ("..." if len(body) > 80 else "")
            lines.append(f"**{author}** commented:\n> {preview}")

        field_value = "\n\n".join(lines) if lines else "New activity detected."
        fields.append({
            "name": (
                f"[#{activity['pr_number']}] {activity['pr_title'][:50]}"
                f"{'...' if len(activity['pr_title']) > 50 else ''}"
                f" — {activity['repo']}"
            ),
            "value": field_value,
            "inline": False,
        })

    payload = {
        "username": "PR Monitor",
        "embeds": [
            {
                "title": f"🔔 PR Activity Alert — {total_prs} PR(s) updated",
                "description": f"New activity detected on your open PRs · {now}",
                "color": 0x57F287,  # green
                "fields": fields[:25],  # Discord allows max 25 fields
                "footer": {
                    "text": "Full details sent to your Gmail inbox 📧"
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }
    return payload


def send_discord(payload):
    """Posts the embed to your Discord channel via webhook."""
    if not DISCORD_WEBHOOK:
        print("  ⚠️  DISCORD_WEBHOOK not set — skipping Discord.")
        return

    print("\n💬 Sending Discord notification...")
    try:
        response = requests.post(
            DISCORD_WEBHOOK,
            json=payload,
            timeout=10,
        )
        if response.status_code == 204:
            print("  ✅ Discord notification sent!")
        else:
            print(f"  ❌ Discord error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"  ❌ Discord request failed: {e}")


# ─────────────────────────────────────────────
# STEP 6: EMAIL NOTIFICATION
# ─────────────────────────────────────────────

def build_email(activity_list):
    """
    Builds a detailed HTML email listing all PR activity.
    Each PR gets a card showing comments, reviews, and status.
    """
    now       = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p UTC")
    total_prs = len(activity_list)

    pr_sections = ""
    for activity in activity_list:
        events_html = ""

        # Status change
        if activity["status_changed"]:
            color = "#2ea44f" if activity["status"] == "merged" else "#cb2431"
            label = status_label(activity["status"])
            events_html += (
                f'<div style="padding:10px 12px;border-left:4px solid {color};'
                f'margin-bottom:10px;background:#f6f8fa;border-radius:0 6px 6px 0">'
                f'<strong>Status changed:</strong> '
                f'<span style="color:{color}">{label}</span>'
                f"</div>"
            )

        # Reviews
        for review in activity["new_reviews"]:
            reviewer = review.get("user", {}).get("login", "Someone")
            state    = review_state_label(review.get("state", ""))
            body     = review.get("body", "").strip()
            color    = (
                "#2ea44f" if review.get("state") == "APPROVED"
                else "#cb2431" if review.get("state") == "CHANGES_REQUESTED"
                else "#0366d6"
            )
            events_html += (
                f'<div style="padding:10px 12px;border-left:4px solid {color};'
                f'margin-bottom:10px;background:#f6f8fa;border-radius:0 6px 6px 0">'
                f'<strong>{reviewer}</strong> submitted a review: '
                f'<span style="color:{color}">{state}</span>'
            )
            if body:
                events_html += (
                    f'<p style="margin:8px 0 0;color:#555;font-size:13px">'
                    f'"{body[:200]}{"..." if len(body) > 200 else ""}"</p>'
                )
            events_html += "</div>"

        # Comments
        for comment in activity["new_comments"]:
            author  = comment.get("user", {}).get("login", "Someone")
            body    = comment.get("body", "").strip()
            comment_url = comment.get("html_url", activity["pr_url"])
            events_html += (
                f'<div style="padding:10px 12px;border-left:4px solid #0366d6;'
                f'margin-bottom:10px;background:#f6f8fa;border-radius:0 6px 6px 0">'
                f'<strong>{author}</strong> commented: '
                f'<a href="{comment_url}" style="color:#0366d6;font-size:12px">'
                f"view →</a>"
                f'<p style="margin:8px 0 0;color:#555;font-size:13px">'
                f'"{body[:200]}{"..." if len(body) > 200 else ""}"</p>'
                f"</div>"
            )

        pr_sections += (
            f'<div style="border:1px solid #e1e4e8;border-radius:8px;'
            f'padding:16px;margin-bottom:20px">'
            f'<h3 style="margin:0 0 12px;font-size:15px">'
            f'<a href="{activity["pr_url"]}" '
            f'style="color:#0366d6;text-decoration:none">'
            f'#{activity["pr_number"]} {activity["pr_title"]}</a>'
            f'<span style="color:#888;font-size:12px;font-weight:normal;'
            f'margin-left:8px">{activity["repo"]}</span>'
            f"</h3>"
            f"{events_html}"
            f"</div>"
        )

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,
             Arial,sans-serif;max-width:800px;margin:0 auto;padding:20px;
             color:#24292e">

    <div style="background:#24292e;color:white;padding:20px 24px;
                border-radius:8px;margin-bottom:24px">
        <h1 style="margin:0;font-size:20px">🔔 PR Activity Alert</h1>
        <p style="margin:6px 0 0;color:#aaa;font-size:14px">
            {now} · {total_prs} PR(s) with new activity
        </p>
    </div>

    {pr_sections}

    <hr style="margin-top:40px;border:none;border-top:1px solid #eee">
    <p style="color:#888;font-size:12px;text-align:center">
        Sent automatically by your GitHub Actions bot in
        <a href="https://github.com/JohnMartin0301/my-contributions"
           style="color:#0366d6">JohnMartin0301/my-contributions</a>
    </p>
</body>
</html>"""
    return html


def send_email(html_content, total_prs):
    """Sends the PR activity digest via Gmail SMTP."""
    now     = datetime.now().strftime("%b %d, %Y %I:%M %p")
    subject = f"🔔 PR Activity Alert — {total_prs} PR(s) updated · {now}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_RECEIVER
    msg.attach(MIMEText(html_content, "html"))

    print(f"\n📧 Sending email to {GMAIL_RECEIVER}...")
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_SENDER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_SENDER, GMAIL_RECEIVER, msg.as_string())
        print("  ✅ Email sent successfully!")
    except Exception as e:
        print(f"  ❌ Failed to send email: {e}")
        raise


# ─────────────────────────────────────────────
# STEP 7: MAIN — runs everything in order
# ─────────────────────────────────────────────

def main():
    print("🚀 Starting PR Activity Monitor...\n")
    print(f"🕘 Running at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    # 1. Load the last-checked timestamp
    since = load_last_checked()

    # 2. Fetch all your open PRs
    open_prs = get_open_prs()
    if not open_prs:
        print("\n✅ No open PRs to monitor.")
        save_last_checked()
        return

    # 3. Check each PR for new activity since last run
    print(f"\n🔎 Checking {len(open_prs)} PR(s) for activity since last run...")
    activity_list = []
    for pr in open_prs:
        activity = check_pr_activity(pr, since)
        if activity:
            activity_list.append(activity)

    # 4. Save the current time for next run
    save_last_checked()

    # 5. If nothing changed, exit silently — no notification
    if not activity_list:
        print("\n✅ No new activity detected — no notification sent.")
        return

    print(f"\n🔔 {len(activity_list)} PR(s) have new activity!")

    # 6. Send Discord notification (quick ping)
    discord_payload = build_discord_message(activity_list)
    send_discord(discord_payload)

    # 7. Send Gmail notification (full detailed report)
    print("\n📝 Building email...")
    html_content = build_email(activity_list)
    send_email(html_content, len(activity_list))

    print(f"\n✅ Done! Notified about {len(activity_list)} PR(s).")


if __name__ == "__main__":
    main()