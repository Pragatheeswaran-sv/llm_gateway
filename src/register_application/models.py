import uuid

from sqlalchemy import (Boolean,Column,DateTime,String,Text,Uuid,func,)

from src.database import Base


class RegisterApplication(Base):
    __tablename__ = "register"

    id = Column(Uuid(as_uuid=True),primary_key=True,default=uuid.uuid4,)
    app_name = Column(String(255),nullable=False,unique=True)
    description = Column(Text,nullable=True)
    client_id = Column(String(255),nullable=False,unique=True,index=True,)
    client_secret_key_hash = Column(String(64),nullable=False)    # Stores deterministic hash only.
    is_active = Column(Boolean,nullable=False,default=True,index=True,)
    created_at = Column(DateTime(timezone=True),nullable=False,server_default=func.now())   
    updated_at = Column(DateTime(timezone=True),nullable=False,server_default=func.now(),onupdate=func.now(),)
    created_by = Column(String(255),nullable=True,)
    updated_by = Column(String(255),nullable=True,)