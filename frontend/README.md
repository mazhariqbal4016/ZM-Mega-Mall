# ZM Mega Mall Inventory System — Frontend

The frontend is a React + Vite operations console for the existing FastAPI and Supabase backend.

## Requirements

- Node.js 20+
- pnpm
- The backend running at `http://127.0.0.1:8000`

## Setup

```powershell
cd C:\Users\khank\OneDrive\Desktop\ZM-Mega-Mall\frontend
pnpm install
Copy-Item .env.example .env
```

Set `VITE_API_BASE_URL` in `.env` if the backend is hosted somewhere else. Do not put Supabase or Gemini secrets in frontend environment variables.

## Run locally

```powershell
pnpm exec vite --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`.

## Production check

```powershell
pnpm lint
pnpm build
```

## Included screens

- Login with the existing FastAPI authentication
- Role-aware dashboard and product catalog
- Department and location filters for Grocery, Clothes, Electronics, Household,
  Cosmetics, Toys & Stationery, Food Court, and Pharmacy inventory
- Stock operations and stock history
- Staff-safe and manager-only reports
- Read-only AI assistant
- AI stock-change preview with human confirm/cancel workflow
- Pending stock-change requests

The UI sends the logged-in user's bearer token to the backend and does not store or expose service-role keys, passwords, or Gemini keys.
