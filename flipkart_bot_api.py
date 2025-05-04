import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright, Page, TimeoutError, Response, Route
import re
import json
import aiohttp
from typing import Dict, List, Optional, Any, Union
import time
from datetime import datetime
import pdb
import google.generativeai as genai
from dotenv import load_dotenv

# Configure Gemini API Key
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("Warning: GEMINI_API_KEY environment variable not set. Gemini vision features will be disabled.")

# Create debug images directory
debug_images_dir = Path("debug_images")
debug_images_dir.mkdir(exist_ok=True)

# Create sessions directory
sessions_dir = Path("sessions")
sessions_dir.mkdir(exist_ok=True)

# Global store for active processes
active_processes = {}

# Process states
PROCESS_STATES = {
    "INITIALIZING": "Initializing the checkout process",
    "NAVIGATING": "Navigating to product page",
    "CLICKING_BUY_NOW": "Clicking Buy Now button",
    "LOGIN_REQUIRED": "Waiting for phone number input",
    "OTP_REQUESTED": "Waiting for OTP input",
    "SELECTING_ADDRESS": "Waiting for address selection",
    "ORDER_SUMMARY": "Processing order summary",
    "PAYMENT_REQUESTED": "Waiting for payment details",
    "PAYMENT_CLICKED": "Pay button clicked, waiting for bank page",
    "PAYMENT_NAVIGATION_COMPLETE": "Navigation to bank page complete, detecting state",
    "BANK_OTP_REQUESTED": "Waiting for bank OTP",
    "COMPLETED": "Checkout process completed",
    "ERROR": "An error occurred during checkout",
    "CANCELLED": "Checkout process was cancelled",
    "POST_BUY_NOW": "Clicked Buy Now, detecting next step"
}

# Event locks for synchronization
event_locks = {}

# State to Handler Mapping definition moved below handler functions


async def create_or_load_session(session_path: Optional[Path] = None) -> Path:
    """Create a new session or load an existing one."""
    if session_path is None:
        # Generate a timestamp-based session name
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        session_path = sessions_dir / f"session_{timestamp}.json"

    return session_path


def sanitize_filename(name):
    """Removes or replaces characters unsuitable for filenames."""
    name = re.sub(r'[\\/*?":<>|]', '', name)
    name = name.replace(' ', '_')
    return name[:50]


async def create_debug_screenshot(page: Page, name: str) -> str:
    """Create a debug screenshot and return the path."""
    if page.is_closed():
        return "Page is closed, cannot take screenshot"

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    sanitized_name = sanitize_filename(name)
    file_name = f"{sanitized_name}_{timestamp}.png"
    file_path = debug_images_dir / file_name

    try:
        await page.screenshot(path=file_path)
        return str(file_path)
    except Exception as e:
        return f"Error taking screenshot: {str(e)}"


def get_process_status(process_id: str) -> Optional[Dict[str, Any]]:
    """Get the status of a specific process."""
    if process_id not in active_processes:
        return None

    # Create a copy without sensitive data
    result = active_processes[process_id].copy()
    if "_payment_details" in result:
        del result["_payment_details"]

    return result


def get_active_processes() -> List[Dict[str, Any]]:
    """Get a list of all active processes."""
    return [
        {**{k: v for k, v in process.items() if k != "_payment_details"},
         "process_id": pid}
        for pid, process in active_processes.items()
    ]


def update_process_status(process_id: str, stage: str, message: str = None, data: Dict[str, Any] = None):
    """Update the status (stage) of a process."""
    if process_id not in active_processes:
        active_processes[process_id] = {
            "stage": stage,
            "message": message or PROCESS_STATES.get(stage, ""),
            "timestamp": time.time(),
            "data": data or {},
            "screenshots": []
        }
    else:
        active_processes[process_id]["stage"] = stage
        active_processes[process_id]["message"] = message or PROCESS_STATES.get(
            stage, "")
        active_processes[process_id]["timestamp"] = time.time()

        if data:
            active_processes[process_id]["data"].update(data)

    # Add a small delay after updating status
    time.sleep(1)


def add_process_screenshot(process_id: str, screenshot_path: str):
    """Add a screenshot to the process data."""
    if process_id in active_processes:
        if "screenshots" not in active_processes[process_id]:
            active_processes[process_id]["screenshots"] = []

        active_processes[process_id]["screenshots"].append({
            "path": screenshot_path,
            "url": f"/debug-images/{Path(screenshot_path).name}",
            "timestamp": time.time()
        })

# Functions for handling user inputs


async def submit_login_otp(process_id: str, otp: str) -> bool:
    """Submit OTP for login."""
    if process_id not in active_processes or active_processes[process_id]["stage"] != "OTP_REQUESTED":
        print(
            f"[submit_login_otp] Process {process_id} not found or not in OTP_REQUESTED stage. Current stage: {active_processes.get(process_id, {}).get('stage')}")
        return False

    if process_id not in event_locks:
        return False

    # Store OTP in process data
    update_process_status(process_id, "OTP_SUBMITTED", "OTP submitted, processing", {
        "otp": otp
    })

    # Set the event to resume the checkout process
    event_locks[process_id].set()
    return True


async def select_address(process_id: str, address_index: int) -> bool:
    """Select delivery address."""
    if process_id not in active_processes or active_processes[process_id]["stage"] != "SELECTING_ADDRESS":
        print(
            f"[select_address] Process {process_id} not found or not in SELECTING_ADDRESS stage. Current stage: {active_processes.get(process_id, {}).get('stage')}")
        return False

    if process_id not in event_locks:
        return False

    # Store address selection in process data
    update_process_status(process_id, "ADDRESS_SELECTED", "Address selected, processing", {
        "address_index": address_index
    })

    # Set the event to resume the checkout process
    event_locks[process_id].set()
    return True


