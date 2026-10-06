# n8n Import Guide — ZM Mega Mall Inventory System

These workflows are notification-only. They do not connect to Supabase and do
not confirm, cancel, or modify inventory. FastAPI remains the only service
allowed to change stock.

## Database credential decision

Do not create or configure a PostgreSQL credential for these workflows. They
already receive notification payloads from FastAPI, and the daily summary is
read through the manager-protected FastAPI endpoint. This avoids giving n8n
database access entirely, which is safer than creating an unused database role
and password. If a future workflow genuinely needs database reads, create a
separate read-only role and credential at that time; never use the Supabase
`postgres` or `service_role` credential in n8n.

## 1. Open n8n

Open your n8n instance and sign in. Download or copy these four JSON files
from the backend folder:

- `n8n_low_stock_alert.json`
- `n8n_damaged_stock_alert.json`
- `n8n_ai_stock_request_notifications.json`
- `n8n_daily_manager_summary.json`

## 2. Import each workflow

In n8n, select **Workflows**, choose **Import from File**, and import each JSON
file separately. The imported workflows are inactive by default.

## 3. Connect Gmail credentials

Open every Gmail node and select or create your Gmail OAuth2 credential.
Credentials are intentionally not included in these files. The Gmail nodes
use `sanakhan031788@gmail.com` as the configured manager notification
recipient.

If a workflow was imported before this update, replace its Gmail **Send To**
value with `sanakhan031788@gmail.com` before activation.

## 4. Configure the daily-summary HTTP credential

The daily workflow contains an HTTP Request node with a placeholder
`httpHeaderAuth` credential named `REPLACE_AFTER_IMPORT`.

Create or select an n8n Header Auth credential with:

- Header name: `Authorization`
- Header value: `Bearer <manager-access-token>`

Store the real token only in the n8n credential, never in this JSON file or in
the repository. The FastAPI endpoint is manager-protected.

Update the URL in **Get Manager Summary** if n8n cannot reach the local
machine. The default is:

```text
http://127.0.0.1:8000/manager/automation/daily-summary
```

For hosted n8n, replace `127.0.0.1` with a securely reachable backend URL.

## 5. Activate workflows

Test each workflow manually first, then activate it using the n8n toggle.
Webhook workflows use their **Production URL** only after activation.

The webhook paths are:

| Workflow | Path |
|---|---|
| Low Stock + Out of Stock Alerts | `inventory-stock-alert` |
| Damaged Stock Alert | `inventory-damaged-alert` |
| AI Stock Request Notifications | `inventory-ai-request` |

## 6. Copy Production Webhook URLs

After activation, copy the Production URL from each webhook node. Add these
values to the backend `.env` file:

```env
N8N_LOW_STOCK_WEBHOOK_URL=<Production URL from Low Stock + Out of Stock Alerts>
N8N_STOCK_EVENT_WEBHOOK_URL=<Production URL from Damaged Stock Alert>
N8N_AI_REQUEST_WEBHOOK_URL=<Production URL from AI Stock Request Notifications>
```

The low-stock Production URL intentionally handles both `low_stock` and
`out_of_stock` events. The damaged-stock Production URL handles
`damaged_stock` events.

## 7. Daily summary environment variable

The supplied daily workflow uses a Schedule Trigger and calls the protected
FastAPI endpoint directly. It does **not** need
`N8N_DAILY_SUMMARY_WEBHOOK_URL`.

That variable remains available for the optional backend endpoint:

```text
POST /manager/automation/daily-summary/notify
```

Leave it blank when using the supplied Schedule Trigger workflow. If you later
configure a separate n8n webhook for that optional endpoint, set:

```env
N8N_DAILY_SUMMARY_WEBHOOK_URL=<optional daily-summary webhook URL>
```

## 8. Restart FastAPI

After saving `.env`, restart the backend:

```powershell
cd C:\Users\khank\OneDrive\Desktop\ZM-Mega-Mall\backend
.\venv\Scripts\Activate.ps1
uvicorn main:app --reload
```

## 9. Test the workflows

Use n8n's test webhook URL while testing, or activate the workflow and use
the Production URL. Example safe payloads:

### Low stock

```json
{
  "event_type": "low_stock",
  "product_name": "Type-C Cable",
  "sku": "ELEC-TC-001",
  "new_quantity": 5,
  "minimum_stock": 5,
  "location": "Shelf A1"
}
```

### Out of stock

```json
{
  "event_type": "out_of_stock",
  "product_name": "LED Bulb",
  "sku": "ELEC-LB-001",
  "new_quantity": 0,
  "minimum_stock": 5,
  "location": "Shelf A2"
}
```

### Damaged stock

```json
{
  "event_type": "damaged_stock",
  "product_name": "Type-C Cable",
  "quantity": 2,
  "reason": "Broken packaging",
  "user_id": "staff-user-id",
  "occurred_at": "2026-10-03T12:00:00+00:00"
}
```

### AI request

```json
{
  "event_type": "ai_stock_request_created",
  "request_id": 12,
  "product": "Type-C Cable",
  "action": "stock_in",
  "quantity": 40,
  "supplier": "Ali Traders",
  "requested_by": "staff-user-id",
  "status": "pending"
}
```

