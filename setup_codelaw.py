import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

db_url = os.getenv('DATABASE_URL')
if not db_url:
    print("❌ សូមពិនិត្យមើល .env របស់អ្នកម្តងទៀត (ខ្វះ DATABASE_URL)")
    exit()

try:
    # បន្ថែម sslmode='require' សម្រាប់ Cloud Database
    conn = psycopg2.connect(db_url, sslmode='require')
    cur = conn.cursor()

    # បង្កើត Table (ប្រើ IF NOT EXISTS ដើម្បីកុំឱ្យលុបរបស់ចាស់)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS law_articles (
            id SERIAL PRIMARY KEY,
            law_code VARCHAR(50),
            section VARCHAR(200),
            article_title VARCHAR(100),
            content TEXT
        );
    """)

    conn.commit()
    print("✅ បានបង្កើត Table 'law_articles' ជោគជ័យ!")
    cur.close()
    conn.close()
except Exception as e:
    print(f"❌ Error Setup DB: {e}")