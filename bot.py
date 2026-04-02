import logging, json, time, requests, os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    CallbackQueryHandler, MessageHandler,
    filters, ContextTypes
)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
AUDIERA_KEY    = os.environ["AUDIERA_API_KEY"]
OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]

STYLES = [
    "Hip-Hop", "Pop", "R&B", "Electronic", "Afrobeat",
    "Reggae", "Soul", "Rock", "Latin", "Jazz"
]

MOODS = [
    "Heartbreak", "Euphoric", "Angry", "Nostalgic", "Motivated",
    "Dark", "Romantic", "Melancholic", "Hopeful", "Rebellious",
    "Lonely", "Triumphant", "Anxious", "Carefree", "Spiritual"
]

ARTISTS = {
    "Hip-Hop":    ("Ray",  "jyjcnj6t3arzzb5dnzk4p"),
    "Pop":        ("Kira", "osipvytdvxuzci9pn2nz1"),
    "R&B":        ("Kira", "osipvytdvxuzci9pn2nz1"),
    "Electronic": ("Ray",  "jyjcnj6t3arzzb5dnzk4p"),
    "Afrobeat":   ("Ray",  "jyjcnj6t3arzzb5dnzk4p"),
    "Reggae":     ("Ray",  "jyjcnj6t3arzzb5dnzk4p"),
    "Soul":       ("Kira", "osipvytdvxuzci9pn2nz1"),
    "Rock":       ("Ray",  "jyjcnj6t3arzzb5dnzk4p"),
    "Latin":      ("Kira", "osipvytdvxuzci9pn2nz1"),
    "Jazz":       ("Kira", "osipvytdvxuzci9pn2nz1"),
}

WALLET = "0x88c773d7c09d7889bd5e47dc588a390fa05989da"

logging.basicConfig(level=logging.INFO)

user_themes  = {}
user_styles  = {}
user_moods   = {}
user_lyrics  = {}
user_waiting = {}

def get_credits():
    try:
        r = requests.get(
            "https://ai.audiera.fi/api/user/credits",
            headers={"Authorization": f"Bearer {AUDIERA_KEY}"},
            timeout=10
        )
        return r.json().get("credits", 0)
    except Exception:
        return None

def generate_lyrics(inspiration):
    """Use official Audiera lyrics skill API."""
    try:
        r = requests.post(
            "https://ai.audiera.fi/api/skills/lyrics",
            headers={
                "Authorization": f"Bearer {AUDIERA_KEY}",
                "Content-Type": "application/json"
            },
            json={"inspiration": inspiration},
            timeout=60
        ).json()
        if r.get("success") and r.get("data", {}).get("lyrics"):
            return r["data"]["lyrics"], None
        return None, r.get("message", "Lyrics generation failed.")
    except Exception as e:
        return None, str(e)

def ask_openrouter(prompt):
    """Use OpenRouter for scout report, pitch and theme suggestions."""
    models = [
        "openrouter/free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "meta-llama/llama-4-maverick:free"
    ]
    for model in models:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=60
            )
            data = r.json()
            if "choices" in data and len(data["choices"]) > 0:
                return data["choices"][0]["message"]["content"]
        except Exception:
            continue
    raise Exception("All OpenRouter models failed. Try again later.")

def submit_to_audiera(lyrics, style, theme, mood):
    artist_name, artist_id = ARTISTS[style]
    try:
        resp = requests.post(
            "https://ai.audiera.fi/api/skills/music",
            headers={
                "Authorization": f"Bearer {AUDIERA_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "styles":      [style],
                "artistId":    artist_id,
                "inspiration": f"{theme} — {mood} mood",
                "lyrics":      lyrics
            },
            timeout=30
        ).json()
    except Exception as e:
        return None, artist_name, f"Audiera submission failed: {e}"

    if not resp.get("success"):
        return None, artist_name, resp.get("message", "Audiera returned an error.")

    task_id = (resp.get("data") or {}).get("taskId")
    if not task_id:
        return None, artist_name, f"No taskId returned. Response: {resp}"

    return task_id, artist_name, None

def poll_audiera(task_id):
    for _ in range(120):
        time.sleep(5)
        try:
            resp = requests.get(
                f"https://ai.audiera.fi/api/skills/music/{task_id}",
                headers={"Authorization": f"Bearer {AUDIERA_KEY}"},
                timeout=10
            ).json()
            d = resp.get("data") or {}
            if d.get("status") == "completed":
                musics = d.get("musics") or []
                if musics and musics[0].get("url"):
                    return musics[0]["url"]
                music = d.get("music") or {}
                if music and music.get("url"):
                    return music["url"]
                return f"https://ai.audiera.fi/music/{task_id}"
        except Exception:
            continue
    return None

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎙️ *Boom Master* — AI Music A&R Agent\n\n"
        "I find stories worth singing, commission real songs on Audiera,\n"
        "and pitch them to the world.\n\n"
        "*/create* — Start making a song\n"
        "*/credits* — Check your Audiera balance",
        parse_mode="Markdown"
    )

