import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    BigInteger,
    Integer,
    String,
    Text,
    Uuid,
    func,
)

from src.database import Base

class UserQuery(Base):
    __tablename__ = "user_queries"

    message_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    app_id = Column(Uuid(as_uuid=True), ForeignKey("register.id"), nullable=False, index=True)
    conversation_id = Column(String(255), nullable=False, index=True)
    external_prompt_id = Column(String(255), nullable=True, index=True)
    query = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255), nullable=True)
    updated_by = Column(String(255), nullable=True)

class LLMFallbackModel(Base):
    """Fallback/rate-limit state. Contains only the requested columns."""

    __tablename__ = "llm_fallback_models"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String(100), nullable=False)
    llm_model = Column(String(255), nullable=False)
    api_base_url = Column(String(500), nullable=False)
    api_key = Column(String(1000), nullable=False)
    priority = Column(Integer, nullable=True)
    daily_token_limit = Column(BigInteger, nullable=False)
    used_tokens = Column(BigInteger, nullable=False, default=0)
    daily_request_limit = Column(Integer, nullable=False)
    used_requests = Column(Integer, nullable=False, default=0)
    rpm_limit = Column(Integer, nullable=False)
    tpm_limit = Column(BigInteger, nullable=False)
    minute_requests = Column(Integer, nullable=False, default=0)
    minute_tokens = Column(BigInteger, nullable=False, default=0)
    window_start = Column(DateTime(timezone=True), nullable=True)
    cooldown_until = Column(DateTime(timezone=True), nullable=True)
    is_rate_limited = Column(Boolean, nullable=False, default=False)
    status = Column(String(50), nullable=False, default="ACTIVE")
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255), nullable=True)
    updated_by = Column(String(255), nullable=True)
