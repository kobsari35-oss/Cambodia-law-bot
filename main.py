import logging
import os
import base64
import psycopg2
import warnings
import uuid
from openai import OpenAI
from duckduckgo_search import DDGS
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from keep_alive import keep_alive  # <--- Import Web Server for Render

# --- CONFIGURATION ---
warnings.filterwarnings("ignore")
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
DB_URL = os.getenv('DATABASE_URL')
OPENAI_KEY = os.getenv('OPENAI_API_KEY')

logging.basicConfig(level=logging.ERROR)
client = OpenAI(api_key=OPENAI_KEY)

# --- 1. AI CORE FUNCTIONS ---

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
    except: pass
    
    context = "\n".join([r['body'] for r in results]) if results else "No web results."
    messages = [
        {"role": "system", "content": "You are a Cambodian Law Expert. Answer in KHMER."},
        {"role": "user", "content": f"Context: {context}\n\nQuestion: {user_question}"}
    ]
    return ask_chatgpt(messages)

def calculate_traffic_fine(violation_text):
    prompt = f"Calculate traffic fine in Riel for: '{violation_text}' based on Cambodia Sub-decree No. 39. Answer in Khmer."
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

# --- 2. DATABASE FUNCTIONS ---
def get_db_connection(): return psycopg2.connect(DB_URL)

def get_sections(law_code):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT section FROM law_articles WHERE law_code = %s ORDER BY section", (law_code,))
    results = cur.fetchall()
    conn.close()
    return [r[0] for r in results]

def get_articles_by_section(law_code, section_name):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, article_title FROM law_articles WHERE law_code = %s AND section = %s ORDER BY id", (law_code, section_name))
    results = cur.fetchall()
    conn.close()
    return results

def get_content(article_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT article_title, content, section, law_code FROM law_articles WHERE id = %s", (article_id,))
    result = cur.fetchone()
    conn.close()
    return result

def check_database_first(user_text):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        keywords = user_text.split()
        for word in keywords:
            if len(word) < 2: continue
            cur.execute("SELECT article_title, content FROM law_articles WHERE article_title ILIKE %s LIMIT 1", (f"%{word}%",))
            result = cur.fetchone()
            if result:
                conn.close()
                return result
        conn.close()
        return None
    except: return None

# --- 3. MENUS ---

def main_menu():
    keyboard = [
        # ប៊ូតុងរបៀបប្រើប្រាស់ (ថ្មី)
        [InlineKeyboardButton("ℹ️ របៀបប្រើប្រាស់ (How to Use)", callback_data='help_usage')],

        [InlineKeyboardButton("🤖 សួរ AI (ស្វែងរកទូទៅ)", callback_data='ask_ai_info')],
        
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

# --- 4. HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['mode'] = None
    user = update.effective_user.first_name
    
    # សារស្វាគមន៍ (ថ្មី)
    welcome_text = (
        f"សួស្តី {user}! 🙏 ស្វាគមន៍មកកាន់ **ជំនួយការច្បាប់ AI**\n\n"
        "ខ្ញុំត្រូវបានបង្កើតឡើងដើម្បីជួយអ្នកដោះស្រាយបញ្ហាផ្លូវច្បាប់, "
        "គណនាប្រាក់ពិន័យចរាចរណ៍, និងផ្តល់យោបល់ផ្នែកច្បាប់បានយ៉ាងរហ័ស។\n\n"
        "👇 **សូមជ្រើសរើសសេវាកម្មខាងក្រោម៖**"
    )
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=welcome_text,
        reply_markup=main_menu(),
        parse_mode='Markdown'
    )

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("🎧 កំពុងស្តាប់...")
    voice_file = await context.bot.get_file(update.message.voice.file_id)
    unique_filename = f"voice_{uuid.uuid4()}.ogg"
    await voice_file.download_to_drive(unique_filename)
    try:
        text_query = transcribe_audio(unique_filename)
        if not text_query:
            await context.bot.edit_message_text("❌ ស្តាប់មិនច្បាស់។", chat_id=update.effective_chat.id, message_id=status_msg.message_id)
            return
        await context.bot.edit_message_text(f"🗣️ \"{text_query}\"\n\n🤖 កំពុងគិត...", chat_id=update.effective_chat.id, message_id=status_msg.message_id)
        answer = search_web_and_solve(text_query)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🤖 **ចម្លើយ AI៖**\n\n{answer}", parse_mode='Markdown', reply_markup=back_to_main_menu())
    except Exception as e:
        print(f"Voice Error: {e}")
    finally:
        if os.path.exists(unique_filename): os.remove(unique_filename)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text("📸 កំពុងវិភាគ...")
    unique_filename = f"temp_{uuid.uuid4()}.jpg"
    try:
        photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
        await photo_file.download_to_drive(unique_filename)
        with open(unique_filename, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        answer = analyze_photo(base64_image)
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status.message_id, 
                                            text=f"🤖 **លទ្ធផល៖**\n\n{answer}", parse_mode='Markdown', reply_markup=back_to_main_menu())
    except:
        await context.bot.edit_message_text("❌ មានបញ្ហារូបភាព", chat_id=update.effective_chat.id, message_id=status.message_id)
    finally:
        if os.path.exists(unique_filename): os.remove(unique_filename)

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lat = update.message.location.latitude
    lng = update.message.location.longitude
    maps_url = f"https://www.google.com/maps/search/police+station+near+me/@{lat},{lng},15z"
    await update.message.reply_text(f"📍 [មើលទីតាំងលើផែនទី]({maps_url})", parse_mode='Markdown', reply_markup=back_to_main_menu())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    mode = context.user_data.get('mode')

    if mode == 'calc':
        processing = await update.message.reply_text("🧮 កំពុងគណនា...")
        result = calculate_traffic_fine(user_text)
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=processing.message_id,
                                            text=result, reply_markup=back_to_main_menu())
        context.user_data['mode'] = None 
        return

    if mode == 'translate':
        processing = await update.message.reply_text("ea កំពុងបកប្រែ...")
        result = translate_text(user_text)
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=processing.message_id,
                                            text=f"📝 **លទ្ធផល៖**\n\n{result}", reply_markup=back_to_main_menu())
        context.user_data['mode'] = None
        return

    status_msg = await update.message.reply_text("🔍 កំពុងស្វែងរក...")
    db_result = check_database_first(user_text)
    if db_result:
        title, content = db_result
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, 
                                            text=f"📚 **ឯកសារច្បាប់៖**\n\n**{title}**\n{content}", parse_mode='Markdown', reply_markup=back_to_main_menu())
    else:
        answer = search_web_and_solve(user_text)
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_msg.message_id, 
                                            text=f"🤖 **ចម្លើយ AI៖**\n\n{answer}", parse_mode='Markdown', reply_markup=back_to_main_menu())

