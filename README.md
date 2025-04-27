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


```

# Self-Hosted Firecrawl Setup

This project demonstrates how to use Firecrawl's self-hosted version for web scraping and crawling.

## Prerequisites

- [Docker](https://www.docker.com/products/docker-desktop/) installed on your system
- [Python](https://www.python.org/downloads/) 3.8 or higher
- Required Python packages: `requests`, `pandas`, `asyncio`

## Setup

1. **Install Dependencies**

```bash
pip install requests pandas asyncio
```

2. **Start Firecrawl Services**

Run the batch script to start all required Docker containers:

```bash
start_firecrawl.bat
```

This will start three Docker containers:
- `firecrawl` (main API service)
- `playwright-service` (web browser automation service)
- `redis` (queue and caching service)

The Firecrawl API will be available at: http://localhost:3002

3. **Test the API Connection**

Run the test script to verify that the API is working:

```bash
python test_firecrawl_api.py
```

This will make a simple request to scrape example.com and display the results.

## Usage

### Web Crawling

To crawl a website and map its structure:

```bash
python test_pages.py
```

This will:
1. Submit a crawl job to the local Firecrawl API
2. Wait for the job to complete
3. Process the results
4. Save the data to a CSV file

### Customization

You can modify `test_pages.py` to:
- Change the target URL
- Adjust the maximum number of pages to crawl
- Change the output formats
- Process the data differently

## Troubleshooting

- If you encounter connection errors, make sure the Docker containers are running
- Check the Docker logs for any errors:
  ```bash
  docker logs firecrawl_firecrawl_1
  ```
- Ensure ports 3000, 3002, and 6379 are not being used by other applications

## References

- [Firecrawl GitHub Repository](https://github.com/mendableai/firecrawl)
- [Firecrawl Documentation](https://docs.firecrawl.dev/)

# Web Crawler API

This API provides functionality to crawl websites and extract information from web pages. It supports both simple requests-based crawling and Selenium-based crawling for JavaScript-heavy sites.

## Setup

The API server is already running on `http://localhost:5000`.

## API Status

✅ **CONFIRMED WORKING**: The API is running and has been tested successfully.

## Testing with Postman

1. Open Postman
2. Import the collection file `web_crawler_api.postman_collection.json` included in this repository
3. Use the included requests to test the API

### Quick Postman Setup

1. Create a new request in Postman
2. Set the request URL to `http://localhost:5000/api/health` and method to `GET`
3. Send the request to verify the API is running
4. Create another request with URL `http://localhost:5000/api/crawl` and method `POST`
5. Add headers:
   - Key: `api-key`, Value: `this_is_very_stupid_key_for_this_api`
   - Key: `Content-Type`, Value: `application/json`
6. Add body (raw JSON):
   ```json
   {
     "url": "https://example.com",
     "max_pages": 10,
     "use_selenium": true
   }
   ```
7. Send the request to start a crawl job
8. Copy the `job_id` from the response
9. Create a GET request to `http://localhost:5000/api/crawl/{job_id}` (replace `{job_id}` with the actual ID)
10. Add the `api-key` header and send the request to check the job status

## API Endpoints

### Health Check

Check if the API is running.

- **URL**: `/api/health`
- **Method**: `GET`
- **Authentication**: Not required
- **Response Example**:
  ```json
  {
    "status": "ok",
    "message": "Web Crawler API is running"
  }
  ```

### Start a Crawl Job

Start a new web crawling job.

- **URL**: `/api/crawl`
- **Method**: `POST`
- **Headers**: 
  - `api-key`: `this_is_very_stupid_key_for_this_api`
  - `Content-Type`: `application/json`
- **Request Body**:
  ```json
  {
    "url": "https://example.com",
    "max_pages": 10,
    "use_selenium": true
  }
  ```
- **Response Example**:
  ```json
  {
    "job_id": "1234-5678-90ab-cdef",
    "status": "running",
    "message": "Crawl job started successfully"
  }
  ```

### Get Crawl Job Status

Check the status of a crawl job and get results if complete.

- **URL**: `/api/crawl/{job_id}`
- **Method**: `GET`
- **Headers**: 
  - `api-key`: `this_is_very_stupid_key_for_this_api`
- **Response Example (Completed)**:
  ```json
  {
    "job_id": "c0ec11c2-c4dd-491b-9ab6-63485a81cbbf",
    "url": "example.com",
    "status": "completed",
    "total_pages": 1,
    "results": [
      {
        "url": "https://example.com",
        "title": "Example Domain",
        "description": "",
        "status_code": 200
      }
    ]
  }
  ```

### List All Crawl Jobs

Get a list of all crawl jobs.

- **URL**: `/api/crawl`
- **Method**: `GET`
- **Headers**: 
  - `api-key`: `this_is_very_stupid_key_for_this_api`
- **Response Example**:
  ```json
  {
    "jobs": [
      {
        "job_id": "c0ec11c2-c4dd-491b-9ab6-63485a81cbbf",
        "url": "example.com",
        "status": "completed",
        "created_at": 1745748433.2292097
      }
    ],
    "total": 1
  }
  ```

## Common Issues

- Make sure to use the correct endpoints:
  - To check job status: `/api/crawl/{job_id}` (not `/api/job_status/{job_id}`)
- All endpoints except `/api/health` require the `api-key` header
- Ensure URLs include the protocol (https:// or http://)

