"""
telegram_listener.py — Minimal Telegram listener.
Only /run (exact match) from the authorized chat triggers portfolio_manager.yml.
All other messages are silently marked as read via offset.
Offset is persisted in telegram_offset.txt on GitHub via the API.
"""

import os
import base64
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
GITHUB_API = "https://api.github.com"
REPO = "johnbrami55/portfolio-manager"
OFFSET_FILE = "telegram_offset.txt"
WORKFLOW_FILE = "portfolio_manager.yml"
BRANCH = "main"


def read_offset(pat: str) -> tuple[int, str | None]:
    """Read last_update_id from GitHub. Returns (offset, sha) or (0, None) if not found."""
    resp = requests.get(
        f"{GITHUB_API}/repos/{REPO}/contents/{OFFSET_FILE}",
        headers={"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"},
        timeout=10,
    )
    if resp.status_code == 200:
        data = resp.json()
        content = base64.b64decode(data["content"]).decode().strip()
        return int(content or "0"), data["sha"]
    return 0, None


def write_offset(pat: str, update_id: int, sha: str | None) -> None:
    """Commit new last_update_id to GitHub."""
    body: dict = {
        "message": f"chore: telegram offset {update_id} [skip ci]",
        "content": base64.b64encode(str(update_id).encode()).decode(),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    requests.put(
        f"{GITHUB_API}/repos/{REPO}/contents/{OFFSET_FILE}",
        headers={"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"},
        json=body,
        timeout=10,
    )


def get_updates(token: str, offset: int) -> list[dict]:
    resp = requests.get(
        TELEGRAM_API.format(token=token, method="getUpdates"),
        params={"offset": offset, "timeout": 5},
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json().get("result", [])
    return []


def trigger_workflow(pat: str) -> bool:
    resp = requests.post(
        f"{GITHUB_API}/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        headers={"Authorization": f"token {pat}", "Accept": "application/vnd.github.v3+json"},
        json={"ref": BRANCH},
        timeout=10,
    )
    return resp.status_code == 204


def send_telegram(token: str, chat_id: str, text: str) -> None:
    requests.post(
        TELEGRAM_API.format(token=token, method="sendMessage"),
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    authorized_chat = os.environ["TELEGRAM_CHAT_ID"]
    pat = os.environ["GITHUB_PAT"]

    last_id, sha = read_offset(pat)

    # First ever run: drain all pending updates without processing any
    if sha is None:
        updates = get_updates(token, 0)
        if updates:
            write_offset(pat, max(u["update_id"] for u in updates), None)
        return

    updates = get_updates(token, last_id + 1)
    if not updates:
        return

    new_id = max(u["update_id"] for u in updates)
    # ACK immediately before processing — prevents reprocessing on crash
    write_offset(pat, new_id, sha)

    for update in updates:
        msg = update.get("message", {})
        if str(msg.get("chat", {}).get("id", "")) != str(authorized_chat):
            continue
        if msg.get("text", "").strip() != "/run":
            continue
        if trigger_workflow(pat):
            send_telegram(token, authorized_chat, "\U0001f680 Run lancé ! Signaux dans ~20 minutes")


if __name__ == "__main__":
    main()
