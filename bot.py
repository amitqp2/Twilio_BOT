# bot.py

import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest # Keep this for error handling in callbacks
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
BUY_TEXT = '🛒 Buy Number'
SHOW_MESSAGES_TEXT = '✉️ Show Messages'
REMOVE_NUMBER_TEXT = '🗑️ Remove Number'
LOGOUT_TEXT = '↪️ Logout'
SUPPORT_TEXT = '💬 Support'

# Persistent menu
menu_keyboard = [
    [LOGIN_TEXT],
    [BUY_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT],
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
    if not body:
        return ""
    # Regex patterns to find potential codes
    # Prioritized to find longer, more specific codes first
    patterns = [
        r'\b(G-\d{6})\b',                    # Google code G-XXXXXX
        r'\b([A-Z0-9]{7,8})\b',             # 7 or 8 char alphanumeric (e.g. some 2FA, longer codes)
        r'\b([A-Z0-9]{6})\b',               # 6 char alphanumeric (e.g. common 2FA, Discord)
        r'\b(\d{7,8})\b',                   # 7 or 8 digit codes
        r'\b(\d{6})\b',                     # 6 digit codes (common OTP)
        r'\b(\d{4,5})\b',                   # 4 or 5 digit codes
        # r'is: (\d+)',                     # "is: 123456"
        # r'code: (\d+)',                   # "code: 123456"
        # r'PIN: (\d+)',                    # "PIN: 1234"
    ]
    
    all_matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, body):
            # Avoid formatting if already formatted (simple check for surrounding backticks)
            pre_char = body[match.start()-1:match.start()] if match.start() > 0 else ""
            post_char = body[match.end():match.end()+1] if match.end() < len(body) else ""
            
            if pre_char != '`' and post_char != '`':
                 all_matches.append({'start': match.start(), 'end': match.end(), 'text': match.group(0)})

    if not all_matches:
        return body

    # Sort matches by start position, then by length (longer first) to handle overlaps
    all_matches.sort(key=lambda m: (m['start'], -(m['end'] - m['start'])))

    # Filter out overlapping matches, keeping the longest/earliest one
    unique_matches = []
    last_processed_end = -1
    for match in all_matches:
        if match['start'] >= last_processed_end:
            unique_matches.append(match)
            last_processed_end = match['end']
    
    # Rebuild the string with backticks around unique matches
    result_parts = []
    current_pos = 0
    for match in unique_matches:
        if match['start'] > current_pos:
            result_parts.append(body[current_pos:match['start']])
        result_parts.append(f"`{match['text']}`")
        current_pos = match['end']
    
    if current_pos < len(body):
        result_parts.append(body[current_pos:])
        
    return "".join(result_parts)

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        logger.warning("Start command received with no effective_user.")
        return
    
    logger.info(f"User {user.id} ({user.full_name if user.full_name else 'N/A'}) triggered start.")
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
    main_menu_button_texts = [LOGIN_TEXT, BUY_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT, LOGOUT_TEXT, SUPPORT_TEXT]  
    if user_input in main_menu_button_texts:  
        await update.message.reply_text(
            f"✋ এই সময়ে বাটন না চেপে, অনুগ্রহ করে আপনার Twilio Account SID এবং Auth Token টাইপ করে পাঠান।"
            f" আবার চেষ্টা করতে '{LOGIN_TEXT}' বাটন চাপুন।"
        )
        return ConversationHandler.END  
    try:
        sid, auth = user_input.split(maxsplit=1)
        if not (sid.startswith("AC") and len(sid) == 34):
            await update.message.reply_text(f"⚠️ আপনার দেওয়া SID ({sid}) সঠিক ফরম্যাটে নেই বলে মনে হচ্ছে। অনুগ্রহ করে আবার '{LOGIN_TEXT}' বাটন চেপে সঠিক SID ও Auth Token দিন.")
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
    if not user:
        logger.warning("Logout triggered with no effective_user.")
        if update.message:
            await update.message.reply_text("ব্যবহারকারী সনাক্ত করতে সমস্যা হচ্ছে।")
        return

    user_id = user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        logger.info(f"User {user_id} ({user.full_name if user.full_name else 'N/A'}) logged out.")
        await update.message.reply_text("✅ আপনি সফলভাবে লগ আউট হয়েছেন।")  
        await start(update, context) # Show start menu after logout
    else:
        await update.message.reply_text("ℹ️ আপনি লগইন অবস্থায় নেই।", reply_markup=reply_markup)

