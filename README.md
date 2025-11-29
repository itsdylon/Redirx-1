# Redirx: Automated 301 Redirect Generation for Website Migrations
Redirx is a student project at the Georgia Institute of Technology.

Redirx is made up of multiple components:
- A website: Used as the user-facing component of Redirx. Orchestrates the interaction between itself, the Python module, and the SQL database.
- A SQL database: Used to ensure user data persists across sessions. 
- A Python module: Used to implement the primary logic behind Redirx
- A Python script: Used to interact with the website


1. User uploads CSVs (URLs only, maybe status codes)
2. PRUNING PHASE #1 (no scraping yet)
   • Exact URL matches (normalized)
   • Obvious URL patterns
   • Exclude blog posts (/blog/, /YYYY/, etc.)
   • Exclude static assets, admin URLs
   • Exclude 4xx/3xx on old site
3. Remaining URLs → Queue for scraping
4. SCRAPE PHASE (only unmatched URLs)
   • Scrape old site URLs
   • Scrape new site URLs
   • Extract: title, h1s, meta description, main content
   • Store raw content + cleaned text
2. PRUNING PHASE #2 (basic content scrapping)
   • Exact HTML matches
6. EMBEDDING PHASE
   • Generate vector embeddings from content
   • Store in pgvector
7. MATCHING PHASE
   • Nearest neighbor search (cosine similarity)
   • Calculate confidence scores
   • Flag ambiguous cases
8. Human review interface

Test sites can be spun up locally using tests/mock_sites/start_servers.py 
