# ZM Mega Mall Inventory System

## Backend

This is the starter backend for the ZM Mega Mall Inventory System, built with Python and FastAPI.

## Setup

Open PowerShell in this `backend` folder and activate the virtual environment:

```powershell
.\venv\Scripts\Activate.ps1
```

Install the project requirements:

```powershell
pip install -r requirements.txt
```

## Run the API

```powershell
uvicorn main:app --reload
```

The API will be available at:

- http://127.0.0.1:8000/
- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/docs

The backend uses Supabase for authentication and inventory data. The AI assistant is a strictly read-only Gemini integration.

## Railway deployment

This backend includes `railway.json` and `Procfile` with the production
command:

```text
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Set the variables from `.env.example` in Railway's service variables. Set a
long random `AUTOMATION_SECRET` in Railway and in the n8n Header Auth
credential before enabling the scheduled manager summary. Keep
`SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, webhook URLs, and the automation
secret out of Git and frontend variables.

## Supabase setup

1. Create a Supabase project.
2. Copy `.env.example` to `.env` and add your Supabase URL and keys.
3. Run `supabase_schema.sql` in the Supabase SQL Editor.
4. Start the API and open `/db-health` to verify the connection.

The backend prefers `SUPABASE_SERVICE_ROLE_KEY` for server-side database access and falls back to `SUPABASE_ANON_KEY` when the service-role key is not set. Never expose the service-role key to a frontend.

For the read-only AI assistant, create a Gemini API key in Google AI Studio and set `GEMINI_API_KEY` in `.env`. The optional `GEMINI_MODEL` variable defaults to `gemini-2.5-flash`. Never commit either secret or expose it to a frontend.

## Authentication and roles

Authentication endpoints:

- `POST /signup` creates staff users only.
- `POST /login` returns an access token and the user's role.
- `GET /me` requires `Authorization: Bearer <access_token>`.
- `GET /staff/products` is available to active staff and managers.
- `GET /manager/products` is available only to active managers.

Set `SUPABASE_SERVICE_ROLE_KEY` in `.env` for secure signup, profile lookups, and protected backend queries. This key must remain backend-only.

To promote a user, run the command in `promote_manager.sql` from the Supabase SQL Editor. Public signup never accepts the manager role.

## Inventory operations

Manager-only product management:

- `POST /products`
- `PUT /products/{product_id}`
- `DELETE /products/{product_id}`

Staff and managers can use:

- `POST /stock/in`
- `POST /stock/out`
- `POST /stock/damaged`
- `POST /stock/return`
- `GET /stock/history`
- `GET /stock/low`
- `GET /stock/out-of-stock`

Only managers can use:

- `POST /stock/adjustment`
- `GET /audit-logs`

Stock changes are processed atomically in Supabase and are rejected if they would make quantity negative. Every product change and stock movement creates an audit log.

## Reports and manager dashboard

Manager-only endpoints:

- `GET /manager/dashboard-summary`
- `GET /manager/recent-movements?limit=10`
- `GET /manager/reports/stock-movements`
- `GET /manager/reports/top-moving-products?days=7`
- `GET /manager/reports/damaged-stock`
- `GET /manager/reports/suppliers`
- `GET /manager/reports/categories`
- `GET /manager/reports/inventory-value`

Staff and managers can access:

- `GET /reports/low-stock`
- `GET /reports/out-of-stock`

Date filters use `YYYY-MM-DD` and are interpreted in Pakistan Standard Time. Reports are calculated from real Supabase product and stock-movement records.

## Read-only AI inventory assistant

Authenticated staff and managers can call:

- `POST /ai/chat` with `{ "message": "How many Type-C cables are available?" }`

The backend first queries an allow-listed set of read-only Supabase helpers, filters the result by role, and sends only that verified context to Gemini. Staff receive stock-safe information only. Managers can also ask for cost price, inventory value, supplier summaries, damaged stock, movement summaries, and top-moving products.

AI cannot add, remove, adjust, or otherwise modify inventory. Write requests return a read-only message and do not call any stock mutation endpoint.

## Single AI Inventory Agent

The controlled agent endpoint combines verified questions and stock-change
previews:

- `POST /ai/agent` with `{ "message": "How many Type-C cables are available?" }`
- Include an optional `conversation` array with the last user/assistant messages
  for safe short follow-up questions.

The agent automatically routes read-only questions to the existing allow-listed
Supabase helpers. Stock-change language is routed only to the pending
`stock_change_requests` workflow. It never changes product quantity directly;
confirmation is still required through the existing confirm endpoint.

## AI stock-change confirmation workflow

Step 7 keeps Gemini out of the mutation path. `POST /ai/stock-request` only parses a natural-language request and saves a `pending` row in `stock_change_requests`.

Example:

```json
{
  "message": "Add 40 Type-C cables from Ali Traders"
}
```

Authenticated users can review their pending requests with `GET /stock-change-requests/pending`. Managers can see all pending requests. A human must call either:

- `POST /stock-change-requests/{request_id}/confirm`
- `POST /stock-change-requests/{request_id}/cancel`

Confirmation re-checks permissions, request status, product activity, quantity, and available stock. Supabase performs the confirmation, stock movement, and request update atomically through the existing `apply_stock_change` function plus the Step 7 confirmation function. Cancellation never changes product quantity. AI-generated request creation, confirmation, and cancellation are recorded in `audit_logs`.

## n8n notifications and automation

Copy the n8n variables from `.env.example` into the backend `.env` and paste
the matching n8n webhook URLs. Webhook calls are queued in a background worker
after the database operation succeeds. A timeout or n8n outage is logged and
does not roll back inventory.

Events sent by FastAPI:

- `low_stock` and `out_of_stock` after stock mutations
- `stock_event` for stock in, stock out, returns, and adjustments
- `damaged_stock` for damaged movements
- `ai_stock_request_created`, `ai_stock_request_confirmed`, and
  `ai_stock_request_cancelled`
- `daily_manager_summary` from the manager-only notify endpoint

Daily summary endpoints:

- `GET /manager/automation/daily-summary`
- `POST /manager/automation/daily-summary/notify`

Both daily-summary endpoints require a manager bearer token. This allows an
n8n schedule to use an authorized manager credential without bypassing FastAPI
permissions. The payload includes dashboard totals, recent movements,
inventory cost value, selling value, and estimated profit. No service-role
key, password, access token, or Gemini key is included in any webhook payload.

### Suggested n8n workflows

For event notifications, use:

1. Webhook Trigger (POST)
2. Switch on `event_type`
3. Edit Fields to format the email subject/body
4. Gmail or Email Send node
5. Optional Google Sheets append row

Suggested subjects:

- `Low Stock Alert - {{$json.product_name}}`
- `Out of Stock - {{$json.product_name}}`
- `Inventory Change Request - {{$json.product}}`
- `Daily Inventory Summary`

For the daily summary, use an n8n Schedule Trigger followed by an HTTP Request
to `GET /manager/automation/daily-summary` with an authorized manager bearer
token, then pass the response to Edit Fields and Gmail/Email Send. n8n must
not call Supabase directly or modify inventory.