# --- Buy Number Conversation (Canada Fixed, Asks Area Code) ---
async def ask_for_ca_area_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks for Canadian area code. Entry point for buy number conversation."""
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
    """Receives area code, lists numbers for Canada, and ends buy number conversation."""
    user_id = update.effective_user.id
    area_code_input = update.message.text.strip()

    if not area_code_input.isdigit() or len(area_code_input) != 3:
        await update.message.reply_text("⚠️ অনুগ্রহ করে সঠিক ৩ সংখ্যার কানাডিয়ান এরিয়া কোড দিন (যেমন, 416)। আবার চেষ্টা করতে '🛒 Buy Number' বাটন চাপুন অথবা /cancel টাইপ করুন।")
        return AWAITING_CA_AREA_CODE # Stay in the same state to allow re-entry of area code

    if user_id not in user_sessions: # Should be caught by entry point, but good check
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন। '🛒 Buy Number' বাটন চেপে আবার চেষ্টা করুন।")
        return ConversationHandler.END
        
    client = user_sessions[user_id]['client']
    try:
        logger.info(f"User {user_id} searching for CA numbers with area code: {area_code_input}")
        await update.message.reply_text(f"🔎 {area_code_input} এরিয়া কোডে নম্বর খোঁজা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...")
        available_numbers = client.available_phone_numbers("CA").local.list(area_code=area_code_input, limit=10)
        
        if not available_numbers:
            await update.message.reply_text(f"😔 এই এরিয়া কোডে (`{area_code_input}`) এই মুহূর্তে কোনো উপলভ্য নম্বর নেই। অন্য কোনো এরিয়া কোড দিয়ে চেষ্টা করতে পারেন।", parse_mode='Markdown')
            return ConversationHandler.END

        message_parts = [f"📞 `{area_code_input}` এরিয়া কোডে উপলব্ধ নম্বর নিচে দেওয়া হলো। নম্বরটি চেপে ধরে কপি করতে পারেন:\n"]
        keyboard_buttons = []
        for number_obj in available_numbers:
            copyable_number_text = f"`{number_obj.phone_number}`"
            message_parts.append(copyable_number_text)
            button_text = f"🛒 কিনুন {number_obj.phone_number}"
            callback_data = f"purchase_{number_obj.phone_number}"
            keyboard_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        if not keyboard_buttons: # Should not happen if available_numbers is not empty
             await update.message.reply_text("😔 নম্বর পাওয়া গেলেও বাটন তৈরি করা যায়নি।")
             return ConversationHandler.END

        full_message_text = "\n".join(message_parts)
        inline_reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        await update.message.reply_text(full_message_text, reply_markup=inline_reply_markup, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Failed to fetch numbers for user {user_id} with CA area code {area_code_input}: {e}")
        await update.message.reply_text("⚠️ নম্বর আনতে সমস্যা হয়েছে। এরিয়া কোডটি সঠিক কিনা দেখুন, আপনার Twilio অ্যাকাউন্টে সমস্যা থাকতে পারে অথবা ইন্টারনেট সংযোগ পরীক্ষা করুন।")
    
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Generic function to cancel any active conversation."""
    logger.info(f"User {update.effective_user.id} cancelled a conversation.")
    await update.message.reply_text('ℹ️ বর্তমান প্রক্রিয়া বাতিল করা হয়েছে।', reply_markup=reply_markup)
    return ConversationHandler.END
# --- End Buy Number Conversation ---

