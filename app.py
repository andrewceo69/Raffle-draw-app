import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
DB = BASE / "raffle.db"
UPLOAD_DIR = BASE / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")

CURRENCIES = {"NGN": "₦", "USD": "$", "GBP": "£", "EUR": "€"}
DEFAULTS = {
    "system_mode": "demo",
    "campaign_hours": "24",
    "sales_threshold": "80",
    "min_ticket": "100",
    "max_ticket": "10000",
    "platform_fee_percent": "5",
    "reminder_limit": "3",
    "winner_question": "How are you feeling about being today's lucky winner?",
    "winner_response_options": "Highly Happy|Super Glad|Marvelous|Extremely Excited|I Feel Lucky|Very Grateful",
    "withdrawal_feedback_required": "1",
    "payment_ready": "0",
}

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat()


def setting(key):
    row = db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else DEFAULTS.get(key)


def settings_map():
    conn = db()
    values = {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM settings")}
    conn.close()
    for key, value in DEFAULTS.items():
        values.setdefault(key, value)
    return values


def currency_symbol(code):
    return CURRENCIES.get(code, code + " ")


def money(amount_minor, currency="NGN"):
    value = Decimal(int(amount_minor or 0)) / Decimal(100)
    formatted = f"{value:,.2f}"
    if formatted.endswith(".00"):
        formatted = formatted[:-3]
    return f"{currency_symbol(currency)}{formatted}"


def valid_external_url(value):
    parsed = urlparse((value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def audit(action, campaign_id=None, details=""):
    conn = db()
    conn.execute(
        "INSERT INTO audit(actor,action,campaign_id,details,created_at) VALUES(?,?,?,?,?)",
        ("system", action, campaign_id, details, iso(utcnow())),
    )
    conn.commit()
    conn.close()


def notify(user_id, title, message, kind="info"):
    conn = db()
    conn.execute(
        "INSERT INTO notifications(user_id,title,message,kind,created_at,read_at) VALUES(?,?,?,?,?,NULL)",
        (user_id, title, message, kind, iso(utcnow())),
    )
    conn.commit()
    conn.close()


def parse_tiers(raw, default_minor):
    tiers = []
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        try:
            rank, amount = part.split(":", 1)
            tiers.append((int(rank), int(round(Decimal(amount) * 100))))
        except (ValueError, InvalidOperation):
            continue
    return tiers or [(1, int(default_minor))]


def tier_total_minor(raw, default_minor):
    return sum(amount for _, amount in parse_tiers(raw, default_minor))


def campaign_stats(cid):
    conn = db()
    campaign = conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    if not campaign:
        conn.close()
        return {"tickets": 0, "revenue": 0, "target": 0, "pct": 0, "state": "RESTRICTED", "threshold": 80,
                "target_tickets": 0, "free_rewards": 0, "winners": 0}

    paid = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount_minor),0) AS revenue FROM tickets "
        "WHERE campaign_id=? AND kind='paid' AND eligible=1",
        (cid,),
    ).fetchone()
    free_rewards = conn.execute(
        "SELECT COUNT(*) AS n FROM rewards WHERE campaign_id=?", (cid,)
    ).fetchone()["n"]
    winners = conn.execute("SELECT COUNT(*) AS n FROM winners WHERE campaign_id=?", (cid,)).fetchone()["n"]
    conn.close()

    target = int(campaign["economic_target_minor"] or 0)
    pct = int((paid["revenue"] / target) * 100) if target else 0
    threshold = int(campaign["threshold"])
    state = "WINNING" if pct >= 100 else ("WATCH" if pct >= threshold else "LOSING")
    return {
        "tickets": paid["n"],
        "revenue": paid["revenue"],
        "target": target,
        "pct": pct,
        "state": state,
        "threshold": threshold,
        "target_tickets": int(campaign["target_tickets"] or 0),
        "free_rewards": free_rewards,
        "winners": winners,
    }


