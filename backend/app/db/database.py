from pymongo import MongoClient
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL")

client = MongoClient(MONGODB_URL)
db = client["ai_course_builder"]  # You can name your DB anything you like
