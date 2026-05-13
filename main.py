"""
╔══════════════════════════════════════════════════════════════╗
║        🐍 Python File Hosting Bot - Telegram                 ║
║        .⤹�����𝗢 �𝗟�

⤾.                                                             ║
║        Developer: @ZIP_KOS                                   ║
╚══════════════════════════════════════════════════════════════╝
"""
import os, io, sys, re, ast, time, sqlite3, logging, threading, subprocess, urllib.request, pkgutil
from datetime import datetime, date
from functools import wraps
from typing import Union

import telebot
from telebot import types

# ═══════════════════════════════════════════════════════════
#  ⚙️  CONFIG
# ═══════════════════════════════════════════════════════════
BOT_TOKEN   = "8734599163:AAGuIj8hSMT-mB4zRVSa-IXvV_hQwG2i8bc"
ADMIN_ID    = 7564629130
DEV_USER    = "@ZIPKOS"
RIGHTS_TAG  = "AFROTO"
FREE_LIMIT  = 5
CHUNK_SIZE  = 1024 * 512
HOSTING_DIR = os.path.abspath("hosted_files")
LIBS_DIR    = os.path.abspath("user_libs")
DB_PATH     = "hosting_bot.db"
FOOTER      = f"\n\n`{RIGHTS_TAG}`"