async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "main":
        context.user_data['mode'] = None
        try:
            await query.edit_message_text("សួស្តី! 🙏 ខ្ញុំជាជំនួយការច្បាប់។ សូមជ្រើសរើស៖", reply_markup=main_menu())
        except:
            await query.message.delete()
            await context.bot.send_message(chat_id=update.effective_chat.id, text="សួស្តី! 🙏 ខ្ញុំជាជំនួយការច្បាប់។ សូមជ្រើសរើស៖", reply_markup=main_menu())
        return

    # --- ផ្នែកបង្ហាញរបៀបប្រើប្រាស់ (ថ្មី) ---
    if data == 'help_usage':
        help_text = (
            "ℹ️ **របៀបប្រើប្រាស់ Bot នេះ៖**\n\n"
            "1. **🗣️ សួរតាមសំឡេង:** ចុចរូប Mic (🎙️) និយាយសំណួររបស់អ្នក ហើយផ្ញើមក។\n"
            "2. **💬 សួរតាមអក្សរ:** វាយសំណួរផ្ទាល់ ដូចជា \"ច្បាប់លែងលះ\", \"ពិន័យបើកភ្លើងក្រហម\"។\n"
            "3. **📸 វិភាគរូបភាព:** ផ្ញើរូបភាពឯកសារច្បាប់ ឬកន្លែងកើតហេតុ ដើម្បីឱ្យ AI ជួយមើល។\n"
            "4. **🧮 គណនាពិន័យ:** ចូលម៉ឺនុយ \"គណនាពិន័យ\" រួចសរសេរកំហុសចរាចរណ៍។\n"
            "5. **📍 រកទីតាំង:** ចុចម៉ឺនុយ \"រកសមត្ថកិច្ច\" រួចផ្ញើ Location របស់អ្នកមក។"
        )
        await query.edit_message_text(help_text, parse_mode='Markdown', reply_markup=back_to_main_menu())

    elif data == 'ask_ai_info':
        await query.edit_message_text(
            "🤖 **របៀបសួរ AI:**\n\n1. វាយសំណួរ (ឧ. \"ច្បាប់ការងារថ្មី\")\n2. **និយាយជាសំឡេង (Voice)** ផ្ញើមកខ្ញុំក៏បាន! 🎙️",
            reply_markup=back_to_main_menu()
        )

    elif data == 'tool_calc':
        context.user_data['mode'] = 'calc'
        await query.edit_message_text("🧮 **ម៉ាស៊ីនគណនាពិន័យ**\n\nសរសេរកំហុសរបស់អ្នកមក (ឧទាហរណ៍: អត់ពាក់មួក, ជិះបញ្ច្រាស)...", reply_markup=back_to_main_menu())
    
    elif data == 'tool_translate':
        context.user_data['mode'] = 'translate'
        await query.edit_message_text("ea **អ្នកបកប្រែច្បាប់**\n\nសូមផ្ញើអត្ថបទ (ខ្មែរ ឬ អង់គ្លេស) មកខ្ញុំ ខ្ញុំនឹងបកប្រែជូន។", reply_markup=back_to_main_menu())

    elif data == 'info_location':
        await query.message.reply_text("📍 សូមផ្ញើ **Location** មកខ្ញុំ (ចុចរូប 📎 -> Location)", reply_markup=back_to_main_menu())

    elif data == 'menu_gen':
        await query.edit_message_text("📝 ជ្រើសរើសលិខិត៖", reply_markup=generator_menu())

    elif data.startswith('gen_'):
        doc_map = {'gen_complaint': 'ពាក្យបណ្តឹង', 'gen_loan': 'កិច្ចសន្យាខ្ចីប្រាក់'}
        doc_type = doc_map.get(data)
        await query.edit_message_text(f"⏳ កំពុងសរសេរ **{doc_type}**...")
        doc_content = generate_legal_document(doc_type)
        await query.message.delete()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"```\n{doc_content}\n```", parse_mode='Markdown', reply_markup=back_to_main_menu())

    elif data.startswith('explain|'):
        article_id = data.split('|')[1]
        result = get_content(article_id)
        if result:
            title, content, _, _ = result
            await query.edit_message_text(f"💡 **កំពុងពន្យល់...**\n\n{title}")
            explanation = explain_legal_text(f"{title}\n{content}")
            await query.edit_message_text(f"{explanation}", parse_mode='Markdown', reply_markup=back_to_main_menu())

    # --- Law Navigation ---
    elif data.startswith('code_'):
        law_code = data.split('_')[1]
        sections = get_sections(law_code)
        keyboard = []
        for index, section_name in enumerate(sections):
            short_name = section_name.split('(')[0].strip()
            btn_text = short_name if len(short_name) < 30 else short_name[:28] + ".."
            keyboard.append([InlineKeyboardButton(f"📂 {btn_text}", callback_data=f"sect|{law_code}|{index}")])
        keyboard.append([InlineKeyboardButton("🔙 ត្រឡប់ទៅម៉ឺនុយដើម", callback_data="main")])
        try: await query.edit_message_text(f"📖 **មាតិកាច្បាប់៖**", reply_markup=InlineKeyboardMarkup(keyboard))
        except BadRequest: pass

    elif data.startswith('sect|'):
        _, law_code, section_index = data.split('|')
        sections = get_sections(law_code)
        try: full_section_name = sections[int(section_index)]
        except: return
        articles = get_articles_by_section(law_code, full_section_name)
        keyboard = []
        row = []
        for art_id, art_title in articles:
            short_title = art_title.split(':')[0]
            row.append(InlineKeyboardButton(f"📄 {short_title}", callback_data=f"art|{art_id}"))
            if len(row) == 3: keyboard.append(row); row = []
        if row: keyboard.append(row)
        keyboard.append([InlineKeyboardButton("🔙 ត្រឡប់", callback_data=f"code_{law_code}")])
        try: await query.edit_message_text(f"📂 **{full_section_name}**", reply_markup=InlineKeyboardMarkup(keyboard))
        except BadRequest: pass

    elif data.startswith('art|'):
        article_id = data.split('|')[1]
        result = get_content(article_id)
        if result:
            title, content, section, law_code = result
            all_secs = get_sections(law_code)
            try: s_idx = all_secs.index(section)
            except: s_idx = 0
            
            keyboard = [
                [InlineKeyboardButton("💡 ពន្យល់ខ្ញុំ", callback_data=f"explain|{article_id}")],
                [InlineKeyboardButton("🔙 ត្រឡប់", callback_data=f"sect|{law_code}|{s_idx}")]
            ]
            try: await query.edit_message_text(f"**{title}**\n\n{content}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            except BadRequest: pass

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