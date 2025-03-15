from fastapi import FastAPI, HTTPException, WebSocket, Form, UploadFile, File, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import Optional, Dict, List
import datetime
import os
from passlib.context import CryptContext
import uvicorn
from dotenv import load_dotenv
import logging
import asyncio
import json

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# SQLAlchemy Models
class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    phone_number = Column(String, unique=True)
    password = Column(String)
    role = Column(Integer)
    profile_pic = Column(String, nullable=True, default="/static/default_profile.jpg")
    about = Column(String, nullable=True, default="Hey there! I'm using G.Chat")
    last_seen = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

class JobDB(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String)
    description = Column(String)
    requirements = Column(String)
    deadline = Column(String)
    employer_email = Column(String)
    company_name = Column(String, nullable=True)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

class MessageDB(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"))
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    message_type = Column(String)
    content = Column(String)
    sent_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True)

class GroupDB(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    group_pic = Column(String, nullable=True, default="/static/default_group.jpg")
    created_by = Column(Integer, ForeignKey("users.id"))

class GroupMemberDB(Base):
    __tablename__ = "group_members"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    user_id = Column(Integer, ForeignKey("users.id"))

class StatusDB(Base):
    __tablename__ = "status"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    content_type = Column(String)
    content = Column(String)
    posted_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    expires_at = Column(DateTime)

class CallDB(Base):
    __tablename__ = "calls"
    id = Column(Integer, primary_key=True, index=True)
    caller_id = Column(Integer, ForeignKey("users.id"))
    recipient_id = Column(Integer, ForeignKey("users.id"))
    call_type = Column(String)
    start_time = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    end_time = Column(DateTime, nullable=True)

Base.metadata.create_all(bind=engine)

# Pydantic Models
class User(BaseModel):
    email: str
    phone_number: str
    password: str
    role: int

class Message(BaseModel):
    sender_id: int
    recipient_id: Optional[int] = None
    group_id: Optional[int] = None
    message_type: str
    content: str

# Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# WebSocket for Real-Time Messaging
active_connections: Dict[int, WebSocket] = {}
typing_users: Dict[int, set] = {}

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int, db: Session = Depends(get_db)):
    await websocket.accept()
    active_connections[user_id] = websocket
    db.query(UserDB).filter(UserDB.id == user_id).update({"last_seen": datetime.datetime.now(datetime.timezone.utc)})
    db.commit()
    try:
        while True:
            data = await websocket.receive_json()
            if "type" in data and data["type"] == "webrtc_signal":
                recipient_id = int(data["to"])
                if recipient_id in active_connections:
                    await active_connections[recipient_id].send_json(data)
            elif "type" in data and data["type"] == "typing":
                recipient_id = int(data["to"])
                if recipient_id not in typing_users:
                    typing_users[recipient_id] = set()
                typing_users[recipient_id].add(user_id)
                if recipient_id in active_connections:
                    await active_connections[recipient_id].send_json({"type": "typing", "from": user_id})
                await asyncio.sleep(2)
                typing_users[recipient_id].discard(user_id)
                if recipient_id in active_connections:
                    await active_connections[recipient_id].send_json({"type": "stop_typing", "from": user_id})
            else:
                message = MessageDB(
                    sender_id=user_id,
                    recipient_id=data.get("recipient_id"),
                    group_id=data.get("group_id"),
                    message_type=data.get("message_type", "text"),
                    content=data["content"],
                    delivered_at=datetime.datetime.now(datetime.timezone.utc) if data.get("recipient_id") in active_connections else None
                )
                db.add(message)
                db.commit()
                db.refresh(message)
                message_dict = {
                    "id": message.id,
                    "sender_id": message.sender_id,
                    "recipient_id": message.recipient_id,
                    "group_id": message.group_id,
                    "message_type": message.message_type,
                    "content": message.content,
                    "sent_at": message.sent_at.isoformat(),
                    "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None,
                    "read_at": message.read_at.isoformat() if message.read_at else None
                }
                if message.recipient_id and message.recipient_id in active_connections:
                    await active_connections[message.recipient_id].send_json(message_dict)
                    db.query(MessageDB).filter(MessageDB.id == message.id).update({"read_at": datetime.datetime.now(datetime.timezone.utc)})
                    db.commit()
                elif message.group_id:
                    group_members = db.query(GroupMemberDB).filter(GroupMemberDB.group_id == message.group_id).all()
                    for member in group_members:
                        if member.user_id in active_connections and member.user_id != user_id:
                            await active_connections[member.user_id].send_json(message_dict)
                await websocket.send_json(message_dict)
    except Exception as e:
        logger.error(f"WebSocket error for user {user_id}: {e}")
    finally:
        if user_id in active_connections:
            del active_connections[user_id]
        db.query(UserDB).filter(UserDB.id == user_id).update({"last_seen": datetime.datetime.now(datetime.timezone.utc)})
        db.commit()

# API Endpoints
@app.post("/register")
async def register(user: User, db: Session = Depends(get_db)):
    if db.query(UserDB).filter(UserDB.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already exists")
    if db.query(UserDB).filter(UserDB.phone_number == user.phone_number).first():
        raise HTTPException(status_code=400, detail="Phone number already exists")
    hashed_password = get_password_hash(user.password)
    last_user = db.query(UserDB).order_by(UserDB.id.desc()).first()
    user_id = (last_user.id + 1) if last_user else 1
    db_user = UserDB(id=user_id, email=user.email, phone_number=user.phone_number, password=hashed_password, role=user.role)
    db.add(db_user)
    db.commit()
    return {"user_id": user_id, "role": user.role}

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(UserDB).filter((UserDB.email == username) | (UserDB.phone_number == username)).first()
    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"user_id": user.id, "role": user.role}

