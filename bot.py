# bot.py

import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from twilio.rest import Client
import os
import threading
from flask import Flask
import re
import json
from functools import wraps

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ফোর্স সাবস্ক্রাইব ফিচার এর জন্য কনস্ট্যান্ট ---
# আপনার দেওয়া তথ্য অনুযায়ী চ্যানেল আইডি ও নতুন লিংক এখানে বসানো হয়েছে।
FORCE_SUB_CHANNEL_ID = -1002085020447
FORCE_SUB_CHANNEL_LINK = "https://t.me/+-HQpmwwkFaRhNmI1" # আপনার নতুন চ্যানেল লিংক


# --- Globals & Persistence ---
user_sessions = {} 
SESSIONS_FILE = 'sessions.json'

def save_sessions():
    with open(SESSIONS_FILE, 'w') as f:
        sessions_to_save = {}
        for uid, data in user_sessions.items():
            sessions_to_save[uid] = {k: v for k, v in data.items() if k != 'client'}
        json.dump(sessions_to_save, f, indent=4)
    logger.info("User sessions saved to file.")

def load_sessions():
    global user_sessions
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, 'r') as f:
                user_sessions = json.load(f)
            logger.info(f"Successfully loaded {len(user_sessions)} user sessions from {SESSIONS_FILE}.")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Could not load sessions from {SESSIONS_FILE}: {e}")
            user_sessions = {}
    else:
        logger.info(f"{SESSIONS_FILE} not found. Starting with empty sessions.")
        user_sessions = {}

def get_twilio_client(user_id: int) -> Client | None:
    if user_id not in user_sessions: return None
    if 'client' in user_sessions[user_id] and isinstance(user_sessions[user_id]['client'], Client):
        return user_sessions[user_id]['client']
    sid, auth = user_sessions[user_id].get('sid'), user_sessions[user_id].get('auth')
    if sid and auth:
        try:
            client = Client(sid, auth)
            client.api.accounts(sid).fetch()
            user_sessions[user_id]['client'] = client
            return client
        except Exception as e:
            logger.error(f"Failed to create Twilio client for user {user_id} on demand: {e}")
            return None
    return None

def format_codes_in_message(body: str) -> str:
    if not body: return ""
    patterns = [r'\b(G-\d{6})\b', r'\b([A-Z0-9]{7,8})\b', r'\b([A-Z0-9]{6})\b', r'\b(\d{7,8})\b', r'\b(\d{6})\b', r'\b(\d{4,5})\b']
    all_matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            pre_char = body[match.start()-1:match.start()] if match.start() > 0 else ""
            post_char = body[match.end():match.end()+1] if match.end() < len(body) else ""
            if pre_char != '`' and post_char != '`':
                all_matches.append({'start': match.start(), 'end': match.end(), 'text': match.group(0)})
    if not all_matches: return body
    all_matches.sort(key=lambda m: (m['start'], -(m['end'] - m['start'])))
    unique_matches = []
    last_processed_end = -1
    for match in all_matches:
        if match['start'] >= last_processed_end:
            unique_matches.append(match)
            last_processed_end = match['end']
    result_parts = []
    current_pos = 0
    for match in unique_matches:
        if match['start'] > current_pos: result_parts.append(body[current_pos:match['start']])
        result_parts.append(f"`{match['text']}`")
        current_pos = match['end']
    if current_pos < len(body): result_parts.append(body[current_pos:])
    return "".join(result_parts)

