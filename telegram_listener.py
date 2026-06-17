"""
telegram_listener.py — Central Telegram hub.
Handles ALL commands every 5 minutes via GitHub Actions cron.
/run triggers portfolio_manager.yml. All other commands delegated to handle_command.
Offset persisted in telegram_offset.txt on GitHub via the API.
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
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=15,
    )


def update_prices(state: dict, token: str = None, chat_id: str = None) -> None:
    positions = state.get("positions", {})
    if not positions:
        return

    # Récupérer le taux EUR/USD
    eur_usd = 1.12  # fallback
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            params={"interval": "1d", "range": "5d"},
            timeout=10
        )
        if r.status_code == 200:
            result = r.json().get("chart", {}).get("result")
            if result:
                closes = result[0]["indicators"]["quote"][0].get("close", [])
                closes = [c for c in closes if c]
                if closes:
                    eur_usd = closes[-1]
    except Exception:
        pass

    lines = ["📊 *Prix mis à jour*\n"]
    total_invested = 0
    total_current  = 0

    for ticker, pos in positions.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            }, params={"interval": "1d", "range": "5d"}, timeout=10)
            if r.status_code == 200:
                result = r.json().get("chart", {}).get("result")
                if result:
                    closes = result[0]["indicators"]["quote"][0].get("close", [])
                    # Récupérer la devise
                    currency = result[0].get("meta", {}).get("currency", "USD")
                    closes = [c for c in closes if c]
                    if closes:
                        price_raw = closes[-1]
                        # Convertir en EUR si nécessaire
                        price_eur = price_raw / eur_usd if currency == "USD" else price_raw
                        shares   = pos.get("nb_shares", 0)
                        entry    = pos.get("entry_price", price_eur)
                        pnl_pct  = (price_eur - entry) / entry * 100 if entry else 0
                        pnl_eur  = (price_eur - entry) * shares
                        sign     = "🟢" if pnl_eur >= 0 else "🔴"
                        pos["current_price"] = round(price_eur, 4)
                        pos["position_eur"]  = round(price_eur * shares, 2)
                        pos["currency"]      = currency
                        pos["eur_usd"]       = round(eur_usd, 4)
                        total_invested += entry * shares
                        total_current  += price_eur * shares
                        lines.append(
                            f"{sign} *{ticker}*: {price_eur:.2f}€ "
                            f"({pnl_pct:+.1f}% | {pnl_eur:+.0f}€)"
                        )
        except Exception:
            lines.append(f"⚠️ {ticker}: erreur prix")

    total_pnl = total_current - total_invested
    total_pnl_pct = total_pnl / total_invested * 100 if total_invested else 0
    lines.append(f"\n💼 *P&L total: {total_pnl:+.0f}€ ({total_pnl_pct:+.1f}%)*")
    lines.append(f"💱 EUR/USD: {eur_usd:.4f}")

    if token and chat_id:
        from datetime import datetime
        if datetime.utcnow().minute < 10:
            msg = "\n".join(lines)
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
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

    # Mise à jour des prix à chaque run
    from state import load_state, save_state
    state = load_state()
    update_prices(state)
    save_state(state)

    updates = get_updates(token, last_id + 1)
    if not updates:
        return

    new_id = max(u["update_id"] for u in updates)
    # ACK immediately before processing — prevents reprocessing on crash
    write_offset(pat, new_id, sha)

    from telegram_bot import handle_command

    for update in updates:
        msg = update.get("message", {})
        if str(msg.get("chat", {}).get("id", "")) != str(authorized_chat):
            continue
        text = msg.get("text", "").strip()
        if not text.startswith("/"):
            continue

        if text == "/run":
            if trigger_workflow(pat):
                send_telegram(token, authorized_chat, "\U0001f680 Run lancé ! Signaux dans ~20 minutes")
            else:
                send_telegram(token, authorized_chat, "❌ Erreur lors du lancement du run.")
        else:
            reply = handle_command(text)
            send_telegram(token, authorized_chat, reply)


if __name__ == "__main__":
    main()
