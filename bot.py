# bot.py

import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatMemberStatus # এটি আপনার কোডে ছিল, ঠিক আছে
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
from twilio.rest import Client # আপনার কোডে এটি আছে
import os
import threading
from flask import Flask # আপনার কোডে এটি আছে
import traceback # বিস্তারিত ট্রেসব্যাক লগ করার জন্য

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
TARGET_GROUP_USERNAME = "@boss_universe75_support"
TARGET_CHANNEL_ID = -1002652802704
TARGET_GROUP_ID = -1002623419206
JOIN_CHANNEL_PROMPT_TEXT = "এটি আমাদের চ্যানেল। সকল প্রকার আয়ের উপায় ও কৌশল জানতে সবসময় এই চ্যানেলের পাশে থাকুন।"
JOIN_GROUP_PROMPT_TEXT = "আপনার যেকোনো সমস্যা আপনি এই গ্রুপে শেয়ার করতে পারেন।"
USER_COMPLETED_ALL_JOINS_KEY = 'has_completed_all_joins'
VERIFY_ALL_JOINS_CALLBACK_DATA = "verify_all_joins"

# Persistent menu (আপনার কোডের ফরম্যাটিং অনুযায়ী)
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

# --- Helper function to check channel/group memberships (AttributeError সমাধানের জন্য পরিবর্তিত) ---
async def check_all_memberships(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    is_member_of_channel = False
    is_member_of_group = False
    
    # স্ট্যাটাসের গ্রহণযোগ্য স্ট্রিং ভ্যালুগুলো
    ACCEPTED_STATUSES = ["member", "administrator", "creator"]

    # চ্যানেলের মেম্বারশিপ চেক
    try:
        if context.bot:
            member_channel = await context.bot.get_chat_member(chat_id=TARGET_CHANNEL_ID, user_id=user_id)
            # member_channel.status.value ব্যবহার করে স্ট্রিং ভ্যালুর সাথে তুলনা করা হচ্ছে
            if hasattr(member_channel, 'status') and member_channel.status and hasattr(member_channel.status, 'value') and member_channel.status.value in ACCEPTED_STATUSES:
                is_member_of_channel = True
                logger.info(f"User {user_id} IS a member of channel {TARGET_CHANNEL_ID} with status value: {member_channel.status.value}")
            elif hasattr(member_channel, 'status') and member_channel.status: 
                 logger.info(f"User {user_id} is NOT a member of channel {TARGET_CHANNEL_ID} (status: {member_channel.status}, value: {getattr(member_channel.status, 'value', 'N/A')})")
            else:
                 logger.info(f"User {user_id} - Could not determine valid status for channel {TARGET_CHANNEL_ID}")
        else:
            logger.error(f"Bot instance not found in context for channel {TARGET_CHANNEL_ID} check for user {user_id}.")
    except BadRequest as e:
        logger.warning(f"BadRequest when checking channel {TARGET_CHANNEL_ID} for user {user_id}: {e}")
    except Forbidden as e:
        logger.error(f"Forbidden: Bot cannot access channel {TARGET_CHANNEL_ID} members for user {user_id}. Is it an admin? Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error checking channel {TARGET_CHANNEL_ID} for user {user_id}. Exception Type: {type(e)}, Error: {e}")
        logger.error(f"Full Traceback for channel check error: {traceback.format_exc()}")

    # গ্রুপের মেম্বারশিপ চেক
    try:
        if context.bot:
            member_group = await context.bot.get_chat_member(chat_id=TARGET_GROUP_ID, user_id=user_id)
            # member_group.status.value ব্যবহার করে স্ট্রিং ভ্যালুর সাথে তুলনা করা হচ্ছে
            if hasattr(member_group, 'status') and member_group.status and hasattr(member_group.status, 'value') and member_group.status.value in ACCEPTED_STATUSES:
                is_member_of_group = True
                logger.info(f"User {user_id} IS a member of group {TARGET_GROUP_ID} with status value: {member_group.status.value}")
            elif hasattr(member_group, 'status') and member_group.status:
                 logger.info(f"User {user_id} is NOT a member of group {TARGET_GROUP_ID} (status: {member_group.status}, value: {getattr(member_group.status, 'value', 'N/A')})")
            else:
                logger.info(f"User {user_id} - Could not determine valid status for group {TARGET_GROUP_ID}")
        else:
            logger.error(f"Bot instance not found in context for group {TARGET_GROUP_ID} check for user {user_id}.")
    except BadRequest as e:
        logger.warning(f"BadRequest when checking group {TARGET_GROUP_ID} for user {user_id}: {e}")
    except Forbidden as e:
        logger.error(f"Forbidden: Bot cannot access group {TARGET_GROUP_ID} members for user {user_id}. Is it an admin? Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error checking group {TARGET_GROUP_ID} for user {user_id}. Exception Type: {type(e)}, Error: {e}")
        logger.error(f"Full Traceback for group check error: {traceback.format_exc()}")
        
    return is_member_of_channel and is_member_of_group

# send_join_prompt ফাংশনে আগের AttributeError সমাধান করা আছে
async def send_join_prompt(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    current_user = None
    if hasattr(update_or_query, 'effective_user') and update_or_query.effective_user:
        current_user = update_or_query.effective_user
    elif hasattr(update_or_query, 'from_user') and update_or_query.from_user: 
        current_user = update_or_query.from_user
    
    if not current_user:
        logger.error("send_join_prompt: Could not determine user from update_or_query object.")
        if hasattr(update_or_query, 'callback_query') and update_or_query.callback_query:
            try:
                await update_or_query.callback_query.answer("ব্যবহারকারী সনাক্ত করতে একটি সমস্যা হয়েছে।", show_alert=True)
            except Exception as e_ans:
                logger.error(f"Error sending answer to callback query in send_join_prompt: {e_ans}")
        return

    user_id = current_user.id
    
    join_message = (
        f"👋 এই বটটি সম্পূর্ণভাবে ব্যবহার করার জন্য, অনুগ্রহ করে আমাদের নিচের দুটি প্ল্যাটফর্মেই জয়েন করুন:\n\n"
        f"১. **চ্যানেল:** {JOIN_CHANNEL_PROMPT_TEXT}\n"
        f"   জয়েন করুন: {TARGET_CHANNEL_USERNAME}\n\n"
        f"২. **গ্রুপ:** {JOIN_GROUP_PROMPT_TEXT}\n"
        f"   জয়েন করুন: {TARGET_GROUP_USERNAME}\n\n"
        f"দুটোতেই জয়েন করার পর নিচের বাটনে ক্লিক করে যাচাই করুন:"
    )
    keyboard = [[InlineKeyboardButton("✅ আমি দুটোতেই জয়েন করেছি (যাচাই করুন)", callback_data=VERIFY_ALL_JOINS_CALLBACK_DATA)]]
    reply_markup_join = InlineKeyboardMarkup(keyboard)

    try:
        if hasattr(update_or_query, 'message') and update_or_query.message:
            await update_or_query.message.reply_text(join_message, reply_markup=reply_markup_join)
        elif hasattr(update_or_query, 'callback_query') and update_or_query.callback_query:
            await update_or_query.callback_query.edit_message_text(join_message, reply_markup=reply_markup_join)
    except BadRequest as e:
        logger.warning(f"Could not edit message for join prompt (User: {user_id}). Error: {e}. Sending new message instead.")
        chat_id_to_send = None
        if hasattr(update_or_query, 'callback_query') and update_or_query.callback_query and update_or_query.callback_query.message:
            chat_id_to_send = update_or_query.callback_query.message.chat_id
        elif hasattr(update_or_query, 'message') and update_or_query.message:
             chat_id_to_send = update_or_query.message.chat_id

        if chat_id_to_send:
            try:
                await context.bot.send_message(chat_id=chat_id_to_send, text=join_message, reply_markup=reply_markup_join)
            except Exception as send_e:
                logger.error(f"Failed to send new join_prompt message to chat_id {chat_id_to_send}. Error: {send_e}")
        else:
            logger.error(f"Could not determine chat_id to send new join_prompt for user {user_id}")


async def ensure_user_has_joined(update_or_query, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = None 
    if hasattr(update_or_query, 'effective_user') and update_or_query.effective_user:
        user = update_or_query.effective_user
    elif hasattr(update_or_query, 'from_user') and update_or_query.from_user: 
        user = update_or_query.from_user

    if not user:
        logger.warning("ensure_user_has_joined: effective_user/from_user not found.")
        if hasattr(update_or_query, 'message') and update_or_query.message: 
            await update_or_query.message.reply_text("ব্যবহারকারী সনাক্ত করতে সমস্যা হচ্ছে। অনুগ্রহ করে আবার /start কমান্ড দিন।")
        elif hasattr(update_or_query, 'callback_query') and update_or_query.callback_query: 
            await update_or_query.callback_query.answer("ব্যবহারকারী সনাক্ত করতে সমস্যা হচ্ছে। অনুগ্রহ করে আবার চেষ্টা করুন।", show_alert=True)
        return False
        
    user_id = user.id
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
    if not update.effective_user: 
        logger.warning("Start command received with no effective_user.")
        return

    if await ensure_user_has_joined(update, context): 
        await update.message.reply_text(
            f"👋 স্বাগতম! আপনি আমাদের চ্যানেল ও গ্রুপের সদস্য। '{LOGIN_TEXT}' বাটন চাপুন অথবা মেনু থেকে অন্য কোনো অপশন বেছে নিন।",
            reply_markup=reply_markup
        )

async def verify_all_joins_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        logger.warning("verify_all_joins_callback: query or query.from_user not found.")
        if query: await query.answer("একটি সমস্যা হয়েছে।")
        return

    await query.answer() 
    user_id = query.from_user.id

    all_joined = await check_all_memberships(user_id, context)
    if all_joined:
        context.user_data[USER_COMPLETED_ALL_JOINS_KEY] = True
        try:
            await query.edit_message_text(
                text=f"🎉 ধন্যবাদ! আপনি সফলভাবে চ্যানেল এবং গ্রুপের সদস্যপদ যাচাই করেছেন। বটটি এখন আপনার জন্য আনলক করা হয়েছে।"
            )
        except BadRequest as e: 
            logger.warning(f"Could not edit success message for user {user_id}: {e}")
            await context.bot.send_message(chat_id=user_id, text=f"🎉 ধন্যবাদ! আপনি সফলভাবে চ্যানেল এবং গ্রুপের সদস্যপদ যাচাই করেছেন। বটটি এখন আপনার জন্য আনলক করা হয়েছে।")
        await context.bot.send_message(chat_id=user_id, text="প্রধান মেনু:", reply_markup=reply_markup)
    else:
        context.user_data[USER_COMPLETED_ALL_JOINS_KEY] = False
        original_message_text = "😔 দুঃখিত, যাচাই সফল হয়নি। অনুগ্রহ করে আবার চেষ্টা করুন অথবা নিশ্চিত করুন আপনি উভয় প্ল্যাটফর্মে জয়েন আছেন।"
        try:
            await query.edit_message_text(text=original_message_text)
        except BadRequest as e:
            logger.warning(f"Could not edit failure message for user {user_id}: {e}")
        await send_join_prompt(query, context) 


async def login_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context): 
        return ConversationHandler.END 

    user_id = update.effective_user.id
    if user_id in user_sessions:
        await update.message.reply_text("✅ আপনি ইতিমধ্যেই লগইন করা আছেন।", reply_markup=reply_markup)
        return ConversationHandler.END
    await update.message.reply_text("📝 আপনার Twilio Account SID এবং Auth Token এখানে দিন, একটি স্পেস দিয়ে আলাদা করে (যেমন: ACxxxxxxxxxxxxxx xxxxxxxxxxxxxx ):")
    return LOGIN_AWAITING_CREDENTIALS

async def receive_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text.strip()
    
    main_menu_button_texts = [LOGIN_TEXT, BUY_TEXT, SHOW_MESSAGES_TEXT, REMOVE_NUMBER_TEXT, LOGOUT_TEXT]
    if user_input in main_menu_button_texts: 
        await update.message.reply_text(
            f"✋ এই সময়ে বাটন না চেপে, অনুগ্রহ করে আপনার Twilio Account SID এবং Auth Token টাইপ করে পাঠান।"
            f" আবার চেষ্টা করতে '{LOGIN_TEXT}' বাটন চাপুন।"
        )
        return ConversationHandler.END  

    try:
        sid, auth = user_input.split(maxsplit=1)
        if not (sid.startswith("AC") and len(sid) == 34):
            await update.message.reply_text(
                f"⚠️ আপনার দেওয়া SID ({sid}) সঠিক ফরম্যাটে নেই বলে মনে হচ্ছে। "
                f"অনুগ্রহ করে আবার '{LOGIN_TEXT}' বাটন চেপে সঠিক SID ও Auth Token দিন।"
            )
            return ConversationHandler.END

        client = Client(sid, auth)
        client.api.accounts(sid).fetch() 
        user_sessions[user_id] = {'sid': sid, 'auth': auth, 'client': client, 'number': None}
        await update.message.reply_text("🎉 লগইন সফল হয়েছে!", reply_markup=reply_markup)
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text(
            f"⚠️ SID এবং Auth Token সঠিকভাবে পাওয়া যায়নি। অনুগ্রহ করে SID, তারপর একটি স্পেস, তারপর Auth Token দিন। "
            f"আবার চেষ্টা করতে '{LOGIN_TEXT}' বাটন চাপুন।"
        )
        return ConversationHandler.END
    except Exception as e: 
        logger.error(f"Login failed for user {user_id} (SID: {sid if 'sid' in locals() else 'N/A'}): {e}")
        await update.message.reply_text(f"❌ আপনার দেওয়া SID এবং Auth Token দিয়ে লগইন করতে ব্যর্থ হয়েছে। অনুগ্রহ করে আবার '{LOGIN_TEXT}' বাটন চেপে চেষ্টা করুন।")
        return ConversationHandler.END

async def logout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context): return 
    user_id = update.effective_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        await update.message.reply_text("✅ আপনি সফলভাবে লগ আউট হয়েছেন।", reply_markup=reply_markup)
    else:
        await update.message.reply_text("ℹ️ আপনি লগইন অবস্থায় নেই।", reply_markup=reply_markup)

async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context): return 
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    
    client = user_sessions[user_id]['client']
    try:
        available_numbers = client.available_phone_numbers("CA").local.list(limit=5) 
        if not available_numbers:
            await update.message.reply_text("😔 এই মুহূর্তে কোনো উপলভ্য নম্বর নেই।")
            return

        keyboard = []
        for number_obj in available_numbers:
            button_text = f"🛒 কিনুন {number_obj.phone_number}"
            callback_data = f"purchase_{number_obj.phone_number}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        if not keyboard:
              await update.message.reply_text("😔 নম্বর পাওয়া গেলেও বাটন তৈরি করা যায়নি।")
              return

        inline_reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("📞 উপলব্ধ নম্বর নিচে দেওয়া হলো। পছন্দের নম্বরের পাশের 'কিনুন' বাটনে ক্লিক করুন:", reply_markup=inline_reply_markup)

    except Exception as e:
        logger.error(f"Failed to fetch numbers for user {user_id}: {e}")
        await update.message.reply_text("⚠️ নম্বর আনতে সমস্যা হয়েছে। সম্ভবত আপনার অ্যাকাউন্টে এই অঞ্চলের নম্বর কেনার অনুমতি নেই অথবা অন্য কোনো সমস্যা।")

