# SellFlow AI 2.0

Mobile-first SaaS business operating system for small businesses and solo entrepreneurs.

## Included in this version
- Dashboard, revenue/profit and inventory alerts
- Products, stock and low-stock thresholds
- Customers with WhatsApp click-to-chat
- Orders and payment status
- Printable invoices / receipt workflow
- Expenses and profit tracking
- Customer follow-up scheduling and WhatsApp message links
- Recurring customer revenue / renewal tracking
- Analytics for sales, expenses, profit, top products and order status
- AI business assistant on paid plans
- Free / Starter / Pro / Business subscriptions
- Manual payment screenshot confirmation and admin approval
- Admin customer subscription management
- Admin payment account settings
- Existing payment account defaults: Dauda Joseph / 8085743879 / Opay

## Deploy to the existing Render service
Build command:
`pip install -r requirements.txt`

Start command:
`uvicorn main:app --host 0.0.0.0 --port $PORT`

Keep `.python-version` as `3.13.5`.

## Render environment variables
Set these on the existing Render service:
- `ADMIN_EMAIL`
- `ADMIN_PASSWORD` (10+ characters)
- `ADMIN_NAME`
- `OPENAI_API_KEY`
- Optional: `AI_MODEL` (defaults to `gpt-5.6-luna`)
- Optional: `JWT_SECRET` (recommended for production)
- Optional: `DATABASE_PATH`

Never commit API keys or admin passwords to GitHub.

## Notes
The WhatsApp feature creates a secure click-to-chat link and message draft. Actual WhatsApp Business API automation requires Meta/WhatsApp Business credentials and webhooks; this package does not pretend to send messages automatically without those credentials.

The recurring feature is a business tracker for renewals and next-due dates. It does not charge customers automatically.

For production scale, move SQLite and payment screenshots to persistent managed storage/database, add backups, rate limiting, email verification, password reset, audit logs, legal pages and automated tests.
