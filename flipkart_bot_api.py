import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright, Page, TimeoutError, Response
import re
import json
from typing import Dict, List, Optional, Any, Union
import time
from datetime import datetime

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
    "BANK_OTP_REQUESTED": "Waiting for bank OTP",
    "COMPLETED": "Checkout process completed",
    "ERROR": "An error occurred during checkout",
    "CANCELLED": "Checkout process was cancelled"
}

# Event locks for synchronization
event_locks = {}


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


async def submit_bank_otp(process_id: str, otp: str) -> bool:
    """Submit bank OTP."""
    if process_id not in active_processes or active_processes[process_id]["stage"] != "BANK_OTP_REQUESTED":
        print(
            f"[submit_bank_otp] Process {process_id} not found or not in BANK_OTP_REQUESTED stage. Current stage: {active_processes.get(process_id, {}).get('stage')}")
        return False

    if process_id not in event_locks:
        return False

    # Store Bank OTP in process data
    update_process_status(process_id, "BANK_OTP_SUBMITTED", "Bank OTP submitted, processing", {
        "bank_otp": otp
    })

    # Set the event to resume the checkout process
    event_locks[process_id].set()
    return True

# Page state detection


async def detect_page_state(page: Page) -> str:
    """Detect the current page state using a structured approach for extensibility."""

    # Define page signatures in order of priority
    page_signatures = [
        # Highest priority first
        {
            "state_name": "PAYMENT",
            "selector": ':text-matches("Credit / Debit / ATM Card", "i") >> xpath=ancestor::*[self::label or self::div][1]',
            "context": "page"
        },
        {
            "state_name": "ORDER_SUMMARY",
            "selector": 'button:has-text("CONTINUE")',
            "context": "page"
        },
        {
            "state_name": "ADDRESS",
            "selector": 'label:has(input[name="address"])',
            "context": "page"
        },
        {
            "state_name": "LOGIN",
            "selector": "input[type='text'][autocomplete='off']",
            "context": "page"
        },
        # --- BANK OTP Checks --- (Add more specific checks before generic ones)
        # 1. Check for SBI specific OTP page (on main page)
        {
            "state_name": "BANK_OTP",
            "selector": 'input#otpValue[type="password"]',
            "context": "page"
        },
        # 2. Check for generic OTP in common iframes
        {
            "state_name": "BANK_OTP",
            "selector": 'input[type="password"], input[type="tel"], input[name*="otp" i], input[id*="otp" i]',
            "context": "iframe",
            "iframe_selectors": ['iframe[id*="card"]', 'iframe[name*="card"]', 'iframe[title*="3D Secure"]', 'iframe']
        },
         # 3. Check for generic OTP on main page (fallback)
        {
            "state_name": "BANK_OTP",
            "selector": 'input[type="password"], input[type="tel"], input[name*="otp" i], input[id*="otp" i]',
            "context": "page"
        },
        # Add signatures for other page types or OTP variations here
    ]

    # Timeout for each individual visibility check (in milliseconds)
    check_timeout = 3000
    # Timeout for checking if an iframe itself is visible (shorter)
    iframe_visible_timeout = 500

    print("--- Detecting Page State ---")
    for signature in page_signatures:
        state_name = signature["state_name"]
        selector = signature["selector"]
        context_type = signature.get("context", "page")
        iframe_selectors = signature.get("iframe_selectors")
        search_context_msg = ""

        # print(f"Checking for state: {state_name}...") # Optional: More verbose logging

        try:
            if context_type == "page":
                search_context_msg = "main page"
                target_locator = page.locator(selector).first
                if await target_locator.is_visible(timeout=check_timeout):
                    print(f"Detected state: {state_name} (in {search_context_msg})")
                    return state_name

            elif context_type == "iframe" and iframe_selectors:
                search_context_msg = "iframes"
                found_in_iframe = False
                for iframe_selector in iframe_selectors:
                    # print(f"  Checking iframe: {iframe_selector}") # Optional
                    try:
                        iframe = page.locator(iframe_selector).first
                        # Quick check if iframe is present and visible
                        if await iframe.is_visible(timeout=iframe_visible_timeout):
                            # print(f"  Iframe {iframe_selector} is visible. Checking selector inside...") # Optional
                            frame_context = iframe.frame_locator()
                            target_locator = frame_context.locator(selector).first
                            # Check if the target selector is visible within this iframe
                            if await target_locator.is_visible(timeout=check_timeout):
                                search_context_msg = f"iframe ('{iframe_selector}')"
                                print(f"Detected state: {state_name} (in {search_context_msg})")
                                return state_name # Found it, exit early
                        # else: # Optional: log iframe not visible quickly
                            # print(f"  Iframe {iframe_selector} not visible quickly.")
                    except Exception as iframe_err:
                        # Error locating/checking this specific iframe, try the next one
                        # print(f"  Error checking iframe {iframe_selector}: {iframe_err}")
                        continue
                # If loop finishes without returning, it wasn't found in any specified iframe

            else:
                 print(f"Warning: Skipping signature for {state_name} due to invalid context/config.")
                 continue

        except Exception as e:
            # Error during the is_visible check for this signature, move to the next
            print(f"  Visibility check failed for state '{state_name}' (context: {search_context_msg}, selector: '{selector}'). Error: {e}")
            pass # Continue to the next signature

    # If no state matched after checking all signatures
    print("Detected state: UNKNOWN (no signatures matched)")
    return "UNKNOWN"

