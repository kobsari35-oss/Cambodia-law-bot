import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def save_to_db(cur, code, section, title, content):
    try:
        cur.execute("""
            INSERT INTO law_articles (law_code, section, article_title, content)
            VALUES (%s, %s, %s, %s)
        """, (code, section, title, content))
    except Exception as e:
        print(f"❌ Error saving {title}: {e}")

def import_laws_from_text(filename):
    if not os.path.exists(filename):
        print(f"❌ រកមិនឃើញ file {filename} ទេ! សូមបង្កើតវាសិន។")
        return

    try:
        conn = psycopg2.connect(os.getenv('DATABASE_URL'), sslmode='require')
        cur = conn.cursor()
    except Exception as e:
        print(f"❌ DB Connection Error: {e}")
        return

    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    current_law_code = "general"
    current_section = "ទូទៅ"
    current_title = None
    current_content = []

    print("🚀 កំពុងចាប់ផ្តើមបញ្ចូលទិន្នន័យ...")

    for line in lines:
        line = line.strip()
        if not line: continue

        if line.startswith("LAW_CODE:"):
            current_law_code = line.split(":")[1].strip()
            print(f"📂 កំណត់ច្បាប់៖ {current_law_code}")

        elif line.startswith("SECTION:"):
            if current_title and current_content:
                save_to_db(cur, current_law_code, current_section, current_title, "\n".join(current_content))
                current_content = []
                current_title = None
            current_section = line.replace("SECTION:", "").strip()
            print(f"  Start Section: {current_section}")

        elif line.startswith("មាត្រា") and ":" in line:
            if current_title and current_content:
                save_to_db(cur, current_law_code, current_section, current_title, "\n".join(current_content))
            current_title = line
            current_content = []
            print(f"    -> Saving: {line}")

        else:
            current_content.append(line)

    if current_title and current_content:
        save_to_db(cur, current_law_code, current_section, current_title, "\n".join(current_content))

    conn.commit()
    cur.close()
    conn.close()
    print("✅ បញ្ចូលទិន្នន័យចប់សព្វគ្រប់!")

if __name__ == "__main__":
    import_laws_from_text("raw_law.txt")