The backend sends only safe event data. Do not add passwords, access tokens,
Supabase keys, Gemini keys, or Gmail credentials to test payloads.

## Simple click-by-click checklist

### Workflow 1 — Low Stock + Out of Stock

1. Open **Workflows** in n8n.
2. Click **Import from File**. In some n8n versions this is under the **Add
   workflow** or three-dot menu.
3. Select `n8n_low_stock_alert.json`.
4. Open both nodes named **Gmail - Low Stock Alert** and **Gmail - Out of
   Stock Alert**.
5. In each Gmail node, open **Credential** and select your Gmail OAuth2
   credential. If none exists, choose **Create New Credential**, then complete
   Google's authorization screen yourself.
6. Confirm each **Send To** field is `sanakhan031788@gmail.com`.
7. Click **Save**, then switch the workflow to **Active**.
8. Open **Inventory Stock Alert Webhook**, choose **Production URL**, and copy
   it for `N8N_LOW_STOCK_WEBHOOK_URL`.

The Switch node already routes `low_stock` and `out_of_stock` separately.

### Workflow 2 — Damaged Stock

1. Use **Workflows → Import from File**.
2. Select `n8n_damaged_stock_alert.json`.
3. Open **Gmail - Damaged Stock Alert**.
4. Select/connect the Gmail OAuth2 credential.
5. Confirm **Send To** is `sanakhan031788@gmail.com`.
6. Click **Save**, then switch the workflow to **Active**.
7. Open **Damaged Stock Webhook**, choose **Production URL**, and copy it for
   `N8N_STOCK_EVENT_WEBHOOK_URL`.

The IF node accepts only `event_type = damaged_stock`.

### Workflow 3 — AI Stock Request Notifications

1. Use **Workflows → Import from File**.
2. Select `n8n_ai_stock_request_notifications.json`.
3. Open all three Gmail nodes: **Gmail - Request Created**, **Gmail - Request
   Confirmed**, and **Gmail - Request Cancelled**.
4. Select the same Gmail OAuth2 credential in each node.
5. Confirm all three **Send To** fields are `sanakhan031788@gmail.com`.
6. Click **Save**, then switch the workflow to **Active**.
7. Open **AI Stock Request Webhook**, choose **Production URL**, and copy it for
   `N8N_AI_REQUEST_WEBHOOK_URL`.

The Switch node handles created, confirmed, and cancelled notifications only;
it has no inventory write node.

### Workflow 4 — Daily Manager Summary

1. Use **Workflows → Import from File**.
2. Select `n8n_daily_manager_summary.json`.
3. Open **Get Manager Summary**.
4. Replace the URL with the reachable backend URL if n8n is hosted remotely.
5. In **Authentication**, select the placeholder `httpHeaderAuth` credential
   or create a new **Header Auth** credential with header name
   `Authorization` and value `Bearer <manager-token>`.
6. Do not put the real token in the workflow JSON or repository.
7. Open **Gmail - Daily Manager Summary**, select Gmail OAuth2, and replace
   `sanakhan031788@gmail.com`.
8. In workflow **Settings**, set timezone to `Asia/Karachi` if that option is
   available. If the setting is global in your n8n version, set the n8n
   instance timezone there.
9. Confirm **Every Day at 8 PM** uses cron `0 20 * * *`.
10. Click **Save**, then switch the workflow to **Active**.

This workflow has no webhook, so it has no Production Webhook URL and does not
need `N8N_DAILY_SUMMARY_WEBHOOK_URL`.

## Backend URL checklist after manual n8n setup

The backend `.env` already contains blank entries for these variables:

```env
N8N_LOW_STOCK_WEBHOOK_URL=
N8N_STOCK_EVENT_WEBHOOK_URL=
N8N_AI_REQUEST_WEBHOOK_URL=
N8N_DAILY_SUMMARY_WEBHOOK_URL=
```

Paste the three copied Production URLs into the first three entries and leave
the fourth blank. Then restart FastAPI. The final checklist is:

- [ ] Workflow 1 imported
- [ ] Workflow 1 Gmail connected
- [ ] Workflow 1 manager email changed
- [ ] Workflow 1 activated
- [ ] Workflow 1 Production URL copied
- [ ] Workflow 2 imported, Gmail connected, manager email changed, activated
- [ ] Workflow 2 Production URL copied
- [ ] Workflow 3 imported, Gmail connected, manager email changed, activated
- [ ] Workflow 3 Production URL copied
- [ ] Workflow 4 imported, Header Auth and Gmail configured
- [ ] Workflow 4 timezone set to Asia/Karachi
- [ ] Workflow 4 activated
- [ ] Three backend `.env` URLs updated
- [ ] `N8N_DAILY_SUMMARY_WEBHOOK_URL` left blank
- [ ] FastAPI restarted
- [ ] Low-stock and out-of-stock tests passed
- [ ] Damaged-stock test passed
- [ ] AI created/confirmed/cancelled tests passed
- [ ] Daily summary test passed
- [ ] Failure-safety test passed