@app.get("/dashboard/{role}", response_class=HTMLResponse)
async def dashboard(role: str, user_id: str = Query(..., regex=r"^\d+:\d+$")):
    try:
        uid, rid = map(int, user_id.split(":"))
        db = SessionLocal()
        try:
            user = db.query(UserDB).filter(UserDB.id == uid).first()
            if not user or user.role != rid or (role == "jobseeker" and rid != 0) or (role == "employer" and rid != 1):
                raise HTTPException(status_code=403, detail="Not authorized")
            with open(f"static/{role}_dashboard.html", "r") as f:
                return HTMLResponse(content=f.read())
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@app.get("/employer/dashboard")
async def employer_dashboard(user_id: str = Query(..., regex=r"^\d+:\d+$"), db: Session = Depends(get_db)):
    uid, rid = map(int, user_id.split(":"))
    user = db.query(UserDB).filter(UserDB.id == uid).first()
    if not user or user.role != rid or rid != 1:
        raise HTTPException(status_code=403, detail="Not authorized")
    jobs = db.query(JobDB).filter(JobDB.user_id == uid).all()
    return {"jobs": [{"id": j.id, "title": j.title, "description": j.description} for j in jobs]}

@app.get("/jobseeker/dashboard")
async def jobseeker_dashboard(user_id: str = Query(..., regex=r"^\d+:\d+$"), db: Session = Depends(get_db)):
    uid, rid = map(int, user_id.split(":"))
    user = db.query(UserDB).filter(UserDB.id == uid).first()
    if not user or user.role != rid or rid != 0:
        raise HTTPException(status_code=403, detail="Not authorized")
    jobs = db.query(JobDB).filter(JobDB.status == "active").all()
    return {"jobs": [{"id": j.id, "title": j.title, "description": j.description} for j in jobs]}

@app.get("/chat_list")
async def get_chat_list(user_id: str = Query(..., regex=r"^\d+:\d+$"), db: Session = Depends(get_db)):
    uid, _ = map(int, user_id.split(":"))
    messages = db.query(MessageDB).filter((MessageDB.sender_id == uid) | (MessageDB.recipient_id == uid)).all()
    chat_dict = {}
    for msg in messages:
        other_id = msg.recipient_id if msg.sender_id == uid else msg.sender_id
        if other_id and other_id not in chat_dict:
            other_user = db.query(UserDB).filter(UserDB.id == other_id).first()
            chat_dict[other_id] = {
                "user_id": other_id,
                "name": other_user.email.split("@")[0],
                "profile_pic": other_user.profile_pic,
                "last_message": msg.content,
                "last_time": msg.sent_at.isoformat(),
                "unread": 0 if msg.read_at else 1
            }
    return list(chat_dict.values())

@app.get("/messages/{recipient_id}")
async def get_messages(recipient_id: int, user_id: str = Query(..., regex=r"^\d+:\d+$"), db: Session = Depends(get_db)):
    uid, _ = map(int, user_id.split(":"))
    messages = db.query(MessageDB).filter(
        ((MessageDB.sender_id == uid) & (MessageDB.recipient_id == recipient_id)) |
        ((MessageDB.sender_id == recipient_id) & (MessageDB.recipient_id == uid))
    ).order_by(MessageDB.sent_at).all()
    return [{"id": m.id, "sender_id": m.sender_id, "recipient_id": m.recipient_id, "message_type": m.message_type, "content": m.content, "sent_at": m.sent_at.isoformat(), "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None, "read_at": m.read_at.isoformat() if m.read_at else None} for m in messages]

@app.get("/status")
async def get_status(user_id: str = Query(..., regex=r"^\d+:\d+$"), db: Session = Depends(get_db)):
    uid, _ = map(int, user_id.split(":"))
    now = datetime.datetime.now(datetime.timezone.utc)
    statuses = db.query(StatusDB).filter(StatusDB.expires_at > now).all()
    return [{"user_id": s.user_id, "content_type": s.content_type, "content": s.content, "posted_at": s.posted_at.isoformat()} for s in statuses]

@app.post("/status")
async def post_status(user_id: str = Form(...), content_type: str = Form(...), content: str = Form(...), db: Session = Depends(get_db)):
    uid, _ = map(int, user_id.split(":"))
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    status = StatusDB(user_id=uid, content_type=content_type, content=content, expires_at=expires_at)
    db.add(status)
    db.commit()
    return {"message": "Status posted"}

@app.get("/calls")
async def get_calls(user_id: str = Query(..., regex=r"^\d+:\d+$"), db: Session = Depends(get_db)):
    uid, _ = map(int, user_id.split(":"))
    calls = db.query(CallDB).filter((CallDB.caller_id == uid) | (CallDB.recipient_id == uid)).all()
    return [{"caller_id": c.caller_id, "recipient_id": c.recipient_id, "call_type": c.call_type, "start_time": c.start_time.isoformat(), "end_time": c.end_time.isoformat() if c.end_time else None} for c in calls]

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)