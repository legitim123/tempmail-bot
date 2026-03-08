import requests
import random
import string
import re
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters

import os
TOKEN = os.environ.get("TOKEN", "")  # Set this in Railway environment variables

# ── Plan settings ─────────────────────────────────────────────────────────────
FREE_EMAIL_LIFETIME_MINUTES = 5
FREE_DAILY_EMAIL_LIMIT = 2
PREMIUM_EMAIL_LIFETIME_HOURS = 24
PREMIUM_DURATION_DAYS = 7
PREMIUM_STARS_PRICE = 50  # Telegram Stars
ADMIN_ID = 0  # Your Telegram user ID (get it from @userinfobot)

DB_PATH = "tempmail.db"

# ── Database ──────────────────────────────────────────────────────────────────
def db_init():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        chat_id     INTEGER PRIMARY KEY,
        lang        TEXT    DEFAULT 'en',
        premium_expiry TEXT DEFAULT NULL,
        daily_date  TEXT    DEFAULT NULL,
        daily_count INTEGER DEFAULT 0,
        created_at  TEXT    DEFAULT (datetime('now'))
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sponsors (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        name    TEXT NOT NULL,
        text    TEXT NOT NULL,
        url     TEXT NOT NULL,
        button  TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()


def db_get_user(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT lang, premium_expiry, daily_date, daily_count FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()
    return row


def db_set_lang(chat_id, lang):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO users (chat_id, lang) VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE SET lang=?",
              (chat_id, lang, lang))
    conn.commit()
    conn.close()


def db_set_premium(chat_id, expiry: datetime):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    expiry_str = expiry.strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO users (chat_id, premium_expiry) VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE SET premium_expiry=?",
              (chat_id, expiry_str, expiry_str))
    conn.commit()
    conn.close()


def db_get_premium_expiry(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT premium_expiry FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
    return None


def db_get_daily_count(chat_id):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT daily_date, daily_count FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0] == today:
        return row[1]
    return 0


def db_increment_daily(chat_id):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT daily_date, daily_count FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    if row and row[0] == today:
        new_count = row[1] + 1
        c.execute("UPDATE users SET daily_count=? WHERE chat_id=?", (new_count, chat_id))
    else:
        c.execute("INSERT INTO users (chat_id, daily_date, daily_count) VALUES (?,?,1) ON CONFLICT(chat_id) DO UPDATE SET daily_date=?, daily_count=1",
                  (chat_id, today, today))
    conn.commit()
    conn.close()


def db_add_sponsor(name, text, url, button):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO sponsors (name, text, url, button) VALUES (?,?,?,?)", (name, text, url, button))
    conn.commit()
    conn.close()


def db_get_sponsors():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, text, url, button FROM sponsors")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "text": r[2], "url": r[3], "button": r[4]} for r in rows]


def db_remove_sponsor(index):
    sponsors = db_get_sponsors()
    if index < 0 or index >= len(sponsors):
        return None
    sponsor_id = sponsors[index]["id"]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM sponsors WHERE id=?", (sponsor_id,))
    conn.commit()
    conn.close()
    return sponsors[index]["name"]


def db_get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE premium_expiry > datetime('now')")
    premium = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM sponsors")
    sponsors = c.fetchone()[0]
    conn.close()
    return total, premium, sponsors


# ── Sponsor data structure ────────────────────────────────────────────────────
# Stored in bot_data["sponsors"] as list of dicts:
# {"name": str, "text": str, "url": str, "button": str}

