#!/usr/bin/env python3
"""
Diagnostic script to check if idempotency migration was applied.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Try using psycopg2 for direct database access
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("❌ DATABASE_URL not set in .env")
        exit(1)

    print("🔍 Checking database for idempotency_key column...")
    conn = psycopg2.connect(database_url)
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Check if column exists
    cursor.execute("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'migration_sessions'
          AND column_name = 'idempotency_key';
    """)

    result = cursor.fetchone()

    if result:
        print(f"✅ idempotency_key column EXISTS")
        print(f"   Type: {result['data_type']}, Nullable: {result['is_nullable']}")
    else:
        print("❌ idempotency_key column DOES NOT EXIST")
        print("\n🔧 ACTION REQUIRED:")
        print("   1. Go to Supabase Dashboard → SQL Editor")
        print("   2. Run migration: database/migrations/006_add_idempotency_keys.sql")
        cursor.close()
        conn.close()
        exit(1)

    # Check for unique index
    cursor.execute("""
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE tablename = 'migration_sessions'
          AND indexname LIKE '%idempotency%';
    """)

    indexes = cursor.fetchall()
    if indexes:
        print(f"\n✅ Found {len(indexes)} idempotency index(es):")
        for idx in indexes:
            print(f"   - {idx['indexname']}")
    else:
        print("\n⚠️  No idempotency indexes found (migration may be incomplete)")

    # Check recent sessions
    cursor.execute("""
        SELECT id, user_id, idempotency_key, status, created_at
        FROM migration_sessions
        ORDER BY created_at DESC
        LIMIT 5;
    """)

    sessions = cursor.fetchall()
    print(f"\n📊 Last {len(sessions)} sessions:")
    for i, session in enumerate(sessions, 1):
        key = session['idempotency_key']
        key_display = (key[:16] + '...') if key and len(key) > 16 else (key or '❌ NULL')
        print(f"   {i}. {session['id']} | Key: {key_display} | Status: {session['status']}")

    # Check for duplicate keys (should find pairs)
    cursor.execute("""
        SELECT idempotency_key, COUNT(*) as count
        FROM migration_sessions
        WHERE idempotency_key IS NOT NULL
        GROUP BY idempotency_key
        HAVING COUNT(*) > 1;
    """)

    duplicates = cursor.fetchall()
    if duplicates:
        print(f"\n⚠️  Found {len(duplicates)} duplicate idempotency keys:")
        for dup in duplicates:
            print(f"   - {dup['idempotency_key'][:16]}... ({dup['count']} times)")
        print("\n   This means the same CSV was uploaded multiple times BEFORE the fix.")
    else:
        print("\n✅ No duplicate idempotency keys found (good!)")

    cursor.close()
    conn.close()

    print("\n✅ Idempotency system is configured correctly!")

except ImportError:
    print("⚠️  psycopg2 not installed, trying Supabase client...")

    try:
        from supabase import create_client

        url = os.getenv('SUPABASE_URL')
        key = os.getenv('SUPABASE_KEY') or os.getenv('SUPABASE_SERVICE_ROLE_KEY')

        if not url or not key:
            print("❌ SUPABASE_URL or SUPABASE_KEY not set")
            exit(1)

        client = create_client(url, key)

        # Try to query with idempotency_key
        result = client.table('migration_sessions').select('id, user_id, idempotency_key, status, created_at').order('created_at', desc=True).limit(10).execute()

        if result.data and len(result.data) > 0:
            if 'idempotency_key' in result.data[0]:
                print("✅ idempotency_key column EXISTS")

                # Show recent sessions
                print(f"\n📊 Last {len(result.data)} sessions:")
                for i, session in enumerate(result.data, 1):
                    key = session.get('idempotency_key')
                    key_display = (key[:16] + '...') if key and len(key) > 16 else (key or '❌ NULL')
                    print(f"   {i}. {session['id'][:8]}... | Key: {key_display} | Status: {session['status']}")

                # Count sessions with NULL keys
                null_count = sum(1 for s in result.data if not s.get('idempotency_key'))
                print(f"\n⚠️  {null_count} of {len(result.data)} recent sessions have NULL idempotency_key")
                if null_count == len(result.data):
                    print("   ❌ THIS IS THE PROBLEM! All recent sessions have NULL keys.")
                    print("   The code is NOT setting idempotency_key when creating sessions!")
            else:
                print("❌ idempotency_key column DOES NOT EXIST")
                print("\n🔧 ACTION REQUIRED:")
                print("   Run migration: database/migrations/006_add_idempotency_keys.sql")
                exit(1)
        else:
            print("✅ idempotency_key column EXISTS (no sessions yet)")

        print("\n✅ Diagnosis complete!")

    except Exception as e:
        print(f"❌ Error checking with Supabase: {e}")
        exit(1)

except Exception as e:
    print(f"❌ Error: {e}")
    exit(1)
