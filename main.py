import logging
import os
import base64
import psycopg2
from psycopg2 import pool
import warnings
import uuid
import re 
from openai import OpenAI
from duckduckgo_search import DDGS
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from keep_alive import keep_alive

# --- CONFIGURATION ---
warnings.filterwarnings("ignore")
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
DB_URL = os.getenv('DATABASE_URL')
OPENAI_KEY = os.getenv('OPENAI_API_KEY')

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.ERROR)

client = OpenAI(api_key=OPENAI_KEY)

# --- DATABASE POOL (SECURE) ---
try:
    db_pool = psycopg2.pool.ThreadedConnectionPool(1, 20, DB_URL, sslmode='require')
    if db_pool:
        print("✅ Database pool created successfully")
except Exception as e:
    print(f"❌ Database connection error: {e}")
    db_pool = None

def get_db_connection():
    try:
        return db_pool.getconn()
    except Exception as e:
        print(f"Error getting connection: {e}")
        return None

def return_db_connection(conn):
    if conn and db_pool:
        db_pool.putconn(conn)

# --- HELPER: SEND MESSAGE SAFELY ---
# មុខងារនេះសំខាន់បំផុត! វាជួយការពារមិនឱ្យ Bot គាំងពេលមានបញ្ហា Format
async def safe_send_message(context, chat_id, text, reply_markup=None):
    try:
        # ព្យាយាមផ្ញើជា MarkdownV2 (ដើម្បីអក្សរដិត)
        escaped_text = escape_markdown(text)
        await context.bot.send_message(chat_id=chat_id, text=escaped_text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)
    except BadRequest:
        try:
            # បើបរាជ័យ ផ្ញើជា HTML
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        except BadRequest:
            # បើនៅតែបរាជ័យ ផ្ញើជាអក្សរសុទ្ធ (អត់ Format)
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

async def safe_edit_message(query, text, reply_markup=None):
    try:
        # ព្យាយាមផ្ញើជា HTML ជាមុនសិន (សុវត្ថិភាពជាងគេសម្រាប់ Menu)
        await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except BadRequest:
        try:
            # បើបរាជ័យ ព្យាយាម Escape ហើយផ្ញើជា MarkdownV2
            escaped_text = escape_markdown(text)
            await query.edit_message_text(text=escaped_text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)
        except BadRequest:
            # បើនៅតែបរាជ័យ ផ្ញើជាអក្សរសុទ្ធ
            await query.edit_message_text(text=text, reply_markup=reply_markup)

def escape_markdown(text):
    """Escape special chars for MarkdownV2"""
    if not text: return ""
    text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- AI CORE FUNCTIONS ---

def ask_chatgpt(messages, temperature=0.7):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=temperature
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return "⚠️ AI មានបញ្ហាបច្ចេកទេស។"

def transcribe_audio(file_path):
    try:
        with open(file_path, "rb") as audio_file:
            transcript = client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_file,
                language="km"
            )
        return transcript.text
    except Exception as e:
        print(f"Whisper Error: {e}")
        return None

def translate_text(text):
    prompt = f"Translate the following legal text into formal Khmer. Maintain legal terminology:\n\n'{text}'"
    return ask_chatgpt([{"role": "user", "content": prompt}], temperature=0.3)

def search_web_and_solve(user_question):
    results = []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(f"{user_question} ច្បាប់កម្ពុជា", region='wt-wt', safesearch='off', max_results=2))
    except Exception as e:
        print(f"Search Error: {e}")
    
    context = "\n".join([r['body'] for r in results]) if results else "No web results."
    messages = [
        {"role": "system", "content": "You are a Cambodian Law Expert. Answer in KHMER. Keep it short."},
        {"role": "user", "content": f"Context: {context}\n\nQuestion: {user_question}"}
    ]
    return ask_chatgpt(messages)

def calculate_traffic_fine(violation_text):
    prompt = f"Calculate traffic fine in Riel for: '{violation_text}' based on Cambodia Sub-decree No. 39. Answer in Khmer only."
    return ask_chatgpt([{"role": "user", "content": prompt}])

def analyze_photo(photo_base64):
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "តើរូបនេះជាអ្វី? បើជាឯកសារច្បាប់ សូមសង្ខេប។ បើជាហេតុការណ៍ សូមណែនាំតាមផ្លូវច្បាប់។ ឆ្លើយជាខ្មែរ។"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{photo_base64}"}}
        ]
    }]
    return ask_chatgpt(messages)

def generate_legal_document(doc_type):
    prompt = f"សរសេរគំរូ '{doc_type}' ជាភាសាខ្មែរផ្លូវការ។"
    return ask_chatgpt([{"role": "user", "content": prompt}], temperature=0.3)

