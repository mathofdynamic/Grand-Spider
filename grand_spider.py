# grand_spider.py (Updated to wait for specific social link)

from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait # For explicit waits
from selenium.webdriver.support import expected_conditions as EC # For explicit waits
from selenium.webdriver.common.by import By # For explicit waits selectors
from bs4 import BeautifulSoup
from urllib.parse import urlparse, unquote
import time # Keep time for potential future use, though not for main wait now
import os
import re
from dotenv import load_dotenv
import traceback # For detailed error logging

# --- Load environment variables from .env file ---
load_dotenv()

# --- Configuration ---
chrome_options = Options()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
# chrome_options.add_argument("--window-size=1920,1080") # Consider uncommenting if issues persist
chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")

# Define social media domains
SOCIAL_MEDIA_DOMAINS = {
  'twitter.com', 'x.com', 'facebook.com', 'fb.com', 'instagram.com', 'linkedin.com',
  'youtube.com', 'youtu.be', 'pinterest.com', 'tiktok.com', 'snapchat.com', 'reddit.com',
  'tumblr.com', 'whatsapp.com', 'wa.me', 't.me', 'telegram.me', 'discord.gg',
  'discord.com', 'medium.com', 'github.com', 'threads.net', 'mastodon.social',
}

# --- Regex Patterns ---
EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
PHONE_REGEX = r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4,}"

# --- Flask App Setup ---
app = Flask(__name__)

