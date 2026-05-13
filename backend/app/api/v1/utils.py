"""
API endpoint for utility functions.

This endpoint provides utility functions like profile ID extraction.
"""
import logging
import re
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Header, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.db.dependencies import get_db
from app.ws.events import WebSocketEventHandler
from app.api.dependencies import get_ws_handler
from app.auth.dependencies import validate_api_key_from_header_or_body
from app.linkedin.services.base import LinkedInServiceBase
from app.linkedin.helpers import get_linkedin_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/utils",
    tags=["utils"],
)


class ProfileIdRequest(BaseModel):
    """Request model for profile ID extraction."""
    profile_url: str = Field(
        ..., 
        description="LinkedIn profile URL or vanity name (e.g., 'https://www.linkedin.com/in/username' or 'username')",
        example="https://www.linkedin.com/in/vlad-centea-821435309"
    )
    api_key: Optional[str] = Field(default=None, description="The user's full API key (optional if provided via X-API-Key header)")
    server_call: bool = Field(False, description="If true, execute on server; if false, use proxy via extension")


class ProfileIdResponse(BaseModel):
    """Response model for profile ID extraction."""
    profile_id: str = Field(
        ..., 
        description="Extracted LinkedIn profile ID",
        example="ACoAAE6NWVkBs_w9UHFzV8oRIt_9bFJxdXlAEVM"
    )
    vanity_name: str = Field(
        ..., 
        description="Extracted vanity name from the profile URL",
        example="vlad-centea-821435309"
    )
    method: str = Field(
        ..., 
        description="Extraction method used",
        example="html_parsing"
    )


async def _extract_vanity_name_from_url(profile_input: str) -> str:
    """
    Extract vanity name from LinkedIn profile URL.
    
    Args:
        profile_input: LinkedIn profile URL or vanity name
        
    Returns:
        The extracted vanity name
        
    Raises:
        ValueError: If vanity name cannot be extracted
    """
    # Check if input is already a vanity name (not a URL)
    if not re.search(r'https?://|linkedin\.com', profile_input, re.IGNORECASE):
        # Validate it looks like a vanity name
        if re.match(r'^[A-Za-z0-9_-]+$', profile_input):
            logger.info(f"Input is already a vanity name: {profile_input}")
            return profile_input
    
    # Extract the vanity name from the URL
    vanity_match = re.search(r'linkedin\.com/in/([^/\?]+)', profile_input)
    if not vanity_match:
        raise ValueError(f"Could not extract vanity name from URL: {profile_input}")
    
    vanity_name = vanity_match.group(1)
    logger.info(f"Extracted vanity name: {vanity_name}")
    return vanity_name


async def _extract_profile_id_from_html_content(html: str, vanity_name: str) -> str:
    """
    Extract profile ID from HTML content.

    LinkedIn minifies its pages as a single line with JSON inside JS strings,
    so double-quotes are escaped as \\". The embedded structure looks like:
      vanityName\":\"<name>\", ... ,\"profileUrn\":\"urn:li:fsd_profile:<id>\"
    """
    pattern = (
        r'vanityName\\":\\"' + re.escape(vanity_name) + r'\\"'
        r'.{0,400}?'
        r'profileUrn\\":\\"urn:li:fsd_profile:([A-Za-z0-9_-]+)\\"'
    )
    match = re.search(pattern, html, re.DOTALL)
    if match:
        profile_id = match.group(1)
        logger.info(f"Extracted profile ID from inline JSON: {profile_id}")
        return profile_id

    raise ValueError(f"Could not find profile ID in HTML for vanity name: {vanity_name}")


