from fastapi import FastAPI, HTTPException, WebSocket, Form, UploadFile, File, Depends
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
import requests
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

# PostgreSQL Setup
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
    language = Column(String, default="en")
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

class JobSeekerDB(Base):
    __tablename__ = "jobseekers"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    email = Column(String)
    cv_path = Column(String, nullable=True)
    skills = Column(String, nullable=True)

class JobApplicationDB(Base):
    __tablename__ = "job_applications"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    status = Column(String, default="Submitted")  # New field for status tracking
    applied_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

class MessageDB(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"))
    recipient_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    message_type = Column(String)
    content = Column(String)
    sent_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))
    read_at = Column(DateTime, nullable=True)

class GroupDB(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    category = Column(String, nullable=True)  # New field for industry-specific groups
    created_by = Column(Integer, ForeignKey("users.id"))

class GroupMemberDB(Base):
    __tablename__ = "group_members"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    user_id = Column(Integer, ForeignKey("users.id"))

class ContactsDB(Base):
    __tablename__ = "contacts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    contact_user_id = Column(Integer, ForeignKey("users.id"))
    contact_email = Column(String)
    contact_phone = Column(String, nullable=True)

class BadgeDB(Base):  # New table for gamification
    __tablename__ = "badges"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    badge_name = Column(String)
    awarded_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

class FeedbackDB(Base):  # New table for feedback
    __tablename__ = "feedback"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    content = Column(String)
    submitted_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc))

Base.metadata.create_all(bind=engine)

# Pydantic Models
class User(BaseModel):
    email: str
    phone_number: str
    password: str
    role: int
    language: Optional[str] = "en"

class Job(BaseModel):
    user_id: int
    title: str
    description: str
    requirements: str
    deadline: str
    employer_email: str
    company_name: Optional[str] = None

class JobApplication(BaseModel):
    job_id: int
    user_id: int
    status: Optional[str] = "Submitted"

class Message(BaseModel):
    sender_id: int
    recipient_id: Optional[int] = None
    group_id: Optional[int] = None
    message_type: str
    content: str

class Group(BaseModel):
    name: str
    category: Optional[str] = None
    members: List[int]

class Contact(BaseModel):
    user_id: int
    contact_email: str
    contact_phone: Optional[str] = None

class Feedback(BaseModel):
    user_id: int
    content: str

class InterviewSchedule(BaseModel):
    employer_id: int
    jobseeker_id: int
    job_id: int
    time: str

class NotificationSubscription(BaseModel):
    user_id: int
    channel: str  # "email" or "sms"

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
webrtc_signals: Dict[str, list] = {}
typing_users: Dict[int, set] = {}

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int, db: Session = Depends(get_db)):
    await websocket.accept()
    active_connections[user_id] = websocket
    try:
        while True:
            data = await websocket.receive_json()
            if "type" in data and data["type"] == "webrtc_signal":
                room = str(data["room"])
                if room not in webrtc_signals:
                    webrtc_signals[room] = []
                webrtc_signals[room].append(data["signal"])
                for uid, ws in active_connections.items():
                    if uid != user_id and uid in [int(data["to"]) if "to" in data else uid]:
                        await ws.send_json(data)
            elif "type" in data and data["type"] == "typing":
                recipient_id = int(data["to"])
                if recipient_id not in typing_users:
                    typing_users[recipient_id] = set()
                typing_users[recipient_id].add(user_id)
                if recipient_id in active_connections:
                    await active_connections[recipient_id].send_json({"type": "typing", "from": user_id})
                await asyncio.sleep(2)
                typing_users[recipient_id].discard(user_id)
                if recipient_id in active_connections and user_id not in typing_users.get(recipient_id, set()):
                    await active_connections[recipient_id].send_json({"type": "stop_typing", "from": user_id})
            else:
                message = MessageDB(
                    sender_id=user_id,
                    recipient_id=data.get("recipient_id"),
                    group_id=data.get("group_id"),
                    message_type=data.get("message_type", "text"),
                    content=data["content"]
                )
                db.add(message)
                db.commit()
                db.refresh(message)
                message_dict = {
                    "sender_id": message.sender_id,
                    "recipient_id": message.recipient_id,
                    "group_id": message.group_id,
                    "message_type": message.message_type,
                    "content": message.content,
                    "sent_at": message.sent_at.isoformat(),
                    "read_at": message.read_at.isoformat() if message.read_at else None
                }
                if message.recipient_id:
                    if message.recipient_id in active_connections:
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
        for recipient_id in typing_users:
            typing_users[recipient_id].discard(user_id)

