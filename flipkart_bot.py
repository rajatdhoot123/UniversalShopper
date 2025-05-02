import asyncio
import os # Import os for directory operations
from pathlib import Path # Import Path
from playwright.async_api import async_playwright, Page, TimeoutError, Response
import re # For sanitizing filename AND regex matching


async def handle_login(page: Page):
    """Handles the Flipkart login process with OTP retry based on API response."""
    print("Login required. Handling login...")

    # --- Selectors ---
    phone_input_selector = "input[type='text'][autocomplete='off']"
    continue_button_selector = "button:has-text('CONTINUE')"
    otp_input_selector = "input[type='text'][maxlength='6']"
    final_login_button_selector = "button:has-text('LOGIN'), button:has-text('SIGNUP')"
    otp_api_endpoint = '/api/1/user/login/otp' # Target API endpoint

    max_otp_attempts = 3
    otp_attempt = 0
    listener_active = False # Track if listener is currently active
    otp_response_future = None # Initialize here

    async def intercept_response(response: Response):
        """Callback function to intercept and check the OTP API response."""
        nonlocal listener_active
        # Check if the URL matches the target endpoint and we expect a response
        if otp_api_endpoint in response.url and otp_response_future and not otp_response_future.done():
            print(f"Intercepted OTP API response from: {response.url}")
            try:
                response_json = await response.json()
                print(f"API Response Body: {response_json}")

                # Check for SUCCESS based on STATUS_CODE
                if response_json.get("STATUS_CODE") == 200:
                    print("API indicates OTP Success (STATUS_CODE 200).")
                    otp_response_future.set_result(True)
                # Check for specific INCORRECT OTP error
                elif response_json.get("errorCode") == "LOGIN_1008":
                    print(f"API indicates OTP Failure: {response_json.get('message', 'OTP Incorrect')}")
                    otp_response_future.set_result("OTP_INCORRECT")
                # Handle other API errors
                else:
                    error_message = response_json.get("errors", [{}])[0].get("message", "Unknown API Error")
                    print(f"API indicates generic OTP Failure: {error_message}")
                    otp_response_future.set_result(False)

            except Exception as e:
                print(f"Error parsing OTP API response: {e}")
                if not otp_response_future.done():
                     otp_response_future.set_result(False) # Assume failure on parse error
            finally:
                # Clean up listener ONLY after processing the target response
                if listener_active:
                    try:
                        page.remove_listener("response", intercept_response)
                        listener_active = False # Mark listener as inactive
                        print("Removed OTP response listener.")
                    except Exception as remove_error:
                        print(f"Error removing listener: {remove_error}")

    try:
        # --- Enter Phone Number (only once) ---
        phone_number = input("Please enter your Flipkart Email/Mobile number: ")
        print(f"Entering number: {phone_number}")
        phone_input = page.locator(phone_input_selector).first
        await phone_input.wait_for(state='visible', timeout=10000)
        await phone_input.fill(phone_number)

        print("Clicking CONTINUE...")
        continue_button = page.locator(continue_button_selector).first
        await continue_button.wait_for(state='visible', timeout=5000)
        await continue_button.click()

        print("Waiting for OTP input field...")
        otp_input = page.locator(otp_input_selector).first
        await otp_input.wait_for(state='visible', timeout=15000)

        # --- OTP Entry and Verification Loop ---
        while otp_attempt < max_otp_attempts:
            otp_attempt += 1
            print(f"--- OTP Attempt {otp_attempt}/{max_otp_attempts} ---")

            # --- Enter OTP ---
            otp = input(f"Please enter the OTP received (Attempt {otp_attempt}): ")
            print(f"Entering OTP: {otp}")
            await otp_input.fill("") # Clear previous OTP first
            await otp_input.fill(otp)

            # --- Setup API Listener & Click LOGIN/SIGNUP ---
            otp_response_future = asyncio.Future() # Create a new Future for this attempt
            print("Setting up API response listener...")
            page.on("response", intercept_response)
            listener_active = True # Mark listener as active

            print("Locating final LOGIN/SIGNUP button...")
            final_button = page.locator(final_login_button_selector).first
            await final_button.wait_for(state='visible', timeout=10000)
            print("Clicking LOGIN/SIGNUP button...")
            await final_button.click()

            # --- Wait for API Response Result ---
            print(f"Waiting for OTP API response (timeout 20s)...")
            login_result = None
            try:
                login_result = await asyncio.wait_for(otp_response_future, timeout=20.0)
            except asyncio.TimeoutError:
                print("Login failed: Timed out waiting for OTP API response.")
                raise Exception("Login failed: Timeout waiting for API response.")
            finally:
                # Ensure listener is removed if it wasn't by the callback (e.g., timeout)
                if listener_active:
                    try:
                        page.remove_listener("response", intercept_response)
                        listener_active = False
                        print("Cleaned up listener after wait/timeout.")
                    except Exception:
                        pass # Ignore error if already removed

            # --- Process API Result ---
            if login_result is True:
                print("Login successful (confirmed by API response). Proceeding...")
                await page.wait_for_timeout(1000) # Small wait for UI update
                break # Exit the OTP loop
            elif login_result == "OTP_INCORRECT":
                print("OTP was incorrect.")
                if otp_attempt >= max_otp_attempts:
                    print("Maximum OTP attempts reached.")
                    raise Exception("Login failed: Maximum OTP attempts reached.")
                else:
                    print("Please try entering the OTP again.")
                    # Loop continues
            else: # login_result is False (generic API error)
                print("Login failed (API response indicated generic failure).")
                raise Exception("Login failed based on API response.")

        # If loop finishes without breaking (shouldn't happen with current logic, but defense)
        else:
             if otp_attempt >= max_otp_attempts:
                 print("Login failed after maximum attempts.")
                 raise Exception("Login failed: Maximum OTP attempts reached finally.")

    except TimeoutError as e:
        print(f"Login failed: Timed out waiting for a UI element: {e}")
        screenshot_path = "login_timeout_error.png"
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        raise # Re-raise exception
    except Exception as e:
        print(f"Login failed: An error occurred: {e}")
        screenshot_path = "login_other_error.png"
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        raise # Re-raise exception
    finally:
        # Final cleanup: ensure listener is off if an exception occurred mid-process
        if listener_active:
            try:
                page.remove_listener("response", intercept_response)
                print("Cleaned up listener in final exception handler.")
            except Exception:
                pass # Ignore removal error


