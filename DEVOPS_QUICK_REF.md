# Redirx DevOps Quick Reference

## Critical Information for Deployment

### Service Setup (3 Render Services)

```
┌─────────────────────────────────────────────────────────────┐
│ 1. FRONTEND (Static Site)                                   │
├─────────────────────────────────────────────────────────────┤
│ Build:    cd frontend && npm install && npm run build       │
│ Publish:  frontend/build                                    │
│ Env Vars:                                                    │
│   - VITE_SUPABASE_URL                                        │
│   - VITE_SUPABASE_ANON_KEY                                   │
│   - VITE_API_BASE_URL                                        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 2. BACKEND API (Web Service)                                │
├─────────────────────────────────────────────────────────────┤
│ Start:    gunicorn backend.app:create_app()                 │
│ Port:     10000 (Render default)                            │
│ Env Vars:                                                    │
│   - SUPABASE_URL                                             │
│   - SUPABASE_KEY (service_role, NOT anon)                   │
│   - OPENAI_API_KEY                                           │
│   - CORS_ORIGINS (set to frontend URL)                      │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 3. WORKER (Background Worker)                               │
├─────────────────────────────────────────────────────────────┤
│ Start:    python -m backend.worker                          │
│ Env Vars: (same as Backend API)                             │
│   - SUPABASE_URL                                             │
│   - SUPABASE_KEY (service_role)                             │
│   - OPENAI_API_KEY                                           │
└─────────────────────────────────────────────────────────────┘
```

---

## Job Flow Diagram

```
User Upload CSVs
       ↓
   API Service
   ├─ Validate JWT
   ├─ Check quota
   ├─ Parse CSVs
   ├─ INSERT INTO migration_sessions (status='pending')
   └─ Return session_id immediately
       ↓
   Database (Job Queue)
   ├─ migration_sessions table
   │  └─ id, user_id, status, old_urls, new_urls
   ↓
   Worker Service (polls every 5s)
   ├─ SELECT * WHERE status='pending' LIMIT 1
   ├─ UPDATE status='processing'
   ├─ Execute 7-stage pipeline
   │  ├─ 1. UrlPruneStage (filter assets)
   │  ├─ 2. BlogPruneStage (filter posts)
   │  ├─ 3. ExactUrlMatchStage (exact paths)
   │  ├─ 4. WebScraperStage (scrape HTML, concurrent)
   │  ├─ 5. HtmlPruneStage (duplicate HTML)
   │  ├─ 6. EmbedStage (OpenAI embeddings)
   │  └─ 7. PairingStage (vector similarity)
   ├─ INSERT INTO url_mappings (results)
   ├─ UPDATE usage_current_month (quota)
   └─ UPDATE status='completed' or 'failed'
       ↓
   Frontend (polls status every 2-3s)
   └─ GET /api/user/sessions/{id}/status
```

---

## Key API Endpoints

### Job Creation
```
POST /api/process
Headers: Authorization: Bearer <jwt>
Body: multipart/form-data
  - old_csv: file
  - new_csv: file

Response:
{
  "success": true,
  "session_id": "uuid"
}
```

### Status Polling (used by frontend during processing)
```
GET /api/user/sessions/<id>/status
Headers: Authorization: Bearer <jwt>

Response:
{
  "success": true,
  "status": "processing",  # pending | processing | completed | failed
  "current_stage": 3,
  "stage_name": "ExactUrlMatchStage",
  "total_stages": 7
}
```

### Results Retrieval
```
GET /api/results/<session_id>

Response:
{
  "success": true,
  "mappings": [{old_url, new_url, confidence_score}],
  "stats": {total, high_confidence, medium_confidence},
  "session": {id, status, project_name}
}
```

---

## Database Schema (Core Tables)

```sql
-- Job queue and session metadata
migration_sessions (
  id UUID PRIMARY KEY,
  user_id TEXT,
  status TEXT,  -- 'pending', 'processing', 'completed', 'failed'
  old_urls JSONB,
  new_urls JSONB,
  current_stage INTEGER,
  stage_name TEXT,
  total_stages INTEGER,
  created_at TIMESTAMPTZ
)

-- Results
url_mappings (
  id UUID PRIMARY KEY,
  session_id UUID REFERENCES migration_sessions,
  old_url TEXT,
  new_url TEXT,
  confidence_score FLOAT,
  match_type TEXT,
  needs_review BOOLEAN
)

-- Vector embeddings (OpenAI text-embedding-3-small)
webpage_embeddings (
  id UUID PRIMARY KEY,
  session_id UUID,
  url TEXT,
  site_type TEXT,  -- 'old' or 'new'
  embedding VECTOR(1536),
  extracted_text TEXT
)

-- User accounts and quotas
user_profiles (
  id UUID PRIMARY KEY,
  email TEXT,
  subscription_plan TEXT,  -- 'free', 'pro', 'enterprise'
  usage_limit_redirects INTEGER DEFAULT 1000,
  usage_current_month INTEGER DEFAULT 0
)
```

---

## Environment Variables Checklist

### Backend/Worker (.env at root)
```bash
# Required
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=service_role_key_here    # MUST BE service_role, NOT anon
OPENAI_API_KEY=sk-xxx

# Production
CORS_ORIGINS=https://redirx.onrender.com   # Set to frontend URL
```

### Frontend (frontend/.env)
```bash
# Required
VITE_SUPABASE_URL=https://xxx.supabase.co
VITE_SUPABASE_ANON_KEY=eyJxxx...           # Use anon key here

# Production only (dev uses Vite proxy)
VITE_API_BASE_URL=https://api.redirx.onrender.com
```