# ── Translations ──────────────────────────────────────────────────────────────
TEXTS = {
    "en": {
        "choose_lang":        "🌍 Please select your language:",
        "welcome_title":      "📬 TempMail Bot",
        "welcome_body":       (
            "Generate a disposable email address instantly.\n\n"
            "✅ No registration required\n"
            "✅ Messages delivered in real time\n"
            "✅ Protect your inbox from spam\n\n"
            "Press the button below to get started."
        ),
        "get_started":        "🚀 Get Started",
        "generating":         "⏳ Generating your temporary address...",
        "active":             "✅ Your temporary address is active.\n\nTap to copy:",
        "expires_in":         "⏱ Expires in: {minutes} min",
        "auto_renew":         "Address renews automatically when expired.",
        "gen_new":            "🔄 Generate New Address",
        "change_lang":        "🌍 Language",
        "get_premium":        "⭐ Get Premium",
        "my_plan":            "👑 My Plan",
        "incoming":           "📬 New Message",
        "from_label":         "From",
        "subject_label":      "Subject",
        "expired_notice":     "⏰ Address expired. A new one has been generated.\n\n",
        "gen_failed":         "❌ Failed to generate address. Please try /start again.",
        "new_failed":         "❌ Failed to generate address. Please try again.",
        "daily_limit":        (
            "⚠️ Daily limit reached.\n\n"
            "Free plan allows 2 addresses per day.\n\n"
            "Upgrade to Premium for unlimited access."
        ),
        "premium_info":       (
            "⭐ *TempMail Premium*\n\n"
            "✅ 24\\-hour address lifetime\n"
            "✅ Unlimited addresses per day\n"
            "✅ Priority message delivery\n\n"
            "Price: *50 Stars* for 7 days\n\n"
            "Press the button below to subscribe\\."
        ),
        "buy_premium":        "⭐ Pay 50 Stars",
        "premium_active":     (
            "👑 *Premium Active*\n\n"
            "Your plan expires on: {date}\n\n"
            "Enjoy unlimited access\\!"
        ),
        "free_plan":          (
            "📋 *Free Plan*\n\n"
            "Address lifetime: 5 minutes\n"
            "Daily limit: 2 addresses\n\n"
            "Upgrade to Premium for more\\."
        ),
        "payment_success":    "🎉 Premium activated! Valid for 7 days. Enjoy unlimited access!",
        "premium_badge":      "👑 PREMIUM",
        "back":               "🔙 Back",
        "invoice_title":      "TempMail Premium",
        "invoice_desc":       "7-day Premium access: 24h addresses, unlimited daily usage.",
    },
    "ru": {
        "choose_lang":        "🌍 Пожалуйста, выберите язык:",
        "welcome_title":      "📬 TempMail Bot",
        "welcome_body":       (
            "Мгновенно создайте одноразовый адрес электронной почты.\n\n"
            "✅ Регистрация не требуется\n"
            "✅ Сообщения в реальном времени\n"
            "✅ Защита от спама\n\n"
            "Нажмите кнопку ниже, чтобы начать."
        ),
        "get_started":        "🚀 Начать",
        "generating":         "⏳ Генерация адреса...",
        "active":             "✅ Ваш временный адрес активен.\n\nНажмите, чтобы скопировать:",
        "expires_in":         "⏱ Истекает через: {minutes} мин",
        "auto_renew":         "Адрес обновится автоматически.",
        "gen_new":            "🔄 Новый адрес",
        "change_lang":        "🌍 Язык",
        "get_premium":        "⭐ Premium",
        "my_plan":            "👑 Мой план",
        "incoming":           "📬 Новое сообщение",
        "from_label":         "От",
        "subject_label":      "Тема",
        "expired_notice":     "⏰ Адрес истёк. Создан новый.\n\n",
        "gen_failed":         "❌ Ошибка. Попробуйте /start снова.",
        "new_failed":         "❌ Ошибка. Попробуйте снова.",
        "daily_limit":        (
            "⚠️ Дневной лимит исчерпан.\n\n"
            "Бесплатный план: 2 адреса в день.\n\n"
            "Оформите Premium для безлимитного доступа."
        ),
        "premium_info":       (
            "⭐ *TempMail Premium*\n\n"
            "✅ Адрес на 24 часа\n"
            "✅ Безлимитные адреса в день\n"
            "✅ Приоритетная доставка\n\n"
            "Цена: *50 Stars* на 7 дней\n\n"
            "Нажмите кнопку для оформления\\."
        ),
        "buy_premium":        "⭐ Оплатить 50 Stars",
        "premium_active":     (
            "👑 *Premium активен*\n\n"
            "Действует до: {date}\n\n"
            "Пользуйтесь безлимитным доступом\\!"
        ),
        "free_plan":          (
            "📋 *Бесплатный план*\n\n"
            "Время жизни адреса: 5 минут\n"
            "Дневной лимит: 2 адреса\n\n"
            "Оформите Premium для большего\\."
        ),
        "payment_success":    "🎉 Premium активирован на 7 дней. Наслаждайтесь безлимитным доступом!",
        "premium_badge":      "👑 PREMIUM",
        "back":               "🔙 Назад",
        "invoice_title":      "TempMail Premium",
        "invoice_desc":       "Premium на 7 дней: адреса на 24ч, безлимитное использование.",
    },
    "tr": {
        "choose_lang":        "🌍 Lütfen dilinizi seçin:",
        "welcome_title":      "📬 TempMail Bot",
        "welcome_body":       (
            "Anında geçici e-posta adresi oluşturun.\n\n"
            "✅ Kayıt gerekmez\n"
            "✅ Mesajlar anında iletilir\n"
            "✅ Gelen kutunuzu spam'den koruyun\n\n"
            "Başlamak için aşağıdaki düğmeye basın."
        ),
        "get_started":        "🚀 Başla",
        "generating":         "⏳ Adresiniz oluşturuluyor...",
        "active":             "✅ Geçici adresiniz aktif.\n\nKopyalamak için dokunun:",
        "expires_in":         "⏱ Kalan süre: {minutes} dk",
        "auto_renew":         "Süre dolduğunda adres otomatik yenilenir.",
        "gen_new":            "🔄 Yeni Adres Oluştur",
        "change_lang":        "🌍 Dil",
        "get_premium":        "⭐ Premium Al",
        "my_plan":            "👑 Planım",
        "incoming":           "📬 Yeni Mesaj",
        "from_label":         "Gönderen",
        "subject_label":      "Konu",
        "expired_notice":     "⏰ Adres süresi doldu. Yeni adres oluşturuldu.\n\n",
        "gen_failed":         "❌ Oluşturulamadı. /start komutunu tekrar deneyin.",
        "new_failed":         "❌ Oluşturulamadı. Tekrar deneyin.",
        "daily_limit":        (
            "⚠️ Günlük limit doldu.\n\n"
            "Ücretsiz plan: günde 2 adres.\n\n"
            "Sınırsız erişim için Premium alın."
        ),
        "premium_info":       (
            "⭐ *TempMail Premium*\n\n"
            "✅ 24 saatlik adres ömrü\n"
            "✅ Günlük sınırsız adres\n"
            "✅ Öncelikli mesaj iletimi\n\n"
            "Fiyat: *50 Stars* \\- 7 gün\n\n"
            "Abone olmak için düğmeye basın\\."
        ),
        "buy_premium":        "⭐ 50 Stars Öde",
        "premium_active":     (
            "👑 *Premium Aktif*\n\n"
            "Bitiş tarihi: {date}\n\n"
            "Sınırsız erişimin tadını çıkarın\\!"
        ),
        "free_plan":          (
            "📋 *Ücretsiz Plan*\n\n"
            "Adres ömrü: 5 dakika\n"
            "Günlük limit: 2 adres\n\n"
            "Daha fazlası için Premium alın\\."
        ),
        "payment_success":    "🎉 Premium aktifleştirildi! 7 gün geçerli. Sınırsız erişimin keyfini çıkarın!",
        "premium_badge":      "👑 PREMIUM",
        "back":               "🔙 Geri",
        "invoice_title":      "TempMail Premium",
        "invoice_desc":       "7 günlük Premium: 24 saatlik adresler, sınırsız kullanım.",
    },
}


