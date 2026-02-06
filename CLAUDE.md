# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Redirx is a student project at Georgia Institute of Technology for automated 301 redirect generation during website migrations. The system analyzes old and new website URLs to intelligently pair them through multiple processing stages.

## Core Architecture

The codebase uses a **pipeline-based architecture** where data flows through a series of async stages:

### Pipeline Flow
1. **UrlPruneStage** - Filters out asset URLs (CSS, JS, images, etc.)
2. **BlogPruneStage** - Filters individual blog posts, keeps landing pages
3. **ExactUrlMatchStage** - Matches identical URL paths before scraping
4. **WebScraperStage** - Scrapes HTML content from URLs using aiohttp
5. **HtmlPruneStage** - Pairs pages with duplicate HTML content
6. **EmbedStage** - Generates vector embeddings via OpenAI
7. **PairingStage** - Matches old→new URLs via vector similarity

The `Pipeline` class ([lib.py](src/redirx/lib.py)) orchestrates execution:
- Accepts input (tuple of old URLs and new URLs lists)
- Uses `iterate()` async generator to execute stages sequentially
- Each stage transforms state and returns it to the next stage
- State type changes as it progresses through the pipeline

### Key Classes

**Pipeline** ([lib.py](src/redirx/lib.py)):
- `__init__(input, stages)` - Initialize with input data and optional stage list
- `iterate()` - Async generator that executes stages and yields intermediate state
- `default_pipeline()` - Returns the standard 7-stage pipeline

**Stage** ([stages.py](src/redirx/stages.py)):
- Abstract base class for all pipeline stages
- `execute(input) -> output` - Async method that transforms data

**WebPage** ([stages.py](src/redirx/stages.py)):
- Represents a scraped webpage with URL and HTML content
- `scrape(session, url)` - Async classmethod to fetch webpage
- Implements `__hash__()` with caching for deduplication

**Mapping** ([stages.py](src/redirx/stages.py)):
- Represents a pairing between old and new webpages
- Used to accumulate redirect mappings through the pipeline

### Important Patterns

- **All stage execution is async** - Use `async def` and `await`
- **Concurrent scraping** - WebScraperStage uses `asyncio.TaskGroup` and `asyncio.gather()` to scrape URLs in parallel
- **Type transformation** - Pipeline input/output types change between stages:
  - Start: `tuple[list[str], list[str]]` (URL lists)
  - After scraping: `tuple[list[WebPage], list[WebPage]]`
  - After HTML pruning: `tuple[list[WebPage], list[WebPage], set[Mapping]]`

## Database & Configuration

**Supabase Backend:**
- Uses PostgreSQL with pgvector extension for vector similarity search
- Stores webpage embeddings (1536-dimensional vectors)
- Handles migration sessions and URL mappings
- **Push-based notifications** via PostgreSQL LISTEN/NOTIFY for instant job processing

**Configuration** ([config.py](src/redirx/config.py)):
- Loads settings from `.env` file (use `.env.example` as template)
- `Config.validate()` - Validates required Supabase credentials
- `Config.validate_embeddings()` - Validates OpenAI API key (optional)

**Database Client** ([database.py](src/redirx/database.py)):
- `SupabaseClient.get_client()` - Singleton Supabase client
- `MigrationSessionDB` - CRUD for migration sessions, idempotency support, lease management
- `WebPageEmbeddingDB` - Insert/search embeddings with vector similarity
- `URLMappingDB` - Manage URL redirects with confidence scores

**Database Migrations** ([database/migrations/](database/migrations/)):
- `004_add_listen_notify_trigger.sql` - LISTEN/NOTIFY infrastructure
- `005_add_lease_columns.sql` - Lease-based locking with `claim_next_job()` and `reclaim_expired_leases()` RPC functions
- `006_add_idempotency_keys.sql` - Prevent duplicate job creation
- Run manually in Supabase SQL Editor (see `database/migrations/README.md`)

**Embedding Strategy:**
- Using OpenAI `text-embedding-3-small` (1536 dims)
- Cost: ~$0.002 per 200 webpages (negligible for demos)
- Future: Can add local embeddings (sentence-transformers) if needed

## Background Worker Architecture

**Worker System** ([backend/worker.py](backend/worker.py)):
- **Push-based** using PostgreSQL LISTEN/NOTIFY (instant job pickup, no polling delay)
- **Lease-based locking** prevents duplicate processing and enables multiple workers
- **Automatic retries** with exponential backoff (up to 5 attempts configurable)
- **Self-healing** via expired lease reclamation
- **Fallback polling** every 60 seconds in case notifications are missed

