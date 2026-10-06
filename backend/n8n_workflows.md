# n8n Inventory Workflows

FastAPI sends small, role-safe JSON payloads to n8n. n8n never receives the
Supabase service-role key and must not write to Supabase or inventory tables.

## Event workflow

Create one n8n workflow for each webhook URL, or use one shared workflow with
a Switch node:

1. **Webhook Trigger** — POST, copy the matching production webhook URL into
   the backend `.env` variable.
2. **Switch** — route on `event_type`:
   `low_stock`, `out_of_stock`, `damaged_stock`, `stock_event`, or an
   `ai_stock_request_*` event.
3. **Edit Fields / Set** — format the subject and email body.
4. **Gmail** or **Email Send** — send the notification.
5. Optional **Google Sheets** — append the safe event fields for history.

The import-ready workflow files in this folder use these production URL
mappings:

- `n8n_low_stock_alert.json` receives both `low_stock` and `out_of_stock` and
  maps to `N8N_LOW_STOCK_WEBHOOK_URL`.
- `n8n_damaged_stock_alert.json` receives `damaged_stock` and maps to
  `N8N_STOCK_EVENT_WEBHOOK_URL`.
- `n8n_ai_stock_request_notifications.json` maps to
  `N8N_AI_REQUEST_WEBHOOK_URL`.

Suggested subjects:

- `Low Stock Alert - {{$json.product_name}}`
- `Out of Stock - {{$json.product_name}}`
- `Damaged Stock Alert - {{$json.product_name}}`
- `Inventory Change Request - {{$json.product}}`

## Daily manager summary workflow

1. **Schedule Trigger** — once daily at the manager's chosen time.
2. **HTTP Request** — `GET /manager/automation/daily-summary` with an
   authorized manager bearer token stored in an n8n credential.
3. **Edit Fields / Set** — format totals and recent movements.
4. **Gmail** or **Email Send** — subject `Daily Inventory Summary`.
5. Optional **Google Sheets** — append the summary.

The endpoint is manager-protected by FastAPI. Do not use an anonymous request
or a service-role key in n8n. The response contains current active-product,
stock, low-stock, out-of-stock, damaged, recent-movement, inventory-cost, and
estimated-profit values.

## Example event payload

```json
{
  "event_type": "low_stock",
  "occurred_at": "2026-10-03T12:00:00+00:00",
  "product_id": 1,
  "product_name": "Type-C Cable",
  "sku": "ELEC-TC-001",
  "movement_type": "stock_out",
  "quantity": 2,
  "old_quantity": 7,
  "new_quantity": 5,
  "minimum_stock": 5,
  "location": "Shelf A1",
  "reason": "Sale",
  "user_id": "user-uuid"
}
```

AI request events use the same webhook pattern and include `request_id`,
`product`, `action`, `quantity`, `supplier`, `requested_by`, and `status`.
The payload sanitizer removes keys that look like passwords, tokens, API keys,
service-role keys, or secrets before sending.