def t(lang, key, **kwargs):
    text = TEXTS.get(lang, TEXTS["en"]).get(key, key)
    return text.format(**kwargs) if kwargs else text


def clean_text(text):
    if not text:
        return ""
    text = text.replace("&", "and")
    text = re.sub(r'[*`_\[\]<>]', '', text)
    return text[:1000]


def escape_md(text):
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


# ── Premium helpers ───────────────────────────────────────────────────────────
def is_premium(context, chat_id):
    expiry = db_get_premium_expiry(chat_id)
    return expiry is not None and datetime.now() < expiry


def activate_premium(context, chat_id):
    expiry = datetime.now() + timedelta(days=PREMIUM_DURATION_DAYS)
    db_set_premium(chat_id, expiry)
    return expiry


def get_email_lifetime(context, chat_id):
    if is_premium(context, chat_id):
        return PREMIUM_EMAIL_LIFETIME_HOURS * 60
    return FREE_EMAIL_LIFETIME_MINUTES


def check_daily_limit(context, chat_id):
    if is_premium(context, chat_id):
        return True
    return db_get_daily_count(chat_id) < FREE_DAILY_EMAIL_LIMIT


def increment_daily_count(context, chat_id):
    db_increment_daily(chat_id)


# ── Sponsor helpers ──────────────────────────────────────────────────────────
def get_sponsors(context):
    return db_get_sponsors()