async def submit_payment_details(
    process_id: str,
    card_number: str,
    cvv: str,
    expiry_month: Optional[str] = None,
    expiry_year: Optional[str] = None,
    expiry_combined: Optional[str] = None
) -> bool:
    """Submit payment details."""
    if process_id not in active_processes or active_processes[process_id]["stage"] != "PAYMENT_REQUESTED":
        print(
            f"[submit_payment_details] Process {process_id} not found or not in PAYMENT_REQUESTED stage. Current stage: {active_processes.get(process_id, {}).get('stage')}")
        return False

    if process_id not in event_locks:
        return False

    # Store payment details in process data
    update_process_status(process_id, "PAYMENT_SUBMITTED", "Payment details submitted, processing", {
        "payment_details_provided": True
    })

    # Store payment details in a more secure way (in memory only)
    active_processes[process_id]["_payment_details"] = {
        "card_number": card_number,
        "cvv": cvv,
        "expiry_month": expiry_month,
        "expiry_year": expiry_year,
        "expiry_combined": expiry_combined
    }

    # Set the event to resume the checkout process
    event_locks[process_id].set()
    return True


# --- Add Gemini Helper ---
async def call_gemini_vision(image_bytes: bytes, prompt: str) -> Optional[Dict[str, str]]:
    """Sends an image and prompt to Gemini Vision Pro and returns the parsed JSON response."""
    if not GEMINI_API_KEY:
        print("Error: Gemini API key not configured. Cannot use vision features.")
        return None

    try:
        print("Calling Gemini Vision API...")
        model = genai.GenerativeModel('gemini-pro-vision')
        image_part = {"mime_type": "image/png", "data": image_bytes}
        response = await asyncio.to_thread(
            model.generate_content,
            [prompt, image_part]
        )

        # Attempt to parse the response as JSON
        try:
            # Clean the response text before parsing
            cleaned_text = response.text.strip().replace('```json', '').replace('```', '').strip()
            print(f"Gemini Raw Response Text:\n{response.text}") # Log raw response
            print(f"Cleaned Text for JSON parsing:\n{cleaned_text}")
            result = json.loads(cleaned_text)
            print(f"Gemini Parsed JSON Response: {result}")
            # Basic validation
            if isinstance(result, dict) and "otp_input_selector" in result and "submit_button_selector" in result:
                 # Optionally add context check: and "context" in result and result["context"] in ["page", "iframe"]
                return result
            else:
                print(f"Error: Gemini response missing required keys or invalid structure: {result}")
                return None
        except json.JSONDecodeError as json_err:
            print(f"Error: Failed to parse Gemini response as JSON. Error: {json_err}")
            print(f"Gemini Raw Text was: {response.text}")
            return None
        except Exception as parse_err:
            print(f"Error: Unexpected error parsing Gemini response: {parse_err}")
            print(f"Gemini Raw Text was: {response.text}")
            return None

    except Exception as e:
        print(f"Error calling Gemini Vision API: {e}")
        return None


async def handle_bank_otp_gemini(process_id: str, page: Page):
    """Handle the bank OTP verification page using Gemini Vision API."""
    print("--- Handling Bank OTP via Gemini Vision ---")
    if not GEMINI_API_KEY:
        print("Gemini API key not set. Falling back to multi-attempt logic.")
        # Fallback (optional, or just error out)
        # return await handle_bank_otp_multi_attempt(process_id, page)
        update_process_status(process_id, "ERROR", "Gemini API key not configured for Bank OTP step.")
        return False

    # 1. Update Status & Wait for OTP via API (Common part)
    update_process_status(process_id, "BANK_OTP_REQUESTED", "Please provide bank OTP via API (Using Gemini Vision)")
    screenshot_path = await create_debug_screenshot(page, "bank_otp_request_gemini")
    add_process_screenshot(process_id, screenshot_path)

    if process_id not in event_locks:
        event_locks[process_id] = asyncio.Event()

    print("Waiting for Bank OTP submission via API...")
    await event_locks[process_id].wait()
    event_locks[process_id].clear()
    print("Received signal for Bank OTP submission.")

    if "bank_otp" not in active_processes[process_id]["data"]:
        update_process_status(process_id, "ERROR", "Bank OTP missing after waiting")
        return False
    bank_otp = active_processes[process_id]["data"]["bank_otp"]
    print(f"Retrieved Bank OTP. Asking Gemini to find elements...")

    # 2. Get Screenshot for Gemini
    try:
        # Take screenshot again *after* OTP is available, in case page changed slightly
        screenshot_bytes = await page.screenshot()
        screenshot_path_gemini = await create_debug_screenshot(page, "bank_otp_for_gemini")
        add_process_screenshot(process_id, screenshot_path_gemini)
    except Exception as ss_err:
        update_process_status(process_id, "ERROR", f"Failed to take screenshot for Gemini: {ss_err}")
        return False

    # 3. Call Gemini Vision
    prompt = """
Analyze this bank OTP page screenshot. Identify the CSS selectors for the following elements:
1.  The primary input field where the user should type the One-Time Password (OTP).
2.  The main confirmation or submit button to click after entering the OTP (e.g., "Submit", "Confirm", "Pay").

Return the response ONLY as a JSON object with the following keys:
- "otp_input_selector": The CSS selector for the OTP input field.
- "submit_button_selector": The CSS selector for the submit button.

Example:
{
  "otp_input_selector": "input#otpValue[type='password']",
  "submit_button_selector": "button:text-matches('Submit', 'i')"
}

If you cannot confidently identify one or both selectors, return null for that key.
"""
    gemini_result = await call_gemini_vision(screenshot_bytes, prompt)

    # 4. Process Gemini Response
    if not gemini_result or not gemini_result.get("otp_input_selector") or not gemini_result.get("submit_button_selector"):
        print("Error: Gemini failed to provide valid selectors. Cannot proceed with OTP submission.")
        # Optional: Fallback to multi-attempt here?
        update_process_status(process_id, "ERROR", "Gemini Vision failed to identify OTP elements.")
        # return await handle_bank_otp_multi_attempt(process_id, page) # Example fallback
        return False

    otp_selector = gemini_result["otp_input_selector"]
    submit_selector = gemini_result["submit_button_selector"]
    # context = gemini_result.get("context", "page") # If context detection is added

    print(f"Gemini identified selectors: OTP Input='{otp_selector}', Submit Button='{submit_selector}'")

    # 5. Interact using Gemini's selectors (Assuming page context for now)
    context_locator = page # Future: use context if Gemini provides it (e.g., iframe locator)
    try:
        # Find OTP Input
        print(f"   Locating OTP input via Gemini: '{otp_selector}'")
        otp_input = context_locator.locator(otp_selector).first
        await otp_input.wait_for(state='visible', timeout=15000)
        print(f"   OTP input found.")

        # Find Submit Button
        print(f"   Locating Submit button via Gemini: '{submit_selector}'")
        submit_button = context_locator.locator(submit_selector).first
        await submit_button.wait_for(state='visible', timeout=15000) # Consider waiting for enabled too? state='enabled'
        print(f"   Submit button found.")

        # Fill OTP
        print(f"   Filling OTP...")
        await otp_input.fill(bank_otp)
        await page.wait_for_timeout(500)
        print("   OTP Filled.")
        screenshot_path = await create_debug_screenshot(page, f"otp_filled_gemini")
        add_process_screenshot(process_id, screenshot_path)

        # Click Submit
        print(f"   Clicking Submit...")
        try:
            await submit_button.click(timeout=10000)
            print(f"   Clicked Submit successfully.")
        except TimeoutError:
            print(f"   Submit button timed out on click. Trying force click...")
            await submit_button.click(force=True, timeout=10000)
            print(f"   Clicked Submit (force=True).")

        # Wait for Navigation
        print("   Waiting for page navigation/load after OTP submission...")
        await page.wait_for_load_state('networkidle', timeout=90000)
        print("   Navigation/load complete.")
        screenshot_path = await create_debug_screenshot(page, f"otp_success_gemini")
        add_process_screenshot(process_id, screenshot_path)

        # Success
        final_url = page.url
        print(f"   Final URL: {final_url}")
        # TODO: Check final URL for success/failure if possible
        update_process_status(process_id, "COMPLETED", f"Order completed (via Gemini Vision)")
        return True

    except TimeoutError as te:
        error_msg = f"Timeout waiting for element identified by Gemini. Selector: {te}" # Improve error msg
        print(f"   Gemini interaction failed: {error_msg}")
        update_process_status(process_id, "ERROR", f"Timeout using Gemini selector: {error_msg}")
        screenshot_path = await create_debug_screenshot(page, "bank_otp_gemini_timeout")
        add_process_screenshot(process_id, screenshot_path)
        # Optional Fallback here?
        return False
    except Exception as e:
        error_msg = f"Error during interaction using Gemini selectors: {e}"
        print(f"   Gemini interaction failed: {error_msg}")
        update_process_status(process_id, "ERROR", error_msg)
        screenshot_path = await create_debug_screenshot(page, "bank_otp_gemini_error")
        add_process_screenshot(process_id, screenshot_path)
        # Optional Fallback here?
        return False

