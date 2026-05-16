"""
monitor_prs.py
Checks all open Pull Requests every hour for new activity.
Detects new comments, reviews, and status changes (merged/closed).
Sends a Discord ping and Gmail alert only when something changed.
Saves a timestamp to last_checked.txt after each run to track state.
Runs via GitHub Actions (monitor_prs.yml).
"""

import os
import time
import smtplib
import requests
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# ── Credentials from GitHub Secrets ──────────────────────────────
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

# Stores the last check timestamp — persists between workflow runs
STATE_FILE = "last_checked.txt"


# ── State Management ──────────────────────────────────────────────

def load_last_checked():
    # Reads the last-checked timestamp from last_checked.txt
    # Defaults to 1 hour ago on the first run
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            timestamp_str = f.read().strip()
        try:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            print(f"📅 Last checked: {dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            return dt
        except ValueError:
            print("⚠️  Could not parse last_checked.txt — defaulting to 1 hour ago.")

    from datetime import timedelta
    one_hour_ago = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
    print(f"📅 First run — checking since: {one_hour_ago.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    return one_hour_ago


def save_last_checked():
    # Saves the current UTC time to last_checked.txt
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with open(STATE_FILE, "w") as f:
        f.write(now)
    print(f"\n💾 Saved last checked time: {now}")


# ── Fetch Open PRs ────────────────────────────────────────────────

def get_open_prs():
    # Fetches all open PRs authored by the configured GitHub user
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


# ── Activity Checks ───────────────────────────────────────────────

def get_new_comments(repo_full_name, pr_number, since):
    # Fetches comments posted after 'since', excluding own comments
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
        return [
            c for c in comments
            if c.get("user", {}).get("login", "").lower() != GITHUB_USERNAME.lower()
        ]
    except Exception:
        return []


def get_new_reviews(repo_full_name, pr_number, since):
    # Fetches reviews submitted after 'since', excluding own reviews
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
                review_time = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
                if (review_time > since and
                        review.get("user", {}).get("login", "").lower() != GITHUB_USERNAME.lower()):
                    new_reviews.append(review)
        return new_reviews
    except Exception:
        return []


def get_pr_status(repo_full_name, pr_number):
    # Returns the current status of a PR: open, merged, or closed
    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}"
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
    # Checks a single PR for new comments, reviews, and status changes
    # Returns None if nothing new happened
    repo_full_name = pr["pull_request"]["url"].split("/repos/")[1].split("/pulls")[0]
    pr_number = pr["number"]
    pr_title  = pr["title"]
    pr_url    = pr["html_url"]

    print(f"\n  📋 Checking: {repo_full_name} #{pr_number}")

    new_comments = get_new_comments(repo_full_name, pr_number, since)
    time.sleep(1)

    new_reviews = get_new_reviews(repo_full_name, pr_number, since)
    time.sleep(1)

    status, pr_data = get_pr_status(repo_full_name, pr_number)
    time.sleep(1)

    status_changed = status in ("merged", "closed")

    if not new_comments and not new_reviews and not status_changed:
        print(f"    💤 No new activity")
        return None

    if new_comments:   print(f"    💬 {len(new_comments)} new comment(s)")
    if new_reviews:    print(f"    👀 {len(new_reviews)} new review(s)")
    if status_changed: print(f"    🔄 Status changed → {status.upper()}")

    return {
        "repo":           repo_full_name,
        "pr_number":      pr_number,
        "pr_title":       pr_title,
        "pr_url":         pr_url,
        "new_comments":   new_comments,
        "new_reviews":    new_reviews,
        "status":         status,
        "status_changed": status_changed,
    }


# ── Label Helpers ─────────────────────────────────────────────────

def review_state_label(state):
    # Converts a GitHub review state to a readable emoji label
    return {
        "APPROVED":          "✅ Approved",
        "CHANGES_REQUESTED": "🔴 Changes Requested",
        "COMMENTED":         "💬 Review Comment",
        "DISMISSED":         "🚫 Review Dismissed",
    }.get(state, state)


def status_label(status):
    # Converts a PR status string to an emoji label
    return {
        "merged": "✅ Merged!",
        "closed": "❌ Closed",
        "open":   "🔄 Still Open",
    }.get(status, status)


# ── Discord Notification ──────────────────────────────────────────

def build_discord_message(activity_list):
    # Builds a Discord embed with one field per PR showing what changed
    now       = datetime.now().strftime("%A, %B %d, %Y · %I:%M %p")
    total_prs = len(activity_list)

    fields = []
    for activity in activity_list:
        lines = []

        if activity["status_changed"]:
            lines.append(f"**Status:** {status_label(activity['status'])}")

        for review in activity["new_reviews"]:
            reviewer = review.get("user", {}).get("login", "Someone")
            state    = review_state_label(review.get("state", ""))
            body     = review.get("body", "").strip()
            if body:
                preview = body[:80] + ("..." if len(body) > 80 else "")
                lines.append(f"**{reviewer}** left a review: {state}\n> {preview}")
            else:
                lines.append(f"**{reviewer}** left a review: {state}")

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
            "value":  field_value,
            "inline": False,
        })

    return {
        "username": "PR Monitor",
        "embeds": [{
            "title":       f"🔔 PR Activity Alert — {total_prs} PR(s) updated",
            "description": f"New activity detected on your open PRs · {now}",
            "color":       0x57F287,
            "fields":      fields[:25],
            "footer":      {"text": "Full details sent to your Gmail inbox 📧"},
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }],
    }


