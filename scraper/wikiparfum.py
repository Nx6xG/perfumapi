"""
Wikiparfum Full Scraper v3
===========================
Scrapes perfume data + bottle images from wikiparfum.com.
No Cloudflare — images served via api-assets.wikiparfum.com CDN.

Used for:
1. Search: find perfumes by name, return full data
2. Image: get bottle image for a known perfume

Strategy for search:
- Build slug variations from query → try direct URLs in parallel
- Scrape brand page as fallback
- Extract full perfume data from detail pages
"""

import re
import asyncio
import httpx
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE = "https://www.wikiparfum.com"
CDN_PREFIX = "https://api-assets.wikiparfum.com/"

SLUG_SUFFIXES = [
    "",
    "-eau-de-parfum",
    "-eau-de-toilette",
    "-parfum",
    "-eau-de-parfum-1",
    "-eau-de-toilette-1",
    "-parfum-1",
    "-edp",
    "-edt",
    "-1",
    "-2",
    "-elixir",
    "-intense",
]


def _slugify(text: str) -> str:
    s = text.lower().strip()
    for suffix in [
        " eau de parfum", " eau de toilette", " parfum",
        " edp", " edt", " cologne",
    ]:
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    s = s.replace("'", "").replace("\u2018", "").replace("\u2019", "")
    s = s.replace("&", "and")
    for src, dst in [
        ("\u00e9", "e"), ("\u00e8", "e"), ("\u00ea", "e"), ("\u00eb", "e"),
        ("\u00e0", "a"), ("\u00e2", "a"), ("\u00e4", "a"),
        ("\u00f9", "u"), ("\u00fb", "u"), ("\u00fc", "u"),
        ("\u00f4", "o"), ("\u00f6", "o"), ("\u00f2", "o"),
        ("\u00ee", "i"), ("\u00ef", "i"), ("\u00ec", "i"),
        ("\u00f1", "n"), ("\u00e7", "c"),
    ]:
        s = s.replace(src, dst)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-+", "-", s)
    return s


def _brand_slug(brand: str) -> str:
    s = brand.lower().strip()
    s = s.replace("&", "")
    for src, dst in [
        ("\u00e9", "e"), ("\u00e8", "e"), ("\u00ea", "e"), ("\u00eb", "e"),
        ("\u00e0", "a"), ("\u00e2", "a"), ("\u00e4", "a"),
        ("\u00f9", "u"), ("\u00fb", "u"), ("\u00fc", "u"),
        ("\u00f4", "o"), ("\u00f6", "o"),
        ("\u00ee", "i"), ("\u00ef", "i"),
        ("\u00f1", "n"), ("\u00e7", "c"),
    ]:
        s = s.replace(src, dst)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-+", "-", s)
    return s


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ──────────────────────────────────────────────
# Extraction helpers
# ──────────────────────────────────────────────

def _extract_bottle_image(soup: BeautifulSoup) -> str:
    for img in soup.select("img"):
        src = img.get("src", "")
        if CDN_PREFIX in src and "-w250-" in src:
            return src
    og = soup.select_one("meta[property='og:image']")
    if og:
        content = og.get("content", "")
        if CDN_PREFIX in content:
            return content
    for img in soup.select("img"):
        src = img.get("src", "")
        if CDN_PREFIX in src and "-w1750-" not in src and src.endswith(".jpg"):
            return src
    return ""


