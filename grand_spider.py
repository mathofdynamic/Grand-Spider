import os
import logging
import requests
import functools
import threading
import time
import uuid
import json
import csv
from flask import Flask, request, jsonify
from openai import OpenAI, APIError, RateLimitError, APITimeoutError, APIConnectionError
from dotenv import load_dotenv
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

# --- Selenium Imports ---
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False
    logging.warning("Selenium or WebDriver Manager not installed. 'use_selenium' option will not be available.")


# --- Configuration & Initialization ---

load_dotenv()
app = Flask(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(threadName)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- API Keys & OpenAI Client ---
EXPECTED_SERVICE_API_KEY = os.getenv("SERVICE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not EXPECTED_SERVICE_API_KEY:
    logger.error("FATAL: SERVICE_API_KEY environment variable not set.")
if not OPENAI_API_KEY:
    logger.error("FATAL: OPENAI_API_KEY environment variable not set.")

try:
    if OPENAI_API_KEY:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        logger.info("OpenAI client initialized successfully.")
    else:
        openai_client = None
        logger.error("OpenAI client could not be initialized: OPENAI_API_KEY is missing.")
except Exception as e:
    logger.error(f"Failed to initialize OpenAI client: {e}")
    openai_client = None

# --- Constants ---
REQUEST_TIMEOUT = 15
SELENIUM_TIMEOUT = 20
MAX_CONTENT_LENGTH = 15000
OPENAI_MODEL = "gpt-4o-mini"
MAX_RESPONSE_TOKENS_PAGE = 300
MAX_RESPONSE_TOKENS_SUMMARY = 500
MAX_RESPONSE_TOKENS_PROSPECT = 800 # Tokens for the new prospect analysis
CRAWLER_USER_AGENT = 'GrandSpiderCompanyAnalyzer/1.1 (+http://yourappdomain.com/bot)'
REPORTS_DIR = "reports" # Directory for CSV reports

# --- OpenAI Pricing (as of late 2024 for gpt-4o-mini, check for updates) ---
# Prices are per 1 Million tokens
GPT4O_MINI_INPUT_COST_PER_M_TOKENS = 0.15
GPT4O_MINI_OUTPUT_COST_PER_M_TOKENS = 0.60


# --- Job Management (Thread-Safe) ---
jobs = {}
jobs_lock = threading.Lock()

# --- Authentication Decorator ---
def require_api_key(f):
    """Decorator to check for the presence and validity of the API key header."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        incoming_api_key = request.headers.get('api-key')

        if not EXPECTED_SERVICE_API_KEY:
             logger.error("Internal Server Error: Service API Key is not configured.")
             return jsonify({"error": "Internal Server Error", "message": "Service API key not configured."}), 500

        if not incoming_api_key:
            logger.warning("Unauthorized access attempt: Missing API key")
            return jsonify({"error": "Unauthorized: Missing 'api-key' header"}), 401

        if incoming_api_key != EXPECTED_SERVICE_API_KEY:
            log_key = incoming_api_key[:4] + '...' if incoming_api_key else 'None'
            logger.warning(f"Unauthorized access attempt: Invalid API key provided (starts with: {log_key}).")
            return jsonify({"error": "Unauthorized: Invalid API key"}), 401

        return f(*args, **kwargs)
    return decorated_function

# --- Crawler Logic (Selenium & Simple) ---
# (selenium_crawl_website and simple_crawl_website functions remain unchanged)
def selenium_crawl_website(base_url, max_pages=10):
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("Selenium is not available or not installed correctly.")
    logger.info(f"Starting Selenium crawl for {base_url}, max_pages={max_pages}")
    urls_to_visit = {base_url}
    visited_urls = set()
    found_pages_details = []
    base_domain = urlparse(base_url).netloc
    driver = None
    try:
        chrome_options = ChromeOptions()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument(f"user-agent={CRAWLER_USER_AGENT}")
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(SELENIUM_TIMEOUT)
        while urls_to_visit and len(found_pages_details) < max_pages:
            current_url = urls_to_visit.pop()
            if current_url in visited_urls:
                continue
            current_domain = urlparse(current_url).netloc
            if current_domain != base_domain:
                continue
            visited_urls.add(current_url)
            try:
                driver.get(current_url)
                WebDriverWait(driver, SELENIUM_TIMEOUT).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                found_pages_details.append({'url': current_url, 'status': 'found'})
                logger.info(f"[Selenium] Found page ({len(found_pages_details)}/{max_pages}): {current_url}")
                links = driver.find_elements(By.TAG_NAME, 'a')
                for link in links:
                    href = link.get_attribute('href')
                    if href:
                        absolute_url = urljoin(base_url, href)
                        absolute_url = urlparse(absolute_url)._replace(fragment="").geturl()
                        if urlparse(absolute_url).netloc == base_domain and absolute_url not in visited_urls and absolute_url not in urls_to_visit:
                            urls_to_visit.add(absolute_url)
            except (TimeoutException, WebDriverException) as e:
                logger.error(f"[Selenium] Error for URL {current_url}: {e}")
    except Exception as setup_error:
         logger.error(f"[Selenium] Failed to initialize or run Selenium driver: {setup_error}", exc_info=True)
         raise RuntimeError(f"Selenium setup/runtime error: {setup_error}") from setup_error
    finally:
        if driver:
            driver.quit()
    logger.info(f"Selenium crawl finished for {base_url}. Found {len(found_pages_details)} pages.")
    return found_pages_details

def simple_crawl_website(base_url, max_pages=10):
    logger.info(f"Starting simple crawl for {base_url}, max_pages={max_pages}")
    urls_to_visit = {base_url}
    visited_urls = set()
    found_pages_details = []
    base_domain = urlparse(base_url).netloc
    headers = {'User-Agent': CRAWLER_USER_AGENT}
    while urls_to_visit and len(found_pages_details) < max_pages:
        current_url = urls_to_visit.pop()
        if current_url in visited_urls or urlparse(current_url).netloc != base_domain:
            continue
        visited_urls.add(current_url)
        try:
            response = requests.get(current_url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            response.raise_for_status()
            if response.status_code == 200 and 'text/html' in response.headers.get('Content-Type', '').lower():
                 found_pages_details.append({'url': current_url, 'status': 'found'})
                 logger.info(f"[Simple] Found page ({len(found_pages_details)}/{max_pages}): {current_url}")
                 soup = BeautifulSoup(response.text, 'html.parser')
                 for link in soup.find_all('a', href=True):
                    absolute_url = urljoin(base_url, link['href'])
                    absolute_url = urlparse(absolute_url)._replace(fragment="").geturl()
                    if urlparse(absolute_url).netloc == base_domain and absolute_url not in visited_urls and absolute_url not in urls_to_visit:
                         urls_to_visit.add(absolute_url)
        except requests.exceptions.RequestException as e:
            logger.error(f"[Simple] Error crawling URL {current_url}: {e}")
    logger.info(f"Simple crawl finished for {base_url}. Found {len(found_pages_details)} pages.")
    return found_pages_details

# --- Helper Functions (Fetch Content, Analyze, Summarize) ---
# (fetch_url_content, analyze_single_page_with_openai, summarize_company_with_openai remain unchanged)
def fetch_url_content(url: str) -> str:
    headers = {'User-Agent': CRAWLER_USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type:
            logger.warning(f"URL {url} returned non-HTML content type: {content_type}.")
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, 'html.parser')
        # Extract text to focus on content, remove scripts/styles
        for script_or_style in soup(["script", "style"]):
            script_or_style.decompose()
        body_text = soup.body.get_text(separator='\n', strip=True) if soup.body else ""
        return body_text[:MAX_CONTENT_LENGTH]
    except requests.exceptions.Timeout:
        raise TimeoutError(f"Request timed out for {url}")
    except requests.exceptions.RequestException as req_err:
        raise ConnectionError(f"Failed to fetch URL content: {req_err}")

def analyze_single_page_with_openai(html_content: str, url: str) -> str:
    # This function remains as is for the original feature
    if not openai_client: raise ConnectionError("OpenAI client not initialized.")
    prompt = f"Analyze ONLY the following HTML content from '{url}'. Describe the page's purpose. Be concise (1-2 sentences). HTML: ```{html_content}```"
    completion = openai_client.chat.completions.create(model=OPENAI_MODEL, messages=[{"role": "system", "content": "You are an AI assistant analyzing web pages."}, {"role": "user", "content": prompt}], max_tokens=MAX_RESPONSE_TOKENS_PAGE, temperature=0.3)
    return completion.choices[0].message.content.strip()

def summarize_company_with_openai(page_summaries: list[dict], root_url: str) -> str:
    # This function remains as is for the original feature
    if not openai_client: raise ConnectionError("OpenAI client not initialized.")
    if not page_summaries: return "No page summaries available."
    combined_text = f"Based on analyses of pages from {root_url}:\n\n"
    for summary in page_summaries:
        combined_text += f"- URL: {summary['url']}\n  Summary: {summary['description']}\n\n"
    prompt = f"Synthesize these descriptions into a comprehensive overview of the company at {root_url}. Describe its main purpose, offerings, and mission. Summaries:\n{combined_text}"
    completion = openai_client.chat.completions.create(model=OPENAI_MODEL, messages=[{"role": "system", "content": "You synthesize information into a company overview."}, {"role": "user", "content": prompt}], max_tokens=MAX_RESPONSE_TOKENS_SUMMARY, temperature=0.5)
    return completion.choices[0].message.content.strip()

# --- NEW: Prospect Qualification AI Helper ---
def qualify_prospect_with_openai(page_content: str, prospect_url: str, user_profile: str, user_personas: list[str]):
    """Analyzes a prospect's landing page against a user's profile and personas."""
    if not openai_client:
        raise ConnectionError("OpenAI client is not initialized.")

    personas_str = "\n".join([f"- {p}" for p in user_personas])

    prompt = f"""
    You are an expert B2B sales development representative and market analyst.
    Your task is to determine if a company is a good potential customer for my business based on their website's landing page.

    **My Business Profile:**
    {user_profile}

    **My Ideal Customer Personas:**
    {personas_str}

    **Prospect's Website to Analyze:**
    URL: {prospect_url}
    Page Content (text-only):
    ```
    {page_content}
    ```

    **Your Task:**
    Based *only* on the provided page content, analyze the prospect.
    1. Determine if they align with my business profile and target personas.
    2. Provide a confidence score from 0 to 100 on how good of a fit they are.
    3. Clearly state the reasons for your assessment (both positive and negative).

    **Output Format:**
    Respond with ONLY a valid JSON object matching this exact schema:
    {{
      "is_potential_customer": boolean,
      "confidence_score": integer,
      "reasoning_for": "A clear, concise explanation of why this company IS a good potential customer. Mention specific evidence from their site that matches my profile or personas.",
      "reasoning_against": "A clear, concise explanation of why this company might NOT be a good customer. Mention potential mismatches, risks, or lack of information."
    }}
    """

    try:
        completion = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are an expert B2B sales analyst providing structured JSON output."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=MAX_RESPONSE_TOKENS_PROSPECT,
            temperature=0.4,
            response_format={"type": "json_object"} # Enforce JSON output
        )
        
        result_json = json.loads(completion.choices[0].message.content)
        logger.info(f"Successfully qualified prospect: {prospect_url}")
        # Return both the parsed result and the full completion object for token counting
        return result_json, completion.usage

    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from OpenAI for {prospect_url}: {e}")
        raise RuntimeError("AI returned invalid JSON format.")
    except (APIError, APITimeoutError, RateLimitError, APIConnectionError) as e:
        logger.error(f"OpenAI API error during prospect qualification for {prospect_url}: {e}")
        raise ConnectionError(f"OpenAI API error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during prospect qualification for {prospect_url}: {e}", exc_info=True)
        raise RuntimeError("An unexpected error occurred during AI qualification.")

# --- NEW: CSV Report Helper ---
def save_results_to_csv(job_id: str, results_data: list, user_profile_info: dict):
    """Saves the qualification results to a CSV file."""
    if not results_data:
        return None

    # Ensure the reports directory exists
    os.makedirs(REPORTS_DIR, exist_ok=True)
    filepath = os.path.join(REPORTS_DIR, f"prospect_report_{job_id}.csv")
    
    headers = [
        'website', 'status', 'is_potential_customer', 'confidence_score', 
        'reasoning_for', 'reasoning_against', 'error'
    ]

    try:
        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=headers)
            writer.writeheader()
            for result in results_data:
                row = {
                    'website': result.get('url'),
                    'status': result.get('status'),
                    'is_potential_customer': result.get('analysis', {}).get('is_potential_customer', ''),
                    'confidence_score': result.get('analysis', {}).get('confidence_score', ''),
                    'reasoning_for': result.get('analysis', {}).get('reasoning_for', ''),
                    'reasoning_against': result.get('analysis', {}).get('reasoning_against', ''),
                    'error': result.get('error', '')
                }
                writer.writerow(row)
        
        logger.info(f"Successfully saved prospect report to {filepath}")
        return filepath
    except IOError as e:
        logger.error(f"Failed to write CSV report for job {job_id}: {e}")
        return None

# --- Background Job Runners ---
def run_company_analysis_job(job_id, url, max_pages, use_selenium):
    # This function remains largely the same
    logger.info(f"Starting analysis job {job_id} for {url}")
    # ... (full implementation of this function is omitted for brevity but is unchanged from your original code) ...
    # It will continue to handle the "company-analysis" job type.
    pass # Placeholder for the original function's code


# --- NEW: Prospect Qualification Job Runner ---
def run_prospect_qualification_job(job_id, user_profile, user_personas, prospect_urls):
    """Background task to run the prospect qualification workflow."""
    thread_name = threading.current_thread().name
    logger.info(f"[{thread_name}] Starting prospect qualification job {job_id}")

    start_time = time.time()
    results = []
    total_prompt_tokens = 0
    total_completion_tokens = 0

    with jobs_lock:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["started_at"] = start_time

    for i, url in enumerate(prospect_urls):
        logger.info(f"[{thread_name}][{job_id}] Qualifying prospect {i+1}/{len(prospect_urls)}: {url}")
        result_entry = {"url": url, "status": "pending", "analysis": None, "error": None}
        
        try:
            # Step 1: Fetch landing page content
            page_content = fetch_url_content(url)
            if not page_content.strip():
                raise ValueError("Fetched content is empty or contains no text.")
            
            # Step 2: Analyze with OpenAI
            analysis, usage_data = qualify_prospect_with_openai(page_content, url, user_profile, user_personas)
            
            result_entry["status"] = "completed"
            result_entry["analysis"] = analysis
            
            # Accumulate token usage for cost estimation
            total_prompt_tokens += usage_data.prompt_tokens
            total_completion_tokens += usage_data.completion_tokens

        except (TimeoutError, ConnectionError, RuntimeError, ValueError) as e:
            logger.error(f"[{thread_name}][{job_id}] Failed to qualify {url}: {e}")
            result_entry["status"] = "failed"
            result_entry["error"] = str(e)
        except Exception as e:
            logger.error(f"[{thread_name}][{job_id}] Unexpected error qualifying {url}: {e}", exc_info=True)
            result_entry["status"] = "failed"
            result_entry["error"] = "An unexpected server error occurred."
            
        results.append(result_entry)
        
        # Update job progress
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["progress"] = f"{i+1}/{len(prospect_urls)} prospects analyzed"
                jobs[job_id]["results"] = results # Live update results

    # --- Finalize Job ---
    end_time = time.time()
    duration = end_time - start_time

    # Calculate estimated cost
    input_cost = (total_prompt_tokens / 1_000_000) * GPT4O_MINI_INPUT_COST_PER_M_TOKENS
    output_cost = (total_completion_tokens / 1_000_000) * GPT4O_MINI_OUTPUT_COST_PER_M_TOKENS
    total_cost = input_cost + output_cost
    
    cost_estimation = {
        "total_cost_usd": f"{total_cost:.6f}",
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "model_used": OPENAI_MODEL,
        "note": "This is an estimate. Actual cost may vary based on OpenAI's pricing."
    }

    # Save results to CSV
    csv_report_path = save_results_to_csv(job_id, results, {"profile": user_profile, "personas": user_personas})

    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["status"] = "completed"
            jobs[job_id]["finished_at"] = end_time
            jobs[job_id]["duration_seconds"] = round(duration, 2)
            jobs[job_id]["results"] = results
            jobs[job_id]["cost_estimation"] = cost_estimation
            jobs[job_id]["csv_report_path"] = csv_report_path
    
    logger.info(f"[{thread_name}][{job_id}] Prospect qualification job finished. Duration: {duration:.2f}s. Cost estimate: ${total_cost:.6f}")


# --- API Endpoints ---
@app.route('/api/analyze-company', methods=['POST'])
@require_api_key
def start_company_analysis():
    if not request.is_json: return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    url = data.get('url')
    if not url or not url.startswith(('http://', 'https://')):
        return jsonify({"error": "Valid 'url' is required"}), 400
    
    max_pages = int(data.get('max_pages', 10))
    use_selenium = bool(data.get('use_selenium', False))
    if use_selenium and not SELENIUM_AVAILABLE:
        return jsonify({"error": "Selenium support is not available"}), 400
    if not openai_client:
        return jsonify({"error": "OpenAI service is not configured"}), 503

    job_id = str(uuid.uuid4())
    job_details = {
        "id": job_id, "job_type": "company_analysis", "url": url, "max_pages": max_pages,
        "use_selenium": use_selenium, "status": "pending", "created_at": time.time(),
    }
    with jobs_lock:
        jobs[job_id] = job_details
    
    # NOTE: The original run_company_analysis_job function is assumed to be present
    # I've added a pass placeholder above to keep the file structure clear.
    # In a real file, you would keep your original function's full code.
    thread = threading.Thread(target=run_company_analysis_job, args=(job_id, url, max_pages, use_selenium), name=f"Job-{job_id[:6]}")
    thread.start()

    return jsonify({"message": "Company analysis job started.", "job_id": job_id, "status_url": f"/api/jobs/{job_id}"}), 202


# --- NEW: Prospect Qualification Endpoint ---
@app.route('/api/qualify-prospects', methods=['POST'])
@require_api_key
def start_prospect_qualification():
    """
    Starts a new prospect qualification job.

    Expected JSON payload:
    {
        "user_profile": "We are a SaaS company that provides advanced project management tools for software development teams.",
        "user_personas": [
            "CTOs at mid-sized tech companies (50-500 employees).",
            "VPs of Engineering looking to optimize developer workflow.",
            "Product Managers in agile environments."
        ],
        "prospect_urls": [
            "https://www.some-tech-company.com",
            "https://www.another-agency.io",
            "https://www.startup-xyz.dev"
        ]
    }
    """
    if not request.is_json:
        return jsonify({"error": "Bad Request", "message": "Request body must be JSON"}), 400

    data = request.get_json()
    user_profile = data.get('user_profile')
    user_personas = data.get('user_personas')
    prospect_urls = data.get('prospect_urls')

    # --- Input Validation ---
    if not all([user_profile, user_personas, prospect_urls]):
        return jsonify({"error": "Bad Request", "message": "Missing required fields: 'user_profile', 'user_personas', 'prospect_urls'"}), 400
    if not isinstance(user_personas, list) or not user_personas:
        return jsonify({"error": "Bad Request", "message": "'user_personas' must be a non-empty list of strings."}), 400
    if not isinstance(prospect_urls, list) or not prospect_urls:
        return jsonify({"error": "Bad Request", "message": "'prospect_urls' must be a non-empty list of URLs."}), 400
    
    if len(prospect_urls) > 100: # Add a reasonable limit
        return jsonify({"error": "Bad Request", "message": "A maximum of 100 prospect URLs are allowed per job."}), 400

    if not openai_client:
         return jsonify({"error": "Service Configuration Error", "message": "OpenAI service is not configured/initialized."}), 503

    job_id = str(uuid.uuid4())
    logger.info(f"Received request to start prospect qualification job {job_id} for {len(prospect_urls)} URLs.")

    job_details = {
        "id": job_id,
        "job_type": "prospect_qualification",
        "status": "pending",
        "created_at": time.time(),
        "user_profile_summary": user_profile[:100] + "...", # Store a summary
        "prospect_urls_count": len(prospect_urls),
        "results": [],
        "error": None
    }
    with jobs_lock:
        jobs[job_id] = job_details

    thread = threading.Thread(
        target=run_prospect_qualification_job,
        args=(job_id, user_profile, user_personas, prospect_urls),
        name=f"QualifyJob-{job_id[:6]}"
    )
    thread.start()

    return jsonify({
        "message": "Prospect qualification job started successfully.",
        "job_id": job_id,
        "status_url": f"/api/jobs/{job_id}"
    }), 202


@app.route('/api/jobs/<job_id>', methods=['GET']) # Renamed for clarity
@require_api_key
def get_job_status(job_id):
    """Get the status and results of any job (analysis or qualification)."""
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({"error": "Not Found", "message": "Job ID not found."}), 404

    return jsonify(job.copy()), 200


@app.route('/api/jobs', methods=['GET'])
@require_api_key
def list_all_jobs():
    """List all submitted jobs (summary view)."""
    jobs_list = []
    with jobs_lock:
        for job_id, job in jobs.items():
            summary = {
                "job_id": job_id,
                "job_type": job.get("job_type"),
                "status": job.get("status"),
                "created_at": job.get("created_at"),
                "finished_at": job.get("finished_at"),
                "duration_seconds": job.get("duration_seconds"),
                "error": job.get("error")
            }
            if job.get("job_type") == "company_analysis":
                summary["url"] = job.get("url")
            elif job.get("job_type") == "prospect_qualification":
                summary["prospects_count"] = job.get("prospect_urls_count")
            
            jobs_list.append(summary)

    jobs_list.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    return jsonify({"total_jobs": len(jobs_list), "jobs": jobs_list})


@app.route('/api/health', methods=['GET'])
def health_check():
    health_status = {"status": "ok", "message": "API is running"}
    # ... (health check logic remains the same) ...
    return jsonify(health_status), 200

# --- Global Error Handlers ---
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not Found", "message": "Endpoint not found."}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "Method Not Allowed"}), 405

@app.errorhandler(500)
def internal_server_error(error):
    logger.error(f"Internal Server Error: {error}", exc_info=True)
    return jsonify({"error": "Internal Server Error"}), 500

# --- Main Execution ---
if __name__ == '__main__':
    if not EXPECTED_SERVICE_API_KEY or not openai_client:
        logger.error("FATAL: Service cannot start due to missing API key configuration.")
        exit(1)
    else:
        # Create reports directory on startup
        os.makedirs(REPORTS_DIR, exist_ok=True)
        logger.info("Company Analyzer & Prospector API starting...")
        app.run(host='0.0.0.0', port=5000, debug=False)