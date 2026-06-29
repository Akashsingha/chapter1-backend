import os
from supabase import create_client
from dotenv import load_dotenv  # loading the .env file and using it

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

supabase = create_client(url, key) # connecting in create_clind