def _extract_full_data(soup: BeautifulSoup, url: str) -> dict | None:
    """Extract full perfume data from a Wikiparfum detail page."""
    text = soup.get_text(" ", strip=True)

    # --- Name ---
    name = ""
    h1 = soup.select_one("h1")
    if h1:
        name = h1.get_text(strip=True)

    # --- Brand ---
    brand = ""
    brand_link = soup.select_one("a[href*='/brands/']")
    if brand_link:
        brand = brand_link.get_text(strip=True)

    # --- Concentration (from subtitle like "Eau de Toilette") ---
    concentration = ""
    h5 = soup.select_one("h1 + h5") or soup.select_one("h5")
    if h5:
        h5_text = h5.get_text(strip=True)
        if any(kw in h5_text.lower() for kw in ["eau de", "parfum", "cologne", "toilette", "extrait"]):
            concentration = h5_text

    # --- Image ---
    image_url = _extract_bottle_image(soup)

    # --- Notes / Ingredients ---
    notes = []
    for link in soup.select("a[href*='/ingredients/']"):
        note_name = link.get_text(strip=True)
        if note_name and len(note_name) > 1 and note_name not in notes:
            notes.append(note_name)
    # Deduplicate (Wikiparfum repeats notes multiple times on page)
    seen = set()
    unique_notes = []
    for n in notes:
        if n.lower() not in seen:
            seen.add(n.lower())
            unique_notes.append(n)
    notes = unique_notes

    # --- Family / Subfamily ---
    family = ""
    subfamily = ""
    # Look for "Family" and "Subfamily" labels
    family_match = re.search(r'Family\s+([A-Z][A-Z\s]+?)(?:\s+Subfamily|\s+Clasif)', text)
    if family_match:
        family = family_match.group(1).strip().title()

    subfamily_match = re.search(r'Subfamily\s+([A-Z][A-Z\s()]+?)(?:\s+Clasif|\s+perfumer|\s+price)', text)
    if subfamily_match:
        subfamily = subfamily_match.group(1).strip().title()

    # Fallback: look for patterns like "AROMATIC FOUGERE / CITRUS"
    if not family:
        fam_pattern = re.search(r'(AROMATIC|FLORAL|WOODY|CITRUS|AMBERY|CHYPRE|GOURMAND|MUSK|GREEN)\s*(?:FOUGERE|ORIENTAL)?(?:\s*/\s*(\w+))?', text, re.IGNORECASE)
        if fam_pattern:
            family = fam_pattern.group(0).strip().title()

    # --- Description ---
    description = ""
    desc_section = soup.select_one("h6:-soup-contains('Description')")
    if desc_section:
        # Get next text sibling/element
        next_el = desc_section.find_next_sibling()
        if next_el:
            description = next_el.get_text(strip=True)[:500]

    if not description:
        # Fallback: find description-like paragraph
        for p in soup.select("p"):
            p_text = p.get_text(strip=True)
            if len(p_text) > 50 and any(kw in p_text.lower() for kw in ["fragrance", "scent", "perfume", "composition", "notes"]):
                description = p_text[:500]
                break

    # --- Perfumer ---
    perfumer = ""
    perfumer_link = soup.select_one("a[href*='/perfumers/']")
    if perfumer_link:
        perfumer = perfumer_link.get_text(strip=True)

    if not name:
        return None

    return {
        "name": name,
        "brand": brand,
        "concentration": concentration,
        "family": family,
        "subfamily": subfamily,
        "notes_top": notes[:3] if len(notes) > 3 else notes,
        "notes_middle": notes[3:6] if len(notes) > 3 else [],
        "notes_base": notes[6:] if len(notes) > 6 else [],
        "description": description,
        "perfumer": perfumer,
        "image_url": image_url,
        "perfume_url": url,
        "rating": None,
        "votes": None,
        "release_year": None,
        "gender": "Unisex",
        "longevity": "",
        "sillage": "",
    }


def _is_perfume_page(soup: BeautifulSoup) -> bool:
    title = soup.select_one("title")
    if title:
        t = title.get_text(strip=True).lower()
        if "perfume" in t or "fragrance" in t:
            return True
    text = soup.get_text(strip=True).lower()
    if "olfactive classification" in text:
        return True
    h1 = soup.select_one("h1")
    if h1 and soup.select_one("a[href*='/brands/']"):
        return True
    return False


# ──────────────────────────────────────────────
# Search functions
# ──────────────────────────────────────────────

async def _try_url_full(client: httpx.AsyncClient, url: str) -> dict | None:
    """Fetch a Wikiparfum URL and extract full perfume data."""
    try:
        resp = await client.get(url, headers=HEADERS)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        if not _is_perfume_page(soup):
            return None
        return _extract_full_data(soup, url)
    except Exception as e:
        print(f"Wikiparfum fetch error for {url}: {e}")
        return None


async def _try_url_image(client: httpx.AsyncClient, url: str) -> str | None:
    """Fetch a Wikiparfum URL and extract only the bottle image."""
    try:
        resp = await client.get(url, headers=HEADERS)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        if not _is_perfume_page(soup):
            return None
        return _extract_bottle_image(soup) or None
    except Exception as e:
        print(f"Wikiparfum fetch error for {url}: {e}")
        return None


