-- ============================================================
-- PerfumAPI — perfume_catalog table
-- Run this in Supabase SQL Editor
-- This is SEPARATE from the FLACON fragrances table!
-- perfume_catalog = scraped Fragrantica data (shared catalog)
-- fragrances = user's personal collection with ratings etc.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.perfume_catalog (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    name TEXT NOT NULL,
    brand TEXT,
    release_year INTEGER,
    gender TEXT,
    notes_top TEXT[] DEFAULT '{}',
    notes_middle TEXT[] DEFAULT '{}',
    notes_base TEXT[] DEFAULT '{}',
    rating REAL,
    votes INTEGER,
    description TEXT DEFAULT '',
    longevity TEXT DEFAULT '',
    sillage TEXT DEFAULT '',
    image_url TEXT DEFAULT '',
    perfume_url TEXT UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc', NOW())
);

CREATE INDEX IF NOT EXISTS idx_catalog_name ON public.perfume_catalog USING gin (to_tsvector('simple', name));
CREATE INDEX IF NOT EXISTS idx_catalog_brand ON public.perfume_catalog(brand);
CREATE INDEX IF NOT EXISTS idx_catalog_url ON public.perfume_catalog(perfume_url);

-- Allow public read access (no auth needed for searching)
ALTER TABLE public.perfume_catalog ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read access to catalog"
    ON public.perfume_catalog FOR SELECT
    USING (true);

-- Service role can insert/update (the PerfumAPI backend uses service key)
CREATE POLICY "Service role can manage catalog"
    ON public.perfume_catalog FOR ALL
    USING (true)
    WITH CHECK (true);