def seed_demo(conn):
    now = utcnow()
    creator = conn.execute("SELECT * FROM users WHERE username='creator_demo'").fetchone()
    viewer = conn.execute("SELECT * FROM users WHERE username='viewer_demo'").fetchone()
    if not creator:
        avatar = "/static/sample_creator.jpg"
        conn.execute(
            "INSERT INTO users(name,username,avatar,currency,wallet_minor,role,created_at) VALUES(?,?,?,?,?,?,?)",
            ("Artist X", "creator_demo", avatar, "NGN", 0, "creator", iso(now)),
        )
        creator = conn.execute("SELECT * FROM users WHERE username='creator_demo'").fetchone()
    if not viewer:
        conn.execute(
            "INSERT INTO users(name,username,avatar,currency,wallet_minor,role,created_at) VALUES(?,?,?,?,?,?,?)",
            ("Demo Viewer", "viewer_demo", "/static/sample_creator.jpg", "NGN", 1560050, "user", iso(now)),
        )
        viewer = conn.execute("SELECT * FROM users WHERE username='viewer_demo'").fetchone()

    if not conn.execute("SELECT 1 FROM campaigns LIMIT 1").fetchone():
        tiers = "1:50000,2:20000,3:10000"
        prize_total = tier_total_minor(tiers, 5000000)
        target_tickets = 2000
        ticket_minor = 10000
        economic_target = 50000000  # ₦500,000, consistent with the reference meter.
        close = now + timedelta(hours=24)
        conn.execute(
            """INSERT INTO campaigns
            (creator_id,title,media_type,media_url,cover_url,prize_minor,ticket_minor,threshold,
             duration_hours,status,winner_tiers,free_reward_text,created_at,closes_at,slug,target_tickets,
             economic_target_minor,layout_json,free_reward_count)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                creator["id"], "New Hit Song", "audio", "https://www.youtube.com/", "/static/sample_creator.jpg",
                prize_total, ticket_minor, 80, 24, "published", tiers,
                "Play my song and claim your configured free promotional reward.", iso(now), iso(close),
                "artist-x-new-hit-song", target_tickets, economic_target,
                json.dumps({"x": 0, "y": 0, "scale": 1}), 873,
            ),
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Keep the sample's visual ticket pool, but use real, internally consistent revenue.
        for i, number in enumerate(["18492", "72015", "30944", "88103", "45921"]):
            conn.execute(
                "INSERT INTO tickets(campaign_id,user_id,number,kind,amount_minor,created_at,eligible) VALUES(?,?,?,?,?,?,1)",
                (cid, viewer["id"], number, "paid", ticket_minor, iso(now - timedelta(minutes=i))),
            )
        conn.execute(
            "INSERT INTO rewards(campaign_id,user_id,reward_text,created_at) VALUES(?,?,?,?)",
            (cid, viewer["id"], "Demo promotional reward", iso(now)),
        )
        conn.execute(
            "INSERT INTO tutorials(title,description,url,category,audience,thumbnail_url,active,display_order,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("How to Create a Show", "Learn how to create and publish your first campaign.",
             "https://www.youtube.com/", "Getting Started", "creator", "/static/sample_creator.jpg", 1, 1, iso(now)),
        )
        conn.execute(
            "INSERT INTO tutorials(title,description,url,category,audience,thumbnail_url,active,display_order,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("How Wallets & Tickets Work", "Understand wallet funding, tickets and winnings.",
             "https://www.youtube.com/", "User Tutorials", "user", "/static/sample_creator.jpg", 1, 2, iso(now)),
        )


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL, username TEXT UNIQUE NOT NULL, avatar TEXT,
          currency TEXT DEFAULT 'NGN', wallet_minor INTEGER DEFAULT 0,
          role TEXT DEFAULT 'user', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS campaigns(
          id INTEGER PRIMARY KEY AUTOINCREMENT, creator_id INTEGER NOT NULL,
          title TEXT NOT NULL, media_type TEXT NOT NULL, media_url TEXT NOT NULL,
          cover_url TEXT, prize_minor INTEGER NOT NULL, ticket_minor INTEGER NOT NULL,
          threshold INTEGER NOT NULL, duration_hours INTEGER NOT NULL, status TEXT DEFAULT 'draft',
          winner_tiers TEXT NOT NULL, free_reward_text TEXT, created_at TEXT NOT NULL,
          closes_at TEXT NOT NULL, slug TEXT UNIQUE, target_tickets INTEGER DEFAULT 1000,
          economic_target_minor INTEGER DEFAULT 0, layout_json TEXT DEFAULT '{}',
          free_reward_count INTEGER DEFAULT 0,
          FOREIGN KEY(creator_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS tickets(
          id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          number TEXT UNIQUE NOT NULL, kind TEXT DEFAULT 'paid', amount_minor INTEGER DEFAULT 0,
          created_at TEXT NOT NULL, eligible INTEGER DEFAULT 1,
          FOREIGN KEY(campaign_id) REFERENCES campaigns(id), FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS ledger(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, campaign_id INTEGER,
          direction TEXT NOT NULL, kind TEXT NOT NULL, amount_minor INTEGER NOT NULL,
          note TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit(
          id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT, action TEXT, campaign_id INTEGER,
          details TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS winners(
          id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER, ticket_id INTEGER, user_id INTEGER,
          tier INTEGER, prize_minor INTEGER, feedback TEXT, feedback_submitted_at TEXT,
          withdrawal_eligible INTEGER DEFAULT 0, payout_status TEXT DEFAULT 'pending', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT NOT NULL,
          message TEXT NOT NULL, kind TEXT DEFAULT 'info', created_at TEXT NOT NULL, read_at TEXT
        );
        CREATE TABLE IF NOT EXISTS rewards(
          id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          reward_text TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tutorials(
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
          url TEXT NOT NULL, category TEXT NOT NULL, audience TEXT NOT NULL,
          thumbnail_url TEXT, active INTEGER DEFAULT 1, display_order INTEGER DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tutorial_views(
          id INTEGER PRIMARY KEY AUTOINCREMENT, tutorial_id INTEGER NOT NULL, user_id INTEGER,
          created_at TEXT NOT NULL
        );
        """
    )
    for key, value in DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
    # Lightweight migrations for databases created by the earlier prototype.
    migrations = {
        "campaigns": [
            ("slug", "TEXT"), ("target_tickets", "INTEGER DEFAULT 1000"),
            ("economic_target_minor", "INTEGER DEFAULT 0"), ("layout_json", "TEXT DEFAULT '{}'"),
            ("free_reward_count", "INTEGER DEFAULT 0"),
        ],
        "winners": [
            ("feedback", "TEXT"), ("feedback_submitted_at", "TEXT"),
            ("withdrawal_eligible", "INTEGER DEFAULT 0"), ("payout_status", "TEXT DEFAULT 'pending'"),
        ],
    }
    for table, columns in migrations.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, definition in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    seed_demo(conn)
    conn.commit()
    conn.close()


