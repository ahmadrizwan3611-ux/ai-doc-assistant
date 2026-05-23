# DevFlow — AI-Powered Developer Workspace

DevFlow is an AI-powered developer workspace for software teams. It helps developers generate documentation, analyze bugs, check project health, create developer tasks, document GitHub repositories, save workspace history, and manage team workspaces from one SaaS-style dashboard.

## Live Demo

Backend / App URL:

```text
https://ai-doc-assistant-production-7946.up.railway.app
```

## Core Features

- AI documentation generator for uploaded code files and project folders
- GitHub repository documentation from a repo URL
- AI bug analyzer for terminal errors, tracebacks, and logs
- Project health reports with scoring and improvement suggestions
- Team task generator from client requirements or meeting notes
- Supabase authentication with signup and login
- Team workspaces
- Saved documentation history
- Export to PDF and Markdown
- Usage limits for Free users
- Stripe Checkout subscription flow
- Stripe Customer Portal subscription management
- Premium SaaS UI with light and dark mode

## Tech Stack

### Frontend

- React
- JavaScript
- CSS
- jsPDF

### Backend

- Python
- Flask
- Flask-CORS
- Groq AI API
- Supabase
- Stripe
- GitHub REST API

### Database and Auth

- Supabase PostgreSQL
- Supabase Authentication
- Row Level Security

### Deployment

- Railway for backend/app deployment
- Stripe Webhooks for subscription updates

## Project Structure

```text
ai-doc-assistant/
├── app.py
├── requirements.txt
├── Procfile
├── railway.json
├── README.md
├── .gitignore
├── frontend/
│   ├── package.json
│   └── src/
│       ├── App.js
│       ├── App.css
│       └── index.js
└── static/
```

## Environment Variables

Create a `.env` file locally. Do not commit it to GitHub.

```env
FRONTEND_URL=http://localhost:3000

GROQ_API_KEY=your_groq_api_key

SUPABASE_URL=your_supabase_project_url
SUPABASE_ANON_KEY=your_supabase_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_supabase_service_role_key

STRIPE_SECRET_KEY=sk_test_your_stripe_secret_key
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret
STRIPE_PRO_PRICE_ID=price_your_pro_monthly_price_id
STRIPE_TEAM_PRICE_ID=price_your_team_monthly_price_id
```

For Railway, add the same variables in:

```text
Railway Project → Service → Variables
```

For production deployment, update:

```env
FRONTEND_URL=https://your-production-domain.com
```

## Local Backend Setup

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Backend runs by default on:

```text
http://127.0.0.1:5000
```

## Local Frontend Setup

```bash
cd frontend
npm install
npm start
```

Frontend runs by default on:

```text
http://localhost:3000
```

## Stripe Local Webhook Testing

Run Flask first:

```bash
python app.py
```

Then open another terminal inside the Stripe CLI folder and run:

```bash
stripe listen --forward-to http://127.0.0.1:5000/stripe/webhook
```

Copy the webhook signing secret shown by Stripe CLI and add it to `.env`:

```env
STRIPE_WEBHOOK_SECRET=whsec_xxxxxxxxx
```

## Stripe Railway Webhook URL

For Railway deployment, create a Stripe webhook endpoint with this format:

```text
https://your-railway-app.up.railway.app/stripe/webhook
```

For the current Railway project:

```text
https://ai-doc-assistant-production-7946.up.railway.app/stripe/webhook
```

Recommended Stripe events:

```text
checkout.session.completed
customer.subscription.created
customer.subscription.updated
customer.subscription.deleted
invoice.paid
invoice.payment_failed
```

## Railway Deployment

Railway should use:

```text
Procfile
requirements.txt
```

Recommended start command:

```bash
gunicorn app:app
```

If Railway asks for environment variables, add them in the Railway Variables panel.

## Safe Git Push

Before pushing, make sure `.env` is ignored.

```bash
git status
git add README.md requirements.txt .gitignore Procfile railway.json app.py frontend/src/App.js frontend/src/App.css
git commit -m "Prepare DevFlow SaaS deployment"
git push origin main
```

If `.env` appears in `git status`, do not commit it.

## Roadmap Status

### Completed

- Core AI engine
- AI documentation generator
- Bug analyzer
- Project health report
- Task generator
- Supabase login/signup
- Team workspaces
- GitHub repository documentation
- Saved documentation history
- Usage limits
- Stripe billing foundation
- Premium SaaS UI

### Next

- Live deployment testing
- Public landing page polish
- Production domain
- Code Review Assistant
- Jira/Trello/Slack integrations
- VS Code extension
- API access
- Enterprise admin dashboard

## Founder

Muhammad Ahmad

GitHub:

```text
https://github.com/ahmadrizwan3611-ux
```
