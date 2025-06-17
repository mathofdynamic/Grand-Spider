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
        # Initialize OpenAI client with basic configuration
        openai_client = OpenAI(
            api_key=OPENAI_API_KEY,
            timeout=30.0,
            max_retries=3
        )
        logger.info("OpenAI client initialized successfully.")
    else:
        openai_client = None
        logger.error("OpenAI client could not be initialized: OPENAI_API_KEY is missing.")
except Exception as e:
    logger.error(f"Failed to initialize OpenAI client: {e}")
    logger.error("Attempting fallback initialization...")
    try:
        if OPENAI_API_KEY:
            # Fallback: minimal initialization
            openai_client = OpenAI(api_key=OPENAI_API_KEY)
            logger.info("OpenAI client initialized with fallback method.")
        else:
            openai_client = None
    except Exception as fallback_error:
        logger.error(f"Fallback initialization also failed: {fallback_error}")
        openai_client = None

# --- Constants ---
REQUEST_TIMEOUT = 15
SELENIUM_TIMEOUT = 20
MAX_CONTENT_LENGTH = 15000
OPENAI_MODEL = "gpt-4.1-nano-2025-04-14"
MAX_RESPONSE_TOKENS_PAGE = 300
MAX_RESPONSE_TOKENS_SUMMARY = 500
MAX_RESPONSE_TOKENS_PROSPECT = 800 # Tokens for the new prospect analysis
# GPT-4.1 nano specifications:
# - Context window: 1,047,576 tokens
# - Max output tokens: 32,768 tokens
CRAWLER_USER_AGENT = 'GrandSpiderCompanyAnalyzer/1.1 (+http://yourappdomain.com/bot)'
REPORTS_DIR = "reports" # Directory for CSV reports

# --- OpenAI Pricing for GPT-4.1 nano ---
# Prices are per 1 Million tokens
GPT4O_MINI_INPUT_COST_PER_M_TOKENS = 0.10     # Input tokens: $0.10 per 1M tokens
GPT4O_MINI_OUTPUT_COST_PER_M_TOKENS = 0.40    # Output tokens: $0.40 per 1M tokens
GPT4O_MINI_CACHED_INPUT_COST_PER_M_TOKENS = 0.025  # Cached input: $0.025 per 1M tokens


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