def explain_legal_text(legal_text):
    prompt = f"Explain this law article in simple Khmer: '{legal_text}'"
    return ask_chatgpt([{"role": "user", "content": prompt}])

# --- DATABASE FUNCTIONS ---

def get_sections(law_code):
    conn = get_db_connection()
    if not conn: return []
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT section FROM law_articles WHERE law_code = %s ORDER BY section", (law_code,))
        results = cur.fetchall()
        cur.close()
        return [r[0] for r in results]
    except Exception as e:
        print(f"DB Error: {e}")
        return []
    finally:
        return_db_connection(conn)

def get_articles_by_section(law_code, section_name):
    conn = get_db_connection()
    if not conn: return []
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, article_title FROM law_articles WHERE law_code = %s AND section = %s ORDER BY id", (law_code, section_name))
        results = cur.fetchall()
        cur.close()
        return results
    except Exception as e:
        print(f"DB Error: {e}")
        return []
    finally:
        return_db_connection(conn)

def get_content(article_id):
    conn = get_db_connection()
    if not conn: return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT article_title, content, section, law_code FROM law_articles WHERE id = %s", (article_id,))
        result = cur.fetchone()
        cur.close()
        return result
    except Exception as e:
        print(f"DB Error: {e}")
        return None
    finally:
        return_db_connection(conn)

def check_database_first(user_text):
    conn = get_db_connection()
    if not conn: return None
    try:
        cur = conn.cursor()
        search_term = f"%{user_text[:20]}%"
        cur.execute("SELECT article_title, content FROM law_articles WHERE article_title ILIKE %s OR content ILIKE %s LIMIT 1", (search_term, search_term))
        result = cur.fetchone()
        cur.close()
        return result
    except Exception as e:
        print(f"DB Error: {e}")
        return None
    finally:
        return_db_connection(conn)

# --- MENUS ---
def main_menu():
    keyboard = [
        [InlineKeyboardButton("ℹ️ របៀបប្រើប្រាស់", callback_data='help_usage')],
        [InlineKeyboardButton("🤖 សួរ AI (ស្វែងរក)", callback_data='ask_ai_info')],
        [InlineKeyboardButton("🧮 គណនាពិន័យ", callback_data='tool_calc'),
         InlineKeyboardButton("📝 បង្កើតលិខិត", callback_data='menu_gen')],
        [InlineKeyboardButton("🗣️ បកប្រែ (Translate)", callback_data='tool_translate')],
        [InlineKeyboardButton("📘 ក្រមព្រហ្មទណ្ឌ", callback_data='code_criminal'),
         InlineKeyboardButton("🛵 ច្បាប់ចរាចរណ៍", callback_data='code_traffic')],
        [InlineKeyboardButton("📍 រកសមត្ថកិច្ច", callback_data='info_location')]
    ]
    return InlineKeyboardMarkup(keyboard)

def back_to_main_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 ត្រឡប់ទៅម៉ឺនុយដើម", callback_data='main')]])

