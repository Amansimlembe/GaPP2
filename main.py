from fastapi import FastAPI, HTTPException, WebSocket, Depends, UploadFile, File
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pydantic import BaseModel, Optional
import datetime
from jose import jwt
import os
from passlib.context import CryptContext
from typing import Dict
import uvicorn

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "https://ga-pp-2.vercel.app",
        "https://ga-pp-2-amansimlembes-projects.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB Atlas Connection
MONGO_URI = "mongodb+srv://GaPP:Ammy%40123@cluster0.mv3zr.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["jobseeker_app"]
users_collection = db["users"]
messages_collection = db["messages"]
jobs_collection = db["jobs"]
jobseekers_collection = db["jobseekers"]
job_applications_collection = db["job_applications"]

# Security
SECRET_KEY = "your-secret-key"  # Change this in production
ALGORITHM = "HS256"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Models
class User(BaseModel):
    email: str
    phone_number: str
    password: str
    role: int
    created_at: datetime.datetime = datetime.datetime.utcnow()

class Job(BaseModel):
    user_id: int
    title: str
    description: str
    requirements: str
    deadline: str
    employer_email: str
    company_name: Optional[str] = None
    status: str = "active"
    created_at: datetime.datetime = datetime.datetime.utcnow()

class JobSeeker(BaseModel):
    user_id: int
    email: str
    cv_path: Optional[str] = None
    po_box: Optional[str] = None
    location: Optional[str] = None
    skills: Optional[str] = None
    experience: Optional[str] = None

class JobApplication(BaseModel):
    job_id: int
    user_id: int
    applied_at: datetime.datetime = datetime.datetime.utcnow()

class Message(BaseModel):
    sender_id: int
    recipient_id: int
    message_type: str = "text"
    content: str
    sent_at: datetime.datetime = datetime.datetime.utcnow()
    read_at: Optional[datetime.datetime] = None

# Authentication
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

# WebSocket Connections
active_connections: Dict[int, WebSocket] = {}

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await websocket.accept()
    active_connections[user_id] = websocket
    print(f"User {user_id} connected")

    try:
        while True:
            data = await websocket.receive_json()
            message = {
                "sender_id": user_id,
                "recipient_id": int(data["recipient_id"]),
                "message_type": data.get("message_type", "text"),
                "content": data["content"],
                "sent_at": datetime.datetime.utcnow(),
                "read_at": None
            }
            messages_collection.insert_one(message)

            # Send to sender and recipient instantly
            if user_id in active_connections:
                await active_connections[user_id].send_json(message)
            if data["recipient_id"] in active_connections:
                await active_connections[int(data["recipient_id"])].send_json(message)

    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        if user_id in active_connections:
            del active_connections[user_id]
            print(f"User {user_id} disconnected")

# Routes
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
    return {"access_token": token, "token_type": "bearer", "role": user.role}

@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = users_collection.find_one({"$or": [{"email": form_data.username}, {"phone_number": form_data.username}]})
    if not user or not verify_password(form_data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(user["_id"])})
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}

@app.get("/employer/dashboard")
async def employer_dashboard(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != 1:
        raise HTTPException(status_code=403, detail="Not authorized")
    jobs = list(jobs_collection.find({"user_id": current_user["_id"]}))
    applications = list(job_applications_collection.aggregate([
        {"$lookup": {"from": "jobs", "localField": "job_id", "foreignField": "_id", "as": "job"}},
        {"$lookup": {"from": "jobseekers", "localField": "user_id", "foreignField": "_id", "as": "jobseeker"}},
        {"$match": {"job.user_id": current_user["_id"]}}
    ]))
    return {"user_id": current_user["_id"], "jobs": jobs, "applications": applications}

@app.post("/employer/post_job")
async def post_job(job: Job, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != 1:
        raise HTTPException(status_code=403, detail="Not authorized")
    job_dict = job.dict()
    last_job = jobs_collection.find_one(sort=[("_id", -1)])
    job_dict["_id"] = (last_job["_id"] + 1) if last_job else 1
    jobs_collection.insert_one(job_dict)
    return {"message": "Job posted successfully"}

@app.get("/jobseeker/dashboard")
async def jobseeker_dashboard(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != 0:
        raise HTTPException(status_code=403, detail="Not authorized")
    jobs = list(jobs_collection.find({"status": "active"}))
    return {"user_id": current_user["_id"], "jobs": jobs}

@app.post("/jobseeker/update_cv")
async def update_cv(email: str, po_box: Optional[str] = None, location: Optional[str] = None, skills: Optional[str] = None, experience: Optional[str] = None, cv: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    if current_user["role"] != 0:
        raise HTTPException(status_code=403, detail="Not authorized")
    cv_path = f"uploads/cv_{current_user['_id']}.pdf"
    os.makedirs("uploads", exist_ok=True)
    with open(cv_path, "wb") as f:
        f.write(await cv.read())
    jobseekers_collection.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"email": email, "cv_path": cv_path, "po_box": po_box, "location": location, "skills": skills, "experience": experience}},
        upsert=True
    )
    return {"message": "CV updated successfully"}

@app.post("/jobseeker/apply")
async def apply_for_job(job_id: int, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != 0:
        raise HTTPException(status_code=403, detail="Not authorized")
    if job_applications_collection.find_one({"job_id": job_id, "user_id": current_user["_id"]}):
        raise HTTPException(status_code=400, detail="Already applied")
    last_app = job_applications_collection.find_one(sort=[("_id", -1)])
    app_dict = {"job_id": job_id, "user_id": current_user["_id"], "applied_at": datetime.datetime.utcnow()}
    app_dict["_id"] = (last_app["_id"] + 1) if last_app else 1
    job_applications_collection.insert_one(app_dict)
    return {"message": "Application submitted"}

@app.get("/messages/{recipient_id}")
async def get_messages(recipient_id: int, current_user: dict = Depends(get_current_user)):
    messages = list(messages_collection.find({
        "$or": [
            {"sender_id": current_user["_id"], "recipient_id": recipient_id},
            {"sender_id": recipient_id, "recipient_id": current_user["_id"]}
        ]
    }).sort("sent_at", 1))
    return messages

@app.get("/chat_list")
async def get_chat_list(current_user: dict = Depends(get_current_user)):
    pipeline = [
        {"$match": {"$or": [{"sender_id": current_user["_id"]}, {"recipient_id": current_user["_id"]}]}},
        {"$group": {
            "_id": {"$cond": [{"$eq": ["$sender_id", current_user["_id"]]}, "$recipient_id", "$sender_id"]},
            "last_message": {"$max": "$sent_at"},
            "unread": {"$sum": {"$cond": [{"$and": [{"$eq": ["$recipient_id", current_user["_id"]]}, {"$eq": ["$read_at", None]}]}, 1, 0]}}
        }},
        {"$lookup": {"from": "users", "localField": "_id", "foreignField": "_id", "as": "user"}},
        {"$lookup": {"from": "jobs", "localField": "_id", "foreignField": "user_id", "as": "job"}},
        {"$project": {
            "user_id": "$_id",
            "email": {"$arrayElemAt": ["$user.email", 0]},
            "company_name": {"$arrayElemAt": ["$job.company_name", 0]},
            "last_message": 1,
            "unread": 1
        }}
    ]
    chat_list = list(messages_collection.aggregate(pipeline))
    return chat_list

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)