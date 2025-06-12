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

# --- নতুন: ফোর্স সাবস্ক্রাইব ফিচার এর জন্য কনস্ট্যান্ট ---
# আপনার দেওয়া তথ্য অনুযায়ী চ্যানেল আইডি ও লিংক এখানে বসানো হয়েছে।
FORCE_SUB_CHANNEL_ID = -1002085020447  # আপনার চ্যানেল আইডি
FORCE_SUB_CHANNEL_LINK = "https://t.me/+7BaDKDxZc1FjNTll" # আপনার চ্যানেল লিংক


# --- Globals & Persistence ---
user_sessions = {} 
SESSIONS_FILE = 'sessions.json'

def save_sessions():
    """Saves the current user_sessions dictionary to a JSON file."""
    with open(SESSIONS_FILE, 'w') as f:
        sessions_to_save = {}
        for uid, data in user_sessions.items():
            sessions_to_save[uid] = {k: v for k, v in data.items() if k != 'client'}
        json.dump(sessions_to_save, f, indent=4)
    logger.info("User sessions saved to file.")

def load_sessions():
    """Loads user sessions from the JSON file on startup."""
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
    """Gets or creates a Twilio client for a user."""
    if user_id not in user_sessions:
        return None
    if 'client' in user_sessions[user_id] and isinstance(user_sessions[user_id]['client'], Client):
        return user_sessions[user_id]['client']
    sid = user_sessions[user_id].get('sid')
    auth = user_sessions[user_id].get('auth')
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
    patterns = [
        r'\b(G-\d{6})\b', r'\b([A-Z0-9]{7,8})\b', r'\b([A-Z0-9]{6})\b',
        r'\b(\d{7,8})\b', r'\b(\d{6})\b', r'\b(\d{4,5})\b',
    ]
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
    message_parts = [f"📞 {intro_text} উপলব্ধ নম্বর নিচে দেওয়া হলো। নম্বরটি চেপে ধরে কপি করতে পারেন:\n"]
    keyboard_buttons = []
    for number_obj in available_numbers:
        copyable_number_text = f"`{number_obj.phone_number}`"
        message_parts.append(copyable_number_text)
        button_text = f"🛒 কিনুন {number_obj.phone_number}"
        callback_data = f"{PURCHASE_CALLBACK_PREFIX}{number_obj.phone_number}"
        keyboard_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    if not keyboard_buttons:
        await message_object.reply_text("😔 নম্বর পাওয়া গেলেও বাটন তৈরি করা যায়নি।")
        return None
    full_message_text = "\n".join(message_parts)
    inline_reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    sent_message = await message_object.reply_text(full_message_text, reply_markup=inline_reply_markup, parse_mode='Markdown')
    return sent_message

