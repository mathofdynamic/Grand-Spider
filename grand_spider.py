import os
import logging
import requests
import functools
from flask import Flask, request, jsonify
from openai import OpenAI, APIError
from dotenv import load_dotenv

# --- Configuration & Initialization ---

load_dotenv()  # Load environment variables from .env file

app = Flask(__name__)

# Configure Logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- API Keys & OpenAI Client ---

# Load the SERVICE_API_KEY required for incoming requests
# Renamed variable for clarity
EXPECTED_SERVICE_API_KEY = os.getenv("SERVICE_API_KEY")
if not EXPECTED_SERVICE_API_KEY:
    logger.error("FATAL: SERVICE_API_KEY environment variable not set.")
    # In a real app, you might exit or raise a more specific config error

# Load and initialize OpenAI client
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.error("FATAL: OPENAI_API_KEY environment variable not set.")
    # Handle appropriately
    # raise ValueError("OpenAI API Key not found in environment variables") # Example

try:
    # Check if OPENAI_API_KEY was actually loaded before initializing
    if OPENAI_API_KEY:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        logger.info("OpenAI client initialized successfully.")
    else:
        # Keep openai_client as None if key is missing
        openai_client = None
        logger.error("OpenAI client could not be initialized because OPENAI_API_KEY is missing.")
except Exception as e:
    logger.error(f"Failed to initialize OpenAI client: {e}")
    openai_client = None # Ensure client is None if init fails

# --- Constants ---
REQUEST_TIMEOUT = 15  # seconds for fetching URL content
MAX_CONTENT_LENGTH = 15000 # Limit characters sent to OpenAI to avoid large bills/token limits
OPENAI_MODEL = "gpt-4.1-nano-2025-04-14" # Specify the desired model
MAX_RESPONSE_TOKENS = 300 # Limit OpenAI response length


# --- Authentication Decorator ---