# Navigation and core checkout functions


async def navigate_and_buy(process_id: str, page: Page, url: str) -> bool:
    """Navigate to product page and click Buy Now."""
    try:
        # Navigate to product URL
        update_process_status(process_id, "NAVIGATING", f"Navigating to {url}")
        await page.goto(url, wait_until='networkidle', timeout=45000)
        await page.wait_for_timeout(3000)  # Allow page to settle

        # Take screenshot after navigation
        screenshot_path = await create_debug_screenshot(page, "product_page_loaded")
        add_process_screenshot(process_id, screenshot_path)

        # Try to extract product title
        product_title = "Unknown"
        try:
            title_locator = page.locator('span.B_NuCI, h1 span._35KyD6')
            if await title_locator.first.is_visible(timeout=10000):
                title_text = await title_locator.first.text_content()
                if title_text:
                    product_title = title_text.strip()
            update_process_status(process_id, "NAVIGATING", "Product page loaded", {
                "product_title": product_title
            })
        except Exception as title_ex:
            print(f"Could not extract product title: {title_ex}")
            update_process_status(process_id, "NAVIGATING", "Product page loaded (title unknown)", {
                "product_title": product_title
            })

        # Click Buy Now button
        update_process_status(process_id, "CLICKING_BUY_NOW",
                              "Attempting to click Buy Now")

        buy_now_button = page.locator('*:text-matches("Buy now", "i")')
        await buy_now_button.wait_for(state='visible', timeout=20000)

        # Take screenshot before clicking
        screenshot_path = await create_debug_screenshot(page, "before_buy_now_click")
        add_process_screenshot(process_id, screenshot_path)

        await buy_now_button.click()

        # Wait for navigation triggered by the click
        # Using wait_for_load_state('load') might be more reliable here than networkidle
        # as checkout pages often have ongoing background requests.
        print("Waiting for page load after clicking Buy Now...")
        await page.wait_for_load_state('load', timeout=30000)
        print(f"Navigation complete after Buy Now. Current URL: {page.url}")

        # Take screenshot after clicking and navigation
        screenshot_path = await create_debug_screenshot(page, "after_buy_now_click")
        add_process_screenshot(process_id, screenshot_path)

        return True

    except TimeoutError as te:
        error_msg = f"TimeoutError during navigation or Buy Now click: {te}"
        print(error_msg)
        update_process_status(process_id, "ERROR", error_msg)
        if not page.is_closed():
            screenshot_path = await create_debug_screenshot(page, "navigation_timeout_error")
            add_process_screenshot(process_id, screenshot_path)
        return False
    except Exception as e:
        error_msg = f"Failed to navigate or click Buy Now: {str(e)}"
        print(error_msg)
        update_process_status(process_id, "ERROR", error_msg)
        try:
            if not page.is_closed():
                screenshot_path = await create_debug_screenshot(page, "navigation_general_error")
                add_process_screenshot(process_id, screenshot_path)
        except Exception as ss_err:
            print(
                f"Could not take screenshot during navigation error handling: {ss_err}")
        return False

# Main process orchestrator


