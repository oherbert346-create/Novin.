#!/usr/bin/env python3
"""Initialize PostgreSQL database with full schema."""

import asyncio
import sys
from dotenv import load_dotenv

load_dotenv()

from backend.database import init_db, engine
from backend.config import settings


async def main():
    """Initialize PostgreSQL database schema."""
    print(f"Initializing database: {settings.db_url.split('@')[1].split('/')[0] if '@' in settings.db_url else 'SQLite'}...")
    
    try:
        await init_db()
        print("✅ Database schema initialized successfully")
        
        # Test query
        from sqlalchemy import text
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM streams"))
            count = result.scalar()
            print(f"✓ Verified: streams table exists ({count} rows)")
            
            result = await conn.execute(text("SELECT COUNT(*) FROM events"))
            count = result.scalar()
            print(f"✓ Verified: events table exists ({count} rows)")
        
        await engine.dispose()
        return True
        
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