# Navigation and core checkout functions


async def navigate_and_buy(process_id: str, page: Page, url: str) -> bool:
    """Navigate to product page and click Buy Now."""
    try:
        # Navigate to product URL
        await page.goto(url, wait_until='networkidle', timeout=45000)
        await page.wait_for_timeout(3000)  # Add a 3-second delay

        # Take screenshot
        screenshot_path = await create_debug_screenshot(page, "product_page")
        add_process_screenshot(process_id, screenshot_path)

        # Try to extract product title
        try:
            title_locator = page.locator('span.B_NuCI, h1 span._35KyD6')
            if await title_locator.first.is_visible(timeout=10000):
                product_title = await title_locator.first.text_content()
                update_process_status(process_id, "NAVIGATING", "Product page loaded", {
                    "product_title": product_title.strip() if product_title else "Unknown"
                })
        except:
            pass

        # Click Buy Now button
        update_process_status(process_id, "CLICKING_BUY_NOW",
                              "Attempting to click Buy Now")

        buy_now_button = page.locator('*:text-matches("Buy now", "i")')
        await buy_now_button.wait_for(state='visible', timeout=20000)

        # Take screenshot before clicking
        screenshot_path = await create_debug_screenshot(page, "before_buy_now_click")
        add_process_screenshot(process_id, screenshot_path)

        await buy_now_button.click()

        # Wait for navigation
        await page.wait_for_load_state('networkidle', timeout=25000)

        # Take screenshot after clicking
        screenshot_path = await create_debug_screenshot(page, "after_buy_now_click")
        add_process_screenshot(process_id, screenshot_path)

        return True

    except Exception as e:
        update_process_status(process_id, "ERROR",
                              f"Failed to navigate or click Buy Now: {str(e)}")

        # Try to take screenshot if page is still available
        try:
            if not page.is_closed():
                screenshot_path = await create_debug_screenshot(page, "navigation_error")
                add_process_screenshot(process_id, screenshot_path)
        except:
            pass

        return False

# Main process orchestrator


