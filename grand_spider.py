import os
import logging
import requests
import functools
import threading
import time
import uuid
import json
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
REQUEST_TIMEOUT = 15 # Increased default timeout slightly for Selenium fetches
SELENIUM_TIMEOUT = 20 # Timeout for Selenium page loads and waits
MAX_CONTENT_LENGTH = 15000
OPENAI_MODEL = "gpt-4o-mini"
MAX_RESPONSE_TOKENS_PAGE = 300
MAX_RESPONSE_TOKENS_SUMMARY = 500
CRAWLER_USER_AGENT = 'GrandSpiderCompanyAnalyzer/1.1 (+http://yourappdomain.com/bot)' # Updated version

# --- Job Management (Thread-Safe) ---
jobs = {}
jobs_lock = threading.Lock()

# --- Authentication Decorator ---
# (require_api_key function remains the same as your previous version)
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

# --- Selenium Crawler Logic ---
def selenium_crawl_website(base_url, max_pages=10):
    """
    Crawls a website using Selenium to handle JavaScript rendering.
    Returns a list of unique URLs found.
    """
    if not SELENIUM_AVAILABLE:
        raise RuntimeError("Selenium is not available or not installed correctly.")

    logger.info(f"Starting Selenium crawl for {base_url}, max_pages={max_pages}")
    urls_to_visit = {base_url}
    visited_urls = set()
    found_pages_details = []
    base_domain = urlparse(base_url).netloc
    driver = None # Initialize driver to None for finally block

    try:
        chrome_options = ChromeOptions()
        chrome_options.add_argument("--headless") # Run headless
        chrome_options.add_argument("--no-sandbox") # Important for Linux/Docker
        chrome_options.add_argument("--disable-dev-shm-usage") # Important for Linux/Docker
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument(f"user-agent={CRAWLER_USER_AGENT}")
        # Disable logging clutter from Selenium/WebDriver Manager
        chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
        # Suppress webdriver-manager logs if desired (might require specific config)

        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(SELENIUM_TIMEOUT) # Timeout for page loads

        while urls_to_visit and len(found_pages_details) < max_pages:
            current_url = urls_to_visit.pop()
            if current_url in visited_urls:
                continue

            current_domain = urlparse(current_url).netloc
            if current_domain != base_domain:
                logger.debug(f"[Selenium] Skipping external URL: {current_url}")
                continue

            visited_urls.add(current_url)
            logger.debug(f"[Selenium] Visiting: {current_url}")

            try:
                driver.get(current_url)
                # Wait for body to be present, basic check for page load
                WebDriverWait(driver, SELENIUM_TIMEOUT).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                # Optional: Add a small fixed sleep if pages are still loading dynamically after body appears
                # time.sleep(1)

                # Check status implicitly (Selenium usually throws error if page fails badly)
                # For more specific checks, you might need browser logs or JS execution
                found_pages_details.append({'url': current_url, 'status': 'found'})
                logger.info(f"[Selenium] Found page ({len(found_pages_details)}/{max_pages}): {current_url}")

                # Extract links from the fully rendered page
                links = driver.find_elements(By.TAG_NAME, 'a')
                for link in links:
                    href = link.get_attribute('href')
                    if href:
                        absolute_url = urljoin(base_url, href)
                        absolute_url = urlparse(absolute_url)._replace(fragment="").geturl()

                        if urlparse(absolute_url).netloc == base_domain and \
                           absolute_url not in visited_urls and \
                           absolute_url not in urls_to_visit:
                            urls_to_visit.add(absolute_url)

            except TimeoutException:
                logger.error(f"[Selenium] Timeout loading URL: {current_url}")
            except WebDriverException as e:
                logger.error(f"[Selenium] WebDriver error for URL {current_url}: {e}")
            except Exception as e:
                logger.error(f"[Selenium] Unexpected error processing URL {current_url}: {e}")

    except Exception as setup_error:
         logger.error(f"[Selenium] Failed to initialize or run Selenium driver: {setup_error}", exc_info=True)
         # Reraise or handle as appropriate, maybe return empty list?
         raise RuntimeError(f"Selenium setup/runtime error: {setup_error}") from setup_error
    finally:
        if driver:
            try:
                driver.quit()
                logger.debug("[Selenium] WebDriver quit successfully.")
            except Exception as quit_err:
                logger.error(f"[Selenium] Error quitting WebDriver: {quit_err}")

    logger.info(f"Selenium crawl finished for {base_url}. Found {len(found_pages_details)} pages.")
    return found_pages_details


