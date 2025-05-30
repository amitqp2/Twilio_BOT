import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton, ChatMember
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from twilio.rest import Client
import os
import threading
from flask import Flask

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Globals
user_sessions = {}  # user_id -> {'sid': str, 'auth': str, 'client': Client, 'number': str}

# State for ConversationHandler
LOGIN_AWAITING_CREDENTIALS = range(1)

# ---- English Menu Texts with Emojis (Standard Font) ----
LOGIN_TEXT = '🔑 Login'
BUY_TEXT = '🛒 Buy Number'
SHOW_MESSAGES_TEXT = '✉️ Show Messages'
REMOVE_NUMBER_TEXT = '🗑️ Remove Number'
LOGOUT_TEXT = '↪️ Logout'

# ---- Channel/Group Join Configuration ----
TARGET_CHANNEL_USERNAME = "@boss_universe75"
TARGET_GROUP_USERNAME = "@boss_universe75_support" # Make sure bot is admin in both
JOIN_CHANNEL_PROMPT_TEXT = "এটি আমাদের চ্যানেল। সকল প্রকার আয়ের উপায় ও কৌশল জানতে সবসময় এই চ্যানেলের পাশে থাকুন।"
JOIN_GROUP_PROMPT_TEXT = "আপনার যেকোনো সমস্যা আপনি এই গ্রুপে শেয়ার করতে পারেন।"
USER_COMPLETED_ALL_JOINS_KEY = 'has_completed_all_joins'
VERIFY_ALL_JOINS_CALLBACK_DATA = "verify_all_joins"

