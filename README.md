# PerfumAPI — Parfum-Datenbank API

Selbst-gehostete API die Parfum-Daten aus Fragrantica scrapt und in Supabase speichert. Designed als Backend für die FLACON App.

## Key Feature: Smart Search

`GET /search?q=Sauvage` sucht zuerst in der lokalen DB. Wenn zu wenig Ergebnisse gefunden werden, scrapt die API automatisch Fragrantica und speichert die neuen Daten. Beim nächsten Mal ist das Parfum sofort verfügbar.

## Setup

### 1. Supabase

Nutze dein bestehendes Supabase Projekt. Führe `migrations/001_perfume_catalog.sql` im SQL Editor aus.

Du brauchst den **Service Role Key** (nicht den Anon Key) — findest du unter Settings → API → service_role.

### 2. Render Deployment

1. Fork dieses Repo auf GitHub
2. Geh zu [render.com](https://render.com) → New → Web Service
3. Verbinde dein GitHub Repo
4. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
5. Environment Variables:
   - `SUPABASE_URL` = deine Supabase URL
   - `SUPABASE_KEY` = dein Anon Key
   - `SUPABASE_SERVICE_KEY` = dein Service Role Key
6. Deploy!

### 3. In FLACON konfigurieren

Setze in deiner FLACON `.env`:
```
VITE_PERFUMAPI_URL=https://dein-service.onrender.com
```

## API Endpoints

| Endpoint | Beschreibung |
|----------|-------------|
| `GET /search?q=...` | **Smart Search** — DB + Auto-Scrape |
| `GET /perfumes/search/{query}` | Nur lokale DB Suche |
| `GET /perfumes` | Alle Parfums auflisten |
| `GET /perfumes/{id}` | Einzelnes Parfum |
| `GET /stats` | Statistiken |
| `POST /scrape/url` | Einzelne URL scrapen |
| `POST /scrape/search` | Fragrantica suchen & scrapen |

## Hinweis

Der Scraper respektiert Fragrantica mit 2 Sekunden Delay zwischen Requests. Nutze ihn verantwortungsvoll und nur für persönliche/educational Zwecke.