async def checkout_process_manager(process_id: str, product_url: str, session_path: Optional[Path] = None):
    """Main function to manage the checkout process."""
    update_process_status(process_id, "INITIALIZING",
                          "Initializing browser")
    browser = None
    context = None
    try:
        async with async_playwright() as p:
            # Launch browser
            # Consider headless=True for production
            browser = await p.chromium.launch(headless=False)

            # Create or load context based on session
            if session_path and session_path.exists():
                update_process_status(
                    process_id, "INITIALIZING", f"Loading session from {session_path}")
                try:
                    context = await browser.new_context(storage_state=session_path)
                    print(f"Session loaded successfully from {session_path}")
                except Exception as load_err:
                    print(
                        f"Warning: Failed to load session from {session_path}: {load_err}. Creating new context.")
                    # Fallback to new context if loading fails
                    context = await browser.new_context()
            else:
                if session_path:
                    print(
                        f"Session file {session_path} not found. Creating new context.")
                else:
                    print("No session path provided. Creating new context.")
                update_process_status(
                    process_id, "INITIALIZING", "Creating new browser context")
                context = await browser.new_context()

            # Run the checkout process, passing the session_path down
            result = await start_purchase_process(process_id, product_url, context, session_path)

            # Save session state if path was provided and process didn't error early
            if session_path and context and get_process_status(process_id).get("stage") != "ERROR":
                try:
                    await context.storage_state(path=session_path)
                    print(f"Session state saved to {session_path}")
                    # Optionally update status
                    current_status = get_process_status(process_id)
                    if current_status:
                        update_process_status(
                            process_id,
                            current_status["stage"],
                            f"{current_status['message']} (Session saved)"
                        )
                except Exception as e:
                    print(f"Error saving session state to {session_path}: {e}")
                    # Update status to reflect session saving error
                    current_status = get_process_status(process_id)
                    if current_status:
                        update_process_status(
                            process_id,
                            current_status["stage"],
                            f"{current_status['message']} (Error saving session)"
                        )

            # Optional: Keep browser open for inspection only if process completed successfully or needs manual OTP etc.
            # final_status = get_process_status(process_id)
            # if final_status and final_status["stage"] in ["COMPLETED", "BANK_OTP_REQUESTED"]:
            #     print(f"Process ended with state: {final_status['stage']}. Keeping browser open indefinitely.")
            #     await asyncio.sleep(float('inf')) # Keep open forever
            # else:
            #      print(f"Process ended with state: {final_status.get('stage', 'UNKNOWN')}. Keeping browser open.")
            #      await asyncio.sleep(float('inf')) # Keep open forever

            print(f"Process finished. Keeping browser open.")
            await asyncio.sleep(float('inf'))  # Keep open forever

    except Exception as e:
        error_msg = f"Process manager error: {str(e)}"
        print(error_msg)
        # Ensure status reflects the manager-level error
        update_process_status(process_id, "ERROR", error_msg)
        print("Process encountered an error. Keeping browser open.")
        await asyncio.sleep(float('inf'))  # Keep open on error too

    finally:
        # Ensure browser and context are closed
        # if context:
        #     try:
        #         await context.close()
        #         print("Browser context closed.")
        #     except Exception as ctx_close_err:
        #         print(f"Error closing browser context: {ctx_close_err}")
        # if browser:
        #     try:
        #         await browser.close()
        #         print("Browser closed.")
        #     except Exception as br_close_err:
        #         print(f"Error closing browser: {br_close_err}")
        print(
            f"Checkout process manager finished for process {process_id}. Browser remains open.")

# Handler functions for different checkout stages


async def handle_login_api(process_id: str, page: Page):
    """Handle login with phone number and OTP."""
    # --- Selectors ---
    phone_input_selector = "input[type='text'][autocomplete='off']"
    continue_button_selector = "button:has-text('CONTINUE')"
    otp_input_selector = "input[type='text'][maxlength='6']"
    final_login_button_selector = "button:has-text('LOGIN'), button:has-text('SIGNUP')"
    otp_api_endpoint = '/api/1/user/login/otp'

    # Update process status
    update_process_status(process_id, "LOGIN_REQUIRED",
                          "Please provide your phone number via API")

    # Take screenshot
    screenshot_path = await create_debug_screenshot(page, "login_phone_request")
    add_process_screenshot(process_id, screenshot_path)

    # Wait for phone number input via API
    # For simplicity in this API version, we'll use a hardcoded phone number
    # In a real implementation, this would come from the client
    phone_number = "1234567890"  # This would come from the client

    try:
        # Enter phone number
        phone_input = page.locator(phone_input_selector).first
        await phone_input.wait_for(state='visible', timeout=10000)
        await phone_input.fill(phone_number)

        # Click continue
        continue_button = page.locator(continue_button_selector).first
        await continue_button.wait_for(state='visible', timeout=5000)
        await continue_button.click()

        # Wait for OTP input field
        otp_input = page.locator(otp_input_selector).first
        await otp_input.wait_for(state='visible', timeout=15000)

        # Update status and take screenshot
        update_process_status(process_id, "OTP_REQUESTED",
                              "Please provide the OTP received on your phone")
        screenshot_path = await create_debug_screenshot(page, "login_otp_request")
        add_process_screenshot(process_id, screenshot_path)

        # Wait for OTP to be submitted via API
        if process_id not in event_locks:
            event_locks[process_id] = asyncio.Event()

        # Wait for the API to provide OTP (user interaction)
        await event_locks[process_id].wait()
        event_locks[process_id].clear()  # Reset for next wait

        # Get OTP from process data
        if "otp" in active_processes[process_id]["data"]:
            otp = active_processes[process_id]["data"]["otp"]

            # Enter OTP
            await otp_input.fill(otp)

            # Set up OTP verification listener (simplified for API version)
            final_button = page.locator(final_login_button_selector).first
            await final_button.wait_for(state='visible', timeout=10000)
            await final_button.click()

            # Wait for navigation after login
            await page.wait_for_load_state('networkidle', timeout=20000)

            # Take screenshot after login
            screenshot_path = await create_debug_screenshot(page, "after_login")
            add_process_screenshot(process_id, screenshot_path)

            update_process_status(
                process_id, "LOGIN_COMPLETED", "Login completed successfully")
            return True
        else:
            update_process_status(
                process_id, "ERROR", "OTP was provided but is missing from process data")
            return False

    except Exception as e:
        update_process_status(process_id, "ERROR",
                              f"Error during login: {str(e)}")
        screenshot_path = await create_debug_screenshot(page, "login_error")
        add_process_screenshot(process_id, screenshot_path)
        return False


