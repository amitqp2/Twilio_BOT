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

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Globals
user_sessions = {}  # user_id -> {'sid': str, 'auth': str, 'client': Client, 'number': str}

# States for ConversationHandlers
AWAITING_CREDENTIALS = 0  # For login conversation
AWAITING_CA_AREA_CODE = 1 # For buy number conversation (Canada area code)

# ---- Menu Texts with Emojis (Standard Font) ----
LOGIN_TEXT = '🔑 Login'
BUY_RANDOM_CA_TEXT = '🎲 Buy Number (Random Area Code)'
BUY_SPECIFIC_CA_TEXT = '🎯 Buy Number (Specific Area Code)'
SHOW_MESSAGES_TEXT = '✉️ Show Messages'
REMOVE_NUMBER_TEXT = '🗑️ Remove Number' # This one will still use 2-step confirmation
LOGOUT_TEXT = '↪️ Logout'
SUPPORT_TEXT = '💬 Support'

# ---- Callback Data Constants ----
PURCHASE_CALLBACK_PREFIX = 'purchase_'
CONFIRM_REMOVE_YES_CALLBACK = 'confirm_remove_yes'
CONFIRM_REMOVE_NO_CALLBACK = 'confirm_remove_no'
DIRECT_REMOVE_AFTER_SHOW_MSG_CALLBACK = 'direct_remove_this_number' # New callback data

