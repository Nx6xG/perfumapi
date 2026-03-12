import os
import re
import asyncio
import httpx
from urllib.parse import quote_plus
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

from utils.db import (
    get_all_perfumes,
    search_perfumes,
    get_perfume_by_id,
    upsert_many,
    get_stats,
    update_image_url,
    update_image_urls_batch,
    get_perfumes_without_images,
    find_perfume_by_name_brand,
)
from scraper.wikiparfum import fetch_wikiparfum_image, fetch_images_batch

app = FastAPI(title="PerfumAPI", version="3.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE = "https://www.parfumo.com"


# ──────────────────────────────────────────────
# Parfumo Scraper
# ──────────────────────────────────────────────

async def parfumo_search(query: str, limit: int = 10) -> list[str]:
    """Search Parfumo and return perfume detail URLs."""
    url = f"{BASE}/s_perfumes.php?search={quote_plus(query)}"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            urls = []
            seen = set()

            for link in soup.select("a[href]"):
                href = link.get("href", "")
                if "/Perfumes/" not in href:
                    continue
                full = href if href.startswith("http") else f"{BASE}{href}"
                path = full.replace(BASE, "").strip("/")
                parts = path.split("/")
                if len(parts) < 3 or parts[0] != "Perfumes":
                    continue
                if "/prices" in full or "/reviews" in full or full in seen:
                    continue
                seen.add(full)
                urls.append(full)
                if len(urls) >= limit:
                    break

            return urls
    except Exception as e:
        print(f"Parfumo search error: {e}")
        return []


async def parfumo_detail(client: httpx.AsyncClient, url: str) -> dict | None:
    """Scrape a single Parfumo perfume detail page."""
    try:
        resp = await client.get(url, headers=HEADERS)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        text = resp.text

        # --- Name & Brand & Year (from title tag — most reliable) ---
        name = ""
        brand = ""
        year = None

        title_tag = soup.select_one("title")
        if title_tag:
            # Format: "Sauvage Parfum by Dior » Reviews..."
            t = title_tag.get_text(strip=True)
            parts = t.split(" » ")[0]  # "Sauvage Parfum by Dior"
            if " by " in parts:
                name = parts.split(" by ")[0].strip()
                brand = parts.split(" by ")[1].strip()
            else:
                name = parts.strip()

        # Year from h1 link
        h1 = soup.select_one("h1")
        if h1:
            year_link = h1.select_one("a[href*='/Release_Years/']")
            if year_link:
                try:
                    year = int(year_link.get_text(strip=True))
                except ValueError:
                    pass

        # --- Image ---
        image_url = ""
        img = soup.select_one("img[src*='media.parfumo.com/perfumes']")
        if img:
            image_url = img.get("src", "")
        if not image_url:
            og = soup.select_one("meta[property='og:image']")
            if og:
                image_url = og.get("content", "")

        # --- Rating ---
        rating = None
        rating_match = re.search(r'(\d+\.?\d*)\s*/\s*10\s*\n?\s*\d+\s*Ratings', text)
        if rating_match:
            try:
                rating = float(rating_match.group(1))
            except ValueError:
                pass

        # --- Votes ---
        votes = None
        votes_match = re.search(r'(\d+)\s*Ratings', text)
        if votes_match:
            try:
                votes = int(votes_match.group(1))
            except ValueError:
                pass

        # --- Notes ---
        notes_top = []
        notes_mid = []
        notes_base = []

        pyramid_html = str(soup)
        top_match = re.search(r'Top Notes?(.*?)Heart Notes?', pyramid_html, re.DOTALL | re.IGNORECASE)
        mid_match = re.search(r'Heart Notes?(.*?)Base Notes?', pyramid_html, re.DOTALL | re.IGNORECASE)
        base_match = re.search(r'Base Notes?(.*?)(?:Perfumer|Videos|Ratings|Reviews|Submitted|$)', pyramid_html, re.DOTALL | re.IGNORECASE)

        def extract_notes(html_chunk: str) -> list[str]:
            chunk_soup = BeautifulSoup(html_chunk, "html.parser")
            notes = []
            for img_tag in chunk_soup.select("img[alt]"):
                alt = img_tag.get("alt", "").strip()
                if alt and len(alt) > 1 and alt not in ["Top Notes", "Heart Notes", "Base Notes"]:
                    notes.append(alt)
            return notes

        if top_match:
            notes_top = extract_notes(top_match.group(1))
        if mid_match:
            notes_mid = extract_notes(mid_match.group(1))
        if base_match:
            notes_base = extract_notes(base_match.group(1))

        # --- Longevity & Sillage ---
        longevity = ""
        sillage = ""
        lon_match = re.search(r'Longevity\s*(\d+\.?\d*)', text)
        sil_match = re.search(r'Sillage\s*(\d+\.?\d*)', text)
        if lon_match:
            longevity = f"{lon_match.group(1)}/10"
        if sil_match:
            sillage = f"{sil_match.group(1)}/10"

        # --- Gender ---
        gender = "Unisex"
        if "for men" in text.lower()[:2000]:
            if "for women" in text.lower()[:2000]:
                gender = "Unisex"
            else:
                gender = "Men"
        elif "for women" in text.lower()[:2000]:
            gender = "Women"

        # --- Description ---
        description = ""
        desc_match = re.search(r'(A (?:popular )?perfume by .+?(?:released in \d{4}\.?|\.))', text)
        if desc_match:
            description = desc_match.group(1)[:300]

        if not name:
            return None

        return {
            "name": name,
            "brand": brand,
            "release_year": year,
            "gender": gender,
            "notes_top": notes_top,
            "notes_middle": notes_mid,
            "notes_base": notes_base,
            "rating": rating,
            "votes": votes,
            "description": description,
            "longevity": longevity,
            "sillage": sillage,
            "image_url": image_url,
            "perfume_url": url,
        }

    except Exception as e:
        print(f"Parfumo detail error for {url}: {e}")
        return None


async def scrape_parfumo(query: str, limit: int = 10) -> list[dict]:
    """Search Parfumo → scrape details IN PARALLEL → return list."""
    urls = await parfumo_search(query, limit=limit)
    if not urls:
        return []

    # Fetch all detail pages in parallel (much faster than sequential)
    results = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        tasks = [parfumo_detail(client, url) for url in urls]
        details = await asyncio.gather(*tasks, return_exceptions=True)

        for detail in details:
            if isinstance(detail, dict) and detail.get("name"):
                results.append(detail)

    return results[:limit]


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "PerfumAPI v3.2 — Parfumo Scraper + Wikiparfum Images", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    return get_stats()


@app.get("/perfumes")
def list_perfumes(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    data = get_all_perfumes(limit=limit, offset=offset)
    return {"perfumes": data, "count": len(data)}


@app.get("/perfumes/search/{query}")
def search_local(query: str, limit: int = Query(20, ge=1, le=100)):
    data = search_perfumes(query, limit=limit)
    return {"perfumes": data, "count": len(data)}


@app.get("/perfumes/{perfume_id}")
def get_perfume(perfume_id: str):
    data = get_perfume_by_id(perfume_id)
    if not data:
        raise HTTPException(status_code=404, detail="Perfume not found")
    return data


@app.get("/search")
async def smart_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=20),
):
    """
    SMART SEARCH — main endpoint for FLACON.
    1. Search local DB
    2. If <3 results → scrape Parfumo (parallel)
    3. Cache in DB for next time
    """
    local_results = search_perfumes(q, limit=limit)

    if len(local_results) >= 3:
        return {"perfumes": local_results, "count": len(local_results), "source": "local"}

    # Scrape Parfumo
    scraped = []
    try:
        scraped = await scrape_parfumo(q, limit=limit)
    except Exception as e:
        print(f"Scrape failed: {e}")

    # Cache in DB
    if scraped:
        try:
            upsert_many(scraped)
        except Exception as e:
            print(f"DB save error: {e}")

    # Merge & deduplicate
    seen = set()
    combined = []
    for p in local_results + scraped:
        key = f"{p.get('name', '').lower()}_{p.get('brand', '').lower()}"
        if key not in seen:
            seen.add(key)
            combined.append(p)

    return {
        "perfumes": combined[:limit],
        "count": len(combined[:limit]),
        "source": "local+parfumo" if scraped else "local",
        "scraped_count": len(scraped),
    }