---

## Critical Gotchas

### 1. Supabase Keys
- ❌ **NEVER** use anon key in backend/worker
- ✅ Backend/Worker: `service_role` key (bypasses RLS)
- ✅ Frontend: `anon` key (subject to RLS)

### 2. CORS Configuration
- Development: `CORS_ORIGINS=*`
- Production: `CORS_ORIGINS=https://your-frontend.onrender.com`
- Must match frontend URL exactly (no trailing slash)

### 3. Worker Must Run Continuously
- Worker is a long-running daemon (not a cron job)
- Polls database every 5 seconds
- If worker stops, jobs queue up indefinitely

### 4. Job Status Flow
```
pending ──────→ processing ──────→ completed
                     └──────────────→ failed
```
- Never stuck in 'pending' if worker is running
- If stuck in 'processing' >15min, worker likely crashed

---

## Health Checks

### API Health
```bash
curl https://api.redirx.onrender.com/
# Expected: "Redirx backend is running!"
```

### Worker Health (via logs)
```bash
render logs --service=redirx-worker --tail
# Expected: "[Worker] No pending jobs. Waiting 5s..." (repeating)
```

### Database Queue Depth
```sql
SELECT COUNT(*) FROM migration_sessions WHERE status = 'pending';
-- Should be 0 if worker is keeping up
```

---

## Troubleshooting

### Symptom: Jobs stuck in 'pending'
**Cause:** Worker not running
**Fix:** Restart worker service
```bash
render services restart redirx-worker
```

### Symptom: Jobs stuck in 'processing'
**Cause:** Worker crashed during job
**Fix:** Reset stuck jobs
```sql
UPDATE migration_sessions
SET status = 'pending'
WHERE status = 'processing'
AND updated_at < NOW() - INTERVAL '15 minutes';
```

### Symptom: 401 Unauthorized on /api/process
**Causes:**
1. Invalid/expired JWT token
2. Frontend using wrong Supabase key

**Fix:**
- Verify `VITE_SUPABASE_ANON_KEY` is set correctly
- Check token expiration (access token: 1 hour)
- Use refresh token to get new access token

### Symptom: CORS errors
**Cause:** Backend CORS_ORIGINS not set correctly
**Fix:**
```bash
# In backend Render service env vars:
CORS_ORIGINS=https://your-frontend.onrender.com
```

### Symptom: Worker fails with "Invalid API key"
**Causes:**
1. Missing `OPENAI_API_KEY`
2. Missing `SUPABASE_KEY`
3. Using anon key instead of service_role key

**Fix:** Verify all env vars in worker service

---

## Performance Expectations

### Job Processing Times
| URLs | Time |
|------|------|
| 50   | 30-35s |
| 100  | 50-60s |
| 500  | 3-5min |

### Bottlenecks
1. **Web scraping** (Stage 4): 60-70% of time
2. **Embedding generation** (Stage 6): 20-30% of time
3. **Vector search** (Stage 7): <5% of time

### Costs (OpenAI)
- ~$0.002 per 200 URLs
- 10K redirects/month: ~$1/month
- 100K redirects/month: ~$10/month

---

## Scaling Considerations

### Current Limits (Free Tier)
- **API:** 1 instance, 512MB RAM
- **Worker:** 1 instance, 512MB RAM
- **Database:** 500MB storage, unlimited queries
- **Concurrency:** 1 job at a time (sequential)

### Scaling Path
1. **More concurrent jobs:** Add row locking, deploy 2+ workers
2. **Faster jobs:** Increase scraping parallelism (10→50 concurrent)
3. **Larger jobs:** Upgrade worker to 1GB RAM
4. **Database:** Upgrade Supabase for connection pooling

### When to Scale
- **Queue depth >10:** Add more workers
- **Jobs >500 URLs:** Increase worker RAM
- **>1000 jobs/day:** Add connection pooling

---

## Monitoring Queries

### Queue Depth
```sql
SELECT status, COUNT(*) FROM migration_sessions GROUP BY status;
```

### Recent Jobs
```sql
SELECT id, project_name, status, created_at, updated_at
FROM migration_sessions
ORDER BY created_at DESC
LIMIT 20;
```

### Failed Jobs (last 24h)
```sql
SELECT * FROM migration_sessions
WHERE status = 'failed'
AND created_at > NOW() - INTERVAL '24 hours';
```

### Job Duration Stats
```sql
SELECT
  AVG(updated_at - created_at) as avg_duration,
  MAX(updated_at - created_at) as max_duration
FROM migration_sessions
WHERE status = 'completed';
```

---

## Quick Deploy Checklist

- [ ] Create 3 Render services (frontend, API, worker)
- [ ] Set all environment variables (see checklist above)
- [ ] Verify Supabase keys (service_role vs anon)
- [ ] Set CORS_ORIGINS to frontend URL
- [ ] Run database migrations (001, 002, 003.sql)
- [ ] Test API health endpoint
- [ ] Check worker logs for polling activity
- [ ] Create test user and submit test job
- [ ] Monitor job progress through completion

---

## Key Files Reference

| Purpose | File Path |
|---------|-----------|
| API entry point | `backend/app.py` |
| Worker entry point | `backend/worker.py` |
| Pipeline logic | `src/redirx/lib.py` |
| Stage implementations | `src/redirx/stages.py` |
| Database operations | `src/redirx/database.py` |
| Configuration | `src/redirx/config.py` |
| API routes | `backend/routes/*.py` |
| DB migrations | `database/migrations/*.sql` |

---

**Full Documentation:** See `DEVOPS_ARCHITECTURE.md` for complete details
