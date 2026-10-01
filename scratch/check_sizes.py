from sqlalchemy import text
from src.database import SessionLocal

db = SessionLocal()
query = text("""
    SELECT 
        relname AS table_name,
        pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
        pg_total_relation_size(c.oid) AS bytes
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE relkind = 'r' 
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY pg_total_relation_size(c.oid) DESC;
""")
rows = db.execute(query).fetchall()
print("--- Table Size Breakdown ---")
for r in rows:
    print(f"{r.table_name}: {r.total_size}")
db.close()