async def handle_address_selection_api(process_id: str, page: Page):
    """Handle address selection via API."""
    # Selectors
    address_container_selector = 'label:has(input[name="address"])'
    name_selector_relative = 'span:has-text("HOME") >> xpath=preceding-sibling::span[1]'
    name_selector_fallback = 'p > span:first-child'
    address_text_selector_relative = 'p + span'
    deliver_button_selector = 'button:has-text("Deliver Here")'

    try:
        # Take screenshot of address page
        screenshot_path = await create_debug_screenshot(page, "address_selection_page")
        add_process_screenshot(process_id, screenshot_path)

        # Try to click 'View all addresses' if present
        view_all_selector = 'div:text-matches("View all \\d+ addresses", "i")'
        try:
            view_all_button = page.locator(view_all_selector).first
            if await view_all_button.is_visible(timeout=3000):
                await view_all_button.click()
                await page.wait_for_timeout(1500)
        except:
            pass

        # Find all address blocks
        address_labels = await page.locator(address_container_selector).all()

        if not address_labels:
            update_process_status(process_id, "ERROR",
                                  "No address blocks found")
            return False

        # Parse addresses
        addresses = []
        for i, label in enumerate(address_labels):
            try:
                # Try finding name relative to HOME tag
                name_element = label.locator(name_selector_relative)
                if await name_element.is_visible(timeout=500):
                    name = await name_element.text_content()
                else:
                    # Try fallback name selector
                    name_element_fallback = label.locator(
                        name_selector_fallback)
                    if await name_element_fallback.is_visible(timeout=500):
                        name = await name_element_fallback.text_content()
                    else:
                        name = f"Address {i+1}"

                # Find address text
                address_element = label.locator(address_text_selector_relative)
                if await address_element.is_visible(timeout=500):
                    address_text = await address_element.text_content()
                    address_text = ' '.join(address_text.split())
                else:
                    address_text = "Address details not found"

                addresses.append({
                    "index": i,
                    "name": name.strip() if name else f"Address {i+1}",
                    "text": address_text.strip() if address_text else "No details",
                })
            except Exception as e:
                addresses.append({
                    "index": i,
                    "name": f"Address {i+1}",
                    "text": f"Error parsing: {str(e)}",
                })

        # Update process status with available addresses
        update_process_status(process_id, "SELECTING_ADDRESS", "Please select a delivery address via API", {
            "available_addresses": addresses
        })

        # Wait for address selection via API
        if process_id not in event_locks:
            event_locks[process_id] = asyncio.Event()

        await event_locks[process_id].wait()
        event_locks[process_id].clear()  # Reset for next wait

        # Get selected address from process data
        if "address_index" in active_processes[process_id]["data"]:
            address_index = active_processes[process_id]["data"]["address_index"]

            if address_index >= 0 and address_index < len(address_labels):
                # Click the selected address label
                await address_labels[address_index].click()
                await page.wait_for_timeout(1000)

                # Take screenshot after selection
                screenshot_path = await create_debug_screenshot(page, "after_address_selection")
                add_process_screenshot(process_id, screenshot_path)

                # Click 'Deliver Here' button
                deliver_button = page.locator(deliver_button_selector).first
                await deliver_button.wait_for(state='visible', timeout=10000)
                await deliver_button.click()

                # Wait for page navigation
                await page.wait_for_load_state('networkidle', timeout=20000)

                # Take screenshot after clicking Deliver Here
                screenshot_path = await create_debug_screenshot(page, "after_deliver_here_click")
                add_process_screenshot(process_id, screenshot_path)

                update_process_status(
                    process_id, "ADDRESS_SELECTED", "Address selected successfully")
                return True
            else:
                update_process_status(
                    process_id, "ERROR", f"Invalid address index: {address_index}")
                return False
        else:
            update_process_status(process_id, "ERROR",
                                  "Address index missing from process data")
            return False

    except Exception as e:
        update_process_status(process_id, "ERROR",
                              f"Error during address selection: {str(e)}")
        screenshot_path = await create_debug_screenshot(page, "address_selection_error")
        add_process_screenshot(process_id, screenshot_path)
        return False

async def handle_order_summary_api(process_id: str, page: Page):
    """Handle the order summary page and potential popups."""
    # Selector
    continue_button_selector = 'button:has-text("CONTINUE")'
    accept_popup_button_selector = 'button.QqFHMw._0ofT-K.M5XAsp:has-text("Accept & Continue")' # Added selector for popup

    try:
        # Take screenshot of order summary page
        screenshot_path = await create_debug_screenshot(page, "order_summary_page")
        add_process_screenshot(process_id, screenshot_path)

        # Update status
        update_process_status(process_id, "ORDER_SUMMARY",
                              "Processing order summary")

        # Try to extract order details (optional)
        try:
            # Example: Extract total amount
            # Using a more robust selector that finds the final amount row
            total_amount_row_selector = 'div._1YBGQV' # Assuming this is the container for the total amount row
            # Find the last span within this row, which usually holds the final price
            total_amount_locator = page.locator(f'{total_amount_row_selector} span').last
            if await total_amount_locator.is_visible(timeout=5000):
                total_amount = await total_amount_locator.text_content()
                update_process_status(process_id, "ORDER_SUMMARY", "Processing order summary", {
                    "total_amount": total_amount.strip() if total_amount else "Unknown"
                })
            else:
                 update_process_status(process_id, "ORDER_SUMMARY", "Processing order summary", {
                    "total_amount": "Unknown (Selector not found/visible)"
                 })

        except Exception as detail_ex:
            print(f"Could not extract order details: {detail_ex}")
            # Update status even if details extraction fails
            update_process_status(process_id, "ORDER_SUMMARY", "Processing order summary (Details extraction failed)", {
                "total_amount": "Unknown"
            })


        # Locate and click the CONTINUE button
        continue_button = page.locator(continue_button_selector).first
        await continue_button.wait_for(state='visible', timeout=15000)

        # Check if button is enabled
        if not await continue_button.is_enabled(timeout=1000):
            print("CONTINUE button not enabled, waiting...")
            await page.wait_for_timeout(3000)  # Extra wait
            if not await continue_button.is_enabled(timeout=1000):
                 print("CONTINUE button still not enabled after wait.")
                 # Optional: Raise error or try force click depending on desired robustness
                 # For now, we'll try clicking anyway

        await continue_button.click()
        print("Clicked CONTINUE on order summary.")

        # Wait for potential page transition or overlay
        # Using a combination of networkidle and timeout to be safe
        try:
            print("Waiting after CONTINUE click (networkidle)...")
            await page.wait_for_load_state('networkidle', timeout=15000) # Reduced timeout, popup might appear before full load
            print("Network became idle or timeout reached.")
        except TimeoutError:
            print("Timeout waiting for networkidle after CONTINUE, proceeding to check for popup.")


        # --- Check for "Accept & Continue" Popup ---
        await page.wait_for_timeout(1000) # Small pause before checking popup
        try:
            print("Checking for 'Accept & Continue' popup...")
            popup_button = page.locator(accept_popup_button_selector).first
            # Use a short timeout for the check
            if await popup_button.is_visible(timeout=5000):
                 print("Popup found. Clicking 'Accept & Continue'...")
                 await popup_button.click()
                 print("Clicked 'Accept & Continue' popup button.")
                 # Wait a bit after clicking the popup
                 await page.wait_for_timeout(2000)
                 # Optional: Wait for load state again if popup click triggers navigation
                 print("Waiting after popup click (load)...")
                 await page.wait_for_load_state('load', timeout=20000)
                 print("Load state reached after popup click.")
            else:
                 print("Popup button not visible.")
        except TimeoutError:
            print("Popup 'Accept & Continue' button not found within timeout.")
        except Exception as popup_err:
            print(f"Error checking/clicking popup: {popup_err}")
        # --- End Popup Check ---


        # Wait for the *final* page load after Continue/Popup click
        print("Final wait for page load after summary actions...")
        await page.wait_for_load_state('networkidle', timeout=30000)
        print("Final page load complete after summary.")


        # Take screenshot after clicking CONTINUE (and potentially popup)
        screenshot_path = await create_debug_screenshot(page, "after_summary_actions")
        add_process_screenshot(process_id, screenshot_path)

        update_process_status(
            process_id, "ORDER_SUMMARY_COMPLETED", "Order summary processed successfully")
        return True

    except Exception as e:
        update_process_status(process_id, "ERROR",
                              f"Error during order summary: {str(e)}")
        screenshot_path = await create_debug_screenshot(page, "order_summary_error")
        add_process_screenshot(process_id, screenshot_path)
        return False