def get_random_sponsor(context):
    sponsors = db_get_sponsors()
    return random.choice(sponsors) if sponsors else None


async def send_sponsor(context, chat_id):
    sponsor = get_random_sponsor(context)
    if not sponsor:
        return
    keyboard = [[InlineKeyboardButton(sponsor["button"], url=sponsor["url"])]]
    try:
        msg = "📢 " + sponsor["name"] + "\n\n" + sponsor["text"]
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        print("Sponsor send error: " + str(e))

# ── Mail helpers ──────────────────────────────────────────────────────────────
def create_email():
    try:
        domains = requests.get("https://api.mail.tm/domains").json()
        domain = domains["hydra:member"][0]["domain"]
        name = ''.join(random.choices(string.ascii_lowercase, k=10))
        email = f"{name}@{domain}"
        password = "TempPass123!"
        r = requests.post("https://api.mail.tm/accounts", json={
            "address": email,
            "password": password
        })
        if r.status_code == 201:
            return email, password
        return None, None
    except Exception as e:
        print(f"Create email error: {e}")
        return None, None


def get_mail_token(email, password):
    try:
        r = requests.post("https://api.mail.tm/token", json={
            "address": email,
            "password": password
        })
        return r.json().get("token")
    except Exception as e:
        print(f"Token error: {e}")
        return None


def check_messages(token):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get("https://api.mail.tm/messages", headers=headers)
        return r.json().get("hydra:member", [])
    except Exception as e:
        return []


def get_message_content(token, message_id):
    try:
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(f"https://api.mail.tm/messages/{message_id}", headers=headers)
        data = r.json()
        text = data.get("text", "")
        if not text:
            html = data.get("html", [""])[0] if data.get("html") else ""
            text = re.sub(r'<[^>]+>', '', html).strip()
        return clean_text(text) or "Content unavailable."
    except Exception as e:
        return "Failed to retrieve message content."


