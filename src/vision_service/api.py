import logging
import time
import uuid
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

    try:
        dbml_query, token_used = generate_dbml_from_file(
            file_base64=request.file_base64,
            file_name=request.file_name,
            llm=request.llm,
            llm_api_key=resolved_api_key,
            base_url=request.base_url
        )
        
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
        
        raise HTTPException(status_code=500, detail=str(e))