# Persistent menu
menu_keyboard = [
    [LOGIN_TEXT],
    [BUY_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT],
    [LOGOUT_TEXT]
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

# --- Helper function to check channel/group memberships ---
async def check_all_memberships(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    is_member_of_channel = False
    is_member_of_group = False

    try:
        member_channel = await context.bot.get_chat_member(chat_id=TARGET_CHANNEL_USERNAME, user_id=user_id)
        if member_channel.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            is_member_of_channel = True
    except BadRequest as e: # User not found in channel, or channel username incorrect
        logger.warning(f"BadRequest when checking channel {TARGET_CHANNEL_USERNAME} for user {user_id}: {e}")
    except Forbidden as e: # Bot not admin or kicked from channel
        logger.error(f"Forbidden: Bot cannot access channel {TARGET_CHANNEL_USERNAME} members. Is it an admin? Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error checking channel {TARGET_CHANNEL_USERNAME} for user {user_id}: {e}")

    try:
        member_group = await context.bot.get_chat_member(chat_id=TARGET_GROUP_USERNAME, user_id=user_id)
        if member_group.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            is_member_of_group = True
    except BadRequest as e: # User not found in group, or group username incorrect
        logger.warning(f"BadRequest when checking group {TARGET_GROUP_USERNAME} for user {user_id}: {e}")
    except Forbidden as e: # Bot not admin or kicked from group
        logger.error(f"Forbidden: Bot cannot access group {TARGET_GROUP_USERNAME} members. Is it an admin? Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error checking group {TARGET_GROUP_USERNAME} for user {user_id}: {e}")
        
    return is_member_of_channel and is_member_of_group

async def send_join_prompt(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    user_id = update_or_query.effective_user.id
    join_message = (
        f"👋 এই বটটি সম্পূর্ণভাবে ব্যবহার করার জন্য, অনুগ্রহ করে আমাদের নিচের দুটি প্ল্যাটফর্মেই জয়েন করুন:\n\n"
        f"১. **চ্যানেল:** {JOIN_CHANNEL_PROMPT_TEXT}\n"
        f"   ইউজারনেম: {TARGET_CHANNEL_USERNAME}\n\n"
        f"২. **গ্রুপ:** {JOIN_GROUP_PROMPT_TEXT}\n"
        f"   ইউজারনেম: {TARGET_GROUP_USERNAME}\n\n"
        f"দুটোতেই জয়েন করার পর নিচের বাটনে ক্লিক করে যাচাই করুন:"
    )
    keyboard = [[InlineKeyboardButton("✅ আমি দুটোতেই জয়েন করেছি (যাচাই করুন)", callback_data=VERIFY_ALL_JOINS_CALLBACK_DATA)]]
    reply_markup_join = InlineKeyboardMarkup(keyboard)

    if hasattr(update_or_query, 'message') and update_or_query.message:
        await update_or_query.message.reply_text(join_message, reply_markup=reply_markup_join)
    elif hasattr(update_or_query, 'callback_query') and update_or_query.callback_query:
        try:
            await update_or_query.callback_query.edit_message_text(join_message, reply_markup=reply_markup_join)
        except BadRequest: # Message is not modified or other issues
            await context.bot.send_message(chat_id=user_id, text=join_message, reply_markup=reply_markup_join)


async def ensure_user_has_joined(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update_or_query.effective_user.id
    if context.user_data.get(USER_COMPLETED_ALL_JOINS_KEY, False):
        return True

    all_joined = await check_all_memberships(user_id, context)
    if all_joined:
        context.user_data[USER_COMPLETED_ALL_JOINS_KEY] = True
        return True
    else:
        context.user_data[USER_COMPLETED_ALL_JOINS_KEY] = False
        await send_join_prompt(update_or_query, context)
        return False

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await ensure_user_has_joined(update, context):
        await update.message.reply_text(
            f"👋 স্বাগতম! আপনি আমাদের চ্যানেল ও গ্রুপের সদস্য। '{LOGIN_TEXT}' বাটন চাপুন অথবা মেনু থেকে অন্য কোনো অপশন বেছে নিন।",
            reply_markup=reply_markup
        )
    # If not joined, ensure_user_has_joined will send the prompt

async def verify_all_joins_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # Answer callback query immediately
    user_id = query.from_user.id

    all_joined = await check_all_memberships(user_id, context)
    if all_joined:
        context.user_data[USER_COMPLETED_ALL_JOINS_KEY] = True
        await query.edit_message_text(
            text=f"🎉 ধন্যবাদ! আপনি সফলভাবে চ্যানেল এবং গ্রুপের সদস্যপদ যাচাই করেছেন। বটটি এখন আপনার জন্য আনলক করা হয়েছে।"
        )
        # Send the main menu with a new message
        await context.bot.send_message(chat_id=user_id, text="প্রধান মেনু:", reply_markup=reply_markup)
    else:
        context.user_data[USER_COMPLETED_ALL_JOINS_KEY] = False
        # Re-send join prompt and button
        await query.edit_message_text(text="😔 দুঃখিত, যাচাই সফল হয়নি।") # Clear previous message first
        await send_join_prompt(query, context) # query object can be passed if effective_user is needed


async def login_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context):
        return ConversationHandler.END # End conversation if not joined

    user_id = update.effective_user.id
    if user_id in user_sessions:
        await update.message.reply_text("✅ আপনি ইতিমধ্যেই লগইন করা আছেন।", reply_markup=reply_markup)
        return ConversationHandler.END
    await update.message.reply_text("📝 আপনার Twilio Account SID এবং Auth Token এখানে দিন, একটি স্পেস দিয়ে আলাদা করে (যেমন: <SID>space<AUTH_TOKEN> ):")
    return LOGIN_AWAITING_CREDENTIALS

async def receive_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Join check is implicitly handled by login_command_handler, but can be added here too if direct access is possible
    user_id = update.effective_user.id
    user_input = update.message.text.strip()
    
    main_menu_button_texts = [LOGIN_TEXT, BUY_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT, LOGOUT_TEXT]
    if user_input in main_menu_button_texts: # Prevent using menu buttons as input
        await update.message.reply_text(
            f"✋ এই সময়ে বাটন না চেপে, অনুগ্রহ করে আপনার Twilio Account SID এবং Auth Token টাইপ করে পাঠান।"
            f" আবার চেষ্টা করতে '{LOGIN_TEXT}' বাটন চাপুন।"
        )
        return ConversationHandler.END 

    try:
        sid, auth = user_input.split(maxsplit=1)
        if not (sid.startswith("AC") and len(sid) == 34):
            await update.message.reply_text(
                f"⚠️ আপনার দেওয়া SID ({sid}) সঠিক ফরম্যাটে নেই বলে মনে হচ্ছে। "
                f"অনুগ্রহ করে আবার '{LOGIN_TEXT}' বাটন চেপে সঠিক SID ও Auth Token দিন।"
            )
            return ConversationHandler.END

        client = Client(sid, auth)
        client.api.accounts(sid).fetch() 
        user_sessions[user_id] = {'sid': sid, 'auth': auth, 'client': client, 'number': None}
        await update.message.reply_text("🎉 লগইন সফল হয়েছে!", reply_markup=reply_markup)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text(
            f"⚠️ SID এবং Auth Token সঠিকভাবে পাওয়া যায়নি। অনুগ্রহ করে SID, তারপর একটি স্পেস, তারপর Auth Token দিন। "
            f"আবার চেষ্টা করতে '{LOGIN_TEXT}' বাটন চাপুন।"
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Login failed for user {user_id} (SID: {sid if 'sid' in locals() else 'N/A'}): {e}")
        await update.message.reply_text(f"❌ আপনার দেওয়া SID এবং Auth Token দিয়ে লগইন করতে ব্যর্থ হয়েছে। অনুগ্রহ করে আবার '{LOGIN_TEXT}' বাটন চেপে চেষ্টা করুন।")
        return ConversationHandler.END

async def logout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context): return
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        await update.message.reply_text("✅ আপনি সফলভাবে লগ আউট হয়েছেন।", reply_markup=reply_markup)
    else:
        await update.message.reply_text("ℹ️ আপনি লগইন অবস্থায় নেই।", reply_markup=reply_markup)

async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context): return
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

        keyboard = []
        for number_obj in available_numbers:
            button_text = f"🛒 কিনুন {number_obj.phone_number}"
            callback_data = f"purchase_{number_obj.phone_number}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        if not keyboard:
             await update.message.reply_text("😔 নম্বর পাওয়া গেলেও বাটন তৈরি করা যায়নি।")
             return

        inline_reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("📞 উপলব্ধ নম্বর নিচে দেওয়া হলো। পছন্দের নম্বরের পাশের 'কিনুন' বাটনে ক্লিক করুন:", reply_markup=inline_reply_markup)

    except Exception as e:
        logger.error(f"Failed to fetch numbers for user {user_id}: {e}")
        await update.message.reply_text("⚠️ নম্বর আনতে সমস্যা হয়েছে। সম্ভবত আপনার অ্যাকাউন্টে এই অঞ্চলের নম্বর কেনার অনুমতি নেই অথবা অন্য কোনো সমস্যা।")

async def purchase_number_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_user_has_joined(query, context): # Pass query here
        await query.answer("অনুগ্রহ করে প্রথমে চ্যানেল ও গ্রুপে জয়েন করে ভেরিফাই করুন।")
        return
        
    await query.answer() 
    user_id = query.from_user.id
    
    if user_id not in user_sessions: # Should be caught by ensure_user_has_joined if login is required for this
        await query.edit_message_text(text=f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return

    if user_sessions[user_id].get('number'):
        current_number = user_sessions[user_id]['number']
        await query.edit_message_text(text=f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর ({current_number}) কেনা আছে। নতুন নম্বর কিনতে আগেরটি '{REMOVE_NUMBER_TEXT}' ব্যবহার করে মুছুন।")
        return

    try:
        action, number_to_buy = query.data.split('_', 1)
        if action != "purchase" or not number_to_buy.startswith('+'): 
            logger.warning(f"Invalid callback data format: {query.data} for user {user_id}")
            await query.edit_message_text(text="⚠️ নম্বর কেনার অনুরোধে ত্রুটি হয়েছে।")
            return
    except ValueError:
        logger.warning(f"Callback data splitting error: {query.data} for user {user_id}")
        await query.edit_message_text(text="⚠️ নম্বর কেনার অনুরোধ বুঝতে সমস্যা হয়েছে।")
        return

    client = user_sessions[user_id]['client']
    try:
        logger.info(f"User {user_id} attempting to purchase number: {number_to_buy}")
        incoming_number = client.incoming_phone_numbers.create(phone_number=number_to_buy)
        user_sessions[user_id]['number'] = incoming_number.phone_number
        success_message = f"🛍️ নম্বর {incoming_number.phone_number} সফলভাবে কেনা হয়েছে!"
        await query.edit_message_text(text=success_message, reply_markup=None)
    except Exception as e:
        logger.error(f"Failed to buy number {number_to_buy} for user {user_id}: {e}")
        error_message = f"❌ এই নম্বরটি ({number_to_buy}) কিনতে সমস্যা হয়েছে।"
        if "violates a uniqueness constraint" in str(e).lower() or "already provisioned" in str(e).lower():
            error_message += " এটি ইতিমধ্যেই আপনার অ্যাকাউন্টে রয়েছে অথবা অন্য কেউ ব্যবহার করছে।"
        elif "not be found" in str(e).lower() or "not available" in str(e).lower(): 
            error_message += " নম্বরটি এই মুহূর্তে আর উপলব্ধ নেই।"
        else:
            error_message += " এটি উপলব্ধ নাও থাকতে পারে অথবা আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স/অনুমতি নেই।"
        await query.edit_message_text(text=error_message, reply_markup=None)

async def show_messages_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context): return
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
            await update.message.reply_text("📪 আপনার এই নম্বরে কোনো মেসেজ পাওয়া যায়নি।")
        else:
            response_msg = "📨 আপনার নম্বরে আসা সাম্প্রতিক মেসেজ:\n"
            for msg_instance in messages:
                response_msg += f"From: {msg_instance.from_}\nBody: {msg_instance.body}\n---\n"
            await update.message.reply_text(response_msg)
    except Exception as e:
        logger.error(f"Failed to fetch messages for user {user_id} on number {twilio_number_str}: {e}")
        await update.message.reply_text("⚠️ মেসেজ আনতে সমস্যা হয়েছে।")

async def remove_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context): return
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    
    active_number = user_sessions[user_id].get('number')
    if not active_number:
        await update.message.reply_text("ℹ️ আপনার অ্যাকাউন্টে রিমুভ করার মতো কোনো সক্রিয় নম্বর নেই।")
        return

    confirmation_message = f"ℹ️ আপনার বর্তমানে কেনা নম্বরটি হলো: {active_number}। আপনি কি এই নম্বরটি রিমুভ করতে নিশ্চিত?"
    keyboard = [[
        InlineKeyboardButton("✅ হ্যাঁ, নিশ্চিত", callback_data="confirm_remove_yes"),
        InlineKeyboardButton("❌ না, বাতিল", callback_data="confirm_remove_no")
    ]]
    inline_reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(confirmation_message, reply_markup=inline_reply_markup)

async def confirm_remove_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_user_has_joined(query, context): # Pass query here
        await query.answer("অনুগ্রহ করে প্রথমে চ্যানেল ও গ্রুপে জয়েন করে ভেরিফাই করুন।")
        return

    await query.answer()
    user_id = query.from_user.id
    action = query.data

    # Re-check login and active number, as ensure_user_has_joined only checks channel/group join
    if user_id not in user_sessions or not user_sessions[user_id].get('number'):
        await query.edit_message_text(text="🚫 এই অনুরোধটি আর বৈধ নয় অথবা আপনার লগইন সেশন বা সক্রিয় নম্বর নেই।")
        return

    number_to_remove = user_sessions[user_id]['number']

    if action == "confirm_remove_yes":
        client = user_sessions[user_id]['client']
        try:
            logger.info(f"User {user_id} confirmed removal for number: {number_to_remove}")
            incoming_phone_numbers = client.incoming_phone_numbers.list(phone_number=number_to_remove, limit=1)
            if not incoming_phone_numbers:
                await query.edit_message_text(text=f"❓ নম্বর {number_to_remove} আপনার অ্যাকাউন্টে পাওয়া যায়নি।")
                user_sessions[user_id]['number'] = None 
                return

            number_sid = incoming_phone_numbers[0].sid
            client.incoming_phone_numbers(number_sid).delete()
            user_sessions[user_id]['number'] = None
            await query.edit_message_text(text=f"🗑️ নম্বর {number_to_remove} সফলভাবে রিমুভ করা হয়েছে!")
        except Exception as e:
            logger.error(f"Failed to remove number {number_to_remove} for user {user_id} after confirmation: {e}")
            await query.edit_message_text(text="⚠️ নম্বর রিমুভ করতে সমস্যা হয়েছে।")
    
    elif action == "confirm_remove_no":
        await query.edit_message_text(text="🚫 নম্বর রিমুভ করার প্রক্রিয়া বাতিল করা হয়েছে।")


async def handle_general_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This handler should also be protected if it provides core functionality
    if not await ensure_user_has_joined(update, context): return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id in user_sessions and text.startswith('+') and user_sessions[user_id].get('client'):
        number_to_buy = text
        client = user_sessions[user_id]['client']
        if user_sessions[user_id].get('number'):
            await update.message.reply_text(f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর ({user_sessions[user_id]['number']}) কেনা আছে। নতুন নম্বর কিনতে আগেরটি '{REMOVE_NUMBER_TEXT}' ব্যবহার করে মুছুন।")
            return
        try:
            incoming_number = client.incoming_phone_numbers.create(phone_number=number_to_buy)
            user_sessions[user_id]['number'] = incoming_number.phone_number
            await update.message.reply_text(f"🛍️ নম্বর {incoming_number.phone_number} সফলভাবে কেনা হয়েছে। (ম্যানুয়াল)", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to buy number {number_to_buy} for user {user_id} via general text: {e}")
            error_message = "❌ এই নম্বরটি কিনতে সমস্যা হয়েছে। (ম্যানুয়াল)"
            if "violates a uniqueness constraint" in str(e).lower() or "already provisioned" in str(e).lower():
                error_message = f"⚠️ নম্বর {number_to_buy} ইতিমধ্যেই আপনার অ্যাকাউন্টে রয়েছে অথবা অন্য কেউ ব্যবহার করছে। (ম্যানুয়াল)"
            elif "AreaCode is required for an address-based search" in str(e):
                 error_message = "ℹ️ নম্বর কেনার জন্য এরিয়া কোডসহ নম্বর দিন অথবা উপলভ্য নম্বর তালিকা থেকে বাছাই করুন। (ম্যানুয়াল)"
            await update.message.reply_text(error_message)
    else:
        # If user types something random and is already past the join check
        if context.user_data.get(USER_COMPLETED_ALL_JOINS_KEY, False):
            await update.message.reply_text("🤔 আপনার অনুরোধ বুঝতে পারিনি। অনুগ্রহ করে মেনু থেকে একটি অপশন বেছে নিন।", reply_markup=reply_markup)
        # If they haven't joined, ensure_user_has_joined would have already sent a prompt.


if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if TOKEN is None:
        print("ত্রুটি: TELEGRAM_BOT_TOKEN নামক এনভায়রনমেন্ট ভেরিয়েবল সেট করা নেই!")
        exit() 
    
    # Persistence can be configured here if needed for context.user_data across restarts
    # For now, context.user_data will be in-memory
    
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Text([LOGIN_TEXT]), login_command_handler)],
        states={
            LOGIN_AWAITING_CREDENTIALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_credentials)]
        },
        fallbacks=[] 
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))

    app.add_handler(MessageHandler(filters.Text([LOGOUT_TEXT]), logout_handler))
    app.add_handler(MessageHandler(filters.Text([BUY_TEXT]), buy_handler)) 
    app.add_handler(MessageHandler(filters.Text([REMOVE_NUMBER_TEXT]), remove_number_handler))
    app.add_handler(MessageHandler(filters.Text([SHOW_MESSAGES_TEXT]), show_messages_handler))
    
    app.add_handler(CallbackQueryHandler(purchase_number_callback_handler, pattern='^purchase_'))
    app.add_handler(CallbackQueryHandler(confirm_remove_callback_handler, pattern='^confirm_remove_(yes|no)$'))
    app.add_handler(CallbackQueryHandler(verify_all_joins_callback, pattern=f'^{VERIFY_ALL_JOINS_CALLBACK_DATA}$')) # Handler for join verification
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_text))

    print("Flask keep-alive server চালু হচ্ছে...")
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True 
    flask_thread.start()

    logger.info("🤖 Bot starting to poll... (Flask keep-alive server running on a separate thread)")
    app.run_polling()
