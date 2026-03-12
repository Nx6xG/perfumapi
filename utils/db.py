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


def get_stats():
    result = supabase.table("perfume_catalog").select("id", count="exact").execute()
    return {"total_perfumes": result.count or 0}
