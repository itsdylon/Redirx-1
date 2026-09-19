CREATE EXTENSION vector;
CREATE TABLE migration_sessions(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),user_id text,status text DEFAULT 'processing',current_stage int,stage_name text,total_stages int,updated_at timestamptz);
CREATE TABLE webpage_embeddings(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),session_id uuid REFERENCES migration_sessions(id),url text NOT NULL,site_type text NOT NULL,embedding vector(1536),extracted_text text,title text);
CREATE INDEX idx_embeddings_session ON webpage_embeddings(session_id);
CREATE INDEX idx_embeddings_site_type ON webpage_embeddings(site_type);
CREATE INDEX webpage_embeddings_embedding_idx ON webpage_embeddings USING hnsw(embedding vector_cosine_ops) WITH(m=16,ef_construction=64);
CREATE TABLE url_mappings(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),session_id uuid REFERENCES migration_sessions(id),old_url text,new_url text,confidence_score float,match_type text,needs_review boolean);
CREATE INDEX idx_mapping_session ON url_mappings(session_id);
-- Repository's documented actual match_pages RPC, not a synthetic scorer.
CREATE FUNCTION match_pages(query_embedding vector(1536),target_site_type text,target_session_id uuid,match_count int DEFAULT 5,match_threshold float DEFAULT 0)
RETURNS TABLE(id uuid,session_id uuid,url text,site_type text,extracted_text text,title text,similarity float)
LANGUAGE sql STABLE AS $$ SELECT id,session_id,url,site_type,extracted_text,title,1-(embedding<=>query_embedding) AS similarity
 FROM webpage_embeddings WHERE session_id=target_session_id AND site_type=target_site_type AND 1-(embedding<=>query_embedding)>=match_threshold ORDER BY embedding<=>query_embedding LIMIT match_count $$;