**Worker Flow**:
1. Worker subscribes to `job_queue_events` PostgreSQL channel
2. When job inserted/updated to `pending`, database sends notification
3. Worker calls `claim_next_job()` RPC to atomically claim job (FOR UPDATE SKIP LOCKED)
4. Job status updated to `processing`, lease set (default: 10 minutes)
5. Pipeline runs with progress updates to database
6. On completion: release lease, update status to `completed`
7. On failure: increment attempt count, release lease, retry (or mark `permanently_failed`)

**Configuration** (Environment Variables):
- `DATABASE_URL` - PostgreSQL direct connection (required for LISTEN/NOTIFY)
- `WORKER_LEASE_DURATION` - Lease duration in seconds (default: 600)
- `WORKER_MAX_CONCURRENT` - Max concurrent jobs (default: 1)
- `WORKER_FALLBACK_INTERVAL` - Fallback poll interval (default: 60)
- `WORKER_MAX_ATTEMPTS` - Max retry attempts (default: 5)

**Idempotency** ([backend/services/pipeline_runner.py](backend/services/pipeline_runner.py)):
- API generates deterministic `idempotency_key` from `SHA256(user_id + sorted_urls)`
- Duplicate requests return existing session instead of creating new job
- Prevents double-processing of identical CSV uploads

## Development Commands

### First-Time Setup
```bash
# 1. Create virtual environment (Python 3.12 or 3.13 required)
python -m venv venv
# Linux/macOS: source venv/bin/activate
# Windows: venv\Scripts\activate

# 2. Copy environment templates
cp .env.example .env          # Backend env vars (Supabase service_role key, OpenAI key, DATABASE_URL)
cp frontend/.env.example frontend/.env  # Frontend env vars (Supabase anon key)

# 3. Edit .env files with your credentials (see comments in each file)
# IMPORTANT: Set DATABASE_URL for worker (get from Supabase Dashboard → Connect → Direct connection)

# 4. Run database migrations (in Supabase SQL Editor)
# See database/migrations/README.md for instructions

# 5. Install dependencies
pip install -r requirements.txt
cd frontend && npm install && cd ..
```

### Running Everything (Recommended)
```bash
# Single command starts all services:
python dev.py
```
This starts:
- **Frontend** at http://localhost:3000 (Vite, proxies /api to Flask)
- **Backend** at http://localhost:5001 (Flask API)
- **Worker** (push-based with LISTEN/NOTIFY, instant job processing)
- **Mock sites** at http://localhost:8000 (old) and http://localhost:8001 (new)

Options:
```bash
python dev.py --no-mocks   # Skip mock test sites
python dev.py --backend    # Backend + worker only
```

### Running Services Individually
```bash
# Frontend
cd frontend && npm run dev

# Backend API (from project root)
python -m backend.app

# Worker (from project root)
python -m backend.worker

# Mock test sites
python tests/mock_sites/start_servers.py
```

### Environment Variables
- **Root `.env`** — Backend config: Supabase (service_role key), OpenAI, CORS, Flask settings
- **`frontend/.env`** — Frontend config: Supabase (anon key). `VITE_API_BASE_URL` is only needed in production (Vite proxy handles local dev).
- **Production (Render)** — Set env vars in the Render dashboard, not in files.

### Running Tests
```bash
python tests/test_database_connection.py     # Verify DB connection
python tests/driver.py                       # Run all tests
python -m unittest tests.stage_tests.html_prune_test  # Specific test
```

### Deployment (Render)
- **Frontend**: Static site, build command `cd frontend && npm install && npm run build`, publish dir `frontend/build`
- **Backend API**: Web service, start command `gunicorn backend.app:create_app()`
- **Worker**: Background worker, start command `python -m backend.worker`
- Set `VITE_API_BASE_URL` to the backend URL in the frontend's Render env vars

## File Structure

- `src/redirx/lib.py` - Pipeline orchestration
- `src/redirx/stages.py` - All stage implementations and helper classes
- `src/redirx/config.py` - Configuration management (loads from .env)
- `src/redirx/database.py` - Supabase client and database operations
- `tests/driver.py` - Test runner entry point
- `tests/test_database_connection.py` - Database connection verification
- `tests/stage_tests/` - Unit tests for individual stages
- `.env.example` - Template for environment variables (copy to `.env`)