async def handle_payment_api(process_id: str, page: Page):
    """Handle the payment page."""
    # Selectors (Assume elements are on the main page)
    card_option_selector_locator = page.locator(
        ':text-matches("Credit / Debit / ATM Card", "i")').locator('xpath=ancestor::*[self::label or self::div][1]')
    card_number_input_selector = 'input[name="cardNumber"], input[autocomplete="cc-number"]'
    month_select_selector = 'select[name="month"]'
    year_select_selector = 'select[name="year"]'
    valid_thru_input_selector = 'input[autocomplete="cc-exp"]'
    cvv_input_selector = 'input[name="cvv"], input#cvv-input'

    try:
        # Take screenshot of payment page
        screenshot_path = await create_debug_screenshot(page, "payment_page")
        add_process_screenshot(process_id, screenshot_path)

        # Select Credit/Debit Card option
        card_option_container = card_option_selector_locator.first
        await card_option_container.wait_for(state='visible', timeout=15000)
        await card_option_container.click()
        await page.wait_for_timeout(2000)  # Wait after clicking card option

        # Use page context directly
        context_locator = page
        print("Using context: page (iframe logic removed)")

        # Wait for card number field
        card_number_input = context_locator.locator(
            card_number_input_selector).first
        await card_number_input.wait_for(state='visible', timeout=30000)

        # Determine expiry format
        expiry_input_type = 'combined'  # Assume combined input MM / YY first
        try:
            await context_locator.locator(valid_thru_input_selector).wait_for(state='visible', timeout=2000)
            print("Detected combined MM / YY expiry input.")
        except TimeoutError:
            try:
                # If combined not found, check for separate dropdowns
                await context_locator.locator(month_select_selector).wait_for(state='visible', timeout=1000)
                await context_locator.locator(year_select_selector).wait_for(state='visible', timeout=1000)
                expiry_input_type = 'dropdowns'
                print("Detected separate Month/Year dropdowns for expiry.")
            except TimeoutError:
                # If neither found, proceed assuming combined as default but log warning
                print(
                    "Warning: Could not definitively detect expiry input format. Assuming combined MM / YY.")
                expiry_input_type = 'combined'

        # Update process status requesting payment details
        update_process_status(process_id, "PAYMENT_REQUESTED", "Please provide payment details via API", {
            "expiry_input_type": expiry_input_type  # Inform client of expected format
        })

        # Take screenshot before payment details
        screenshot_path = await create_debug_screenshot(page, "before_payment_details")
        add_process_screenshot(process_id, screenshot_path)

        # Wait for payment details via API
        if process_id not in event_locks:
            event_locks[process_id] = asyncio.Event()

        await event_locks[process_id].wait()
        event_locks[process_id].clear()  # Reset for next wait

        # Get payment details from process data
        if "_payment_details" in active_processes[process_id]:
            payment_details = active_processes[process_id]["_payment_details"]

            # Fill card number
            await card_number_input.fill(payment_details["card_number"])

            # Fill CVV
            await context_locator.locator(cvv_input_selector).fill(payment_details["cvv"])
            await page.wait_for_timeout(500)

            # Fill expiry date based on format
            if expiry_input_type == 'combined':
                if payment_details.get("expiry_combined"):
                    await context_locator.locator(valid_thru_input_selector).fill(payment_details["expiry_combined"])
                else:
                    expiry_combined = f"{payment_details.get('expiry_month', '12')} / {payment_details.get('expiry_year', '25')}"
                    await context_locator.locator(valid_thru_input_selector).fill(expiry_combined)
            elif expiry_input_type == 'dropdowns':
                if payment_details.get("expiry_month") and payment_details.get("expiry_year"):
                    await context_locator.locator(month_select_selector).select_option(value=payment_details["expiry_month"])
                    await context_locator.locator(year_select_selector).select_option(value=payment_details["expiry_year"])
            else:
                # Fallback / Log error if format detection failed unexpectedly
                print("Error: Unexpected expiry_input_type during filling.")
                # Attempt combined format as a last resort
                expiry_combined = f"{payment_details.get('expiry_month', '12')} / {payment_details.get('expiry_year', '25')}"
                try:
                    await context_locator.locator(valid_thru_input_selector).fill(expiry_combined)
                except Exception as fill_err:
                    print(
                        f"Failed to fill expiry even with fallback: {fill_err}")

            await page.wait_for_timeout(500)

            # Take screenshot after filling payment details
            screenshot_path = await create_debug_screenshot(page, "after_payment_details")
            add_process_screenshot(process_id, screenshot_path)

            # Wait like in the original bot before locating pay button
            print("Pausing for 2 seconds before locating Pay button form...")
            await page.wait_for_timeout(2000)

            # Ensure payment form is present first (like original bot)
            payment_form = context_locator.locator('form#cards')
            try:
                print("Waiting for payment form (form#cards) to be attached...")
                await payment_form.wait_for(state='attached', timeout=10000)
                print("Payment form found.")
            except TimeoutError:
                print(
                    "Timeout waiting for payment form (form#cards). Cannot proceed reliably.")
                update_process_status(
                    process_id, "ERROR", "Payment form (form#cards) not found.")
                # Add screenshot here for debugging
                screenshot_path_form_error = await create_debug_screenshot(page, "payment_form_not_found")
                add_process_screenshot(process_id, screenshot_path_form_error)
                return False

            # Take screenshot right before final pause+click (like original bot)
            screenshot_path_before_pay = await create_debug_screenshot(page, "before_final_pay_attempt")
            add_process_screenshot(process_id, screenshot_path_before_pay)

            # Add the final pause from original bot
            print("Pausing for 3 seconds before final locate and click...")
            await page.wait_for_timeout(3000)

            # Locate and Click Pay Button (Mimic original bot more closely)
            pay_button_to_click = None
            pay_button_locator = None
            pay_button_selector_primary_regex = r"Pay\\s*₹\\d*\\s*"

            print(f"Locating PAY button within form#cards just before clicking...")
            try:
                # Locate within the form
                pay_button_locator = payment_form.locator(
                    f'button:text-matches("{pay_button_selector_primary_regex}", "i")').first
                # Only wait for visible, not enabled (like original bot)
                await pay_button_locator.wait_for(state='visible', timeout=25000)
                print("PAY button located and visible within form.")
                pay_button_to_click = pay_button_locator

            except TimeoutError as te:
                print(
                    f"Timeout waiting for PAY button visibility within form: {te}")
                # Optional: Could add a fallback search outside the form here if needed
                update_process_status(
                    process_id, "ERROR", f"Timeout waiting for PAY button visibility: {str(te)}")
                screenshot_path_error = await create_debug_screenshot(page, "pay_button_locate_timeout")
                add_process_screenshot(process_id, screenshot_path_error)
                return False
            except Exception as e:
                print(f"Error locating PAY button: {e}")
                update_process_status(
                    process_id, "ERROR", f"Error locating PAY button: {str(e)}")
                screenshot_path_error = await create_debug_screenshot(page, "pay_button_locate_error")
                add_process_screenshot(process_id, screenshot_path_error)
                return False

            # If we found the button, attempt to click it immediately
            if pay_button_to_click:
                try:
                    print(f"Attempting click on the located PAY button...")
                    # Click the locator we just found
                    await pay_button_to_click.click(timeout=15000)
                    print("Clicked PAY button.")
                    # NEW: Update status immediately after successful click
                    update_process_status(
                        process_id, "PAYMENT_CLICKED", "Pay button clicked, waiting for bank page")

                except Exception as click_err:
                    print(
                        f"Click failed: {click_err}. Attempting force click...")
                    try:
                        await pay_button_to_click.click(force=True, timeout=10000)
                        print("Clicked PAY button (force=True).")
                        # NEW: Update status immediately after successful force click
                        update_process_status(
                            process_id, "PAYMENT_CLICKED", "Pay button clicked (force), waiting for bank page")
                    except Exception as force_click_err:
                        print(f"Force click also failed: {force_click_err}")
                        update_process_status(
                            process_id, "ERROR", f"Failed to click PAY button (standard and force): {str(force_click_err)}")
                        screenshot_path_click_error = await create_debug_screenshot(page, "pay_button_click_error")
                        add_process_screenshot(
                            process_id, screenshot_path_click_error)
                        return False
            else:
                # This case should ideally be caught by the try/except above
                print("Error: Pay button locator was not assigned.")
                update_process_status(
                    process_id, "ERROR", "Pay button locator was None before click attempt.")
                return False

            # --- Handle potential 'Save Card' popup (Quick attempt) ---
            await page.wait_for_timeout(500)  # Brief pause after pay click
            try:
                maybe_later_selector = 'button:has-text("Maybe later")'
                maybe_later_button = page.locator(maybe_later_selector).first
                print("Quick check for 'Save Card' popup (Maybe later button)...")
                # Use a very short timeout - just click if immediately visible
                await maybe_later_button.click(timeout=2000)
                print("Clicked 'Maybe later' button during quick check.")
                # Small pause after clicking popup
                await page.wait_for_timeout(500)
            except TimeoutError:
                print(
                    "'Maybe later' button not immediately visible or clickable. Proceeding...")
            except Exception as e:
                print(
                    f"Error during quick check/click for 'Maybe later': {e}. Proceeding...")
            # --- End Quick Popup Handling ---

            # Wait for navigation to bank OTP page (Main wait)
            print("Waiting for navigation after payment submission (load state)...")
            await page.wait_for_load_state('load', timeout=90000)
            print(f"Navigated after payment. Current URL: {page.url}")

            # Take screenshot after payment submission
            screenshot_path = await create_debug_screenshot(page, "after_payment_submission")
            add_process_screenshot(process_id, screenshot_path)

            # NEW: Update status *after* successful navigation wait
            update_process_status(
                process_id, "PAYMENT_NAVIGATION_COMPLETE", "Navigation to bank page complete")

            # Return success, the loop will detect the next state (hopefully BANK_OTP)
            return True
        else:
            update_process_status(process_id, "ERROR",
                                  "Payment details missing from process data")
            return False

    except Exception as e:
        update_process_status(process_id, "ERROR",
                              f"Error during payment processing: {str(e)}")
        screenshot_path = await create_debug_screenshot(page, "payment_error")
        add_process_screenshot(process_id, screenshot_path)
        return False