@app.context_processor
def globals_for_templates():
    return {"money": money, "currency_symbol": currency_symbol, "app_mode": setting("system_mode")}


@app.route("/")
def home():
    conn = db()
    campaigns = conn.execute(
        """SELECT campaigns.*, users.name creator_name, users.username, users.avatar, users.currency
           FROM campaigns JOIN users ON users.id=campaigns.creator_id
           WHERE campaigns.status IN ('published','active') ORDER BY campaigns.id DESC"""
    ).fetchall()
    creator = conn.execute("SELECT * FROM users WHERE role='creator' ORDER BY id LIMIT 1").fetchone()
    viewer = conn.execute("SELECT * FROM users WHERE role='user' ORDER BY id LIMIT 1").fetchone()
    tutorials = conn.execute("SELECT * FROM tutorials WHERE active=1 ORDER BY display_order,id LIMIT 4").fetchall()
    unread = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read_at IS NULL", (viewer["id"],)).fetchone()["n"] if viewer else 0
    conn.close()
    return render_template(
        "index.html", campaigns=campaigns, stats=campaign_stats, creator=creator, viewer=viewer,
        tutorials=tutorials, unread=unread,
    )


@app.route("/campaign/new", methods=["GET", "POST"])
def new_campaign():
    conn = db()
    creator = conn.execute("SELECT * FROM users WHERE role='creator' LIMIT 1").fetchone()
    if request.method == "POST":
        try:
            title = request.form["title"].strip()
            media_url = request.form["media_url"].strip()
            if not title or not valid_external_url(media_url):
                raise ValueError("Campaign title and a valid external media URL are required.")
            prize = int(round(Decimal(request.form["prize"]) * 100))
            ticket = int(round(Decimal(request.form["ticket"]) * 100))
            threshold = int(request.form.get("threshold", setting("sales_threshold")))
            hours = int(request.form.get("hours", setting("campaign_hours")))
            target_tickets = int(request.form.get("target_tickets", "2000"))
            tiers_raw = request.form.get("tiers", "1:50000,2:20000,3:10000")
            if ticket < int(setting("min_ticket")) * 100 or ticket > int(setting("max_ticket")) * 100:
                raise ValueError("Ticket price is outside the configured creator limits.")
            if not 1 <= threshold <= 100:
                raise ValueError("Threshold must be 1–100%.")
            if hours < 1:
                raise ValueError("Campaign duration must be at least 1 hour.")
            if target_tickets < 1:
                raise ValueError("Target tickets must be at least 1.")

            tiers = parse_tiers(tiers_raw, prize)
            prize_total = sum(amount for _, amount in tiers)
            fee_percent = Decimal(setting("platform_fee_percent") or "0")
            economic_target = int(round((Decimal(prize_total) * (Decimal(100) + fee_percent)) / Decimal(100)))
            now = utcnow()
            slug_base = "-".join(title.lower().split())[:45].strip("-") or "campaign"
            slug = slug_base
            suffix = 2
            while conn.execute("SELECT 1 FROM campaigns WHERE slug=?", (slug,)).fetchone():
                slug = f"{slug_base}-{suffix}"; suffix += 1

            cover_url = request.form.get("cover_url", "").strip() or "/static/sample_creator.jpg"
            uploaded = request.files.get("image")
            if uploaded and uploaded.filename:
                ext = Path(secure_filename(uploaded.filename)).suffix.lower().lstrip(".")
                if ext not in ALLOWED_IMAGE_EXTENSIONS:
                    raise ValueError("Use JPG, JPEG, PNG or WEBP for creator images.")
                image_name = f"campaign-new-{secrets.token_hex(6)}.{ext}"
                uploaded.save(UPLOAD_DIR / image_name)
                cover_url = f"/static/uploads/{image_name}"
            if not valid_external_url(cover_url) and not cover_url.startswith("/static/"):
                cover_url = "/static/sample_creator.jpg"
            conn.execute(
                """INSERT INTO campaigns
                (creator_id,title,media_type,media_url,cover_url,prize_minor,ticket_minor,threshold,
                 duration_hours,status,winner_tiers,free_reward_text,created_at,closes_at,slug,target_tickets,
                 economic_target_minor,layout_json,free_reward_count)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (creator["id"], title, request.form.get("media_type", "video"), media_url, cover_url,
                 prize_total, ticket, threshold, hours, "published", tiers_raw,
                 request.form.get("free_reward_text", "").strip(), iso(now), iso(now + timedelta(hours=hours)),
                 slug, target_tickets, economic_target, json.dumps({"x": 0, "y": 0, "scale": 1}), 0),
            )
            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
            audit("campaign_created", cid, title)
            return redirect(url_for("campaign", cid=cid))
        except (ValueError, InvalidOperation) as exc:
            flash(str(exc), "error")
    conn.close()
    return render_template("new.html", settings=settings_map())


@app.post("/campaign/<int:cid>/upload-image")
def upload_campaign_image(cid):
    file = request.files.get("image")
    if not file or not file.filename:
        flash("Choose an image first.", "error")
        return redirect(url_for("campaign", cid=cid))
    ext = Path(secure_filename(file.filename)).suffix.lower().lstrip(".")
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        flash("Use JPG, JPEG, PNG or WEBP.", "error")
        return redirect(url_for("campaign", cid=cid))
    name = f"campaign-{cid}-{secrets.token_hex(6)}.{ext}"
    file.save(UPLOAD_DIR / name)
    conn = db()
    conn.execute("UPDATE campaigns SET cover_url=? WHERE id=?", (f"/static/uploads/{name}", cid))
    conn.commit(); conn.close()
    audit("campaign_image_uploaded", cid, name)
    flash("Creator/campaign image updated. No Base64 embedding was used.", "success")
    return redirect(url_for("campaign", cid=cid))


@app.route("/campaign/<int:cid>")
@app.route("/c/<slug>")
def campaign(cid=None, slug=None):
    conn = db()
    if cid is not None:
        camp = conn.execute(
            """SELECT campaigns.*, users.name creator_name, users.username, users.avatar, users.currency
               FROM campaigns JOIN users ON users.id=campaigns.creator_id WHERE campaigns.id=?""", (cid,)
        ).fetchone()
    else:
        camp = conn.execute(
            """SELECT campaigns.*, users.name creator_name, users.username, users.avatar, users.currency
               FROM campaigns JOIN users ON users.id=campaigns.creator_id WHERE campaigns.slug=?""", (slug,)
        ).fetchone()
    if not camp:
        conn.close(); return "Campaign not found", 404
    viewer = conn.execute("SELECT * FROM users WHERE role='user' LIMIT 1").fetchone()
    tickets = conn.execute("SELECT * FROM tickets WHERE campaign_id=? ORDER BY id DESC LIMIT 12", (camp["id"],)).fetchall()
    tutorials = conn.execute("SELECT * FROM tutorials WHERE active=1 ORDER BY display_order,id LIMIT 3").fetchall()
    conn.close()
    return render_template("campaign.html", camp=camp, viewer=viewer, stats=campaign_stats(camp["id"]),
                           tickets=tickets, tutorials=tutorials)


@app.post("/campaign/<int:cid>/buy")
def buy(cid):
    conn = db(); camp = conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    user = conn.execute("SELECT * FROM users WHERE role='user' LIMIT 1").fetchone()
    if not camp or not user:
        conn.close(); return "Not found", 404
    if setting("system_mode") != "demo" and setting("payment_ready") != "1":
        flash("Live ticket purchases are locked until a real payment gateway is configured.", "error")
        conn.close(); return redirect(url_for("campaign", cid=cid))
    if camp["status"] not in ("published", "active"):
        flash("This campaign is not open.", "error"); conn.close(); return redirect(url_for("campaign", cid=cid))
    if utcnow() >= datetime.fromisoformat(camp["closes_at"]):
        flash("Sales period has ended.", "error"); conn.close(); return redirect(url_for("campaign", cid=cid))
    if user["wallet_minor"] < camp["ticket_minor"]:
        flash("Your wallet balance is insufficient. Please fund your wallet to purchase tickets.", "error")
        conn.close(); return redirect(url_for("campaign", cid=cid))
    number = str(secrets.randbelow(90000) + 10000)
    while conn.execute("SELECT 1 FROM tickets WHERE number=?", (number,)).fetchone():
        number = str(secrets.randbelow(90000) + 10000)
    now = iso(utcnow())
    conn.execute("UPDATE users SET wallet_minor=wallet_minor-? WHERE id=?", (camp["ticket_minor"], user["id"]))
    conn.execute(
        "INSERT INTO tickets(campaign_id,user_id,number,kind,amount_minor,created_at,eligible) VALUES(?,?,?,?,?,?,1)",
        (cid, user["id"], number, "paid", camp["ticket_minor"], now),
    )
    conn.execute(
        "INSERT INTO ledger(user_id,campaign_id,direction,kind,amount_minor,note,created_at) VALUES(?,?,?,?,?,?,?)",
        (user["id"], cid, "debit", "ticket_purchase", camp["ticket_minor"], "Demo wallet ticket purchase" if setting("system_mode") == "demo" else "Ticket purchase", now),
    )
    conn.commit(); conn.close()
    audit("ticket_purchased", cid, number)
    flash(f"Ticket {number} purchased.", "success")
    return redirect(url_for("campaign", cid=cid))


@app.post("/campaign/<int:cid>/reward")
def claim_reward(cid):
    conn = db(); camp = conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    user = conn.execute("SELECT * FROM users WHERE role='user' LIMIT 1").fetchone()
    if not camp or not user:
        conn.close(); return "Not found", 404
    existing = conn.execute("SELECT 1 FROM rewards WHERE campaign_id=? AND user_id=?", (cid, user["id"])).fetchone()
    if existing:
        flash("Your promotional reward has already been claimed for this campaign.", "error")
    else:
        conn.execute("INSERT INTO rewards(campaign_id,user_id,reward_text,created_at) VALUES(?,?,?,?)",
                     (cid, user["id"], camp["free_reward_text"] or "Promotional reward", iso(utcnow())))
        conn.execute("UPDATE campaigns SET free_reward_count=free_reward_count+1 WHERE id=?", (cid,))
        conn.commit(); notify(user["id"], "Promotional reward claimed", "Your engagement reward has been recorded.", "success")
        audit("free_reward_claimed", cid, user["username"])
        flash("Promotional reward claimed.", "success")
    conn.close(); return redirect(url_for("campaign", cid=cid))


@app.post("/campaign/<int:cid>/draw")
def draw(cid):
    conn = db(); camp = conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    if not camp:
        conn.close(); return "Not found", 404
    st = campaign_stats(cid)
    if st["pct"] < camp["threshold"]:
        flash(f"Draw locked: campaign is at {st['pct']}% of the economic target; {camp['threshold']}% is required.", "error")
        conn.close(); return redirect(url_for("campaign", cid=cid))
    if conn.execute("SELECT 1 FROM winners WHERE campaign_id=?", (cid,)).fetchone():
        flash("Draw already completed.", "error"); conn.close(); return redirect(url_for("campaign", cid=cid))
    pool = conn.execute("SELECT * FROM tickets WHERE campaign_id=? AND eligible=1", (cid,)).fetchall()
    if not pool:
        flash("No eligible paid tickets.", "error"); conn.close(); return redirect(url_for("campaign", cid=cid))
    tickets = list(pool); secrets.SystemRandom().shuffle(tickets)
    tiers = parse_tiers(camp["winner_tiers"], camp["prize_minor"])
    now = iso(utcnow())
    for index, (tier, amount) in enumerate(tiers):
        if index >= len(tickets): break
        ticket = tickets[index]
        conn.execute(
            "INSERT INTO winners(campaign_id,ticket_id,user_id,tier,prize_minor,created_at) VALUES(?,?,?,?,?,?)",
            (cid, ticket["id"], ticket["user_id"], tier, amount, now),
        )
        # Prize is credited to the wallet, but withdrawal is gated by the configured winner experience.
        conn.execute("UPDATE users SET wallet_minor=wallet_minor+? WHERE id=?", (amount, ticket["user_id"]))
        conn.execute(
            "INSERT INTO ledger(user_id,campaign_id,direction,kind,amount_minor,note,created_at) VALUES(?,?,?,?,?,?,?)",
            (ticket["user_id"], cid, "credit", "prize", amount, f"Tier {tier} prize", now),
        )
        notify(ticket["user_id"], "Congratulations — you won!", f"You won {money(amount, camp['currency'])}. Complete the winner feedback step before withdrawal.", "winner")
    conn.execute("UPDATE campaigns SET status='completed' WHERE id=?", (cid,))
    conn.commit(); conn.close()
    audit("draw_completed", cid, f"{len(tiers)} tier(s); server-side secure random selection")
    flash("Draw completed. Winner wallets were credited and withdrawal requirements were applied.", "success")
    return redirect(url_for("campaign", cid=cid))


@app.route("/winner/<int:winner_id>", methods=["GET", "POST"])
def winner_experience(winner_id):
    conn = db()
    winner = conn.execute(
        """SELECT winners.*, users.name winner_name, users.username, campaigns.title campaign_title,
                  campaigns.currency, campaigns.prize_minor AS campaign_prize
           FROM winners JOIN users ON users.id=winners.user_id
           JOIN campaigns ON campaigns.id=winners.campaign_id WHERE winners.id=?""", (winner_id,)
    ).fetchone()
    if not winner:
        conn.close(); return "Winner not found", 404
    options = [x.strip() for x in (setting("winner_response_options") or "").split("|") if x.strip()]
    if request.method == "POST":
        response = request.form.get("response", "").strip()
        if response not in options:
            flash("Choose one of the configured responses.", "error")
        else:
            now = iso(utcnow())
            eligible = 1 if setting("withdrawal_feedback_required") == "1" else 1
            conn.execute("UPDATE winners SET feedback=?,feedback_submitted_at=?,withdrawal_eligible=? WHERE id=?",
                         (response, now, eligible, winner_id))
            conn.commit()
            audit("winner_feedback_submitted", winner["campaign_id"], f"winner={winner_id}; response={response}")
            # Notify eligible non-winners with a configurable social-proof prompt.
            non_winners = conn.execute(
                "SELECT DISTINCT user_id FROM tickets WHERE campaign_id=? AND user_id<>? LIMIT 100",
                (winner["campaign_id"], winner["user_id"]),
            ).fetchall()
            for row in non_winners:
                notify(row["user_id"], "A winner just cashed out", f"{winner['winner_name']} just completed the winner experience. Would you like to chat with {winner['winner_name']} and hear their experience on the Raffle Draw Show?", "social")
            flash("Thank you. Your winner experience is complete and withdrawal is now eligible.", "success")
    conn.close()
    return render_template("winner.html", winner=winner, question=setting("winner_question"), options=options)


@app.post("/winner/<int:winner_id>/withdraw")
def withdraw(winner_id):
    conn = db()
    winner = conn.execute("SELECT * FROM winners WHERE id=?", (winner_id,)).fetchone()
    if not winner:
        conn.close(); return "Winner not found", 404
    if winner["withdrawal_eligible"] != 1:
        flash("Withdrawal is locked until the winner feedback requirement is completed.", "error")
        conn.close(); return redirect(url_for("winner_experience", winner_id=winner_id))
    if winner["payout_status"] == "paid":
        flash("This winning amount has already been marked as paid.", "error")
        conn.close(); return redirect(url_for("winner_experience", winner_id=winner_id))
    # Demo mode simulates the payout request. Live payout must be connected to a verified provider.
    status = "paid" if setting("system_mode") == "demo" else "pending"
    conn.execute("UPDATE winners SET payout_status=? WHERE id=?", (status, winner_id))
    conn.commit(); conn.close()
    audit("withdrawal_requested", winner["campaign_id"], f"winner={winner_id}; status={status}")
    flash("Demo withdrawal completed." if status == "paid" else "Withdrawal request submitted for provider processing.", "success")
    return redirect(url_for("winner_experience", winner_id=winner_id))


@app.route("/admin")
def admin():
    conn = db()
    campaigns = conn.execute(
        """SELECT campaigns.*,users.name creator_name,users.currency FROM campaigns
           JOIN users ON users.id=campaigns.creator_id ORDER BY campaigns.id DESC"""
    ).fetchall()
    tutorials = conn.execute("SELECT * FROM tutorials ORDER BY display_order,id").fetchall()
    settings = settings_map()
    conn.close()
    return render_template("admin.html", campaigns=campaigns, tutorials=tutorials, settings=settings, stats=campaign_stats)


@app.post("/admin/settings")
def save_settings():
    conn = db()
    allowed = set(DEFAULTS)
    for key, value in request.form.items():
        if key in allowed:
            if key == "system_mode" and value not in {"demo", "live"}:
                continue
            conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
    conn.commit(); conn.close()
    audit("settings_updated", None, "Admin configuration")
    flash("Control Room settings saved.", "success")
    return redirect(url_for("admin"))


@app.post("/admin/tutorials")
def add_tutorial():
    title = request.form.get("title", "").strip()
    url = request.form.get("url", "").strip()
    if not title or not valid_external_url(url):
        flash("Tutorial title and a valid URL are required.", "error")
        return redirect(url_for("admin"))
    conn = db()
    conn.execute(
        "INSERT INTO tutorials(title,description,url,category,audience,thumbnail_url,active,display_order,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (title, request.form.get("description", "").strip(), url, request.form.get("category", "Getting Started"),
         request.form.get("audience", "all"), request.form.get("thumbnail_url", "").strip(), 1,
         int(request.form.get("display_order", "0") or 0), iso(utcnow())),
    )
    conn.commit(); conn.close()
    audit("tutorial_created", None, title)
    flash("Tutorial added to the Help Center.", "success")
    return redirect(url_for("admin"))


@app.post("/tutorial/<int:tutorial_id>/view")
def tutorial_view(tutorial_id):
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE role='user' LIMIT 1").fetchone()
    if conn.execute("SELECT 1 FROM tutorials WHERE id=? AND active=1", (tutorial_id,)).fetchone():
        conn.execute("INSERT INTO tutorial_views(tutorial_id,user_id,created_at) VALUES(?,?,?)",
                     (tutorial_id, user["id"] if user else None, iso(utcnow())))
        conn.commit()
    tutorial = conn.execute("SELECT * FROM tutorials WHERE id=?", (tutorial_id,)).fetchone()
    conn.close()
    if not tutorial:
        return jsonify({"error": "not found"}), 404
    return redirect(tutorial["url"])


@app.post("/admin/reset")
def reset():
    conn = db()
    for key, value in DEFAULTS.items():
        conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))
    conn.commit(); conn.close()
    audit("safe_reset", None, "Restored system configuration defaults; financial history preserved")
    flash("Configuration restored to safe defaults. Financial and audit history was preserved.", "success")
    return redirect(url_for("admin"))


@app.get("/api/campaign/<int:cid>/status")
def status(cid):
    conn = db(); camp = conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone(); conn.close()
    if not camp:
        return jsonify({"error": "not found"}), 404
    return jsonify({"campaign": cid, "status": camp["status"], "stats": campaign_stats(cid), "closes_at": camp["closes_at"], "mode": setting("system_mode")})


@app.get("/health")
def health():
    return {"status": "ok", "service": "raffle-promotion-app", "mode": setting("system_mode")}


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
