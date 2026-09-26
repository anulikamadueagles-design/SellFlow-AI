import os, sqlite3, json, secrets, hashlib, hmac, base64, time, urllib.request, urllib.error
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext

APP_DIR=os.path.dirname(os.path.abspath(__file__))
DB=os.getenv("DATABASE_PATH", os.path.join(APP_DIR,"sellflow.db"))
SECRET=os.getenv("JWT_SECRET") or secrets.token_urlsafe(48)
pwd=CryptContext(schemes=["bcrypt"], deprecated="auto")
app=FastAPI(title="SellFlow AI", version="1.0.0")
app.mount("/assets", StaticFiles(directory=APP_DIR), name="assets")

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, password TEXT, created TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, data TEXT, created TEXT)""")
    c.commit(); return c

def token(uid):
    payload=base64.urlsafe_b64encode(json.dumps({"uid":uid,"exp":int(time.time())+60*60*24*14}).encode()).decode().rstrip("=")
    sig=hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).digest()
    return payload+"."+base64.urlsafe_b64encode(sig).decode().rstrip("=")

def current(authorization):
    if not authorization or not authorization.lower().startswith("bearer "): raise HTTPException(401,"Please sign in")
    try:
        p,s=authorization.split()[1].split(".")
        sig=base64.urlsafe_b64encode(hmac.new(SECRET.encode(),p.encode(),hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(sig,s): raise ValueError()
        data=json.loads(base64.urlsafe_b64decode(p+"==="))
        if data["exp"]<time.time(): raise ValueError()
        return int(data["uid"])
    except Exception: raise HTTPException(401,"Session expired. Sign in again.")

class Signup(BaseModel):
    name:str; email:EmailStr; password:str
class Login(BaseModel):
    email:EmailStr; password:str
class Record(BaseModel):
    data:dict
class Ask(BaseModel):
    message:str

@app.get("/api/health")
def health(): return {"status":"ok","app":"SellFlow AI"}

@app.post("/api/signup")
def signup(x:Signup):
    if len(x.password)<8: raise HTTPException(400,"Password must be at least 8 characters")
    c=db()
    try:
        cur=c.execute("INSERT INTO users(name,email,password,created) VALUES(?,?,?,?)",(x.name.strip(),x.email.lower(),pwd.hash(x.password),datetime.now(timezone.utc).isoformat()))
        c.commit(); uid=cur.lastrowid
    except sqlite3.IntegrityError: c.close(); raise HTTPException(409,"An account with this email already exists")
    c.close(); return {"token":token(uid),"user":{"name":x.name,"email":x.email}}
@app.post("/api/login")
def login(x:Login):
    c=db(); row=c.execute("SELECT * FROM users WHERE email=?",(x.email.lower(),)).fetchone(); c.close()
    if not row or not pwd.verify(x.password,row["password"]): raise HTTPException(401,"Email or password is incorrect")
    return {"token":token(row["id"]),"user":{"name":row["name"],"email":row["email"]}}
@app.get("/api/me")
def me(authorization:Optional[str]=Header(None)):
    uid=current(authorization); c=db(); u=c.execute("SELECT name,email FROM users WHERE id=?",(uid,)).fetchone(); c.close()
    return dict(u)
@app.get("/api/{kind}")
def list_records(kind:str,authorization:Optional[str]=Header(None)):
    if kind not in ["products","customers","orders","invoices","expenses"]: raise HTTPException(404,"Unknown resource")
    uid=current(authorization); c=db(); rows=c.execute("SELECT id,data,created FROM records WHERE user_id=? AND kind=? ORDER BY id DESC",(uid,kind)).fetchall(); c.close()
    return [{"id":r["id"],**json.loads(r["data"]),"created":r["created"]} for r in rows]
@app.post("/api/{kind}")
def create_record(kind:str,x:Record,authorization:Optional[str]=Header(None)):
    if kind not in ["products","customers","orders","invoices","expenses"]: raise HTTPException(404,"Unknown resource")
    uid=current(authorization); data=x.data
    if not data: raise HTTPException(400,"Record data is required")
    c=db(); cur=c.execute("INSERT INTO records(user_id,kind,data,created) VALUES(?,?,?,?)",(uid,kind,json.dumps(data),datetime.now(timezone.utc).isoformat())); c.commit(); rid=cur.lastrowid; c.close()
    return {"id":rid,**data}
@app.delete("/api/{kind}/{rid}")
def delete_record(kind:str,rid:int,authorization:Optional[str]=Header(None)):
    if kind not in ["products","customers","orders","invoices","expenses"]: raise HTTPException(404,"Unknown resource")
    uid=current(authorization); c=db(); cur=c.execute("DELETE FROM records WHERE id=? AND user_id=? AND kind=?",(rid,uid,kind)); c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(404,"Record not found")
    return {"ok":True}
@app.get("/api/dashboard")
def dashboard(authorization:Optional[str]=Header(None)):
    uid=current(authorization); c=db(); rows=c.execute("SELECT kind,data FROM records WHERE user_id=?",(uid,)).fetchall(); c.close()
    totals={"products":0,"customers":0,"orders":0,"invoices":0,"expenses":0,"revenue":0,"costs":0,"pending":0}
    for r in rows:
        d=json.loads(r["data"]); k=r["kind"]; totals[k]+=1
        amount=float(d.get("total") or d.get("amount") or 0)
        if k=="orders" and d.get("status")=="Paid": totals["revenue"]+=amount
        if k=="expenses": totals["costs"]+=amount
        if k in ("orders","invoices") and d.get("status","Pending")!="Paid": totals["pending"]+=amount
    totals["profit"]=totals["revenue"]-totals["costs"]
    return totals
@app.post("/api/ai")
def ai(x:Ask,authorization:Optional[str]=Header(None)):
    current(authorization)
    key=os.getenv("OPENAI_API_KEY")
    if key:
        body=json.dumps({"model":os.getenv("AI_MODEL","gpt-4o-mini"),"messages":[{"role":"system","content":"You are SellFlow AI, a practical small-business sales and operations assistant. Give concise, actionable advice. Do not claim to have sent messages, made payments, or accessed external accounts."},{"role":"user","content":x.message}]}).encode()
        req=urllib.request.Request("https://api.openai.com/v1/chat/completions",data=body,headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(req,timeout=45) as res: return {"reply":json.loads(res.read())["choices"][0]["message"]["content"]}
        except Exception: raise HTTPException(502,"AI provider request failed. Check your server-side API key.")
    return {"reply":"AI mode is ready to connect. Add OPENAI_API_KEY in your server environment to enable live AI.\\n\\nFor now, try: record sales and expenses daily, follow up with unpaid orders, and restock products that sell consistently. Tell me your business type and I can help draft a sales plan."}
@app.post("/api/paystack/initialize")
def paystack(x:dict,authorization:Optional[str]=Header(None)):
    uid=current(authorization); key=os.getenv("PAYSTACK_SECRET_KEY")
    if not key: raise HTTPException(503,"Paystack is not configured yet. Add PAYSTACK_SECRET_KEY in Render Environment.")
    email=x.get("email"); amount=x.get("amount")
    if not email or not amount or float(amount)<=0: raise HTTPException(400,"Valid email and amount are required")
    body=json.dumps({"email":email,"amount":int(float(amount)*100),"metadata":{"user_id":uid}}).encode()
    req=urllib.request.Request("https://api.paystack.co/transaction/initialize",data=body,headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=30) as res:
            data=json.loads(res.read())
            if not data.get("status"): raise HTTPException(502,"Paystack could not initialize payment")
            return data["data"]
    except urllib.error.HTTPError: raise HTTPException(502,"Paystack rejected the request. Check your secret key and currency.")
@app.get("/")
def home(): return FileResponse(os.path.join(APP_DIR,"index.html"))