async def purchase_number_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        logger.warning("purchase_number_callback_handler: query or query.from_user not found.")
        if query: await query.answer("একটি সমস্যা হয়েছে।")
        return
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
        if action != "purchase" or not number_to_buy.startswith('+'):  
            logger.warning(f"Invalid callback data format: {query.data} for user {user_id}")
            await query.edit_message_text(text="⚠️ নম্বর কেনার অনুরোধে ত্রুটি হয়েছে।")
            return
    except ValueError:
        logger.warning(f"Callback data splitting error: {query.data} for user {user_id}")
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
        # More specific error checking
        str_error = str(e).lower()
        if "violates a uniqueness constraint" in str_error or "already provisioned" in str_error: 
            error_message += " এটি ইতিমধ্যেই আপনার অ্যাকাউন্টে রয়েছে অথবা অন্য কেউ ব্যবহার করছে।"
        elif "not be found" in str_error or "not available" in str_error or "no available numbers" in str_error: 
            error_message += " নম্বরটি এই মুহূর্তে আর উপলব্ধ নেই।"
        elif "permission" in str_error or "authorization" in str_error or "not authorized" in str_error:
            error_message += " আপনার অ্যাকাউন্টে এই নম্বরটি কেনার অনুমতি নেই।"
        elif "balance" in str_error:
            error_message += " আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স নেই।"
        else: 
            error_message += " এটি উপলব্ধ নাও থাকতে পারে অথবা আপনার অ্যাকাউন্টে অন্য কোনো সমস্যা রয়েছে।"
        await query.edit_message_text(text=error_message, reply_markup=None, parse_mode='Markdown')

