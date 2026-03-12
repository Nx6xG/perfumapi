import requests
from bs4 import BeautifulSoup
import time
import re
import json
from urllib.parse import quote_plus

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

BASE_URL = "https://www.fragrantica.com"


def search_fragrantica(query: str, limit: int = 10) -> list[dict]:
    """
    Search Fragrantica for perfumes matching the query.
    Returns a list of basic perfume info with URLs for detailed scraping.
    """
    search_url = f"{BASE_URL}/search/?query={quote_plus(query)}"

    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"Search request failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    # Fragrantica search results are in divs with perfume links
    # Try to find search result items
    perfume_links = soup.select("a[href*='/perfume/']")

    seen_urls = set()
    for link in perfume_links:
        href = link.get("href", "")
        if "/perfume/" not in href or href in seen_urls:
            continue

        full_url = href if href.startswith("http") else f"{BASE_URL}{href}"

        # Skip non-perfume pages
        if "/perfume/" not in full_url or full_url.count("/") < 5:
            continue

        seen_urls.add(full_url)

        # Try to extract basic info from the search result
        name_text = link.get_text(strip=True)
        if not name_text or len(name_text) < 2:
            continue

        results.append({
            "name": name_text,
            "perfume_url": full_url,
        })

        if len(results) >= limit:
            break

    return results


def scrape_perfume_detail(url: str) -> dict | None:
    """
    Scrape detailed perfume data from a Fragrantica perfume page.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    try:
        # Name
        name_el = soup.select_one("h1[itemprop='name']") or soup.select_one("h1")
        name = name_el.get_text(strip=True) if name_el else ""

        if not name:
            # Try alternate selectors
            title = soup.find("title")
            if title:
                name = title.get_text(strip=True).split(" - ")[0].strip()

        # Brand
        brand = ""
        brand_el = soup.select_one("span[itemprop='name']")
        if brand_el:
            brand = brand_el.get_text(strip=True)
        if not brand:
            # Try from breadcrumb or other elements
            designer_link = soup.select_one("a[href*='/designers/']")
            if designer_link:
                brand = designer_link.get_text(strip=True)

        # Image
        image_url = ""
        img_el = soup.select_one("img[itemprop='image']") or soup.select_one(".perfume-big img")
        if img_el:
            image_url = img_el.get("src", "")

        # Rating
        rating = None
        rating_el = soup.select_one("span[itemprop='ratingValue']")
        if rating_el:
            try:
                rating = float(rating_el.get_text(strip=True))
            except ValueError:
                pass

        # Votes
        votes = None
        votes_el = soup.select_one("span[itemprop='ratingCount']")
        if votes_el:
            try:
                votes = int(votes_el.get_text(strip=True).replace(",", "").replace(".", ""))
            except ValueError:
                pass

        # Gender
        gender = "Unisex"
        gender_el = soup.select_one("small")
        if gender_el:
            gt = gender_el.get_text(strip=True).lower()
            if "women" in gt and "men" not in gt:
                gender = "Women"
            elif "men" in gt and "women" not in gt:
                gender = "Men"

        # Year
        release_year = None
        year_match = re.search(r'was launched in (\d{4})', resp.text)
        if year_match:
            release_year = int(year_match.group(1))

        # Notes
        notes_top = []
        notes_middle = []
        notes_base = []

        # Try to find note pyramids
        note_sections = soup.select("div[style*='margin']")
        for section in note_sections:
            text = section.get_text(strip=True).lower()
            note_links = section.select("a[href*='/notes/']")
            note_names = [n.get_text(strip=True) for n in note_links if n.get_text(strip=True)]

            if "top note" in text or "head note" in text:
                notes_top = note_names
            elif "middle note" in text or "heart note" in text:
                notes_middle = note_names
            elif "base note" in text:
                notes_base = note_names

        # Fallback: get all notes if pyramid extraction failed
        if not notes_top and not notes_middle and not notes_base:
            all_note_links = soup.select("a[href*='/notes/']")
            all_notes = list(set(n.get_text(strip=True) for n in all_note_links if n.get_text(strip=True)))
            notes_top = all_notes[:3]
            notes_middle = all_notes[3:6]
            notes_base = all_notes[6:]

        # Description
        description = ""
        desc_el = soup.select_one("div[itemprop='description']")
        if desc_el:
            description = desc_el.get_text(strip=True)[:500]

        # Longevity & Sillage (from vote bars if available)
        longevity = ""
        sillage = ""

        perfume_data = {
            "name": name,
            "brand": brand,
            "release_year": release_year,
            "gender": gender,
            "notes_top": notes_top,
            "notes_middle": notes_middle,
            "notes_base": notes_base,
            "rating": rating,
            "votes": votes,
            "description": description,
            "longevity": longevity,
            "sillage": sillage,
            "image_url": image_url,
            "perfume_url": url,
        }

        return perfume_data

    except Exception as e:
        print(f"Error parsing {url}: {e}")
        return None


def search_and_scrape(query: str, limit: int = 5) -> list[dict]:
    """
    Search Fragrantica and scrape details for each result.
    This is the main function for the auto-scrape feature.
    """
    search_results = search_fragrantica(query, limit=limit)
    detailed = []

    for result in search_results:
        time.sleep(2)  # Be respectful
        detail = scrape_perfume_detail(result["perfume_url"])
        if detail and detail.get("name"):
            detailed.append(detail)

    return detailed