# ──────────────────────────────────────────────
# Wikiparfum Image Endpoints
# ──────────────────────────────────────────────

@app.get("/image")
async def get_image(
    name: str = Query(..., min_length=1),
    brand: str = Query(""),
    perfume_id: str = Query(""),
):
    """
    Fetch a perfume bottle image from Wikiparfum.

    1. If perfume_id given → check DB first, return if image exists
    2. Scrape Wikiparfum for the image
    3. If perfume_id given → save image_url to DB
    4. Return the image URL

    Called by FLACON frontend when a perfume has no image_url.
    """
    # Check if we already have an image in DB
    if perfume_id:
        try:
            existing = get_perfume_by_id(perfume_id)
            if existing and existing.get("image_url"):
                return {
                    "image_url": existing["image_url"],
                    "source": "cache",
                    "perfume_id": perfume_id,
                }
        except Exception:
            pass

    # Scrape Wikiparfum
    image_url = None
    try:
        image_url = await fetch_wikiparfum_image(name, brand)
    except Exception as e:
        print(f"Wikiparfum scrape error: {e}")

    if not image_url:
        raise HTTPException(
            status_code=404,
            detail=f"No image found for '{name}' by '{brand}' on Wikiparfum",
        )

    # Save to DB if we have an ID
    if perfume_id:
        try:
            update_image_url(perfume_id, image_url)
        except Exception as e:
            print(f"DB image save error: {e}")

    return {
        "image_url": image_url,
        "source": "wikiparfum",
        "perfume_id": perfume_id or None,
    }


@app.post("/images/batch")
async def batch_fetch_images(
    limit: int = Query(20, ge=1, le=100),
):
    """
    Background job: fetch images for perfumes that don't have one.
    Processes up to `limit` perfumes without images.
    Returns how many were updated.
    """
    # Get perfumes without images
    perfumes = get_perfumes_without_images(limit=limit)

    if not perfumes:
        return {"message": "All perfumes have images", "updated": 0}

    # Fetch images in parallel (with rate limiting)
    image_map = await fetch_images_batch(perfumes, max_concurrent=3)

    # Save to DB
    updated = 0
    if image_map:
        try:
            update_image_urls_batch(image_map)
            updated = len(image_map)
        except Exception as e:
            print(f"Batch DB save error: {e}")

    return {
        "processed": len(perfumes),
        "found": len(image_map),
        "updated": updated,
        "missing": len(perfumes) - len(image_map),
    }