async def start_purchase_process(
    process_id: str,
    product_url: str,
    browser_context,
    # Keep session_path for potential future use
    session_path: Optional[Path] = None
) -> bool:
    """Start the purchase process using a sequential, state-driven approach."""
    page = None
    try:
        page = await browser_context.new_page()

        # Create process-specific event for waiting on user input
        if process_id not in event_locks:
            event_locks[process_id] = asyncio.Event()

        # --- 1. Navigate and click Buy Now ---
        update_process_status(process_id, "NAVIGATING", "Navigating to product page", {
            "product_url": product_url
        })
        navigation_success = await navigate_and_buy(process_id, page, product_url)
        if not navigation_success:
            print("Failed during initial navigation or Buy Now click.")
            return False  # navigate_and_buy updates status on failure

        print("Clicked 'Buy Now'. Proceeding with checkout steps sequentially.")
        update_process_status(process_id, "POST_BUY_NOW",
                              "Clicked Buy Now, checking login status.")

        # --- 2. Check Login Status & Handle Login if Needed ---
        try:
            print("Checking login status via localStorage...")
            is_logged_in_str = await page.evaluate("() => localStorage.getItem('isLoggedIn')")
            is_logged_in = is_logged_in_str == 'true'
            print(f"localStorage 'isLoggedIn' value: '{is_logged_in_str}' (Parsed as: {is_logged_in})")

            if not is_logged_in:
                print("User is not logged in. Starting login flow...")
                login_success = await handle_login_api(process_id, page)
                if not login_success:
                    print("Login flow failed.")
                    # handle_login_api should set ERROR status
                    return False
                print("Login flow completed successfully.")
            else:
                print("User is already logged in. Skipping login flow.")
                # Update status to reflect skipping login
                update_process_status(process_id, "LOGIN_SKIPPED", "User already logged in")

        except Exception as login_check_err:
            error_msg = f"Error checking login status or during login flow: {login_check_err}"
            print(error_msg)
            update_process_status(process_id, "ERROR", error_msg)
            screenshot_path = await create_debug_screenshot(page, "login_check_error")
            add_process_screenshot(process_id, screenshot_path)
            return False

        # --- 3. Handle Address Selection ---
        print("Proceeding to address selection...")
        address_success = await handle_address_selection_api(process_id, page)
        if not address_success:
            print("Address selection failed.")
            # handle_address_selection_api should set ERROR status
            return False
        print("Address selection completed successfully.")

        # --- 4. Handle Order Summary (includes popup handling) ---
        print("Proceeding to order summary...")
        summary_success = await handle_order_summary_api(process_id, page)
        if not summary_success:
            print("Order summary failed.")
            # handle_order_summary_api should set ERROR status
            return False
        print("Order summary completed successfully.")

        # --- 5. Handle Payment ---
        print("Proceeding to payment...")
        payment_success = await handle_payment_api(process_id, page)
        if not payment_success:
            print("Payment handling failed.")
            # handle_payment_api should set ERROR status
            return False
        print("Payment details submitted successfully, proceeding to bank OTP.")
        # Status should be PAYMENT_NAVIGATION_COMPLETE or similar after handle_payment_api succeeds

        # --- 6. Handle Bank OTP ---
        print("Proceeding to Bank OTP handling...")
        # Use the Gemini handler directly as it's now the primary method
        otp_success = await handle_bank_otp_gemini(process_id, page)
        if not otp_success:
            print("Bank OTP handling failed.")
            # handle_bank_otp_gemini should set ERROR status
            return False
        print("Bank OTP handling completed successfully.")
        # handle_bank_otp_gemini should set COMPLETED status on success

        # --- 7. Final Check ---
        final_status = get_process_status(process_id)
        if final_status and final_status.get("stage") == "COMPLETED":
            print("Checkout process finished successfully.")
            return True
        else:
            print(f"Checkout process ended with unexpected status: {final_status.get('stage')}")
            if final_status and final_status.get("stage") != "ERROR": # Ensure error state if not completed
                update_process_status(process_id, "ERROR", f"Process ended unexpectedly after OTP step. Final Stage: {final_status.get('stage')}")
            return False

    except Exception as e:
        error_message = f"An critical error occurred in start_purchase_process: {str(e)}"
        print(error_message)
        # Ensure status is updated even for top-level errors
        if get_process_status(process_id).get("stage") != "ERROR":
             update_process_status(process_id, "ERROR", error_message)
        if page and not page.is_closed():
            try:
                screenshot_path = await create_debug_screenshot(page, "main_process_critical_exception")
                add_process_screenshot(process_id, screenshot_path)
            except Exception as ss_err:
                print(
                    f"Failed to take screenshot during critical exception handling: {ss_err}")
        return False
    finally:
        # Cleanup event lock if it exists
        if process_id in event_locks:
            if not event_locks[process_id].is_set():
                event_locks[process_id].set() # Ensure any waiting handlers are released
            del event_locks[process_id]
        print(f"start_purchase_process finished for {process_id}.")