# --- Simple Crawler Logic (Requests/BS4) ---
# (simple_crawl_website function remains the same as your previous version)
def simple_crawl_website(base_url, max_pages=10):
    """
    Crawls a website starting from base_url, staying within the same domain.
    Returns a list of unique URLs found.
    """
    logger.info(f"Starting simple crawl for {base_url}, max_pages={max_pages}")
    urls_to_visit = {base_url}
    visited_urls = set()
    found_pages_details = [] # Store dicts with {'url': url, 'status': 'found'}

    base_domain = urlparse(base_url).netloc

    headers = {'User-Agent': CRAWLER_USER_AGENT}

    while urls_to_visit and len(found_pages_details) < max_pages:
        current_url = urls_to_visit.pop()
        if current_url in visited_urls:
            continue

        # Only visit pages within the original domain
        if urlparse(current_url).netloc != base_domain:
            logger.debug(f"[Simple] Skipping external URL: {current_url}")
            continue

        visited_urls.add(current_url)
        logger.debug(f"[Simple] Visiting: {current_url}")

        try:
            response = requests.get(current_url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            response.raise_for_status() # Check for HTTP errors

            # Add successfully visited URL to our list
            if response.status_code == 200 and 'text/html' in response.headers.get('Content-Type', '').lower():
                 found_pages_details.append({'url': current_url, 'status': 'found'})
                 logger.info(f"[Simple] Found page ({len(found_pages_details)}/{max_pages}): {current_url}")

                 # Parse HTML for more links
                 soup = BeautifulSoup(response.text, 'html.parser')
                 for link in soup.find_all('a', href=True):
                    href = link['href']
                    # Construct absolute URL
                    absolute_url = urljoin(base_url, href)
                    # Basic cleanup - remove fragments
                    absolute_url = urlparse(absolute_url)._replace(fragment="").geturl()

                    # Check if it's within scope and not visited/queued
                    if urlparse(absolute_url).netloc == base_domain and \
                       absolute_url not in visited_urls and \
                       absolute_url not in urls_to_visit:
                         urls_to_visit.add(absolute_url)

            else:
                logger.warning(f"[Simple] Skipping non-HTML or non-200 page: {current_url} (Status: {response.status_code}, Type: {response.headers.get('Content-Type')})")


        except requests.exceptions.Timeout:
            logger.error(f"[Simple] Timeout occurred while crawling URL: {current_url}")
        except requests.exceptions.RequestException as e:
            logger.error(f"[Simple] Error crawling URL {current_url}: {e}")
        except Exception as e:
             logger.error(f"[Simple] Unexpected error processing URL {current_url}: {e}")

    logger.info(f"Simple crawl finished for {base_url}. Found {len(found_pages_details)} pages.")
    return found_pages_details

# --- Helper Functions (Fetch Content, Analyze, Summarize) ---
# (fetch_url_content, analyze_single_page_with_openai, summarize_company_with_openai remain the same)
def fetch_url_content(url: str) -> str:
    """Fetches HTML content from a URL with error handling."""
    headers = {'User-Agent': CRAWLER_USER_AGENT}
    try:
        # Use a slightly longer timeout for fetching content as well
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type:
            logger.warning(f"URL {url} returned non-HTML content type: {content_type}. Attempting analysis anyway.")
        # Decode explicitly to handle potential encoding issues better
        response.encoding = response.apparent_encoding # Guess encoding
        return response.text[:MAX_CONTENT_LENGTH]
    except requests.exceptions.Timeout:
        logger.error(f"Timeout fetching content for analysis: {url}")
        raise TimeoutError(f"Request timed out after {REQUEST_TIMEOUT} seconds.")
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Error fetching content for analysis {url}: {req_err}")
        raise ConnectionError(f"Failed to fetch URL content: {req_err}")
    except Exception as e:
         logger.error(f"Unexpected error fetching content for {url}: {e}", exc_info=True)
         raise ConnectionError(f"An unexpected error occurred while fetching content for the URL.")


def analyze_single_page_with_openai(html_content: str, url: str) -> str:
    """Analyzes single page HTML content using OpenAI."""
    if not openai_client:
        raise ConnectionError("OpenAI client is not initialized.")

    prompt = f"""
    Analyze ONLY the following HTML content from the URL '{url}'.
    Describe the specific purpose and key information presented ON THIS PAGE.
    Focus on what a human visitor would see and understand from THIS specific page.
    Be concise (1-2 sentences).

    HTML Content (potentially truncated):
    ```html
    {html_content}
    ```
    """
    try:
        completion = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are an AI assistant analyzing individual web pages."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=MAX_RESPONSE_TOKENS_PAGE,
            temperature=0.3,
        )
        description = completion.choices[0].message.content.strip()
        logger.info(f"Successfully analyzed single page: {url}")
        return description
    except (APIError, APITimeoutError, RateLimitError, APIConnectionError) as e:
        logger.error(f"OpenAI API error during single page analysis for {url}: {e}")
        raise ConnectionError(f"OpenAI API error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during OpenAI single page analysis for {url}: {e}")
        raise RuntimeError(f"An unexpected error occurred during single page AI analysis.")

