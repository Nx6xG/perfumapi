import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")

# Use service key for server-side operations (bypasses RLS)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY or SUPABASE_KEY)


def get_all_perfumes(limit: int = 100, offset: int = 0):
    result = (
        supabase.table("perfume_catalog")
        .select("*")
        .order("rating", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return result.data or []


def search_perfumes(query: str, limit: int = 20):
    """Search perfumes by name or brand using ilike."""
    q = f"%{query}%"
    result = (
        supabase.table("perfume_catalog")
        .select("*")
        .or_(f"name.ilike.{q},brand.ilike.{q}")
        .limit(limit)
        .execute()
    )
    return result.data or []


def get_perfume_by_id(perfume_id: str):
    result = (
        supabase.table("perfume_catalog")
        .select("*")
        .eq("id", perfume_id)
        .single()
        .execute()
    )
    return result.data


def upsert_perfume(perfume_data: dict):
    """Insert or update a perfume (upsert on perfume_url)."""
    result = (
        supabase.table("perfume_catalog")
        .upsert(perfume_data, on_conflict="perfume_url")
        .execute()
    )
    return result.data


def upsert_many(perfumes: list[dict]):
    """Bulk upsert perfumes."""
    if not perfumes:
        return []
    result = (
        supabase.table("perfume_catalog")
        .upsert(perfumes, on_conflict="perfume_url")
        .execute()
    )
    return result.data or []


def update_image_url(perfume_id: str, image_url: str):
    """Update the image_url for a single perfume by ID."""
    result = (
        supabase.table("perfume_catalog")
        .update({"image_url": image_url})
        .eq("id", perfume_id)
        .execute()
    )
    return result.data


def update_image_urls_batch(updates: dict[str, str]):
    """Batch update image_urls. updates = {perfume_id: image_url}."""
    results = []
    for pid, url in updates.items():
        try:
            r = update_image_url(pid, url)
            if r:
                results.extend(r)
        except Exception as e:
            print(f"Failed to update image for {pid}: {e}")
    return results


def get_perfumes_without_images(limit: int = 50):
    """Get perfumes that have no image_url set."""
    result = (
        supabase.table("perfume_catalog")
        .select("id,name,brand")
        .or_("image_url.is.null,image_url.eq.")
        .limit(limit)
        .execute()
    )
    return result.data or []


def find_perfume_by_name_brand(name: str, brand: str = ""):
    """Find a perfume by exact or close name+brand match."""
    q_name = f"%{name}%"
    query = supabase.table("perfume_catalog").select("*")
    if brand:
        q_brand = f"%{brand}%"
        query = query.ilike("name", q_name).ilike("brand", q_brand)
    else:
        query = query.ilike("name", q_name)
    result = query.limit(5).execute()
    return result.data or []


def get_stats():
    result = supabase.table("perfume_catalog").select("id", count="exact").execute()
    total = result.count or 0

    # Count perfumes with images
    img_result = (
        supabase.table("perfume_catalog")
        .select("id", count="exact")
        .neq("image_url", "")
        .not_.is_("image_url", "null")
        .execute()
    )
    with_images = img_result.count or 0

    return {
        "total_perfumes": total,
        "with_images": with_images,
        "without_images": total - with_images,
    }