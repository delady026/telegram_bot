from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import re
import sqlite3
from datetime import datetime
import os
import math

# --- CONFIGURATION ---
TOKEN = os.getenv("8265851070:AAHB9CUdDF2pN7WjxXza1zhQSuh51C58hJs") 
DB_PATH = "bot_database.db"

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
    row = cur.fetchone()
    if row and row[0] == 0:
        cur.execute("INSERT INTO bankroll (id, balance) VALUES (1, 100.0)")

    conn.commit()
    conn.close()


def get_bankroll():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM bankroll WHERE id = 1")
    row = cur.fetchone()
    balance = row[0] if row else 100.0
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


# --- POISSON DISTRIBUTION ENGINE ---

def poisson_probability(k, lam):
    """Calculates Poisson probability for k goals given an average lambda."""
    return (pow(lam, k) * math.exp(-lam)) / math.factorial(k)

def calculate_over_15_prob(home_attack, away_defense, away_attack, home_defense):
    """Calculates the mathematical probability of Over 1.5 Goals using Poisson."""
    home_expected_goals = home_attack * home_defense
    away_expected_goals = away_attack * away_defense
    
    p_home_0 = poisson_probability(0, home_expected_goals)
    p_home_1 = poisson_probability(1, home_expected_goals)
    p_away_0 = poisson_probability(0, away_expected_goals)
    p_away_1 = poisson_probability(1, away_expected_goals)
    
    prob_0_0 = p_home_0 * p_away_0
    prob_1_0 = p_home_1 * p_away_0
    prob_0_1 = p_home_0 * p_away_1
    
    prob_under_15 = prob_0_0 + prob_1_0 + prob_0_1
    return 1 - prob_under_15

def score_runner(line):
    """Parses performance metrics from data strings."""
    lower = line.lower()
    
    odds = extract_odds(lower)
    if odds is None:
        return {"name": "Unknown", "value_edge": -1, "prob": 0, "odds": 0}
    
    implied_prob = 1 / (1 + odds)
    
    ha = float(re.search(r'ha:(\d+\.\d+)', lower).group(1)) if re.search(r'ha:(\d+\.\d+)', lower) else 1.0
    ad = float(re.search(r'ad:(\d+\.\d+)', lower).group(1)) if re.search(r'ad:(\d+\.\d+)', lower) else 1.0
    aa = float(re.search(r'aa:(\d+\.\d+)', lower).group(1)) if re.search(r'aa:(\d+\.\d+)', lower) else 1.0
    hd = float(re.search(r'hd:(\d+\.\d+)', lower).group(1)) if re.search(r'hd:(\d+\.\d+)', lower) else 1.0
    
    true_prob = calculate_over_15_prob(ha, ad, aa, hd)
    value_edge = true_prob - implied_prob
    
    m = re.search(r'\d+\s*/\s*\d+', line)
    name = line[:m.start()].strip() if m else line.strip()
    
    return {"name": name, "value_edge": value_edge, "prob": true_prob, "odds": odds}

def analyse_race(text):
    """Checks for a mathematical value edge and assigns scaled stake."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return None
        
    first_part = parts[0]
    m_odds = re.search(r'\d+\s*/\s*\d+', first_part)
    if not m_odds:
        return None
    
    fixture_name = first_part[:m_odds.start()].strip()
    market_data = score_runner(first_part)
    
    edge = market_data["value_edge"]
    true_percentage = market_data["prob"] * 100
    
    if edge >= 0.05:  
        decision = "BET"
        reason = f"Value Edge Detected: +{edge*100:.1f}%\nModel Probability: {true_percentage:.1f}%"
        
        b = market_data["odds"]
        p = market_data["prob"]
        q = 1 - p
        kelly_stake = ((b * p) - q) / b
        stake = round(max(0.5, min(5.0, kelly_stake * 10)), 1)
    elif edge >= 0.0:
        decision = "WATCH"
        reason = f"Fairly Priced match (+{edge*100:.1f}% edge).\nNo substantial value."
        stake = 0.0
    else:
        decision = "SKIP"
        reason = f"Negative Value: {edge*100:.1f}%\nBookie overpricing this line."
        stake = 0.0
        
    return fixture_name, market_data["name"], "Under_1.5_Goals Cover", decision, reason, stake


# --- TELEGRAM COMMAND ROUTERS ---

async def analyse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("/analyse", "", 1).strip()
    result = analyse_race(text)

    if result is None:
        await update.message.reply_text(
            "Use:\n/analyse Fixture Name Market A 1/4 [HA:1.5, AD:1.2, AA:1.1, HD:1.4]"
        )
        return

    race, pick, danger, decision, reason, stake = result
    bet_id = save_bet(race, pick, danger, decision, reason, stake)
    balance = get_bankroll()

    await update.message.reply_text(
f"""⚽ Football Smart Filter

ID: {bet_id}
Fixture: {race}

Primary Bet: {pick}
Hedge Cover: {danger}

Decision: {decision}
Stake: {stake}u

Model Note:
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

    await update.message.reply_text(f"Bet ID {bet_id} settled as {result.upper()}.")


# --- THE MAIN BOT RUNTIME ENGINE ---
if __name__ == "__main__":
    init_db()
    print("Database checking complete...")

    if not TOKEN:
        raise ValueError("CRITICAL ERROR: TELEGRAM_BOT_TOKEN variable is missing or blank!")
        
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("analyse", analyse))
    app.add_handler(CommandHandler("result", result_cmd))

    print("⚽ Football Smart Filter Bot is launching...")
    app.run_polling(poll_interval=2)
