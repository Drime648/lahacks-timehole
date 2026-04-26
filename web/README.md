# TimeHole Web

This is a full-stack web app for:

- user registration and login
- saving focus schedules and study mode
- selecting blocked categories
- entering a focus summary for later LLM-based on-topic checks
- saving substring blacklist entries
- capturing the user's source IP address from the incoming request and storing it in MongoDB

## Run

1. Install dependencies:

```bash
npm install
```

2. Set environment variables:

```bash
cp .env.example .env
```

3. Start the API:

```bash
export $(grep -v '^#' .env | xargs)
npm run dev:server
```

4. Start the frontend:

```bash
npm run dev:client
```

The Vite frontend runs on `http://localhost:3000` and proxies `/api` to the backend on `http://localhost:4000`.

## Docker Compose

From the repo root:

```bash
cp .env.example .env
```

Edit `.env` and fill in only:

- `MONGODB_URI`
- `GEMINI_API_KEY`

Then run:

```bash
docker compose up --build
```

This starts:

- `web` on `http://localhost:3000`
- `gateway` proxy on `http://localhost:8080`
- MongoDB Atlas via `MONGODB_URI`

So on a fresh machine the full setup is:

1. Clone the repo
2. Copy `.env.example` to `.env`
3. Paste in your MongoDB Atlas URI and Gemini API key
4. Run `docker compose up --build`