# --- নতুন: ফোর্স সাবস্ক্রাইব চেকের জন্য ডেкоರೇটর ---
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
            join_text = f"🤖 এই বটটি ব্যবহার করার জন্য আপনাকে আমাদের চ্যানেলে যোগ দিতে হবে। অনুগ্রহ করে নিচের বাটনে ক্লিক করে চ্যানেলে যোগ দিন এবং তারপর আবার চেষ্টা করুন।"
            keyboard = [[InlineKeyboardButton("✅ চ্যানেলে যোগ দিন", url=FORCE_SUB_CHANNEL_LINK)]]
            
            # Check if the update is a callback query or a message
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_text(join_text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update.message.reply_text(join_text, reply_markup=InlineKeyboardMarkup(keyboard))

            return ConversationHandler.END

    return wrapper

# States for ConversationHandlers
AWAITING_CREDENTIALS = 0
AWAITING_CA_AREA_CODE = 1

# ---- Menu Texts with Emojis (Standard Font) ----
START_COMMAND_TEXT = '🏠 /start'
LOGIN_TEXT = '🔑 Login'
BUY_TEXT = '🛒 Buy Number'
SHOW_MESSAGES_TEXT = '✉️ Show Messages'
REMOVE_NUMBER_TEXT = '🗑️ Remove Number'
LOGOUT_TEXT = '↪️ Logout'
SUPPORT_TEXT = '💬 Support'

# ---- Callback Data Constants ----
PURCHASE_CALLBACK_PREFIX = 'purchase_'
CONFIRM_REMOVE_YES_CALLBACK = 'confirm_remove_yes'
CONFIRM_REMOVE_NO_CALLBACK = 'confirm_remove_no'
DIRECT_REMOVE_AFTER_SHOW_MSG_CALLBACK = 'direct_remove_this_number'

# Persistent menu
menu_keyboard = [
    [START_COMMAND_TEXT, LOGIN_TEXT],
    [BUY_TEXT, SHOW_MESSAGES_TEXT],
    [REMOVE_NUMBER_TEXT, LOGOUT_TEXT],
    [SUPPORT_TEXT]
]
reply_markup = ReplyKeyboardMarkup(menu_keyboard, resize_keyboard=True, one_time_keyboard=False)

# Flask App for Keep-Alive
flask_app = Flask(__name__)

@flask_app.route('/')
def keep_alive_route():
    return 'Bot is alive and kicking!'

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    flask_app.run(host='0.0.0.0', port=port)

# --- NEW: Refactored helper function for releasing a Twilio number ---
async def _release_twilio_number(user_id: int, client: Client, number_to_release: str) -> tuple[bool, str]:
    try:
        logger.info(f"Attempting to release number {number_to_release} for user {user_id}")
        incoming_phone_numbers = client.incoming_phone_numbers.list(phone_number=number_to_release, limit=1)
        if not incoming_phone_numbers:
            return False, f"❓ নম্বর `{number_to_release}` আপনার অ্যাকাউন্টে পাওয়া যায়নি।"

        number_sid_to_delete = incoming_phone_numbers[0].sid
        client.incoming_phone_numbers(number_sid_to_delete).delete()
        
        if user_id in user_sessions:
            user_sessions[user_id]['number'] = None
            save_sessions()

        return True, f"🗑️ নম্বর `{number_to_release}` সফলভাবে রিমুভ করা হয়েছে!"
    except Exception as e:
        logger.error(f"Failed during _release_twilio_number for user {user_id}, number {number_to_release}: {e}")
        return False, f"⚠️ নম্বর `{number_to_release}` রিমুভ করতে সমস্যা হয়েছে।"


# --- Telegram Bot Handlers ---
@force_subscribe_check
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: return
    await update.message.reply_text(
        f"👋 স্বাগতম! '{LOGIN_TEXT}' বাটন চাপুন অথবা মেনু থেকে অন্য কোনো অপশন বেছে নিন।",
        reply_markup=reply_markup
    )

@force_subscribe_check
async def login_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id in user_sessions:
        await update.message.reply_text("✅ আপনি ইতিমধ্যেই লগইন করা আছেন।", reply_markup=reply_markup)
        return ConversationHandler.END
    await update.message.reply_text("📝 আপনার Twilio Account SID এবং Auth Token দিন (যেমন: ACxxxxxxxxxxxxxx xxxxxxxxxxxxxx):")
    return AWAITING_CREDENTIALS

async def receive_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This handler is part of a conversation, so the check is done at the entry point.
    user_id = update.effective_user.id
    user_input = update.message.text.strip()
    try:
        sid, auth = user_input.split(maxsplit=1)
        if not (sid.startswith("AC") and len(sid) == 34):
            await update.message.reply_text(f"⚠️ আপনার দেওয়া SID (`{sid}`) সঠিক ফরম্যাটে নেই।", parse_mode='Markdown')
            return ConversationHandler.END
        client = Client(sid, auth)
        client.api.accounts(sid).fetch()
        user_sessions[user_id] = {'sid': sid, 'auth': auth, 'number': None}
        save_sessions()
        await update.message.reply_text("🎉 লগইন সফল হয়েছে!", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Login failed for user {user_id}: {e}")
        await update.message.reply_text(f"❌ আপনার দেওয়া SID এবং Auth Token দিয়ে লগইন করতে ব্যর্থ হয়েছে।")
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
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return ConversationHandler.END
    if user_sessions[user_id].get('number'):
        await update.message.reply_text(f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর কেনা আছে।", parse_mode='Markdown')
        return ConversationHandler.END
    await update.message.reply_text("📝 অনুগ্রহ করে কানাডার ৩ সংখ্যার এরিয়া কোড দিন (যেমন: 416)।\n\nপ্রক্রিয়া বাতিল করতে /cancel টাইপ করুন।")
    return AWAITING_CA_AREA_CODE

async def list_numbers_by_ca_area_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    area_code_input = update.message.text.strip()
    if not area_code_input.isdigit() or len(area_code_input) != 3:
        await update.message.reply_text("⚠️ অনুগ্রহ করে সঠিক ৩ সংখ্যার এরিয়া কোড দিন।")
        return AWAITING_CA_AREA_CODE
    client = get_twilio_client(user_id)
    if client is None: return ConversationHandler.END
    try:
        await update.message.reply_text(f"🔎 `{area_code_input}` এরিয়া কোডে নম্বর খোঁজা হচ্ছে...", parse_mode='Markdown')
        available_numbers = client.available_phone_numbers("CA").local.list(area_code=area_code_input, limit=10)
        await display_numbers_with_buy_buttons(update.message, context, available_numbers, f"`{area_code_input}` এরিয়া কোডে")
    except Exception as e:
        logger.error(f"Failed to fetch numbers for user {user_id}: {e}")
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
    if client is None: return
    if user_sessions[user_id].get('number'):
        await context.bot.send_message(chat_id=user_id, text=f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর কেনা আছে।", parse_mode='Markdown')
        return
    try:
        number_to_buy = query.data.replace(PURCHASE_CALLBACK_PREFIX, '')
        if not number_to_buy.startswith('+'): raise ValueError("Invalid number format")
    except (ValueError, IndexError):
        await context.bot.send_message(chat_id=user_id, text="⚠️ নম্বর কেনার অনুরোধে ত্রুটি হয়েছে।")
        return
    processing_msg = await context.bot.send_message(chat_id=user_id, text=f"⏳ `{number_to_buy}` নম্বরটি কেনা হচ্ছে...", parse_mode='Markdown')
    try:
        incoming_number = client.incoming_phone_numbers.create(phone_number=number_to_buy)
        user_sessions[user_id]['number'] = incoming_number.phone_number
        save_sessions()
        success_message = f"🛍️ নম্বর `{incoming_number.phone_number}` সফলভাবে কেনা হয়েছে!"
        await processing_msg.edit_text(text=success_message, parse_mode='Markdown')
    except Exception as e:
        await processing_msg.delete()
        logger.error(f"Failed to buy number {number_to_buy} for user {user_id}: {e}")
        error_message = f"❌ এই নম্বরটি (`{number_to_buy}`) কিনতে সমস্যা হয়েছে।"
        str_error = str(e).lower()
        if "already provisioned" in str_error: error_message += " এটি ইতিমধ্যেই আপনার অ্যাকাউন্টে রয়েছে।"
        elif "not available" in str_error: error_message += " নম্বরটি আর উপলব্ধ নেই।"
        elif "permission" in str_error: error_message += " আপনার অনুমতি নেই।"
        elif "balance" in str_error: error_message += " আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স নেই।"
        else: error_message += " অন্য কোনো সমস্যা রয়েছে।"
        await context.bot.send_message(chat_id=user_id, text=error_message, parse_mode='Markdown')

@force_subscribe_check
async def show_messages_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    client = get_twilio_client(user_id)
    if client is None:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    active_number = user_sessions[user_id].get('number')
    if not active_number:
        await update.message.reply_text(f"ℹ️ আপনার কোনো কেনা নম্বর নেই।")
        return
    try:
        thinking_msg = await update.message.reply_text(f"📨 `{active_number}` নম্বরের মেসেজ খোঁজা হচ্ছে...", parse_mode='Markdown')
        messages = client.messages.list(to=active_number, limit=5)
        await thinking_msg.delete()
        keyboard = [[InlineKeyboardButton("🗑️ এই নম্বরটা রিমুভ করুন", callback_data=DIRECT_REMOVE_AFTER_SHOW_MSG_CALLBACK)]]
        inline_reply_markup = InlineKeyboardMarkup(keyboard)
        if not messages:
            await update.message.reply_text("📪 এই নম্বরে কোনো নতুন মেসেজ নেই।", reply_markup=inline_reply_markup, parse_mode='Markdown')
        else:
            response_msg_parts = [f"📨 আপনার নম্বর (`{active_number}`) এ আসা সাম্প্রতিক মেসেজ:\n"]
            for msg in messages:
                formatted_body = format_codes_in_message(msg.body or "")
                response_msg_parts.append(f"\n➡️ **প্রেরক:** `{msg.from_}`\n📝 **বার্তা:** {formatted_body}\n---")
            await update.message.reply_text("".join(response_msg_parts), reply_markup=inline_reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to fetch messages for user {user_id}: {e}")
        await update.message.reply_text("⚠️ মেসেজ আনতে সমস্যা হয়েছে।")

@force_subscribe_check
async def direct_remove_after_show_msg_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    client = get_twilio_client(user_id)
    if client is None or not user_sessions[user_id].get('number'):
        await query.edit_message_text(text="🚫 কোনো সক্রিয় নম্বর নেই বা সেশন শেষ হয়ে গেছে।")
        return
    number_to_remove = user_sessions[user_id]['number']
    await query.edit_message_text(text=f"⏳ `{number_to_remove}` নম্বরটি রিমুভ করা হচ্ছে...", parse_mode='Markdown')
    success, message = await _release_twilio_number(user_id, client, number_to_remove)
    await query.edit_message_text(text=message, parse_mode='Markdown')

@force_subscribe_check
async def remove_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if get_twilio_client(user_id) is None:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    active_number = user_sessions[user_id].get('number')
    if not active_number:
        await update.message.reply_text("ℹ️ আপনার রিমুভ করার মতো কোনো নম্বর নেই।")
        return
    keyboard = [[
        InlineKeyboardButton("✅ হ্যাঁ, নিশ্চিত", callback_data=CONFIRM_REMOVE_YES_CALLBACK),
        InlineKeyboardButton("❌ না, বাতিল", callback_data=CONFIRM_REMOVE_NO_CALLBACK)
    ]]
    await update.message.reply_text(f"ℹ️ আপনার নম্বর: `{active_number}`। আপনি কি রিমুভ করতে নিশ্চিত?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

@force_subscribe_check
async def confirm_remove_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if query.data == CONFIRM_REMOVE_NO_CALLBACK:
        await query.edit_message_text(text="🚫 নম্বর রিমুভ বাতিল করা হয়েছে।")
        return
    client = get_twilio_client(user_id)
    if client is None or not user_sessions[user_id].get('number'):
        await query.edit_message_text(text="🚫 অনুরোধটি আর বৈধ নয়।")
        return
    number_to_remove = user_sessions[user_id]['number']
    await query.edit_message_text(text=f"⏳ `{number_to_remove}` নম্বরটি রিমুভ করা হচ্ছে...", parse_mode='Markdown')
    success, message = await _release_twilio_number(user_id, client, number_to_remove)
    await query.edit_message_text(text=message, parse_mode='Markdown')

async def handle_general_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    all_button_texts = [START_COMMAND_TEXT, LOGIN_TEXT, BUY_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT, LOGOUT_TEXT, SUPPORT_TEXT]
    if text in all_button_texts: return
    if not text.startswith('/'):
        await update.message.reply_text("🤔 আপনার অনুরোধ বুঝতে পারিনি।", reply_markup=reply_markup)

@force_subscribe_check
async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    support_username = "MrGhosh75"
    keyboard = [[InlineKeyboardButton(f"💬 অ্যাডমিনের সাথে যোগাযোগ করুন", url=f"https://t.me/{support_username}")]]
    await update.message.reply_text("সাপোর্টের জন্য, অ্যাডমিনের সাথে যোগাযোগ করুন:", reply_markup=InlineKeyboardMarkup(keyboard))

if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if TOKEN is None:
        logger.critical("FATAL: TELEGRAM_BOT_TOKEN environment variable not set!")
        exit()
    
    load_sessions()
    
    app = Application.builder().token(TOKEN).build()

    # Conversation Handlers
    login_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f'^{LOGIN_TEXT}$'), login_command_handler)],
        states={AWAITING_CREDENTIALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_credentials)]},
        fallbacks=[CommandHandler('cancel', cancel_conversation)]
    )
    buy_number_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f'^{BUY_TEXT}$'), ask_for_ca_area_code)],
        states={AWAITING_CA_AREA_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, list_numbers_by_ca_area_code)]},
        fallbacks=[CommandHandler('cancel', cancel_conversation)]
    )

    app.add_handler(login_conv_handler)
    app.add_handler(buy_number_conv_handler)
    
    # Command & Message Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(f'^{START_COMMAND_TEXT}$'), start))
    app.add_handler(MessageHandler(filters.Regex(f'^{LOGOUT_TEXT}$'), logout_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{REMOVE_NUMBER_TEXT}$'), remove_number_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{SHOW_MESSAGES_TEXT}$'), show_messages_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{SUPPORT_TEXT}$'), support_handler))
    
    # Callback Query Handlers
    app.add_handler(CallbackQueryHandler(purchase_number_callback_handler, pattern=f'^{PURCHASE_CALLBACK_PREFIX}'))
    app.add_handler(CallbackQueryHandler(confirm_remove_callback_handler, pattern=f'^{CONFIRM_REMOVE_YES_CALLBACK}$|^{CONFIRM_REMOVE_NO_CALLBACK}$'))
    app.add_handler(CallbackQueryHandler(direct_remove_after_show_msg_callback, pattern=f'^{DIRECT_REMOVE_AFTER_SHOW_MSG_CALLBACK}$'))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_text))

    # Run Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    logger.info("🤖 Bot is starting to poll...")
    app.run_polling()