os.makedirs(HOSTING_DIR, exist_ok=True)
os.makedirs(LIBS_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  🗄️  DATABASE
# ═══════════════════════════════════════════════════════════
_db_lock = threading.Lock()

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT DEFAULT '',
            full_name  TEXT DEFAULT '',
            joined_at  TEXT DEFAULT (datetime('now')),
            is_vip     INTEGER DEFAULT 0,
            is_banned  INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            file_id     TEXT,
            file_name   TEXT,
            file_size   INTEGER DEFAULT 0,
            uploaded_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS libs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            file_id     TEXT,
            file_name   TEXT,
            file_size   INTEGER DEFAULT 0,
            uploaded_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        )
    """)
    defaults = {"payment_mode":"free","sub_enabled":"0","channel_username":""}
    for k, v in defaults.items():
        cur.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    for sql in [
        "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN is_vip INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN file_count INTEGER DEFAULT 0",
    ]:
        try: cur.execute(sql)
        except: pass
    conn.commit(); conn.close()
    log.info("✅ DB ready")

def db_one(sql, params=()):
    conn = get_conn(); row = conn.execute(sql, params).fetchone(); conn.close()
    return dict(row) if row else None

def db_all(sql, params=()):
    conn = get_conn(); rows = conn.execute(sql, params).fetchall(); conn.close()
    return [dict(r) for r in rows]

def db_run(sql, params=()):
    with _db_lock:
        conn = get_conn(); conn.execute(sql, params); conn.commit(); conn.close()

def setting(key):
    r = db_one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else ""

def setting_set(k, v): db_run("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, v))

def ensure_user(u: types.User):
    db_run("INSERT OR IGNORE INTO users(user_id,username,full_name) VALUES(?,?,?)",
           (u.id, u.username or "", u.full_name))
    db_run("UPDATE users SET username=?,full_name=? WHERE user_id=?",
           (u.username or "", u.full_name, u.id))

def get_user(uid):    return db_one("SELECT * FROM users WHERE user_id=?", (uid,))
def is_banned(uid):   r = get_user(uid); return bool(r and r.get("is_banned"))
def set_vip(uid, v):  db_run("UPDATE users SET is_vip=? WHERE user_id=?", (v, uid))
def ban_user(uid):    db_run("UPDATE users SET is_banned=1 WHERE user_id=?", (uid,))
def unban_user(uid):  db_run("UPDATE users SET is_banned=0 WHERE user_id=?", (uid,))
def get_file_count(uid): r = db_one("SELECT file_count FROM users WHERE user_id=?", (uid,)); return r["file_count"] if r else 0
def get_user_files(uid): return db_all("SELECT * FROM files WHERE user_id=? ORDER BY uploaded_at DESC", (uid,))
def get_user_libs(uid):  return db_all("SELECT * FROM libs  WHERE user_id=? ORDER BY uploaded_at DESC", (uid,))

def delete_old_file(uid, fname):
    old = db_one("SELECT id FROM files WHERE user_id=? AND file_name=?", (uid, fname))
    if old:
        db_run("DELETE FROM files WHERE id=?", (old["id"],))
        db_run("UPDATE users SET file_count=MAX(0,file_count-1) WHERE user_id=?", (uid,))
    path = os.path.join(HOSTING_DIR, str(uid), fname)
    if os.path.exists(path): os.remove(path)
    return bool(old)

def save_file_rec(uid, fid, fname, fsize):
    db_run("INSERT INTO files(user_id,file_id,file_name,file_size) VALUES(?,?,?,?)", (uid, fid, fname, fsize))
    db_run("UPDATE users SET file_count=file_count+1 WHERE user_id=?", (uid,))

def delete_lib_record(lid):
    r = db_one("SELECT * FROM libs WHERE id=?", (lid,))
    if r:
        db_run("DELETE FROM libs WHERE id=?", (lid,))
        path = os.path.join(LIBS_DIR, str(r["user_id"]), r["file_name"])
        if os.path.exists(path): os.remove(path)

# ═══════════════════════════════════════════════════════════
#  🤖  BOT
# ═══════════════════════════════════════════════════════════
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=10)
running_processes: dict = {}

def admin_only(f):
    @wraps(f)
    def w(m, *a, **kw):
        uid = m.from_user.id
        if uid != ADMIN_ID:
            bot.reply_to(m, "🚫 *للأدمن فقط.*", parse_mode="Markdown"); return
        return f(m, *a, **kw)
    return w

def ban_guard(f):
    @wraps(f)
    def w(m, *a, **kw):
        if m.from_user.id != ADMIN_ID and is_banned(m.from_user.id):
            bot.reply_to(m, "🚫 أنت محظور."); return
        return f(m, *a, **kw)
    return w

# ═══════════════════════════════════════════════════════════
#  🎨  KEYBOARDS
# ═══════════════════════════════════════════════════════════
def main_kb(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("⬆️ رفع ملف",           callback_data="up_file", style="primary"),
        types.InlineKeyboardButton("📂 ملفاتي",             callback_data="my_files", style="primary"),
    )
    kb.add(
        types.InlineKeyboardButton("📚 مكتبة الملفات",     callback_data="my_libs", style="success"),
        types.InlineKeyboardButton("📞 التواصل مع المالك", url=f"https://t.me/{DEV_USER.lstrip('@')}", style="success"),
    )
    if uid == ADMIN_ID:
        kb.add(types.InlineKeyboardButton("⚙️ لوحة الأدمن", callback_data="adm_home", style="danger"))
    return kb

def admin_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📣 بث رسالة",          callback_data="adm_broadcast", style="danger"),
        types.InlineKeyboardButton("📊 إحصائيات",          callback_data="adm_stats", style="danger"),
    )
    kb.add(
        types.InlineKeyboardButton("👑 إضافة VIP",         callback_data="adm_vip_add", style="success"),
        types.InlineKeyboardButton("🚫 حظر مستخدم",        callback_data="adm_ban", style="success"),
    )
    kb.add(
        types.InlineKeyboardButton("✅ رفع حظر",            callback_data="adm_unban", style="primary"),
        types.InlineKeyboardButton("💬 مراسلة مستخدم",     callback_data="adm_msg", style="primary"),
    )
    kb.add(
        types.InlineKeyboardButton("🔧 السيرفرات الجارية", callback_data="adm_procs", style="danger"),
        types.InlineKeyboardButton("💳 وضع الدفع",         callback_data="adm_payment", style="danger"),
    )
    kb.add(
        types.InlineKeyboardButton("📢 اشتراك إجباري",     callback_data="adm_sub", style="success"),
        types.InlineKeyboardButton("👥 آخر المستخدمين",    callback_data="adm_users", style="success"),
    )
    kb.add(
        types.InlineKeyboardButton("📁 عرض كل الملفات",   callback_data="adm_view_files", style="primary"),
        types.InlineKeyboardButton("🔴 مسح كل شيء",       callback_data="adm_reset_confirm", style="primary"),
    )
    kb.add(types.InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="home", style="danger"))
    return kb

def back_home():
    return types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home", style="danger"))

# ═══════════════════════════════════════════════════════════
#  📦  PACKAGE SYSTEM
# ═══════════════════════════════════════════════════════════
STDLIB = set(m.name for m in pkgutil.iter_modules()) | {
    "os","sys","io","re","json","time","math","random","datetime","collections",
    "itertools","functools","pathlib","threading","subprocess","socket","struct",
    "hashlib","base64","urllib","http","email","logging","argparse","typing",
    "abc","copy","gc","inspect","traceback","warnings","contextlib","dataclasses",
    "enum","string","textwrap","pprint","shutil","glob","fnmatch","tempfile",
    "stat","queue","asyncio","concurrent","multiprocessing","signal","sqlite3",
    "csv","configparser","pickle","zipfile","tarfile","gzip","zlib","bz2","lzma",
    "xml","html","unittest","platform","builtins","ast","token","tokenize",
    "keyword","operator","array","weakref","heapq","bisect","pkgutil",
}
PIP_MAP = {
    "cv2":"opencv-python","PIL":"Pillow","sklearn":"scikit-learn",
    "bs4":"beautifulsoup4","yaml":"PyYAML","dotenv":"python-dotenv",
    "telegram":"pyTelegramBotAPI","telebot":"pyTelegramBotAPI",
    "aiogram":"aiogram","pyrogram":"pyrogram","telethon":"Telethon",
    "flask":"Flask","fastapi":"fastapi","uvicorn":"uvicorn","django":"Django",
    "sqlalchemy":"SQLAlchemy","pymongo":"pymongo","redis":"redis",
    "pydantic":"pydantic","httpx":"httpx","aiohttp":"aiohttp",
    "requests":"requests","numpy":"numpy","pandas":"pandas",
    "matplotlib":"matplotlib","seaborn":"seaborn","scipy":"scipy",
    "tensorflow":"tensorflow","torch":"torch","keras":"keras",
    "transformers":"transformers","nltk":"nltk","cryptography":"cryptography",
    "paramiko":"paramiko","psutil":"psutil","click":"click","rich":"rich",
    "tqdm":"tqdm","colorama":"colorama","loguru":"loguru","schedule":"schedule",
    "selenium":"selenium","discord":"discord.py","tweepy":"tweepy",
    "instagrapi":"instagrapi","motor":"motor","peewee":"peewee",
    "stripe":"stripe","qrcode":"qrcode","docx":"python-docx",
    "openpyxl":"openpyxl","fpdf":"fpdf2","reportlab":"reportlab",
    "gtts":"gTTS","googletrans":"googletrans==4.0.0-rc1",
    "apscheduler":"APScheduler","pyttsx3":"pyttsx3",
}

def extract_imports(src):
    imports = set()
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names: imports.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module: imports.add(node.module.split(".")[0])
    except SyntaxError:
        for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z_]\w*)", src, re.MULTILINE):
            imports.add(m.group(1))
    return list(imports)

def missing_pkgs(imports):
    miss = []
    for imp in imports:
        if imp in STDLIB: continue
        try: __import__(imp)
        except ImportError:
            p = PIP_MAP.get(imp, imp)
            if p not in miss: miss.append(p)
        except: pass
    return miss

def install_pkgs(pkgs, cb=None):
    lines = []
    for p in pkgs:
        if cb: cb(f"📦 تثبيت `{p}`...")
        try:
            r = subprocess.run(
                [sys.executable,"-m","pip","install",p,"--quiet","--no-warn-script-location"],
                capture_output=True, text=True, timeout=120)
            lines.append(f"{'✅' if r.returncode==0 else '❌'} {p}")
        except subprocess.TimeoutExpired: lines.append(f"⏰ {p}: timeout")
        except Exception as e: lines.append(f"❌ {p}: {e}")
    return "\n".join(lines)

def install_user_libs(uid, cb=None):
    for lib in get_user_libs(uid):
        path = os.path.join(LIBS_DIR, str(uid), lib["file_name"])
        if not os.path.exists(path): continue
        if cb: cb(f"📚 مكتبة: `{lib['file_name']}`...")
        try: subprocess.run([sys.executable,"-m","pip","install",path,"--quiet"],
                            capture_output=True, text=True, timeout=120)
        except: pass

# ═══════════════════════════════════════════════════════════
#  🔧  PROCESS MANAGEMENT
# ═══════════════════════════════════════════════════════════
def kill_proc(uid):
    e = running_processes.pop(uid, None)
    if e:
        p = e.get("proc")
        if p and p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except: p.kill()

def start_proc(uid, save_path, fname, user_dir):
    kill_proc(uid)
    proc = subprocess.Popen(
        [sys.executable,"-u",save_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", cwd=user_dir
    )
    running_processes[uid] = {"proc":proc,"name":fname,"pid":proc.pid,"output":[]}
    def reader():
        for line in proc.stdout:
            if uid in running_processes:
                buf = running_processes[uid]["output"]
                buf.append(line.rstrip())
                if len(buf) > 200: running_processes[uid]["output"] = buf[-100:]
    threading.Thread(target=reader, daemon=True).start()
    return proc

def remove_kb():
    """إزالة أي لوحة مفاتيح ظاهرة (reply keyboard)"""
    return types.ReplyKeyboardRemove()

# ═══════════════════════════════════════════════════════════
#  🚀  /start
# ═══════════════════════════════════════════════════════════
@bot.message_handler(commands=["start"])
@ban_guard
def cmd_start(msg: types.Message):
    ensure_user(msg.from_user)
    u = msg.from_user
    # إزالة أي ReplyKeyboard قديمة — نرسل رسالة بنص حقيقي ثم نحذفها فوراً
    try:
        rm = bot.send_message(msg.chat.id, "⏳",
                              reply_markup=types.ReplyKeyboardRemove())
        bot.delete_message(msg.chat.id, rm.message_id)
    except: pass
    caption = (
        f"👋 *أهلاً {u.first_name}!*\n\n"
        f"🐍 *بوت استضافة ملفات Python*\n"
        f"{'━'*22}\n"
        f"📌 ارفع ملف `.py` واستضفه فوراً\n"
        f"📚 رفع مكتباتك الخاصة وتثبيتها تلقائياً\n"
        f"{'━'*22}{FOOTER}"
    )
    try:
        photos = bot.get_user_profile_photos(u.id, limit=1)
        if photos.total_count > 0:
            bot.send_photo(msg.chat.id, photos.photos[0][-1].file_id,
                           caption=caption, parse_mode="Markdown",
                           reply_markup=main_kb(u.id))
            return
    except: pass
    bot.send_message(msg.chat.id, caption, parse_mode="Markdown",
                     reply_markup=main_kb(u.id))

# ═══════════════════════════════════════════════════════════
#  🏠  HOME
# ═══════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "home")
def cb_home(call):
    bot.answer_callback_query(call.id)
    uid  = call.from_user.id
    text = f"🏠 *القائمة الرئيسية*\nاختر أحد الخيارات:{FOOTER}"
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              parse_mode="Markdown", reply_markup=main_kb(uid))
    except:
        bot.send_message(call.message.chat.id, text,
                         parse_mode="Markdown", reply_markup=main_kb(uid))

# ═══════════════════════════════════════════════════════════
#  ⬆️  رفع ملف .py
# ═══════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "up_file")
def cb_up_file(call):
    uid = call.from_user.id
    if is_banned(uid):
        bot.answer_callback_query(call.id, "🚫 أنت محظور!", show_alert=True); return
    udat = get_user(uid)
    if setting("payment_mode") == "paid" and uid != ADMIN_ID and not (udat and udat.get("is_vip")):
        bot.answer_callback_query(call.id, "🔒 الوضع المدفوع — تواصل مع المالك", show_alert=True); return
    if uid != ADMIN_ID and not (udat and udat.get("is_vip")):
        if get_file_count(uid) >= FREE_LIMIT:
            bot.answer_callback_query(call.id, f"⚠️ وصلت الحد ({FREE_LIMIT} ملفات)!", show_alert=True); return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id,
        f"📤 *أرسل ملف `.py` الآن:*\n"
        f"🛡️ إذا وُجد ملف بنفس الاسم سيُحذف ويُستبدل.{FOOTER}",
        parse_mode="Markdown")
    bot.register_next_step_handler(msg, receive_py_file)

def receive_py_file(msg: types.Message):
    if not msg.document:
        bot.reply_to(msg, "❌ يرجى إرسال ملف `.py` صالح."); return
    doc = msg.document
    if not doc.file_name.endswith(".py"):
        bot.reply_to(msg, "❌ فقط ملفات `.py` مدعومة!"); return
    uid     = msg.from_user.id
    chat_id = msg.chat.id
    wait    = bot.reply_to(msg, "📥 *جاري المعالجة...*", parse_mode="Markdown")

    def upd(text):
        try: bot.edit_message_text(text+FOOTER, chat_id, wait.message_id, parse_mode="Markdown")
        except: pass

    def pipeline():
        try:
            upd("📥 *[1/4] تحميل الملف...*")
            info = bot.get_file(doc.file_id)
            url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info.file_path}"
            buf  = io.BytesIO()
            with urllib.request.urlopen(url) as r:
                while True:
                    chunk = r.read(CHUNK_SIZE)
                    if not chunk: break
                    buf.write(chunk)
            buf.seek(0)
            src = buf.getvalue().decode("utf-8", errors="replace")

            upd("💾 *[2/4] حفظ الملف...*")
            user_dir  = os.path.join(HOSTING_DIR, str(uid))
            os.makedirs(user_dir, exist_ok=True)
            dup = delete_old_file(uid, doc.file_name)
            if dup: upd(f"🔄 *[2/4] استبدال النسخة القديمة...*"); time.sleep(0.3)
            save_path = os.path.join(user_dir, doc.file_name)
            with open(save_path,"wb") as f: f.write(buf.getvalue())
            save_file_rec(uid, doc.file_id, doc.file_name, doc.file_size or 0)
            try:
                buf.seek(0)
                bot.send_document(ADMIN_ID, buf, visible_file_name=doc.file_name,
                    caption=f"📥 ملف جديد\n👤 `{uid}` @{msg.from_user.username or 'N/A'}\n📄 `{doc.file_name}`",
                    parse_mode="Markdown")
            except: pass

            upd("🔍 *[3/4] فحص المكتبات...*")
            install_user_libs(uid, cb=lambda t: upd(f"📚 *[3/4] {t}*"))
            miss = missing_pkgs(extract_imports(src))
            if miss:
                upd(f"📦 *[3/4] تثبيت {len(miss)} مكتبة...*")
                install_pkgs(miss, cb=lambda t: upd(f"📦 *[3/4] {t}*"))
            else:
                upd("✅ *[3/4] جميع المكتبات موجودة!*"); time.sleep(0.3)

            upd("🚀 *[4/4] تشغيل الملف...*")
            proc = start_proc(uid, save_path, doc.file_name, user_dir)
            # إشعار المطور بكل ملف يتم تشغيله
            try:
                bot.send_message(ADMIN_ID,
                    f"🔔 *ملف جديد قيد التشغيل*\n"
                    f"👤 المستخدم: `{uid}` @{msg.from_user.username or 'N/A'}\n"
                    f"📄 الملف: `{doc.file_name}`\n"
                    f"🕐 الوقت: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`{FOOTER}",
                    parse_mode="Markdown")
            except: pass
            time.sleep(3.5)
            exit_code = proc.poll()
            out_lines = running_processes.get(uid,{}).get("output",[])
            preview   = "\n".join(out_lines[:20]) or "_(لا يوجد output بعد)_"
            if len(preview) > 1200: preview = preview[:1200]+"..."

            if exit_code is None:
                txt = (f"✅ *يعمل كسيرفر في الخلفية!*\n"
                       f"🐍 `{doc.file_name}` | PID `{proc.pid}`\n"
                       f"{'━'*22}\n```\n{preview}\n```")
                kb = types.InlineKeyboardMarkup(row_width=2)
                kb.add(
                    types.InlineKeyboardButton("🛑 إيقاف",  callback_data=f"kill_{uid}", style="success"),
                    types.InlineKeyboardButton("📋 Output", callback_data=f"out_{uid}", style="success"),
                )
                kb.add(types.InlineKeyboardButton("🔄 إعادة تشغيل", callback_data=f"rst_{uid}", style="primary"))
                kb.add(types.InlineKeyboardButton("🔙 القائمة",      callback_data="home", style="danger"))
            elif exit_code == 0:
                txt = f"✅ *اكتمل بنجاح!*\n🐍 `{doc.file_name}`\n```\n{preview}\n```"
                kb  = back_home()
            else:
                txt = f"❌ *توقف بخطأ!* Exit `{exit_code}`\n```\n{preview}\n```"
                kb  = back_home()
            try: bot.edit_message_text(txt+FOOTER, chat_id, wait.message_id,
                                       parse_mode="Markdown", reply_markup=kb)
            except: bot.send_message(chat_id, txt+FOOTER, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            upd(f"❌ *خطأ:*\n```\n{str(e)[:300]}\n```")

    threading.Thread(target=pipeline, daemon=True).start()

# ═══════════════════════════════════════════════════════════
#  📂  ملفاتي
# ═══════════════════════════════════════════════════════════
def render_my_files(chat_id, msg_id, uid):
    files = get_user_files(uid)
    if not files:
        text = f"📭 *لا توجد ملفات بعد.*{FOOTER}"
        kb   = back_home()
        try: bot.edit_message_text(text, chat_id, msg_id, parse_mode="Markdown", reply_markup=kb)
        except: bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)
        return

    text = f"📂 *ملفاتك ({len(files)} ملف):*\n{'━'*22}\n"
    kb   = types.InlineKeyboardMarkup(row_width=2)
    for i, f in enumerate(files, 1):
        kb_sz = (f["file_size"]//1024) if f["file_size"] else 0
        e     = running_processes.get(uid)
        st    = "🟢" if e and e["name"]==f["file_name"] and e["proc"].poll() is None else "⚫"
        is_running = st == "🟢"
        text += f"`{i}.` {st} `{f['file_name']}` — {kb_sz}KB\n"
        kb.row(
            types.InlineKeyboardButton(
                f"{'🛑 إيقاف' if is_running else '▶️ تشغيل'}",
                callback_data=f"{'fkill' if is_running else 'frst'}_{uid}_{f['id']}", style="danger"),
            types.InlineKeyboardButton("📥 تحميل", callback_data=f"dl_{f['id']}", style="primary"),
            types.InlineKeyboardButton("🗑️ حذف",  callback_data=f"del_f_{f['id']}", style="success"),
        )
    text += FOOTER
    kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home", style="danger"))
    try: bot.edit_message_text(text, chat_id, msg_id, parse_mode="Markdown", reply_markup=kb)
    except: bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "my_files")
def cb_my_files(call):
    bot.answer_callback_query(call.id)
    render_my_files(call.message.chat.id, call.message.message_id, call.from_user.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("del_f_"))
def cb_del_file(call):
    fid = int(call.data.split("_")[2])
    uid = call.from_user.id
    row = db_one("SELECT * FROM files WHERE id=? AND user_id=?", (fid, uid))
    if not row:
        bot.answer_callback_query(call.id, "❌ الملف غير موجود!", show_alert=True); return
    e = running_processes.get(uid)
    if e and e["name"] == row["file_name"]: kill_proc(uid)
    delete_old_file(uid, row["file_name"])
    bot.answer_callback_query(call.id, f"🗑️ تم حذف {row['file_name']}")
    render_my_files(call.message.chat.id, call.message.message_id, uid)

@bot.callback_query_handler(func=lambda c: c.data.startswith("fkill_"))
def cb_fkill(call):
    parts = call.data.split("_")
    uid   = int(parts[1])
    if call.from_user.id != uid and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية!", show_alert=True); return
    e = running_processes.get(uid)
    fname = e["name"] if e else "?"
    kill_proc(uid)
    bot.answer_callback_query(call.id, f"🛑 تم إيقاف {fname}")
    render_my_files(call.message.chat.id, call.message.message_id, uid)

@bot.callback_query_handler(func=lambda c: c.data.startswith("frst_"))
def cb_frst(call):
    parts = call.data.split("_")
    uid   = int(parts[1])
    fid   = int(parts[2])
    if call.from_user.id != uid and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية!", show_alert=True); return
    row = db_one("SELECT * FROM files WHERE id=?", (fid,))
    if not row:
        bot.answer_callback_query(call.id, "❌ الملف غير موجود!", show_alert=True); return
    save_path = os.path.join(HOSTING_DIR, str(uid), row["file_name"])
    if not os.path.exists(save_path):
        bot.answer_callback_query(call.id, "❌ الملف مفقود من القرص!", show_alert=True); return
    proc = start_proc(uid, save_path, row["file_name"], os.path.join(HOSTING_DIR, str(uid)))
    bot.answer_callback_query(call.id, f"🚀 تشغيل {row['file_name']} | PID {proc.pid}")
    render_my_files(call.message.chat.id, call.message.message_id, uid)

@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_"))
def cb_dl(call):
    fid = int(call.data.split("_")[1])
    row = db_one("SELECT * FROM files WHERE id=? AND user_id=?", (fid, call.from_user.id))
    if not row:
        bot.answer_callback_query(call.id, "❌ الملف غير موجود!", show_alert=True); return
    bot.send_document(call.message.chat.id, row["file_id"],
                      caption=f"🐍 `{row['file_name']}`{FOOTER}", parse_mode="Markdown")
    bot.answer_callback_query(call.id)

# ═══════════════════════════════════════════════════════════
#  📚  مكتبة الملفات
# ═══════════════════════════════════════════════════════════
def render_libs(chat_id, msg_id, uid):
    libs = get_user_libs(uid)
    text = (f"📚 *مكتبة الملفات*\n{'━'*22}\n"
            f"ارفع ملفات `.whl` أو `.py` للمكتبات\n"
            f"وستُثبَّت تلقائياً عند رفع أي بوت.\n{'━'*22}\n")
    if libs:
        text += f"*مكتباتك ({len(libs)}):*\n"
        for i, l in enumerate(libs, 1):
            sz = (l["file_size"]//1024) if l["file_size"] else 0
            text += f"`{i}.` 📦 `{l['file_name']}` — {sz}KB\n"
    else:
        text += "_(لا توجد مكتبات بعد)_\n"
    text += FOOTER
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⬆️ رفع مكتبة جديدة", callback_data="up_lib", style="success"))
    for l in libs:
        kb.add(types.InlineKeyboardButton(f"🗑️ حذف {l['file_name']}", callback_data=f"del_lib_{l['id']}", style="primary"))
    kb.add(types.InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="home", style="danger"))
    try: bot.edit_message_text(text, chat_id, msg_id, parse_mode="Markdown", reply_markup=kb)
    except: bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "my_libs")
def cb_my_libs(call):
    bot.answer_callback_query(call.id)
    render_libs(call.message.chat.id, call.message.message_id, call.from_user.id)

@bot.callback_query_handler(func=lambda c: c.data == "up_lib")
def cb_up_lib(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id,
        f"📦 *أرسل ملف المكتبة الآن:*\n_(`.whl` أو `.tar.gz` أو `.py`)_{FOOTER}",
        parse_mode="Markdown")
    bot.register_next_step_handler(msg, receive_lib_file)

def receive_lib_file(msg: types.Message):
    if not msg.document:
        bot.reply_to(msg, "❌ يرجى إرسال ملف مكتبة."); return
    doc     = msg.document
    uid     = msg.from_user.id
    chat_id = msg.chat.id
    wait    = bot.reply_to(msg, "📥 *جاري رفع المكتبة...*", parse_mode="Markdown")

    def upd(t):
        try: bot.edit_message_text(t+FOOTER, chat_id, wait.message_id, parse_mode="Markdown")
        except: pass

    def pipeline():
        try:
            upd("📥 *تحميل الملف...*")
            info = bot.get_file(doc.file_id)
            url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{info.file_path}"
            buf  = io.BytesIO()
            with urllib.request.urlopen(url) as r:
                while True:
                    chunk = r.read(CHUNK_SIZE)
                    if not chunk: break
                    buf.write(chunk)
            user_lib_dir = os.path.join(LIBS_DIR, str(uid))
            os.makedirs(user_lib_dir, exist_ok=True)
            save_path = os.path.join(user_lib_dir, doc.file_name)
            with open(save_path,"wb") as f: f.write(buf.getvalue())
            old = db_one("SELECT id FROM libs WHERE user_id=? AND file_name=?", (uid, doc.file_name))
            if old: db_run("DELETE FROM libs WHERE id=?", (old["id"],))
            db_run("INSERT INTO libs(user_id,file_id,file_name,file_size) VALUES(?,?,?,?)",
                   (uid, doc.file_id, doc.file_name, doc.file_size or 0))
            upd("📦 *جاري التثبيت...*")
            r = subprocess.run([sys.executable,"-m","pip","install",save_path,"--quiet"],
                               capture_output=True, text=True, timeout=120)
            st = "✅ تم التثبيت!" if r.returncode==0 else f"⚠️ تحذير:\n`{r.stderr[-150:]}`"
            upd(f"📚 *تم رفع:* `{doc.file_name}`\n{st}")
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(types.InlineKeyboardButton("📚 مكتبتي",  callback_data="my_libs", style="success"),
                   types.InlineKeyboardButton("🔙 القائمة", callback_data="home", style="danger"))
            try: bot.edit_message_reply_markup(chat_id, wait.message_id, reply_markup=kb)
            except: pass
        except Exception as e:
            upd(f"❌ *خطأ:*\n```\n{str(e)[:300]}\n```")

    threading.Thread(target=pipeline, daemon=True).start()

@bot.callback_query_handler(func=lambda c: c.data.startswith("del_lib_"))
def cb_del_lib(call):
    lid = int(call.data.split("_")[2])
    lib = db_one("SELECT * FROM libs WHERE id=? AND user_id=?", (lid, call.from_user.id))
    if not lib:
        bot.answer_callback_query(call.id, "❌ المكتبة غير موجودة!", show_alert=True); return
    delete_lib_record(lid)
    bot.answer_callback_query(call.id, f"🗑️ تم حذف {lib['file_name']}")
    render_libs(call.message.chat.id, call.message.message_id, call.from_user.id)

# ═══════════════════════════════════════════════════════════
#  🎛️  أزرار التحكم بالسيرفر (من لوحة التشغيل)
# ═══════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("kill_"))
def cb_kill(call):
    uid = int(call.data.split("_")[1])
    if call.from_user.id != uid and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية!", show_alert=True); return
    e = running_processes.get(uid)
    if e and e["proc"].poll() is None:
        fname = e["name"]; kill_proc(uid)
        bot.answer_callback_query(call.id, f"🛑 تم إيقاف {fname}")
        bot.send_message(call.message.chat.id,
                         f"🛑 *تم إيقاف السيرفر*\n`{fname}`{FOOTER}",
                         parse_mode="Markdown", reply_markup=back_home())
    else:
        bot.answer_callback_query(call.id, "ℹ️ السيرفر لم يعد يعمل.", show_alert=True)

@bot.callback_query_handler(func=lambda c: c.data.startswith("out_"))
def cb_out(call):
    uid = int(call.data.split("_")[1])
    if call.from_user.id != uid and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية!", show_alert=True); return
    e = running_processes.get(uid)
    if not e:
        bot.answer_callback_query(call.id, "❌ لا توجد عملية.", show_alert=True); return
    lines   = e.get("output",[])[-20:]
    preview = "\n".join(lines) if lines else "_(لا يوجد output)_"
    if len(preview) > 1500: preview = preview[-1500:]
    st = "🟢 يعمل" if e["proc"].poll() is None else "🔴 توقف"
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id,
        f"📊 *Output* | {st}\n`{e['name']}` | PID `{e['pid']}`\n```\n{preview}\n```{FOOTER}",
        parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("rst_"))
def cb_rst(call):
    uid = int(call.data.split("_")[1])
    if call.from_user.id != uid and call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "🚫 ليس لديك صلاحية!", show_alert=True); return
    e = running_processes.get(uid)
    fname = e["name"] if e else None
    if not fname:
        files = get_user_files(uid)
        if not files:
            bot.answer_callback_query(call.id, "❌ لا يوجد ملف!", show_alert=True); return
        fname = files[0]["file_name"]
    save_path = os.path.join(HOSTING_DIR, str(uid), fname)
    if not os.path.exists(save_path):
        bot.answer_callback_query(call.id, "❌ الملف مفقود من القرص!", show_alert=True); return
    proc = start_proc(uid, save_path, fname, os.path.join(HOSTING_DIR, str(uid)))
    bot.answer_callback_query(call.id, f"🔄 إعادة تشغيل | PID {proc.pid}")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🛑 إيقاف",  callback_data=f"kill_{uid}", style="danger"),
           types.InlineKeyboardButton("📋 Output", callback_data=f"out_{uid}", style="primary"))
    kb.add(types.InlineKeyboardButton("🔙 القائمة", callback_data="home", style="danger"))
    bot.send_message(call.message.chat.id,
        f"🔄 *إعادة التشغيل بنجاح!*\n`{fname}` | PID `{proc.pid}`{FOOTER}",
        parse_mode="Markdown", reply_markup=kb)

# ═══════════════════════════════════════════════════════════
#  ⚙️  ADMIN
# ═══════════════════════════════════════════════════════════
def is_adm(call): return call.from_user.id == ADMIN_ID

@bot.message_handler(commands=["admin"])
@admin_only
def cmd_admin(msg):
    bot.send_message(msg.chat.id, f"⚙️ *لوحة الأدمن*{FOOTER}",
                     parse_mode="Markdown", reply_markup=admin_kb())

@bot.callback_query_handler(func=lambda c: c.data == "adm_home")
def cb_adm_home(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    try: bot.edit_message_text(f"⚙️ *لوحة الأدمن*{FOOTER}",
                                call.message.chat.id, call.message.message_id,
                                parse_mode="Markdown", reply_markup=admin_kb())
    except: bot.send_message(call.message.chat.id, f"⚙️ *لوحة الأدمن*{FOOTER}",
                              parse_mode="Markdown", reply_markup=admin_kb())

@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def cb_adm_stats(call):
    if not is_adm(call): return
    u = db_one("SELECT COUNT(*) AS c FROM users")["c"]
    v = db_one("SELECT COUNT(*) AS c FROM users WHERE is_vip=1")["c"]
    b = db_one("SELECT COUNT(*) AS c FROM users WHERE is_banned=1")["c"]
    f = db_one("SELECT COUNT(*) AS c FROM files")["c"]
    l = db_one("SELECT COUNT(*) AS c FROM libs")["c"]
    p = len([e for e in running_processes.values() if e["proc"].poll() is None])
    txt = (f"📊 *الإحصائيات*\n{'━'*22}\n"
           f"👥 المستخدمون: `{u}`\n👑 VIP: `{v}`\n🚫 محظورون: `{b}`\n"
           f"📂 الملفات: `{f}`\n📚 المكتبات: `{l}`\n🟢 سيرفرات: `{p}`{FOOTER}")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="adm_home", style="danger"))
    bot.answer_callback_query(call.id)
    try: bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                                parse_mode="Markdown", reply_markup=kb)
    except: bot.send_message(call.message.chat.id, txt, parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "adm_procs")
def cb_adm_procs(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    if not running_processes:
        bot.answer_callback_query(call.id, "ℹ️ لا توجد سيرفرات جارية.", show_alert=True); return
    txt = f"🔧 *السيرفرات ({len(running_processes)}):*\n{'━'*22}\n"
    kb  = types.InlineKeyboardMarkup(row_width=1)
    for uid, e in running_processes.items():
        st = "🟢" if e["proc"].poll() is None else "🔴"
        txt += f"{st} `{uid}` — `{e['name']}` PID`{e['pid']}`\n"
        if e["proc"].poll() is None:
            kb.add(types.InlineKeyboardButton(f"🛑 إيقاف {e['name']}", callback_data=f"kill_{uid}", style="primary"))
    kb.add(types.InlineKeyboardButton("🛑 إيقاف الكل", callback_data="adm_killall", style="success"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع",       callback_data="adm_home", style="danger"))
    txt += FOOTER
    try: bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                                parse_mode="Markdown", reply_markup=kb)
    except: bot.send_message(call.message.chat.id, txt, parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "adm_killall")
def cb_killall(call):
    if not is_adm(call): return
    n = len(running_processes)
    for uid in list(running_processes): kill_proc(uid)
    bot.answer_callback_query(call.id, f"🛑 تم إيقاف {n} سيرفر.", show_alert=True)
    cb_adm_home(call)

@bot.callback_query_handler(func=lambda c: c.data == "adm_broadcast")
def cb_adm_broadcast(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "📣 *أرسل رسالة البث:*", parse_mode="Markdown")
    bot.register_next_step_handler(msg, do_broadcast)

def do_broadcast(msg: types.Message):
    uids = [r["user_id"] for r in db_all("SELECT user_id FROM users")]
    ok = fail = 0
    st = bot.send_message(msg.chat.id, f"📡 `0/{len(uids)}`", parse_mode="Markdown")
    for i, uid in enumerate(uids, 1):
        try:
            if msg.photo: bot.send_photo(uid, msg.photo[-1].file_id, caption=(msg.caption or "")+FOOTER, parse_mode="Markdown")
            else: bot.send_message(uid, (msg.text or "")+FOOTER, parse_mode="Markdown")
            ok += 1
        except: fail += 1
        if i % 20 == 0 or i == len(uids):
            try: bot.edit_message_text(f"📡 `{i}/{len(uids)}`", msg.chat.id, st.message_id, parse_mode="Markdown")
            except: pass
        time.sleep(0.04)
    bot.edit_message_text(f"✅ *اكتمل!* ✔️`{ok}` ❌`{fail}`{FOOTER}",
                          msg.chat.id, st.message_id, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data == "adm_payment")
def cb_adm_payment(call):
    if not is_adm(call): return
    mode = setting("payment_mode")
    kb   = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🆓 مجاني", callback_data="pay_free", style="primary"),
           types.InlineKeyboardButton("💳 مدفوع", callback_data="pay_paid", style="success"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="adm_home", style="danger"))
    bot.answer_callback_query(call.id)
    st = "🆓 مجاني" if mode=="free" else "💳 مدفوع"
    try: bot.edit_message_text(f"💳 *وضع الدفع:* {st}{FOOTER}",
                                call.message.chat.id, call.message.message_id,
                                parse_mode="Markdown", reply_markup=kb)
    except: pass

@bot.callback_query_handler(func=lambda c: c.data in ("pay_free","pay_paid"))
def cb_pay_toggle(call):
    if not is_adm(call): return
    setting_set("payment_mode", "free" if call.data=="pay_free" else "paid")
    bot.answer_callback_query(call.id, "✅ تم"); cb_adm_payment(call)

@bot.callback_query_handler(func=lambda c: c.data == "adm_sub")
def cb_adm_sub(call):
    if not is_adm(call): return
    en = setting("sub_enabled"); ch = setting("channel_username")
    st = "✅ مفعّل" if en=="1" else "❌ معطّل"
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🟢 تفعيل", callback_data="sub_on", style="success"),
           types.InlineKeyboardButton("🔴 تعطيل", callback_data="sub_off", style="success"))
    kb.add(types.InlineKeyboardButton("✏️ تغيير القناة", callback_data="sub_setchan", style="primary"))
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="adm_home", style="danger"))
    bot.answer_callback_query(call.id)
    try: bot.edit_message_text(
        f"📢 *الاشتراك الإجباري*\nالحالة: {st}\nالقناة: `{ch or 'غير محددة'}`{FOOTER}",
        call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=kb)
    except: pass

@bot.callback_query_handler(func=lambda c: c.data in ("sub_on","sub_off"))
def cb_sub_toggle(call):
    if not is_adm(call): return
    setting_set("sub_enabled", "1" if call.data=="sub_on" else "0")
    bot.answer_callback_query(call.id, "✅ تم"); cb_adm_sub(call)

@bot.callback_query_handler(func=lambda c: c.data == "sub_setchan")
def cb_sub_setchan(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "✏️ *أرسل يوزر القناة بدون @:*", parse_mode="Markdown")
    bot.register_next_step_handler(msg, lambda m: (
        setting_set("channel_username", m.text.strip().lstrip("@")),
        bot.send_message(m.chat.id, f"✅ تم: `@{m.text.strip().lstrip('@')}`{FOOTER}", parse_mode="Markdown")
    ))

@bot.callback_query_handler(func=lambda c: c.data == "adm_vip_add")
def cb_adm_vip(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "👑 *ID المستخدم لإضافة VIP:*", parse_mode="Markdown")
    def step(m):
        try:
            tid = int(m.text.strip()); set_vip(tid, 1)
            bot.send_message(m.chat.id, f"✅ `{tid}` أصبح VIP!{FOOTER}", parse_mode="Markdown")
            try: bot.send_message(tid, f"🎉 *تمت ترقيتك إلى VIP!*{FOOTER}", parse_mode="Markdown")
            except: pass
        except: bot.send_message(m.chat.id, "❌ معرّف غير صالح!")
    bot.register_next_step_handler(msg, step)

@bot.callback_query_handler(func=lambda c: c.data == "adm_ban")
def cb_adm_ban(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🚫 *ID المستخدم لحظره:*", parse_mode="Markdown")
    def step(m):
        try:
            tid = int(m.text.strip()); ban_user(tid); kill_proc(tid)
            bot.send_message(m.chat.id, f"✅ تم حظر `{tid}`{FOOTER}", parse_mode="Markdown")
            try: bot.send_message(tid, f"🚫 *تم حظرك.*{FOOTER}", parse_mode="Markdown")
            except: pass
        except: bot.send_message(m.chat.id, "❌ معرّف غير صالح!")
    bot.register_next_step_handler(msg, step)

@bot.callback_query_handler(func=lambda c: c.data == "adm_unban")
def cb_adm_unban(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "✅ *ID المستخدم لرفع الحظر:*", parse_mode="Markdown")
    def step(m):
        try:
            tid = int(m.text.strip()); unban_user(tid)
            bot.send_message(m.chat.id, f"✅ رُفع الحظر عن `{tid}`{FOOTER}", parse_mode="Markdown")
            try: bot.send_message(tid, f"✅ *تم رفع الحظر عنك!*{FOOTER}", parse_mode="Markdown")
            except: pass
        except: bot.send_message(m.chat.id, "❌ معرّف غير صالح!")
    bot.register_next_step_handler(msg, step)

@bot.callback_query_handler(func=lambda c: c.data == "adm_msg")
def cb_adm_msg(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id,
        "💬 *أرسل ID ثم الرسالة:*\n_(123456 مرحبا)_", parse_mode="Markdown")
    def step(m):
        parts = m.text.strip().split(None, 1)
        if len(parts) != 2:
            bot.send_message(m.chat.id, "❌ صيغة: ID الرسالة"); return
        try:
            tid = int(parts[0])
            bot.send_message(tid, f"📨 *رسالة من الإدارة:*\n\n{parts[1]}{FOOTER}", parse_mode="Markdown")
            bot.send_message(m.chat.id, f"✅ تم الإرسال لـ `{tid}`{FOOTER}", parse_mode="Markdown")
        except Exception as e: bot.send_message(m.chat.id, f"❌ {e}")
    bot.register_next_step_handler(msg, step)

@bot.callback_query_handler(func=lambda c: c.data == "adm_users")
def cb_adm_users(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    rows = db_all("SELECT * FROM users ORDER BY joined_at DESC LIMIT 15")
    txt  = f"👥 *آخر 15 مستخدم:*\n{'━'*22}\n"
    for u in rows:
        vip = "👑" if u["is_vip"] else "👤"
        ban = "🚫" if u["is_banned"] else ""
        txt += f"{vip}{ban} `{u['user_id']}` @{u['username'] or 'N/A'} — {u['full_name'][:12]}\n"
    txt += FOOTER
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="adm_home", style="danger"))
    try: bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                                parse_mode="Markdown", reply_markup=kb)
    except: bot.send_message(call.message.chat.id, txt, parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "adm_view_files")
def cb_adm_view_files(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    txt = f"📁 *كل ملفات الاستضافة:*\n{'━'*22}\n"
    total = 0
    try:
        for user_folder in os.listdir(HOSTING_DIR):
            user_path = os.path.join(HOSTING_DIR, user_folder)
            if os.path.isdir(user_path):
                files = os.listdir(user_path)
                if files:
                    txt += f"\n👤 المستخدم `{user_folder}`:\n"
                    for fname in files:
                        fpath = os.path.join(user_path, fname)
                        fsize = os.path.getsize(fpath) // 1024
                        uid_int = int(user_folder) if user_folder.isdigit() else 0
                        e = running_processes.get(uid_int)
                        st = "🟢" if e and e.get("name") == fname and e["proc"].poll() is None else "⚫"
                        txt += f"  {st} `{fname}` — {fsize}KB\n"
                        total += 1
    except Exception as ex:
        txt += f"❌ خطأ: `{ex}`\n"
    txt += f"\n{'━'*22}\n📊 إجمالي: `{total}` ملف{FOOTER}"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 رجوع", callback_data="adm_home", style="danger"))
    if len(txt) > 4096: txt = txt[:4000] + f"\n...{FOOTER}"
    try: bot.edit_message_text(txt, call.message.chat.id, call.message.message_id,
                                parse_mode="Markdown", reply_markup=kb)
    except: bot.send_message(call.message.chat.id, txt, parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data == "adm_reset_confirm")
def cb_adm_reset_confirm(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ نعم، امسح كل شيء!", callback_data="adm_reset_exec", style="success"),
        types.InlineKeyboardButton("❌ إلغاء",             callback_data="adm_home", style="danger"),
    )
    try: bot.edit_message_text(
        f"⚠️ *تأكيد الإعادة الكاملة*\n{'━'*22}\n"
        f"🚨 سيتم:\n"
        f"• إيقاف جميع السيرفرات الجارية\n"
        f"• حذف كل ملفات المستخدمين\n"
        f"• حذف كل المكتبات\n"
        f"• مسح قاعدة البيانات بالكامل\n"
        f"{'━'*22}\n"
        f"❓ هل أنت متأكد تماماً؟{FOOTER}",
        call.message.chat.id, call.message.message_id,
        parse_mode="Markdown", reply_markup=kb)
    except: pass

@bot.callback_query_handler(func=lambda c: c.data == "adm_reset_exec")
def cb_adm_reset_exec(call):
    if not is_adm(call): return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "🔴 *جاري إعادة تعيين الاستضافة...*", parse_mode="Markdown")
    # 1. إيقاف جميع السيرفرات
    for uid in list(running_processes): kill_proc(uid)
    # 2. حذف جميع الملفات
    import shutil
    deleted_files = deleted_libs = 0
    try:
        if os.path.exists(HOSTING_DIR):
            for f in os.listdir(HOSTING_DIR):
                fp = os.path.join(HOSTING_DIR, f)
                shutil.rmtree(fp, ignore_errors=True) if os.path.isdir(fp) else os.remove(fp)
                deleted_files += 1
    except: pass
    try:
        if os.path.exists(LIBS_DIR):
            for f in os.listdir(LIBS_DIR):
                fp = os.path.join(LIBS_DIR, f)
                shutil.rmtree(fp, ignore_errors=True) if os.path.isdir(fp) else os.remove(fp)
                deleted_libs += 1
    except: pass
    # 3. مسح قاعدة البيانات (الملفات والمكتبات فقط، مع الإبقاء على المستخدمين)
    try:
        conn = get_conn()
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM libs")
        conn.execute("UPDATE users SET file_count=0")
        conn.commit(); conn.close()
    except: pass
    bot.send_message(call.message.chat.id,
        f"✅ *تم إعادة تعيين الاستضافة بنجاح!*\n{'━'*22}\n"
        f"🛑 السيرفرات الموقوفة: `{len(running_processes) + 1}` (قبل الإيقاف)\n"
        f"🗂️ المجلدات المحذوفة: `{deleted_files + deleted_libs}`\n"
        f"🗄️ قاعدة البيانات: مُنظَّفة\n{'━'*22}\n"
        f"🟢 الاستضافة جاهزة من جديد!{FOOTER}",
        parse_mode="Markdown", reply_markup=admin_kb())

# ═══════════════════════════════════════════════════════════
#  🚦  ENTRY POINT
# ═══════════════════════════════════════════════════════════
def main():
    init_db()
    log.info(f"🚀 Bot started | Admin: {ADMIN_ID} | Dev: {DEV_USER}")
    bot.infinity_polling(
        timeout=20,
        long_polling_timeout=20,
        skip_pending=True,
        logger_level=logging.WARNING,
        allowed_updates=["message","callback_query"]
    )

if __name__ == "__main__":
    main()
