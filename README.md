# Grand Spider

A Flask-based API that uses OpenAI's GPT models to analyze and describe web pages based on their HTML content.

## Overview

Grand Spider is a web service that fetches HTML content from a specified URL and uses OpenAI's language models to generate a concise description of what's on the page. This is useful for content categorization, web indexing, or quickly understanding a webpage's purpose without having to visit it.

## Features

- **URL Analysis**: Submit any public URL and receive an AI-generated description of its content
- **OpenAI Integration**: Leverages GPT-4.1-nano for intelligent content analysis
- **API Authentication**: Secure endpoints with API key authentication
- **Error Handling**: Comprehensive error handling for various failure scenarios
- **Logging**: Detailed logging for monitoring and debugging

## Technology Stack

- **Python 3.x**
- **Flask**: Web framework for the API
- **OpenAI API**: For AI-powered content analysis
- **Requests**: For fetching web content
- **python-dotenv**: For secure environment variable management

## Installation & Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/yourusername/grand_spider.git
   cd grand_spider
   ```

2. **Create a Virtual Environment**:
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install flask openai requests python-dotenv
   ```

4. **Create Environment Variables**:
   Create a `.env` file in the project root with:
   ```
   SERVICE_API_KEY=your_chosen_api_key_here
   OPENAI_API_KEY=your_openai_api_key_here
   ```

## API Usage

### Analyze a Webpage

**Endpoint**: `/analyze`

**Method**: `POST`

**Headers**:
- `Content-Type: application/json`
- `api-key: your_chosen_api_key_here`

**Request Body**:
```json
{
  "url": "https://example.com"
}
```

**Example Request (curl)**:
```bash
curl -X POST "http://localhost:5000/analyze" \
  -H "Content-Type: application/json" \
  -H "api-key: your_chosen_api_key_here" \
  -d '{"url": "https://example.com"}'
```

**Success Response**:
```json
{
  "url": "https://example.com",
  "description": "This webpage is a simple example domain used for illustrative purposes in documentation. It provides a brief explanation that example domains like this one are reserved for use in examples and documentation as per RFC 2606.",
  "model_used": "gpt-4.1-nano-2025-04-14"
}
```

### Health Check

**Endpoint**: `/health`

**Method**: `GET`

**Response**:
```json
{
  "status": "ok",
  "components": {
    "service_api_key": "configured",
    "openai_api_key": "configured",
    "openai_client": "initialized"
  }
}
```

## Error Handling

The API returns appropriate HTTP status codes and descriptive error messages:

- **400**: Bad Request (invalid URL, missing parameters)
- **401**: Unauthorized (invalid or missing API key)
- **500**: Internal Server Error (unexpected issues)
- **502**: Bad Gateway (issues fetching URL content)
- **503**: Service Unavailable (OpenAI API issues)
- **504**: Gateway Timeout (URL fetch timeout)

## Deployment

For production deployment:

1. **Set Environment Variables**:
   Ensure `SERVICE_API_KEY` and `OPENAI_API_KEY` are set in your production environment.

2. **Run with a Production WSGI Server**:
   ```bash
   pip install gunicorn  # on Linux/macOS
   gunicorn -w 4 -b 0.0.0.0:5000 grand_spider:app
   ```
   
   For Windows, consider using waitress:
   ```bash
   pip install waitress
   waitress-serve --host=0.0.0.0 --port=5000 grand_spider:app
   ```

## Configuration Options

The following environment variables can be configured:

- `SERVICE_API_KEY`: Required for authentication
- `OPENAI_API_KEY`: Required for OpenAI API access
- `OPENAI_MODEL`: The OpenAI model to use (default: "gpt-4.1-nano-2025-04-14")
- `REQUEST_TIMEOUT`: Timeout for URL fetching in seconds (default: 15)
- `MAX_CONTENT_LENGTH`: Maximum HTML content length to analyze (default: 15000)
- `MAX_RESPONSE_TOKENS`: Maximum tokens in the OpenAI response (default: 300)

## License

[MIT License](https://opensource.org/licenses/MIT)

## Disclaimer

This tool is designed for analyzing publicly accessible web content. Always respect robots.txt, website terms of service, and avoid excessive requests to the same domain.

