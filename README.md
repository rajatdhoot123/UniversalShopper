# Flipkart Automated Checkout Bot API

## Description

This project uses FastAPI and Playwright to automate the process of purchasing products on Flipkart.com. It provides a RESTful API that can be integrated with any frontend UI, allowing for a more user-friendly experience compared to the command-line version.

The API handles several steps in the checkout flow, including:

*   Navigating to a specified product page.
*   Clicking the "Buy Now" button.
*   Handling user login via phone number and OTP.
*   Selecting a delivery address from available options.
*   Navigating through the order summary page.
*   Selecting Credit/Debit card payment.
*   Processing payment details.
*   Handling the bank's 3D Secure/OTP verification page.
*   Session management to save and reuse login state, avoiding repeated logins.
*   Screenshot capture for debugging and monitoring.

**Disclaimer:** This project is intended for educational purposes and personal use only. Automating website interactions may violate the terms of service of the target website (Flipkart). Use responsibly and at your own risk.

## Features

*   **RESTful API:** All functionality is exposed via a RESTful API built with FastAPI.
*   **Stateful Process Management:** Each checkout process runs as a background task with a unique ID.
*   **Session Management:** Login sessions can be saved and reused for faster checkout.
*   **Screenshot Capture:** Screenshots of key steps are saved and accessible via API.
*   **Interactive Checkout Flow:** The API allows for interactive input at each stage (OTP, address selection, payment details, etc.).
*   **Multiple Concurrent Checkouts:** Run multiple checkout processes simultaneously.

## Prerequisites

*   Python 3.8+
*   Pip (Python package installer)

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/flipkart-checkout-bot-api.git
    cd flipkart-checkout-bot-api
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    # On Windows
    .\venv\Scripts\activate
    # On macOS/Linux
    source venv/bin/activate
    ```

3.  **Install required Python packages:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Install Playwright browsers:**
    ```bash
    playwright install chromium
    ```

## Running the API

Start the FastAPI server:

```bash
python app.py
```

Or use uvicorn directly:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`. API documentation is automatically generated and available at:

* Swagger UI: `http://localhost:8000/docs`
* ReDoc: `http://localhost:8000/redoc`

## API Endpoints

### Session Management
- `GET /sessions` - List all available saved sessions
- `GET /debug-images/{filename}` - Access debug screenshots 

### Process Management
- `POST /process` - Start a new checkout process
- `GET /process/{process_id}` - Get status of a specific checkout process
- `GET /processes` - List all active checkout processes

### Checkout Steps
- `POST /process/{process_id}/login-otp` - Submit OTP for login
- `POST /process/{process_id}/select-address` - Select delivery address
- `POST /process/{process_id}/payment` - Submit payment details
- `POST /process/{process_id}/bank-otp` - Submit bank OTP

## Checkout Flow

1. **Start a checkout process** by providing a product URL and optionally a session name
2. **Monitor the process status** to determine which action is required next
3. **Submit required information** (OTP, address selection, payment details, etc.) at each stage
4. **View screenshots** for visual confirmation of each step

## Building a Frontend

You can build a UI for this API using any frontend technology (React, Vue, Angular, etc.). The API provides all the necessary endpoints for a interactive checkout experience.

## Security Considerations

* Payment details are only held in memory during the checkout process and not persisted.
* Consider implementing proper authentication for the API in production.
* For production use, enable HTTPS to secure data in transit.

## Project Structure

* `app.py` - FastAPI application with route definitions
* `flipkart_bot_api.py` - Core bot logic and API functions
* `debug_images/` - Directory containing screenshots
* `sessions/` - Directory containing saved browser sessions

## Disclaimer & Warning

*   **Use Responsibly:** This project interacts with a live e-commerce website. Be absolutely sure you want to purchase the items before confirming checkout.
*   **Terms of Service:** Automation might be against Flipkart's Terms of Service. Use at your own risk.
*   **Security:** While the project doesn't store payment details persistently, they are processed in memory. Ensure your environment is secure.
*   **Maintainability:** Website UIs change frequently. This project might break if Flipkart updates its website structure. 