# SellFlow AI — mobile-first small business SaaS

A working starter SaaS with account registration/login, per-user product/customer/order/invoice/expense records, dashboard totals, optional live AI via OpenAI, and optional Paystack transaction initialization.

## Flat project layout
All project files are at the repository root: `main.py`, `index.html`, `styles.css`, `app.js`, `requirements.txt`, `render.yaml`, and this README. No nested project folder is required.

## Run locally
Requires Python 3.11+.
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
uvicorn main:app --host 0.0.0.0 --port 8000
```
Open http://localhost:8000

## Deploy on Render
1. Create a new GitHub repository named `SellFlow-AI`.
2. Upload the contents of this ZIP directly to the repository root (not the ZIP's enclosing folder).
3. In Render, choose **New + → Blueprint**, connect the repository, and apply `render.yaml`.
4. Set `OPENAI_API_KEY` to enable live AI. Set `PAYSTACK_SECRET_KEY` to enable Paystack transaction initialization. Keep secret keys only in Render Environment; never commit them.
5. Deploy and open the generated `https://sellflow-ai-....onrender.com` URL.

The included Blueprint uses a paid Starter web service because this MVP stores SQLite data on the service filesystem. For durable production data, migrate to managed PostgreSQL and configure persistent storage or use a Postgres database. Render's environment-variable guidance: https://render.com/docs/configure-environment-variables

## Included
- Responsive dark dashboard and mobile navigation
- Signup/signin with password hashing and signed expiring sessions
- Isolated per-account records
- Products, customers, orders, invoices, expenses CRUD
- Revenue/profit/pending-payment summaries
- Optional OpenAI-powered assistant
- Optional Paystack payment initialization endpoint
- Health endpoint and Render Blueprint

## Production work still required
This is a complete deployable MVP, not a claim of audited production readiness. Before taking real customer payments at scale, add PostgreSQL migrations/backups, email verification/password reset, rate limiting, audit logging, verified Paystack webhooks and subscription entitlements, privacy/terms pages, automated tests, and security review. Payment initialization alone does not verify that a payment has succeeded.
