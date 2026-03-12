"""
Wikiparfum Image Scraper v2
============================
Fetches perfume bottle images from wikiparfum.com.
No Cloudflare — images served via api-assets.wikiparfum.com CDN.

Strategy (ordered by speed):
1. Try multiple slug variations in parallel (name, name-eau-de-toilette, etc.)
2. Scrape the brand page on Wikiparfum and find the perfume there
3. Return the CDN bottle image URL
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

# Common suffixes Wikiparfum appends to perfume slugs
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
]


def _slugify(text: str) -> str:
    """Convert perfume name to Wikiparfum-style slug."""
    s = text.lower().strip()
    # Remove common type suffixes (we add them back systematically)
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
    """Convert brand name to Wikiparfum brand page slug."""
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


def _extract_bottle_image(soup: BeautifulSoup) -> str | None:
    """
    Extract the perfume bottle image from a Wikiparfum page.
    Bottle images use w250 sizing. Ingredient images use w1750.
    """
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

    return None


def _is_perfume_page(soup: BeautifulSoup) -> bool:
    """Check if the page is actually a perfume detail page."""
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


async def fetch_wikiparfum_image(
    name: str,
    brand: str = "",
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """
    Find a bottle image for a perfume on Wikiparfum.

    Approaches (in order):
    1. Try multiple slug variations in parallel
    2. Scrape brand page and find the perfume link

    Returns the CDN image URL or None.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15, follow_redirects=True)

    try:
        # --- Approach 1: Try slug variations in parallel ---
        base_slug = _slugify(name)
        slugs = []
        for suffix in SLUG_SUFFIXES:
            slug = f"{base_slug}{suffix}"
            if slug not in slugs:
                slugs.append(slug)

        urls = [f"{BASE}/en/fragrances/{s}" for s in slugs]
        tasks = [_try_url(client, url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, str):
                return result

        # --- Approach 2: Brand page scrape ---
        if brand:
            image_url = await _search_brand_page(client, name, brand)
            if image_url:
                return image_url

        return None

    finally:
        if own_client:
            await client.aclose()


async def _try_url(client: httpx.AsyncClient, url: str) -> str | None:
    """Try fetching a Wikiparfum URL and extract the bottle image."""
    try:
        resp = await client.get(url, headers=HEADERS)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        if not _is_perfume_page(soup):
            return None

        return _extract_bottle_image(soup)
    except Exception as e:
        print(f"Wikiparfum fetch error for {url}: {e}")
        return None


async def _search_brand_page(
    client: httpx.AsyncClient,
    name: str,
    brand: str,
) -> str | None:
    """
    Fallback: Load the brand's page on Wikiparfum and find the perfume.
    Brand pages list fragrances with links + thumbnail images.
    e.g. https://www.wikiparfum.com/en/brands/dior
    """
    slug = _brand_slug(brand)
    brand_url = f"{BASE}/en/brands/{slug}"

    try:
        resp = await client.get(brand_url, headers=HEADERS)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        name_lower = name.lower()

        best_match = None
        best_score = 0.0

        for link in soup.select("a[href*='/fragrances/']"):
            href = link.get("href", "")
            link_text = link.get_text(strip=True).lower()

            score = _similarity(name_lower, link_text)
            if score > best_score and score > 0.4:
                best_score = score
                full_url = href if href.startswith("http") else f"{BASE}{href}"
                best_match = full_url

            if name_lower in link_text or link_text in name_lower:
                if score > best_score * 0.8:
                    best_match = href if href.startswith("http") else f"{BASE}{href}"
                    best_score = max(score, 0.6)

        if best_match:
            return await _try_url(client, best_match)

        return None

    except Exception as e:
        print(f"Wikiparfum brand page error for {brand}: {e}")
        return None


async def fetch_images_batch(
    perfumes: list[dict],
    max_concurrent: int = 3,
) -> dict[str, str]:
    """
    Fetch images for multiple perfumes.
    Returns dict of {perfume_id: image_url}.
    """
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
                async with httpx.AsyncClient(
                    timeout=15, follow_redirects=True
                ) as client:
                    url = await fetch_wikiparfum_image(name, brand, client)
                    if url:
                        results[pid] = url
            except Exception as e:
                print(f"Batch image fetch error for {name}: {e}")

            await asyncio.sleep(1)

    tasks = [_fetch_one(p) for p in perfumes]
    await asyncio.gather(*tasks, return_exceptions=True)

    return results