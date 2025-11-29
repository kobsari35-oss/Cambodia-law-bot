import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = conn.cursor()

# បង្កើត Table សម្រាប់ផ្ទុកច្បាប់
cur.execute("""
    DROP TABLE IF EXISTS law_articles;
    CREATE TABLE law_articles (
        id SERIAL PRIMARY KEY,
        law_code VARCHAR(50),
        section VARCHAR(200),
        article_title VARCHAR(100),
        content TEXT
    );
""")

print("✅ បានបង្កើត Table 'law_articles' ជោគជ័យ!")
conn.commit()
cur.close()
conn.close()