def summarize_company_with_openai(page_summaries: list[dict], root_url: str) -> str:
    """Summarizes the company based on individual page descriptions using OpenAI."""
    if not openai_client:
        raise ConnectionError("OpenAI client is not initialized.")
    if not page_summaries:
        return "No page summaries were available to generate a company overview."

    combined_text = f"Based on analyses of the following pages from the website {root_url}:\n\n"
    for summary in page_summaries:
        combined_text += f"- URL: {summary['url']}\n  Summary: {summary['description']}\n\n"

    # Limit combined text length to avoid excessive prompt size
    max_prompt_chars = 10000 # Adjust as needed
    if len(combined_text) > max_prompt_chars:
        logger.warning(f"Combined text for summary exceeds {max_prompt_chars} chars, truncating.")
        combined_text = combined_text[:max_prompt_chars] + "\n... [Summaries Truncated]"


    prompt = f"""
    Analyze the following collection of summaries from different pages of the website {root_url}.
    Synthesize these descriptions into a comprehensive overview of the company or organization.
    Describe its main purpose, key offerings/services, target audience, and overall mission based *only* on the information provided in the summaries below.
    Structure the output logically.

    Individual Page Summaries:
    {combined_text}

    Provide a consolidated company description:
    """

    try:
        completion = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are an AI assistant synthesizing information from multiple webpage summaries into a single company overview."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=MAX_RESPONSE_TOKENS_SUMMARY,
            temperature=0.5,
        )
        final_summary = completion.choices[0].message.content.strip()
        logger.info(f"Successfully generated final company summary for {root_url}")
        return final_summary
    except (APIError, APITimeoutError, RateLimitError, APIConnectionError) as e:
        logger.error(f"OpenAI API error during final summarization for {root_url}: {e}")
        raise ConnectionError(f"OpenAI API error during summarization: {e}")
    except Exception as e:
        logger.error(f"Unexpected error during OpenAI final summarization for {root_url}: {e}")
        raise RuntimeError(f"An unexpected error occurred during final AI summarization.")


