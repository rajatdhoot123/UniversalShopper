import uvicorn
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends, Request, Form
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import os
from pathlib import Path
import uuid
import json
from typing import Dict, Optional, List, Any

# Import our bot module
from flipkart_bot_api import (
    provide_bank_otp,
    get_active_processes,
    submit_login_otp,
    select_address,
    submit_payment_details,
    get_process_status,
    checkout_process_manager,
    terminate_process
)

app = FastAPI(title="Flipkart Checkout Bot API",
              description="API for automating Flipkart checkout process",
              version="1.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve debug images directory
try:
    debug_images_dir = Path("debug_images")
    debug_images_dir.mkdir(exist_ok=True)
    app.mount("/debug-images", StaticFiles(directory="debug_images"),
              name="debug_images")
except Exception as e:
    print(f"Warning: Could not mount debug-images directory: {e}")

# Create sessions directory if it doesn't exist
sessions_dir = Path("sessions")
sessions_dir.mkdir(exist_ok=True)

# Data models for API requests and responses


class ProductRequest(BaseModel):
    product_url: str
    session_name: Optional[str] = None
    use_existing_session: bool = False


class OTPRequest(BaseModel):
    process_id: str
    otp: str


class AddressSelectionRequest(BaseModel):
    process_id: str
    address_index: int


class PaymentDetailsRequest(BaseModel):
    process_id: str
    card_number: str
    cvv: str
    expiry_month: Optional[str] = None
    expiry_year: Optional[str] = None
    expiry_combined: Optional[str] = None


class BankOTPRequest(BaseModel):
    process_id: str
    otp: str


class StatusResponse(BaseModel):
    status: str
    message: str
    data: Optional[Dict[str, Any]] = None


@app.get("/", response_model=StatusResponse)
async def read_root():
    return {
        "status": "success",
        "message": "Flipkart Checkout Bot API is running",
        "data": {"version": "1.0.0"}
    }


@app.get("/sessions", response_model=StatusResponse)
async def list_sessions():
    """List all available saved sessions"""
    session_files = [f.stem for f in sessions_dir.glob("*.json")]
    return {
        "status": "success",
        "message": f"Found {len(session_files)} sessions",
        "data": {"sessions": session_files}
    }


@app.post("/process", response_model=StatusResponse)
async def start_process(request: ProductRequest, background_tasks: BackgroundTasks):
    """Start a new checkout process for a product"""
    try:
        process_id = str(uuid.uuid4())

        # Initialize session
        session_path = None
        if request.use_existing_session and request.session_name:
            session_path = sessions_dir / f"{request.session_name}.json"
            if not session_path.exists():
                return JSONResponse(
                    status_code=404,
                    content={
                        "status": "error",
                        "message": f"Session '{request.session_name}' not found",
                        "data": None
                    }
                )
        elif not request.use_existing_session and request.session_name:
            session_path = sessions_dir / f"{request.session_name}.json"

        # Start the process in background
        background_tasks.add_task(
            checkout_process_manager,
            process_id,
            request.product_url,
            session_path
        )

        return {
            "status": "success",
            "message": "Checkout process started",
            "data": {
                "process_id": process_id,
                "product_url": request.product_url,
                "session_name": request.session_name
            }
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Failed to start checkout process: {str(e)}",
                "data": None
            }
        )


@app.get("/process/{process_id}", response_model=StatusResponse)
async def get_process(process_id: str):
    """Get status of a specific checkout process"""
    status = get_process_status(process_id)
    if not status:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "message": f"Process with ID {process_id} not found",
                "data": None
            }
        )

    return {
        "status": "success",
        "message": "Process status retrieved successfully",
        "data": status
    }


@app.get("/processes", response_model=StatusResponse)
async def list_processes():
    """Get status of all active checkout processes"""
    active_processes = get_active_processes()
    return {
        "status": "success",
        "message": f"Found {len(active_processes)} active processes",
        "data": {"processes": active_processes}
    }


@app.post("/process/{process_id}/login-otp", response_model=StatusResponse)
async def handle_login_otp(process_id: str, otp_request: OTPRequest):
    """Submit OTP for login"""
    success = await submit_login_otp(process_id, otp_request.otp)
    if not success:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "message": f"Process with ID {process_id} not found or not expecting OTP",
                "data": None
            }
        )

    return {
        "status": "success",
        "message": "OTP submitted successfully",
        "data": None
    }


@app.post("/process/{process_id}/select-address", response_model=StatusResponse)
async def handle_address_selection(process_id: str, address_request: AddressSelectionRequest):
    """Select delivery address"""
    success = await select_address(process_id, address_request.address_index)
    if not success:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "message": f"Process with ID {process_id} not found or not at address selection stage",
                "data": None
            }
        )

    return {
        "status": "success",
        "message": "Address selected successfully",
        "data": None
    }


@app.post("/process/{process_id}/payment", response_model=StatusResponse)
async def handle_payment(process_id: str, payment_request: PaymentDetailsRequest):
    """Submit payment details"""
    success = await submit_payment_details(
        process_id,
        payment_request.card_number,
        payment_request.cvv,
        payment_request.expiry_month,
        payment_request.expiry_year,
        payment_request.expiry_combined
    )

    if not success:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "message": f"Process with ID {process_id} not found or not at payment stage",
                "data": None
            }
        )

    return {
        "status": "success",
        "message": "Payment details submitted successfully",
        "data": None
    }


@app.post("/process/{process_id}/bank-otp", response_model=StatusResponse)
async def handle_bank_otp(process_id: str, bank_otp_request: BankOTPRequest):
    """Submit bank OTP"""
    success = await provide_bank_otp(process_id, bank_otp_request.otp)
    if not success:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "message": f"Process with ID {process_id} not found or not at bank OTP stage",
                "data": None
            }
        )

    return {
        "status": "success",
        "message": "Bank OTP submitted successfully",
        "data": None
    }


@app.delete("/process/{process_id}", response_model=StatusResponse)
async def handle_terminate_process(process_id: str):
    """Terminate a specific checkout process"""
    # NOTE: The actual termination logic needs to be implemented
    # in flipkart_bot_api.terminate_process(process_id)
    # This function should signal the background task to stop gracefully
    # and update its status.

    # Assuming terminate_process is async
    success = await terminate_process(process_id)

    if not success:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "message": f"Process with ID {process_id} not found or could not be terminated",
                "data": None
            }
        )

    return {
        "status": "success",
        "message": f"Process {process_id} termination requested successfully",
        "data": None
    }

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
