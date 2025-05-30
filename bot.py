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

# State for ConversationHandler
LOGIN_AWAITING_CREDENTIALS = range(1)

# ---- Menu Texts with Emojis (Standard Font) ----
# START_TEXT = '🏠 Start / Home' # স্টার্ট বাটন সরিয়ে ফেলা হয়েছে
LOGIN_TEXT = '🔑 Login'
BUY_TEXT = '🛒 Buy Number'
SHOW_MESSAGES_TEXT = '✉️ Show Messages'
REMOVE_NUMBER_TEXT = '🗑️ Remove Number'
LOGOUT_TEXT = '↪️ Logout'
SUPPORT_TEXT = '💬 Support'

# Persistent menu (স্টার্ট বাটন সরানো হয়েছে)
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

async def login_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_sessions:
        await update.message.reply_text("✅ আপনি ইতিমধ্যেই লগইন করা আছেন।", reply_markup=reply_markup)
        return ConversationHandler.END
    await update.message.reply_text("📝 আপনার Twilio Account SID এবং Auth Token এখানে দিন, একটি স্পেস দিয়ে আলাদা করে (যেমন: ACxxxxxxxxxxxxxx xxxxxxxxxxxxxx ):")
    return LOGIN_AWAITING_CREDENTIALS

async def receive_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text.strip()
    # START_TEXT main_menu_button_texts থেকে সরানো হয়েছে
    main_menu_button_texts = [LOGIN_TEXT, BUY_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT, LOGOUT_TEXT, SUPPORT_TEXT] 
    if user_input in main_menu_button_texts: 
        await update.message.reply_text(
            f"✋ এই সময়ে বাটন না চেপে, অনুগ্রহ করে আপনার Twilio Account SID এবং Auth Token টাইপ করে পাঠান।"
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
        # Optionally send a message if update.message exists
        if update.message:
            await update.message.reply_text("ব্যবহারকারী সনাক্ত করতে সমস্যা হচ্ছে।")
        return

    user_id = user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        logger.info(f"User {user_id} ({user.full_name if user.full_name else 'N/A'}) logged out.")
        await update.message.reply_text("✅ আপনি সফলভাবে লগ আউট হয়েছেন।") 
        # লগআউটের পর start ফাংশন কল করা হচ্ছে
        await start(update, context)
    else:
        await update.message.reply_text("ℹ️ আপনি লগইন অবস্থায় নেই।", reply_markup=reply_markup)
        # যদি লগইন না থাকা অবস্থাতেও স্টার্ট মেনু দেখাতে চান:
        # await start(update, context)


async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    client = user_sessions[user_id]['client']
    try:
        available_numbers = client.available_phone_numbers("CA").local.list(limit=10) 
        if not available_numbers:
            await update.message.reply_text("😔 এই মুহূর্তে কোনো উপলভ্য নম্বর নেই।")
            return
        message_parts = ["📞 উপলব্ধ নম্বর নিচে দেওয়া হলো। নম্বরটি চেপে ধরে কপি করতে পারেন:\n"]
        keyboard_buttons = []
        for number_obj in available_numbers:
            copyable_number_text = f"`{number_obj.phone_number}`"
            message_parts.append(copyable_number_text)
            button_text = f"🛒 কিনুন {number_obj.phone_number}"
            callback_data = f"purchase_{number_obj.phone_number}"
            keyboard_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        if not keyboard_buttons:
              await update.message.reply_text("😔 নম্বর পাওয়া গেলেও বাটন তৈরি করা যায়নি।")
              return
        full_message_text = "\n".join(message_parts)
        inline_reply_markup = InlineKeyboardMarkup(keyboard_buttons)
        await update.message.reply_text(full_message_text, reply_markup=inline_reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to fetch numbers for user {user_id}: {e}")
        await update.message.reply_text("⚠️ নম্বর আনতে সমস্যা হয়েছে। সম্ভবত আপনার অ্যাকাউন্টে এই অঞ্চলের নম্বর কেনার অনুমতি নেই অথবা অন্য কোনো সমস্যা।")

async def purchase_number_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        logger.warning("purchase_number_callback_handler: query or query.from_user not found.")
        if query: await query.answer("একটি সমস্যা হয়েছে।")
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
        incoming_number = client.incoming_phone_numbers.create(phone_number=number_to_buy)
        user_sessions[user_id]['number'] = incoming_number.phone_number
        success_message = f"🛍️ নম্বর `{incoming_number.phone_number}` সফলভাবে কেনা হয়েছে!"
        await query.edit_message_text(text=success_message, reply_markup=None, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to buy number {number_to_buy} for user {user_id}: {e}")
        error_message = f"❌ এই নম্বরটি (`{number_to_buy}`) কিনতে সমস্যা হয়েছে।"
        if "violates a uniqueness constraint" in str(e).lower() or "already provisioned" in str(e).lower(): error_message += " এটি ইতিমধ্যেই আপনার অ্যাকাউন্টে রয়েছে অথবা অন্য কেউ ব্যবহার করছে।"
        elif "not be found" in str(e).lower() or "not available" in str(e).lower(): error_message += " নম্বরটি এই মুহূর্তে আর উপলব্ধ নেই।"
        else: error_message += " এটি উপলব্ধ নাও থাকতে পারে অথবা আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স/অনুমতি নেই।"
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
        messages = client.messages.list(to=twilio_number_str, limit=5) 
        if not messages:
            await update.message.reply_text("📪 আপনার এই নম্বরে কোনো নতুন মেসেজ পাওয়া যায়নি।")
        else:
            response_msg_parts = [f"📨 আপনার নম্বর (`{twilio_number_str}`) এ আসা সাম্প্রতিক মেসেজ:\n"]
            for msg_instance in messages:
                formatted_body = format_codes_in_message(msg_instance.body if msg_instance.body else "")
                sender_from = msg_instance.from_ if msg_instance.from_ else "N/A"
                time_sent = msg_instance.date_sent.strftime('%Y-%m-%d %H:%M:%S') if msg_instance.date_sent else "N/A"
                msg_detail = (f"\n➡️ **প্রেরক:** `{sender_from}`\n📝 **বার্তা:** {formatted_body}\n🗓️ **সময়:** {time_sent}\n---")
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
        if query: await query.answer("একটি সমস্যা হয়েছে।")
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
            incoming_phone_numbers = client.incoming_phone_numbers.list(phone_number=number_to_remove, limit=1)
            if not incoming_phone_numbers:
                await query.edit_message_text(text=f"❓ নম্বর `{number_to_remove}` আপনার অ্যাকাউন্টে পাওয়া যায়নি。", parse_mode='Markdown')
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
    if user_id in user_sessions and text.startswith('+') and len(text) > 7 and text[1:].isdigit() and user_sessions[user_id].get('client'):
        number_to_buy = text
        client = user_sessions[user_id]['client']
        if user_sessions[user_id].get('number'):
            await update.message.reply_text(f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর (`{user_sessions[user_id]['number']}`) কেনা আছে। নতুন নম্বর কিনতে আগেরটি '{REMOVE_NUMBER_TEXT}' ব্যবহার করে মুছুন।", parse_mode='Markdown')
            return
        try:
            logger.info(f"User {user_id} attempting to purchase {number_to_buy} via general text.")
            incoming_number = client.incoming_phone_numbers.create(phone_number=number_to_buy)
            user_sessions[user_id]['number'] = incoming_number.phone_number
            await update.message.reply_text(f"🛍️ নম্বর `{incoming_number.phone_number}` সফলভাবে কেনা হয়েছে। (সরাসরি ইনপুট)", reply_markup=reply_markup, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Failed to buy number {number_to_buy} for user {user_id} via general text: {e}")
            error_message = f"❌ এই নম্বরটি (`{number_to_buy}`) কিনতে সমস্যা হয়েছে। (সরাসরি ইনপুট)"
            if "violates a uniqueness constraint" in str(e).lower() or "already provisioned" in str(e).lower(): error_message = f"⚠️ নম্বর `{number_to_buy}` ইতিমধ্যেই আপনার অ্যাকাউন্টে রয়েছে অথবা অন্য কেউ ব্যবহার করছে।"
            elif "AreaCode is required for an address-based search" in str(e): error_message = "ℹ️ নম্বর কেনার জন্য এরিয়া কোডসহ নম্বর দিন অথবা উপলভ্য নম্বর তালিকা থেকে বাছাই করুন।"
            await update.message.reply_text(error_message, parse_mode='Markdown')
    else:
        await update.message.reply_text("🤔 আপনার অনুরোধ বুঝতে পারিনি। অনুগ্রহ করে মেনু থেকে একটি অপশন বেছে নিন।", reply_markup=reply_markup)

async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        logger.warning("Support command received with no effective_user.")
        return
    logger.info(f"User {user.id} ({user.full_name if user.full_name else 'N/A'}) clicked Support button.")
    support_username = "MrGhosh75"
    support_message = "সাপোর্টের জন্য, অনুগ্রহ করে অ্যাডমিনের সাথে যোগাযোগ করুন:"
    keyboard = [[InlineKeyboardButton(f"যোগাযোগ করুন @{support_username}", url=f"https://t.me/{support_username}")]]
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

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f'^{LOGIN_TEXT}$'), login_command_handler)],
        states={
            LOGIN_AWAITING_CREDENTIALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_credentials)]
        },
        fallbacks=[] 
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    # START_TEXT এর জন্য MessageHandler সরিয়ে ফেলা হয়েছে
    # app.add_handler(MessageHandler(filters.Regex(f'^{START_TEXT}$'), start)) 

    app.add_handler(MessageHandler(filters.Regex(f'^{LOGOUT_TEXT}$'), logout_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{BUY_TEXT}$'), buy_handler)) 
    app.add_handler(MessageHandler(filters.Regex(f'^{REMOVE_NUMBER_TEXT}$'), remove_number_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{SHOW_MESSAGES_TEXT}$'), show_messages_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{SUPPORT_TEXT}$'), support_handler))
    
    app.add_handler(CallbackQueryHandler(purchase_number_callback_handler, pattern='^purchase_'))
    app.add_handler(CallbackQueryHandler(confirm_remove_callback_handler, pattern='^confirm_remove_(yes|no)$'))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_text))

    print("Flask keep-alive server চালু হচ্ছে...")
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True 
    flask_thread.start()

    logger.info("🤖 Bot starting to poll... (Flask keep-alive server running on a separate thread)")
    app.run_polling()
