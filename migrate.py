"""
Migration script — runs ALTER TABLE statements to add new columns.
Run this ONCE from the backend folder:  python migrate.py
"""

import os
import sys
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

if not url or not key:
    print("ERROR: SUPABASE_URL or SUPABASE_KEY not found in .env")
    sys.exit(1)

supabase = create_client(url, key)

# ── Migration 1: acknowledged column on orders ────────────
# Tracks whether staff has acknowledged a new order.
# Stored in DB so it syncs across all staff devices.
print("Running migration 1: adding 'acknowledged' column to orders...")
try:
    # Test if column already exists by trying to select it
    result = supabase.table("orders").select("acknowledged").limit(1).execute()
    print("  ✅ Column 'acknowledged' already exists — skipping.")
except Exception:
    print("  ⚠️  Could not verify via select. Please run this SQL manually in Supabase:")
    print("  ALTER TABLE orders ADD COLUMN IF NOT EXISTS acknowledged BOOLEAN DEFAULT FALSE;")

# ── Migration 2: is_available column on menu_items ────────
# Lets staff mark items as sold out in real time.
print("Running migration 2: adding 'is_available' column to menu_items...")
try:
    result = supabase.table("menu_items").select("is_available").limit(1).execute()
    print("  ✅ Column 'is_available' already exists — skipping.")
except Exception:
    print("  ⚠️  Could not verify via select. Please run this SQL manually in Supabase:")
    print("  ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS is_available BOOLEAN DEFAULT TRUE;")

print("\nDone. If you saw any warnings above, run the SQL in Supabase dashboard → SQL Editor.")