async def select_delivery_address(page: Page, debug_image_dir: Path):
    """Finds delivery addresses, presents them to the user, selects the chosen one, and clicks 'Deliver Here'."""
    print("Scanning for available delivery addresses...")

    # --- Try to reveal all addresses first ---
    view_all_selector = 'div:text-matches("View all \\d+ addresses", "i")' # Case-insensitive regex
    try:
        view_all_button = page.locator(view_all_selector).first
        if await view_all_button.is_visible(timeout=3000):
            print("Found 'View all addresses' button. Clicking it...")
            await view_all_button.click()
            await page.wait_for_timeout(1500) # Wait for addresses to potentially load
            print("'View all addresses' clicked.")
        else:
            print("'View all addresses' button not visible or not found. Proceeding...")
    except TimeoutError:
         print("Timed out looking for 'View all addresses' button. Proceeding...")
    except Exception as e:
        print(f"Error trying to click 'View all addresses': {e}. Proceeding...")
    # --- End reveal all addresses ---

    address_container_selector = 'label:has(input[name="address"])'
    name_selector_relative = 'span:has-text("HOME") >> xpath=preceding-sibling::span[1]'
    name_selector_fallback = 'p > span:first-child' # Fallback if HOME tag not present or different
    address_text_selector_relative = 'p + span'
    # Note: We no longer look for the deliver button inside each label initially

    addresses = []
    try:
        address_labels = await page.locator(address_container_selector).all()
        print(f"Found {len(address_labels)} potential address blocks.")

        if not address_labels:
             print("No address blocks found using the selector.")
             raise Exception("No address blocks found.")

        for i, label in enumerate(address_labels):
            name = "Name not found"
            address_text = "Address not found"

            try:
                # Try finding name relative to HOME tag
                name_element = label.locator(name_selector_relative)
                if await name_element.is_visible(timeout=500):
                     name = await name_element.text_content()
                else:
                    # Try fallback name selector
                    name_element_fallback = label.locator(name_selector_fallback)
                    if await name_element_fallback.is_visible(timeout=500):
                        name = await name_element_fallback.text_content()

                # Find address text
                address_element = label.locator(address_text_selector_relative)
                if await address_element.is_visible(timeout=500):
                     address_text = await address_element.text_content()
                     address_text = ' '.join(address_text.split()) # Clean whitespace

                print(f"  Address {i+1}: Found Name='{name.strip() if name else 'N/A'}'")

                # Store the label locator itself
                addresses.append({
                    "name": name.strip() if name else "N/A",
                    "text": address_text.strip() if address_text else "N/A",
                    "label_locator": label # Store the locator for the entire label
                })

            except Exception as e:
                print(f"  Error processing address block {i+1}: {e}")
                # Store placeholder if processing failed but block was found
                addresses.append({
                    "name": f"Error processing {i+1}",
                    "text": str(e),
                    "label_locator": label
                })

    except Exception as e:
        print(f"Error finding address blocks: {e}")
        screenshot_path = debug_image_dir / 'error_finding_addresses.png'
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        raise Exception("Could not retrieve delivery addresses.")

    if not addresses:
        # This case should ideally be caught above, but added as safety
        print("No addresses were processed or found.")
        raise Exception("No addresses found on the page.")

    # Display choices to user
    print("\nPlease select a delivery address:")
    for idx, addr in enumerate(addresses):
        print(f"  [{idx + 1}] {addr['name']}")
        print(f"      {addr['text']}")

    # Get user choice
    while True:
        try:
            choice_str = input(f"Enter the number of the address to use (1-{len(addresses)}): ")
            choice_idx = int(choice_str) - 1
            if 0 <= choice_idx < len(addresses):
                selected_address = addresses[choice_idx]
                print(f"Selected address {choice_str}: {selected_address['name']}")
                break
            else:
                print("Invalid number. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")

    # Click the chosen label to select the address
    try:
        print(f"Selecting address {choice_str} by clicking its label...")
        await selected_address['label_locator'].click()
        await page.wait_for_timeout(1000) # Wait for UI update (e.g., button appearance)
        print("Address label clicked.")

    except Exception as e:
        print(f"Error clicking the address label {choice_str}: {e}")
        screenshot_path = debug_image_dir / 'error_clicking_label.png'
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        raise Exception(f"Failed to select address {choice_str}.")

    # Now find and click the (hopefully single) 'Deliver Here' button
    try:
        deliver_button_selector = 'button:has-text("Deliver Here")'
        print(f"Locating the 'Deliver Here' button after selection...")
        deliver_button = page.locator(deliver_button_selector).first
        # Adding a slightly longer wait specifically for the button after label click
        await deliver_button.wait_for(state='visible', timeout=10000)
        print(f"Clicking 'Deliver Here' button...")
        await deliver_button.click()

        print("Clicked 'Deliver Here'. Waiting for next page load...")
        # Wait for potential navigation or UI update
        await page.wait_for_load_state('networkidle', timeout=20000)
        print(f"Page loaded state reached. Current URL: {page.url}")
        # TODO: Add checks for the expected next page (e.g., order summary)

    except TimeoutError:
        print("Timeout waiting for 'Deliver Here' button to be visible or page load after clicking.")
        screenshot_path = debug_image_dir / 'deliver_here_timeout.png'
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        raise Exception("Timeout after selecting address, waiting for 'Deliver Here' or next page.")
    except Exception as e:
        print(f"Error finding or clicking 'Deliver Here' button: {e}")
        screenshot_path = debug_image_dir / 'deliver_here_error.png'
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        raise Exception("Failed to click 'Deliver Here' or process the next step.")

    print("Address selection completed.")


