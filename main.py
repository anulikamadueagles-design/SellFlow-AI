import os, sqlite3, json, secrets, hashlib, hmac, base64, time, urllib.request, urllib.error, mimetypes
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.getenv("DATABASE_PATH", os.path.join(APP_DIR, "sellflow.db"))
SECRET = os.getenv("JWT_SECRET") or secrets.token_urlsafe(48)
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="SellFlow AI", version="1.2.0")
app.mount("/assets", StaticFiles(directory=APP_DIR), name="assets")

PLANS = {
    "free": {"name": "Free", "price": 0, "ai": 0, "description": "Explore the workspace"},
    "starter": {"name": "Starter", "price": 4999, "ai": 50, "description": "For solo businesses"},
    "pro": {"name": "Pro", "price": 9999, "ai": 200, "description": "For growing businesses"},
    "business": {"name": "Business", "price": 19999, "ai": 600, "description": "For teams and advanced workflows"},
}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD = 5 * 1024 * 1024


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        plan TEXT NOT NULL DEFAULT 'free',
        subscription_status TEXT NOT NULL DEFAULT 'active',
        subscription_expires TEXT,
        ai_used INTEGER NOT NULL DEFAULT 0,
        ai_reset TEXT,
        created TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS records(
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        data TEXT NOT NULL,
        created TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        plan TEXT NOT NULL,
        amount REAL NOT NULL,
        reference TEXT,
        filename TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        file_data BLOB NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        note TEXT,
        created TEXT NOT NULL,
        reviewed TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)
    defaults = {
        "payment_account_name": "Add your account name in Admin → Payment Settings",
        "payment_account_number": "Add your account number in Admin → Payment Settings",
        "payment_bank_name": "Add your bank / wallet name in Admin → Payment Settings",
        "payment_instructions": "Transfer the exact plan amount, then upload your payment screenshot. Your subscription starts after admin verification.",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    c.commit()
    return c


def ensure_admin():
    email = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD") or ""
    name = (os.getenv("ADMIN_NAME") or "SellFlow Admin").strip()
    if not email or not password:
        return
    if len(password) < 10:
        raise RuntimeError("ADMIN_PASSWORD must be at least 10 characters")
    c = db()
    row = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if row:
        c.execute("UPDATE users SET role='admin', name=?, password=? WHERE id=?", (name, pwd.hash(password), row["id"]))
    else:
        c.execute("INSERT INTO users(name,email,password,role,plan,subscription_status,created) VALUES(?,?,?,?,?,?,?)",
                  (name, email, pwd.hash(password), "admin", "business", "active", now_iso()))
    c.commit(); c.close()


@app.on_event("startup")
def startup():
    db().close()
    ensure_admin()


def make_token(uid):
    payload = base64.urlsafe_b64encode(json.dumps({"uid": uid, "exp": int(time.time()) + 60 * 60 * 24 * 14}).encode()).decode().rstrip("=")
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return payload + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def current_user(authorization):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Please sign in")
    try:
        p, s = authorization.split()[1].split(".")
        expected = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), p.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(expected, s):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(p + "==="))
        if data["exp"] < time.time():
            raise ValueError()
        uid = int(data["uid"])
    except Exception:
        raise HTTPException(401, "Session expired. Sign in again.")
    c = db(); row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(); c.close()
    if not row:
        raise HTTPException(401, "Account not found")
    return row


def require_admin(authorization):
    u = current_user(authorization)
    if u["role"] != "admin":
        raise HTTPException(403, "Admin access required")
    return u


def subscription_active(u):
    if u["plan"] == "free":
        return False
    if u["subscription_status"] != "active":
        return False
    exp = u["subscription_expires"]
    return bool(exp and datetime.fromisoformat(exp) > datetime.now(timezone.utc))


def reset_ai_if_needed(c, u):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    if u["ai_reset"] != month:
        c.execute("UPDATE users SET ai_used=0, ai_reset=? WHERE id=?", (month, u["id"]))
        c.commit()
        return 0
    return int(u["ai_used"] or 0)


class Signup(BaseModel):
    name: str
    email: EmailStr
    password: str