async def checkout_process_manager(process_id: str, product_url: str, session_path: Optional[Path] = None):
    """Main function to manage the checkout process."""
    update_process_status(process_id, "INITIALIZING",
                          "Initializing browser and session")

    try:
        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch(headless=False)

            # Create or load context based on session
            context = None

            if session_path and session_path.exists():
                update_process_status(
                    process_id, "INITIALIZING", f"Loading session from {session_path}")
                context = await browser.new_context(storage_state=session_path)
            else:
                update_process_status(
                    process_id, "INITIALIZING", "Creating new browser context")
                context = await browser.new_context()

            # Run the checkout process
            result = await start_purchase_process(process_id, product_url, context, session_path)

            # Save session state if path was provided
            if session_path:
                try:
                    await context.storage_state(path=session_path)
                    update_process_status(
                        active_processes[process_id]["stage"],
                        f"{active_processes[process_id]['message']} (Session saved to {session_path})"
                    )
                except Exception as e:
                    print(f"Error saving session: {e}")

            # Keep browser open for inspection
            await asyncio.sleep(3600)  # Keep browser open for 1 hour

            # Close browser when done
            await browser.close()

    except Exception as e:
        update_process_status(process_id, "ERROR",
                              f"Process manager error: {str(e)}")

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
    """Handle the order summary page."""
    # Selector
    continue_button_selector = 'button:has-text("CONTINUE")'

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
            total_amount_selector = 'span:text-matches("₹.*") >> nth=0'
            total_amount = await page.locator(total_amount_selector).text_content()

            update_process_status(process_id, "ORDER_SUMMARY", "Processing order summary", {
                "total_amount": total_amount.strip() if total_amount else "Unknown"
            })
        except:
            pass

        # Locate and click the CONTINUE button
        continue_button = page.locator(continue_button_selector).first
        await continue_button.wait_for(state='visible', timeout=15000)

        # Check if button is enabled
        if not await continue_button.is_enabled(timeout=1000):
            await page.wait_for_timeout(3000)  # Extra wait

        await continue_button.click()

        # Wait for the next page to load
        await page.wait_for_load_state('networkidle', timeout=30000)

        # Take screenshot after clicking CONTINUE
        screenshot_path = await create_debug_screenshot(page, "after_summary_continue_click")
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
        card_number_input = context_locator.locator(card_number_input_selector).first
        await card_number_input.wait_for(state='visible', timeout=30000)

        # Determine expiry format
        expiry_input_type = 'combined' # Assume combined input MM / YY first
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
                print("Warning: Could not definitively detect expiry input format. Assuming combined MM / YY.")
                expiry_input_type = 'combined'

        # Update process status requesting payment details
        update_process_status(process_id, "PAYMENT_REQUESTED", "Please provide payment details via API", {
            "expiry_input_type": expiry_input_type # Inform client of expected format
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
                    print(f"Failed to fill expiry even with fallback: {fill_err}")

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
                print("Timeout waiting for payment form (form#cards). Cannot proceed reliably.")
                update_process_status(process_id, "ERROR", "Payment form (form#cards) not found.")
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
                pay_button_locator = payment_form.locator(f'button:text-matches("{pay_button_selector_primary_regex}", "i")').first
                # Only wait for visible, not enabled (like original bot)
                await pay_button_locator.wait_for(state='visible', timeout=25000)
                print("PAY button located and visible within form.")
                pay_button_to_click = pay_button_locator

            except TimeoutError as te:
                print(f"Timeout waiting for PAY button visibility within form: {te}")
                # Optional: Could add a fallback search outside the form here if needed
                update_process_status(
                    process_id, "ERROR", f"Timeout waiting for PAY button visibility: {str(te)}")
                screenshot_path_error = await create_debug_screenshot(page, "pay_button_locate_timeout")
                add_process_screenshot(process_id, screenshot_path_error)
                return False
            except Exception as e:
                 print(f"Error locating PAY button: {e}")
                 update_process_status(process_id, "ERROR", f"Error locating PAY button: {str(e)}")
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
                except Exception as click_err:
                    print(f"Click failed: {click_err}. Attempting force click...")
                    try:
                        await pay_button_to_click.click(force=True, timeout=10000)
                        print("Clicked PAY button (force=True).")
                    except Exception as force_click_err:
                        print(f"Force click also failed: {force_click_err}")
                        update_process_status(
                            process_id, "ERROR", f"Failed to click PAY button (standard and force): {str(force_click_err)}")
                        screenshot_path_click_error = await create_debug_screenshot(page, "pay_button_click_error")
                        add_process_screenshot(process_id, screenshot_path_click_error)
                        return False
            else:
                # This case should ideally be caught by the try/except above
                print("Error: Pay button locator was not assigned.")
                update_process_status(process_id, "ERROR", "Pay button locator was None before click attempt.")
                return False

            # --- Handle potential 'Save Card' popup (Quick attempt) ---
            await page.wait_for_timeout(500) # Brief pause after pay click
            try:
                maybe_later_selector = 'button:has-text("Maybe later")'
                maybe_later_button = page.locator(maybe_later_selector).first
                print("Quick check for 'Save Card' popup (Maybe later button)...")
                # Use a very short timeout - just click if immediately visible
                await maybe_later_button.click(timeout=2000)
                print("Clicked 'Maybe later' button during quick check.")
                await page.wait_for_timeout(500) # Small pause after clicking popup
            except TimeoutError:
                print("'Maybe later' button not immediately visible or clickable. Proceeding...")
            except Exception as e:
                print(f"Error during quick check/click for 'Maybe later': {e}. Proceeding...")
            # --- End Quick Popup Handling ---

            # Wait for navigation to bank OTP page (Main wait)
            print("Waiting for navigation after payment submission (load state)...")
            await page.wait_for_load_state('load', timeout=90000)
            print(f"Navigated after payment. Current URL: {page.url}")

            # Take screenshot after payment submission
            screenshot_path = await create_debug_screenshot(page, "after_payment_submission")
            add_process_screenshot(process_id, screenshot_path)

            update_process_status(
                process_id, "PAYMENT_COMPLETED", "Payment initiated successfully")
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


async def handle_bank_otp_api(process_id: str, page: Page):
    """Handle the bank OTP verification page."""
    # Selectors (Assume elements are on the main page)
    otp_input_selector = 'input[type="password"], input[type="tel"], input[name*="otp" i], input[id*="otp" i], input:near(:text("Enter your code"))'
    confirm_button_selector = 'button:text-matches("CONFIRM|SUBMIT|PAY", "i"), input[type="submit"]:text-matches("CONFIRM|SUBMIT|PAY", "i")'
    # Removed iframe selectors

    try:
        # Take screenshot of bank OTP page
        screenshot_path = await create_debug_screenshot(page, "bank_otp_page")
        add_process_screenshot(process_id, screenshot_path)

        # Use page context directly
        context_locator = page
        print("Using context: page (iframe logic removed)")

        # Wait for OTP input field
        otp_input = context_locator.locator(otp_input_selector).first
        await otp_input.wait_for(state='visible', timeout=45000)

        # Update process status requesting bank OTP
        update_process_status(process_id, "BANK_OTP_REQUESTED",
                              "Please provide bank OTP via API")

        # Wait for bank OTP via API
        if process_id not in event_locks:
            event_locks[process_id] = asyncio.Event()

        await event_locks[process_id].wait()
        event_locks[process_id].clear()  # Reset for next wait

        # Get bank OTP from process data
        if "bank_otp" in active_processes[process_id]["data"]:
            bank_otp = active_processes[process_id]["data"]["bank_otp"]

            # Fill OTP
            await otp_input.fill(bank_otp)
            await page.wait_for_timeout(2000)

            # Take screenshot after filling OTP
            screenshot_path = await create_debug_screenshot(page, "after_bank_otp_fill")
            add_process_screenshot(process_id, screenshot_path)

            # Click confirm button
            confirm_button = context_locator.locator(
                confirm_button_selector).first
            await confirm_button.click()

            # Wait for final confirmation/redirect
            await page.wait_for_load_state('networkidle', timeout=90000)

            # Take screenshot of final confirmation
            screenshot_path = await create_debug_screenshot(page, "final_confirmation")
            add_process_screenshot(process_id, screenshot_path)

            update_process_status(process_id, "COMPLETED",
                                  "Order completed successfully")
            return True
        else:
            update_process_status(process_id, "ERROR",
                                  "Bank OTP missing from process data")
            return False

    except Exception as e:
        update_process_status(process_id, "ERROR",
                              f"Error during bank OTP processing: {str(e)}")
        screenshot_path = await create_debug_screenshot(page, "bank_otp_error")
        add_process_screenshot(process_id, screenshot_path)
        return False

# Add the start_purchase_process function that was referenced but not defined


async def start_purchase_process(
    process_id: str,
    product_url: str,
    browser_context,
    session_path: Optional[Path] = None
) -> bool:
    """Start the purchase process and handle all stages."""
    try:
        page = await browser_context.new_page()

        # Create process-specific event for waiting on user input
        if process_id not in event_locks:
            event_locks[process_id] = asyncio.Event()

        # Navigate and buy
        update_process_status(process_id, "NAVIGATING", "Navigating to product page", {
            "product_url": product_url
        })

        navigation_success = await navigate_and_buy(process_id, page, product_url)
        if not navigation_success:
            update_process_status(
                process_id, "ERROR", "Failed to navigate to product or click Buy Now")
            return False

        # Check page state and handle each state
        current_state = await detect_page_state(page)

        while current_state not in ["COMPLETED", "ERROR"]:
            if current_state == "LOGIN":
                await handle_login_api(process_id, page)
                current_state = await detect_page_state(page)

            elif current_state == "ADDRESS":
                await handle_address_selection_api(process_id, page)
                current_state = await detect_page_state(page)

            elif current_state == "ORDER_SUMMARY":
                await handle_order_summary_api(process_id, page)
                current_state = await detect_page_state(page)

            elif current_state == "PAYMENT":
                await handle_payment_api(process_id, page)
                current_state = await detect_page_state(page)

            elif current_state == "BANK_OTP":
                await handle_bank_otp_api(process_id, page)
                current_state = await detect_page_state(page)

            elif current_state == "UNKNOWN":
                # Take screenshot for debugging
                screenshot_path = await create_debug_screenshot(page, "unknown_state")
                add_process_screenshot(process_id, screenshot_path)

                update_process_status(
                    process_id, "ERROR", "Cannot determine page state")
                return False

            # Sleep briefly to prevent CPU hogging
            await asyncio.sleep(1)

        # Final status update
        if current_state == "COMPLETED":
            update_process_status(process_id, "COMPLETED",
                                  "Checkout process completed successfully")
            return True
        else:
            return False

    except Exception as e:
        update_process_status(process_id, "ERROR",
                              f"An error occurred: {str(e)}")
        return False


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