async def handle_payment(page: Page, debug_image_dir: Path):
    """Handles the payment page, selecting card payment and filling details, attempting to handle iframes and UI variations."""
    print("\nHandling Payment page...")

    # Selectors (Updated for UI variations)
    card_option_selector_locator = page.locator(':text-matches("Credit / Debit / ATM Card", "i")').locator('xpath=ancestor::*[self::label or self::div][1]')
    card_number_input_selector = 'input[name="cardNumber"], input[autocomplete="cc-number"]' # OR for card number
    # Old UI selectors
    month_select_selector = 'select[name="month"]'
    year_select_selector = 'select[name="year"]'
    # New UI selector
    valid_thru_input_selector = 'input[autocomplete="cc-exp"]'
    # OR for CVV
    cvv_input_selector = 'input[name="cvv"], input#cvv-input'
    # Updated Pay button regex for flexibility with spacing
    pay_button_selector = 'button:text-matches("PAY\\s*₹\\d+\\s*", "i")'
    iframe_selector = 'iframe'

    try:
        # 1. Select Credit/Debit Card Option
        print("Selecting 'Credit / Debit / ATM Card' option (generic method)...")
        card_option_container = card_option_selector_locator.first
        await card_option_container.wait_for(state='visible', timeout=15000)
        await card_option_container.click()
        print("Card option selected.")
        await page.wait_for_timeout(2000) # Increased fixed wait to 2 seconds

        # 2. Determine Context (iframe or page)
        print("Attempting to locate payment fields (checking for iframe)...")
        payment_frame_locator = None
        context_locator = page # Default to page context

        try:
            iframe_element = page.locator(iframe_selector).first
            await iframe_element.wait_for(state='visible', timeout=5000) # Quick check for iframe
            payment_frame_locator = iframe_element.frame_locator()
            context_locator = payment_frame_locator # Switch context to iframe
            print("Found potential payment iframe. Searching within frame.")
        except TimeoutError:
            print("No iframe detected quickly or iframe not visible. Searching within main page.")
        except Exception as e:
             print(f"Error detecting iframe: {e}. Searching within main page.")

        # 3. Wait for card number field to be visible (trigger for form appearance)
        print(f"Waiting for card number field within {'iframe' if payment_frame_locator else 'main page'}...")
        card_number_input = context_locator.locator(card_number_input_selector).first
        await card_number_input.wait_for(state='visible', timeout=30000)
        print("Card number field is visible.")

        # 4. Get Card Details from User
        print("\n--- Enter Card Details --- (These will be filled directly and not stored)")
        card_number = input("Enter Card Number: ").strip()
        cvv = input("Enter CVV: ").strip()

        # Determine expiry input method
        is_new_expiry_format = False
        try:
            await context_locator.locator(valid_thru_input_selector).wait_for(state='visible', timeout=1000) # Quick check
            is_new_expiry_format = True
            print("Detected single MM / YY expiry input field.")
        except TimeoutError:
            print("Detected separate MM and YY expiry dropdowns.")

        expiry_month = ""
        expiry_year = ""
        expiry_combined = ""

        if is_new_expiry_format:
            expiry_combined = input("Enter Expiry Date (MM / YY format, e.g., 05 / 28): ").strip()
            # Basic validation for combined format
            if not re.match(r"^\d{2}\s*/\s*\d{2}$", expiry_combined):
                 raise ValueError("Invalid Expiry Date format (should be MM / YY).")
        else:
            expiry_month = input("Enter Expiry Month (MM): ").strip()
            expiry_year = input("Enter Expiry Year (YY): ").strip()
            # Basic validation for separate format
            if not (len(expiry_month) == 2 and expiry_month.isdigit() and 1 <= int(expiry_month) <= 12):
                 raise ValueError("Invalid Expiry Month format (should be MM).")
            if not (len(expiry_year) == 2 and expiry_year.isdigit()):
                 raise ValueError("Invalid Expiry Year format (should be YY).")

        print("-------------------------")

        # 5. Fill Card Details (based on determined format)
        print("Filling card details...")
        # Fill Card Number and CVV (using OR selectors)
        await card_number_input.fill(card_number) # Already located
        await context_locator.locator(cvv_input_selector).fill(cvv)
        await page.wait_for_timeout(500) # Small pause after CVV fill

        # Fill Expiry Date
        if is_new_expiry_format:
            print(f"Filling combined expiry: {expiry_combined}")
            await context_locator.locator(valid_thru_input_selector).fill(expiry_combined)
        else:
            print(f"Filling separate expiry: MM={expiry_month}, YY={expiry_year}")
            await context_locator.locator(month_select_selector).select_option(value=expiry_month)
            await context_locator.locator(year_select_selector).select_option(value=expiry_year)

        await page.wait_for_timeout(500) # Small pause after expiry fill
        print("Card details filled.")

        # Add a pause before looking for the pay button
        print("Pausing for 2 seconds before locating Pay button...")
        await page.wait_for_timeout(2000)

        # Find the payment form first to scope the search
        print("Locating payment form (form#cards)...")
        payment_form = context_locator.locator('form#cards')
        # Ensure the form itself is present before searching within it
        await payment_form.wait_for(state='attached', timeout=10000)

        # 6. Locate and Click Pay Button (Combined selector with regex)
        # Moved locator definition down, removed explicit waits for visible/enabled
        # print("Locating PAY button using combined selector + regex within the form...")
        pay_button_regex_text = r"Pay\\s+₹\\d+\\s*" # Using raw string and adjusted slashes

        # Add screenshot before waiting/clicking
        print("Taking screenshot before final Pay button interaction...")
        screenshot_path = debug_image_dir / "before_pay_button_final_attempt.png"
        await page.screenshot(path=screenshot_path)

        # Wait briefly for UI to potentially settle after fills
        print("Pausing briefly before locating and clicking Pay button...")
        await page.wait_for_timeout(3000) # Increased pause before final attempt

        print("Locating and clicking PAY button...")
        # Re-locate the button right before clicking to get the current element
        pay_button = context_locator.locator(f'form#cards button:text-matches("{pay_button_regex_text}", "i")').first
        await pay_button.click()

        print("PAY button clicked. Checking for 'Save Card' popup...")
        # Temporarily removed 'Save Card' popup handling

        # --- Re-add Handle potential 'Save Card' Popup --- 
        maybe_later_selector = 'button:has-text("Maybe later")'
        try:
            maybe_later_button = page.locator(maybe_later_selector).first
            print("Waiting for 'Maybe later' button on popup (max 10s)...")
            await maybe_later_button.wait_for(state='visible', timeout=10000)
            print("Found 'Maybe later' button. Clicking it...")
            await maybe_later_button.click()
            print("'Maybe later' clicked.")
            # Add a small pause after clicking popup button
            await page.wait_for_timeout(1000)
        except TimeoutError:
            print("'Save Card' popup/Maybe later button not detected within timeout. Proceeding...")
        except Exception as e:
            print(f"Error handling 'Save Card' popup: {e}. Proceeding...")
        # --- End Popup Handling ---

        # 7. Wait for next page/state (OTP/Confirmation) using wait_for_load_state
        print("Waiting for navigation to next step (OTP/Confirmation)...")
        # Increased timeout for potential bank redirects
        await page.wait_for_load_state('load', timeout=90000) # Wait for load state after navigation
        print(f"Navigated to next step. Current URL: {page.url}")
        print("Payment processing initiated. Further steps (like OTP) may be required manually or need additional automation.")
        # TODO: Add potential OTP handling if desired/possible

    except TimeoutError as e:
        print(f"Timeout during payment processing: {e}")
        screenshot_path = debug_image_dir / 'payment_timeout_error.png'
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        raise Exception("Timeout occurred during payment.")
    except ValueError as e:
        print(f"Input Error: {e}")
        raise # Reraise validation error
    except Exception as e:
        print(f"An error occurred during payment: {e}")
        screenshot_path = debug_image_dir / "payment_error.png"
        try:
            await page.screenshot(path=screenshot_path)
            print(f"Screenshot saved to {screenshot_path}")
        except Exception as screen_err:
            print(f"Could not save error screenshot: {screen_err}")
        raise Exception("Failed to process payment.")