def fetch_full_html_content(url: str) -> str:
    """Fetch the complete HTML source code of a URL."""
    headers = {'User-Agent': CRAWLER_USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type:
            logger.warning(f"URL {url} returned non-HTML content type: {content_type}.")
        response.encoding = response.apparent_encoding
        return response.text
    except requests.exceptions.Timeout:
        raise TimeoutError(f"Request timed out for {url}")
    except requests.exceptions.RequestException as req_err:
        raise ConnectionError(f"Failed to fetch URL content: {req_err}")


def generate_xpath_for_element(element, soup):
    """Generate generic xpath queries that work across different users/profiles."""
    if not element or not element.name:
        return ""
    
    xpath_queries = []
    tag_name = element.name
    
    # 1. XPath by ID (only if generic/meaningful and stable)
    if element.get('id'):
        element_id = element.get('id')
        # Only use IDs that are stable (not dynamic/auto-generated)
        # Avoid: dynamic IDs, random strings, auto-generated patterns
        is_dynamic_id = (
            len(element_id) > 10 and any(char.isdigit() for char in element_id) or  # Long IDs with numbers
            '__' in element_id or  # Auto-generated patterns like id__abc123
            element_id.startswith('id_') or  # Generated ID patterns
            len([c for c in element_id if c.isdigit()]) > 3 or  # Too many numbers
            any(pattern in element_id.lower() for pattern in ['random', 'temp', 'gen', 'auto'])  # Generated indicators
        )
        
        # Only use stable, meaningful IDs
        stable_ids = ['react-root', 'app', 'main', 'header', 'footer', 'content', 'nav', 'menu']
        if (not is_dynamic_id and 
            (element_id in stable_ids or (len(element_id) < 8 and not any(char.isdigit() for char in element_id))) and
            not any(social_term in element_id.lower() for social_term in ['username', 'user_', 'profile_'])):
            xpath_queries.append(f"//{tag_name}[@id='{element_id}']")
    
    # 2. XPath by generic text patterns (avoid user-specific content)
    if element.get_text(strip=True):
        text = element.get_text(strip=True)
        # Generic action words only (no specific counts, usernames, etc.)
        action_words = ['follow', 'following', 'unfollow', 'like', 'share', 'comment', 'login', 'sign', 'submit', 'home', 
                       'profile', 'search', 'menu', 'save', 'edit', 'delete', 'add', 'create', 
                       'more', 'view', 'show', 'hide', 'close', 'open', 'next', 'previous', 
                       'back', 'forward', 'up', 'down', 'settings', 'options', 'message', 'send',
                       'posts', 'story', 'stories', 'reels', 'tagged']
        
        # Only use text if it's a generic action word, not user-specific data
        if (len(text) > 1 and len(text) < 30 and 
            not text.replace('.', '').replace('M', '').replace('K', '').replace(',', '').isdigit() and  # Skip follower counts
            not any(char.isdigit() for char in text) and  # Skip any text containing numbers (post counts, etc.)
            not '@' in text and  # Skip usernames
            not 'followers' in text.lower() and not 'following' in text.lower() and  # Skip follower counts
            not 'posts' in text.lower() and  # Skip post counts like "604posts"
            any(word in text.lower() for word in action_words)):
            # Escape single quotes in text
            escaped_text = text.replace("'", "\\'")
            xpath_queries.append(f"//{tag_name}[contains(text(), '{escaped_text}')]")
            # Also add exact text match
            xpath_queries.append(f"//{tag_name}[text()='{escaped_text}']")
    
    # 3. XPath by semantic attributes (high priority) - Filter out user-specific data
    semantic_attrs = {
        'role': ['button', 'link', 'menu', 'dialog', 'tab', 'tablist', 'navigation', 'main', 'banner', 'contentinfo'],
        'type': ['button', 'submit', 'text', 'password', 'email', 'search', 'file', 'checkbox', 'radio'],
        'aria-label': None,  # Any aria-label is valuable
        'data-testid': None,  # Test IDs are very valuable but need filtering
        'name': None,  # Form element names
        'placeholder': None,  # Input placeholders
        'alt': None,  # Image alt text
        'title': None  # Title attributes
    }
    
    for attr, valid_values in semantic_attrs.items():
        if element.get(attr):
            attr_value = element.get(attr)
            if valid_values is None or attr_value in valid_values:
                # Keep attribute values short and meaningful, but avoid user-specific content
                if len(attr_value) < 50:
                    # For alt text, create generic patterns instead of specific text
                    if attr == 'alt':
                        if 'profile picture' in attr_value.lower():
                            xpath_queries.append(f"//{tag_name}[contains(@alt, 'profile picture')]")
                        elif 'highlight' in attr_value.lower():
                            xpath_queries.append(f"//{tag_name}[contains(@alt, 'highlight')]")
                        elif 'story' in attr_value.lower():
                            xpath_queries.append(f"//{tag_name}[contains(@alt, 'story')]")
                        # Skip user-specific alt text with @usernames
                        elif not '@' in attr_value and not any(char.isdigit() for char in attr_value):
                            xpath_queries.append(f"//{tag_name}[@{attr}='{attr_value}']")
                    elif attr == 'data-testid':
                        # For data-testid, filter out user IDs and create generic patterns
                        if '-follow' in attr_value:
                            # Generic follow button pattern instead of user-specific
                            xpath_queries.append(f"//{tag_name}[contains(@data-testid, '-follow')]")
                        elif '-like' in attr_value:
                            xpath_queries.append(f"//{tag_name}[contains(@data-testid, '-like')]")
                        elif '-retweet' in attr_value:
                            xpath_queries.append(f"//{tag_name}[contains(@data-testid, '-retweet')]")
                        elif not any(char.isdigit() for char in attr_value):
                            # Only use data-testid if it doesn't contain user IDs (numbers)
                            xpath_queries.append(f"//{tag_name}[@data-testid='{attr_value}']")
                    elif attr == 'aria-label':
                        # For aria-label, create patterns that avoid specific numbers/usernames
                        if 'like' in attr_value.lower() and not any(char.isdigit() for char in attr_value):
                            xpath_queries.append(f"//{tag_name}[contains(@aria-label, 'Like')]")
                        elif 'follow' in attr_value.lower():
                            xpath_queries.append(f"//{tag_name}[contains(@aria-label, 'follow')]")
                        elif not '@' in attr_value and not any(char.isdigit() for char in attr_value):
                            xpath_queries.append(f"//{tag_name}[@aria-label='{attr_value}']")
                    else:
                        # For other attributes, use as-is if they don't contain user-specific data
                        if not '@' in attr_value and not any(user_term in attr_value.lower() for user_term in ['username', 'user_id', 'profile_']) and not any(char.isdigit() for char in attr_value):
                            xpath_queries.append(f"//{tag_name}[@{attr}='{attr_value}']")
    
    # 4. XPath by href patterns (for links) - Use generic patterns only
    if element.get('href'):
        href = element.get('href')
        if href and len(href) < 100:  # Avoid very long URLs
            # Check if URL contains user-specific paths
            has_user_path = any(user_indicator in href.lower() for user_indicator in ['/@', '/user/', '/profile/', '/followers/', '/following/'])
            # Also check for direct username patterns like '/username/'
            is_likely_username = href.count('/') >= 2 and not href.startswith('http') and len(href.split('/')[-2]) > 2
            
            if not has_user_path and not is_likely_username:
                xpath_queries.append(f"//{tag_name}[@href='{href}']")
            else:
                # For user-specific URLs, create generic patterns
                if '/followers/' in href:
                    xpath_queries.append(f"//{tag_name}[contains(@href, '/followers/')]")
                elif '/following/' in href:
                    xpath_queries.append(f"//{tag_name}[contains(@href, '/following/')]")
                elif href.startswith('/@'):
                    xpath_queries.append(f"//{tag_name}[starts-with(@href, '/@')]")
                elif '/profile/' in href:
                    xpath_queries.append(f"//{tag_name}[contains(@href, '/profile/')]")
                elif is_likely_username:
                    # For patterns like '/username/', create a more generic selector
                    xpath_queries.append(f"//{tag_name}[@href and string-length(@href) > 1]")
    
    # 5. XPath by src patterns (for images/media)
    if element.get('src'):
        src = element.get('src')
        if src and len(src) < 100:  # Avoid very long URLs
            xpath_queries.append(f"//{tag_name}[@src='{src}']")
    
    # 6. Only use meaningful class names as last resort
    if element.get('class') and len(xpath_queries) < 3:  # Only if we don't have enough good XPaths
        classes = element.get('class')
        # Look for semantic class patterns only
        semantic_class_patterns = ['btn', 'button', 'nav', 'menu', 'header', 'footer', 'main', 'content', 
                                 'post', 'story', 'like', 'share', 'comment', 'follow', 'profile', 'image', 
                                 'video', 'link', 'form', 'input', 'submit', 'search', 'login', 'signup',
                                 'modal', 'popup', 'dropdown', 'tab', 'accordion', 'carousel', 'slider']
        
        for cls in classes:
            # Only use classes that contain semantic patterns and are not auto-generated
            if (any(pattern in cls.lower() for pattern in semantic_class_patterns) and
                len(cls) > 3 and len(cls) < 25 and
                not cls.startswith('x') and  # Skip Facebook/React classes starting with 'x'
                not cls.startswith('_') and  # Skip auto-generated classes starting with '_'
                cls.count('x') < 2):  # Skip classes with multiple 'x' characters
                xpath_queries.append(f"//{tag_name}[contains(@class, '{cls}')]")
                break  # Only use the first meaningful class
    
    # 7. Fallback: Only use simple tag-based XPath if we have very few results
    if len(xpath_queries) < 2:
        # Add simple tag selector as fallback
        xpath_queries.append(f"//{tag_name}")
        
        # Add position-based xpath only as absolute last resort for unique elements
        if tag_name in ['form', 'main', 'header', 'footer', 'nav']:
            xpath_parts = []
            current = element
            depth = 0
            
            while current and current.name and depth < 5:  # Limit depth to avoid very long paths
                current_tag = current.name
                siblings = [s for s in current.parent.find_all(current_tag, recursive=False) if s.name == current_tag] if current.parent else [current]
                
                if len(siblings) > 1:
                    try:
                        index = siblings.index(current) + 1
                        xpath_parts.append(f"{current_tag}[{index}]")
                    except ValueError:
                        xpath_parts.append(current_tag)
                else:
                    xpath_parts.append(current_tag)
                
                current = current.parent
                depth += 1
                if current and current.name == '[document]':
                    break
            
            xpath_parts.reverse()
            if xpath_parts and len(xpath_parts) <= 5:  # Only use short paths
                xpath_queries.append("/" + "/".join(xpath_parts))
    
    # Remove duplicates while preserving order
    seen = set()
    unique_xpaths = []
    for xpath in xpath_queries:
        if xpath not in seen:
            seen.add(xpath)
            unique_xpaths.append(xpath)
    
    return unique_xpaths[:5]  # Return top 5 most useful xpaths

def extract_all_elements(html_content: str) -> dict:
    """Extract all elements and their xpath queries from any HTML content."""
    soup = BeautifulSoup(html_content, 'html.parser')
    elements_map = {}
    
    # Add debugging info
    total_tags = len(soup.find_all())
    logger.info(f"Found {total_tags} total HTML tags in the page")
    
    # Define element categories to search for
    element_categories = {
        # Interactive Elements
        'buttons': ['button', '[role="button"]', 'input[type="button"]', 'input[type="submit"]'],
        'links': ['a[href]'],
        'inputs': ['input', 'textarea', 'select'],
        'forms': ['form'],
        
        # Content Elements
        'images': ['img'],
        'videos': ['video'],
        'audio': ['audio'],
        'paragraphs': ['p'],
        'headings': ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'],
        'lists': ['ul', 'ol', 'li'],
        'tables': ['table', 'tbody', 'thead', 'tr', 'td', 'th'],
        
        # Layout Elements
        'divs': ['div'],
        'spans': ['span'],
        'sections': ['section'],
        'articles': ['article'],
        'headers': ['header'],
        'footers': ['footer'],
        'navigation': ['nav', '[role="navigation"]'],
        'main_content': ['main', '[role="main"]'],
        'sidebars': ['aside', '.sidebar'],
        
        # Common UI Elements
        'modal_dialogs': ['[role="dialog"]', '.modal', '.popup'],
        'dropdown_menus': ['[role="menu"]', '.dropdown', 'select'],
        'tabs': ['[role="tab"]', '[role="tablist"]', '.tab'],
        'accordions': ['[role="button"][aria-expanded]', '.accordion'],
        'tooltips': ['[role="tooltip"]', '.tooltip'],
        'alerts': ['[role="alert"]', '.alert', '.notification'],
        'progress_bars': ['[role="progressbar"]', 'progress', '.progress'],
        'search_boxes': ['input[type="search"]', '[placeholder*="search" i]', '[aria-label*="search" i]'],
        
        # Social Media Specific Elements
        'like_buttons': ['[aria-label*="like" i]', '[data-testid*="like"]', '.like-button', 'button[aria-label*="like" i]'],
        'share_buttons': ['[aria-label*="share" i]', '[data-testid*="share"]', '.share-button', 'button[aria-label*="share" i]'],
        'comment_buttons': ['[aria-label*="comment" i]', '[data-testid*="comment"]', '.comment-button', 'button[aria-label*="comment" i]'],
        'retweet_buttons': ['[aria-label*="retweet" i]', '[data-testid*="retweet"]', 'button[aria-label*="retweet" i]'],
        'follower_counts': ['[href*="/followers"]', 'a[href*="/followers"]', 'span:contains("followers")', 'div:contains("followers")'],
        'following_counts': ['[href*="/following"]', 'a[href*="/following"]', 'span:contains("following")', 'div:contains("following")'],
        'post_counts': ['span:contains("posts")', 'div:contains("posts")', 'span:contains("tweets")', 'div:contains("tweets")'],
        'tweet_content': ['[data-testid="tweetText"]', 'div[data-testid="tweetText"]'],
        'user_profiles': ['[data-testid="UserCell"]', 'div[data-testid="UserCell"]'],
        'follow_buttons': [
            '[aria-label*="follow" i]', '[data-testid*="follow"]', '.follow-button',
            'button[aria-label*="follow" i]', 'div[aria-label*="follow" i]',
            'button:contains("Follow")', 'button:contains("follow")', 'button:contains("Following")',
            'div[role="button"]:contains("Follow")', 'div[role="button"]:contains("follow")',
            '[data-testid*="Follow"]', '[aria-label*="Follow"]',
            'button[data-testid*="follow"]', 'div[data-testid*="follow"]',
            # Additional patterns for different languages and contexts
            '[aria-label*="seguir" i]', '[aria-label*="folgen" i]', '[aria-label*="suivre" i]',
            'button[title*="follow" i]', 'div[title*="follow" i]',
            # More generic patterns that might catch Instagram-style buttons
            'div[tabindex="0"]:contains("Follow")', 'div[tabindex="0"]:contains("follow")',
            'span:contains("Follow")', 'span:contains("follow")', 'span:contains("Following")',
            # Try to catch any text-based follow elements
            '*:contains("Follow")', '*:contains("follow")', '*:contains("Following")'
        ],
        'profile_links': ['[href*="/profile"]', '[href*="/user"]', '[href*="/@"]'],
        'hashtags': ['[href*="#"]', 'a[href*="/tag/"]', 'a[href*="/hashtag/"]'],
        
        # Form Elements
        'checkboxes': ['input[type="checkbox"]'],
        'radio_buttons': ['input[type="radio"]'],
        'file_uploads': ['input[type="file"]'],
        'sliders': ['input[type="range"]', '[role="slider"]'],
        
        # Media Elements
        'iframes': ['iframe'],
        'embeds': ['embed', 'object'],
        'canvas': ['canvas'],
        'svg': ['svg'],
        
        # Data Elements
        'timestamps': ['time', '[datetime]'],
        'prices': ['[class*="price"]', '[data-price]'],
        'ratings': ['[class*="rating"]', '[class*="star"]'],
        'badges': ['[class*="badge"]', '[class*="label"]'],
        
        # Interactive Widgets
        'carousels': ['[class*="carousel"]', '[class*="slider"]', '[role="region"][aria-live]'],
        'calendars': ['[role="grid"][aria-label*="calendar" i]', '.calendar'],
        'maps': ['[class*="map"]', 'iframe[src*="maps"]'],
        'charts': ['canvas[class*="chart"]', 'svg[class*="chart"]']
    }
    
    # Initialize set to track sophisticated patterns (prevent duplication)
    sophisticated_patterns_added = set()
    
    # Find elements and generate xpath queries
    for element_name, selectors in element_categories.items():
        xpath_list = []
        
        for selector in selectors:
            found_elements = []
            
            try:
                # Handle different selector types
                if selector.startswith('[') and ']' in selector:
                    # Complex attribute selector
                    if '=' in selector:
                        attr_part = selector[1:-1]
                        if '*=' in attr_part:
                            attr_name, attr_value = attr_part.split('*=', 1)
                            attr_value = attr_value.strip('"\'')
                            # Handle case-insensitive search
                            if attr_value.endswith(' i'):
                                attr_value = attr_value[:-2]
                                found_elements = soup.find_all(attrs={attr_name: lambda x: x and attr_value.lower() in x.lower()})
                            else:
                                found_elements = soup.find_all(attrs={attr_name: lambda x: x and attr_value in x})
                        elif '=' in attr_part:
                            attr_name, attr_value = attr_part.split('=', 1)
                            attr_value = attr_value.strip('"\'')
                            found_elements = soup.find_all(attrs={attr_name: attr_value})
                    else:
                        # Just attribute presence
                        attr_name = selector[1:-1]
                        found_elements = soup.find_all(attrs={attr_name: True})
                elif selector.startswith('.'):
                    # Class selector
                    class_name = selector[1:]
                    found_elements = soup.find_all(class_=lambda x: x and class_name in x if x else False)
                elif selector.startswith('#'):
                    # ID selector
                    id_name = selector[1:]
                    found_elements = soup.find_all(id=id_name)
                elif ':contains(' in selector:
                    # Handle :contains() pseudo-selector
                    parts = selector.split(':contains(')
                    if len(parts) == 2:
                        tag_part = parts[0]
                        text_part = parts[1].rstrip(')')
                        text_to_find = text_part.strip('"\'')
                        
                        # Find elements by tag and text content
                        if tag_part == '*':
                            # Wildcard selector - search all elements
                            found_elements = soup.find_all(string=lambda text: text and text_to_find.lower() in text.lower())
                            # Get the parent elements that contain this text
                            found_elements = [text.parent for text in found_elements if text.parent and text.parent.name]
                        elif '[' in tag_part and ']' in tag_part:
                            # Handle tag with attributes like 'div[role="button"]'
                            base_tag = tag_part.split('[')[0]
                            attr_part = tag_part[tag_part.find('[')+1:tag_part.find(']')]
                            if '=' in attr_part:
                                attr_name, attr_value = attr_part.split('=', 1)
                                attr_value = attr_value.strip('"\'')
                                found_elements = soup.find_all(base_tag, attrs={attr_name: attr_value})
                                # Filter by text content
                                found_elements = [el for el in found_elements if text_to_find.lower() in el.get_text().lower()]
                            else:
                                found_elements = soup.find_all(base_tag, attrs={attr_part: True})
                                found_elements = [el for el in found_elements if text_to_find.lower() in el.get_text().lower()]
                        else:
                            # Simple tag selector with text
                            found_elements = soup.find_all(tag_part)
                            found_elements = [el for el in found_elements if text_to_find.lower() in el.get_text().lower()]
                else:
                    # Tag selector (or complex selector)
                    if '[' in selector and ']' in selector:
                        # Handle complex tag selectors like 'button[aria-label*="like" i]'
                        base_tag = selector.split('[')[0]
                        attr_part = selector[selector.find('[')+1:selector.find(']')]
                        
                        if '*=' in attr_part:
                            attr_name, attr_value = attr_part.split('*=', 1)
                            attr_value = attr_value.strip('"\'')
                            # Check for case-insensitive flag
                            case_insensitive = attr_part.endswith(' i')
                            if case_insensitive:
                                attr_value = attr_value.replace(' i', '')
                                found_elements = soup.find_all(base_tag, attrs={attr_name: lambda x: x and attr_value.lower() in x.lower()})
                            else:
                                found_elements = soup.find_all(base_tag, attrs={attr_name: lambda x: x and attr_value in x})
                        elif '=' in attr_part:
                            attr_name, attr_value = attr_part.split('=', 1)
                            attr_value = attr_value.strip('"\'')
                            found_elements = soup.find_all(base_tag, attrs={attr_name: attr_value})
                        else:
                            # Just attribute presence
                            found_elements = soup.find_all(base_tag, attrs={attr_part: True})
                    else:
                        # Simple tag selector
                        found_elements = soup.find_all(selector)
                
                # Generate xpath for found elements (limit to avoid too many results)
                for element in found_elements[:3]:  # Reduced to 3 to avoid duplication
                    xpaths = generate_xpath_for_element(element, soup)
                    for xpath in xpaths:
                        if xpath and xpath not in xpath_list:
                            xpath_list.append(xpath)
                
                # Add sophisticated patterns for specific element types (always add if element found)
                if element_name == 'follower_counts' and 'follower_counts' not in sophisticated_patterns_added:
                    xpath_list.append("//a[contains(@href, '/followers') and string-length(normalize-space(text())) > 0 and not(contains(text(), '@')) and not(@aria-hidden='true') and normalize-space(text()) != '·']")
                    sophisticated_patterns_added.add('follower_counts')
                    
                if element_name == 'following_counts' and 'following_counts' not in sophisticated_patterns_added:
                    xpath_list.append("//a[contains(@href, '/following') and string-length(normalize-space(text())) > 0 and not(contains(text(), '@')) and not(@aria-hidden='true') and normalize-space(text()) != '·']")
                    sophisticated_patterns_added.add('following_counts')
                    
                if element_name == 'tweet_content' and 'tweet_content' not in sophisticated_patterns_added:
                    xpath_list.append("//div[@data-testid='tweetText' and string-length(normalize-space(text())) > 0 and not(contains(text(), '@')) and not(contains(text(), 'Ad')) and not(@aria-hidden='true') and normalize-space(text()) != '·']")
                    xpath_list.append(".//span[not(ancestor::*[@data-testid='app-text-transition-container']) and not(ancestor::*[@data-testid='tweetText']) and string-length(normalize-space(text())) > 0 and not(contains(text(), '@')) and not(contains(text(), 'Ad')) and not(@aria-hidden='true') and normalize-space(text()) != '·']")
                    sophisticated_patterns_added.add('tweet_content')
                    
                if element_name == 'follow_buttons' and 'follow_buttons' not in sophisticated_patterns_added:
                    xpath_list.append("//button[contains(@data-testid, '-follow') and not(@aria-hidden='true') and not(@disabled)]")
                    xpath_list.append("//button[contains(text(), 'Follow') and not(contains(text(), 'Following')) and string-length(normalize-space(text())) > 0 and not(@aria-hidden='true')]") 
                    sophisticated_patterns_added.add('follow_buttons')
                    
                if element_name == 'like_buttons' and 'like_buttons' not in sophisticated_patterns_added:
                    xpath_list.append("//button[contains(@data-testid, 'like') and not(@aria-hidden='true') and not(@disabled)]")
                    xpath_list.append("//button[contains(@aria-label, 'Like') and not(contains(@aria-label, 'Unlike')) and not(@aria-hidden='true')]")
                    sophisticated_patterns_added.add('like_buttons')
                    
                if element_name == 'retweet_buttons' and 'retweet_buttons' not in sophisticated_patterns_added:
                    xpath_list.append("//button[contains(@data-testid, 'retweet') and not(@aria-hidden='true') and not(@disabled)]")
                    xpath_list.append("//button[contains(@aria-label, 'Retweet') and not(@aria-hidden='true')]")
                    sophisticated_patterns_added.add('retweet_buttons')
                    
                if element_name == 'user_profiles' and 'user_profiles' not in sophisticated_patterns_added:
                    xpath_list.append("//div[@data-testid='UserCell' and string-length(normalize-space(text())) > 0 and not(@aria-hidden='true')]")
                    sophisticated_patterns_added.add('user_profiles')
                    
                if element_name == 'post_counts' and 'post_counts' not in sophisticated_patterns_added:
                    xpath_list.append("//span[contains(text(), 'posts') and string-length(normalize-space(text())) > 0 and not(contains(text(), '@')) and not(@aria-hidden='true')]")
                    xpath_list.append("//span[contains(text(), 'tweets') and string-length(normalize-space(text())) > 0 and not(contains(text(), '@')) and not(@aria-hidden='true')]")
                    sophisticated_patterns_added.add('post_counts')
                        
            except Exception as e:
                logger.debug(f"Error processing selector '{selector}': {e}")
                continue
        
        if xpath_list:
            # Remove duplicates while preserving order and filter out only obvious user-specific patterns
            seen = set()
            filtered_xpath_list = []
            for xpath in xpath_list:
                # Filter out user-specific patterns AND dynamic IDs
                is_user_specific = any(username_indicator in xpath for username_indicator in 
                                     ['mohamad', 'xpression_app', 'old.time.hawkey', 'profile_images/', 'profile_banners/'])
                
                # Filter out dynamic/unstable IDs (like client mentioned)
                is_dynamic_xpath = any(dynamic_pattern in xpath for dynamic_pattern in [
                    'id__', '@id=\'id_', 'random', 'temp', 'gen_', 'auto_'
                ]) or (
                    '@id=' in xpath and 
                    any(char.isdigit() for char in xpath) and 
                    len([c for c in xpath if c.isdigit()]) > 4  # More than 4 digits suggests dynamic ID
                )
                
                if xpath not in seen and not is_user_specific and not is_dynamic_xpath:
                    seen.add(xpath)
                    filtered_xpath_list.append(xpath)
            
            # Limit to reasonable number of XPath patterns per element type
            if len(filtered_xpath_list) > 10:
                filtered_xpath_list = filtered_xpath_list[:10]
                
            elements_map[element_name] = filtered_xpath_list
            logger.info(f"Found {len(filtered_xpath_list)} {element_name} elements")
    
    logger.info(f"Total element types found: {len(elements_map)}")
    return elements_map

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


# --- NEW: HTML Analysis Route ---
@app.route('/api/analyze-html', methods=['POST'])
@require_api_key
def analyze_html():
    """
    Analyze provided HTML content and return element xpath mappings.
    
    Expected JSON payload:
    {
        "html_content": "<html>...</html>"
    }
    """
    if not request.is_json:
        return jsonify({"error": "Bad Request", "message": "Request body must be JSON"}), 400
    
    data = request.get_json()
    html_content = data.get('html_content')
    
    if not html_content:
        return jsonify({"error": "Bad Request", "message": "'html_content' is required"}), 400
    
    if not isinstance(html_content, str):
        return jsonify({"error": "Bad Request", "message": "'html_content' must be a string"}), 400
    
    if len(html_content.strip()) < 10:
        return jsonify({"error": "Bad Request", "message": "HTML content appears to be too short or empty"}), 400
    
    try:
        logger.info("Starting HTML analysis")
        logger.info(f"HTML content length: {len(html_content)} characters")
        
        elements_map = extract_all_elements(html_content)
        
        logger.info("Successfully analyzed HTML content")
        return jsonify({
            "status": "success",
            "elements": elements_map,
            "total_elements": len(elements_map),
            "html_length": len(html_content),
            "debug_info": f"Analyzed {len(html_content)} characters of HTML content"
        }), 200
        
    except Exception as e:
        logger.error(f"Unexpected error analyzing HTML content: {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred during HTML analysis"}), 500