async def display_numbers_with_buy_buttons(message_object, context: ContextTypes.DEFAULT_TYPE, available_numbers, intro_text: str):
    if not available_numbers:
        await message_object.reply_text(f"😔 {intro_text} এই মুহূর্তে কোনো উপলভ্য নম্বর নেই।")
        return None
    message_parts = [f"📞 {intro_text} উপলব্ধ নম্বর নিচে দেওয়া হলো:\n"]
    keyboard_buttons = []
    for number_obj in available_numbers:
        copyable_number_text = f"`{number_obj.phone_number}`"
        message_parts.append(copyable_number_text)
        button_text = f"🛒 কিনুন {number_obj.phone_number}"
        callback_data = f"purchase_{number_obj.phone_number}"
        keyboard_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    if not keyboard_buttons:
        await message_object.reply_text("😔 নম্বর পাওয়া গেলেও বাটন তৈরি করা যায়নি।")
        return None
    full_message_text = "\n".join(message_parts)
    inline_reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    await message_object.reply_text(full_message_text, reply_markup=inline_reply_markup, parse_mode='Markdown')

# --- ফোর্স সাবস্ক্রাইব চেকের জন্য ডেкоರೇটর ---
def force_subscribe_check(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        try:
            member = await context.bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL_ID, user_id=user_id)
            if member.status.lower() in ['member', 'administrator', 'creator']:
                return await func(update, context, *args, **kwargs)
            else:
                raise Exception("User is not a member")
        except Exception:
            channel_name_display = ""
            button_channel_text = "আমাদের চ্যানেলে"
            try:
                chat = await context.bot.get_chat(chat_id=FORCE_SUB_CHANNEL_ID)
                channel_name_display = f"**{chat.title}**"
                button_channel_text = f"{chat.title}"
            except Exception as e:
                logger.warning(f"Could not fetch channel title: {e}. Using default text.")
                channel_name_display = "এই চ্যানেল"

            join_text = (
                f"👋 **স্বাগতম!**\n\n"
                f"আমাদের **Twilio Boss Bot** টি সম্পূর্ণ বিনামূল্যে ব্যবহার করার আগে, আপনাকে ছোট্ট একটি কাজ করতে হবে।\n\n"
                f"✅ অনুগ্রহ করে {channel_name_display} -এ যোগ দিন।\n\n"
                f"সেখানে আপনি নানা রকম প্রিমিয়াম মেথড বিনামূল্যে পাবেন এবং বট সংক্রান্ত সব ধরনের সাপোর্ট ও আপডেট সবার আগে পেয়ে যাবেন।"
            )
            keyboard = [[InlineKeyboardButton(f"✅ {button_channel_text} এ যোগ দিন", url=FORCE_SUB_CHANNEL_LINK)]]
            
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(join_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            else:
                await update.message.reply_text(join_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

            return ConversationHandler.END

    return wrapper

# States for ConversationHandlers, Menu Texts, Callback Data, etc.
AWAITING_CREDENTIALS, AWAITING_CA_AREA_CODE = 0, 1
START_COMMAND_TEXT, LOGIN_TEXT, BUY_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT, LOGOUT_TEXT, SUPPORT_TEXT = '🏠 /start', '🔑 Login', '🛒 Buy Number', '✉️ Show Messages', '🗑️ Remove Number', '↪️ Logout', '💬 Support'
PURCHASE_CALLBACK_PREFIX, CONFIRM_REMOVE_YES_CALLBACK, CONFIRM_REMOVE_NO_CALLBACK, DIRECT_REMOVE_AFTER_SHOW_MSG_CALLBACK = 'purchase_', 'confirm_remove_yes', 'confirm_remove_no', 'direct_remove_this_number'
menu_keyboard = [[START_COMMAND_TEXT, LOGIN_TEXT], [BUY_TEXT, SHOW_MESSAGES_TEXT], [REMOVE_NUMBER_TEXT, LOGOUT_TEXT], [SUPPORT_TEXT]]
reply_markup = ReplyKeyboardMarkup(menu_keyboard, resize_keyboard=True)

# Flask App
flask_app = Flask(__name__)
@flask_app.route('/')
def keep_alive_route(): return 'Bot is alive!'
def run_flask(): flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# Helper function to release number
async def _release_twilio_number(user_id: int, client: Client, number_to_release: str) -> tuple[bool, str]:
    try:
        incoming_phone_numbers = client.incoming_phone_numbers.list(phone_number=number_to_release, limit=1)
        if not incoming_phone_numbers:
            return False, f"❓ নম্বর `{number_to_release}` আপনার অ্যাকাউন্টে পাওয়া যায়নি।"
        client.incoming_phone_numbers(incoming_phone_numbers[0].sid).delete()
        if user_id in user_sessions:
            user_sessions[user_id]['number'] = None
            save_sessions()
        return True, f"🗑️ নম্বর `{number_to_release}` সফলভাবে রিমুভ করা হয়েছে!"
    except Exception as e:
        logger.error(f"Failed during release: {e}")
        return False, f"⚠️ নম্বর `{number_to_release}` রিমুভ করতে সমস্যা হয়েছে।"

# --- Telegram Bot Handlers (with decorator) ---
@force_subscribe_check
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"👋 স্বাগতম! মেনু থেকে একটি অপশন বেছে নিন।", reply_markup=reply_markup)

@force_subscribe_check
async def login_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id in user_sessions:
        await update.message.reply_text("✅ আপনি ইতিমধ্যেই লগইন করা আছেন।", reply_markup=reply_markup)
        return ConversationHandler.END
    await update.message.reply_text("📝 আপনার Twilio Account SID এবং Auth Token দিন:")
    return AWAITING_CREDENTIALS

async def receive_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    try:
        sid, auth = update.message.text.strip().split(maxsplit=1)
        if not (sid.startswith("AC") and len(sid) == 34):
            await update.message.reply_text(f"⚠️ SID সঠিক ফরম্যাটে নেই।", parse_mode='Markdown')
            return ConversationHandler.END
        client = Client(sid, auth)
        client.api.accounts(sid).fetch()
        user_sessions[user_id] = {'sid': sid, 'auth': auth, 'number': None}
        save_sessions()
        await update.message.reply_text("🎉 লগইন সফল হয়েছে!", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Login failed for {user_id}: {e}")
        await update.message.reply_text(f"❌ লগইন ব্যর্থ হয়েছে।")
    return ConversationHandler.END

async def logout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        save_sessions()
        await update.message.reply_text("✅ আপনি সফলভাবে লগ আউট হয়েছেন।")
    else:
        await update.message.reply_text("ℹ️ আপনি লগইন অবস্থায় নেই।")

@force_subscribe_check
async def ask_for_ca_area_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if get_twilio_client(user_id) is None:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' চাপুন।")
        return ConversationHandler.END
    if user_sessions[user_id].get('number'):
        await update.message.reply_text(f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর কেনা আছে।")
        return ConversationHandler.END
    await update.message.reply_text("📝 কানাডার ৩ সংখ্যার এরিয়া কোড দিন (e.g., 416)।\n\n/cancel দিয়ে বাতিল করুন।")
    return AWAITING_CA_AREA_CODE

async def list_numbers_by_ca_area_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    area_code = update.message.text.strip()
    if not area_code.isdigit() or len(area_code) != 3:
        await update.message.reply_text("⚠️ সঠিক ৩ সংখ্যার এরিয়া কোড দিন।")
        return AWAITING_CA_AREA_CODE
    client = get_twilio_client(user_id)
    if not client: return ConversationHandler.END
    try:
        await update.message.reply_text(f"🔎 `{area_code}` এ নম্বর খোঁজা হচ্ছে...", parse_mode='Markdown')
        numbers = client.available_phone_numbers("CA").local.list(area_code=area_code, limit=10)
        await display_numbers_with_buy_buttons(update.message, context, numbers, f"`{area_code}` এরিয়া কোডে")
    except Exception as e:
        logger.error(f"Fetch numbers failed: {e}")
        await update.message.reply_text("⚠️ নম্বর আনতে সমস্যা হয়েছে।")
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text('ℹ️ প্রক্রিয়া বাতিল করা হয়েছে।', reply_markup=reply_markup)
    return ConversationHandler.END

@force_subscribe_check
async def purchase_number_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    client = get_twilio_client(user_id)
    if not client: return
    if user_sessions[user_id].get('number'):
        await context.bot.send_message(chat_id=user_id, text=f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর কেনা আছে।")
        return
    try:
        number_to_buy = query.data.replace('purchase_', '')
        if not number_to_buy.startswith('+'): raise ValueError("Invalid format")
    except (ValueError, IndexError):
        await context.bot.send_message(chat_id=user_id, text="⚠️ অনুরোধে ত্রুটি হয়েছে।")
        return
    msg = await context.bot.send_message(chat_id=user_id, text=f"⏳ `{number_to_buy}` কেনা হচ্ছে...", parse_mode='Markdown')
    try:
        number = client.incoming_phone_numbers.create(phone_number=number_to_buy)
        user_sessions[user_id]['number'] = number.phone_number
        save_sessions()
        await msg.edit_text(f"🛍️ নম্বর `{number.phone_number}` সফলভাবে কেনা হয়েছে!", parse_mode='Markdown')
    except Exception as e:
        await msg.delete()
        logger.error(f"Buy failed for {user_id}: {e}")
        error = f"❌ এই নম্বরটি (`{number_to_buy}`) কিনতে সমস্যা হয়েছে।"
        if "already provisioned" in str(e).lower(): error += " এটি আপনার অ্যাকাউন্টে আছে।"
        elif "not available" in str(e).lower(): error += " এটি আর উপলব্ধ নেই।"
        elif "balance" in str(e).lower(): error += " আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স নেই।"
        await context.bot.send_message(chat_id=user_id, text=error, parse_mode='Markdown')

@force_subscribe_check
async def show_messages_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = get_twilio_client(user_id)
    if not client:
        await update.message.reply_text(f"🔒 '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    number = user_sessions[user_id].get('number')
    if not number:
        await update.message.reply_text(f"ℹ️ আপনার কোনো কেনা নম্বর নেই।")
        return
    try:
        msg = await update.message.reply_text(f"📨 `{number}` এর মেসেজ খোঁজা হচ্ছে...", parse_mode='Markdown')
        messages = client.messages.list(to=number, limit=5)
        await msg.delete()
        keyboard = [[InlineKeyboardButton("🗑️ এই নম্বরটা রিমুভ করুন", callback_data=DIRECT_REMOVE_AFTER_SHOW_MSG_CALLBACK)]]
        markup = InlineKeyboardMarkup(keyboard)
        if not messages:
            await update.message.reply_text("📪 এই নম্বরে কোনো নতুন মেসেজ নেই।", reply_markup=markup)
        else:
            parts = [f"📨 আপনার নম্বর (`{number}`) এ আসা মেসেজ:\n"]
            for m in messages:
                body = format_codes_in_message(m.body or "")
                parts.append(f"\n➡️ **From:** `{m.from_}`\n📝 **Msg:** {body}\n---")
            await update.message.reply_text("".join(parts), reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Show messages failed: {e}")
        await update.message.reply_text("⚠️ মেসেজ আনতে সমস্যা হয়েছে।")

@force_subscribe_check
async def direct_remove_after_show_msg_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    client = get_twilio_client(user_id)
    if not client or not user_sessions[user_id].get('number'):
        await query.edit_message_text(text="🚫 কোনো সক্রিয় নম্বর নেই।")
        return
    number = user_sessions[user_id]['number']
    await query.edit_message_text(f"⏳ `{number}` রিমুভ করা হচ্ছে...", parse_mode='Markdown')
    success, message = await _release_twilio_number(user_id, client, number)
    await query.edit_message_text(message, parse_mode='Markdown')

@force_subscribe_check
async def remove_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if get_twilio_client(user_id) is None:
        await update.message.reply_text(f"🔒 '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    number = user_sessions[user_id].get('number')
    if not number:
        await update.message.reply_text("ℹ️ আপনার রিমুভ করার মতো কোনো নম্বর নেই।")
        return
    keyboard = [[InlineKeyboardButton("✅ হ্যাঁ", callback_data=CONFIRM_REMOVE_YES_CALLBACK), InlineKeyboardButton("❌ না", callback_data=CONFIRM_REMOVE_NO_CALLBACK)]]
    await update.message.reply_text(f"ℹ️ আপনার নম্বর: `{number}`। আপনি কি রিমুভ করতে চান?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

@force_subscribe_check
async def confirm_remove_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if query.data == CONFIRM_REMOVE_NO_CALLBACK:
        await query.edit_message_text("🚫 রিমুভ বাতিল করা হয়েছে।")
        return
    client = get_twilio_client(user_id)
    if not client or not user_sessions[user_id].get('number'):
        await query.edit_message_text("🚫 অনুরোধটি আর বৈধ নয়।")
        return
    number = user_sessions[user_id]['number']
    await query.edit_message_text(f"⏳ `{number}` রিমুভ করা হচ্ছে...", parse_mode='Markdown')
    success, message = await _release_twilio_number(user_id, client, number)
    await query.edit_message_text(message, parse_mode='Markdown')

async def handle_general_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text not in [START_COMMAND_TEXT, LOGIN_TEXT, BUY_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT, LOGOUT_TEXT, SUPPORT_TEXT] and not text.startswith('/'):
        await update.message.reply_text("🤔 আপনার অনুরোধ বুঝতে পারিনি।", reply_markup=reply_markup)

@force_subscribe_check
async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(f"💬 অ্যাডমিনের সাথে যোগাযোগ", url=f"https://t.me/MrGhosh75")]]
    await update.message.reply_text("সাপোর্টের জন্য, অ্যাডমিনের সাথে যোগাযোগ করুন:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- Main block to run the bot ---
if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if TOKEN is None:
        logger.critical("FATAL: TELEGRAM_BOT_TOKEN not set!")
        exit()
    
    load_sessions()
    
    app = Application.builder().token(TOKEN).build()

    login_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex(f'^{LOGIN_TEXT}$'), login_command_handler)], states={AWAITING_CREDENTIALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_credentials)]}, fallbacks=[CommandHandler('cancel', cancel_conversation)])
    buy_conv = ConversationHandler(entry_points=[MessageHandler(filters.Regex(f'^{BUY_TEXT}$'), ask_for_ca_area_code)], states={AWAITING_CA_AREA_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, list_numbers_by_ca_area_code)]}, fallbacks=[CommandHandler('cancel', cancel_conversation)])

    app.add_handler(login_conv)
    app.add_handler(buy_conv)
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(f'^{START_COMMAND_TEXT}$'), start))
    app.add_handler(MessageHandler(filters.Regex(f'^{LOGOUT_TEXT}$'), logout_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{REMOVE_NUMBER_TEXT}$'), remove_number_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{SHOW_MESSAGES_TEXT}$'), show_messages_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{SUPPORT_TEXT}$'), support_handler))
    
    app.add_handler(CallbackQueryHandler(purchase_number_callback_handler, pattern=f'^{PURCHASE_CALLBACK_PREFIX}'))
    app.add_handler(CallbackQueryHandler(confirm_remove_callback_handler, pattern=f'^{CONFIRM_REMOVE_YES_CALLBACK}$|^{CONFIRM_REMOVE_NO_CALLBACK}$'))
    app.add_handler(CallbackQueryHandler(direct_remove_after_show_msg_callback, pattern=f'^{DIRECT_REMOVE_AFTER_SHOW_MSG_CALLBACK}$'))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_text))

    # Run Flask in a separate thread
    threading.Thread(target=run_flask, daemon=True).start()

    logger.info("🤖 Bot is starting...")
    app.run_polling()