async def credits_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    c = get_credits()
    if c is None:
        await update.message.reply_text(
            "⚠️ Could not fetch credits. Check your Audiera API key."
        )
        return
    songs = (c // 2) * 2
    status = "✅ Ready to generate" if c >= 2 else "❌ Not enough credits"
    await update.message.reply_text(
        f"💳 *Credit Check*\n\n"
        f"Available credits: *{c}*\n"
        f"Songs you can generate: *{songs}*\n"
        f"Status: {status}\n\n"
        f"_Each generation costs 2 credits and produces 2 songs._\n"
        f"{'Top up at: https://ai.audiera.fi' if c < 2 else ''}",
        parse_mode="Markdown"
    )

async def create(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    theme = " ".join(ctx.args).strip()
    if theme:
        user_themes[update.effective_user.id] = theme
        kb = [[InlineKeyboardButton(s, callback_data=f"style:{s}")] for s in STYLES]
        await update.message.reply_text(
            f"🎯 Theme locked: *{theme}*\n\nPick a style:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text("💭 Finding stories worth singing about...")
    prompt = (
        "Generate 6 short emotional song themes. "
        "Each must be under 40 characters. "
        "They should feel cinematic and human — real moments people live through. "
        "Mix different emotions: heartbreak, joy, longing, triumph, loss, love. "
        "No technology, no blockchain, no AI references. Pure human emotion. "
        "Examples: She left on a Sunday, Dancing in the rain alone. "
        "Return ONLY a JSON array of 6 strings. No extra text, no markdown fences."
    )
    try:
        raw = ask_openrouter(prompt).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        themes = json.loads(raw)
        if not isinstance(themes, list) or len(themes) == 0:
            raise ValueError("Empty")
        themes = [t[:40] for t in themes]
    except Exception:
        themes = [
            "She left on a Sunday",
            "Dancing in the rain alone",
            "First love forgotten",
            "Phone that never rings",
            "Rising after the fall",
            "The last goodbye"
        ]

    kb = [[InlineKeyboardButton(t, callback_data=f"theme:{t}")] for t in themes]
    kb.append([InlineKeyboardButton("✏️ Write my own theme", callback_data="theme:__custom__")])
    await update.message.reply_text(
        "🎯 Pick a story to turn into a song:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

async def theme_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    theme = query.data[len("theme:"):]
    uid   = query.from_user.id

    if theme == "__custom__":
        await query.edit_message_text(
            "✏️ Type your theme:\n`/create your theme here`",
            parse_mode="Markdown"
        )
        return

    user_themes[uid] = theme
    kb = [[InlineKeyboardButton(s, callback_data=f"style:{s}")] for s in STYLES]
    await query.edit_message_text(
        f"🎯 Theme: *{theme}*\n\nPick a style:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

async def style_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    style = query.data[len("style:"):]
    uid   = query.from_user.id
    user_styles[uid] = style

    kb = [[InlineKeyboardButton(m, callback_data=f"mood:{m}")] for m in MOODS]
    await query.edit_message_text(
        f"🎨 Style: *{style}*\n\nPick a mood:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

async def mood_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mood  = query.data[len("mood:"):]
    uid   = query.from_user.id
    style = user_styles.get(uid, "Pop")
    theme = user_themes.get(uid, "a story")

    c = get_credits()
    if c is not None and c < 2:
        await query.edit_message_text(
            "❌ *Not enough Audiera credits.*\n\n"
            "You need at least 2 credits per generation.\n"
            "Top up at: https://ai.audiera.fi",
            parse_mode="Markdown"
        )
        return

    user_moods[uid] = mood
    artist_name, _ = ARTISTS[style]

    await query.edit_message_text(
        f"🔍 Scouting *{theme}*\n"
        f"Style: *{style}* · Mood: *{mood}* · Artist: *{artist_name}*\n\n"
        f"✍️ Generating lyrics via Audiera...",
        parse_mode="Markdown"
    )

    # Use official Audiera lyrics skill
    inspiration = f"{theme} — {style} style, {mood} mood"
    lyrics, err = generate_lyrics(inspiration)

    if err or not lyrics:
        await ctx.bot.send_message(uid, f"⚠️ Lyrics generation failed: {err}")
        return

    # Generate scout and pitch via OpenRouter
    prompt = (
        f"You are Boom Master, an AI music A&R agent.\n\n"
        f"Theme: {theme}\nStyle: {style}\nMood: {mood}\n\n"
        f"Do the following:\n"
        f"1. SCOUT: 2 sentences on why this will emotionally connect with listeners.\n"
        f"2. PITCH: 3-sentence A&R pitch — hook, audience, why it gets plays.\n\n"
        f"Use exactly these headers:\n## SCOUT\n## PITCH"
    )

    try:
        full_text = ask_openrouter(prompt)
        scout = ""
        pitch = ""
        if "## SCOUT" in full_text:
            start = full_text.index("## SCOUT") + len("## SCOUT")
            end = full_text.index("## PITCH") if "## PITCH" in full_text else len(full_text)
            scout = full_text[start:end].strip()
        if "## PITCH" in full_text:
            start = full_text.index("## PITCH") + len("## PITCH")
            pitch = full_text[start:].strip()
    except Exception:
        scout = ""
        pitch = ""

    user_lyrics[uid] = {"lyrics": lyrics, "pitch": pitch, "scout": scout}

    kb = [
        [InlineKeyboardButton("✅ Use these lyrics", callback_data="lyrics:use")],
        [InlineKeyboardButton("✏️ Edit lyrics",      callback_data="lyrics:edit")]
    ]

    await ctx.bot.send_message(
        uid,
        f"📡 *Scout Report*\n{scout}\n\n"
        f"🎵 *Lyrics by Audiera*\n\n{lyrics}\n\n"
        f"Happy with these lyrics?",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="Markdown"
    )

async def lyrics_decision(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid    = query.from_user.id
    action = query.data[len("lyrics:"):]

    if action == "edit":
        user_waiting[uid] = True
        await query.edit_message_text(
            "✏️ Send me your edited lyrics now as a message.",
            parse_mode="Markdown"
        )
        return

    await query.edit_message_text("⏳ Sending to Audiera... producing your track.")
    await send_to_audiera(uid, ctx)

async def receive_edited_lyrics(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not user_waiting.get(uid):
        return
    user_waiting[uid] = False
    edited = update.message.text.strip()
    if uid in user_lyrics:
        user_lyrics[uid]["lyrics"] = edited
    await update.message.reply_text("✅ Lyrics saved! Sending to Audiera now...")
    await send_to_audiera(uid, ctx)

async def send_to_audiera(uid, ctx):
    style  = user_styles.get(uid, "Pop")
    theme  = user_themes.get(uid, "a story")
    mood   = user_moods.get(uid,  "Heartbreak")
    data   = user_lyrics.get(uid, {})
    lyrics = data.get("lyrics", "")
    pitch  = data.get("pitch",  "")

    task_id, artist_name, err = submit_to_audiera(lyrics, style, theme, mood)
    if err:
        await ctx.bot.send_message(uid, f"⚠️ {err}")
        return

    await ctx.bot.send_message(uid, "⏳ Producing your track... (up to 10 min)")

    song_url = poll_audiera(task_id)

    if not song_url:
        await ctx.bot.send_message(
            uid,
            f"⚠️ Song is taking longer than expected.\n"
            f"Check manually: https://ai.audiera.fi/music/{task_id}"
        )
        return

    remaining = get_credits()
    credit_line = (
        f"\n💳 Credits remaining: *{remaining}* ({(remaining // 2) * 2} songs left)"
        if remaining is not None else ""
    )

    await ctx.bot.send_message(
        uid,
        f"🎵 *Your track is live!*\n\n"
        f"🎧 {song_url}\n\n"
        f"🏷️ *Style:* {style} · *Mood:* {mood} · *Artist:* {artist_name}\n\n"
        f"📋 *A&R Pitch*\n{pitch}\n\n"
        f"💰 Earning $BEAT → `{WALLET}`"
        f"{credit_line}\n\n"
        f"Type /create to make another song.",
        parse_mode="Markdown"
    )

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start",   start))
app.add_handler(CommandHandler("credits", credits_cmd))
app.add_handler(CommandHandler("create",  create))
app.add_handler(CallbackQueryHandler(theme_chosen,    pattern="^theme:"))
app.add_handler(CallbackQueryHandler(style_chosen,    pattern="^style:"))
app.add_handler(CallbackQueryHandler(mood_chosen,     pattern="^mood:"))
app.add_handler(CallbackQueryHandler(lyrics_decision, pattern="^lyrics:"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edited_lyrics))
app.run_polling()
