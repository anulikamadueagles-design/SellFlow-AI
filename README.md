# SellFlow AI — Flat GitHub/Render Project

SellFlow AI is a mobile-first small-business workspace with products, customers, orders, invoices, expenses, AI assistance, subscriptions and manual bank-transfer payment confirmation.

## Flat project structure

Upload **all files directly into the GitHub repository root**. Do not create another `SellFlow-AI` folder inside the repository.

Files:

- `main.py` — FastAPI backend, SQLite data, auth, subscriptions, payment confirmations and admin review
- `index.html` — app shell
- `styles.css` — responsive UI
- `app.js` — frontend application
- `requirements.txt` — Python dependencies
- `.python-version` — pins Render to Python 3.13.5
- `render.yaml` — optional config file; you can ignore it when deploying manually
- `.gitignore`

## Render deployment — use Web Service

Use the normal **Render → New + → Web Service** flow. You do **not** need Blueprint deployment.

Settings:

- Runtime: Python 3
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Branch: `main`
- Instance: Free/lowest-cost available option while testing

The `.python-version` file pins Python to 3.13.5 to avoid Python 3.14 build problems with some dependencies.

## Required admin setup

To review payment screenshots and activate subscriptions, add these Render Environment Variables:

- `ADMIN_EMAIL` = the email you will use for the admin login
- `ADMIN_PASSWORD` = a strong password of at least 10 characters
- `ADMIN_NAME` = your preferred admin display name

On startup, SellFlow creates or promotes that account to admin.

## Optional live AI

Add:

- `OPENAI_API_KEY` = your server-side OpenAI API key
- `AI_MODEL` = optional model name supported by your OpenAI account/API

Never put the API key in GitHub or frontend JavaScript.

## Payment confirmation workflow

1. Customer creates a free account.
2. Customer opens **Upgrade & billing**.
3. Customer chooses Starter, Pro or Business.
4. Customer sees the bank/wallet account details.
5. Customer transfers the exact amount outside the website.
6. Customer taps **Send payment confirmation**.
7. The phone file/gallery picker opens.
8. Customer selects a JPG/PNG/WebP screenshot (maximum 5 MB).
9. The screenshot and payment reference are submitted.
10. Admin signs in and opens **Admin payments**.
11. Admin opens the authenticated screenshot and approves or rejects it.
12. Approval activates the selected plan for 30 days.

## Set the account customers should pay

After signing in with the admin account:

**Admin payments → Payment account settings**

Enter the exact account name, account number, bank/wallet name and instructions. Customers will see those details in the subscription dialog.

The project intentionally does **not** hard-code the handwritten bank details from the supplied image because financial account details should be verified and editable by the owner rather than copied from an unclear image.

## Current launch prices

These are the starting monthly prices in this build:

- Free — ₦0
- Starter — ₦4,999/month — 50 AI calls/month
- Pro — ₦9,999/month — 200 AI calls/month
- Business — ₦19,999/month — 600 AI calls/month

These are launch prices, not a guarantee of revenue. Current Nigerian business-software pricing shows several products around ₦4,000–₦15,000/month for entry/growth plans, while more advanced offerings can be substantially higher. Re-check your market before changing prices.

## Important storage note

Payment screenshots are stored in the SQLite database in this MVP. On a normal Render service, the local filesystem/database is not durable across every redeploy/restart. For a real paid launch, move the database to PostgreSQL and payment screenshots to durable object storage (for example, S3-compatible storage or another persistent provider) before relying on this as your only payment record.

Also add Terms/Privacy, rate limiting, email verification/password reset, audit logs, automated tests, backup/restore and a security review before handling significant customer volume.
