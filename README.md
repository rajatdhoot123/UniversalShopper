# Flipkart Automated Checkout Bot

## Description

This Python script uses Playwright to automate the process of purchasing a specific product on Flipkart.com. It handles several steps in the checkout flow, including:

*   Navigating to a predefined product page.
*   Clicking the "Buy Now" button.
*   Handling user login via phone number and OTP (with API response validation and retries).
*   Selecting a delivery address from the available options.
*   Navigating through the order summary page.
*   Selecting Credit/Debit card payment.
*   Filling in card details (provided by the user at runtime).
*   Handling the bank's 3D Secure/OTP verification page.
*   Session management to save and reuse login state, avoiding repeated logins.
*   Saving screenshots to a `debug_images/` directory during errors or key steps for easier debugging.

**Disclaimer:** This script is intended for educational purposes and personal use only. Automating website interactions may violate the terms of service of the target website (Flipkart). Use responsibly and at your own risk. The script requires you to enter sensitive information like login credentials, OTPs, and payment details directly into the console during execution; this information is *not* stored by the script itself but is handled by Playwright to interact with the website.

## Prerequisites

*   Python 3.8+
*   Pip (Python package installer)

## Installation

1.  **Clone the repository (or download the script):**
    ```bash
    # If you have git installed
    # git clone <repository_url>
    # cd <repository_directory>
    ```
    *(Replace `<repository_url>` and `<repository_directory>` if applicable)*

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
    pip install playwright
    ```

4.  **Install Playwright browsers:**
    (This needs to be done once after installing the playwright package)
    ```bash
    playwright install chromium
    ```
    *(The script currently uses Chromium)*

## Usage

1.  **Ensure your virtual environment is active.**
2.  **Modify the `product_url` variable** inside `flipkart_bot.py` to the URL of the product you wish to purchase.
    ```python
    # Inside main() function in flipkart_bot.py
    product_url = "YOUR_FLIPKART_PRODUCT_URL_HERE"
    ```
3.  **Run the script from your terminal:**
    ```bash
    python flipkart_bot.py
    ```
4.  **Follow the prompts:**
    *   The script will first ask you to manage sessions (create a new one or use an existing one).
    *   It will then launch a browser window.
    *   You will be prompted to enter your Flipkart login credentials (phone/email), OTPs, select a delivery address, and enter payment card details as the script progresses through the checkout flow.

## Features

*   **Automated Checkout:** Handles navigation, login, address, summary, payment, and OTP steps.
*   **Session Management:** Saves login cookies to a `sessions/` directory, allowing you to skip the login step on subsequent runs with the same session.
*   **OTP Handling:** Intercepts API calls during login to verify OTP success/failure, with retry logic.
*   **Dynamic UI Handling:** Attempts to handle variations in UI elements (e.g., payment forms within iframes, different expiry date formats).
*   **State Machine Logic:** Tries to detect the current page (Login, Address, Summary, Payment) and execute the appropriate handler.
*   **Debugging Screenshots:** Saves screenshots to `debug_images/` on timeouts, errors, or before critical actions.

## Configuration

*   **Product URL:** The target product URL *must* be set in the `product_url` variable within the `main()` function of `flipkart_bot.py`.
*   **User Input:** All sensitive information (login, OTP, card details) is requested via command-line prompts during runtime.

## Disclaimer & Warning

*   **Use Responsibly:** This script interacts with a live e-commerce website. Be absolutely sure you want to purchase the item before running the script through to completion.
*   **Terms of Service:** Automation might be against Flipkart's Terms of Service. Use at your own risk. Account suspension is a possibility.
*   **Security:** While the script doesn't store your payment details or OTPs persistently, they are entered via the console and processed in memory. Ensure your runtime environment is secure.
*   **Maintainability:** Website UIs change frequently. This script might break if Flipkart updates its website structure or selectors. Regular maintenance might be required. 