import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    BigInteger,
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
    message_id = Column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4)
    app_id = Column(Uuid(as_uuid=True),ForeignKey("register.id"),nullable=False,index=True)
    conversation_id = Column(String(255),nullable=False,index=True)
    external_prompt_id = Column(String(255),nullable=True,index=True)
    query = Column(Text,nullable=False)
    summary = Column(Text,nullable=True)
    is_active = Column(Boolean,nullable=False,default=True,server_default="true",index=True)
    created_at = Column(DateTime(timezone=True),nullable=False,server_default=func.now())
    updated_at = Column(DateTime(timezone=True),nullable=False,server_default=func.now(),onupdate=func.now())
    created_by = Column(String(255),nullable=True)
    updated_by = Column(String(255),nullable=True)