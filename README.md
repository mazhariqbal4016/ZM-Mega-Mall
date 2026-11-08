# ZM Mega Mall

Inventory management system for ZM Mega Mall, with a React frontend, FastAPI backend, Supabase authentication/database, and optional Gemini/n8n integrations.

## Project structure

- `frontend/` — React + Vite web app
- `backend/` — FastAPI API
- `backend/supabase_schema.sql` — database schema and functions
- `backend/mall_departments_seed.sql` — departments and safe sample products

## Local setup

1. Configure the variables in `backend/.env.example` in `backend/.env`.
2. Run `backend/supabase_schema.sql` in the Supabase SQL Editor, then run the department seed if sample data is needed.
3. Install backend packages with `pip install -r backend/requirements.txt`.
4. Start the API from `backend/` with `uvicorn main:app --reload`.
5. Copy `frontend/.env.example` to `frontend/.env`, confirm `VITE_API_BASE_URL`, then run `npm install` and `npm run dev` from `frontend/`.

## Production configuration

Deploy the backend first and set its public URL in `frontend/.env` as `VITE_API_BASE_URL`. Add the frontend origin to the backend `CORS_ORIGINS` value. Keep `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, webhook URLs, and `AUTOMATION_SECRET` server-side only; never commit `.env` files or secrets.

## Validation

```powershell
cd frontend
npm run build
npm run lint

cd ../backend
python -m compileall -q .
```
