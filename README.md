# Contact Extractor API

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
<!-- Add other badges here if you set them up (e.g., Build Status, Code Coverage) -->

A Python Flask API designed to extract publicly available contact information (emails, phone numbers) and social media links from web pages using Selenium and BeautifulSoup.

## Overview

This project provides a simple REST API endpoint that accepts a target URL. It then uses Selenium to control a headless Chrome browser, loading the specified page and allowing time for JavaScript rendering. The resulting HTML content is parsed using BeautifulSoup and regular expressions to find and extract relevant contact details. The findings are returned as a structured JSON response.

This tool is useful for automating the initial phase of data gathering for outreach, market research, or lead generation, focusing only on publicly accessible information presented on the website itself.

## Features

*   **Web Scraping:** Navigates to a given URL using Selenium WebDriver.
*   **JavaScript Rendering:** Waits for a period (using explicit waits) to allow dynamic content loaded by JavaScript to appear before parsing.
*   **HTML Parsing:** Uses BeautifulSoup to navigate the DOM structure.
*   **Email Extraction:**
    *   Finds `mailto:` links.
    *   Uses regular expressions to find email patterns in the page text.
*   **Phone Number Extraction:**
    *   Finds `tel:` links.
    *   Uses regular expressions to find common phone number patterns in the page text.
*   **Social Media Link Extraction:** Identifies links pointing to major social media domains (Twitter, LinkedIn, Facebook, Instagram, etc.).
*   **API Interface:** Simple Flask endpoint (`/extract-info`) accepting `POST` requests.
*   **Authentication:** Basic API key authentication via request headers.
*   **JSON Output:** Returns extracted data in a clean JSON format.
*   **Error Handling:** Provides basic error responses for common issues (timeouts, invalid requests, server errors).
*   **Debugging Support:** Saves the fetched HTML source to `debug_page_source.html` for inspection when troubleshooting.

## Technology Stack

*   **Python 3.x**
*   **Flask:** Micro web framework for the API.
*   **Selenium:** Browser automation tool for loading and rendering pages.
*   **BeautifulSoup4 (bs4):** Library for parsing HTML and XML documents.
*   **python-dotenv:** For managing environment variables (like API keys).
*   **ChromeDriver:** WebDriver executable required by Selenium to control Chrome. (Ensure this is installed and matches your Chrome version, or use `webdriver-manager`).
*   **Google Chrome / Chromium:** The browser being automated.

## Installation & Setup

1.  **Clone the Repository:**
    ```bash
    git clone <your-repository-url>
    cd contact_extractor
    ```

2.  **Create a Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    # On Windows:
    venv\Scripts\activate
    # On macOS/Linux:
    source venv/bin/activate
    ```

3.  **Install Dependencies:**
    *   First, ensure you have Google Chrome (or Chromium) installed.
    *   Create a `requirements.txt` file with the following content:
        ```txt
        Flask>=2.0
        selenium>=4.0
        beautifulsoup4>=4.9
        python-dotenv>=0.19
        # Optional, but helpful:
        # webdriver-manager>=3.5
        ```
    *   Install the packages:
        ```bash
        pip install -r requirements.txt
        ```

4.  **Install ChromeDriver:**
    *   Download the ChromeDriver executable that **matches your installed Google Chrome version** from the official site: [https://chromedriver.chromium.org/downloads](https://chromedriver.chromium.org/downloads)
    *   Place the `chromedriver` executable either in your system's PATH or in the project directory.
    *   *(Alternatively, if you installed `webdriver-manager`, you can modify the script to use `webdriver_manager.chrome.ChromeDriverManager().install()` instead of relying on a pre-downloaded driver - see comments in the Python code).*

## Configuration

1.  **Create Environment File:** Create a file named `.env` in the root project directory.
2.  **Set API Key:** Add your desired secret API key to the `.env` file:
    ```dotenv
    MY_API_SECRET=your_super_secret_and_unguessable_api_key
    ```
    *   **Important:** Keep this key secure and do not commit the `.env` file to version control (ensure `.env` is listed in your `.gitignore` file).

## API Usage

### Endpoint: `/extract-info`

*   **Method:** `POST`
*   **Headers:**
    *   `Content-Type: application/json`
    *   `api-key: your_super_secret_and_unguessable_api_key` (Replace with the key from your `.env` file)
*   **Body (Raw JSON):**
    ```json
    {
      "url": "https://example.com"
    }
    ```

### Example Request (using `curl`):

```bash
curl -X POST http://127.0.0.1:5000/extract-info \
     -H "Content-Type: application/json" \
     -H "api-key: your_super_secret_and_unguessable_api_key" \
     -d '{"url": "https://droplinked.com/"}'

