import os
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from utils.db import (
    get_all_perfumes,
    search_perfumes,
    get_perfume_by_id,
    upsert_many,
    get_stats,
)

app = FastAPI(title="PerfumAPI", version="2.2.0")

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


@app.get("/")
def root():
    return {"message": "PerfumAPI v2.2", "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    return get_stats()


@app.get("/test-sites")
async def test_sites():
    """Test which perfume sites are reachable from this server."""
    results = {}
    urls = {
        "parfumo": "https://www.parfumo.com/",
        "parfumo_search": "https://www.parfumo.com/s_perfumes.php?search=sauvage",
        "fragrantica": "https://www.fragrantica.com/",
        "openfragrancedb": "https://openfragrancedb.com/",
        "basenotes": "https://basenotes.com/",
    }
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for name, url in urls.items():
            try:
                resp = await client.get(url, headers=HEADERS)
                text_lower = resp.text.lower()[:3000]
                blocked = "cloudflare" in text_lower or "captcha" in text_lower or "challenge" in text_lower
                results[name] = {
                    "status": resp.status_code,
                    "content_length": len(resp.text),
                    "cloudflare_blocked": blocked,
                    "reachable": resp.status_code == 200 and len(resp.text) > 1000 and not blocked,
                    "first_100_chars": resp.text[:100],
                }
            except Exception as e:
                results[name] = {"reachable": False, "error": str(e)}
    return results


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
    """Smart search: local DB first, then scrape if needed."""
    local_results = search_perfumes(q, limit=limit)

    if len(local_results) >= 3:
        return {"perfumes": local_results, "count": len(local_results), "source": "local"}

    # Try external scrape
    scraped = await scrape_external(q, limit=limit)

    if scraped:
        try:
            upsert_many(scraped)
        except Exception as e:
            print(f"DB save error: {e}")

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
        "source": "local+scraped" if scraped else "local",
        "scraped_count": len(scraped),
    }


async def scrape_external(query: str, limit: int = 10) -> list[dict]:
    """Try to scrape from available sources."""
    # Try Parfumo first
    results = await try_parfumo(query, limit)
    if results:
        return results

    # Try Fragrantica as fallback
    results = await try_fragrantica(query, limit)
    if results:
        return results

    return []


async def try_parfumo(query: str, limit: int = 10) -> list[dict]:
    """Scrape Parfumo search results."""
    try:
        from bs4 import BeautifulSoup
        url = f"https://www.parfumo.com/s_perfumes.php?search={query}"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200 or len(resp.text) < 500:
                return []
            if "cloudflare" in resp.text.lower()[:2000]:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            links = soup.select("a[href*='/Perfumes/']")
            seen_urls = set()
            
            for link in links:
                href = link.get("href", "")
                if href in seen_urls or not href:
                    continue
                seen_urls.add(href)
                
                name = link.get_text(strip=True)
                if not name or len(name) < 2 or len(name) > 200:
                    continue
                
                full_url = href if href.startswith("http") else f"https://www.parfumo.com{href}"
                
                # Try to find brand from parent elements
                brand = ""
                parent = link.parent
                if parent:
                    brand_el = parent.select_one("small, .brand, [class*='brand']")
                    if brand_el:
                        brand = brand_el.get_text(strip=True)
                
                # Try to find image
                img = ""
                if parent:
                    img_el = parent.select_one("img")
                    if img_el:
                        img = img_el.get("src", "") or img_el.get("data-src", "")

                results.append({
                    "name": name,
                    "brand": brand,
                    "release_year": None,
                    "gender": "Unisex",
                    "notes_top": [],
                    "notes_middle": [],
                    "notes_base": [],
                    "rating": None,
                    "votes": None,
                    "description": "",
                    "longevity": "",
                    "sillage": "",
                    "image_url": img,
                    "perfume_url": full_url,
                })
                
                if len(results) >= limit:
                    break

            return results
    except Exception as e:
        print(f"Parfumo error: {e}")
        return []


async def try_fragrantica(query: str, limit: int = 10) -> list[dict]:
    """Try Fragrantica search."""
    try:
        from bs4 import BeautifulSoup
        from urllib.parse import quote_plus
        url = f"https://www.fragrantica.com/search/?query={quote_plus(query)}"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200 or len(resp.text) < 500:
                return []
            if "cloudflare" in resp.text.lower()[:2000] or "challenge" in resp.text.lower()[:2000]:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            links = soup.select("a[href*='/perfume/']")
            seen_urls = set()
            
            for link in links:
                href = link.get("href", "")
                if href in seen_urls or "/perfume/" not in href:
                    continue
                seen_urls.add(href)
                
                name = link.get_text(strip=True)
                if not name or len(name) < 2:
                    continue

                full_url = href if href.startswith("http") else f"https://www.fragrantica.com{href}"

                results.append({
                    "name": name,
                    "brand": "",
                    "release_year": None,
                    "gender": "Unisex",
                    "notes_top": [],
                    "notes_middle": [],
                    "notes_base": [],
                    "rating": None,
                    "votes": None,
                    "description": "",
                    "longevity": "",
                    "sillage": "",
                    "image_url": "",
                    "perfume_url": full_url,
                })

                if len(results) >= limit:
                    break

            return results
    except Exception as e:
        print(f"Fragrantica error: {e}")
        return []