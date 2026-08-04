#!/usr/bin/env python3
"""Report a reusable-workflow run to Telegram (#46).

Reporting is opt-in per consumer: a missing chat id or bot token is a clean
no-op, not an error. A Telegram API failure is caught and surfaced as a
workflow warning rather than failing the calling job — a notification
channel must never become a reason a real job run fails.
"""

import json
import os
import sys
import urllib.error
import urllib.request


def send_message(bot_token: str, chat_id: str, text: str) -> None:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def main() -> None:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not chat_id or not bot_token:
        print("telegram-notify: no-op (chat_id and/or bot_token not configured)")
        return

    workflow = os.environ["REPORT_WORKFLOW"]
    job = os.environ["REPORT_JOB"]
    action = os.environ["REPORT_ACTION"]
    status = os.environ["REPORT_STATUS"]
    details = os.environ.get("REPORT_DETAILS", "")
    repository = os.environ["GITHUB_REPOSITORY"]
    run_url = f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}"

    lines = [
        f"{workflow} / {job}",
        f"repo: {repository}",
        f"action: {action}",
        f"status: {status}",
    ]
    if details:
        lines.append(f"details: {details}")
    lines.append(run_url)

    try:
        send_message(bot_token, chat_id, "\n".join(lines))
    except (urllib.error.URLError, TimeoutError) as err:
        # Never let a notification failure fail the job it's reporting on.
        print(f"::warning::telegram-notify: failed to deliver report: {err}", file=sys.stderr)


if __name__ == "__main__":
    main()
