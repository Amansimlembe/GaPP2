from fastapi import FastAPI, HTTPException, WebSocket, Depends, UploadFile, File, Form
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pymongo import MongoClient
from pydantic import BaseModel
from typing import Optional, Dict
import datetime
from jose import jwt
import os
from passlib.context import CryptContext
import uvicorn
from dotenv import load_dotenv
import base64
import requests  # For AI job fetching simulation

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = "mongodb+srv://GaPP:Ammy%40123@cluster0.mv3zr.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
try:
    client = MongoClient(MONGO_URI)
    db = client["jobseeker_app"]
    client.admin.command('ping')
except Exception as e:
    raise Exception(f"MongoDB connection failed: {e}")

users_collection = db["users"]
messages_collection = db["messages"]
jobs_collection = db["jobs"]
jobseekers_collection = db["jobseekers"]
job_applications_collection = db["job_applications"]
groups_collection = db["groups"]

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key")
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

class User(BaseModel):
    email: str
    phone_number: str
    password: str
    role: int
    created_at: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)

class Job(BaseModel):
    user_id: int
    title: str
    description: str
    requirements: str
    deadline: str
    employer_email: str
    company_name: Optional[str] = None
    status: str = "active"
    created_at: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)

class JobSeeker(BaseModel):
    user_id: int
    email: str
    cv_path: Optional[str] = None
    skills: Optional[str] = None

class JobApplication(BaseModel):
    job_id: int
    user_id: int
    applied_at: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)

class Message(BaseModel):
    sender_id: int
    recipient_id: Optional[int] = None
    group_id: Optional[int] = None
    message_type: str
    content: str
    sent_at: datetime.datetime = datetime.datetime.now(datetime.timezone.utc)

class Group(BaseModel):
    name: str
    members: list[int]

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
        user = users_collection.find_one({"_id": user_id})
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user
    except:
        raise HTTPException(status_code=401, detail="Invalid token")

active_connections: Dict[int, WebSocket] = {}
webrtc_signals: Dict[str, list] = {}

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await websocket.accept()
    active_connections[user_id] = websocket
    try:
        while True:
            data = await websocket.receive_json()
            if "type" in data and data["type"] == "webrtc_signal":
                room = data["room"]
                if room not in webrtc_signals:
                    webrtc_signals[room] = []
                webrtc_signals[room].append(data["signal"])
                for uid, ws in active_connections.items():
                    if uid != user_id and uid in [int(data["to"]) if "to" in data else uid]:
                        await ws.send_json(data)
            else:
                message = {
                    "sender_id": user_id,
                    "recipient_id": data.get("recipient_id"),
                    "group_id": data.get("group_id"),
                    "message_type": data.get("message_type", "text"),
                    "content": data["content"],
                    "sent_at": datetime.datetime.now(datetime.timezone.utc)
                }
                messages_collection.insert_one(message)
                if message["recipient_id"]:
                    if message["recipient_id"] in active_connections:
                        await active_connections[message["recipient_id"]].send_json(message)
                elif message["group_id"]:
                    group = groups_collection.find_one({"_id": message["group_id"]})
                    for member in group["members"]:
                        if member in active_connections and member != user_id:
                            await active_connections[member].send_json(message)
                await websocket.send_json(message)
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        del active_connections[user_id]

@app.post("/register")
async def register(user: User):
    if users_collection.find_one({"email": user.email}) or users_collection.find_one({"phone_number": user.phone_number}):
        raise HTTPException(status_code=400, detail="Email or phone number already exists")
    hashed_password = get_password_hash(user.password)
    user_dict = user.dict()
    user_dict["password"] = hashed_password
    last_user = users_collection.find_one(sort=[("_id", -1)])
    user_dict["_id"] = (last_user["_id"] + 1) if last_user else 1
    users_collection.insert_one(user_dict)
    if user.role == 0:
        jobseekers_collection.insert_one({"_id": user_dict["_id"], "email": user.email})
    token = create_access_token({"sub": str(user_dict["_id"])})
    return {"access_token": token, "role": user.role, "user_id": user_dict["_id"]}

@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = users_collection.find_one({"$or": [{"email": form_data.username}, {"phone_number": form_data.username}]})
    if not user or not verify_password(form_data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(user["_id"])})
    return {"access_token": token, "role": user["role"], "user_id": user["_id"]}

@app.post("/employer/post_job")
async def post_job(job: Job, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != 1:
        raise HTTPException(status_code=403, detail="Not authorized")
    job_dict = job.dict()
    last_job = jobs_collection.find_one(sort=[("_id", -1)])
    job_dict["_id"] = (last_job["_id"] + 1) if last_job else 1
    jobs_collection.insert_one(job_dict)
    return {"message": "Job posted successfully"}

@app.get("/jobs/ai_fetched")
async def ai_fetched_jobs():
    # Simulate AI fetching (e.g., using a free API or mock data)
    response = requests.get("https://api.github.com/repos/github/jobs/contents/sample_jobs.json")
    if response.status_code == 200:
        jobs = response.json()
        return [{"title": job["name"], "description": "Sample job", "requirements": "N/A", "deadline": "N/A", "employer_email": "ai@example.com"} for job in jobs[:5]]
    return [{"title": "AI Job " + str(i), "description": "Sample AI job", "requirements": "N/A", "deadline": "N/A", "employer_email": "ai@example.com"} for i in range(1, 6)]

@app.post("/jobseeker/apply")
async def apply_for_job(job_id: int, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != 0:
        raise HTTPException(status_code=403, detail="Not authorized")
    last_app = job_applications_collection.find_one(sort=[("_id", -1)])
    app_dict = {"job_id": job_id, "user_id": current_user["_id"], "applied_at": datetime.datetime.now(datetime.timezone.utc)}
    app_dict["_id"] = (last_app["_id"] + 1) if last_app else 1
    job_applications_collection.insert_one(app_dict)
    return {"message": "Application submitted"}

@app.post("/jobseeker/update_cv")
async def update_cv(cv: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if current_user["role"] != 0:
        raise HTTPException(status_code=403, detail="Not authorized")
    cv_path = f"uploads/cv_{current_user['_id']}.pdf"
    os.makedirs("uploads", exist_ok=True)
    with open(cv_path, "wb") as f:
        f.write(await cv.read())
    # Simulate AI enhancement (basic keyword extraction)
    skills = "Python, JavaScript"  # Placeholder; use pdf.js in frontend for real extraction
    jobseekers_collection.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"cv_path": cv_path, "skills": skills}},
        upsert=True
    )
    return {"message": "CV updated", "skills": skills}

@app.post("/groups/create")
async def create_group(name: str = Form(...), members: str = Form(...), current_user: dict = Depends(get_current_user)):
    group_dict = {"name": name, "members": [current_user["_id"]] + [int(m) for m in members.split(",")]}
    last_group = groups_collection.find_one(sort=[("_id", -1)])
    group_dict["_id"] = (last_group["_id"] + 1) if last_group else 1
    groups_collection.insert_one(group_dict)
    return {"group_id": group_dict["_id"]}

@app.get("/dashboard/{role}", response_class=HTMLResponse)
async def dashboard(role: str, current_user: dict = Depends(get_current_user)):
    if role == "jobseeker" and current_user["role"] == 0:
        with open("static/jobseeker_dashboard.html", "r") as f:
            return HTMLResponse(content=f.read())
    elif role == "employer" and current_user["role"] == 1:
        with open("static/employer_dashboard.html", "r") as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=403, detail="Not authorized")

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