import os, sqlite3, json, secrets, hashlib, hmac, base64, time, urllib.request, urllib.error, html
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form
from fastapi.responses import FileResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.getenv("DATABASE_PATH", os.path.join(APP_DIR, "sellflow.db"))
SECRET = os.getenv("JWT_SECRET") or secrets.token_urlsafe(48)
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="SellFlow AI", version="2.0.0")
app.mount("/assets", StaticFiles(directory=APP_DIR), name="assets")

PLANS = {
    "free": {"name":"Free", "price":0, "ai":0, "description":"Start and organize your business"},
    "starter": {"name":"Starter", "price":4999, "ai":50, "description":"For solo businesses"},
    "pro": {"name":"Pro", "price":9999, "ai":200, "description":"For growing businesses"},
    "business": {"name":"Business", "price":19999, "ai":600, "description":"For teams and advanced workflows"},
}
ALLOWED_IMAGE_TYPES={"image/jpeg","image/png","image/webp"}
MAX_UPLOAD=5*1024*1024
RESOURCE_KINDS={"products","customers","orders","invoices","expenses","followups","recurring"}


def now_iso(): return datetime.now(timezone.utc).isoformat()
def money(v): return round(float(v or 0),2)

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',plan TEXT NOT NULL DEFAULT 'free',subscription_status TEXT NOT NULL DEFAULT 'active',subscription_expires TEXT,ai_used INTEGER NOT NULL DEFAULT 0,ai_reset TEXT,created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,data TEXT NOT NULL,created TEXT NOT NULL,updated TEXT,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,plan TEXT NOT NULL,amount REAL NOT NULL,reference TEXT,filename TEXT NOT NULL,mime_type TEXT NOT NULL,file_data BLOB NOT NULL,status TEXT NOT NULL DEFAULT 'pending',note TEXT,created TEXT NOT NULL,reviewed TEXT,FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_records_user_kind ON records(user_id,kind);
    CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
    """)
    defaults={
      "payment_account_name":"Dauda Joseph","payment_account_number":"8085743879","payment_bank_name":"Opay",
      "payment_instructions":"Transfer the exact plan amount, then upload your payment screenshot. Subscription starts after admin verification.",
      "business_name":"My Business","currency":"NGN"
    }
    # Safe migrations for the original SellFlow 1.x database.
    cols={r[1] for r in c.execute("PRAGMA table_info(records)").fetchall()}
    if "updated" not in cols:
        c.execute("ALTER TABLE records ADD COLUMN updated TEXT")
        c.execute("UPDATE records SET updated=created WHERE updated IS NULL")
    for k,v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
    # Replace only the old placeholder payment settings; never overwrite a custom value.
    placeholders={
      "payment_account_name":"Add your account name in Admin → Payment Settings",
      "payment_account_number":"Add your account number in Admin → Payment Settings",
      "payment_bank_name":"Add your bank / wallet name in Admin → Payment Settings"
    }
    for k,old_value in placeholders.items():
        row=c.execute("SELECT value FROM settings WHERE key=?",(k,)).fetchone()
        if row and row[0].strip()==old_value:
            c.execute("UPDATE settings SET value=? WHERE key=?",(defaults[k],k))
    c.commit(); return c


def ensure_admin():
    email=(os.getenv("ADMIN_EMAIL") or "").strip().lower(); password=os.getenv("ADMIN_PASSWORD") or ""; name=(os.getenv("ADMIN_NAME") or "SellFlow Admin").strip()
    if not email or not password: return
    if len(password)<10: raise RuntimeError("ADMIN_PASSWORD must be at least 10 characters")
    c=db(); row=c.execute("SELECT id FROM users WHERE email=?",(email,)).fetchone(); hashed=pwd.hash(password)
    if row: c.execute("UPDATE users SET role='admin',name=?,password=? WHERE id=?",(name,hashed,row['id']))
    else: c.execute("INSERT INTO users(name,email,password,role,plan,subscription_status,created) VALUES(?,?,?,?,?,?,?)",(name,email,hashed,'admin','business','active',now_iso()))
    c.commit(); c.close()

@app.on_event("startup")
def startup(): db().close(); ensure_admin()

def make_token(uid):
    payload=base64.urlsafe_b64encode(json.dumps({"uid":uid,"exp":int(time.time())+60*60*24*14}).encode()).decode().rstrip("=")
    sig=hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).digest()
    return payload+"."+base64.urlsafe_b64encode(sig).decode().rstrip("=")

def current_user(authorization):
    if not authorization or not authorization.lower().startswith("bearer "): raise HTTPException(401,"Please sign in")
    try:
        p,s=authorization.split()[1].split("."); expected=base64.urlsafe_b64encode(hmac.new(SECRET.encode(),p.encode(),hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(expected,s): raise ValueError()
        data=json.loads(base64.urlsafe_b64decode(p+"==="));
        if data["exp"]<time.time(): raise ValueError()
        uid=int(data["uid"])
    except Exception: raise HTTPException(401,"Session expired. Sign in again.")
    c=db(); row=c.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone(); c.close()
    if not row: raise HTTPException(401,"Account not found")
    return row

def require_admin(authorization):
    u=current_user(authorization)
    if u['role']!='admin': raise HTTPException(403,"Admin access required")
    return u

def subscription_active(u):
    if u['plan']=='free' or u['subscription_status']!='active': return False
    return bool(u['subscription_expires'] and datetime.fromisoformat(u['subscription_expires'])>datetime.now(timezone.utc))

def reset_ai_if_needed(c,u):
    month=datetime.now(timezone.utc).strftime('%Y-%m')
    if u['ai_reset']!=month:
        c.execute("UPDATE users SET ai_used=0,ai_reset=? WHERE id=?",(month,u['id'])); c.commit(); return 0
    return int(u['ai_used'] or 0)

def get_records(uid,kind):
    c=db(); rows=c.execute("SELECT id,data,created,updated FROM records WHERE user_id=? AND kind=? ORDER BY id DESC",(uid,kind)).fetchall(); c.close()
    return [{"id":r['id'],**json.loads(r['data']),"created":r['created'],"updated":r['updated']} for r in rows]

def save_record(uid,kind,data,rid=None):
    c=db(); ts=now_iso()
    if rid:
        cur=c.execute("UPDATE records SET data=?,updated=? WHERE id=? AND user_id=? AND kind=?",(json.dumps(data),ts,rid,uid,kind))
        if not cur.rowcount: c.close(); raise HTTPException(404,"Record not found")
    else:
        cur=c.execute("INSERT INTO records(user_id,kind,data,created,updated) VALUES(?,?,?,?,?)",(uid,kind,json.dumps(data),ts,ts)); rid=cur.lastrowid
    c.commit(); c.close(); return {"id":rid,**data}

class Signup(BaseModel): name:str; email:EmailStr; password:str
class Login(BaseModel): email:EmailStr; password:str
class Record(BaseModel): data:dict
class Ask(BaseModel): message:str
class PaymentReview(BaseModel): action:str; note:str=""
class PaymentSettings(BaseModel): account_name:str; account_number:str; bank_name:str; instructions:str=""

@app.get('/api/health')
def health(): return {"status":"ok","app":"SellFlow AI","version":"2.0.0"}

@app.post('/api/signup')
def signup(x:Signup):
    if len(x.password)<8: raise HTTPException(400,'Password must be at least 8 characters')
    name=x.name.strip();
    if len(name)<2: raise HTTPException(400,'Please enter your name')
    c=db()
    try:
        cur=c.execute('INSERT INTO users(name,email,password,created) VALUES(?,?,?,?)',(name,x.email.lower(),pwd.hash(x.password),now_iso())); c.commit(); uid=cur.lastrowid
    except sqlite3.IntegrityError: c.close(); raise HTTPException(409,'An account with this email already exists')
    c.close(); return {"token":make_token(uid),"user":{"name":name,"email":x.email.lower(),"role":"user","plan":"free"}}

@app.post('/api/login')
def login(x:Login):
    c=db(); row=c.execute('SELECT * FROM users WHERE email=?',(x.email.lower(),)).fetchone(); c.close()
    if not row or not pwd.verify(x.password,row['password']): raise HTTPException(401,'Email or password is incorrect')
    return {"token":make_token(row['id']),"user":{"name":row['name'],"email":row['email'],'role':row['role'],'plan':row['plan']}}

@app.get('/api/me')
def me(authorization:Optional[str]=Header(None)):
    u=current_user(authorization); active=subscription_active(u)
    return {"name":u['name'],"email":u['email'],'role':u['role'],'plan':u['plan'],'subscription_status':'active' if active else (u['subscription_status'] if u['plan']!='free' else 'free'),'subscription_expires':u['subscription_expires']}

@app.get('/api/{kind}')
def list_records(kind:str,authorization:Optional[str]=Header(None)):
    if kind not in RESOURCE_KINDS: raise HTTPException(404,'Unknown resource')
    return get_records(current_user(authorization)['id'],kind)

@app.post('/api/{kind}')
def create_record(kind:str,x:Record,authorization:Optional[str]=Header(None)):
    if kind not in RESOURCE_KINDS: raise HTTPException(404,'Unknown resource')
    d=dict(x.data); uid=current_user(authorization)['id']
    if kind=='products':
        d['name']=str(d.get('name','')).strip(); d['price']=money(d.get('price')); d['cost']=money(d.get('cost')); d['stock']=int(float(d.get('stock') or 0)); d['low_stock']=int(float(d.get('low_stock') or 5))
        if not d['name']: raise HTTPException(400,'Product name is required')
    if kind=='orders':
        d['amount']=money(d.get('amount')); d['status']=d.get('status') or 'Pending'; d['qty']=int(float(d.get('qty') or 1))
        d['created_at']=d.get('created_at') or now_iso()
    if kind=='invoices': d['amount']=money(d.get('amount')); d['status']=d.get('status') or 'Unpaid'; d['invoice_no']=d.get('invoice_no') or f"SF-{int(time.time())}"
    if kind=='expenses': d['amount']=money(d.get('amount'))
    return save_record(uid,kind,d)

@app.put('/api/{kind}/{rid}')
def update_record(kind:str,rid:int,x:Record,authorization:Optional[str]=Header(None)):
    if kind not in RESOURCE_KINDS: raise HTTPException(404,'Unknown resource')
    d=dict(x.data); uid=current_user(authorization)['id']
    if kind=='products': d['price']=money(d.get('price')); d['cost']=money(d.get('cost')); d['stock']=int(float(d.get('stock') or 0)); d['low_stock']=int(float(d.get('low_stock') or 5))
    return save_record(uid,kind,d,rid)

@app.delete('/api/{kind}/{rid}')
def delete_record(kind:str,rid:int,authorization:Optional[str]=Header(None)):
    if kind not in RESOURCE_KINDS: raise HTTPException(404,'Unknown resource')
    uid=current_user(authorization)['id']; c=db(); cur=c.execute('DELETE FROM records WHERE id=? AND user_id=? AND kind=?',(rid,uid,kind)); c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(404,'Record not found')
    return {'ok':True}

@app.get('/api/dashboard')
def dashboard(authorization:Optional[str]=Header(None)):
    uid=current_user(authorization)['id']; c=db(); rows=c.execute('SELECT kind,data,created FROM records WHERE user_id=?',(uid,)).fetchall(); c.close()
    t={'products':0,'customers':0,'orders':0,'invoices':0,'expenses':0,'followups':0,'recurring':0,'revenue':0,'costs':0,'pending':0,'inventory_value':0,'low_stock':0}
    months={}
    for r in rows:
        d=json.loads(r['data']); k=r['kind']; t[k]+=1
        amount=money(d.get('total') or d.get('amount'))
        if k=='orders' and str(d.get('status','')).lower()=='paid': t['revenue']+=amount
        if k=='expenses': t['costs']+=amount
        if k in ('orders','invoices') and str(d.get('status','Pending')).lower() not in ('paid','completed'): t['pending']+=amount
        if k=='products':
            t['inventory_value']+=money(d.get('stock'))*money(d.get('cost') or d.get('price')); t['low_stock'] += 1 if int(float(d.get('stock') or 0))<=int(float(d.get('low_stock') or 5)) else 0
        if k=='orders':
            dt=str(d.get('created_at') or r['created'])[:7]; months[dt]=months.get(dt,0)+amount if str(d.get('status','')).lower()=='paid' else months.get(dt,0)
    t['profit']=t['revenue']-t['costs']; t['monthly_sales']=months
    return t

@app.get('/api/analytics')
def analytics(authorization:Optional[str]=Header(None)):
    uid=current_user(authorization)['id']; rows=[]
    for k in ('orders','expenses','products','customers','followups','recurring'): rows += [(k,x) for x in get_records(uid,k)]
    sales=sum(money(d.get('amount') or d.get('total')) for k,d in rows if k=='orders' and str(d.get('status','')).lower()=='paid')
    expenses=sum(money(d.get('amount')) for k,d in rows if k=='expenses')
    by_product={}; by_status={}
    for k,d in rows:
        if k=='orders':
            name=d.get('product_name') or d.get('product') or 'Other'; by_product[name]=by_product.get(name,0)+money(d.get('amount') or d.get('total'))
            st=d.get('status','Pending'); by_status[st]=by_status.get(st,0)+1
    return {'sales':sales,'expenses':expenses,'profit':sales-expenses,'by_product':sorted([{'name':k,'value':v} for k,v in by_product.items()],key=lambda x:x['value'],reverse=True)[:10],'order_status':by_status}

@app.get('/api/billing')
def billing(authorization:Optional[str]=Header(None)):
    u=current_user(authorization); c=db(); s={r['key']:r['value'] for r in c.execute('SELECT key,value FROM settings').fetchall()}; p=c.execute('SELECT id,plan,amount,status,reference,created,note FROM payments WHERE user_id=? ORDER BY id DESC LIMIT 10',(u['id'],)).fetchall(); c.close()
    return {'plans':PLANS,'payment':{'account_name':s.get('payment_account_name',''),'account_number':s.get('payment_account_number',''),'bank_name':s.get('payment_bank_name',''),'instructions':s.get('payment_instructions','')},'current':{'plan':u['plan'],'active':subscription_active(u),'expires':u['subscription_expires']},'payments':[dict(x) for x in p]}

@app.post('/api/payments/submit')
async def submit_payment(plan:str=Form(...),reference:str=Form(''),screenshot:UploadFile=File(...),authorization:Optional[str]=Header(None)):
    u=current_user(authorization)
    if plan not in PLANS or plan=='free': raise HTTPException(400,'Choose a paid plan')
    if screenshot.content_type not in ALLOWED_IMAGE_TYPES: raise HTTPException(400,'Upload a JPG, PNG or WebP payment screenshot')
    data=await screenshot.read()
    if len(data)>MAX_UPLOAD: raise HTTPException(413,'Screenshot is too large. Maximum size is 5 MB')
    c=db(); existing=c.execute("SELECT id FROM payments WHERE user_id=? AND status='pending'",(u['id'],)).fetchone()
    if existing: c.close(); raise HTTPException(409,'You already have a payment confirmation awaiting review')
    c.execute('INSERT INTO payments(user_id,plan,amount,reference,filename,mime_type,file_data,status,created) VALUES(?,?,?,?,?,?,?,?,?)',(u['id'],plan,PLANS[plan]['price'],reference.strip()[:120],screenshot.filename or 'payment-screenshot',screenshot.content_type,data,'pending',now_iso())); c.commit(); c.close()
    return {'ok':True,'message':'Payment submitted. Your subscription will activate after verification.'}

@app.get('/api/payments/mine')
def my_payments(authorization:Optional[str]=Header(None)):
    uid=current_user(authorization)['id']; c=db(); r=c.execute('SELECT id,plan,amount,reference,status,note,created,reviewed FROM payments WHERE user_id=? ORDER BY id DESC',(uid,)).fetchall(); c.close(); return [dict(x) for x in r]

@app.post('/api/ai')
def ai(x:Ask,authorization:Optional[str]=Header(None)):
    u=current_user(authorization)
    if not subscription_active(u): raise HTTPException(402,'AI is available on paid plans. Open Upgrade & Billing to subscribe.')
    c=db(); used=reset_ai_if_needed(c,u); limit=PLANS[u['plan']]['ai']
    if used>=limit: c.close(); raise HTTPException(429,f"You have used your monthly AI allowance for the {PLANS[u['plan']]['name']} plan.")
    c.execute('UPDATE users SET ai_used=ai_used+1 WHERE id=?',(u['id'],)); c.commit(); c.close()
    key=os.getenv('OPENAI_API_KEY')
    if not key: return {'reply':'AI is not configured on the server yet. Add OPENAI_API_KEY in Render Environment.'}
    model=os.getenv('AI_MODEL','gpt-5.6-luna')
    prompt=f"You are SellFlow AI, a practical small-business operator. Help with sales, customer follow-ups, inventory, pricing, invoices and marketing. Be concise and actionable. Never claim to have sent a message or completed an external transaction. User request: {x.message}"
    body=json.dumps({'model':model,'input':prompt}).encode(); req=urllib.request.Request('https://api.openai.com/v1/responses',data=body,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=45) as res:
            data=json.loads(res.read())
            def extract_text(value):
                if isinstance(value,str): return value
                if isinstance(value,list):
                    return '\n'.join(x for x in (extract_text(v) for v in value) if x)
                if isinstance(value,dict):
                    if isinstance(value.get('text'),str): return value['text']
                    for key in ('output_text','content','output','message','response'):
                        if key in value:
                            found=extract_text(value[key])
                            if found: return found
                return ''
            reply=extract_text(data.get('output_text') or data.get('output') or data)
            if not reply: reply='The AI provider returned no readable text. Please try again.'
            return {'reply':reply}
    except urllib.error.HTTPError as e:
        detail=e.read().decode(errors='ignore')[:300]
        raise HTTPException(502,'AI provider request failed: '+detail)
    except Exception: raise HTTPException(502,'AI provider request failed. Check the server-side AI configuration.')

@app.get('/api/admin/payments')
def admin_payments(authorization:Optional[str]=Header(None)):
    require_admin(authorization); c=db(); rows=c.execute("SELECT p.id,p.plan,p.amount,p.reference,p.filename,p.mime_type,p.status,p.note,p.created,p.reviewed,u.name,u.email FROM payments p JOIN users u ON u.id=p.user_id ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END,p.id DESC").fetchall(); c.close(); return [dict(r) for r in rows]

@app.get('/api/admin/payments/{payment_id}/image')
def admin_payment_image(payment_id:int,authorization:Optional[str]=Header(None)):
    require_admin(authorization); c=db(); row=c.execute('SELECT mime_type,file_data FROM payments WHERE id=?',(payment_id,)).fetchone(); c.close()
    if not row: raise HTTPException(404,'Payment confirmation not found')
    return Response(content=row['file_data'],media_type=row['mime_type'],headers={'Cache-Control':'no-store'})

@app.post('/api/admin/payments/{payment_id}/review')
def review_payment(payment_id:int,x:PaymentReview,authorization:Optional[str]=Header(None)):
    require_admin(authorization); action=x.action.lower().strip();
    if action not in ('approve','reject'): raise HTTPException(400,'Action must be approve or reject')
    c=db(); p=c.execute('SELECT * FROM payments WHERE id=?',(payment_id,)).fetchone()
    if not p: c.close(); raise HTTPException(404,'Payment confirmation not found')
    if p['status']!='pending': c.close(); raise HTTPException(409,'This payment has already been reviewed')
    reviewed=now_iso()
    if action=='approve':
        expires=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat(); c.execute("UPDATE payments SET status='approved',note=?,reviewed=? WHERE id=?",(x.note.strip()[:500],reviewed,payment_id)); c.execute("UPDATE users SET plan=?,subscription_status='active',subscription_expires=?,ai_used=0,ai_reset=? WHERE id=?",(p['plan'],expires,datetime.now(timezone.utc).strftime('%Y-%m'),p['user_id']))
    else: c.execute("UPDATE payments SET status='rejected',note=?,reviewed=? WHERE id=?",(x.note.strip()[:500],reviewed,payment_id))
    c.commit(); c.close(); return {'ok':True,'message':'Payment approved and subscription activated.' if action=='approve' else 'Payment rejected.'}

@app.get('/api/admin/settings')
def admin_settings(authorization:Optional[str]=Header(None)):
    require_admin(authorization); c=db(); s={r['key']:r['value'] for r in c.execute('SELECT key,value FROM settings').fetchall()}; c.close(); return s

@app.post('/api/admin/settings')
def save_admin_settings(x:PaymentSettings,authorization:Optional[str]=Header(None)):
    require_admin(authorization); c=db(); vals={'payment_account_name':x.account_name.strip(),'payment_account_number':x.account_number.strip(),'payment_bank_name':x.bank_name.strip(),'payment_instructions':x.instructions.strip()}
    for k,v in vals.items(): c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v))
    c.commit(); c.close(); return {'ok':True}

@app.get('/api/admin/users')
def admin_users(authorization:Optional[str]=Header(None)):
    require_admin(authorization); c=db(); rows=c.execute('SELECT id,name,email,role,plan,subscription_status,subscription_expires,created FROM users ORDER BY id DESC').fetchall(); c.close(); return [dict(r) for r in rows]

@app.post('/api/admin/users/{uid}/plan')
def admin_change_plan(uid:int,x:dict,authorization:Optional[str]=Header(None)):
    require_admin(authorization); plan=x.get('plan'); days=int(x.get('days',30) or 30)
    if plan not in PLANS: raise HTTPException(400,'Invalid plan')
    c=db(); c.execute('UPDATE users SET plan=?,subscription_status=?,subscription_expires=? WHERE id=?',(plan,'active' if plan!='free' else 'active',(datetime.now(timezone.utc)+timedelta(days=days)).isoformat() if plan!='free' else None,uid)); c.commit(); c.close(); return {'ok':True}

@app.get('/invoice/{rid}',response_class=HTMLResponse)
def invoice_page(rid:int,authorization:Optional[str]=Header(None)):
    u=current_user(authorization); rows=get_records(u['id'],'invoices'); inv=next((r for r in rows if r['id']==rid),None)
    if not inv: raise HTTPException(404,'Invoice not found')
    c=db(); s={r['key']:r['value'] for r in c.execute('SELECT key,value FROM settings').fetchall()}; c.close()
    esc=lambda x:html.escape(str(x or ''))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(inv.get('invoice_no'))}</title><style>body{{font-family:Arial,sans-serif;background:#f5f7fb;color:#111;padding:30px}}.sheet{{max-width:760px;margin:auto;background:white;padding:40px;border-radius:18px}}.top{{display:flex;justify-content:space-between}}table{{width:100%;margin-top:30px;border-collapse:collapse}}td,th{{padding:12px;border-bottom:1px solid #ddd;text-align:left}}.total{{font-size:24px;font-weight:800;text-align:right;margin-top:25px}}button{{padding:12px 18px;border:0;border-radius:10px;background:#315ad8;color:white}}@media print{{button{{display:none}}body{{background:white}}.sheet{{box-shadow:none}}}}</style></head><body><div class="sheet"><div class="top"><div><h1>{esc(s.get('business_name','SellFlow Business'))}</h1><p>Invoice {esc(inv.get('invoice_no'))}</p></div><button onclick="print()">Print / Save PDF</button></div><hr><p><b>Customer:</b> {esc(inv.get('customer_name'))}</p><p><b>Email:</b> {esc(inv.get('customer_email'))}</p><table><tr><th>Description</th><th>Amount</th></tr><tr><td>{esc(inv.get('description','Business service'))}</td><td>₦{money(inv.get('amount')):,.0f}</td></tr></table><div class="total">Total: ₦{money(inv.get('amount')):,.0f}</div><p>Status: {esc(inv.get('status','Unpaid'))}</p></div></body></html>"""

@app.get('/')
def home(): return FileResponse(os.path.join(APP_DIR,'index.html'))