async def search_wikiparfum(
    query: str,
    limit: int = 10,
) -> list[dict]:
    """
    Search Wikiparfum for perfumes matching the query.
    Returns list of full perfume data dicts.

    Strategy:
    1. Build slug variations from query
    2. Try all as direct URLs in parallel
    3. Collect all that are valid perfume pages
    """
    base_slug = _slugify(query)
    slugs = []
    for suffix in SLUG_SUFFIXES:
        slug = f"{base_slug}{suffix}"
        if slug not in slugs:
            slugs.append(slug)

    urls = [f"{BASE}/en/fragrances/{s}" for s in slugs]

    results = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        tasks = [_try_url_full(client, url) for url in urls]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        seen_names = set()
        for resp in responses:
            if isinstance(resp, dict) and resp.get("name"):
                key = f"{resp['name'].lower()}_{resp.get('brand', '').lower()}"
                if key not in seen_names:
                    seen_names.add(key)
                    results.append(resp)

        # If we got results, return them
        if results:
            return results[:limit]

        # --- Fallback: Try brand page if query looks like "Brand Name" ---
        parts = query.strip().split()
        if len(parts) >= 2:
            # Try treating first word as brand
            possible_brand = parts[0]
            brand_results = await _search_brand_page_full(
                client, query, possible_brand, limit
            )
            if brand_results:
                return brand_results[:limit]

    return results[:limit]


async def _search_brand_page_full(
    client: httpx.AsyncClient,
    query: str,
    brand: str,
    limit: int,
) -> list[dict]:
    """Search a brand page for matching perfumes, return full data."""
    slug = _brand_slug(brand)
    brand_url = f"{BASE}/en/brands/{slug}"

    try:
        resp = await client.get(brand_url, headers=HEADERS)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        query_lower = query.lower()

        # Collect matching links
        matches = []
        for link in soup.select("a[href*='/fragrances/']"):
            href = link.get("href", "")
            link_text = link.get_text(strip=True).lower()

            score = _similarity(query_lower, link_text)
            partial = query_lower in link_text or link_text in query_lower

            if score > 0.3 or partial:
                full_url = href if href.startswith("http") else f"{BASE}{href}"
                matches.append((full_url, score + (0.3 if partial else 0)))

        # Sort by score, take top candidates
        matches.sort(key=lambda x: x[1], reverse=True)
        matches = matches[:limit]

        # Fetch detail pages in parallel
        if not matches:
            return []

        tasks = [_try_url_full(client, url) for url, _ in matches]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        seen = set()
        for resp_item in responses:
            if isinstance(resp_item, dict) and resp_item.get("name"):
                key = f"{resp_item['name'].lower()}_{resp_item.get('brand', '').lower()}"
                if key not in seen:
                    seen.add(key)
                    results.append(resp_item)

        return results

    except Exception as e:
        print(f"Wikiparfum brand page error for {brand}: {e}")
        return []


# ──────────────────────────────────────────────
# Image-only functions (kept for /image endpoint)
# ──────────────────────────────────────────────

async def fetch_wikiparfum_image(
    name: str,
    brand: str = "",
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Find a bottle image for a perfume on Wikiparfum."""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15, follow_redirects=True)

    try:
        base_slug = _slugify(name)
        slugs = []
        for suffix in SLUG_SUFFIXES:
            slug = f"{base_slug}{suffix}"
            if slug not in slugs:
                slugs.append(slug)

        urls = [f"{BASE}/en/fragrances/{s}" for s in slugs]
        tasks = [_try_url_image(client, url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, str):
                return result

        if brand:
            slug = _brand_slug(brand)
            brand_url = f"{BASE}/en/brands/{slug}"
            try:
                resp = await client.get(brand_url, headers=HEADERS)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    name_lower = name.lower()
                    best_match = None
                    best_score = 0.0

                    for link in soup.select("a[href*='/fragrances/']"):
                        link_text = link.get_text(strip=True).lower()
                        score = _similarity(name_lower, link_text)
                        if score > best_score and score > 0.4:
                            best_score = score
                            href = link.get("href", "")
                            best_match = href if href.startswith("http") else f"{BASE}{href}"

                    if best_match:
                        return await _try_url_image(client, best_match)
            except Exception:
                pass

        return None
    finally:
        if own_client:
            await client.aclose()


async def fetch_images_batch(
    perfumes: list[dict],
    max_concurrent: int = 3,
) -> dict[str, str]:
    """Fetch images for multiple perfumes. Returns {perfume_id: image_url}."""
    semaphore = asyncio.Semaphore(max_concurrent)
    results = {}

    async def _fetch_one(perfume: dict):
        async with semaphore:
            pid = perfume.get("id", "")
            name = perfume.get("name", "")
            brand = perfume.get("brand", "")
            if not name:
                return
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                    url = await fetch_wikiparfum_image(name, brand, c)
                    if url:
                        results[pid] = url
            except Exception as e:
                print(f"Batch fetch error for {name}: {e}")
            await asyncio.sleep(1)

    tasks = [_fetch_one(p) for p in perfumes]
    await asyncio.gather(*tasks, return_exceptions=True)
    return results