@router.post(
    "/extract-profile-id", 
    response_model=ProfileIdResponse, 
    summary="Extract LinkedIn Profile ID"
)
async def extract_profile_id_endpoint(
    request_data: ProfileIdRequest,
    ws_handler: WebSocketEventHandler = Depends(get_ws_handler),
    db: AsyncSession = Depends(get_db),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key", include_in_schema=False)
):
    """
    Extract LinkedIn profile ID from profile URL or vanity name.
    
    Uses HTML parsing approach which is more reliable than GraphQL API
    as it doesn't require CSRF token validation.
    
    Supports two execution modes:
    1. server_call=True: Direct server-side execution
    2. server_call=False: Execution via browser extension proxy
    
    Args:
        request_data: Request parameters including profile_url, api_key, server_call
        
    Returns:
        ProfileIdResponse with the extracted profile ID and vanity name
        
    Raises:
        HTTPException: If profile ID cannot be extracted
    """
    # Validate API key
    try:
        requesting_user_id = await validate_api_key_from_header_or_body(
            api_key_from_body=request_data.api_key,
            api_key_header=x_api_key,
            db=db
        )
        logger.info(f"[EXTRACT_PROFILE_ID] API Key validated for user ID: {requesting_user_id}")
    except Exception as e:
        logger.error(f"[EXTRACT_PROFILE_ID] API key validation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key"
        )
    
    # Get LinkedIn service for this user
    try:
        service = await get_linkedin_service(db, requesting_user_id, LinkedInServiceBase)
    except Exception as e:
        logger.error(f"[EXTRACT_PROFILE_ID] Failed to get LinkedIn service: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize LinkedIn service: {str(e)}"
        )
    
    try:
        logger.info(f"[EXTRACT_PROFILE_ID] Processing request for: {request_data.profile_url}")
        
        # Extract vanity name from URL
        vanity_name = await _extract_vanity_name_from_url(request_data.profile_url)
        
        # Build profile URL
        profile_url = f"https://www.linkedin.com/in/{vanity_name}/"
        
        logger.info(f"[EXTRACT_PROFILE_ID] Fetching profile HTML for: {profile_url}")
        
        # Fetch the profile HTML page using authenticated service
        # Force gzip/deflate only — service.headers carries Accept-Encoding: br from
        # the browser session and httpx won't reliably decompress brotli responses.
        fetch_headers = {**service.headers, 'accept-encoding': 'gzip, deflate'}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(profile_url, headers=fetch_headers)
            response.raise_for_status()

        html = response.text
        logger.info(
            f"[EXTRACT_PROFILE_ID] fetch: status={response.status_code} "
            f"content-encoding={response.headers.get('content-encoding', 'none')} "
            f"raw_bytes={len(response.content)} text_len={len(html)}"
        )
        
        # Extract profile ID from HTML
        profile_id = await _extract_profile_id_from_html_content(html, vanity_name)
        
        logger.info(f"[EXTRACT_PROFILE_ID] Successfully extracted profile ID: {profile_id}")
        
        return ProfileIdResponse(
            profile_id=profile_id,
            vanity_name=vanity_name,
            method="html_parsing"
        )
        
    except httpx.HTTPStatusError as e:
        logger.error(f"[EXTRACT_PROFILE_ID] HTTP error: {e.response.status_code}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Failed to fetch LinkedIn profile: {e.response.status_code}"
        )
    except ValueError as e:
        logger.error(f"[EXTRACT_PROFILE_ID] Extraction error: {str(e)}")
        # Dump HTML to disk for pattern inspection
        try:
            import os
            debug_dir = os.path.join(os.path.dirname(__file__), '../../../../debug_responses')
            os.makedirs(debug_dir, exist_ok=True)
            vanity_name_safe = re.sub(r'[^A-Za-z0-9_-]', '_', request_data.profile_url.split('/in/')[-1].strip('/'))
            debug_path = os.path.join(debug_dir, f'profile_html_{vanity_name_safe}.html')
            with open(debug_path, 'w', encoding='utf-8') as f:
                f.write(html)
            logger.info(f"[EXTRACT_PROFILE_ID] Dumped HTML ({len(html)} chars) to {debug_path}")
        except Exception as dump_err:
            logger.warning(f"[EXTRACT_PROFILE_ID] Could not dump HTML: {dump_err}")
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"[EXTRACT_PROFILE_ID] Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