# --- Background Job Runner ---
def run_company_analysis_job(job_id, url, max_pages, use_selenium): # Added use_selenium parameter
    """Background task to run the full company analysis workflow."""
    thread_name = threading.current_thread().name
    logger.info(f"[{thread_name}] Starting analysis job {job_id} for {url} (Selenium: {use_selenium})")

    start_time = time.time()
    found_pages = []
    analyzed_pages = []
    final_summary = None
    job_failed = False
    error_message = None

    try:
        # --- Step 1: Crawl Website ---
        logger.info(f"[{thread_name}][{job_id}] Step 1: Crawling website {url}...")
        with jobs_lock:
            jobs[job_id]["status"] = "crawling"
            jobs[job_id]["started_at"] = start_time
            jobs[job_id]["crawler_used"] = "selenium" if use_selenium else "simple" # Record crawler type

        # --- Choose Crawler ---
        if use_selenium:
            if not SELENIUM_AVAILABLE:
                 raise RuntimeError("Requested Selenium crawl, but Selenium is not installed/available.")
            try:
                found_pages = selenium_crawl_website(url, max_pages)
            except Exception as selenium_err:
                 logger.error(f"[{thread_name}][{job_id}] Selenium crawl failed: {selenium_err}", exc_info=True)
                 raise ValueError(f"Selenium crawling failed: {selenium_err}") from selenium_err
        else:
            found_pages = simple_crawl_website(url, max_pages)

        logger.info(f"[{thread_name}][{job_id}] Crawling complete. Found {len(found_pages)} pages.")
        with jobs_lock:
            jobs[job_id]["found_pages_count"] = len(found_pages)
            jobs[job_id]["found_page_urls"] = [p['url'] for p in found_pages] # Store just URLs

        if not found_pages:
            logger.warning(f"[{thread_name}][{job_id}] No pages found during crawl. Cannot proceed.")
            # Don't raise error here, let job complete with empty results
            final_summary = "Could not generate summary: Crawling found no accessible pages."
            # Skip to finalization below

        else: # Only proceed if pages were found
            # --- Step 2: Analyze Individual Pages ---
            # (This section remains largely the same, just logging thread/job id)
            logger.info(f"[{thread_name}][{job_id}] Step 2: Analyzing {len(found_pages)} individual pages...")
            with jobs_lock:
                jobs[job_id]["status"] = "analyzing_pages"
                jobs[job_id]["analyzed_pages"] = [] # Initialize list

            pages_analyzed_count = 0
            for page in found_pages:
                page_url = page['url']
                logger.info(f"[{thread_name}][{job_id}] Analyzing page: {page_url}")
                page_analysis_result = {"url": page_url, "status": "pending"}
                try:
                    # Ensure fetch_url_content is robust enough for content retrieval
                    html_content = fetch_url_content(page_url)
                    if not html_content: # Handle empty content case
                        raise ValueError("Fetched content is empty.")
                    description = analyze_single_page_with_openai(html_content, page_url)
                    page_analysis_result["status"] = "analyzed"
                    page_analysis_result["description"] = description
                    analyzed_pages.append(page_analysis_result) # Add successful analysis
                    pages_analyzed_count += 1
                    logger.info(f"[{thread_name}][{job_id}] Successfully analyzed page {page_url}")

                except (TimeoutError, ConnectionError, RuntimeError, ValueError) as page_err:
                    logger.error(f"[{thread_name}][{job_id}] Failed to analyze page {page_url}: {page_err}")
                    page_analysis_result["status"] = "failed"
                    page_analysis_result["error"] = str(page_err)
                    # Store failure details
                    with jobs_lock:
                        if "analyzed_pages" not in jobs[job_id]: jobs[job_id]["analyzed_pages"] = []
                        jobs[job_id]["analyzed_pages"].append(page_analysis_result)
                except Exception as page_err:
                     logger.error(f"[{thread_name}][{job_id}] Unexpected error analyzing page {page_url}: {page_err}", exc_info=True)
                     page_analysis_result["status"] = "failed"
                     page_analysis_result["error"] = f"Unexpected error: {page_err}"
                     with jobs_lock:
                        if "analyzed_pages" not in jobs[job_id]: jobs[job_id]["analyzed_pages"] = []
                        jobs[job_id]["analyzed_pages"].append(page_analysis_result)

                # Update progress within the loop
                with jobs_lock:
                     jobs[job_id]["progress"] = f"{pages_analyzed_count}/{len(found_pages)} pages analyzed"
                     # Update the full list of analyzed pages status within the job
                     existing_pages = jobs[job_id].get("analyzed_pages", [])
                     updated = False
                     for i, existing_page in enumerate(existing_pages):
                         if existing_page.get("url") == page_url and existing_page["status"] == "pending":
                             existing_pages[i] = page_analysis_result
                             updated = True
                             break
                     if not updated: # Append if it wasn't pending (e.g., added as failed above)
                         # Check if already exists to avoid duplicates if logic gets complex
                         if not any(p.get("url") == page_url for p in existing_pages):
                              existing_pages.append(page_analysis_result)
                     jobs[job_id]["analyzed_pages"] = existing_pages


            logger.info(f"[{thread_name}][{job_id}] Individual page analysis complete. Successfully analyzed {pages_analyzed_count} pages.")

            # --- Step 3: Combine and Summarize ---
            successful_analyses = [p for p in analyzed_pages if p["status"] == "analyzed"]
            if successful_analyses:
                logger.info(f"[{thread_name}][{job_id}] Step 3: Generating final company summary from {len(successful_analyses)} page descriptions...")
                with jobs_lock:
                    jobs[job_id]["status"] = "summarizing"

                final_summary = summarize_company_with_openai(successful_analyses, url)
                with jobs_lock:
                    jobs[job_id]["final_summary"] = final_summary
                logger.info(f"[{thread_name}][{job_id}] Final summary generated.")
            else:
                logger.warning(f"[{thread_name}][{job_id}] No successful page analyses to generate final summary.")
                final_summary = "Could not generate summary: No pages were successfully analyzed."
                # Store this message even if analysis step was skipped due to no pages found
                with jobs_lock:
                    jobs[job_id]["final_summary"] = final_summary


    except Exception as e:
        logger.error(f"[{thread_name}][{job_id}] Job failed with error: {e}", exc_info=True)
        job_failed = True
        error_message = str(e)
        # Ensure status reflects failure even if error happened early
        with jobs_lock:
             if job_id in jobs: # Check if job still exists
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = error_message

    # --- Step 4: Finalize Job ---
    end_time = time.time()
    duration = end_time - start_time
    final_status = "failed" if job_failed else "completed"

    with jobs_lock:
        # Check job exists before updating final status
        if job_id in jobs:
            jobs[job_id]["status"] = final_status
            jobs[job_id]["finished_at"] = end_time
            jobs[job_id]["duration_seconds"] = round(duration, 2)
            # Ensure error is set if job failed
            if final_status == "failed" and not jobs[job_id].get("error"):
                 jobs[job_id]["error"] = error_message if error_message else "Unknown error during execution"
            # Ensure final summary is set even if job failed after summary step started
            if final_summary is not None and not jobs[job_id].get("final_summary"):
                 jobs[job_id]["final_summary"] = final_summary
        else:
             logger.warning(f"[{thread_name}][{job_id}] Job data not found during finalization. Could have been deleted.")


    logger.info(f"[{thread_name}][{job_id}] Job finished with status: {final_status}. Duration: {duration:.2f} seconds.")