# API Endpoints
@app.post("/register")
async def register(user: User, db: Session = Depends(get_db)):
    try:
        if db.query(UserDB).filter(UserDB.email == user.email).first():
            raise HTTPException(status_code=400, detail="Email already exists")
        if db.query(UserDB).filter(UserDB.phone_number == user.phone_number).first():
            raise HTTPException(status_code=400, detail="Phone number already exists")
        
        hashed_password = get_password_hash(user.password)
        last_user = db.query(UserDB).order_by(UserDB.id.desc()).first()
        user_id = (last_user.id + 1) if last_user else 1
        db_user = UserDB(
            id=user_id,
            email=user.email,
            phone_number=user.phone_number,
            password=hashed_password,
            role=user.role,
            language=user.language
        )
        db.add(db_user)
        db.commit()
        
        if user.role == 0:
            db_jobseeker = JobSeekerDB(user_id=user_id, email=user.email)
            db.add(db_jobseeker)
            db.commit()
            # Award "Newbie" badge
            db_badge = BadgeDB(user_id=user_id, badge_name="Newbie")
            db.add(db_badge)
            db.commit()
        
        logger.info(f"User {user_id} registered successfully")
        return {"user_id": user_id, "role": user.role}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        return JSONResponse(status_code=500, content={"detail": f"Internal server error: {str(e)}"})

@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(UserDB).filter((UserDB.email == username) | (UserDB.phone_number == username)).first()
    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"user_id": user.id, "role": user.role, "language": user.language}

@app.post("/employer/post_job")
async def post_job(job: Job, db: Session = Depends(get_db)):
    if job.user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    user = db.query(UserDB).filter(UserDB.id == job.user_id).first()
    if not user or user.role != 1:
        raise HTTPException(status_code=403, detail="Not authorized")
    last_job = db.query(JobDB).order_by(JobDB.id.desc()).first()
    job_id = (last_job.id + 1) if last_job else 1
    db_job = JobDB(
        id=job_id,
        user_id=job.user_id,
        title=job.title,
        description=job.description,
        requirements=job.requirements,
        deadline=job.deadline,
        employer_email=job.employer_email,
        company_name=job.company_name
    )
    db.add(db_job)
    db.commit()
    return {"message": "Job posted successfully"}

@app.get("/jobs/ai_fetched")
async def ai_fetched_jobs(db: Session = Depends(get_db)):
    try:
        # Mock LinkedIn API fetch
        return [
            {"title": "Software Engineer", "description": "Remote role at TechCo", "requirements": "Python, 3+ years", "deadline": "2025-04-01", "employer_email": "hr@techco.com"},
            {"title": "Data Analyst", "description": "Full-time at DataCorp", "requirements": "SQL, Python", "deadline": "2025-03-30", "employer_email": "jobs@datacorp.com"}
        ]
    except Exception as e:
        logger.error(f"AI job fetch failed: {e}")
        return []

@app.post("/jobseeker/apply")
async def apply_for_job(application: JobApplication, db: Session = Depends(get_db)):
    if application.user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    user = db.query(UserDB).filter(UserDB.id == application.user_id).first()
    if not user or user.role != 0:
        raise HTTPException(status_code=403, detail="Not authorized")
    if db.query(JobApplicationDB).filter_by(job_id=application.job_id, user_id=application.user_id).first():
        raise HTTPException(status_code=400, detail="Already applied")
    last_app = db.query(JobApplicationDB).order_by(JobApplicationDB.id.desc()).first()
    app_id = (last_app.id + 1) if last_app else 1
    db_app = JobApplicationDB(id=app_id, job_id=application.job_id, user_id=application.user_id, status=application.status)
    db.add(db_app)
    db.commit()
    employer = db.query(JobDB).filter(JobDB.id == application.job_id).first().user_id
    if employer in active_connections:
        await active_connections[employer].send_json({"type": "notification", "message": f"New application for job {application.job_id}"})
    # Award "First Apply" badge if first application
    if not db.query(JobApplicationDB).filter_by(user_id=application.user_id).count() > 1:
        db_badge = BadgeDB(user_id=application.user_id, badge_name="First Apply")
        db.add(db_badge)
        db.commit()
    return {"message": "Application submitted"}