async def purchase_number_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.from_user:
        logger.warning("purchase_number_callback_handler: query or query.from_user not found.")
        if query: await query.answer("একটি সমস্যা হয়েছে।")
        return

    if not await ensure_user_has_joined(query, context): 
        await query.answer("অনুগ্রহ করে প্রথমে চ্যানেল ও গ্রুপে জয়েন করে ভেরিফাই করুন।", show_alert=True)
        return
        
    await query.answer() 
    user_id = query.from_user.id
    
    if user_id not in user_sessions:
        try:
            await query.edit_message_text(text=f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        except BadRequest: pass 
        return

    if user_sessions[user_id].get('number'):
        current_number = user_sessions[user_id]['number']
        try:
            await query.edit_message_text(text=f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর ({current_number}) কেনা আছে। নতুন নম্বর কিনতে আগেরটি '{REMOVE_NUMBER_TEXT}' ব্যবহার করে মুছুন।")
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
        success_message = f"🛍️ নম্বর {incoming_number.phone_number} সফলভাবে কেনা হয়েছে!"
        await query.edit_message_text(text=success_message, reply_markup=None)
    except Exception as e:
        logger.error(f"Failed to buy number {number_to_buy} for user {user_id}: {e}")
        error_message = f"❌ এই নম্বরটি ({number_to_buy}) কিনতে সমস্যা হয়েছে।"
        if "violates a uniqueness constraint" in str(e).lower() or "already provisioned" in str(e).lower():
            error_message += " এটি ইতিমধ্যেই আপনার অ্যাকাউন্টে রয়েছে অথবা অন্য কেউ ব্যবহার করছে।"
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
            await update.message.reply_text("📪 আপনার এই নম্বরে কোনো মেসেজ পাওয়া যায়নি।")
        else:
            response_msg = f"📨 আপনার নম্বর ({twilio_number_str}) এ আসা সাম্প্রতিক মেসেজ:\n\n"
            for msg_instance in messages:
                response_msg += f"➡️ ** প্রেরক:** {msg_instance.from_}\n📝 ** বার্তা:** {msg_instance.body}\n🗓️ ** সময়:** {msg_instance.date_sent.strftime('%Y-%m-%d %H:%M:%S') if msg_instance.date_sent else 'N/A'}\n---\n"
            await update.message.reply_text(response_msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Failed to fetch messages for user {user_id} on number {twilio_number_str}: {e}")
        await update.message.reply_text("⚠️ মেসেজ আনতে সমস্যা হয়েছে।")

async def remove_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context): return 
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        await update.message.reply_text(f"🔒 অনুগ্রহ করে প্রথমে '{LOGIN_TEXT}' ব্যবহার করে লগইন করুন।")
        return
    
    active_number = user_sessions[user_id].get('number')
    if not active_number:
        await update.message.reply_text("ℹ️ আপনার অ্যাকাউন্টে রিমুভ করার মতো কোনো সক্রিয় নম্বর নেই।")
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
    if not query or not query.from_user:
        logger.warning("confirm_remove_callback_handler: query or query.from_user not found.")
        if query: await query.answer("একটি সমস্যা হয়েছে।")
        return

    if not await ensure_user_has_joined(query, context): 
        await query.answer("অনুগ্রহ করে প্রথমে চ্যানেল ও গ্রুপে জয়েন করে ভেরিফাই করুন।", show_alert=True)
        return

    await query.answer()
    user_id = query.from_user.id
    action = query.data

    if user_id not in user_sessions or not user_sessions[user_id].get('number'):
        try:
            await query.edit_message_text(text="🚫 এই অনুরোধটি আর বৈধ নয় অথবা আপনার লগইন সেশন বা সক্রিয় নম্বর নেই।")
        except BadRequest: pass
        return

    number_to_remove = user_sessions[user_id]['number']

    if action == "confirm_remove_yes":
        client = user_sessions[user_id]['client']
        try:
            logger.info(f"User {user_id} confirmed removal for number: {number_to_remove}")
            incoming_phone_numbers = client.incoming_phone_numbers.list(phone_number=number_to_remove, limit=1)
            if not incoming_phone_numbers:
                await query.edit_message_text(text=f"❓ নম্বর {number_to_remove} আপনার অ্যাকাউন্টে পাওয়া যায়নি।")
                user_sessions[user_id]['number'] = None 
                return

            number_sid_to_delete = incoming_phone_numbers[0].sid
            client.incoming_phone_numbers(number_sid_to_delete).delete()
            user_sessions[user_id]['number'] = None
            await query.edit_message_text(text=f"🗑️ নম্বর {number_to_remove} সফলভাবে রিমুভ করা হয়েছে!")
        except Exception as e:
            logger.error(f"Failed to remove number {number_to_remove} for user {user_id} after confirmation: {e}")
            await query.edit_message_text(text="⚠️ নম্বর রিমুভ করতে সমস্যা হয়েছে।")
    
    elif action == "confirm_remove_no":
        await query.edit_message_text(text="🚫 নম্বর রিমুভ করার প্রক্রিয়া বাতিল করা হয়েছে।")


async def handle_general_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_user_has_joined(update, context): return 

    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id in user_sessions and text.startswith('+') and len(text) > 7 and text[1:].isdigit() and user_sessions[user_id].get('client'):
        number_to_buy = text
        client = user_sessions[user_id]['client']
        if user_sessions[user_id].get('number'):
            await update.message.reply_text(f"ℹ️ আপনার ইতিমধ্যেই একটি নম্বর ({user_sessions[user_id]['number']}) কেনা আছে। নতুন নম্বর কিনতে আগেরটি '{REMOVE_NUMBER_TEXT}' ব্যবহার করে মুছুন।")
            return
        try:
            logger.info(f"User {user_id} attempting to purchase {number_to_buy} via general text.")
            incoming_number = client.incoming_phone_numbers.create(phone_number=number_to_buy)
            user_sessions[user_id]['number'] = incoming_number.phone_number
            await update.message.reply_text(f"🛍️ নম্বর {incoming_number.phone_number} সফলভাবে কেনা হয়েছে। (সরাসরি ইনপুট)", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to buy number {number_to_buy} for user {user_id} via general text: {e}")
            error_message = f"❌ এই নম্বরটি ({number_to_buy}) কিনতে সমস্যা হয়েছে। (সরাসরি ইনপুট)"
            if "violates a uniqueness constraint" in str(e).lower() or "already provisioned" in str(e).lower():
                error_message = f"⚠️ নম্বর {number_to_buy} ইতিমধ্যেই আপনার অ্যাকাউন্টে রয়েছে অথবা অন্য কেউ ব্যবহার করছে।"
            elif "AreaCode is required for an address-based search" in str(e):
                 error_message = "ℹ️ নম্বর কেনার জন্য এরিয়া কোডসহ নম্বর দিন অথবা উপলভ্য নম্বর তালিকা থেকে বাছাই করুন।"
            await update.message.reply_text(error_message)
    else:
        if context.user_data.get(USER_COMPLETED_ALL_JOINS_KEY, False):
            await update.message.reply_text("🤔 আপনার অনুরোধ বুঝতে পারিনি। অনুগ্রহ করে মেনু থেকে একটি অপশন বেছে নিন।", reply_markup=reply_markup)


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

    app.add_handler(MessageHandler(filters.Regex(f'^{LOGOUT_TEXT}$'), logout_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{BUY_TEXT}$'), buy_handler)) 
    app.add_handler(MessageHandler(filters.Regex(f'^{REMOVE_NUMBER_TEXT}$'), remove_number_handler))
    app.add_handler(MessageHandler(filters.Regex(f'^{SHOW_MESSAGES_TEXT}$'), show_messages_handler))
    
    app.add_handler(CallbackQueryHandler(purchase_number_callback_handler, pattern='^purchase_'))
    app.add_handler(CallbackQueryHandler(confirm_remove_callback_handler, pattern='^confirm_remove_(yes|no)$'))
    app.add_handler(CallbackQueryHandler(verify_all_joins_callback, pattern=f'^{VERIFY_ALL_JOINS_CALLBACK_DATA}$'))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_general_text))

    print("Flask keep-alive server চালু হচ্ছে...")
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True 
    flask_thread.start()

    logger.info("🤖 Bot starting to poll... (Flask keep-alive server running on a separate thread)")
    app.run_polling()
