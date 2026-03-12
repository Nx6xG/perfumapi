import os
import re
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
)

app = FastAPI(title="PerfumAPI", version="3.0.0")

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

            # Parfumo search results contain links to /Perfumes/Brand/Name
            for link in soup.select("a[href]"):
                href = link.get("href", "")
                # Match pattern: /Perfumes/Brand/Something (at least 3 path parts)
                if "/Perfumes/" not in href:
                    continue
                full = href if href.startswith("http") else f"{BASE}{href}"
                # Must have format /Perfumes/Brand/PerfumeName (3+ segments after domain)
                path = full.replace(BASE, "").strip("/")
                parts = path.split("/")
                if len(parts) < 3 or parts[0] != "Perfumes":
                    continue
                # Skip if it's just a brand page like /Perfumes/Dior
                if len(parts) == 2:
                    continue
                # Skip duplicates, prices pages, etc.
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


async def parfumo_detail(url: str) -> dict | None:
    """Scrape a single Parfumo perfume detail page."""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            text = resp.text

            # --- Name & Brand & Year ---
            h1 = soup.select_one("h1")
            name = ""
            brand = ""
            year = None

            if h1:
                # Brand is in a link inside h1
                brand_link = h1.select_one("a[href*='/Perfumes/']")
                if brand_link:
                    brand = brand_link.get_text(strip=True)

                # Year is in a link to /Release_Years/
                year_link = h1.select_one("a[href*='/Release_Years/']")
                if year_link:
                    try:
                        year = int(year_link.get_text(strip=True))
                    except ValueError:
                        pass

                # Name: remove all nested tags, take only direct text nodes
                # Clone h1, remove all child tags, get remaining text
                for tag in h1.find_all(True):
                    tag.decompose()
                name = h1.get_text(strip=True)

            # Fallback: extract from title tag
            if not name:
                title = soup.select_one("title")
                if title:
                    t = title.get_text(strip=True)
                    name = t.split(" by ")[0].split(" » ")[0].strip()

            if not brand:
                # Re-parse since we decomposed h1
                soup2 = BeautifulSoup(resp.text, "html.parser")
                brand_link = soup2.select_one("h1 a[href*='/Perfumes/']")
                if brand_link:
                    brand = brand_link.get_text(strip=True)

            # --- Image ---
            image_url = ""
            img = soup.select_one("img[src*='media.parfumo.com/perfumes']")
            if img:
                image_url = img.get("src", "")
            if not image_url:
                # Try og:image
                og = soup.select_one("meta[property='og:image']")
                if og:
                    image_url = og.get("content", "")

            # --- Rating ---
            rating = None
            # Look for rating pattern like "7.8 / 10"
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

            # --- Notes (Top / Heart / Base) ---
            notes_top = []
            notes_mid = []
            notes_base = []

            # Notes are in the pyramid section with img alt texts
            # Top Notes section
            pyramid_sections = str(soup)

            # Split by pyramid markers
            top_match = re.search(r'Top Notes?(.*?)Heart Notes?', pyramid_sections, re.DOTALL | re.IGNORECASE)
            mid_match = re.search(r'Heart Notes?(.*?)Base Notes?', pyramid_sections, re.DOTALL | re.IGNORECASE)
            base_match = re.search(r'Base Notes?(.*?)(?:Perfumer|Videos|Ratings|Reviews|$)', pyramid_sections, re.DOTALL | re.IGNORECASE)

            def extract_notes(html_chunk: str) -> list[str]:
                chunk_soup = BeautifulSoup(html_chunk, "html.parser")
                notes = []
                for img in chunk_soup.select("img[alt]"):
                    alt = img.get("alt", "").strip()
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
            if "for men" in text.lower():
                gender = "Men"
            elif "for women" in text.lower():
                gender = "Women"

            # --- Description ---
            description = ""
            desc_match = re.search(r'A (?:popular )?perfume by .+?(?:released in \d{4}\.?|\.)', text)
            if desc_match:
                description = desc_match.group(0)[:300]

            if not name:
                return None

            return {
                "name": name.strip(),
                "brand": brand.strip(),
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
    """
    Fast scrape: extract all data directly from the Parfumo search results page.
    Single HTTP request — no detail page visits needed.
    """
    url = f"{BASE}/s_perfumes.php?search={quote_plus(query)}"
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            seen = set()

            # Each perfume result on Parfumo is wrapped in an element with itemprop="itemListElement"
            # or we can find them by looking for perfume image + info blocks
            # The search page contains structured data with schema.org markup

            # Strategy: find all perfume links that point to detail pages
            # and extract surrounding context (image, brand, notes)
            for item in soup.select("[itemtype='https://schema.org/Product'], [itemtype='http://schema.org/Product']"):
                try:
                    perfume = parse_search_item(item)
                    if perfume and perfume["perfume_url"] not in seen:
                        seen.add(perfume["perfume_url"])
                        results.append(perfume)
                        if len(results) >= limit:
                            break
                except Exception as e:
                    print(f"Parse error: {e}")
                    continue

            # Fallback: if schema.org parsing didn't work, try generic approach
            if not results:
                # Find all perfume detail links
                for link in soup.select("a[href*='/Perfumes/']"):
                    href = link.get("href", "")
                    full = href if href.startswith("http") else f"{BASE}{href}"
                    path_parts = full.replace(BASE, "").strip("/").split("/")

                    if len(path_parts) < 3 or path_parts[0] != "Perfumes":
                        continue
                    if "/prices" in full or "/reviews" in full or full in seen:
                        continue
                    if len(path_parts) == 2:
                        continue

                    seen.add(full)

                    # Get the parent container for context
                    parent = link
                    for _ in range(5):
                        if parent.parent:
                            parent = parent.parent
                        else:
                            break

                    # Extract name (clean text only)
                    raw_name = link.get_text(strip=True)
                    # Clean HTML entities from name
                    name = BeautifulSoup(raw_name, "html.parser").get_text(strip=True)

                    # Brand
                    brand = ""
                    brand_el = parent.select_one("a[href*='/Perfumes/'] span[itemprop='name']")
                    if brand_el:
                        brand = brand_el.get_text(strip=True)
                    if not brand:
                        brand_el = parent.select_one(".p_brand_name a span")
                        if brand_el:
                            brand = brand_el.get_text(strip=True)

                    # Image
                    image_url = ""
                    img = parent.select_one("img[src*='media.parfumo.com/perfumes']")
                    if img:
                        image_url = img.get("src", "") or img.get("data-src", "")

                    # Notes from the search page
                    notes_top = []
                    notes_mid = []
                    notes_base = []
                    note_imgs = parent.select("img[alt][src*='media.parfumo.com/notes']")
                    all_notes = [ni.get("alt", "").strip() for ni in note_imgs if ni.get("alt", "").strip()]
                    if all_notes:
                        third = max(len(all_notes) // 3, 1)
                        notes_top = all_notes[:third]
                        notes_mid = all_notes[third:third*2]
                        notes_base = all_notes[third*2:]

                    # Year
                    year = None
                    year_el = parent.select_one("a[href*='/Release_Years/']")
                    if year_el:
                        try:
                            year = int(year_el.get_text(strip=True))
                        except ValueError:
                            pass

                    # Gender
                    gender = "Unisex"
                    text_content = parent.get_text(strip=True).lower()
                    if "for men" in text_content and "for women" not in text_content:
                        gender = "Men"
                    elif "for women" in text_content and "for men" not in text_content:
                        gender = "Women"

                    # Description
                    description = ""
                    desc_match = re.search(r'(A (?:popular )?perfume by .+?(?:released in \d{4}\.?|\.))', parent.get_text())
                    if desc_match:
                        description = desc_match.group(1)[:300]

                    # Votes/Ratings
                    votes = None
                    rating_el = parent.select_one("[itemprop='ratingCount']")
                    if rating_el:
                        try:
                            votes = int(rating_el.get_text(strip=True).replace(",", ""))
                        except ValueError:
                            pass

                    if name and len(name) > 1:
                        results.append({
                            "name": name,
                            "brand": brand,
                            "release_year": year,
                            "gender": gender,
                            "notes_top": notes_top,
                            "notes_middle": notes_mid,
                            "notes_base": notes_base,
                            "rating": None,
                            "votes": votes,
                            "description": description,
                            "longevity": "",
                            "sillage": "",
                            "image_url": image_url,
                            "perfume_url": full,
                        })

                    if len(results) >= limit:
                        break

            return results

    except Exception as e:
        print(f"Parfumo scrape error: {e}")
        return []


def parse_search_item(item) -> dict | None:
    """Parse a single schema.org Product item from search results."""
    name = ""
    brand = ""
    year = None
    image_url = ""
    perfume_url = ""

    # Name
    name_el = item.select_one("[itemprop='name']")
    if name_el:
        name = name_el.get_text(strip=True)

    # Brand
    brand_scope = item.select_one("[itemprop='brand'] [itemprop='name']")
    if brand_scope:
        brand = brand_scope.get_text(strip=True)
    # Remove brand from name if it's the same
    if brand and name == brand:
        name_el2 = item.select_one("h2, h3, .perfume_name")
        if name_el2:
            name = name_el2.get_text(strip=True)

    # URL
    url_el = item.select_one("[itemprop='url']")
    if url_el:
        perfume_url = url_el.get("href", "")
    if not perfume_url:
        link = item.select_one("a[href*='/Perfumes/']")
        if link:
            perfume_url = link.get("href", "")
    if perfume_url and not perfume_url.startswith("http"):
        perfume_url = f"{BASE}{perfume_url}"

    # Image
    img = item.select_one("img[src*='media.parfumo.com/perfumes']")
    if img:
        image_url = img.get("src", "")

    # Year
    year_el = item.select_one("a[href*='/Release_Years/']")
    if year_el:
        try:
            year = int(year_el.get_text(strip=True))
        except ValueError:
            pass

    # Notes
    note_imgs = item.select("img[alt][src*='media.parfumo.com/notes']")
    all_notes = [ni.get("alt", "").strip() for ni in note_imgs if ni.get("alt", "").strip()]
    third = max(len(all_notes) // 3, 1) if all_notes else 0
    notes_top = all_notes[:third] if all_notes else []
    notes_mid = all_notes[third:third*2] if all_notes else []
    notes_base = all_notes[third*2:] if all_notes else []

    # Gender
    gender = "Unisex"
    text = item.get_text(strip=True).lower()
    if "for men" in text and "for women" not in text:
        gender = "Men"
    elif "for women" in text and "for men" not in text:
        gender = "Women"

    # Description
    description = ""
    desc_match = re.search(r'(A (?:popular )?perfume by .+?(?:released in \d{4}\.?|\.))', item.get_text())
    if desc_match:
        description = desc_match.group(1)[:300]

    # Votes
    votes = None
    rating_el = item.select_one("[itemprop='ratingCount']")
    if rating_el:
        try:
            votes = int(rating_el.get_text(strip=True).replace(",", ""))
        except ValueError:
            pass

    if not name or not perfume_url:
        return None

    return {
        "name": name,
        "brand": brand,
        "release_year": year,
        "gender": gender,
        "notes_top": notes_top,
        "notes_middle": notes_mid,
        "notes_base": notes_base,
        "rating": None,
        "votes": votes,
        "description": description,
        "longevity": "",
        "sillage": "",
        "image_url": image_url,
        "perfume_url": perfume_url,
    }


# ──────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "PerfumAPI v3.0 — Parfumo Scraper", "docs": "/docs"}


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
    2. If <3 results → scrape Parfumo
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


@app.get("/test-sites")
async def test_sites():
    """Test which sites are reachable."""
    results = {}
    urls = {"parfumo": f"{BASE}/", "parfumo_search": f"{BASE}/s_perfumes.php?search=sauvage"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for name, url in urls.items():
            try:
                resp = await client.get(url, headers=HEADERS)
                results[name] = {"status": resp.status_code, "length": len(resp.text), "ok": resp.status_code == 200}
            except Exception as e:
                results[name] = {"ok": False, "error": str(e)}
    return results