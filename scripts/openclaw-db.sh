cat <<EOF | kubectl exec -i -n openclaw deploy/openclaw-db -- psql -U claw_admin -d openclaw
-- Create restricted user
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agent_user') THEN
        CREATE USER agent_user WITH PASSWORD 'agent-pass-123';
    END IF;
END
\$\$;

GRANT CONNECT ON DATABASE openclaw TO agent_user;
GRANT USAGE ON SCHEMA public TO agent_user;

CREATE TABLE IF NOT EXISTS public.memories (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(1536), 
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS public.business_facts (
    id SERIAL PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    value JSONB,
    tags TEXT[],
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO agent_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agent_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO agent_user;
EOF
