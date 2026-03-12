"""
Wikiparfum Image Scraper
========================
Searches wikiparfum.com for perfume bottle images.
No Cloudflare — images served via api-assets.wikiparfum.com CDN.

Strategy:
1. Search Google for 'site:wikiparfum.com {name} {brand}'
   (Wikiparfum has no public search API, but their HTML is SSR-friendly)
2. Alternatively: construct slug and try direct URL
3. Scrape the detail page for the bottle image
4. Return the CDN image URL
"""

import re
import httpx
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
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


def _slugify(text: str) -> str:
    """Convert perfume name to Wikiparfum-style slug."""
    s = text.lower().strip()
    # Replace special chars
    s = s.replace("'", "").replace("'", "").replace("'", "")
    s = s.replace("&", "and")
    s = s.replace("é", "e").replace("è", "e").replace("ê", "e").replace("ë", "e")
    s = s.replace("à", "a").replace("â", "a").replace("ä", "a")
    s = s.replace("ù", "u").replace("û", "u").replace("ü", "u")
    s = s.replace("ô", "o").replace("ö", "o")
    s = s.replace("î", "i").replace("ï", "i")
    s = s.replace("ì", "i")
    s = s.replace("ñ", "n")
    s = s.replace("ç", "c")
    # Replace non-alphanum with hyphens
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = s.strip('-')
    # Collapse multiple hyphens
    s = re.sub(r'-+', '-', s)
    return s


def _similarity(a: str, b: str) -> float:
    """Simple string similarity ratio."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _extract_bottle_image(soup: BeautifulSoup) -> str | None:
    """
    Extract the perfume bottle image URL from a Wikiparfum detail page.
    The bottle image is the one with the CDN prefix and ~w250 size,
    NOT the ingredient images (which are ~w1750).
    """
    # Strategy 1: Look for img tags with the CDN prefix and w250 (bottle thumbnails)
    for img in soup.select("img"):
        src = img.get("src", "")
        if CDN_PREFIX in src and "-w250-" in src:
            # This is a bottle/product image (250px wide)
            return src

    # Strategy 2: og:image meta tag (often the bottle)
    og = soup.select_one("meta[property='og:image']")
    if og:
        content = og.get("content", "")
        if CDN_PREFIX in content:
            return content

    # Strategy 3: Any CDN image that's NOT an ingredient (not w1750)
    for img in soup.select("img"):
        src = img.get("src", "")
        if CDN_PREFIX in src and "-w1750-" not in src and src.endswith(".jpg"):
            return src

    return None


async def fetch_wikiparfum_image(
    name: str,
    brand: str = "",
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """
    Try to find a bottle image for a perfume on Wikiparfum.

    Approach:
    1. Try direct slug URL (fast, no search needed)
    2. If that fails, scrape the fragrances listing page with search
    3. Find best match and extract bottle image

    Returns the CDN image URL or None.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=20, follow_redirects=True)

    try:
        # --- Approach 1: Direct slug guess ---
        slug = _slugify(name)
        image_url = await _try_direct_slug(client, slug)
        if image_url:
            return image_url

        # Try with brand prefix removed from name if it starts with brand
        if brand:
            name_lower = name.lower()
            brand_lower = brand.lower()
            if name_lower.startswith(brand_lower):
                clean_name = name[len(brand):].strip()
                if clean_name:
                    slug2 = _slugify(clean_name)
                    image_url = await _try_direct_slug(client, slug2)
                    if image_url:
                        return image_url

        # --- Approach 2: Search the fragrances page ---
        image_url = await _search_and_scrape(client, name, brand)
        if image_url:
            return image_url

        return None

    finally:
        if own_client:
            await client.aclose()


async def _try_direct_slug(client: httpx.AsyncClient, slug: str) -> str | None:
    """Try fetching a direct Wikiparfum URL by slug."""
    url = f"{BASE}/en/fragrances/{slug}"
    try:
        resp = await client.get(url, headers=HEADERS)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Verify it's actually a perfume page (not a 404 or redirect)
        title = soup.select_one("title")
        if title and "perfume" in title.get_text(strip=True).lower():
            return _extract_bottle_image(soup)

        # Also accept pages with the fragrance structure
        h1 = soup.select_one("h1")
        if h1 and h1.get_text(strip=True):
            return _extract_bottle_image(soup)

        return None
    except Exception as e:
        print(f"Wikiparfum direct slug error for {slug}: {e}")
        return None


async def _search_and_scrape(
    client: httpx.AsyncClient,
    name: str,
    brand: str,
) -> str | None:
    """
    Search Wikiparfum fragrances listing and find a matching perfume.
    Wikiparfum doesn't have a search API, so we use Google to find the page.
    """
    # Build search query
    search_query = f"{name} {brand}".strip()

    # Try the fragrances listing page (it may have the perfume in its rendered HTML)
    # This won't work for most since it's paginated/JS-loaded
    # So we use a Google search approach instead
    try:
        google_url = (
            f"https://www.google.com/search?"
            f"q=site:wikiparfum.com+fragrances+{quote_plus(search_query)}"
            f"&num=5"
        )
        resp = await client.get(google_url, headers={
            **HEADERS,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/122.0.0.0 Safari/537.36",
        })

        if resp.status_code != 200:
            return None

        # Extract wikiparfum URLs from Google results
        soup = BeautifulSoup(resp.text, "html.parser")
        wp_urls = []

        for a in soup.select("a[href]"):
            href = a.get("href", "")
            # Google wraps URLs in redirects
            if "wikiparfum.com/en/fragrances/" in href:
                # Extract actual URL
                match = re.search(
                    r'(https?://www\.wikiparfum\.com/en/fragrances/[a-z0-9-]+)',
                    href,
                )
                if match:
                    wp_urls.append(match.group(1))

        # Deduplicate
        wp_urls = list(dict.fromkeys(wp_urls))

        # Try each URL
        for wp_url in wp_urls[:3]:
            try:
                resp2 = await client.get(wp_url, headers=HEADERS)
                if resp2.status_code != 200:
                    continue

                soup2 = BeautifulSoup(resp2.text, "html.parser")

                # Verify name match
                title = soup2.select_one("title")
                if title:
                    title_text = title.get_text(strip=True).lower()
                    if _similarity(name.lower(), title_text) > 0.3:
                        img = _extract_bottle_image(soup2)
                        if img:
                            return img

                # Fallback: just check if it has a bottle image
                img = _extract_bottle_image(soup2)
                if img:
                    return img

            except Exception:
                continue

        return None

    except Exception as e:
        print(f"Wikiparfum Google search error: {e}")
        return None


async def fetch_images_batch(
    perfumes: list[dict],
    max_concurrent: int = 3,
) -> dict[str, str]:
    """
    Fetch images for multiple perfumes.
    Returns dict of {perfume_id: image_url}.
    Respects rate limits with semaphore.
    """
    import asyncio

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
                url = await fetch_wikiparfum_image(name, brand)
                if url:
                    results[pid] = url
            except Exception as e:
                print(f"Batch image fetch error for {name}: {e}")

            # Small delay between requests
            await asyncio.sleep(0.5)

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        tasks = [_fetch_one(p) for p in perfumes]
        await asyncio.gather(*tasks, return_exceptions=True)

    return results
