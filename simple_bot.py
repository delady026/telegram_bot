from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import re
import sqlite3
from datetime import datetime

TOKEN = "8265851070:AAHB9CUdDF2pN7WjxXza1zhQSuh51C58hJs"
DB_PATH = "tracking.db"

def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bankroll (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            race TEXT NOT NULL,
            pick TEXT NOT NULL,
            danger TEXT,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            stake REAL NOT NULL,
            result TEXT,
            profit REAL DEFAULT 0
        )
    """)

    cur.execute("SELECT COUNT(*) FROM bankroll")
    count = cur.fetchone()[0]
    if count == 0:
        cur.execute("INSERT INTO bankroll (id, balance) VALUES (1, 100.0)")

    conn.commit()
    conn.close()


def get_bankroll():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM bankroll WHERE id = 1")
    balance = cur.fetchone()[0]
    conn.close()
    return balance


def update_bankroll(amount):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE bankroll SET balance = balance + ? WHERE id = 1", (amount,))
    conn.commit()
    conn.close()


def save_bet(race, pick, danger, decision, reason, stake):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bets (created_at, race, pick, danger, decision, reason, stake, result, profit)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0)
    """, (
        datetime.now().isoformat(timespec="seconds"),
        race,
        pick,
        danger,
        decision,
        reason,
        stake
    ))
    bet_id = cur.lastrowid
    conn.commit()
    conn.close()
    return bet_id


def settle_bet(bet_id, result):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT stake, result FROM bets WHERE id = ?", (bet_id,))
    row = cur.fetchone()

    if row is None:
        conn.close()
        return "missing"

    stake, existing_result = row

    if existing_result is not None:
        conn.close()
        return "already_settled"

    if result == "win":
        profit = stake
    elif result == "lose":
        profit = -stake
    else:
        profit = 0

    cur.execute("UPDATE bets SET result = ?, profit = ? WHERE id = ?", (result, profit, bet_id))
    conn.commit()
    conn.close()

    update_bankroll(profit)
    return "ok"


def get_stats():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM bets")
    total_bets = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM bets WHERE result IS NOT NULL")
    settled_bets = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(profit), 0) FROM bets WHERE result IS NOT NULL")
    total_profit = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM bets WHERE result = 'win'")
    wins = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM bets WHERE result = 'lose'")
    losses = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM bets WHERE result = 'skip'")
    skips = cur.fetchone()[0]

    conn.close()

    balance = get_bankroll()

    total_staked = 0.0
    if settled_bets > 0:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(stake), 0) FROM bets WHERE result IS NOT NULL")
        total_staked = cur.fetchone()[0]
        conn.close()

    roi = (total_profit / total_staked * 100) if total_staked > 0 else 0.0

    return {
        "total_bets": total_bets,
        "settled_bets": settled_bets,
        "wins": wins,
        "losses": losses,
        "skips": skips,
        "profit": total_profit,
        "balance": balance,
        "roi": roi,
    }


def extract_odds(text):
    m = re.search(r'(\d+)\s*/\s*(\d+)', text)
    if not m:
        return None
    return int(m.group(1)) / int(m.group(2))


def split_input(text):
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) < 2:
        return None, []

    first = parts[0]
    m = re.search(r'\d+\s*/\s*\d+', first)
    if not m:
        return None, []

    before_odds = first[:m.start()].strip()
    after_odds = first[m.start():].strip()

    words = before_odds.split()
    race = before_odds
    first_runner = first

    for i in range(len(words) - 1, 0, -1):
        possible_race = " ".join(words[:i]).strip()
        if re.search(r'\d{1,2}:\d{2}', possible_race):
            race = possible_race
            runner_name = " ".join(words[i:]).strip()
            first_runner = f"{runner_name} {after_odds}"
            break

    return race, [first_runner] + parts[1:]


def score_runner(line):
    lower = line.lower()
    fav = 0
    danger = 0

    odds = extract_odds(lower)

    if odds is not None:
        if odds <= 1.0:
            fav += 5
        elif odds <= 2.0:
            fav += 3
            danger += 2
        elif odds <= 4.0:
            fav += 1
            danger += 2
        elif odds >= 8:
            danger -= 1

    if "progressive" in lower:
        fav += 2

    if "solid form" in lower:
        fav += 1

    if "should win" in lower:
        fav += 2

    if "consistent" in lower:
        fav += 1
        danger += 2

    if "won last" in lower or "recent win" in lower:
        danger += 2

    if "pulled up" in lower:
        fav -= 2
        danger += 1

    if "placed" in lower or "frame" in lower:
        danger += 0

    m = re.search(r'\d+\s*/\s*\d+', line)
    name = line[:m.start()].strip() if m else line.strip()

    return {"name": name, "fav": fav, "danger": danger}


def analyse_race(text):
    race, runner_lines = split_input(text)
    if not race or len(runner_lines) < 2:
        return None

    runners = [score_runner(line) for line in runner_lines]
    runners.sort(key=lambda x: x["fav"], reverse=True)

    pick = runners[0]
    dangers = sorted(runners[1:], key=lambda x: x["danger"], reverse=True)
    main_danger = dangers[0] if dangers else {"name": "None", "danger": 0}

    if pick["fav"] >= 4:
        decision = "BET"
        reason = "Strong favourite"
        stake = 2.0
    elif pick["fav"] >= 2:
        decision = "WATCH"
        reason = "Some confidence"
        stake = 1.0
    else:
        decision = "SKIP"
        reason = "No edge"
        stake = 0.0

    return race, pick["name"], main_danger["name"], decision, reason, stake


async def analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("/analyse", "", 1).strip()
    result = analyse_race(text)

    if result is None:
        await update.message.reply_text(
            "Use:\n/analyse Race Name Horse A 2/1 comment, Horse B 5/1 comment"
        )
        return

    race, pick, danger, decision, reason, stake = result
    bet_id = save_bet(race, pick, danger, decision, reason, stake)
    balance = get_bankroll()

    await update.message.reply_text(
f"""🏇 Smart Filter

ID: {bet_id}
Race: {race}

Top Pick: {pick}
Main Danger: {danger}

Decision: {decision}
Stake: {stake}u

Reason:
{reason}

Bankroll: {balance}u
"""
    )


async def result_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 2:
        await update.message.reply_text("Use: /result <id> <win|lose|skip>")
        return

    try:
        bet_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Bet ID must be a number.")
        return

    result = context.args[1].lower().strip()
    if result not in {"win", "lose", "skip"}:
        await update.message.reply_text("Result must be win, lose, or skip.")
        return

    status = settle_bet(bet_id, result)

    if status == "missing":
        await update.message.reply_text("No bet found with that ID.")
        return
    if status == "already_settled":
        await update.message.reply_text("That bet is already settled.")
        return

    balance = get_bankroll()
    await update.message.reply_text(f"Saved ✅\nNew bankroll: {balance}u")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = get_stats()

    await update.message.reply_text(
f"""📊 Stats

Total bets: {s['total_bets']}
Settled: {s['settled_bets']}
Wins: {s['wins']}
Losses: {s['losses']}
Skips: {s['skips']}

Profit: {round(s['profit'], 2)}u
Bankroll: {round(s['balance'], 2)}u
ROI: {round(s['roi'], 1)}%
"""
    )


def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("analyse", analyse))
    app.add_handler(CommandHandler("result", result_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))

    print("Running...")
    app.run_polling()


if __name__ == "__main__":
    main()