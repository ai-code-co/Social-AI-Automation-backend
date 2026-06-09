# Social AI Automation Backend

FastAPI backend for the Social AI Automation app. It provides authentication, brand management, post generation, social account connections, publishing services, and scheduled publishing.

## Tech Stack

- FastAPI
- Uvicorn
- SQLAlchemy
- PostgreSQL via `psycopg`
- Redis
- Celery
- APScheduler
- OpenAI API

## Getting Started

Create and activate a virtual environment:

```bash
python -m venv venv
venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file with the required settings:

```env
OPENAI_API_KEY=your_openai_api_key
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/social_ai
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=replace_with_a_secure_secret
DEBUG=true
```

Start the API:

```bash
uvicorn app.main:app --reload
```

The API will be available at:

```text
http://localhost:8000
```

## Optional Integrations

Add these environment variables only when the matching integration is enabled:

```env
META_APP_ID=
META_APP_SECRET=
META_REDIRECT_URI=
INSTAGRAM_APP_ID=
INSTAGRAM_APP_SECRET=
INSTAGRAM_REDIRECT_URI=
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=
X_CLIENT_ID=
X_CLIENT_SECRET=
X_REDIRECT_URI=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=
PUBLIC_APP_URL=
```

## Deployment Notes

- Deploy the contents of this `backend` folder as the backend repository.
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Configure production `DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY`, and `SECRET_KEY` in the hosting provider.
- Update the frontend API URL and backend CORS settings for your deployed domains.