# --- API Endpoints ---
@app.route('/api/analyze-company', methods=['POST'])
@require_api_key
def start_company_analysis():
    """
    Starts a new company analysis job (crawl, analyze pages, summarize).

    Expected JSON payload:
    {
        "url": "https://example.com",
        "max_pages": 10,             // Optional, default is 10
        "use_selenium": false        // Optional, default is false. Set true for JS-heavy sites.
    }
    """
    if not request.is_json:
        return jsonify({"error": "Bad Request", "message": "Request body must be JSON"}), 400

    data = request.get_json()
    url = data.get('url')
    if not url or not url.startswith(('http://', 'https://')):
        return jsonify({"error": "Bad Request", "message": "Valid 'url' starting with http:// or https:// is required"}), 400

    max_pages = int(data.get('max_pages', 10))
    use_selenium = bool(data.get('use_selenium', False)) # Get selenium flag

    if max_pages <= 0 or max_pages > 50: # Reduced max pages slightly for resource control
         logger.warning(f"Request for {url} capped max_pages from {max_pages} to 50.")
         max_pages = 50 # Apply cap

    # Check if Selenium was requested but is unavailable
    if use_selenium and not SELENIUM_AVAILABLE:
         logger.error(f"Job request for {url} failed: use_selenium=true but Selenium is not available.")
         return jsonify({"error": "Bad Request", "message": "Selenium support is not available on this server."}), 400

    if not openai_client:
         logger.error(f"Cannot start job for {url}: OpenAI client is not available.")
         return jsonify({"error": "Service Configuration Error", "message": "OpenAI service is not configured/initialized."}), 503

    job_id = str(uuid.uuid4())
    logger.info(f"Received request to start company analysis job {job_id} for URL: {url}, max_pages: {max_pages}, use_selenium: {use_selenium}")

    job_details = {
        "id": job_id,
        "url": url,
        "max_pages": max_pages,
        "use_selenium": use_selenium, # Store the flag
        "status": "pending",
        "created_at": time.time(),
        "found_pages_count": 0,
        "analyzed_pages": [],
        "final_summary": None,
        "error": None,
        "crawler_used": None # Will be set when job runs
    }
    with jobs_lock:
        jobs[job_id] = job_details

    # Start background job, passing the use_selenium flag
    thread = threading.Thread(
        target=run_company_analysis_job,
        args=(job_id, url, max_pages, use_selenium), # Pass flag here
        name=f"Job-{job_id[:6]}"
    )
    thread.start()

    return jsonify({
        "message": "Company analysis job started successfully.",
        "job_id": job_id,
        "status_url": f"/api/analyze-company/{job_id}"
    }), 202