async def handle_bank_otp(page: Page, debug_image_dir: Path):
    """Handles the bank's OTP verification page, often within an iframe."""
    print("\nHandling Bank OTP page...")

    # Selectors
    iframe_selectors = ['iframe[id*="card"]', 'iframe[name*="card"]', 'iframe[title*="3D Secure"]' , 'iframe'] # Common iframe patterns
    otp_input_selector = 'input[type="password"], input[type="tel"], input[name*="otp" i], input[id*="otp" i], input:near(:text("Enter your code"))' # Common OTP input selectors
    confirm_button_selector = 'button:text-matches("CONFIRM|SUBMIT|PAY", "i"), input[type="submit"]:text-matches("CONFIRM|SUBMIT|PAY", "i")'

    otp_frame = None
    context_locator = page # Default to page context

    # Try to find the OTP iframe
    print("Checking for OTP iframe...")
    for i, selector in enumerate(iframe_selectors):
        try:
            iframe_element = page.locator(selector).first
            await iframe_element.wait_for(state='visible', timeout=2000) # Quick check
            otp_frame = iframe_element.frame_locator()
            context_locator = otp_frame
            print(f"Found potential OTP iframe using selector {i+1}: '{selector}'. Searching within frame.")
            break # Found it
        except TimeoutError:
            print(f"Selector {i+1} ('{selector}') did not find a visible iframe quickly.")
            continue
        except Exception as e:
             print(f"Error checking iframe selector {i+1} ('{selector}'): {e}")
             continue
    else:
        print("No specific iframe detected quickly or iframe not visible. Searching within main page.")

    # Wait for OTP input and fill
    try:
        print(f"Waiting for OTP input field within {'iframe' if otp_frame else 'main page'}...")
        otp_input = context_locator.locator(otp_input_selector).first
        await otp_input.wait_for(state='visible', timeout=45000) # Longer wait as OTP pages can be slow
        print("OTP input field visible.")

        otp = input("Please enter the Bank OTP received: ").strip()
        print(f"Filling OTP...")
        await otp_input.fill(otp)

        # Add screenshot before waiting for confirm
        print("Taking screenshot before final CONFIRM button interaction...")
        screenshot_path = debug_image_dir / "before_confirm_button_final_attempt.png"
        await page.screenshot(path=screenshot_path)

        # Add a pause after filling OTP before locating/clicking confirm
        print("Pausing briefly after OTP fill...")
        await page.wait_for_timeout(2000) # Pause for 2 seconds

        # Locate and click Confirm - Re-locate right before click, remove explicit waits
        print("Locating and clicking CONFIRM button...")
        confirm_button_selector = 'button:text-matches("CONFIRM|SUBMIT|PAY", "i"), input[type="submit"]:text-matches("CONFIRM|SUBMIT|PAY", "i")' # Re-define selector here for clarity or reuse from top
        confirm_button = context_locator.locator(confirm_button_selector).first
        await confirm_button.click()

        # Wait for final confirmation/redirect
        print("CONFIRM clicked. Waiting for final confirmation page or redirect...")
        # Use networkidle here as the final page might be simple
        await page.wait_for_load_state('networkidle', timeout=90000)
        print(f"OTP submitted. Current URL: {page.url}")
        print("Order potentially complete. Check browser.")

    except TimeoutError as e:
        print(f"Timeout during bank OTP handling: {e}")
        screenshot_path = debug_image_dir / 'bank_otp_timeout_error.png'
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        raise Exception("Timeout occurred during bank OTP handling.")
    except Exception as e:
        print(f"An error occurred during bank OTP handling: {e}")
        screenshot_path = debug_image_dir / "bank_otp_error.png"
        try:
            await page.screenshot(path=screenshot_path)
            print(f"Screenshot saved to {screenshot_path}")
        except Exception as screen_err:
            print(f"Could not save error screenshot: {screen_err}")
        raise Exception("Failed to process bank OTP.")