def send_discord(payload):
    # Posts the embed to the Discord channel via webhook URL
    if not DISCORD_WEBHOOK:
        print("  ⚠️  DISCORD_WEBHOOK not set — skipping Discord.")
        return

    print("\n💬 Sending Discord notification...")
    try:
        response = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        if response.status_code == 204:
            print("  ✅ Discord notification sent!")
        else:
            print(f"  ❌ Discord error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"  ❌ Discord request failed: {e}")


# ── Email Notification ────────────────────────────────────────────

def build_email(activity_list):
    # Builds a detailed HTML email with a card per PR
    # showing comments, reviews, and status changes
    now       = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p UTC")
    total_prs = len(activity_list)

    pr_sections = ""
    for activity in activity_list:
        events_html = ""

        if activity["status_changed"]:
            color = "#2ea44f" if activity["status"] == "merged" else "#cb2431"
            label = status_label(activity["status"])
            events_html += (
                f'<div style="padding:10px 12px;border-left:4px solid {color};'
                f'margin-bottom:10px;background:#f6f8fa;border-radius:0 6px 6px 0">'
                f'<strong>Status changed:</strong> <span style="color:{color}">{label}</span>'
                f"</div>"
            )

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
                f'<strong>{reviewer}</strong> submitted a review: <span style="color:{color}">{state}</span>'
            )
            if body:
                events_html += (
                    f'<p style="margin:8px 0 0;color:#555;font-size:13px">'
                    f'"{body[:200]}{"..." if len(body) > 200 else ""}"</p>'
                )
            events_html += "</div>"

        for comment in activity["new_comments"]:
            author      = comment.get("user", {}).get("login", "Someone")
            body        = comment.get("body", "").strip()
            comment_url = comment.get("html_url", activity["pr_url"])
            events_html += (
                f'<div style="padding:10px 12px;border-left:4px solid #0366d6;'
                f'margin-bottom:10px;background:#f6f8fa;border-radius:0 6px 6px 0">'
                f'<strong>{author}</strong> commented: '
                f'<a href="{comment_url}" style="color:#0366d6;font-size:12px">view →</a>'
                f'<p style="margin:8px 0 0;color:#555;font-size:13px">'
                f'"{body[:200]}{"..." if len(body) > 200 else ""}"</p>'
                f"</div>"
            )

        pr_sections += (
            f'<div style="border:1px solid #e1e4e8;border-radius:8px;padding:16px;margin-bottom:20px">'
            f'<h3 style="margin:0 0 12px;font-size:15px">'
            f'<a href="{activity["pr_url"]}" style="color:#0366d6;text-decoration:none">'
            f'#{activity["pr_number"]} {activity["pr_title"]}</a>'
            f'<span style="color:#888;font-size:12px;font-weight:normal;margin-left:8px">'
            f'{activity["repo"]}</span></h3>'
            f"{events_html}</div>"
        )

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
             max-width:800px;margin:0 auto;padding:20px;color:#24292e">
    <div style="background:#24292e;color:white;padding:20px 24px;border-radius:8px;margin-bottom:24px">
        <h1 style="margin:0;font-size:20px">🔔 PR Activity Alert</h1>
        <p style="margin:6px 0 0;color:#aaa;font-size:14px">{now} · {total_prs} PR(s) with new activity</p>
    </div>
    {pr_sections}
    <hr style="margin-top:40px;border:none;border-top:1px solid #eee">
    <p style="color:#888;font-size:12px;text-align:center">
        Sent automatically by ContribPilot —
        <a href="https://github.com/JohnMartin0301/ContribPilot" style="color:#0366d6">
        JohnMartin0301/ContribPilot</a>
    </p>
</body>
</html>"""
    return html


def send_email(html_content, total_prs):
    # Sends the PR activity alert via Gmail SMTP on port 587
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


# ── Main ──────────────────────────────────────────────────────────

def main():
    print("🚀 Starting PR Activity Monitor...\n")
    print(f"🕘 Running at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")

    since    = load_last_checked()
    open_prs = get_open_prs()

    if not open_prs:
        print("\n✅ No open PRs to monitor.")
        save_last_checked()
        return

    print(f"\n🔎 Checking {len(open_prs)} PR(s) for activity since last run...")
    activity_list = []
    for pr in open_prs:
        activity = check_pr_activity(pr, since)
        if activity:
            activity_list.append(activity)

    save_last_checked()

    if not activity_list:
        print("\n✅ No new activity detected — no notification sent.")
        return

    print(f"\n🔔 {len(activity_list)} PR(s) have new activity!")

    discord_payload = build_discord_message(activity_list)
    send_discord(discord_payload)

    print("\n📝 Building email...")
    html_content = build_email(activity_list)
    send_email(html_content, len(activity_list))

    print(f"\n✅ Done! Notified about {len(activity_list)} PR(s).")


if __name__ == "__main__":
    main()
