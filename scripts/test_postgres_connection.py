#!/usr/bin/env python3
"""Test PostgreSQL connection and run basic schema migration."""

import asyncio
import os
import sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def test_connection(db_url: str):
    """Test PostgreSQL connection and create tables."""
    print(f"Testing connection to: {db_url.split('@')[1].split('/')[0]}...")
    
    try:
        engine = create_async_engine(
            db_url,
            echo=False,
            pool_size=2,
            max_overflow=5,
            pool_pre_ping=True,
        )
        
        # Test basic connectivity
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT version()"))
            version = result.scalar()
            print(f"✓ Connected to PostgreSQL: {version[:50]}...")
            
            # Test schema creation
            print("\n✓ Testing schema creation...")
            await conn.execute(text("DROP TABLE IF EXISTS test_table CASCADE"))
            await conn.execute(text("CREATE TABLE test_table (id SERIAL PRIMARY KEY, name TEXT)"))
            await conn.execute(text("INSERT INTO test_table (name) VALUES ('test')"))
            result = await conn.execute(text("SELECT COUNT(*) FROM test_table"))
            count = result.scalar()
            print(f"✓ Schema operations work: inserted and counted {count} row(s)")
            
            # Clean up test table
            await conn.execute(text("DROP TABLE test_table"))
            print("✓ Cleanup complete")
        
        await engine.dispose()
        print("\n✅ PostgreSQL connection test PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ PostgreSQL connection test FAILED: {e}")
        return False


async def main():
    # Read DB_URL from command line or use Neon default
    if len(sys.argv) > 1:
        db_url = sys.argv[1]
    else:
        # Read DB_URL from environment or command line — never hardcode credentials
        db_url = os.environ.get("DB_URL")
        if not db_url:
            print("ERROR: DB_URL environment variable not set. Example:")
            print("  DB_URL=postgresql+asyncpg://user:pass@host/db python scripts/test_postgres_connection.py")
            sys.exit(1)
    
    success = await test_connection(db_url)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
