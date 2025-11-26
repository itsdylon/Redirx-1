# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Redirx is a student project at Georgia Institute of Technology for automated 301 redirect generation during website migrations. The system analyzes old and new website URLs to intelligently pair them through multiple processing stages.

## Core Architecture

The codebase uses a **pipeline-based architecture** where data flows through a series of async stages:

### Pipeline Flow
1. **UrlPruneStage** - Filters out invalid/unwanted URLs
2. **WebScraperStage** - Scrapes HTML content from URLs using aiohttp
3. **HtmlPruneStage** - Pairs pages with duplicate HTML content
4. **EmbedStage** - (TODO) Generates vector embeddings from content
5. **PairingStage** - (TODO) Matches old→new URLs via similarity

The `Pipeline` class ([lib.py](src/redirx/lib.py)) orchestrates execution:
- Accepts input (tuple of old URLs and new URLs lists)
- Uses `iterate()` async generator to execute stages sequentially
- Each stage transforms state and returns it to the next stage
- State type changes as it progresses through the pipeline

### Key Classes

**Pipeline** ([lib.py](src/redirx/lib.py)):
- `__init__(input, stages)` - Initialize with input data and optional stage list
- `iterate()` - Async generator that executes stages and yields intermediate state
- `default_pipeline()` - Returns the standard 5-stage pipeline

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

**Configuration** ([config.py](src/redirx/config.py)):
- Loads settings from `.env` file (use `.env.example` as template)
- `Config.validate()` - Validates required Supabase credentials
- `Config.validate_embeddings()` - Validates OpenAI API key (optional)

**Database Client** ([database.py](src/redirx/database.py)):
- `SupabaseClient.get_client()` - Singleton Supabase client
- `MigrationSessionDB` - CRUD for migration sessions
- `WebPageEmbeddingDB` - Insert/search embeddings with vector similarity
- `URLMappingDB` - Manage URL redirects with confidence scores

**Embedding Strategy:**
- Using OpenAI `text-embedding-3-small` (1536 dims)
- Cost: ~$0.002 per 200 webpages (negligible for demos)
- Future: Can add local embeddings (sentence-transformers) if needed

## Development Commands

### First-Time Setup
```bash
# 1. Create virtual environment (Python 3.12 or 3.13 required)
python3.13 -m venv venv
source venv/bin/activate

# 2. Copy environment template
cp .env.example .env

# 3. Edit .env with your Supabase credentials
# Get from: https://app.supabase.com/project/_/settings/api
# Use the "anon public" JWT key (starts with eyJ...)

# 4. Install dependencies
pip install -r .devcontainer/requirements.txt

# 5. Verify database connection
python tests/test_database_connection.py
```

**Important Notes:**
- Must use Python 3.12 or 3.13 (3.14 has compatibility issues with current Supabase libraries)
- Always activate venv before running commands: `source venv/bin/activate`
- Virtual environment bypasses macOS externally-managed-environment restrictions

### Running Tests
```bash
# Test database connection and setup
python tests/test_database_connection.py

# Run all tests
python tests/driver.py

# Run specific test module
python -m unittest tests.stage_tests.html_prune_test
```

### Development Environment
- **Recommended:** Python 3.13 with virtual environment (venv)
- **Alternative:** Use devcontainer with Python 3.13 in VSCode with Remote Containers extension
- **Note:** Python 3.14 is not yet compatible with all dependencies

## File Structure

- `src/redirx/lib.py` - Pipeline orchestration
- `src/redirx/stages.py` - All stage implementations and helper classes
- `src/redirx/config.py` - Configuration management (loads from .env)
- `src/redirx/database.py` - Supabase client and database operations
- `tests/driver.py` - Test runner entry point
- `tests/test_database_connection.py` - Database connection verification
- `tests/stage_tests/` - Unit tests for individual stages
- `.env.example` - Template for environment variables (copy to `.env`)