def generator_menu():
    keyboard = [
        [InlineKeyboardButton("📄 ពាក្យបណ្តឹង", callback_data='gen_complaint')],
        [InlineKeyboardButton("🤝 កិច្ចសន្យាខ្ចីប្រាក់", callback_data='gen_loan')],
        [InlineKeyboardButton("🔙 ត្រឡប់ក្រោយ", callback_data='main')]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mode'] = None
    user = update.effective_user.first_name
    welcome_text = (
        f"សួស្តី <b>{user}</b>! 🙏 ស្វាគមន៍មកកាន់ <b>ជំនួយការច្បាប់ AI</b>\n\n"
        "ខ្ញុំអាចជួយដោះស្រាយបញ្ហាផ្លូវច្បាប់, គណនាប្រាក់ពិន័យ, និងផ្តល់យោបល់បាន។\n\n"
        "👇 <b>សូមជ្រើសរើសសេវាកម្ម៖</b>"
    )
    # ប្រើ safe_send_message
    try:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=welcome_text, reply_markup=main_menu(), parse_mode=ParseMode.HTML)
    except:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=welcome_text.replace("<b>", "").replace("</b>", ""), reply_markup=main_menu())

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🎧 កំពុងស្តាប់...")
    unique_filename = f"voice_{uuid.uuid4()}.ogg"
    
    try:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        await voice_file.download_to_drive(unique_filename)
        
        text_query = transcribe_audio(unique_filename)
        if not text_query:
            await context.bot.edit_message_text("❌ ស្តាប់មិនច្បាស់។", chat_id=update.effective_chat.id, message_id=status_msg.message_id)
            return

        await context.bot.edit_message_text(f"🗣️ \"{text_query}\"\n\n🤖 កំពុងគិត...", chat_id=update.effective_chat.id, message_id=status_msg.message_id)
        
        answer = search_web_and_solve(text_query)
        await safe_send_message(context, update.effective_chat.id, f"🤖 *ចម្លើយ AI៖*\n\n{answer}", back_to_main_menu())

    except Exception as e:
        print(f"Voice Error: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ មានបញ្ហាបច្ចេកទេស។")
    finally:
        if os.path.exists(unique_filename): 
            try: os.remove(unique_filename)
            except: pass

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text("📸 កំពុងវិភាគ...")
    unique_filename = f"temp_{uuid.uuid4()}.jpg"
    try:
        photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
        await photo_file.download_to_drive(unique_filename)
        with open(unique_filename, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        
        answer = analyze_photo(base64_image)
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status.message_id)
        await safe_send_message(context, update.effective_chat.id, f"🤖 *លទ្ធផល៖*\n\n{answer}", back_to_main_menu())

    except Exception as e:
        print(f"Photo Error: {e}")
        await context.bot.edit_message_text("❌ មានបញ្ហារូបភាព", chat_id=update.effective_chat.id, message_id=status.message_id)
    finally:
        if os.path.exists(unique_filename): 
            try: os.remove(unique_filename)
            except: pass

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lat = update.message.location.latitude
    lng = update.message.location.longitude
    maps_url = f"https://www.google.com/maps/search/police+station+near+me/@{lat},{lng},15z"
    await update.message.reply_text(f"📍 <a href='{maps_url}'>មើលទីតាំងលើផែនទី</a>", parse_mode=ParseMode.HTML, reply_markup=back_to_main_menu())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    mode = context.user_data.get('mode')

    try:
        if mode == 'calc':
            processing = await update.message.reply_text("🧮 កំពុងគណនា...")
            result = calculate_traffic_fine(user_text)
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=processing.message_id)
            await safe_send_message(context, update.effective_chat.id, result, back_to_main_menu())
            context.user_data['mode'] = None 
            return

        if mode == 'translate':
            processing = await update.message.reply_text("📝 កំពុងបកប្រែ...")
            result = translate_text(user_text)
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=processing.message_id)
            await safe_send_message(context, update.effective_chat.id, f"📝 *លទ្ធផល៖*\n\n{result}", back_to_main_menu())
            context.user_data['mode'] = None
            return

        status_msg = await update.message.reply_text("🔍 កំពុងស្វែងរក...")
        
        db_result = check_database_first(user_text)
        
        if db_result:
            title, content = db_result
            safe_content = content[:3000] + "..." if len(content) > 3000 else content
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg.message_id)
            await safe_send_message(context, update.effective_chat.id, f"📚 *ឯកសារច្បាប់៖*\n\n*{title}*\n{safe_content}", back_to_main_menu())
        else:
            answer = search_web_and_solve(user_text)
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=status_msg.message_id)
            await safe_send_message(context, update.effective_chat.id, f"🤖 *ចម្លើយ AI៖*\n\n{answer}", back_to_main_menu())
            
    except Exception as e:
        print(f"Text Handler Error: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ មានបញ្ហាបច្ចេកទេស។")