async def handle_order_summary(page: Page, debug_image_dir: Path):
    """Handles the Order Summary page and clicks CONTINUE."""
    print("\nHandling Order Summary page...")
    continue_button_selector = 'button:has-text("CONTINUE")'

    try:
        print("Locating CONTINUE button...")
        continue_button = page.locator(continue_button_selector).first
        await continue_button.wait_for(state='visible', timeout=15000)
        print("Found CONTINUE button.")

        # Optional check: Ensure button is enabled before clicking
        if not await continue_button.is_enabled(timeout=1000):
            print("CONTINUE button is visible but not enabled. Waiting a bit longer...")
            await page.wait_for_timeout(3000) # Extra wait
            if not await continue_button.is_enabled(timeout=1000):
                 print("CONTINUE button still not enabled.")
                 raise Exception("CONTINUE button not enabled on Order Summary.")

        print("Clicking CONTINUE button...")
        await continue_button.click()
        print("CONTINUE button clicked. Waiting for next page (likely Payment)...")

        # Wait for the payment page or next step
        await page.wait_for_load_state('networkidle', timeout=30000) # Increased timeout for payment page
        print(f"Navigated to next page. Current URL: {page.url}")
        print("Payment page loaded (or next step reached). Implement payment logic next.")
        # TODO: Implement payment handling logic here

    except TimeoutError:
        print("Timeout waiting for CONTINUE button or next page load after Order Summary.")
        screenshot_path = debug_image_dir / 'order_summary_timeout.png'
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        raise Exception("Timeout on Order Summary page or loading next page.")
    except Exception as e:
        print(f"Error on Order Summary page: {e}")
        screenshot_path = debug_image_dir / "order_summary_error.png"
        try:
             await page.screenshot(path=screenshot_path)
             print(f"Screenshot saved to {screenshot_path}")
        except Exception as screen_err:
             print(f"Could not save error screenshot: {screen_err}")
        raise Exception("Failed to process Order Summary page.")


