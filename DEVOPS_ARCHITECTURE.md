# Redirx API-to-Worker Architecture Documentation
## For DevOps and Render Deployment Optimization

**Generated:** 2026-02-06
**Purpose:** Complete reference for optimizing Render deployment setup

---

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Service Components](#service-components)
3. [Database Schema](#database-schema)
4. [API Endpoints](#api-endpoints)
5. [Worker Process](#worker-process)
6. [Job Queue Mechanism](#job-queue-mechanism)
7. [Environment Variables](#environment-variables)
8. [Deployment Configuration](#deployment-configuration)
9. [Performance & Scaling](#performance--scaling)
10. [Monitoring & Health Checks](#monitoring--health-checks)

---

## Architecture Overview

### System Architecture
```
┌──────────────┐     HTTP/REST    ┌──────────────┐
│   Frontend   │ ────────────────> │  Backend API │
│  (Vite/React)│                   │   (Flask)    │
└──────────────┘                   └───────┬──────┘
                                           │
                                           │ writes jobs
                                           ▼
                                   ┌───────────────┐
                                   │   Supabase    │
                                   │  PostgreSQL   │
                                   │  + pgvector   │
                                   └───────┬───────┘
                                           │
                                           │ polls for jobs
                                           ▼
                                   ┌───────────────┐
                                   │     Worker    │
                                   │   (Python)    │
                                   └───────────────┘
```

### Key Patterns
- **Asynchronous Processing**: API creates jobs instantly, worker processes them
- **Database as Queue**: Uses Supabase PostgreSQL for job queue (no Redis/RabbitMQ needed)
- **Polling-based**: Worker polls database every 5 seconds for pending jobs
- **Stateless Services**: API and Worker are stateless (can scale horizontally)
- **Event-driven Progress**: Worker updates job status/progress in real-time

---

## Service Components

### 1. Frontend (Static Site)
- **Technology**: Vite + React + TypeScript
- **Port**: 3000 (dev), N/A (production static)
- **Build Output**: `frontend/build/`
- **API Communication**: REST API via fetch
- **Authentication**: JWT tokens (access + refresh)

### 2. Backend API (Web Service)
- **Technology**: Flask 3.1.2 + Gunicorn
- **Port**: 5001 (dev), 10000 (Render default)
- **Entry Point**: `backend.app:create_app()`
- **Purpose**:
  - Handle user authentication
  - Accept CSV uploads
  - Create migration jobs (write to DB)
  - Serve results (read from DB)
- **Process Model**: Stateless, request-response

### 3. Background Worker (Background Worker)
- **Technology**: Python 3.12+ with asyncio
- **Entry Point**: `python -m backend.worker`
- **Purpose**:
  - Poll database for pending jobs
  - Execute 7-stage pipeline
  - Update job progress/status
  - Store results in database
- **Process Model**: Long-running daemon with polling loop

---

## Database Schema

### Tables

#### `migration_sessions`
**Purpose**: Job queue and session metadata

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key (session ID) |
| `user_id` | TEXT | User identifier (FK to auth.users) |
| `status` | TEXT | Job status: `pending`, `processing`, `completed`, `failed` |
| `project_name` | TEXT | User-provided project name |
| `old_urls` | JSONB | List of old site URLs |
| `new_urls` | JSONB | List of new site URLs |
| `current_stage` | INTEGER | Current pipeline stage (1-based) |
| `stage_name` | TEXT | Human-readable stage name |
| `total_stages` | INTEGER | Total stages in pipeline (7) |
| `total_mappings` | INTEGER | Total redirect mappings created |
| `approved_mappings` | INTEGER | User-approved mappings |
| `created_at` | TIMESTAMPTZ | Job creation timestamp |
| `updated_at` | TIMESTAMPTZ | Last update timestamp |

**Indexes:**
```sql
CREATE INDEX idx_migration_sessions_user_id ON migration_sessions(user_id);
CREATE INDEX idx_migration_sessions_status_created
  ON migration_sessions(status, created_at) WHERE status = 'pending';
```

#### `webpage_embeddings`
**Purpose**: Store vector embeddings for similarity matching

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `session_id` | UUID | FK to migration_sessions |
| `url` | TEXT | Webpage URL |
| `site_type` | TEXT | 'old' or 'new' |
| `embedding` | VECTOR(1536) | OpenAI embedding vector |
| `extracted_text` | TEXT | HTML text content |
| `title` | TEXT | Page title |
| `created_at` | TIMESTAMPTZ | Creation timestamp |

#### `url_mappings`
**Purpose**: Store redirect mappings (results)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `session_id` | UUID | FK to migration_sessions |
| `old_url` | TEXT | Source URL |
| `new_url` | TEXT | Destination URL |
| `confidence_score` | FLOAT | Similarity score (0-1) |
| `match_type` | TEXT | 'exact_url', 'exact_html', 'semantic', 'manual' |
| `needs_review` | BOOLEAN | Low confidence flag |
| `is_approved` | BOOLEAN | User approval status |
| `created_at` | TIMESTAMPTZ | Creation timestamp |

#### `user_profiles`
**Purpose**: User accounts and quota tracking

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key (FK to auth.users) |
| `email` | TEXT | User email |
| `full_name` | TEXT | Display name |
| `company` | TEXT | Organization |
| `subscription_plan` | TEXT | 'free', 'pro', 'enterprise' |
| `usage_limit_redirects` | INTEGER | Monthly redirect limit (default: 1000) |
| `usage_current_month` | INTEGER | Current month usage count |
| `created_at` | TIMESTAMPTZ | Account creation |
| `updated_at` | TIMESTAMPTZ | Last profile update |

### Database Functions

#### `increment_user_usage(target_user_id TEXT, amount INT)`
- **Purpose**: Atomically increment user's monthly usage
- **Used By**: Worker after job completion
- **Security**: SECURITY DEFINER (bypasses RLS)

#### `match_pages(query_embedding VECTOR, target_site_type TEXT, ...)`
- **Purpose**: Vector similarity search using pgvector
- **Used By**: Pipeline PairingStage
- **Returns**: Similar pages with cosine similarity scores

---

## API Endpoints

### Authentication Routes (`/api/auth/*`)

#### `POST /api/auth/register`
**Purpose**: Create new user account

**Request:**
```json
{
  "email": "user@example.com",
  "password": "securepass123",
  "full_name": "John Doe"
}
```

**Response (201):**
```json
{
  "success": true,
  "user_id": "uuid",
  "email": "user@example.com",
  "access_token": "jwt...",
  "refresh_token": "jwt...",
  "email_confirmation_required": false
}
```

#### `POST /api/auth/login`
**Purpose**: Authenticate user

**Request:**
```json
{
  "email": "user@example.com",
  "password": "securepass123"
}
```

**Response (200):**
```json
{
  "success": true,
  "user_id": "uuid",
  "email": "user@example.com",
  "access_token": "jwt...",
  "refresh_token": "jwt..."
}
```

#### `POST /api/auth/logout`
**Headers:** `Authorization: Bearer <access_token>`

**Response (200):**
```json
{
  "success": true
}
```

#### `POST /api/auth/refresh`
**Purpose**: Refresh expired access token

**Request:**
```json
{
  "refresh_token": "jwt..."
}
```

**Response (200):**
```json
{
  "success": true,
  "access_token": "new_jwt...",
  "refresh_token": "new_refresh_jwt..."
}
```

#### `GET /api/auth/me`
**Headers:** `Authorization: Bearer <access_token>`

**Response (200):**
```json
{
  "success": true,
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "full_name": "John Doe",
    "subscription_plan": "free"
  }
}
```

---

### Pipeline Routes (`/api/*`)

#### `POST /api/process`
**Purpose**: Create new migration job (uploads CSVs)

**Headers:**
- `Authorization: Bearer <access_token>`
- `Content-Type: multipart/form-data`

**Request Body:**
- `old_csv`: CSV file (first column = URLs)
- `new_csv`: CSV file (first column = URLs)

**Response (200):**
```json
{
  "success": true,
  "message": "Pipeline completed successfully",
  "session_id": "uuid"
}
```

**Error Response (429 - Quota Exceeded):**
```json
{
  "success": false,
  "error": "Usage limit exceeded",
  "message": "You have used 1000 of 1000 redirects this month...",
  "current_usage": 1000,
  "limit": 1000
}
```

**Flow:**
1. Validate JWT token
2. Check user quota
3. Parse CSV files (extract URLs from first column)
4. Generate project name from new site domain
5. Insert row into `migration_sessions` with `status='pending'`
6. Return `session_id` immediately (job queued)

#### `GET /api/results/<session_id>`
**Purpose**: Retrieve job results

**Response (200):**
```json
{
  "success": true,
  "mappings": [
    {
      "old_url": "https://old.com/page1",
      "new_url": "https://new.com/page1",
      "confidence_score": 0.92,
      "match_type": "semantic",
      "needs_review": false
    }
  ],
  "stats": {
    "total": 150,
    "high_confidence": 120,
    "medium_confidence": 25,
    "low_confidence": 5,
    "approval_progress": 0.0
  },
  "session": {
    "id": "uuid",
    "status": "completed",
    "project_name": "example.com project",
    "created_at": "2026-02-06T12:00:00Z"
  }
}
```

---

### User Routes (`/api/user/*`)

#### `GET /api/user/dashboard`
**Headers:** `Authorization: Bearer <access_token>`

**Response (200):**
```json
{
  "success": true,
  "total_redirects": 1247,
  "total_sessions": 12,
  "approval_progress": 87.5,
  "average_confidence": 82.3,
  "recent_sessions": [
    {
      "id": "uuid",
      "project_name": "example.com project",
      "created_at": "2026-02-06T12:00:00Z",
      "total_mappings": 342,
      "status": "completed"
    }
  ]
}
```

#### `GET /api/user/sessions`
**Purpose**: List all user's migration sessions

**Headers:** `Authorization: Bearer <access_token>`

**Response (200):**
```json
{
  "success": true,
  "sessions": [
    {
      "id": "uuid",
      "user_id": "uuid",
      "status": "completed",
      "project_name": "My Migration",
      "created_at": "2026-02-06T12:00:00Z",
      "total_mappings": 150
    }
  ]
}
```

#### `GET /api/user/sessions/<session_id>/status`
**Purpose**: Poll job status during processing

**Headers:** `Authorization: Bearer <access_token>`

**Response (200):**
```json
{
  "success": true,
  "session_id": "uuid",
  "status": "processing",
  "project_name": "example.com project",
  "total_mappings": 0,
  "current_stage": 3,
  "stage_name": "ExactUrlMatchStage",
  "total_stages": 7
}
```

**Usage**: Frontend polls this endpoint every 2-3 seconds during job processing

#### `PUT /api/user/sessions/<session_id>`
**Purpose**: Update session project name

**Headers:** `Authorization: Bearer <access_token>`

**Request:**
```json
{
  "project_name": "New Project Name"
}
```

#### `GET /api/user/profile`
**Headers:** `Authorization: Bearer <access_token>`

**Response (200):**
```json
{
  "success": true,
  "profile": {
    "id": "uuid",
    "email": "user@example.com",
    "full_name": "John Doe",
    "company": "Acme Corp",
    "subscription_plan": "free",
    "usage_limit_redirects": 1000,
    "usage_current_month": 42
  }
}
```

#### `PUT /api/user/profile`
**Headers:** `Authorization: Bearer <access_token>`

**Request:**
```json
{
  "full_name": "Jane Doe",
  "company": "New Company Inc"
}
```

---

## Worker Process

### Entry Point
```bash
python -m backend.worker
```

**File:** `backend/worker.py`

### Main Loop
```python
while True:
    job = get_next_pending_job()  # Query DB for status='pending'

    if job:
        process_job(job)  # Execute pipeline
    else:
        time.sleep(POLL_INTERVAL)  # Wait 5 seconds
```

### Job Processing Flow

#### 1. Job Selection
```python
def get_next_pending_job():
    return client.table('migration_sessions') \
        .select('*') \
        .eq('status', 'pending') \
        .order('created_at') \
        .limit(1) \
        .execute()
```

**Strategy**: FIFO queue (oldest pending job first)

#### 2. Job Execution
```python
def process_job(session):
    session_id = UUID(session['id'])

    # Update status
    update_session_status(session_id, 'processing')

    # Extract URLs
    old_urls = session['old_urls']
    new_urls = session['new_urls']

    # Run pipeline (async)
    pipeline = Pipeline(input=(old_urls, new_urls), session_id=session_id)

    async for step in pipeline.iterate():
        # Update progress after each stage
        update_session_progress(
            session_id,
            current_stage=pipeline.current_stage_index + 1,
            stage_name=pipeline.stage_names[pipeline.current_stage_index],
            total_stages=pipeline.total_stages
        )

    # Update usage quota
    mappings = get_mappings_by_session(session_id)
    increment_usage(user_id, len(mappings))

    # Mark complete
    update_session_status(session_id, 'completed')
```

### Pipeline Stages (7 total)

1. **UrlPruneStage** - Filter asset URLs (CSS, JS, images)
2. **BlogPruneStage** - Filter individual blog posts
3. **ExactUrlMatchStage** - Match identical URL paths
4. **WebScraperStage** - Scrape HTML content (concurrent with aiohttp)
5. **HtmlPruneStage** - Match pages with duplicate HTML
6. **EmbedStage** - Generate OpenAI embeddings
7. **PairingStage** - Match via vector similarity (pgvector)

**Processing Time**:
- ~50-100 URLs: 30-60 seconds
- ~500 URLs: 3-5 minutes
- Dominated by web scraping and embedding generation

### Error Handling

```python
try:
    process_job(job)
    update_session_status(session_id, 'completed')
except Exception as e:
    print(f"Job failed: {e}")
    update_session_status(session_id, 'failed')
```

**Failed Jobs**:
- Status set to `'failed'`
- Not retried automatically
- User sees error in frontend

---

## Job Queue Mechanism

### Database as Queue
- **No external queue service required** (Redis, RabbitMQ, etc.)
- Uses PostgreSQL as job queue
- Simple and reliable for this workload

### Queue Properties
- **FIFO**: Jobs processed in creation order
- **Single Consumer**: Only one worker processes jobs (can scale with locking)
- **Polling Interval**: 5 seconds
- **Visibility**: Job state visible to frontend via polling

### Status Transitions
```
pending → processing → completed
                     ↘ failed
```

### Concurrency
**Current:** Single worker, sequential job processing

**Scaling Options:**
1. **Multiple Workers** (requires row locking):
```sql
SELECT * FROM migration_sessions
WHERE status = 'pending'
ORDER BY created_at
LIMIT 1
FOR UPDATE SKIP LOCKED
```

2. **Job Priorities** (add priority column):
```sql
ORDER BY priority DESC, created_at ASC
```

---

## Environment Variables

### Backend/Worker (Root `.env`)

**Required:**
```bash
# Supabase
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=service_role_key_here  # Use service_role, NOT anon

# OpenAI
OPENAI_API_KEY=sk-xxx
```

**Optional (with defaults):**
```bash
# Embedding Configuration
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSION=1536

# Matching Thresholds
HIGH_CONFIDENCE_THRESHOLD=0.85
MEDIUM_CONFIDENCE_THRESHOLD=0.7
AMBIGUITY_GAP_THRESHOLD=0.1

# CORS (production: set to frontend URL)
CORS_ORIGINS=*

# Flask
FLASK_ENV=development
FLASK_DEBUG=1
```

### Frontend (`frontend/.env`)

**Required:**
```bash
# Supabase (use anon key, NOT service_role)
VITE_SUPABASE_URL=https://xxx.supabase.co
VITE_SUPABASE_ANON_KEY=eyJxxx...
```

**Optional:**
```bash
# Only needed in production (dev uses Vite proxy)
VITE_API_BASE_URL=https://api.redirx.onrender.com

# PostHog conversion funnel analytics
VITE_PUBLIC_POSTHOG_KEY=phc_xxx
VITE_PUBLIC_POSTHOG_HOST=https://us.i.posthog.com
```

---

## Deployment Configuration

### Render Services

#### 1. Frontend (Static Site)
```yaml
name: redirx-frontend
type: static_site
buildCommand: cd frontend && npm install && npm run build
publishDir: frontend/build
envVars:
  - VITE_SUPABASE_URL
  - VITE_SUPABASE_ANON_KEY
  - VITE_API_BASE_URL
  - VITE_PUBLIC_POSTHOG_KEY
  - VITE_PUBLIC_POSTHOG_HOST
```

**Build Command:**
```bash
cd frontend && npm install && npm run build
```

**Publish Directory:** `frontend/build`

**Environment Variables:**
- `VITE_SUPABASE_URL`: Supabase project URL
- `VITE_SUPABASE_ANON_KEY`: Supabase anon/public key
- `VITE_API_BASE_URL`: Backend API URL (e.g., `https://redirx-api.onrender.com`)
- `VITE_PUBLIC_POSTHOG_KEY`: PostHog project API key
- `VITE_PUBLIC_POSTHOG_HOST`: PostHog host (`https://us.i.posthog.com` or `https://eu.i.posthog.com`)

#### 2. Backend API (Web Service)
```yaml
name: redirx-api
type: web
runtime: python3
buildCommand: pip install -r requirements.txt
startCommand: gunicorn backend.app:create_app()
envVars:
  - SUPABASE_URL
  - SUPABASE_KEY  # service_role
  - OPENAI_API_KEY
  - CORS_ORIGINS  # Set to frontend URL
```

**Start Command:**
```bash
gunicorn backend.app:create_app()
```

**Instance Type:** Standard (512MB RAM minimum)

**Auto-Deploy:** Yes (on git push)

**Environment Variables:**
- `SUPABASE_URL`: Supabase project URL
- `SUPABASE_KEY`: Supabase **service_role** key (NOT anon)
- `OPENAI_API_KEY`: OpenAI API key
- `CORS_ORIGINS`: Frontend URL (e.g., `https://redirx.onrender.com`)

#### 3. Worker (Background Worker)
```yaml
name: redirx-worker
type: background_worker
runtime: python3
buildCommand: pip install -r requirements.txt
startCommand: python -m backend.worker
envVars:
  - SUPABASE_URL
  - SUPABASE_KEY  # service_role
  - OPENAI_API_KEY
```

**Start Command:**
```bash
python -m backend.worker
```

**Instance Type:** Standard (512MB RAM minimum)

**Auto-Deploy:** Yes (on git push)

**Environment Variables:** Same as Backend API

### Health Checks

**Backend API:**
```bash
GET / → "Redirx backend is running!"
```

**Worker:**
- No HTTP endpoint (background daemon)
- Monitor via logs for "Worker polling..." messages
- Check database for jobs stuck in 'processing' state

---

## Performance & Scaling

### Current Bottlenecks

1. **Web Scraping** (WebScraperStage)
   - Uses aiohttp with concurrent requests
   - Limited by network I/O and target site rate limits
   - Current: ~10-20 concurrent requests

2. **Embedding Generation** (EmbedStage)
   - OpenAI API rate limits
   - Cost: ~$0.002 per 200 webpages
   - Current: Sequential requests (can parallelize)

3. **Vector Similarity** (PairingStage)
   - pgvector query performance
   - Scales with number of embeddings
   - Indexed by HNSW for fast lookups

### Scaling Strategies

#### Horizontal Scaling

**API Service:**
- ✅ Stateless, scales easily
- Add more Render instances
- Load balancing handled by Render

**Worker Service:**
- ⚠️ Requires row locking to prevent duplicate processing
- Multiple workers can poll simultaneously with `FOR UPDATE SKIP LOCKED`
- Alternative: Use single worker with higher parallelism

#### Optimization Opportunities

1. **Batch Embedding Generation**
   ```python
   # Current: Sequential
   for url in urls:
       embedding = openai.embed(url)

   # Better: Batch
   embeddings = openai.embed_batch(urls)  # Up to 2048 texts/batch
   ```

2. **Connection Pooling**
   - Supabase client: Reuse connections
   - aiohttp: Use single session with connection pool

3. **Caching**
   - Cache embeddings for identical URLs across sessions
   - Cache scraped HTML with TTL

4. **Database Indexes**
   ```sql
   CREATE INDEX idx_session_status ON migration_sessions(status, created_at);
   CREATE INDEX idx_mappings_session ON url_mappings(session_id);
   ```

### Resource Estimates

**Memory:**
- API: 256-512MB per instance
- Worker: 512MB-1GB (depends on job size)
- Embeddings: ~6KB per URL (1536 floats * 4 bytes)

**CPU:**
- API: Low (mostly I/O bound)
- Worker: Medium (HTML parsing, embedding compute)

**Network:**
- API: Low (small JSON payloads)
- Worker: High (web scraping, API calls)

**Supabase:**
- Storage: ~10KB per redirect mapping
- Queries: ~10-50 per job
- Vector search: Optimized with HNSW index

---

## Monitoring & Health Checks

### Key Metrics

#### API Service
- **Request Rate**: `/api/process` POST requests
- **Error Rate**: 4xx/5xx responses
- **Response Time**: p50, p95, p99
- **Auth Failures**: 401/403 responses

#### Worker Service
- **Jobs Processed**: Total completed jobs
- **Job Duration**: Time per job
- **Job Failures**: Failed job count
- **Queue Depth**: Pending jobs count
- **Stage Duration**: Time per pipeline stage

#### Database
- **Connection Count**: Active connections
- **Query Performance**: Slow queries (>1s)
- **Disk Usage**: Storage consumed
- **Vector Search Latency**: pgvector query time

### Logging

**API (`backend/app.py`):**
```python
print(f"[API] Created job {session_id} with {len(old_urls)} old URLs")
print(f"[API] Job queued for background processing")
```

**Worker (`backend/worker.py`):**
```python
print(f"[Worker] Found pending job: {job['id']}")
print(f"[Worker] Processing {len(old_urls)} old URLs and {len(new_urls)} new URLs")
print(f"[Worker] Job {session_id} completed successfully")
print(f"[Worker] Job failed with error: {e}")
```

**Pipeline Stages:**
- Each stage logs progress to stdout
- Worker captures and forwards to Render logs

### Alerting

**Critical Alerts:**
1. **Worker Down**: No log activity for >5 minutes
2. **Jobs Stuck**: Jobs in 'processing' for >15 minutes
3. **High Error Rate**: >10% of jobs failing
4. **Quota Exceeded**: User hitting rate limits

**Warning Alerts:**
1. **Queue Depth**: >10 pending jobs
2. **Slow Jobs**: Job duration >10 minutes
3. **Database Connections**: >80% of pool used

### Dashboard Queries

**Queue Depth:**
```sql
SELECT COUNT(*) FROM migration_sessions WHERE status = 'pending';
```

**Active Jobs:**
```sql
SELECT * FROM migration_sessions
WHERE status = 'processing'
ORDER BY updated_at DESC;
```

**Job Throughput (last hour):**
```sql
SELECT COUNT(*) FROM migration_sessions
WHERE status = 'completed'
AND updated_at > NOW() - INTERVAL '1 hour';
```

**Failed Jobs:**
```sql
SELECT * FROM migration_sessions
WHERE status = 'failed'
ORDER BY updated_at DESC
LIMIT 10;
```

**Average Job Duration:**
```sql
SELECT AVG(updated_at - created_at) as avg_duration
FROM migration_sessions
WHERE status = 'completed';
```

---

## Dependencies

### Python (`requirements.txt`)
```
Flask==3.1.2
flask-cors==6.0.1
gunicorn==23.0.0
PyJWT==2.10.1
supabase>=2.24.0
aiohttp==3.13.2
websockets>=15.0
openai>=1.50.0,<2.0.0
numpy==1.26.4
beautifulsoup4==4.12.3
lxml>=5.3.0
python-dotenv==1.0.0
sentry-sdk[flask]>=2.0.0
```

### Node.js (`frontend/package.json` - subset)
```json
{
  "dependencies": {
    "react": "^18.x",
    "vite": "^5.x",
    "@supabase/supabase-js": "^2.x"
  }
}
```

---

## Security Considerations

### API Keys
- **Supabase Service Role**: Backend/Worker only (never expose to frontend)
- **Supabase Anon Key**: Frontend only (safe to expose)
- **OpenAI API Key**: Backend/Worker only (never expose)

### Authentication
- JWT tokens via Supabase Auth
- Access token: Short-lived (1 hour)
- Refresh token: Long-lived (30 days)
- HTTPS required in production

### Row Level Security (RLS)
- Enabled on all tables
- Users can only access their own data
- Service role bypasses RLS (backend/worker)

### CORS
- Development: `CORS_ORIGINS=*`
- Production: `CORS_ORIGINS=https://redirx.onrender.com`

---

## Troubleshooting

### Common Issues

#### Jobs Stuck in 'pending'
**Cause:** Worker crashed or not running

**Solution:**
```bash
# Check worker logs
render logs --service=redirx-worker --tail

# Restart worker
render services restart redirx-worker
```

#### Jobs Stuck in 'processing'
**Cause:** Worker crashed during job execution

**Solution:**
```sql
-- Reset stuck jobs
UPDATE migration_sessions
SET status = 'pending'
WHERE status = 'processing'
AND updated_at < NOW() - INTERVAL '15 minutes';
```

#### High Job Failure Rate
**Causes:**
- Invalid URLs (unreachable sites)
- OpenAI API errors (rate limits, quota)
- Supabase connection issues

**Solution:**
- Check worker logs for error details
- Verify API keys are valid
- Check Supabase connection limits

#### Slow Job Processing
**Causes:**
- Large number of URLs (>500)
- Slow target websites
- OpenAI API rate limiting

**Solution:**
- Recommend users batch jobs
- Increase worker parallelism
- Add progress indicators in frontend

---

## Cost Estimates

### Render (Free Tier)
- Static Site: Free (100GB bandwidth)
- Web Service: Free (750 hours/month)
- Background Worker: Free (750 hours/month)

**Upgrade Triggers:**
- Need >1 instance of any service
- Need >512MB RAM
- Need faster builds

### Supabase (Free Tier)
- Database: 500MB storage
- Vector Storage: 2GB included
- API Requests: Unlimited

**Upgrade Triggers:**
- Storage >500MB
- Need connection pooling
- Need point-in-time recovery

### OpenAI
- Embedding Model: `text-embedding-3-small`
- Cost: $0.00002 per 1K tokens
- ~200 webpages = ~100K tokens = $0.002

**Monthly Cost Examples:**
- 10K redirects: ~$1
- 100K redirects: ~$10
- 1M redirects: ~$100

---

## Performance Benchmarks

### Job Processing Times

| URL Count | Stages 1-3 | Stage 4 (Scrape) | Stage 5-7 | Total |
|-----------|------------|------------------|-----------|-------|
| 50        | <1s        | 15-20s           | 10-15s    | 30-35s|
| 100       | <1s        | 25-35s           | 20-25s    | 50-60s|
| 500       | 1-2s       | 2-3min           | 1-2min    | 3-5min|

**Note:** Times vary based on:
- Target site response times
- Network latency
- OpenAI API response times

### Database Query Performance

| Query | Avg Time | Index |
|-------|----------|-------|
| Get pending job | <10ms | idx_migration_sessions_status_created |
| Get user sessions | <20ms | idx_migration_sessions_user_id |
| Vector search | 50-200ms | HNSW on embedding column |
| Get mappings | <50ms | idx_mappings_session |

---

## Future Optimizations

### Short Term
1. ✅ Batch OpenAI embedding requests (2048/batch)
2. ✅ Add database connection pooling
3. ✅ Cache common embeddings
4. ✅ Parallel scraping optimization (increase from 10 to 50 concurrent)

### Medium Term
1. 🔄 Multiple worker support with row locking
2. 🔄 Job priority system
3. 🔄 Retry logic for failed jobs
4. 🔄 Webhook notifications on job completion

### Long Term
1. 📋 Switch to Redis/RabbitMQ for job queue
2. 📋 Real-time progress via WebSockets
3. 📋 Distributed worker pool with Celery
4. 📋 CDN for frontend assets

---

## Contact & Support

**Repository:** https://github.com/itsdylon/redirx
**Deployment Platform:** Render.com
**Database:** Supabase (PostgreSQL + pgvector)
**AI Provider:** OpenAI

**Key Files:**
- API: `backend/app.py`, `backend/routes/*.py`
- Worker: `backend/worker.py`
- Pipeline: `src/redirx/lib.py`, `src/redirx/stages.py`
- Database: `src/redirx/database.py`
- Config: `src/redirx/config.py`

---

**Document Version:** 1.0
**Last Updated:** 2026-02-06
**Maintainer:** Redirx Team