@app.route('/api/analyze-html-file', methods=['POST'])
@require_api_key
def analyze_html_file():
    """
    Analyze HTML content from uploaded file and return element xpath mappings.
    
    Expected: HTML file upload using multipart/form-data
    """
    api_key = request.headers.get('api-key')
    if not api_key or api_key != EXPECTED_SERVICE_API_KEY:
        return jsonify({"error": "Unauthorized", "message": "Invalid or missing API key"}), 401
    
    if 'html_file' not in request.files:
        return jsonify({"error": "Bad Request", "message": "No 'html_file' uploaded"}), 400
    
    file = request.files['html_file']
    if file.filename == '':
        return jsonify({"error": "Bad Request", "message": "No file selected"}), 400
    
    try:
        # Read file content
        html_content = file.read().decode('utf-8')
        
        if len(html_content.strip()) < 10:
            return jsonify({"error": "Bad Request", "message": "HTML file appears to be too short or empty"}), 400
        
        logger.info(f"Starting HTML file analysis: {file.filename}")
        logger.info(f"HTML content length: {len(html_content)} characters")
        
        elements_map = extract_all_elements(html_content)
        
        logger.info(f"Successfully analyzed HTML file: {file.filename}")
        return jsonify({
            "status": "success",
            "filename": file.filename,
            "elements": elements_map,
            "total_elements": len(elements_map),
            "html_length": len(html_content),
            "debug_info": f"Analyzed {len(html_content)} characters from file '{file.filename}'"
        }), 200
        
    except UnicodeDecodeError:
        return jsonify({"error": "Bad Request", "message": "File must be valid UTF-8 encoded HTML"}), 400
    except Exception as e:
        logger.error(f"Unexpected error analyzing HTML file: {e}", exc_info=True)
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred during HTML file analysis"}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    health_status = {"status": "ok", "message": "API is running"}
    # ... (health check logic remains the same) ...
    return jsonify(health_status), 200

@app.route('/api/routes', methods=['GET'])
def list_routes():
    """List all available routes for debugging."""
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            "endpoint": rule.endpoint,
            "methods": list(rule.methods),
            "url": str(rule)
        })
    return jsonify({"routes": routes}), 200

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