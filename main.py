from fastapi import FastAPI, HTTPException, WebSocket, Form, UploadFile, File, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import Optional, Dict
import datetime
import os
from passlib.context import CryptContext
import uvicorn
from dotenv import load_dotenv
import requests
import logging

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

class GroupDB(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)

class GroupMemberDB(Base):
    __tablename__ = "group_members"
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"))
    user_id = Column(Integer, ForeignKey("users.id"))

Base.metadata.create_all(bind=engine)

# Pydantic Models
class User(BaseModel):
    email: str
    phone_number: str
    password: str
    role: int

class Job(BaseModel):
    user_id: int
    title: str
    description: str
    requirements: str
    deadline: str
    employer_email: str
    company_name: Optional[str] = None

class JobSeeker(BaseModel):
    user_id: int
    email: str
    cv_path: Optional[str] = None
    skills: Optional[str] = None

class JobApplication(BaseModel):
    job_id: int
    user_id: int

class Message(BaseModel):
    sender_id: int
    recipient_id: Optional[int] = None
    group_id: Optional[int] = None
    message_type: str
    content: str

class Group(BaseModel):
    name: str
    members: list[int]

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
                    "sent_at": message.sent_at.isoformat()
                }
                if message.recipient_id and message.recipient_id in active_connections:
                    await active_connections[message.recipient_id].send_json(message_dict)
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

# API Endpoints (No Token Authentication)
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
            role=user.role
        )
        db.add(db_user)
        db.commit()
        
        if user.role == 0:
            db_jobseeker = JobSeekerDB(user_id=user_id, email=user.email)
            db.add(db_jobseeker)
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
    return {"user_id": user.id, "role": user.role}

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
async def ai_fetched_jobs():
    try:
        response = requests.get("https://api.github.com/repos/github/jobs/contents/sample_jobs.json")
        if response.status_code == 200:
            jobs = response.json()
            return [{"title": job["name"], "description": "Sample job", "requirements": "N/A", "deadline": "N/A", "employer_email": "ai@example.com"} for job in jobs[:5]]
        return [{"title": f"AI Job {i}", "description": "Sample AI job", "requirements": "N/A", "deadline": "N/A", "employer_email": "ai@example.com"} for i in range(1, 6)]
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
    last_app = db.query(JobApplicationDB).order_by(JobApplicationDB.id.desc()).first()
    app_id = (last_app.id + 1) if last_app else 1
    db_app = JobApplicationDB(id=app_id, job_id=application.job_id, user_id=application.user_id)
    db.add(db_app)
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
    skills = "Python, JavaScript"  # Placeholder
    db_jobseeker = db.query(JobSeekerDB).filter(JobSeekerDB.user_id == user_id).first()
    if not db_jobseeker:
        db_jobseeker = JobSeekerDB(user_id=user_id, email=user.email, cv_path=cv_path, skills=skills)
        db.add(db_jobseeker)
    else:
        db_jobseeker.cv_path = cv_path
        db_jobseeker.skills = skills
    db.commit()
    return {"message": "CV updated", "skills": skills}

@app.post("/groups/create")
async def create_group(user_id: int = Form(...), name: str = Form(...), members: str = Form(...), db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    last_group = db.query(GroupDB).order_by(GroupDB.id.desc()).first()
    group_id = (last_group.id + 1) if last_group else 1
    db_group = GroupDB(id=group_id, name=name)
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
    return {
        "user_id": user_id,
        "jobs": [{"id": j.id, "title": j.title, "description": j.description, "requirements": j.requirements, "deadline": j.deadline} for j in jobs]
    }

@app.get("/employer/dashboard")
async def employer_dashboard(user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user or user.role != 1:
        raise HTTPException(status_code=403, detail="Not authorized")
    jobs = db.query(JobDB).filter(JobDB.user_id == user_id).all()
    applications = db.query(JobApplicationDB).join(JobDB, JobApplicationDB.job_id == JobDB.id).filter(JobDB.user_id == user_id).all()
    return {
        "user_id": user_id,
        "jobs": [{"id": j.id, "title": j.title, "description": j.description} for j in jobs],
        "applications": [{"job_id": a.job_id, "user_id": a.user_id, "applied_at": a.applied_at.isoformat()} for a in applications]
    }

@app.get("/chat_list")
async def get_chat_list(user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    messages = db.query(MessageDB).filter((MessageDB.sender_id == user_id) | (MessageDB.recipient_id == user_id)).all()
    chat_dict = {}
    for msg in messages:
        other_id = msg.recipient_id if msg.sender_id == user_id else msg.sender_id
        if other_id and other_id not in chat_dict:
            user = db.query(UserDB).filter(UserDB.id == other_id).first()
            chat_dict[other_id] = {"user_id": other_id, "email": user.email, "last_message": msg.sent_at.isoformat(), "unread": 0}
    return list(chat_dict.values())

@app.get("/messages/{recipient_id}")
async def get_messages(recipient_id: int, user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    messages = db.query(MessageDB).filter(
        ((MessageDB.sender_id == user_id) & (MessageDB.recipient_id == recipient_id)) |
        ((MessageDB.sender_id == recipient_id) & (MessageDB.recipient_id == user_id))
    ).order_by(MessageDB.sent_at).all()
    return [{"sender_id": m.sender_id, "recipient_id": m.recipient_id, "message_type": m.message_type, "content": m.content, "sent_at": m.sent_at.isoformat()} for m in messages]

@app.get("/groups")
async def get_groups(user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    groups = db.query(GroupDB).join(GroupMemberDB, GroupDB.id == GroupMemberDB.group_id).filter(GroupMemberDB.user_id == user_id).all()
    return [{"id": g.id, "name": g.name} for g in groups]

@app.get("/messages/group/{group_id}")
async def get_group_messages(group_id: int, user_id: int, db: Session = Depends(get_db)):
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    messages = db.query(MessageDB).filter(MessageDB.group_id == group_id).order_by(MessageDB.sent_at).all()
    return [{"sender_id": m.sender_id, "group_id": m.group_id, "message_type": m.message_type, "content": m.content, "sent_at": m.sent_at.isoformat()} for m in messages]

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