async def terminate_process(process_id: str) -> bool:
    """Attempts to terminate a running checkout process."""
    # TODO: Implement the actual termination logic here.
    # This needs to find the asyncio task associated with process_id
    # (likely stored in or managed by checkout_process_manager)
    # and cancel it gracefully.

    process_data = active_processes.get(process_id)
    if not process_data:
        print(f"Terminate request for non-existent process: {process_id}")
        return False  # Process not found

    # Example: Check if process is in a cancellable state
    if process_data['stage'] in ["COMPLETED", "ERROR", "CANCELLED"]:
        print(
            f"Process {process_id} is already in a terminal state: {process_data['stage']}")
        return False  # Already finished or cancelled

    # --- Add cancellation logic here ---
    # e.g., find the task and call task.cancel()
    # Need access to how tasks are stored/managed by checkout_process_manager
    print(f"Placeholder: Requesting termination for process {process_id}")
    # Update status to indicate cancellation attempt
    update_process_status(process_id, "CANCELLED",
                          "Termination requested by user.")
    # ----------------------------------

    # Return True assuming the cancellation signal was sent.
    # The actual task cancellation might take time.
    return True

# State to Handler Mapping - Moved here after handlers are defined
# No longer needed for sequential flow, but kept for reference or future state checks
STATE_HANDLERS = {
    "LOGIN": handle_login_api,
    "ADDRESS": handle_address_selection_api,
    "ORDER_SUMMARY": handle_order_summary_api,
    "PAYMENT": handle_payment_api,
    # Consolidated Bank OTP handling
    "BANK_OTP": handle_bank_otp_gemini,
} 