async def show_messages_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:  
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    if not user_sessions[user_id].get('number'):  
        await update.message.reply_text(f"ℹ️ আপনার কোনো কেনা নম্বর নেই। প্রথমে '{BUY_TEXT}' এর মাধ্যমে একটি নম্বর কিনুন।")
        return
    client = user_sessions[user_id]['client']
    twilio_number_str = user_sessions[user_id]['number']  
    try:
        await update.message.reply_text(f"📨 `{twilio_number_str}` নম্বরের মেসেজ খোঁজা হচ্ছে...", parse_mode='Markdown')
        messages = client.messages.list(to=twilio_number_str, limit=5)  
        if not messages:
            await update.message.reply_text("📪 আপনার এই নম্বরে কোনো নতুন মেসেজ পাওয়া যায়নি।")
        else:
            response_msg_parts = [f"📨 আপনার নম্বর (`{twilio_number_str}`) এ আসা সাম্প্রতিক মেসেজ:\n"]
            for msg_instance in messages:
                formatted_body = format_codes_in_message(msg_instance.body if msg_instance.body else "")
                sender_from = msg_instance.from_ if msg_instance.from_ else "N/A"
                time_sent = msg_instance.date_sent.strftime('%Y-%m-%d %H:%M:%S') if msg_instance.date_sent else "N/A"
                msg_detail = (f"\n➡️ **প্রেরক:** `{sender_from}`\n📝 **বার্তা:** {formatted_body}\n🗓️ **সময়:** {time_sent} (UTC)\n---")
                response_msg_parts.append(msg_detail)
            full_response_msg = "\n".join(response_msg_parts)
            await update.message.reply_text(full_response_msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to fetch messages for user {user_id} on number {twilio_number_str}: {e}")
        await update.message.reply_text("⚠️ মেসেজ আনতে সমস্যা হয়েছে।")

async def remove_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    active_number = user_sessions[user_id].get('number')
    if not active_number:
        await update.message.reply_text("ℹ️ আপনার অ্যাকাউন্টে রিমুভ করার মতো কোনো সক্রিয় নম্বর নেই।")
        return
    confirmation_message = f"ℹ️ আপনার বর্তমানে কেনা নম্বরটি হলো: `{active_number}`। আপনি কি এই নম্বরটি রিমুভ করতে নিশ্চিত?"
    keyboard = [[ InlineKeyboardButton("✅ হ্যাঁ, নিশ্চিত", callback_data="confirm_remove_yes"), InlineKeyboardButton("❌ না, বাতিল", callback_data="confirm_remove_no")]]
    inline_reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(confirmation_message, reply_markup=inline_reply_markup, parse_mode='Markdown')

async def confirm_remove_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        logger.warning("confirm_remove_callback_handler: query or query.from_user not found.")
        if query: await query.answer("একটি সমস্যা হয়েছে।")
        return
    await query.answer()
    user_id = query.from_user.id
    action = query.data
    if user_id not in user_sessions or not user_sessions[user_id].get('number'):
        try: await query.edit_message_text(text="🚫 এই অনুরোধটি আর বৈধ নয় অথবা আপনার লগইন সেশন বা সক্রিয় নম্বর নেই।")
        except BadRequest: pass
        return
    number_to_remove = user_sessions[user_id]['number']
    if action == "confirm_remove_yes":
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
    elif action == "confirm_remove_no":
        await query.edit_message_text(text="🚫 নম্বর রিমুভ করার প্রক্রিয়া বাতিল করা হয়েছে।")

async def handle_general_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    # Check if user is in a specific conversation state handled by ConversationHandlers
    # If so, let the ConversationHandler deal with it.
    # This check is a bit manual; ideally ConversationHandler has higher priority or exclusive filters.
    if context.handler_queue and isinstance(context.handler_queue[-1][0], ConversationHandler):
         # If a conversation is active, we assume it will handle this text or fall through
         # So, handle_general_text should ideally not process if a conversation is expecting input.
         # This check might not be fully robust. Better to ensure ConversationHandler filters are specific.
         pass # Let conversation handler take precedence


    if user_id in user_sessions and text.startswith('+') and len(text) > 7 and text[1:].isdigit() and user_sessions[user_id].get('client'):
        # Manual number buying logic, consider deprecating or refining
        await update.message.reply_text("ℹ️ নম্বর সরাসরি টাইপ করে কেনার সুবিধাটি আপাতত নেই। অনুগ্রহ করে '🛒 Buy Number' বাটন ব্যবহার করে এরিয়া কোড দিয়ে নম্বর কিনুন।", reply_markup=reply_markup)
    else:
        # Default fallback for text not handled by other handlers
        await update.message.reply_text("🤔 আপনার অনুরোধ বুঝতে পারিনি। অনুগ্রহ করে মেনু থেকে একটি অপশন বেছে নিন।", reply_markup=reply_markup)


async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        logger.warning("Support command received with no effective_user.")
        return
    logger.info(f"User {user.id} ({user.full_name if user.full_name else 'N/A'}) clicked Support button.")
    support_username = "MrGhosh75" # Replace with actual support username
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
    
    # Buy Number Conversation Handler (Canada fixed, asks area code)
    buy_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f'^{BUY_TEXT}$'), ask_for_ca_area_code)],
        states={
            AWAITING_CA_AREA_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, list_numbers_by_ca_area_code)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conversation)] 
    )

    app.add_handler(login_conv_handler)
    app.add_handler(buy_conv_handler) # Replaces direct MessageHandler for BUY_TEXT
    app.add_handler(CommandHandler("start", start))

    app.add_handler(MessageHandler(filters.Regex(f'^{LOGOUT_TEXT}$'), logout_handler))
    # The direct buy_handler is now replaced by buy_conv_handler
    # app.add_handler(MessageHandler(filters.Regex(f'^{BUY_TEXT}$'), buy_handler)) 
    app.add_handler(MessageHandler(filters.Regex(f'^{REMOVE_NUMBER_TEXT}$'), remove_number_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{SHOW_MESSAGES_TEXT}$'), show_messages_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{SUPPORT_TEXT}$'), support_handler))
    
    app.add_handler(CallbackQueryHandler(purchase_number_callback_handler, pattern='^purchase_'))
    app.add_handler(CallbackQueryHandler(confirm_remove_callback_handler, pattern='^confirm_remove_(yes|no)$'))
    
    # General text handler should be last
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_text))

    print("Flask keep-alive server চালু হচ্ছে...")
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True  
    flask_thread.start()

    logger.info("🤖 Bot starting to poll... (Flask keep-alive server running on a separate thread)")
    app.run_polling()