class Login(BaseModel):
    email: EmailStr
    password: str


class Record(BaseModel):
    data: dict


class Ask(BaseModel):
    message: str


class PaymentReview(BaseModel):
    action: str
    note: str = ""


class PaymentSettings(BaseModel):
    account_name: str
    account_number: str
    bank_name: str
    instructions: str = ""


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "SellFlow AI", "version": "1.2.0"}


@app.post("/api/signup")
def signup(x: Signup):
    if len(x.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    name = x.name.strip()
    if len(name) < 2:
        raise HTTPException(400, "Please enter your name")
    c = db()
    try:
        cur = c.execute("INSERT INTO users(name,email,password,created) VALUES(?,?,?,?)",
                        (name, x.email.lower(), pwd.hash(x.password), now_iso()))
        c.commit(); uid = cur.lastrowid
    except sqlite3.IntegrityError:
        c.close(); raise HTTPException(409, "An account with this email already exists")
    c.close()
    return {"token": make_token(uid), "user": {"name": name, "email": x.email.lower(), "role": "user", "plan": "free"}}


@app.post("/api/login")
def login(x: Login):
    c = db(); row = c.execute("SELECT * FROM users WHERE email=?", (x.email.lower(),)).fetchone(); c.close()
    if not row or not pwd.verify(x.password, row["password"]):
        raise HTTPException(401, "Email or password is incorrect")
    return {"token": make_token(row["id"]), "user": {"name": row["name"], "email": row["email"], "role": row["role"], "plan": row["plan"]}}


@app.get("/api/me")
def me(authorization: Optional[str] = Header(None)):
    u = current_user(authorization)
    active = subscription_active(u)
    return {"name": u["name"], "email": u["email"], "role": u["role"], "plan": u["plan"],
            "subscription_status": "active" if active else (u["subscription_status"] if u["plan"] != "free" else "free"),
            "subscription_expires": u["subscription_expires"]}


@app.get("/api/billing")
def billing(authorization: Optional[str] = Header(None)):
    u = current_user(authorization); c = db()
    settings = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings").fetchall()}
    pending = c.execute("SELECT id,plan,amount,status,reference,created,note FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 10", (u["id"],)).fetchall()
    c.close()
    return {"plans": PLANS, "payment": {"account_name": settings.get("payment_account_name", ""), "account_number": settings.get("payment_account_number", ""), "bank_name": settings.get("payment_bank_name", ""), "instructions": settings.get("payment_instructions", "")},
            "current": {"plan": u["plan"], "active": subscription_active(u), "expires": u["subscription_expires"]},
            "payments": [dict(x) for x in pending]}


@app.get("/api/{kind}")
def list_records(kind: str, authorization: Optional[str] = Header(None)):
    if kind not in ["products", "customers", "orders", "invoices", "expenses"]:
        raise HTTPException(404, "Unknown resource")
    uid = current_user(authorization)["id"]
    c = db(); rows = c.execute("SELECT id,data,created FROM records WHERE user_id=? AND kind=? ORDER BY id DESC", (uid, kind)).fetchall(); c.close()
    return [{"id": r["id"], **json.loads(r["data"]), "created": r["created"]} for r in rows]


@app.post("/api/{kind}")
def create_record(kind: str, x: Record, authorization: Optional[str] = Header(None)):
    if kind not in ["products", "customers", "orders", "invoices", "expenses"]:
        raise HTTPException(404, "Unknown resource")
    uid = current_user(authorization)["id"]
    if not x.data:
        raise HTTPException(400, "Record data is required")
    c = db(); cur = c.execute("INSERT INTO records(user_id,kind,data,created) VALUES(?,?,?,?)", (uid, kind, json.dumps(x.data), now_iso())); c.commit(); rid = cur.lastrowid; c.close()
    return {"id": rid, **x.data}


@app.delete("/api/{kind}/{rid}")
def delete_record(kind: str, rid: int, authorization: Optional[str] = Header(None)):
    if kind not in ["products", "customers", "orders", "invoices", "expenses"]:
        raise HTTPException(404, "Unknown resource")
    uid = current_user(authorization)["id"]
    c = db(); cur = c.execute("DELETE FROM records WHERE id=? AND user_id=? AND kind=?", (rid, uid, kind)); c.commit(); c.close()
    if not cur.rowcount:
        raise HTTPException(404, "Record not found")
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(authorization: Optional[str] = Header(None)):
    uid = current_user(authorization)["id"]; c = db(); rows = c.execute("SELECT kind,data FROM records WHERE user_id=?", (uid,)).fetchall(); c.close()
    totals = {"products": 0, "customers": 0, "orders": 0, "invoices": 0, "expenses": 0, "revenue": 0, "costs": 0, "pending": 0}
    for r in rows:
        d = json.loads(r["data"]); k = r["kind"]; totals[k] += 1
        amount = float(d.get("total") or d.get("amount") or 0)
        if k == "orders" and d.get("status") == "Paid": totals["revenue"] += amount
        if k == "expenses": totals["costs"] += amount
        if k in ("orders", "invoices") and d.get("status", "Pending") != "Paid": totals["pending"] += amount
    totals["profit"] = totals["revenue"] - totals["costs"]
    return totals


@app.post("/api/payments/submit")
async def submit_payment(plan: str = Form(...), reference: str = Form(""), screenshot: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    u = current_user(authorization)
    if plan not in PLANS or plan == "free":
        raise HTTPException(400, "Choose a paid plan")
    amount = PLANS[plan]["price"]
    if amount <= 0:
        raise HTTPException(400, "Invalid plan amount")
    if screenshot.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(400, "Upload a JPG, PNG or WebP payment screenshot")
    data = await screenshot.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Screenshot is too large. Maximum size is 5 MB")
    c = db()
    existing = c.execute("SELECT id FROM payments WHERE user_id=? AND status='pending'", (u["id"],)).fetchone()
    if existing:
        c.close(); raise HTTPException(409, "You already have a payment confirmation awaiting review")
    c.execute("INSERT INTO payments(user_id,plan,amount,reference,filename,mime_type,file_data,status,created) VALUES(?,?,?,?,?,?,?,?,?)",
              (u["id"], plan, amount, reference.strip()[:120], screenshot.filename or "payment-screenshot", screenshot.content_type, data, "pending", now_iso()))
    c.commit(); c.close()
    return {"ok": True, "message": "Payment submitted. Your subscription will activate after verification."}


@app.get("/api/payments/mine")
def my_payments(authorization: Optional[str] = Header(None)):
    uid = current_user(authorization)["id"]; c = db(); rows = c.execute("SELECT id,plan,amount,reference,status,note,created,reviewed FROM payments WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall(); c.close()
    return [dict(r) for r in rows]


@app.post("/api/ai")
def ai(x: Ask, authorization: Optional[str] = Header(None)):
    u = current_user(authorization)
    if not subscription_active(u):
        raise HTTPException(402, "AI is available on paid plans. Open Upgrade & Billing to subscribe.")
    c = db(); used = reset_ai_if_needed(c, u); limit = PLANS[u["plan"]]["ai"]
    if used >= limit:
        c.close(); raise HTTPException(429, f"You have used your monthly AI allowance for the {PLANS[u['plan']]['name']} plan.")
    c.execute("UPDATE users SET ai_used=ai_used+1 WHERE id=?", (u["id"] ,)); c.commit(); c.close()
    key = os.getenv("OPENAI_API_KEY")
    if key:
        body = json.dumps({"model": os.getenv("AI_MODEL", "gpt-4o-mini"), "messages": [
            {"role": "system", "content": "You are SellFlow AI, a practical small-business sales and operations assistant. Give concise, actionable advice. Never claim to have sent messages, made payments, or accessed external accounts."},
            {"role": "user", "content": x.message}
        ]}).encode()
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as res:
                return {"reply": json.loads(res.read())["choices"][0]["message"]["content"]}
        except Exception:
            raise HTTPException(502, "AI provider request failed. Check the server-side AI configuration.")
    return {"reply": "Live AI is not configured yet. The site owner needs to add an OpenAI API key on the server. Meanwhile, focus on recording every sale and expense, following up unpaid orders, and restocking products that sell consistently."}


@app.get("/api/admin/payments")
def admin_payments(authorization: Optional[str] = Header(None)):
    require_admin(authorization); c = db()
    rows = c.execute("""SELECT p.id,p.plan,p.amount,p.reference,p.filename,p.mime_type,p.status,p.note,p.created,p.reviewed,u.name,u.email
                       FROM payments p JOIN users u ON u.id=p.user_id ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END,p.id DESC""").fetchall()
    c.close(); return [dict(r) for r in rows]


@app.get("/api/admin/payments/{payment_id}/image")
def admin_payment_image(payment_id: int, authorization: Optional[str] = Header(None)):
    require_admin(authorization); c = db(); row = c.execute("SELECT mime_type,file_data FROM payments WHERE id=?", (payment_id,)).fetchone(); c.close()
    if not row: raise HTTPException(404, "Payment confirmation not found")
    return Response(content=row["file_data"], media_type=row["mime_type"], headers={"Cache-Control": "no-store"})


@app.post("/api/admin/payments/{payment_id}/review")
def review_payment(payment_id: int, x: PaymentReview, authorization: Optional[str] = Header(None)):
    admin = require_admin(authorization)
    action = x.action.lower().strip()
    if action not in ("approve", "reject"):
        raise HTTPException(400, "Action must be approve or reject")
    c = db(); p = c.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not p: c.close(); raise HTTPException(404, "Payment confirmation not found")
    if p["status"] != "pending": c.close(); raise HTTPException(409, "This payment has already been reviewed")
    reviewed = now_iso()
    if action == "approve":
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        c.execute("UPDATE payments SET status='approved',note=?,reviewed=? WHERE id=?", (x.note.strip()[:500], reviewed, payment_id))
        c.execute("UPDATE users SET plan=?,subscription_status='active',subscription_expires=?,ai_used=0,ai_reset=? WHERE id=?",
                  (p["plan"], expires, datetime.now(timezone.utc).strftime("%Y-%m"), p["user_id"]))
    else:
        c.execute("UPDATE payments SET status='rejected',note=?,reviewed=? WHERE id=?", (x.note.strip()[:500], reviewed, payment_id))
    c.commit(); c.close()
    return {"ok": True, "message": "Payment approved and subscription activated." if action == "approve" else "Payment rejected.", "admin": admin["email"]}


@app.get("/api/admin/settings")
def admin_settings(authorization: Optional[str] = Header(None)):
    require_admin(authorization); c = db(); settings = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings").fetchall()}; c.close()
    return settings


@app.post("/api/admin/settings")
def save_admin_settings(x: PaymentSettings, authorization: Optional[str] = Header(None)):
    require_admin(authorization); c = db()
    vals = {"payment_account_name": x.account_name.strip(), "payment_account_number": x.account_number.strip(), "payment_bank_name": x.bank_name.strip(), "payment_instructions": x.instructions.strip()}
    for k, v in vals.items(): c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    c.commit(); c.close(); return {"ok": True}


@app.post("/api/paystack/initialize")
def paystack(x: dict, authorization: Optional[str] = Header(None)):
    uid = current_user(authorization)["id"]; key = os.getenv("PAYSTACK_SECRET_KEY")
    if not key: raise HTTPException(503, "Paystack is not configured yet. Add PAYSTACK_SECRET_KEY in Render Environment.")
    email = x.get("email"); amount = x.get("amount")
    if not email or not amount or float(amount) <= 0: raise HTTPException(400, "Valid email and amount are required")
    body = json.dumps({"email": email, "amount": int(float(amount) * 100), "metadata": {"user_id": uid}}).encode()
    req = urllib.request.Request("https://api.paystack.co/transaction/initialize", data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.loads(res.read())
            if not data.get("status"): raise HTTPException(502, "Paystack could not initialize payment")
            return data["data"]
    except urllib.error.HTTPError:
        raise HTTPException(502, "Paystack rejected the request. Check your secret key and currency.")


@app.get("/")
def home():
    return FileResponse(os.path.join(APP_DIR, "index.html"))