# --- API Endpoint ---
@app.route('/extract-info', methods=['POST'])
def extract_info():
    # --- Authentication ---
    api_key = request.headers.get('api-key')
    expected_key = os.environ.get("MY_API_SECRET")
    if not expected_key:
        print("ERROR: MY_API_SECRET environment variable not found.")
        return jsonify({"error": "Server configuration error"}), 500
    if not api_key or api_key != expected_key:
        print(f"Unauthorized attempt.")
        return jsonify({"error": "Unauthorized"}), 401

    # --- Get URL from Request Body ---
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Missing 'url' in request body"}), 400
    target_url = data['url']
    print(f"\n--- New Request ---")
    print(f"Target URL: {target_url}")

    # --- Scrape using Selenium ---
    driver = None
    social_links_found = set()
    emails_found = set()
    phones_found = set()

    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(45) # Timeout for initial page load request

        print(f"Attempting to load URL: {target_url}")
        driver.get(target_url)
        print(f"Initial page load initiated. Waiting for specific content (max 20s)...")

        # --- Explicit Wait ---
        # Wait for a specific element that likely appears after JS loading,
        # such as one of the social media links.
        wait_timeout = 20 # seconds
        # *** UPDATED SELECTOR: Waiting for an anchor tag with href containing 'twitter.com' ***
        # You could change 'twitter.com' to 'linkedin.com' or another expected social domain
        # if that proves more reliable for the sites you target.
        wait_selector = (By.CSS_SELECTOR, "a[href*='twitter.com']")
        try:
            wait = WebDriverWait(driver, wait_timeout)
            print(f"Waiting up to {wait_timeout}s for element matching CSS selector: \"{wait_selector[1]}\"")
            wait.until(EC.presence_of_element_located(wait_selector))
            print(f"Element matching selector found. Proceeding to get source.")
        except TimeoutException:
            # This means the specific social link wasn't found within the wait_timeout
            print(f"WARNING: Timed out after {wait_timeout}s waiting for element matching \"{wait_selector[1]}\".")
            print("         Page might be incomplete, blocked, or the element selector needs adjustment.")
            # Continue anyway, maybe other data (email/phone) is present or loaded earlier.

        # --- Get Page Source ---
        page_source = driver.page_source
        source_length = len(page_source) if page_source else 0
        print(f"Got page source, length: {source_length}")

        # --- DEBUG: Save HTML Source ---
        try:
            debug_filename = "debug_page_source.html"
            with open(debug_filename, "w", encoding="utf-8") as f:
                f.write(page_source if page_source else "<html><body>Error: Page source was empty or None</body></html>")
            print(f"Saved page source for debugging to: {debug_filename}")
        except Exception as e:
            print(f"Error saving debug HTML file: {e}")
        # --- End Debug ---

        if not page_source:
             print("WARNING: Page source is empty after wait. Skipping parsing.")
             # Optionally return a specific status or warning in JSON
             return jsonify({
                 "social_links": [],
                 "emails": [],
                 "phone_numbers": [],
                 "status": "Warning: Could not retrieve page source or source was empty."
             }), 200 # Return 200 OK but indicate potential issue

        # --- Parse with Beautiful Soup ---
        soup = BeautifulSoup(page_source, 'html.parser')

        # 1. Extract from Links (Social, Mailto, Tel)
        links = soup.find_all('a', href=True)
        print(f"Found {len(links)} total anchor tags for parsing.")
        for link in links:
            href = link.get('href')
            if not href or not isinstance(href, str):
                continue
            href = href.strip()
            if not href:
                continue

            # Check for mailto links
            if href.startswith('mailto:'):
                try:
                    email_part = href.split('mailto:', 1)[1].split('?')[0]
                    if email_part:
                        potential_email = unquote(email_part)
                        if re.fullmatch(EMAIL_REGEX, potential_email):
                           emails_found.add(potential_email)
                except Exception as e:
                    print(f"Error processing mailto link '{href}': {e}")
                continue # Move to next link

            # Check for tel links
            if href.startswith('tel:'):
                try:
                    phone_part = href.split('tel:', 1)[1]
                    cleaned_phone = phone_part.strip()
                    if cleaned_phone:
                       phones_found.add(cleaned_phone)
                except Exception as e:
                    print(f"Error processing tel link '{href}': {e}")
                continue # Move to next link

            # Check for social media links (if not mailto/tel)
            try:
                # Skip non-http links for social media domain check
                if not href.startswith(('http://', 'https://')):
                    continue

                url_obj = urlparse(href)
                # Normalize hostname: remove 'www.' and convert to lowercase
                hostname = url_obj.netloc.lower().replace('www.', '')
                if not hostname:
                    continue

                for social_domain in SOCIAL_MEDIA_DOMAINS:
                    # Check if the hostname exactly matches or ends with .social_domain (for subdomains like blog.twitter.com)
                    if hostname == social_domain or hostname.endswith('.' + social_domain):
                        social_links_found.add(href)
                        # print(f"Found social link: {href}")
                        break # Found a match for this link's domain, check next link tag
            except Exception as parse_err:
                # print(f"Error parsing potential social link '{href}': {parse_err}")
                pass # Ignore errors for individual link parsing

        # 2. Extract from Page Text using Regex (Emails and Phones)
        page_text = ""
        if soup.body:
            page_text = soup.body.get_text(separator=' ', strip=True)
            # print(f"\nPage Text for Regex (first 500 chars):\n{page_text[:500]}\n---")

            # Find emails in text
            try:
                found_emails_in_text = re.findall(EMAIL_REGEX, page_text)
                if found_emails_in_text:
                    emails_found.update(found_emails_in_text)
            except Exception as regex_email_err:
                 print(f"Error running EMAIL_REGEX: {regex_email_err}")

            # Find phone numbers in text
            try:
                found_phones_in_text = re.findall(PHONE_REGEX, page_text)
                if found_phones_in_text:
                    processed_phones = []
                    for p in found_phones_in_text:
                        phone_str = "".join(filter(None, p)) if isinstance(p, tuple) else p
                        if phone_str:
                             processed_phones.append(phone_str.strip())
                    phones_found.update(processed_phones)
            except Exception as regex_phone_err:
                print(f"Error running PHONE_REGEX: {regex_phone_err}")
        else:
            print("WARNING: Could not find body tag in HTML source for text extraction.")


        print(f"Extraction complete. Found: {len(social_links_found)} social, {len(emails_found)} emails, {len(phones_found)} phones.")
        return jsonify({
            "social_links": sorted(list(social_links_found)),
            "emails": sorted(list(emails_found)),
            "phone_numbers": sorted(list(phones_found))
        }), 200

    except TimeoutException as e: # Catches the driver.get() timeout OR the explicit wait timeout if not caught inside
        print(f"Timeout error encountered for URL: {target_url}")
        # Differentiate between initial load and element wait timeouts if needed based on context or specific exception args
        print(f"Error details: {e}")
        # If it was the initial load timeout: 504 Gateway Timeout
        # If it was the explicit wait timeout (and not handled inside): Maybe 500 or a custom error
        return jsonify({"error": f"Timeout processing URL: {target_url}. Check logs for details."}), 504 # Or 500
    except WebDriverException as e:
        print(f"WebDriver error processing {target_url}: {e}")
        return jsonify({"error": f"Browser automation error. Check server logs."}), 500
    except Exception as e:
        print(f"Unexpected error in /extract-info for URL {target_url}:")
        traceback.print_exc() # Print full traceback to console/log
        return jsonify({"error": "An internal server error occurred."}), 500
    finally:
        if driver:
            print("Closing WebDriver.")
            driver.quit()

# --- Run the Flask App ---
if __name__ == '__main__':
    # Set debug=False when deploying to production
    app.run(host='0.0.0.0', port=5000, debug=True)