# Persistent menu
menu_keyboard = [
    [LOGIN_TEXT],
    [BUY_RANDOM_CA_TEXT, BUY_SPECIFIC_CA_TEXT],
    [SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT],
    [LOGOUT_TEXT],
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

# --- Helper function to format codes/OTPs in message body ---
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

# --- Helper function to display numbers with inline buy buttons ---
async def display_numbers_with_buy_buttons(message_object, context: ContextTypes.DEFAULT_TYPE, available_numbers, intro_text: str):
    if not available_numbers:
        await message_object.reply_text(f"😔 {intro_text} এই মুহূর্তে কোনো উপলভ্য নম্বর নেই।")
        return
    message_parts = [f"📞 {intro_text} উপলব্ধ নম্বর নিচে দেওয়া হলো। নম্বরটি চেপে ধরে কপি করতে পারেন:\n"]
    keyboard_buttons = []
    for number_obj in available_numbers:
        copyable_number_text = f"`{number_obj.phone_number}`"
        message_parts.append(copyable_number_text)
        button_text = f"🛒 কিনুন {number_obj.phone_number}"
        callback_data = f"{PURCHASE_CALLBACK_PREFIX}{number_obj.phone_number}"
        keyboard_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    if not keyboard_buttons:
         await message_object.reply_text("😔 নম্বর পাওয়া গেলেও বাটন তৈরি করা যায়নি।")
         return
    full_message_text = "\n".join(message_parts)
    inline_reply_markup = InlineKeyboardMarkup(keyboard_buttons)
    await message_object.reply_text(full_message_text, reply_markup=inline_reply_markup, parse_mode='Markdown')

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: logger.warning("Start command received with no effective_user."); return
    logger.info(f"User {user.id} ({user.full_name or 'N/A'}) triggered start.")
    await update.message.reply_text(
        f"👋 স্বাগতম! '{LOGIN_TEXT}' বাটন চাপুন অথবা মেনু থেকে অন্য কোনো অপশন বেছে নিন।",
        reply_markup=reply_markup
    )

async def login_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id in user_sessions:
        await update.message.reply_text("✅ আপনি ইতিমধ্যেই লগইন করা আছেন।", reply_markup=reply_markup)
        return ConversationHandler.END
    await update.message.reply_text("📝 আপনার Twilio Account SID এবং Auth Token এখানে দিন, একটি স্পেস দিয়ে আলাদা করে (যেমন: ACxxxxxxxxxxxxxx xxxxxxxxxxxxxx ):")
    return AWAITING_CREDENTIALS

async def receive_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_input = update.message.text.strip()
    main_menu_button_texts = [LOGIN_TEXT, BUY_RANDOM_CA_TEXT, BUY_SPECIFIC_CA_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT, LOGOUT_TEXT, SUPPORT_TEXT]  
    if user_input in main_menu_button_texts:  
        await update.message.reply_text(
            f"✋ এই সময়ে বাটন না চেপে, অনুগ্রহ করে আপনার Twilio Account SID এবং Auth Token টাইপ করে পাঠান।"
            f" আবার চেষ্টা করতে '{LOGIN_TEXT}' বাটন চাপুন।"
        )
        return ConversationHandler.END  
    try:
        sid, auth = user_input.split(maxsplit=1)
        if not (sid.startswith("AC") and len(sid) == 34):
            await update.message.reply_text(f"⚠️ আপনার দেওয়া SID (`{sid}`) সঠিক ফরম্যাটে নেই বলে মনে হচ্ছে। অনুগ্রহ করে আবার '{LOGIN_TEXT}' বাটন চেপে সঠিক SID ও Auth Token দিন.", parse_mode='Markdown')
            return ConversationHandler.END
        client = Client(sid, auth)
        client.api.accounts(sid).fetch()  
        user_sessions[user_id] = {'sid': sid, 'auth': auth, 'client': client, 'number': None}
        await update.message.reply_text("🎉 লগইন সফল হয়েছে!", reply_markup=reply_markup)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text(f"⚠️ SID এবং Auth Token সঠিকভাবে পাওয়া যায়নি। অনুগ্রহ করে SID, তারপর একটি স্পেস, তারপর Auth Token দিন। আবার চেষ্টা করতে '{LOGIN_TEXT}' বাটন চাপুন.")
        return ConversationHandler.END
    except Exception as e:  
        logger.error(f"Login failed for user {user_id} (SID: {sid if 'sid' in locals() else 'N/A'}): {e}")
        await update.message.reply_text(f"❌ আপনার দেওয়া SID এবং Auth Token দিয়ে লগইন করতে ব্যর্থ হয়েছে। অনুগ্রহ করে আবার '{LOGIN_TEXT}' বাটন চেপে চেষ্টা করুন।")
        return ConversationHandler.END

async def logout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: logger.warning("Logout triggered with no effective_user."); return
    user_id = user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        logger.info(f"User {user_id} ({user.full_name or 'N/A'}) logged out.")
        await update.message.reply_text("✅ আপনি সফলভাবে লগ আউট হয়েছেন।")  
        await start(update, context) 
    else:
        await update.message.reply_text("ℹ️ আপনি লগইন অবস্থায় নেই।", reply_markup=reply_markup)

async def buy_random_ca_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    if user_sessions[user_id].get('number'):
        current_number = user_sessions[user_id]['number']
        await update.message.reply_text(f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর (`{current_number}`) কেনা আছে। নতুন নম্বর কিনতে আগেরটি '{REMOVE_NUMBER_TEXT}' ব্যবহার করে মুছুন।", parse_mode='Markdown')
        return
    client = user_sessions[user_id]['client']
    try:
        await update.message.reply_text("🔎 কানাডা থেকে র্যা ন্ডম এরিয়া কোডের নম্বর খোঁজা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...")
        available_numbers = client.available_phone_numbers("CA").local.list(limit=10)
        await display_numbers_with_buy_buttons(update.message, context, available_numbers, "কানাডায় উপলব্ধ কিছু র্যা ন্ডম")
    except Exception as e:
        logger.error(f"Failed to fetch random CA numbers for user {user_id}: {e}")
        await update.message.reply_text("⚠️ নম্বর আনতে সমস্যা হয়েছে। সম্ভবত আপনার অ্যাকাউন্টে এই অঞ্চলের নম্বর কেনার অনুমতি নেই অথবা অন্য কোনো সমস্যা।")

async def ask_for_ca_area_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return ConversationHandler.END
    if user_sessions[user_id].get('number'):
        current_number = user_sessions[user_id]['number']
        await update.message.reply_text(f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর (`{current_number}`) কেনা আছে। নতুন নম্বর কিনতে আগেরটি '{REMOVE_NUMBER_TEXT}' ব্যবহার করে মুছুন।", parse_mode='Markdown')
        return ConversationHandler.END
    await update.message.reply_text("📝 অনুগ্রহ করে কানাডার কোন এরিয়া কোডের নম্বর কিনতে চান সেটি জানান (যেমন: 416, 604, 514 ইত্যাদি)। \n\nপ্রক্রিয়া বাতিল করতে /cancel টাইপ করুন।")
    return AWAITING_CA_AREA_CODE

async def list_numbers_by_ca_area_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    area_code_input = update.message.text.strip()
    if not area_code_input.isdigit() or len(area_code_input) != 3:
        await update.message.reply_text("⚠️ অনুগ্রহ করে সঠিক ৩ সংখ্যার কানাডিয়ান এরিয়া কোড দিন (যেমন, 416)। আবার চেষ্টা করতে '🎯 Buy Number (Specific Area Code)' বাটন চাপুন অথবা /cancel টাইপ করুন।")
        return AWAITING_CA_AREA_CODE
    if user_id not in user_sessions:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন। '🎯 Buy Number (Specific Area Code)' বাটন চেপে আবার চেষ্টা করুন।")
        return ConversationHandler.END
    client = user_sessions[user_id]['client']
    try:
        logger.info(f"User {user_id} searching for CA numbers with area code: {area_code_input}")
        await update.message.reply_text(f"🔎 `{area_code_input}` এরিয়া কোডে নম্বর খোঁজা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...", parse_mode='Markdown')
        available_numbers = client.available_phone_numbers("CA").local.list(area_code=area_code_input, limit=10)
        await display_numbers_with_buy_buttons(update.message, context, available_numbers, f"`{area_code_input}` এরিয়া কোডে")
    except Exception as e:
        logger.error(f"Failed to fetch numbers for user {user_id} with CA area code {area_code_input}: {e}")
        await update.message.reply_text("⚠️ নম্বর আনতে সমস্যা হয়েছে। এরিয়া কোডটি সঠিক কিনা দেখুন, আপনার Twilio অ্যাকাউন্টে সমস্যা থাকতে পারে অথবা ইন্টারনেট সংযোগ পরীক্ষা করুন।")
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info(f"User {update.effective_user.id} cancelled a conversation.")
    await update.message.reply_text('ℹ️ বর্তমান প্রক্রিয়া বাতিল করা হয়েছে।', reply_markup=reply_markup)
    return ConversationHandler.END

async def purchase_number_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user: logger.warning("purchase_number_callback_handler: query or query.from_user not found."); return
    await query.answer()  
    user_id = query.from_user.id
    if user_id not in user_sessions:
        try: await query.edit_message_text(text=f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        except BadRequest: pass  
        return
    if user_sessions[user_id].get('number'):
        current_number = user_sessions[user_id]['number']
        try: await query.edit_message_text(text=f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর (`{current_number}`) কেনা আছে। নতুন নম্বর কিনতে আগেরটি '{REMOVE_NUMBER_TEXT}' ব্যবহার করে মুছুন।", parse_mode='Markdown')
        except BadRequest: pass
        return
    try:
        action, number_to_buy = query.data.split('_', 1)
        if action != PURCHASE_CALLBACK_PREFIX.strip('_') or not number_to_buy.startswith('+'):  
            logger.warning(f"Invalid callback data format for purchase: {query.data} for user {user_id}")
            await query.edit_message_text(text="⚠️ নম্বর কেনার অনুরোধে ত্রুটি হয়েছে।")
            return
    except ValueError: 
        logger.warning(f"Callback data splitting error for purchase: {query.data} for user {user_id}")
        await query.edit_message_text(text="⚠️ নম্বর কেনার অনুরোধ বুঝতে সমস্যা হয়েছে।")
        return
    client = user_sessions[user_id]['client']
    try:
        logger.info(f"User {user_id} attempting to purchase number: {number_to_buy}")
        await query.edit_message_text(text=f"⏳ `{number_to_buy}` নম্বরটি কেনার চেষ্টা করা হচ্ছে...", parse_mode='Markdown')
        incoming_number = client.incoming_phone_numbers.create(phone_number=number_to_buy)
        user_sessions[user_id]['number'] = incoming_number.phone_number
        success_message = f"🛍️ নম্বর `{incoming_number.phone_number}` সফলভাবে কেনা হয়েছে!"
        await query.edit_message_text(text=success_message, reply_markup=None, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to buy number {number_to_buy} for user {user_id}: {e}")
        error_message = f"❌ এই নম্বরটি (`{number_to_buy}`) কিনতে সমস্যা হয়েছে।"
        str_error = str(e).lower()
        if "violates a uniqueness constraint" in str_error or "already provisioned" in str_error: error_message += " এটি ইতিমধ্যেই আপনার অ্যাকাউন্টে রয়েছে অথবা অন্য কেউ ব্যবহার করছে।"
        elif "not be found" in str_error or "not available" in str_error or "no available numbers" in str_error: error_message += " নম্বরটি এই মুহূর্তে আর উপলব্ধ নেই।"
        elif "permission" in str_error or "authorization" in str_error or "not authorized" in str_error: error_message += " আপনার অ্যাকাউন্টে এই নম্বরটি কেনার অনুমতি নেই।"
        elif "balance" in str_error: error_message += " আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স নেই।"
        else: error_message += " এটি উপলব্ধ নাও থাকতে পারে অথবা আপনার অ্যাকাউন্টে অন্য কোনো সমস্যা রয়েছে।"
        await query.edit_message_text(text=error_message, reply_markup=None, parse_mode='Markdown')

async def show_messages_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:  
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
        
    active_number = user_sessions[user_id].get('number')
    if not active_number:  
        await update.message.reply_text(f"ℹ️ আপনার কোনো কেনা নম্বর নেই। প্রথমে '{BUY_RANDOM_CA_TEXT}' অথবা '{BUY_SPECIFIC_CA_TEXT}' এর মাধ্যমে একটি নম্বর কিনুন।")
        return
        
    client = user_sessions[user_id]['client']
    try:
        await update.message.reply_text(f"📨 `{active_number}` নম্বরের মেসেজ খোঁজা হচ্ছে...", parse_mode='Markdown')
        messages = client.messages.list(to=active_number, limit=5)  
        
        reply_message_text = ""
        if not messages:
            reply_message_text = "📪 আপনার এই নম্বরে কোনো নতুন মেসেজ পাওয়া যায়নি।"
        else:
            response_msg_parts = [f"📨 আপনার নম্বর (`{active_number}`) এ আসা সাম্প্রতিক মেসেজ:\n"]
            for msg_instance in messages:
                formatted_body = format_codes_in_message(msg_instance.body if msg_instance.body else "")
                sender_from = msg_instance.from_ if msg_instance.from_ else "N/A"
                time_sent_utc = msg_instance.date_sent 
                time_sent_str = time_sent_utc.strftime('%Y-%m-%d %H:%M:%S UTC') if time_sent_utc else "N/A"
                msg_detail = (f"\n➡️ **প্রেরক:** `{sender_from}`\n📝 **বার্তা:** {formatted_body}\n🗓️ **সময়:** {time_sent_str}\n---")
                response_msg_parts.append(msg_detail)
            reply_message_text = "".join(response_msg_parts)
        
        # Send the messages
        await update.message.reply_text(reply_message_text, parse_mode='Markdown')

        # Now send the inline button for direct removal
        button_text = "এই নম্বরটা রিমুভ করুন" # As per user's request
        keyboard = [[InlineKeyboardButton(button_text, callback_data=DIRECT_REMOVE_AFTER_SHOW_MSG_CALLBACK)]]
        inline_reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("আপনি চাইলে নিচের বাটন ব্যবহার করে এই নম্বরটি সরাসরি রিমুভ করতে পারেন:", reply_markup=inline_reply_markup)

    except Exception as e:
        logger.error(f"Failed to fetch messages for user {user_id} on number {active_number}: {e}")
        await update.message.reply_text("⚠️ মেসেজ আনতে সমস্যা হয়েছে।")

async def direct_remove_after_show_msg_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user: logger.warning("direct_remove_callback: query or query.from_user not found."); return
    
    await query.answer() # Answer immediately
    user_id = query.from_user.id

    if user_id not in user_sessions or not user_sessions[user_id].get('number'):
        try: await query.edit_message_text(text="🚫 কোনো সক্রিয় নম্বর নেই অথবা সেশন শেষ হয়ে গেছে। এই অপশনটি আর কাজ করবে না।")
        except BadRequest: pass # If original message is too old or deleted
        return

    number_to_remove = user_sessions[user_id]['number']
    client = user_sessions[user_id]['client']
    
    try:
        logger.info(f"User {user_id} initiated direct removal for number: {number_to_remove} from show_messages context.")
        await query.edit_message_text(text=f"⏳ `{number_to_remove}` নম্বরটি সরাসরি রিমুভ করা হচ্ছে...", parse_mode='Markdown')
        
        incoming_phone_numbers = client.incoming_phone_numbers.list(phone_number=number_to_remove, limit=1)
        if not incoming_phone_numbers:
            await query.edit_message_text(text=f"❓ নম্বর `{number_to_remove}` আপনার অ্যাকাউন্টে পাওয়া যায়নি বা আগেই রিমুভ করা হয়েছে।", parse_mode='Markdown')
            user_sessions[user_id]['number'] = None 
            return

        number_sid_to_delete = incoming_phone_numbers[0].sid
        client.incoming_phone_numbers(number_sid_to_delete).delete()
        user_sessions[user_id]['number'] = None
        await query.edit_message_text(text=f"🗑️ নম্বর `{number_to_remove}` সফলভাবে সরাসরি রিমুভ করা হয়েছে!", parse_mode='Markdown', reply_markup=None) # Remove button
    except Exception as e:
        logger.error(f"Failed to directly remove number {number_to_remove} for user {user_id}: {e}")
        await query.edit_message_text(text=f"⚠️ নম্বর `{number_to_remove}` রিমুভ করতে সমস্যা হয়েছে।", parse_mode='Markdown', reply_markup=None)


async def remove_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): # Main menu remove (with confirmation)
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    active_number = user_sessions[user_id].get('number')
    if not active_number:
        await update.message.reply_text("ℹ️ আপনার অ্যাকাউন্টে রিমুভ করার মতো কোনো সক্রিয় নম্বর নেই।")
        return
    confirmation_message = f"ℹ️ আপনার বর্তমানে কেনা নম্বরটি হলো: `{active_number}`। আপনি কি এই নম্বরটি রিমুভ করতে নিশ্চিত?"
    keyboard = [[ 
        InlineKeyboardButton("✅ হ্যাঁ, নিশ্চিত", callback_data=CONFIRM_REMOVE_YES_CALLBACK), 
        InlineKeyboardButton("❌ না, বাতিল", callback_data=CONFIRM_REMOVE_NO_CALLBACK)
    ]]
    inline_reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(confirmation_message, reply_markup=inline_reply_markup, parse_mode='Markdown')

async def confirm_remove_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user: logger.warning("confirm_remove_callback_handler: query or query.from_user not found."); return
    await query.answer()
    user_id = query.from_user.id
    action = query.data
    if user_id not in user_sessions or not user_sessions[user_id].get('number'):
        try: await query.edit_message_text(text="🚫 এই অনুরোধটি আর বৈধ নয় অথবা আপনার লগইন সেশন বা সক্রিয় নম্বর নেই।")
        except BadRequest: pass
        return
        
    number_to_remove = user_sessions[user_id]['number']
    if action == CONFIRM_REMOVE_YES_CALLBACK:
        client = user_sessions[user_id]['client']
        try:
            logger.info(f"User {user_id} confirmed removal for number: {number_to_remove}")
            await query.edit_message_text(text=f"⏳ `{number_to_remove}` নম্বরটি রিমুভ করা হচ্ছে...", parse_mode='Markdown')
            incoming_phone_numbers = client.incoming_phone_numbers.list(phone_number=number_to_remove, limit=1)
            if not incoming_phone_numbers:
                await query.edit_message_text(text=f"❓ নম্বর `{number_to_remove}` আপনার অ্যাকাউন্টে পাওয়া যায়নি বা আগেই রিমুভ করা হয়েছে।", parse_mode='Markdown')
                user_sessions[user_id]['number'] = None  
                return
            number_sid_to_delete = incoming_phone_numbers[0].sid
            client.incoming_phone_numbers(number_sid_to_delete).delete()
            user_sessions[user_id]['number'] = None
            await query.edit_message_text(text=f"🗑️ নম্বর `{number_to_remove}` সফলভাবে রিমুভ করা হয়েছে!", parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to remove number {number_to_remove} for user {user_id} after confirmation: {e}")
            await query.edit_message_text(text="⚠️ নম্বর রিমুভ করতে সমস্যা হয়েছে।")
    elif action == CONFIRM_REMOVE_NO_CALLBACK:
        await query.edit_message_text(text="🚫 নম্বর রিমুভ করার প্রক্রিয়া বাতিল করা হয়েছে।")

async def handle_general_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    # This function should ideally only be triggered if no other specific handler (button text, command, or active conversation state) matches.
    # The ConversationHandlers for login and buy should catch their expected inputs.
    
    # Check for manual '+' number input if user is logged in and not in another conversation
    if user_id in user_sessions and text.startswith('+') and len(text) > 7 and text[1:].isdigit() and user_sessions[user_id].get('client'):
        await update.message.reply_text("ℹ️ নম্বর সরাসরি টাইপ করে কেনার সুবিধাটি আপাতত নেই। অনুগ্রহ করে মেনু থেকে 'Buy Number' বাটন ব্যবহার করুন।", reply_markup=reply_markup)
    else:
        # Default fallback for text not handled by other specific handlers
        await update.message.reply_text("🤔 আপনার অনুরোধ বুঝতে পারিনি। অনুগ্রহ করে মেনু থেকে একটি অপশন বেছে নিন।", reply_markup=reply_markup)


async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user: logger.warning("Support command received with no effective_user."); return
    logger.info(f"User {user.id} ({user.full_name or 'N/A'}) clicked Support button.")
    support_username = "MrGhosh75" 
    support_message = "সাপোর্টের জন্য, অনুগ্রহ করে অ্যাডমিনের সাথে যোগাযোগ করুন:"
    keyboard = [[InlineKeyboardButton(f"💬 যোগাযোগ করুন @{support_username}", url=f"https://t.me/{support_username}")]]
    inline_reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await update.message.reply_text(support_message, reply_markup=inline_reply_markup)
    except Exception as e:
        logger.error(f"Error sending support message to user {user.id}: {e}")


if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if TOKEN is None:
        print("ত্রুটি: TELEGRAM_BOT_TOKEN নামক এনভায়রনমেন্ট ভেরিয়েবল সেট করা নেই!")
        logger.critical("TELEGRAM_BOT_TOKEN environment variable not set!")
        exit()  
    
    app = Application.builder().token(TOKEN).build()

    # Login Conversation Handler
    login_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f'^{LOGIN_TEXT}$'), login_command_handler)],
        states={
            AWAITING_CREDENTIALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_credentials)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)] 
    )
    
    # Buy Number by Specific CA Area Code Conversation Handler
    buy_specific_ca_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f'^{BUY_SPECIFIC_CA_TEXT}$'), ask_for_ca_area_code)],
        states={
            AWAITING_CA_AREA_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, list_numbers_by_ca_area_code)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)] 
    )

    app.add_handler(login_conv_handler)
    app.add_handler(buy_specific_ca_conv_handler) 
    app.add_handler(CommandHandler("start", start))

    # Direct handlers for other menu buttons
    app.add_handler(MessageHandler(filters.Regex(f'^{LOGOUT_TEXT}$'), logout_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{BUY_RANDOM_CA_TEXT}$'), buy_random_ca_number_handler)) 
    app.add_handler(MessageHandler(filters.Regex(f'^{REMOVE_NUMBER_TEXT}$'), remove_number_handler)) # This shows confirmation
    app.add_handler(MessageHandler(filters.Regex(f'^{SHOW_MESSAGES_TEXT}$'), show_messages_handler)) # This will now add an inline button
    app.add_handler(MessageHandler(filters.Regex(f'^{SUPPORT_TEXT}$'), support_handler))
    
    # CallbackQueryHandlers
    app.add_handler(CallbackQueryHandler(purchase_number_callback_handler, pattern=f'^{PURCHASE_CALLBACK_PREFIX}'))
    app.add_handler(CallbackQueryHandler(confirm_remove_callback_handler, pattern=f'^{CONFIRM_REMOVE_YES_CALLBACK}$|^{CONFIRM_REMOVE_NO_CALLBACK}$')) # Regex for both yes/no
    app.add_handler(CallbackQueryHandler(direct_remove_after_show_msg_callback, pattern=f'^{DIRECT_REMOVE_AFTER_SHOW_MSG_CALLBACK}$')) # New handler
    
    # General text handler should be last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_text))

    print("Flask keep-alive server চালু হচ্ছে...")
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True  
    flask_thread.start()

    logger.info("🤖 Bot starting to poll... (Flask keep-alive server running on a separate thread)")
    app.run_polling()
