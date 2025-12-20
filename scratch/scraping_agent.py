import json

import requests
from bs4 import BeautifulSoup
from google.cloud import aiplatform
from googleapiclient.discovery import build

# --- CONFIGURATION ---
PROJECT_ID = "your-gcp-project-id"
LOCATION = "us-central1"
API_KEY = "your-google-search-api-key"
SEARCH_ENGINE_ID = "your-search-engine-cx-id"

# Initialize Vertex AI
aiplatform.init(project=PROJECT_ID, location=LOCATION)


def search_audible_link(book_title):
    """Finds the Audible URL using Google Custom Search."""
    service = build("customsearch", "v1", developerKey=API_KEY)

    query = f"site:audible.com {book_title} audiobook"
    res = service.cse().list(q=query, cx=SEARCH_ENGINE_ID, num=1).execute()

    if "items" in res:
        return res["items"][0]["link"]
    return None


def fetch_page_content(url):
    """Fetches raw HTML (mimicking a browser)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/91.0.4472.124 Safari/537.36"
    }
    response = requests.get(url, headers=headers)

    # We strip scripts/styles to save tokens, even though Gemini is cheap
    soup = BeautifulSoup(response.content, "html.parser")
    for script in soup(["script", "style"]):
        script.extract()

    return soup.get_text()


def extract_with_gemini(text_content):
    """Uses Gemini 1.5 Flash to extract data."""
    from vertexai.generative_models import GenerationConfig, GenerativeModel

    model = GenerativeModel("gemini-1.5-flash-001")

    # We define the JSON schema in the prompt for Flash (it follows instructions well)
    prompt = f"""
    You are a data extraction agent. Extract the following fields from the text below.
    Return ONLY a raw JSON object. Do not use Markdown formatting.

    Fields required:
    - title (string)
    - narrator (string): clean name only
    - length_minutes (int): convert "X hrs Y mins" to total minutes
    - rating (float): 0.0 to 5.0

    Input Text:
    {text_content[:30000]}  # Limiting context slightly, though Flash can handle much more
    """

    response = model.generate_content(
        prompt,
        generation_config=GenerationConfig(
            temperature=0, response_mime_type="application/json"
        ),
    )

    return response.text


# --- MAIN EXECUTION ---
book = "The Way of Kings"
print(f"1. Searching for: {book}...")
url = search_audible_link(book)

if url:
    print(f"2. Found URL: {url}")
    print("3. Scraping content...")
    content = fetch_page_content(url)

    print("4. Extracting with Gemini Flash...")
    data = extract_with_gemini(content)

    print("\n--- Result ---")
    print(json.dumps(json.loads(data), indent=2))
else:
    print("Book not found.")
