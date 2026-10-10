import logging
import time
import uuid
import base64
import io
from PIL import Image
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional

from src.database import get_db
from src.conversation.models import LLMRequestLog
from src.conversation.service import validate_access_token
from src.vision_service.schemas import VisionGenerateRequest, VisionGenerateResponse, VisionGenerateData
from src.vision_service.service import generate_dbml_from_file
from src.config import settings
from src.utils.helper import decrypt_api_key
from src.utils.errors import get_user_friendly_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")
security = HTTPBearer()

@router.post("/generate-from-file", response_model=VisionGenerateResponse)
def generate_from_file_api(
    request: VisionGenerateRequest,
    req: Request,
    db: Session = Depends(get_db),
    token: HTTPAuthorizationCredentials = Depends(security),
    authorization: Optional[str] = Header(None)
):
    start_time = time.time()
    
    # Auth Check
    auth_token = authorization.replace("Bearer ", "").strip() if authorization else token.credentials
    try:
        application = validate_access_token(db, auth_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    if not request.llm_api_key or not request.llm:
        raise HTTPException(status_code=400, detail="llm and llm_api_key are required for vision processing.")

    try:
        resolved_api_key = decrypt_api_key(request.llm_api_key, settings.API_KEY_ENCRYPTION_KEY)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid API key.")

    # Strict File Validation
    supported_extensions = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf', '.svg')
    supported_prefixes = ("data:image/", "data:application/pdf", "data:image/svg+xml")
    
    is_valid_format = False
    if request.file_name and request.file_name.lower().endswith(supported_extensions):
        is_valid_format = True
    elif request.file_base64 and any(request.file_base64.startswith(prefix) for prefix in supported_prefixes):
        is_valid_format = True
        
    if not is_valid_format:
        raise HTTPException(
            status_code=400, 
            detail="Unsupported file format. Only PNG, JPG, JPEG, WEBP, GIF, SVG, and PDF files are supported."
        )

    # Image Check
    is_image = False
    if request.file_base64.startswith("data:image/") and not request.file_base64.startswith("data:image/svg+xml"):
        is_image = True
    elif request.file_name and request.file_name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
        is_image = True

    if is_image:
        b64_str = request.file_base64
        if "," in b64_str:
            b64_str = b64_str.split(",")[1]
            
        try:
            image_data = base64.b64decode(b64_str)
            with Image.open(io.BytesIO(image_data)) as img:
                img.verify() # Just verify it is a valid, uncorrupted image file
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=400, detail="Invalid image format.")

    try:
        dbml_query, token_used = generate_dbml_from_file(
            file_base64=request.file_base64,
            file_name=request.file_name,
            llm=request.llm,
            llm_api_key=resolved_api_key,
            base_url=request.base_url
        )
        
        logger.info(f"Vision DBML generated successfully | Model: {request.llm} | Tokens used: {token_used}")
        
        return VisionGenerateResponse(
            message="DBML generated successfully",
            status_code=200,
            data=VisionGenerateData(
                dbml_query=dbml_query
            )
        )

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Generate-from-file API Error: {str(e)}")
        
        # Log Error
        log_entry = LLMRequestLog(
            request_id=uuid.uuid4(),
            user_prompt="Image processing request",
            is_fallback_mode=False,
            provider=request.model or "claude",
            model_name=request.llm,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            status="failed",
            http_status_code=500,
            duration_ms=duration_ms,
            error_message=str(e),
        )
        db.add(log_entry)
        db.commit()
        
        user_message = get_user_friendly_error(e)
        if "valid database diagram" in user_message or "API key does not have access" in user_message:
            raise HTTPException(status_code=400, detail=user_message)
        raise HTTPException(status_code=500, detail=user_message)