def build_email_message(lang, email, expires_at, is_prem=False):
    minutes_left = max(0, int((expires_at - datetime.now()).total_seconds() // 60))
    badge = f"{t(lang, 'premium_badge')} | " if is_prem else ""
    text = (
        f"{escape_md(badge + t(lang, 'active'))}\n\n"
        f"`{escape_md(email)}`\n\n"
        f"{escape_md(t(lang, 'expires_in', minutes=minutes_left))}\n"
        f"{escape_md(t(lang, 'auto_renew'))}"
    )
    keyboard = [
        [InlineKeyboardButton(t(lang, "gen_new"), callback_data="new_email")],
        [
            InlineKeyboardButton(t(lang, "my_plan"), callback_data="my_plan"),
            InlineKeyboardButton(t(lang, "change_lang"), callback_data="change_lang"),
        ],
    ]
    if not is_prem:
        keyboard.insert(1, [InlineKeyboardButton(t(lang, "get_premium"), callback_data="premium_info")])
    return text, InlineKeyboardMarkup(keyboard)


def setup_jobs(context, chat_id, token, seen_ids, lifetime_minutes):
    for job in context.job_queue.get_jobs_by_name(f"check_{chat_id}"):
        job.schedule_removal()
    for job in context.job_queue.get_jobs_by_name(f"expire_{chat_id}"):
        job.schedule_removal()

    context.job_queue.run_repeating(
        auto_check_job,
        interval=10,
        first=10,
        name=f"check_{chat_id}",
        data={"chat_id": chat_id, "token": token, "seen_ids": seen_ids}
    )
    context.job_queue.run_once(
        expire_job,
        when=lifetime_minutes * 60,
        name=f"expire_{chat_id}",
        data={"chat_id": chat_id}
    )


# ── Jobs ──────────────────────────────────────────────────────────────────────
async def auto_check_job(context):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    token = job_data["token"]
    seen_ids = job_data["seen_ids"]
    row = db_get_user(chat_id)
    lang = row[0] if row else "en"

    messages = check_messages(token)
    for msg in messages:
        if msg["id"] not in seen_ids:
            seen_ids.add(msg["id"])
            content = get_message_content(token, msg["id"])
            sender = clean_text(msg["from"]["address"])
            subject = clean_text(msg["subject"] or "(no subject)")
            text = (
                f"{t(lang, 'incoming')}\n\n"
                f"{t(lang, 'from_label')}: {sender}\n"
                f"{t(lang, 'subject_label')}: {subject}\n"
                f"----------------------------------\n"
                f"{content}"
            )
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                print(f"Notification error: {e}")


async def expire_job(context):
    job_data = context.job.data
    chat_id = job_data["chat_id"]
    row = db_get_user(chat_id)
    lang = row[0] if row else "en"

    for job in context.job_queue.get_jobs_by_name(f"check_{chat_id}"):
        job.schedule_removal()

    if not check_daily_limit(context, chat_id):
        await context.bot.send_message(chat_id=chat_id, text=t(lang, "daily_limit"))
        return

    email, password = create_email()
    if not email:
        return

    mail_token = get_mail_token(email, password)
    if not mail_token:
        return

    increment_daily_count(context, chat_id)
    seen_ids = set()
    lifetime = get_email_lifetime(context, chat_id)
    expires_at = datetime.now() + timedelta(minutes=lifetime)

    context.bot_data[f"email_{chat_id}"] = email
    context.bot_data[f"token_{chat_id}"] = mail_token
    context.bot_data[f"seen_{chat_id}"] = seen_ids
    context.bot_data[f"expires_{chat_id}"] = expires_at

    setup_jobs(context, chat_id, mail_token, seen_ids, lifetime)

    prem = is_premium(context, chat_id)
    email_text, keyboard = build_email_message(lang, email, expires_at, prem)
    notice = escape_md(t(lang, "expired_notice")) + email_text
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=notice,
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        print(f"Expire notification error: {e}")


# ── Handlers ──────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    row = db_get_user(chat_id)
    lang = row[0] if row else None

    if not lang:
        keyboard = [
            [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
            [InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")],
        ]
        try:
            with open("logo.png", "rb") as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption="📬 TempMail Bot\n\n" + t("en", "choose_lang"),
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception:
            await update.message.reply_text(
                "📬 TempMail Bot\n\n" + t("en", "choose_lang"),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    await update.message.reply_text(t(lang, "generating"))
    await do_generate(context, chat_id, lang)


async def do_generate(context, chat_id, lang):
    if not check_daily_limit(context, chat_id):
        keyboard = [[InlineKeyboardButton(t(lang, "get_premium"), callback_data="premium_info")]]
        await context.bot.send_message(
            chat_id=chat_id,
            text=t(lang, "daily_limit"),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    email, password = create_email()
    if not email:
        await context.bot.send_message(chat_id=chat_id, text=t(lang, "gen_failed"))
        return

    mail_token = get_mail_token(email, password)
    if not mail_token:
        await context.bot.send_message(chat_id=chat_id, text=t(lang, "gen_failed"))
        return

    increment_daily_count(context, chat_id)
    seen_ids = set()
    lifetime = get_email_lifetime(context, chat_id)
    expires_at = datetime.now() + timedelta(minutes=lifetime)

    context.bot_data[f"email_{chat_id}"] = email
    context.bot_data[f"token_{chat_id}"] = mail_token
    context.bot_data[f"seen_{chat_id}"] = seen_ids
    context.bot_data[f"expires_{chat_id}"] = expires_at
    db_set_lang(chat_id, lang)

    setup_jobs(context, chat_id, mail_token, seen_ids, lifetime)

    prem = is_premium(context, chat_id)
    text, keyboard = build_email_message(lang, email, expires_at, prem)
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode="MarkdownV2"
    )

    # Send sponsor message to free users only
    if not prem:
        await send_sponsor(context, chat_id)


async def add_sponsor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /addsponsor Name | Ad text here | https://link.com | Button Text"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied.")
        return
    try:
        args = " ".join(context.args)
        parts = [p.strip() for p in args.split("|")]
        if len(parts) != 4:
            await update.message.reply_text(
                "Usage: /addsponsor Name | Ad text | https://url.com | Button Text"
            )
            return
        name, text, url, button = parts
        db_add_sponsor(name, text, url, button)
        sponsors = db_get_sponsors()
        await update.message.reply_text(
            "✅ Sponsor added!\n\nName: " + name + "\nSponsors total: " + str(len(sponsors))
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def list_sponsors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all sponsors: /listsponsors"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied.")
        return
    sponsors = db_get_sponsors()
    if not sponsors:
        await update.message.reply_text("No sponsors yet.")
        return
    lines = ["📋 Sponsors:\n"]
    for i, s in enumerate(sponsors):
        lines.append(str(i+1) + ". " + s["name"] + " — " + s["url"])
    await update.message.reply_text("\n".join(lines))


async def remove_sponsor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove sponsor by number: /removesponsor 1"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied.")
        return
    try:
        index = int(context.args[0]) - 1
        removed = db_remove_sponsor(index)
        if removed:
            await update.message.reply_text("✅ Removed: " + removed)
        else:
            await update.message.reply_text("❌ Invalid number.")
    except Exception as e:
        await update.message.reply_text("❌ Error: " + str(e) + "\nUsage: /removesponsor 1")


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    row = db_get_user(chat_id)
    lang = row[0] if row else "en"

    if query.data.startswith("lang_"):
        selected_lang = query.data.split("_")[1]
        db_set_lang(chat_id, selected_lang)
        keyboard = [[InlineKeyboardButton(t(selected_lang, "get_started"), callback_data="get_started")]]
        await query.edit_message_text(
            f"{t(selected_lang, 'welcome_title')}\n\n{t(selected_lang, 'welcome_body')}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "get_started":
        lang = context.bot_data.get(f"lang_{chat_id}", "en")
        try:
            await query.edit_message_text(t(lang, "generating"))
        except Exception:
            pass
        await do_generate(context, chat_id, lang)

    elif query.data == "new_email":
        try:
            await query.edit_message_text(t(lang, "generating"))
        except Exception:
            pass
        await do_generate(context, chat_id, lang)

    elif query.data == "premium_info":
        keyboard = [
            [InlineKeyboardButton(t(lang, "buy_premium"), callback_data="buy_premium")],
            [InlineKeyboardButton(t(lang, "back"), callback_data="new_email")],
        ]
        try:
            await query.edit_message_text(
                t(lang, "premium_info"),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            print(f"Premium info error: {e}")

    elif query.data == "buy_premium":
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=t(lang, "invoice_title"),
            description=t(lang, "invoice_desc"),
            payload=f"premium_{chat_id}",
            currency="XTR",  # Telegram Stars
            prices=[LabeledPrice(label="Premium 7 days", amount=PREMIUM_STARS_PRICE)],
        )

    elif query.data == "my_plan":
        prem = is_premium(context, chat_id)
        if prem:
            expiry = db_get_premium_expiry(chat_id)
            date_str = expiry.strftime("%d.%m.%Y")
            text = t(lang, "premium_active", date=date_str)
        else:
            text = t(lang, "free_plan")
        keyboard = [
            [InlineKeyboardButton(t(lang, "get_premium"), callback_data="premium_info")],
            [InlineKeyboardButton(t(lang, "back"), callback_data="new_email")],
        ]
        try:
            await query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            print(f"My plan error: {e}")

    elif query.data == "change_lang":
        keyboard = [
            [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
            [InlineKeyboardButton("🇹🇷 Türkçe", callback_data="lang_tr")],
        ]
        try:
            await query.edit_message_text(
                t(lang, "choose_lang"),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    row = db_get_user(chat_id)
    lang = row[0] if row else "en"
    activate_premium(context, chat_id)
    await update.message.reply_text(t(lang, "payment_success"))
    await do_generate(context, chat_id, lang)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied.")
        return

    total_users, premium_users, total_sponsors = db_get_stats()
    free_users = total_users - premium_users

    text = (
        "📊 Admin Panel\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "👥 Users\n"
        "  Total:    " + str(total_users) + "\n"
        "  Free:     " + str(free_users) + "\n"
        "  Premium:  " + str(premium_users) + "\n\n"
        "📢 Sponsors\n"
        "  Active:   " + str(total_sponsors) + "\n\n"
        "💰 Revenue (est.)\n"
        "  Premium sales: " + str(premium_users) + " x 50 Stars\n\n"
        "🤖 Bot is running normally."
    )
    await update.message.reply_text(text)


def main():
    db_init()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("addsponsor", add_sponsor))
    app.add_handler(CommandHandler("listsponsors", list_sponsors))
    app.add_handler(CommandHandler("removesponsor", remove_sponsor))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    print("Bot is running!")
    app.run_polling()


if __name__ == "__main__":
    main()