async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    try:
        if data == "main":
            context.user_data['mode'] = None
            await safe_edit_message(query, "សួស្តី! 🙏 ខ្ញុំជាជំនួយការច្បាប់។ សូមជ្រើសរើស៖", main_menu())
            return

        if data == 'help_usage':
            help_text = (
                "ℹ️ <b>របៀបប្រើប្រាស់ Bot នេះ៖</b>\n\n"
                "1. <b>🗣️ សួរតាមសំឡេង:</b> ចុច Mic រួចនិយាយ។\n"
                "2. <b>💬 សួរតាមអក្សរ:</b> វាយសំណួរផ្ទាល់។\n"
                "3. <b>📸 វិភាគរូបភាព:</b> ផ្ញើរូបភាពឯកសារច្បាប់។\n"
                "4. <b>🧮 គណនាពិន័យ:</b> ចូលម៉ឺនុយ 'គណនាពិន័យ'។"
            )
            await safe_edit_message(query, help_text, back_to_main_menu())

        elif data == 'ask_ai_info':
            await safe_edit_message(query, "🤖 <b>របៀបសួរ AI:</b>\n\nវាយសំណួររបស់អ្នកផ្ទាល់ ខ្ញុំនឹងឆ្លើយភ្លាមៗ!", back_to_main_menu())

        elif data == 'tool_calc':
            context.user_data['mode'] = 'calc'
            await safe_edit_message(query, "🧮 <b>ម៉ាស៊ីនគណនាពិន័យ</b>\n\nសរសេរកំហុសរបស់អ្នកមក (ឧទាហរណ៍: អត់ពាក់មួក, ជិះបញ្ច្រាស)...", back_to_main_menu())
        
        elif data == 'tool_translate':
            context.user_data['mode'] = 'translate'
            await safe_edit_message(query, "📝 <b>អ្នកបកប្រែច្បាប់</b>\n\nសូមផ្ញើអត្ថបទមកខ្ញុំ...", back_to_main_menu())

        elif data == 'info_location':
            await query.message.reply_text("📍 សូមផ្ញើ **Location** មកខ្ញុំ (ចុចរូប 📎 -> Location)", reply_markup=back_to_main_menu())

        elif data == 'menu_gen':
            await safe_edit_message(query, "📝 ជ្រើសរើសលិខិត៖", generator_menu())

        elif data.startswith('gen_'):
            doc_map = {'gen_complaint': 'ពាក្យបណ្តឹង', 'gen_loan': 'កិច្ចសន្យាខ្ចីប្រាក់'}
            doc_type = doc_map.get(data)
            await query.edit_message_text(f"⏳ កំពុងសរសេរ...", parse_mode=None)
            doc_content = generate_legal_document(doc_type)
            await query.message.delete()
            # Send plain text for documents to avoid format errors
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{doc_content}", reply_markup=back_to_main_menu())

        elif data.startswith('explain|'):
            article_id = data.split('|')[1]
            result = get_content(article_id)
            if result:
                title, content, _, _ = result
                await safe_edit_message(query, f"💡 <b>កំពុងពន្យល់...</b>\n\n{title}")
                explanation = explain_legal_text(f"{title}\n{content}")
                await safe_edit_message(query, explanation, back_to_main_menu())

        elif data.startswith('code_'):
            law_code = data.split('_')[1]
            sections = get_sections(law_code)
            keyboard = []
            for index, section_name in enumerate(sections):
                short_name = section_name.split('(')[0].strip()
                btn_text = short_name if len(short_name) < 30 else short_name[:28] + ".."
                keyboard.append([InlineKeyboardButton(f"📂 {btn_text}", callback_data=f"sect|{law_code}|{index}")])
            keyboard.append([InlineKeyboardButton("🔙 ត្រឡប់ទៅម៉ឺនុយដើម", callback_data="main")])
            await safe_edit_message(query, "📖 <b>មាតិកាច្បាប់៖</b>", InlineKeyboardMarkup(keyboard))

        elif data.startswith('sect|'):
            parts = data.split('|')
            law_code = parts[1]
            section_index = int(parts[2])
            
            sections = get_sections(law_code)
            if section_index < len(sections):
                full_section_name = sections[section_index]
                articles = get_articles_by_section(law_code, full_section_name)
                keyboard = []
                row = []
                for art_id, art_title in articles:
                    short_title = art_title.split(':')[0]
                    row.append(InlineKeyboardButton(f"📄 {short_title}", callback_data=f"art|{art_id}"))
                    if len(row) == 3: keyboard.append(row); row = []
                if row: keyboard.append(row)
                keyboard.append([InlineKeyboardButton("🔙 ត្រឡប់", callback_data=f"code_{law_code}")])
                await safe_edit_message(query, f"📂 <b>{full_section_name}</b>", InlineKeyboardMarkup(keyboard))

        elif data.startswith('art|'):
            article_id = data.split('|')[1]
            result = get_content(article_id)
            if result:
                title, content, section, law_code = result
                all_secs = get_sections(law_code)
                s_idx = all_secs.index(section) if section in all_secs else 0
                
                keyboard = [
                    [InlineKeyboardButton("💡 ពន្យល់ខ្ញុំ", callback_data=f"explain|{article_id}")],
                    [InlineKeyboardButton("🔙 ត្រឡប់", callback_data=f"sect|{law_code}|{s_idx}")]
                ]
                # ប្រើ safe_edit_message ដើម្បីការពារ Error
                await safe_edit_message(query, f"*{title}*\n\n{content}", InlineKeyboardMarkup(keyboard))

    except Exception as e:
        print(f"Navigation Error: {e}")
        try: await query.message.reply_text("⚠️ មានកំហុស សូមព្យាយាមម្តងទៀត។", reply_markup=back_to_main_menu())
        except: pass

if __name__ == '__main__':
    keep_alive() # Start Web Server
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.add_handler(CallbackQueryHandler(handle_navigation))
    print("✅ DEPLOYMENT READY: Bot is running...")
    application.run_polling()