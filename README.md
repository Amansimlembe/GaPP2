# GaPP2
A job seeker and employer platform built with FastAPI and MongoDB.

## Setup
1. Clone the repo: `git clone https://github.com/Amansimlembe/GaPP2.git`
2. Install dependencies: `pip install -r requirements.txt`
3. Create a `.env` file with `SECRET_KEY=your-very-secure-key-here`
4. Run locally: `uvicorn main:app --host 0.0.0.0 --port 8000`
5. Serve frontend: `cd static && python -m http.server 8080`

## Deployment
- **Backend**: Deployed on Render (`https://gapp2-znqj.onrender.com`)
- **Frontend**: Deployed on Vercel (`https://ga-pp-2.vercel.app`)

## Features
- User registration and login
- Job posting and applications
- Real-time messaging via WebSocket