# (get_company_analysis_status, list_analysis_jobs, health_check endpoints remain the same)
@app.route('/api/analyze-company/<job_id>', methods=['GET'])
@require_api_key
def get_company_analysis_status(job_id):
    """Get the status and results of a company analysis job."""
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({"error": "Not Found", "message": "Job ID not found."}), 404

    # Return a copy to avoid direct modification issues if needed elsewhere
    return jsonify(job.copy()), 200


@app.route('/api/jobs', methods=['GET'])
@require_api_key
def list_analysis_jobs():
    """List all submitted analysis jobs (summary view)."""
    jobs_list = []
    with jobs_lock:
        for job_id, job in jobs.items():
            # Add crawler_used to the summary view
            jobs_list.append({
                "job_id": job_id,
                "url": job.get("url"),
                "status": job.get("status"),
                "crawler_used": job.get("crawler_used"),
                "created_at": job.get("created_at"),
                "finished_at": job.get("finished_at"),
                "duration_seconds": job.get("duration_seconds"),
                "error": job.get("error")
            })
    # Sort by creation time, newest first
    jobs_list.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    return jsonify({"total_jobs": len(jobs_list), "jobs": jobs_list})


@app.route('/api/health', methods=['GET'])
def health_check():
    """Simple health check endpoint."""
    health_status = {"status": "ok", "message": "Company Analyzer API is running"}
    status_code = 200

    if not EXPECTED_SERVICE_API_KEY:
        health_status["service_api_key_status"] = "missing"
        health_status["status"] = "error"
        status_code = 503
    else:
        health_status["service_api_key_status"] = "configured"

    if not openai_client:
         health_status["openai_client_status"] = "not_initialized (check key)"
         health_status["status"] = "error"
         status_code = 503
    else:
         health_status["openai_client_status"] = "initialized"

    # Add Selenium availability check
    health_status["selenium_support"] = "available" if SELENIUM_AVAILABLE else "not_available"
    if not SELENIUM_AVAILABLE:
         health_status["notes"] = health_status.get("notes", "") + " Selenium crawls will fail. "


    return jsonify(health_status), status_code


# --- Global Error Handlers ---
# (Error handlers 404, 405, 500 remain the same)
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not Found", "message": "The requested endpoint does not exist."}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "Method Not Allowed", "message": "The HTTP method is not allowed for this endpoint."}), 405

@app.errorhandler(500)
def internal_server_error(error):
    logger.error(f"Internal Server Error encountered: {error}", exc_info=True)
    return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred on the server."}), 500


# --- Main Execution ---
if __name__ == '__main__':
    if not EXPECTED_SERVICE_API_KEY or not openai_client:
        logger.error("FATAL: Service cannot start due to missing API key configuration. Check .env file and logs.")
        exit(1)
    else:
        logger.info("Company Analyzer API starting...")
        logger.info(f"Service API Key: Configured")
        logger.info(f"OpenAI Client: Initialized")
        logger.info(f"Selenium Support Available: {SELENIUM_AVAILABLE}")
        if not SELENIUM_AVAILABLE:
             logger.warning("Running without Selenium support. 'use_selenium=true' requests will fail.")
        # Set debug=False for production
        app.run(host='0.0.0.0', port=5000, debug=False)