@app.post("/jobseeker/update_cv")
async def update_cv(user_id: int = Form(...), cv: UploadFile = File(...), db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user or user.role != 0:
        raise HTTPException(status_code=403, detail="Not authorized")
    cv_path = f"uploads/cv_{user_id}.pdf"
    os.makedirs("uploads", exist_ok=True)
    with open(cv_path, "wb") as f:
        f.write(await cv.read())
    # Mock AI enhancement
    skills = "Python, JavaScript, SQL"  # Enhanced AI suggestion
    suggestions = {"formatting": "Use bullet points", "skills": "Add 'Data Analysis'"}
    db_jobseeker = db.query(JobSeekerDB).filter(JobSeekerDB.user_id == user_id).first()
    if not db_jobseeker:
        db_jobseeker = JobSeekerDB(user_id=user_id, email=user.email, cv_path=cv_path, skills=skills)
        db.add(db_jobseeker)
    else:
        db_jobseeker.cv_path = cv_path
        db_jobseeker.skills = skills
    db.commit()
    # Award "Profile Pro" badge
    db_badge = BadgeDB(user_id=user_id, badge_name="Profile Pro")
    db.add(db_badge)
    db.commit()
    return {"message": "CV updated", "skills": skills, "suggestions": suggestions}

@app.post("/groups/create")
async def create_group(user_id: int = Form(...), name: str = Form(...), category: str = Form(...), members: str = Form(...), db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    last_group = db.query(GroupDB).order_by(GroupDB.id.desc()).first()
    group_id = (last_group.id + 1) if last_group else 1
    db_group = GroupDB(id=group_id, name=name, category=category, created_by=user_id)
    db.add(db_group)
    db.commit()
    member_ids = [user_id] + [int(m) for m in members.split(",") if m]
    for member_id in member_ids:
        db_member = GroupMemberDB(group_id=group_id, user_id=member_id)
        db.add(db_member)
    db.commit()
    return {"group_id": group_id}

@app.get("/dashboard/{role}", response_class=HTMLResponse)
async def dashboard(role: str, user_id: int):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    db = SessionLocal()
    try:
        user = db.query(UserDB).filter(UserDB.id == user_id).first()
        if not user or (role == "jobseeker" and user.role != 0) or (role == "employer" and user.role != 1):
            raise HTTPException(status_code=403, detail="Not authorized")
        with open(f"static/{role}_dashboard.html", "r") as f:
            return HTMLResponse(content=f.read())
    finally:
        db.close()

@app.get("/jobseeker/dashboard")
async def jobseeker_dashboard(user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user or user.role != 0:
        raise HTTPException(status_code=403, detail="Not authorized")
    jobs = db.query(JobDB).filter(JobDB.status == "active").all()
    applications = db.query(JobApplicationDB).filter(JobApplicationDB.user_id == user_id).all()
    badges = db.query(BadgeDB).filter(BadgeDB.user_id == user_id).all()
    return {
        "user_id": user_id,
        "jobs": [{"id": j.id, "title": j.title, "description": j.description, "requirements": j.requirements, "deadline": j.deadline} for j in jobs],
        "applications": [{"job_id": a.job_id, "status": a.status} for a in applications],
        "badges": [{"name": b.badge_name, "awarded_at": b.awarded_at.isoformat()} for b in badges]
    }

@app.get("/employer/dashboard")
async def employer_dashboard(user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user or user.role != 1:
        raise HTTPException(status_code=403, detail="Not authorized")
    jobs = db.query(JobDB).filter(JobDB.id == user_id).all()
    applications = db.query(JobApplicationDB).join(JobDB, JobApplicationDB.job_id == JobDB.id).filter(JobDB.user_id == user_id).all()
    badges = db.query(BadgeDB).filter(BadgeDB.user_id == user_id).all()
    return {
        "user_id": user_id,
        "jobs": [{"id": j.id, "title": j.title, "description": j.description} for j in jobs],
        "applications": [{"job_id": a.job_id, "user_id": a.user_id, "status": a.status, "applied_at": a.applied_at.isoformat()} for a in applications],
        "badges": [{"name": b.badge_name, "awarded_at": b.awarded_at.isoformat()} for b in badges]
    }

@app.get("/chat_list")
async def get_chat_list(user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    messages = db.query(MessageDB).filter((MessageDB.sender_id == user_id) | (MessageDB.recipient_id == user_id)).all()
    chat_dict = {}
    for msg in messages:
        other_id = msg.recipient_id if msg.sender_id == user_id else msg.sender_id
        if other_id and other_id not in chat_dict:
            other_user = db.query(UserDB).filter(UserDB.id == other_id).first()
            if not other_user:
                continue
            if user.role == 0 and other_user.role == 1 and msg.sender_id != other_id:
                continue
            chat_dict[other_id] = {
                "user_id": other_id,
                "email": other_user.email,
                "phone_number": other_user.phone_number,
                "last_message": msg.sent_at.isoformat(),
                "unread": 0 if msg.read_at else 1
            }
    return list(chat_dict.values())

@app.get("/messages/{recipient_id}")
async def get_messages(recipient_id: int, user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    recipient = db.query(UserDB).filter(UserDB.id == recipient_id).first()
    if user.role == 0 and recipient.role == 1:
        first_msg = db.query(MessageDB).filter(
            ((MessageDB.sender_id == user_id) & (MessageDB.recipient_id == recipient_id)) |
            ((MessageDB.sender_id == recipient_id) & (MessageDB.recipient_id == user_id))
        ).order_by(MessageDB.sent_at).first()
        if first_msg and first_msg.sender_id != recipient_id:
            raise HTTPException(status_code=403, detail="Employer must initiate chat")
    messages = db.query(MessageDB).filter(
        ((MessageDB.sender_id == user_id) & (MessageDB.recipient_id == recipient_id)) |
        ((MessageDB.sender_id == recipient_id) & (MessageDB.recipient_id == user_id))
    ).order_by(MessageDB.sent_at).all()
    return [{"sender_id": m.sender_id, "recipient_id": m.recipient_id, "message_type": m.message_type, "content": m.content, "sent_at": m.sent_at.isoformat(), "read_at": m.read_at.isoformat() if m.read_at else None} for m in messages]

@app.get("/groups")
async def get_groups(user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    groups = db.query(GroupDB).join(GroupMemberDB, GroupDB.id == GroupMemberDB.group_id).filter(GroupMemberDB.user_id == user_id).all()
    return [{"id": g.id, "name": g.name, "category": g.category} for g in groups]

@app.get("/messages/group/{group_id}")
async def get_group_messages(group_id: int, user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    if not db.query(GroupMemberDB).filter_by(group_id=group_id, user_id=user_id).first():
        raise HTTPException(status_code=403, detail="Not a group member")
    messages = db.query(MessageDB).filter(MessageDB.group_id == group_id).order_by(MessageDB.sent_at).all()
    return [{"sender_id": m.sender_id, "group_id": m.group_id, "message_type": m.message_type, "content": m.content, "sent_at": m.sent_at.isoformat(), "read_at": m.read_at.isoformat() if m.read_at else None} for m in messages]

# Contacts Endpoints
@app.post("/contacts/add")
async def add_contact(contact: Contact, db: Session = Depends(get_db)):
    if contact.user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    user = db.query(UserDB).filter(UserDB.id == contact.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    contact_user = db.query(UserDB).filter((UserDB.email == contact.contact_email) | (UserDB.phone_number == contact.contact_phone)).first()
    if not contact_user:
        raise HTTPException(status_code=404, detail="Contact not found")
    if db.query(ContactsDB).filter_by(user_id=contact.user_id, contact_user_id=contact_user.id).first():
        raise HTTPException(status_code=400, detail="Contact already exists")
    last_contact = db.query(ContactsDB).order_by(ContactsDB.id.desc()).first()
    contact_id = (last_contact.id + 1) if last_contact else 1
    db_contact = ContactsDB(
        id=contact_id,
        user_id=contact.user_id,
        contact_user_id=contact_user.id,
        contact_email=contact_user.email,
        contact_phone=contact_user.phone_number
    )
    db.add(db_contact)
    db.commit()
    return {"message": "Contact added", "contact_id": contact_id}

@app.get("/contacts/list")
async def list_contacts(user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    contacts = db.query(ContactsDB).filter(ContactsDB.user_id == user_id).all()
    return [{"contact_user_id": c.contact_user_id, "email": c.contact_email, "phone": c.contact_phone} for c in contacts]

@app.post("/contacts/remove")
async def remove_contact(user_id: int = Form(...), contact_user_id: int = Form(...), db: Session = Depends(get_db)):
    if user_id <= 0 or contact_user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id or contact_user_id")
    contact = db.query(ContactsDB).filter_by(user_id=user_id, contact_user_id=contact_user_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    db.delete(contact)
    db.commit()
    return {"message": "Contact removed"}

# New Endpoints
@app.post("/feedback")
async def submit_feedback(feedback: Feedback, db: Session = Depends(get_db)):
    db_feedback = FeedbackDB(user_id=feedback.user_id, content=feedback.content)
    db.add(db_feedback)
    db.commit()
    return {"message": "Feedback submitted"}

@app.get("/jobs/advanced_match")
async def advanced_job_match(user_id: int, db: Session = Depends(get_db)):
    jobseeker = db.query(JobSeekerDB).filter(JobSeekerDB.user_id == user_id).first()
    if not jobseeker:
        raise HTTPException(status_code=404, detail="Job seeker not found")
    skills = jobseeker.skills.split(", ") if jobseeker.skills else []
    jobs = db.query(JobDB).filter(JobDB.status == "active").all()
    # Mock advanced AI matching
    matched_jobs = [
        {"id": j.id, "title": j.title, "description": j.description, "match_score": sum(1 for s in skills if s in j.requirements) * 20}
        for j in jobs if any(s in j.requirements for s in skills)
    ]
    return sorted(matched_jobs, key=lambda x: x["match_score"], reverse=True)

@app.post("/schedule_interview")
async def schedule_interview(schedule: InterviewSchedule, db: Session = Depends(get_db)):
    # Mock calendar sync
    employer = db.query(UserDB).filter(UserDB.id == schedule.employer_id).first()
    jobseeker = db.query(UserDB).filter(UserDB.id == schedule.jobseeker_id).first()
    if employer.role != 1 or jobseeker.role != 0:
        raise HTTPException(status_code=403, detail="Invalid roles")
    if schedule.jobseeker_id in active_connections:
        await active_connections[schedule.jobseeker_id].send_json({
            "type": "notification",
            "message": f"Interview scheduled for job {schedule.job_id} at {schedule.time}"
        })
    return {"message": "Interview scheduled", "details": schedule.dict()}

@app.post("/generate_cover_letter")
async def generate_cover_letter(job_id: int = Form(...), user_id: int = Form(...), db: Session = Depends(get_db)):
    job = db.query(JobDB).filter(JobDB.id == job_id).first()
    jobseeker = db.query(JobSeekerDB).filter(JobSeekerDB.user_id == user_id).first()
    if not job or not jobseeker:
        raise HTTPException(status_code=404, detail="Job or job seeker not found")
    # Mock AI cover letter
    cover_letter = f"Dear Hiring Manager,\n\nI am excited to apply for the {job.title} position at {job.company_name or 'your company'}. My skills in {jobseeker.skills} align well with the requirements.\n\nSincerely,\n{jobseeker.email}"
    return {"cover_letter": cover_letter}

@app.post("/subscribe_notifications")
async def subscribe_notifications(subscription: NotificationSubscription, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.id == subscription.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    # Mock notification subscription
    return {"message": f"Subscribed to {subscription.channel} notifications"}

@app.get("/", response_class=HTMLResponse)
async def login_page():
    with open("static/login.html", "r") as f:
        return HTMLResponse(content=f.read())

@app.get("/register", response_class=HTMLResponse)
async def register_page():
    with open("static/register.html", "r") as f:
        return HTMLResponse(content=f.read())

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)