def require_api_key(f):
    """
    Decorator to enforce API key authentication.
    Checks for the 'api-key' header and compares it with the
    SERVICE_API_KEY from the environment variables.
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        # Fetch the API key from the 'api-key' header (lowercase, hyphenated)
        incoming_api_key = request.headers.get('api-key')

        if not EXPECTED_SERVICE_API_KEY:
             logger.error("Internal Server Error: Service API Key is not configured.")
             return jsonify({"error": "Internal Server Error", "message": "Service API key not configured."}), 500

        # Compare the incoming key with the expected key loaded from .env
        if incoming_api_key and incoming_api_key == EXPECTED_SERVICE_API_KEY:
            return f(*args, **kwargs)
        else:
            # Log the key attempt carefully in real-world scenarios to avoid logging secrets
            log_key = incoming_api_key[:4] + '...' if incoming_api_key else 'None' # Avoid logging full key
            logger.warning(f"Unauthorized access attempt with key starting: {log_key}")
            return jsonify({"error": "Unauthorized", "message": "Invalid or missing API Key in 'api-key' header"}), 401
    return decorated_function

# --- Helper Functions ---
# (fetch_url_content and analyze_content_with_openai remain the same as the previous version)
# ... (Keep the fetch_url_content and analyze_content_with_openai functions here) ...
def fetch_url_content(url: str) -> str:
    """Fetches HTML content from a URL with error handling."""
    headers = {
        'User-Agent': 'GrandSpider/1.0 (+http://yourappdomain.com/bot)' # Be a good web citizen
    }
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        content_type = response.headers.get('Content-Type', '').lower()

        # Basic check for HTML content type
        if 'text/html' not in content_type:
            logger.warning(f"URL {url} returned non-HTML content type: {content_type}")
            # Decide if you want to proceed or raise an error
            # For now, we'll proceed but log a warning
            # raise ValueError(f"Content is not HTML ({content_type})")

        # Return only the beginning of the content to manage token limits
        return response.text[:MAX_CONTENT_LENGTH]

    except requests.exceptions.Timeout:
        logger.error(f"Timeout occurred while fetching URL: {url}")
        raise TimeoutError(f"Request timed out after {REQUEST_TIMEOUT} seconds.")
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error occurred for URL {url}: {http_err}")
        raise ConnectionError(f"HTTP Error: {http_err.response.status_code} - {http_err.response.reason}")
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Error fetching URL {url}: {req_err}")
        raise ConnectionError(f"Failed to fetch URL: {req_err}")
    except Exception as e:
        logger.error(f"An unexpected error occurred during fetch for {url}: {e}")
        raise ConnectionError(f"An unexpected error occurred while fetching the URL.")


def analyze_content_with_openai(html_content: str, url: str) -> str:
    """Analyzes HTML content using OpenAI GPT-4o-mini."""
    if not openai_client:
        # This check is now more important as init might fail if key is missing
        logger.error(f"Cannot analyze {url}: OpenAI client is not initialized (check API key?).")
        raise ConnectionError("OpenAI client is not initialized.")

    prompt = f"""
    Analyze the following HTML content from the URL '{url}'.
    Describe the main purpose, key content sections, and overall topic of the webpage based on this HTML.
    Focus on what a human visitor would likely see or understand from the page.
    Keep the description concise and informative.

    HTML Content (potentially truncated):
    ```html
    {html_content}
    ```
    """

    try:
        completion = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are an AI assistant skilled at summarizing web pages from their HTML content."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=MAX_RESPONSE_TOKENS,
            temperature=0.5, # Lower temperature for more factual description
        )
        description = completion.choices[0].message.content.strip()
        logger.info(f"Successfully analyzed URL: {url}")
        return description
    except APIError as e:
        logger.error(f"OpenAI API error during analysis for {url}: {e}")
        # Check for authentication errors specifically
        if "authentication" in str(e).lower():
             raise ConnectionError(f"OpenAI API authentication error: Please check your OPENAI_API_KEY. Details: {e}")
        else:
             raise ConnectionError(f"OpenAI API error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during OpenAI analysis for {url}: {e}")
        raise RuntimeError(f"An unexpected error occurred during AI analysis.")

# --- API Endpoints ---

@app.route('/health', methods=['GET'])
def health_check():
    """Basic health check endpoint."""
    # Check if essential components are configured/initialized
    health_status = {"status": "ok", "components": {}}
    status_code = 200

    if not EXPECTED_SERVICE_API_KEY:
        health_status["status"] = "error"
        health_status["components"]["service_api_key"] = "missing"
        status_code = 500
    else:
        health_status["components"]["service_api_key"] = "configured"

    if not OPENAI_API_KEY:
        health_status["status"] = "error"
        health_status["components"]["openai_api_key"] = "missing"
        status_code = 500
    else:
        health_status["components"]["openai_api_key"] = "configured" # Doesn't guarantee validity

    if not openai_client:
         health_status["status"] = "error"
         health_status["components"]["openai_client"] = "not_initialized"
         status_code = 500
    else:
         health_status["components"]["openai_client"] = "initialized" # Doesn't guarantee reachability

    return jsonify(health_status), status_code


@app.route('/analyze', methods=['POST'])
@require_api_key # This now checks for the 'api-key' header
def analyze_url():
    """
    Main endpoint to fetch, analyze, and describe a URL.
    Requires JSON body: {"url": "http://example.com"}
    Requires Header: api-key: YOUR_SERVICE_API_KEY_HERE
    """
    if not request.is_json:
        logger.warning("Received non-JSON request to /analyze")
        return jsonify({"error": "Bad Request", "message": "Request body must be JSON"}), 400

    data = request.get_json()
    url = data.get('url')

    if not url:
        logger.warning("Missing 'url' parameter in request to /analyze")
        return jsonify({"error": "Bad Request", "message": "Missing 'url' in JSON body"}), 400

    # Basic URL validation (can be improved with regex or libraries like validators)
    if not url.startswith(('http://', 'https://')):
        logger.warning(f"Invalid URL format received: {url}")
        return jsonify({"error": "Bad Request", "message": "Invalid URL format. Must start with http:// or https://"}), 400

    logger.info(f"Received analysis request for URL: {url}")

    # Pre-check if OpenAI client is ready before attempting fetch
    if not openai_client:
        logger.error(f"Cannot process {url}: OpenAI client is not available (check API key?).")
        return jsonify({"error": "Service Configuration Error", "url": url, "message": "OpenAI service is not configured or initialized correctly."}), 503 # Service Unavailable

    try:
        # 1. Fetch Content
        logger.info(f"Fetching content for: {url}")
        html_content = fetch_url_content(url)
        logger.info(f"Successfully fetched content (truncated to {len(html_content)} chars) for: {url}")

        # 2. Analyze with OpenAI
        logger.info(f"Analyzing content for: {url} using {OPENAI_MODEL}")
        description = analyze_content_with_openai(html_content, url)

        # 3. Return Result
        return jsonify({
            "url": url,
            "description": description,
            "model_used": OPENAI_MODEL
        }), 200

    except TimeoutError as e:
        return jsonify({"error": "Gateway Timeout", "url": url, "message": str(e)}), 504
    except ConnectionError as e: # Covers fetch errors and OpenAI API errors
         # Distinguish between upstream fetch failure vs OpenAI failure if needed
         # If the error message indicates OpenAI auth issues specifically, return 503
        if "OpenAI API authentication error" in str(e):
            return jsonify({"error": "Service Configuration Error", "url": url, "message": str(e)}), 503 # Service Unavailable
        elif "OpenAI client is not initialized" in str(e):
             return jsonify({"error": "Service Configuration Error", "url": url, "message": str(e)}), 503 # Service Unavailable
        else:
            # Could be URL fetch error or other OpenAI API error
            return jsonify({"error": "Bad Gateway or Connection Issue", "url": url, "message": str(e)}), 502
    except ValueError as e: # e.g., non-HTML content if we choose to raise error
        return jsonify({"error": "Bad Request", "url": url, "message": str(e)}), 400
    except RuntimeError as e: # Covers unexpected OpenAI analysis errors
        return jsonify({"error": "Internal Server Error", "url": url, "message": str(e)}), 500
    except Exception as e:
        logger.error(f"Unhandled exception for URL {url}: {e}", exc_info=True) # Log full traceback
        return jsonify({"error": "Internal Server Error", "url": url, "message": "An unexpected error occurred."}), 500

# --- Global Error Handlers ---
# (Error handlers 404, 405, 500 remain the same)
# ... (Keep the 404, 405, 500 error handlers here) ...
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not Found", "message": "The requested URL was not found on the server."}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "Method Not Allowed", "message": "The method is not allowed for the requested URL."}), 405

@app.errorhandler(500)
def internal_server_error(error):
    # Log the actual error if possible, Flask usually does this
    logger.error(f"Internal Server Error: {error}")
    return jsonify({"error": "Internal Server Error", "message": "An internal server error occurred."}), 500


# --- Main Execution ---

if __name__ == '__main__':
    # Check if keys are present before starting
    keys_ok = True
    if not EXPECTED_SERVICE_API_KEY:
        logger.error("Startup Error: SERVICE_API_KEY is not configured in .env file. Service authentication will fail.")
        keys_ok = False
    if not OPENAI_API_KEY:
        logger.error("Startup Error: OPENAI_API_KEY is not configured in .env file. OpenAI analysis will fail.")
        keys_ok = False
    if not openai_client:
         logger.error("Startup Error: OpenAI client failed to initialize (likely due to missing API key). OpenAI analysis will fail.")
         # keys_ok is likely already False due to missing OPENAI_API_KEY check above, but good to note client status too
         keys_ok = False

    if keys_ok:
        logger.info("API Keys seem configured. Starting Grand Spider service...")
        # Use host='0.0.0.0' to make it accessible externally (e.g., from Postman)
        # debug=False is crucial for production environments
        app.run(host='0.0.0.0', port=5000, debug=False)
    else:
        logger.error("Grand Spider service cannot start due to missing configuration. Please check your .env file.")