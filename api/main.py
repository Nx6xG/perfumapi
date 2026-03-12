import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from utils.db import (
    get_all_perfumes,
    search_perfumes,
    get_perfume_by_id,
    upsert_perfume,
    upsert_many,
    get_stats,
)
from scraper.scrape import scrape_perfume_detail, search_and_scrape

app = FastAPI(
    title="PerfumAPI",
    description="Parfum-Datenbank API mit Auto-Scrape von Fragrantica",
    version="2.0.0",
)

# CORS — allow all origins (needed for FLACON frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# === Models ===

class ScrapeUrlRequest(BaseModel):
    perfume_url: str


class ScrapeRequest(BaseModel):
    query: str
    limit: int = 5


# === Public Endpoints ===

@app.get("/")
def root():
    return {"message": "PerfumAPI v2.0 — Parfum-Datenbank", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    return get_stats()


@app.get("/perfumes")
def list_perfumes(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    """List all perfumes in the database."""
    data = get_all_perfumes(limit=limit, offset=offset)
    return {"perfumes": data, "count": len(data)}


@app.get("/perfumes/search/{query}")
def search(query: str, limit: int = Query(20, ge=1, le=100)):
    """Search perfumes in the local database only."""
    data = search_perfumes(query, limit=limit)
    return {"perfumes": data, "count": len(data)}


@app.get("/perfumes/{perfume_id}")
def get_perfume(perfume_id: str):
    """Get a single perfume by ID."""
    data = get_perfume_by_id(perfume_id)
    if not data:
        raise HTTPException(status_code=404, detail="Perfume not found")
    return data


@app.get("/search")
def smart_search(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=20),
    auto_scrape: bool = Query(True, description="Auto-scrape from Fragrantica if not enough local results"),
    min_local: int = Query(3, description="Minimum local results before triggering scrape"),
):
    """
    SMART SEARCH — The main endpoint for FLACON.
    
    1. Searches the local database first
    2. If fewer than `min_local` results found AND `auto_scrape` is True:
       - Searches Fragrantica
       - Scrapes details for each result
       - Saves to local database
       - Returns combined results
    3. All results are returned in a unified format
    """
    # Step 1: Search local DB
    local_results = search_perfumes(q, limit=limit)

    if len(local_results) >= min_local or not auto_scrape:
        return {
            "perfumes": local_results,
            "count": len(local_results),
            "source": "local",
        }

    # Step 2: Auto-scrape from Fragrantica
    try:
        scraped = search_and_scrape(q, limit=limit)
    except Exception as e:
        print(f"Scrape failed: {e}")
        # Return whatever local results we have
        return {
            "perfumes": local_results,
            "count": len(local_results),
            "source": "local",
            "scrape_error": str(e),
        }

    # Step 3: Save scraped results to DB
    if scraped:
        try:
            upsert_many(scraped)
        except Exception as e:
            print(f"DB upsert failed: {e}")

    # Step 4: Merge and deduplicate
    seen_urls = set()
    combined = []

    for p in local_results:
        url = p.get("perfume_url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            combined.append(p)

    for p in scraped:
        url = p.get("perfume_url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            combined.append(p)

    return {
        "perfumes": combined[:limit],
        "count": len(combined[:limit]),
        "source": "local+scraped",
        "scraped_count": len(scraped),
    }


# === Scrape Endpoints ===

@app.post("/scrape/url")
def scrape_by_url(request: ScrapeUrlRequest):
    """Scrape a single perfume by Fragrantica URL and save to DB."""
    data = scrape_perfume_detail(request.perfume_url)
    if not data or not data.get("name"):
        raise HTTPException(status_code=400, detail="Could not scrape perfume from URL")

    try:
        saved = upsert_perfume(data)
        return {"perfume": data, "saved": bool(saved)}
    except Exception as e:
        return {"perfume": data, "saved": False, "error": str(e)}


@app.post("/scrape/search")
def scrape_search(request: ScrapeRequest):
    """Search Fragrantica, scrape results, and save to DB."""
    scraped = search_and_scrape(request.query, limit=request.limit)

    if not scraped:
        raise HTTPException(status_code=404, detail="No results found on Fragrantica")

    try:
        upsert_many(scraped)
    except Exception as e:
        print(f"DB save error: {e}")

    return {
        "perfumes": scraped,
        "count": len(scraped),
        "saved": True,
    }