async def navigate_and_buy(page: Page, url: str, debug_image_dir: Path):
    """Navigates to product page, extracts title, and clicks 'BUY NOW'."""
    print(f"Navigating to {url}...")
    try:
        # Wait until network is idle for potentially better element readiness
        await page.goto(url, wait_until='networkidle', timeout=45000) # Changed from domcontentloaded
        print("Page loaded (network idle).")

        # Try common selectors for the title
        # Inspect the page for the correct one if these fail
        title_locator = page.locator('span.B_NuCI, h1 span._35KyD6') # Trying both selectors

        try:
            # Wait for the element to be visible
            await title_locator.first.wait_for(state='visible', timeout=10000)
            title = await title_locator.first.text_content()
            title = title.strip() if title else "Title not found (empty text)"
            print(f"Product Title: {title}")
        except TimeoutError:
            print("Could not find title element (TimeoutError).")
            title = "Title not found"
        except Exception as e:
            print(f"Error getting title: {e}")
            title = "Title not found"

        # TODO: Extract other details like price, availability

        # Locate and click the "BUY NOW" button
        print("Attempting to locate 'Buy now' element (any tag, case-insensitive)...")
        # Use text-matches with 'i' flag for case-insensitivity, targeting any element (*)
        buy_now_button = page.locator('*:text-matches("Buy now", "i")')

        try:
            print("Waiting for 'Buy now' element...")
            # Increased timeout for visibility
            await buy_now_button.wait_for(state='visible', timeout=20000)
            print("Clicking 'Buy now' element...")
            await buy_now_button.click()
            print("'Buy now' element clicked.")

            # Wait for navigation/load state after click
            print("Waiting for page navigation after clicking 'Buy now'...")
            await page.wait_for_load_state('networkidle', timeout=25000) # Slightly increased timeout
            print(f"Navigated to new page: {page.url}")
            # NEXT STEP: Handled in main function now
            return True # Indicate success

        except TimeoutError as e:
            print(f"Could not find or click 'Buy now' element (TimeoutError): {e}")
            screenshot_path = debug_image_dir / "buy_now_timeout_error.png"
            await page.screenshot(path=screenshot_path)
            print(f"Screenshot saved to {screenshot_path}")
            return False
        except Exception as e:
            print(f"Error clicking 'Buy now' or waiting for navigation: {e}")
            screenshot_path = debug_image_dir / "buy_now_other_error.png"
            await page.screenshot(path=screenshot_path)
            print(f"Screenshot saved to {screenshot_path}")
            return False

    except TimeoutError as e:
         print(f"Timeout loading page {url}: {e}")
         screenshot_path = debug_image_dir / "page_load_timeout_error.png"
         await page.screenshot(path=screenshot_path)
         print(f"Screenshot saved to {screenshot_path}")
         return False
    except Exception as e:
        print(f"An error occurred during page navigation or initial interaction: {e}")
        screenshot_path = debug_image_dir / "page_load_other_error.png"
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot saved to {screenshot_path}")
        return False

def sanitize_filename(name):
    """Removes or replaces characters unsuitable for filenames."""
    # Remove characters that are definitely problematic
    name = re.sub(r'[\\/*?":<>|]', '', name)
    # Replace spaces with underscores (optional, but common)
    name = name.replace(' ', '_')
    # Limit length (optional)
    return name[:50] # Limit to 50 chars

async def main():
    product_url = "https://www.flipkart.com/hotstyle-stylish-comfortable-sneakers-canvas-shoes-casuals-running-men/p/itm5cc34d19633e0?pid=SHOGKRW7RGFUGTYN&lid=LSTSHOGKRW7RGFUGTYNQOTXSJ&marketplace=FLIPKART&q=shoes&store=osp&srno=s_1_1&otracker=AS_Query_TrendingAutoSuggest_3_0_na_na_na&otracker1=AS_Query_TrendingAutoSuggest_3_0_na_na_na&fm=search-autosuggest&iid=3ff67d73-e2fb-4937-904b-8c804b458a1a.SHOGKRW7RGFUGTYN.SEARCH&ppt=sp&ppn=sp&ssid=iujd3yyp4w0000001746194703260&qH=b0a8b6f820479900"
    session_dir = Path("sessions") # Directory to store sessions
    session_dir.mkdir(exist_ok=True) # Ensure directory exists
    debug_image_dir = Path("debug_images") # Directory for screenshots
    debug_image_dir.mkdir(exist_ok=True) # Ensure directory exists

    storage_state_path = None
    load_existing_state = False

    # --- Session Selection Menu ---
    while True:
        print("\n--- Manage Sessions ---")
        existing_sessions = sorted([f for f in session_dir.glob("*.json")])

        if existing_sessions:
            print("Select an existing session:")
            for i, session_file in enumerate(existing_sessions):
                print(f"  [{i+1}] {session_file.stem}") # Show name without .json
        else:
            print("No existing sessions found.")

        print("[N] Create New Session")
        print("[Q] Quit")

        choice = input("Enter your choice: ").strip().lower()

        if choice == 'q':
            print("Exiting.")
            return # Exit the script
        elif choice == 'n':
            new_name = input("Enter a name for the new session: ").strip()
            if not new_name:
                print("Session name cannot be empty.")
                continue
            sanitized_name = sanitize_filename(new_name)
            storage_state_path = session_dir / f"{sanitized_name}.json"
            load_existing_state = False
            print(f"Creating new session: {sanitized_name}")
            break
        elif choice.isdigit():
            try:
                index = int(choice) - 1
                if 0 <= index < len(existing_sessions):
                    storage_state_path = existing_sessions[index]
                    load_existing_state = True
                    print(f"Using existing session: {storage_state_path.stem}")
                    break
                else:
                    print("Invalid session number.")
            except ValueError:
                print("Invalid input.")
        else:
            print("Invalid choice. Please enter a number, 'N', or 'Q'.")
    # --- End Session Selection Menu ---

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = None
        try:
            # Load or create context based on user choice
            if load_existing_state and storage_state_path.exists():
                print(f"Loading session state from {storage_state_path}")
                # Remove device emulation when loading context
                context = await browser.new_context(
                    storage_state=storage_state_path
                )
            else:
                if load_existing_state:
                     print(f"Warning: Selected session file {storage_state_path} not found. Creating new context (desktop).")
                else:
                     print("Creating new context for the new session (desktop).")
                # Remove device emulation when creating new context
                context = await browser.new_context()

            page = await context.new_page()

            # Pass debug_image_dir to functions that might take screenshots
            navigation_success = await navigate_and_buy(page, product_url, debug_image_dir)

            if navigation_success:
                print("\nChecking checkout page state...")

                # Define selectors for state detection (Most generic payment indicator)
                login_input_selector = "input[type='text'][autocomplete='off']"
                address_label_selector = 'label:has(input[name="address"])'
                order_summary_continue_selector = 'button:has-text("CONTINUE")'
                # Find text, then nearest label/div ancestor as indicator
                _payment_text_locator = page.locator(':text-matches("Credit / Debit / ATM Card", "i")')
                payment_page_indicator_locator = _payment_text_locator.locator('xpath=ancestor::*[self::label or self::div][1]')

                # Check state AFTER 'BUY NOW' click
                # Priority: 1. Payment? 2. Order Summary? 3. Address? 4. Login?
                print("\nChecking page state after 'BUY NOW' click (or subsequent steps)...")
                current_state = "UNKNOWN"

                # 1. Check for Payment Page using the derived locator
                try:
                    # Check if the container element is visible
                    await payment_page_indicator_locator.first.wait_for(state='visible', timeout=5000)
                    print("Detected PAYMENT page (generic method).")
                    current_state = "PAYMENT"
                except TimeoutError:
                    print("Payment page indicator not found. Checking for Order Summary page...")
                except Exception as e:
                     print(f"Error checking for Payment page: {e}. Checking for Order Summary page...")

                # 2. Check for Order Summary (if not Payment)
                if current_state == "UNKNOWN":
                    try:
                        await page.locator(order_summary_continue_selector).first.wait_for(state='visible', timeout=5000)
                        print("Detected ORDER SUMMARY page.")
                        current_state = "ORDER_SUMMARY"
                    except TimeoutError:
                        print("Order Summary continue button not found. Checking for Address page...")
                    except Exception as e:
                        print(f"Error checking for Order Summary: {e}. Checking for Address page...")

                # 3. Check for Address Page (if not Payment or Order Summary)
                if current_state == "UNKNOWN":
                    try:
                        await page.locator(address_label_selector).first.wait_for(state='visible', timeout=5000)
                        print("Detected DELIVERY ADDRESS page.")
                        current_state = "ADDRESS"
                    except TimeoutError:
                         print("Address label not found. Checking for Login page...")
                    except Exception as e:
                         print(f"Error checking for Address page: {e}. Checking for Login page...")

                # 4. Check for Login Page (if not Payment, Order Summary or Address)
                if current_state == "UNKNOWN":
                    try:
                        await page.locator(login_input_selector).first.wait_for(state='visible', timeout=5000)
                        print("Detected LOGIN/SIGNUP page.")
                        current_state = "LOGIN"
                    except TimeoutError:
                         print("Login input not found.")
                    except Exception as e:
                         print(f"Error checking for Login page: {e}.")


                # --- Handle the detected state --- (State machine logic)
                if current_state == "LOGIN":
                    print("Handling LOGIN...")
                    await handle_login(page)
                    # After login, expect Address page
                    print("Re-checking for Address page after login...")
                    try:
                         await page.locator(address_label_selector).first.wait_for(state='visible', timeout=10000)
                         print("Now on Address page.")
                         current_state = "ADDRESS" # Update state for next step
                    except Exception as e:
                         print(f"Did not find Address page after login: {e}. State is uncertain.")
                         current_state = "UNKNOWN_AFTER_LOGIN"

                if current_state == "ADDRESS":
                    print("Handling ADDRESS selection...")
                    await select_delivery_address(page, debug_image_dir)
                    # After address selection, expect Order Summary page
                    print("Re-checking for Order Summary page after address selection...")
                    try:
                         await page.locator(order_summary_continue_selector).first.wait_for(state='visible', timeout=10000)
                         print("Now on Order Summary page.")
                         current_state = "ORDER_SUMMARY" # Update state for next step
                    except Exception as e:
                         print(f"Did not find Order Summary page after address selection: {e}. State is uncertain.")
                         current_state = "UNKNOWN_AFTER_ADDRESS"

                if current_state == "ORDER_SUMMARY":
                    print("Handling ORDER SUMMARY...")
                    await handle_order_summary(page, debug_image_dir)
                    # After order summary, expect Payment page
                    print("Re-checking for Payment page indicator text after order summary...")
                    try:
                         # Simpler check: Just look for the text itself as confirmation
                         payment_text_indicator = page.locator(':text-matches("Credit / Debit / ATM Card", "i")').first
                         await payment_text_indicator.wait_for(state='visible', timeout=15000)
                         print("Found Payment page indicator text. Assuming now on Payment page.")
                         current_state = "PAYMENT" # Update state for next step
                    except Exception as e:
                         print(f"Did not find Payment page indicator text after order summary: {e}. State is uncertain.")
                         current_state = "UNKNOWN_AFTER_SUMMARY"

                if current_state == "PAYMENT":
                    print("Handling PAYMENT...")
                    await handle_payment(page, debug_image_dir)
                    print("Payment handled. Proceeding to Bank OTP...")
                    # The script might end here, or handle OTP/confirmation
                    current_state = "POST_PAYMENT"

                # --- NEW: Handle Bank OTP --- 
                if current_state == "POST_PAYMENT":
                    print("Handling BANK OTP...")
                    await handle_bank_otp(page, debug_image_dir)
                    print("Bank OTP handled. Order process should be complete.")
                    current_state = "ORDER_COMPLETE"

                # --- Final State Check ---
                if current_state in ["UNKNOWN", "UNKNOWN_AFTER_LOGIN", "UNKNOWN_AFTER_ADDRESS", "UNKNOWN_AFTER_SUMMARY"]:
                    print(f"Could not reliably determine page state ({current_state}) or transition failed. Stopping.")
                    screenshot_filename = debug_image_dir / f'debug_unknown_state_{current_state.lower()}.png'
                    await page.screenshot(path=screenshot_filename)
                    print(f"Saved screenshot to {screenshot_filename}")
                    raise Exception(f"Script stopped due to uncertain page state: {current_state}")
                elif current_state == "ORDER_COMPLETE":
                     print("\nScript finished checkout flow up to post-payment.")
                     print("Further steps (OTP, Confirmation) may require manual interaction or additional code.")
                else:
                    # Should not happen if logic is correct, but safety net
                    print(f"Ended in unexpected state: {current_state}")

                print("Browser window will remain open for inspection.")
                await asyncio.sleep(15) # Keep open longer for payment inspection

            else:
                 print("\nNavigation or 'BUY NOW' click failed. Cannot proceed to checkout.")
                 await asyncio.sleep(5)

        except Exception as e:
            print(f"An error occurred in main: {e}")
            if page and not page.is_closed(): # Check if page exists and is open
                 try:
                     screenshot_path = debug_image_dir / "main_error_screenshot.png"
                     await page.screenshot(path=screenshot_path)
                     print(f"Saved error screenshot to {screenshot_path}")
                 except Exception as screen_err:
                     print(f"Could not save error screenshot: {screen_err}")

        finally:
            if context:
                # Save state only if a path was determined (i.e., not quit)
                if storage_state_path:
                    try:
                        print(f"Saving session state to {storage_state_path}...")
                        await context.storage_state(path=storage_state_path)
                        print("Session state saved.")
                    except Exception as save_err:
                        print(f"Could not save session state: {save_err}")
                else:
                    print("No session path determined, skipping state save.")

                print("Closing browser context.")
                # await context.close() # User commented out
            elif browser.is_connected():
                 print("Closing browser.")
                 # await browser.close() # User commented out

if __name__ == "__main__":
    asyncio.run(main()) 