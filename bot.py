# bot.py
import os
import json
import base64
import hashlib
import time
import threading
import requests
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import ForceReply, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
import stripe
from cryptography.fernet import Fernet

from config import *

app = Client("mogambo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
stripe.api_key = STRIPE_KEY
state = {}

# === BIN CHECKER (NEW & WORKING) ===
def check_bin(bin_digits):
    bin_str = str(bin_digits)[:6]
    if len(bin_str) < 6:
        return {"error": "BIN must be 6 digits"}

    headers = {'User-Agent': 'MogamboBot/1.0'}
    apis = [
        f"https://binlist.io/lookup/{bin_str}/",
        f"https://lookup.binlist.net/{bin_str}",
        "https://neutrinoapi.net/bin-lookup"
    ]

    for url in apis:
        try:
            if "neutrinoapi" in url:
                payload = {"bin-number": bin_str}
                response = requests.post(url, data=payload, headers=headers, timeout=7)
            else:
                response = requests.get(url, headers=headers, timeout=7)

            if response.status_code == 200:
                data = response.json()

                # binlist.io
                if 'valid' in data:
                    return {
                        'brand': data.get('scheme', 'Unknown').title(),
                        'type': data.get('type', 'Unknown').title(),
                        'bank': data.get('bank', {}).get('name', 'Unknown'),
                        'country': data.get('country', {}).get('name', 'Unknown'),
                        'valid': data['valid']
                    }
                # binlist.net
                elif 'scheme' in data:
                    return {
                        'brand': data.get('scheme', 'Unknown').title(),
                        'type': data.get('type', 'Unknown').title(),
                        'bank': data.get('bank', {}).get('name', 'Unknown'),
                        'country': data.get('country', {}).get('name', 'Unknown'),
                        'valid': True
                    }
        except:
            continue

    return {"error": "BIN not found"}

def format_bin_result(info):
    if 'error' in info:
        return f"Error: {info['error']}"
    return (
        f"**BIN Lookup Result**\n"
        f"• Brand: `{info['brand']}`\n"
        f"• Type: `{info['type']}`\n"
        f"• Bank: `{info['bank']}`\n"
        f"• Country: `{info['country']}`\n"
        f"• Valid: `{'Yes' if info['valid'] else 'No'}`"
    )

# === PHONE LOOKUP (WORKING) ===
def check_phone(number):
    if not number.startswith('+'):
        number = '+' + number.replace('+', '')

    try:
        url = f"https://api.apilayer.com/numverify?number={number}"
        headers = {"apikey": "demo"}  # Free tier
        res = requests.get(url, headers=headers, timeout=7)
        if res.status_code == 200:
            data = res.json()
            if data.get('valid'):
                return {
                    'valid': True,
                    'country': data.get('country_name', 'Unknown'),
                    'carrier': data.get('carrier', 'Unknown'),
                    'type': data.get('line_type', 'Unknown'),
                    'location': data.get('location', 'Unknown')
                }
    except:
        pass

    return {"error": "Phone info not available"}

def format_phone_result(info):
    if 'error' in info:
        return f"Error: {info['error']}"
    return (
        f"**Phone Lookup**\n"
        f"• Valid: `Yes`\n"
        f"• Country: `{info['country']}`\n"
        f"• Carrier: `{info['carrier']}`\n"
        f"• Type: `{info['type']}`\n"
        f"• Location: `{info['location']}`"
    )

# === ENCRYPTION ===
def get_fernet():
    key = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))

def save_data(entry):
    f = get_fernet()
    path = 'my_personal_card_data.enc'
    lst = []
    if os.path.exists(path):
        try:
            with open(path, 'rb') as file:
                lst = json.loads(f.decrypt(file.read()))
        except: pass
    lst.append(entry)
    with open(path, 'wb') as file:
        file.write(f.encrypt(json.dumps(lst).encode()))

def load_data():
    path = 'my_personal_card_data.enc'
    if not os.path.exists(path): return []
    f = get_fernet()
    try:
        with open(path, 'rb') as file:
            return json.loads(f.decrypt(file.read()))
    except: return []

# === CARD AUTH ===
def do_auth(message, cmd, info, card, mm, yy, cvc, bin_info=None, phone_info=None):
    last4 = card[-4:]
    success = False
    result = ""

    try:
        mm, yy = int(mm), int(yy)
        if yy < 100: yy += 2000

        pm = stripe.PaymentMethod.create(
            type="card",
            card={"number": card, "exp_month": mm, "exp_year": yy, "cvc": cvc}
        )

        if cmd == 'check':
            si = stripe.SetupIntent.create(payment_method=pm.id, confirm=True, usage="off_session")
            success = si.status in ['succeeded', 'requires_capture']
            result = f"Card is valid! (Last 4: {last4})" if success else "Validation failed."
        elif cmd == 'hold':
            for _ in range(15):
                pi = stripe.PaymentIntent.create(
                    amount=1, currency="usd", payment_method=pm.id, confirm=True, capture_method="manual"
                )
                if pi.status != "requires_capture":
                    raise Exception("Hold failed")
            result = f"15x $0.01 hold placed. (Last 4: {last4})"
            success = True
    except Exception as e:
        result = f"Error: {str(e)[:100]}"

    # Combine
    msg = result
    if bin_info: msg = format_bin_result(bin_info) + "\n\n" + msg
    if phone_info: msg = format_phone_result(phone_info) + "\n\n" + msg

    message.reply(msg, parse_mode=ParseMode.MARKDOWN)

    save_data({
        'timestamp': datetime.now().isoformat(),
        'command': cmd,
        'name': info.get('name', ''),
        'phone': info.get('phone', ''),
        'card': card,
        'bin_info': bin_info,
        'phone_info': phone_info,
        'result': 'success' if success else 'failed'
    })

# === PROCESS CARD ===
def process_card(message, cmd, info, card, mm, yy, cvc):
    bin_info = check_bin(card[:6])
    message.reply(format_bin_result(bin_info), parse_mode=ParseMode.MARKDOWN)

    phone = info.get('phone', '')
    phone_info = check_phone(phone) if phone else None

    if cmd == 'check':
        do_auth(message, cmd, info, card, mm, yy, cvc, bin_info, phone_info)
    else:  # hold
        state['pending'] = {
            'cmd': cmd, 'info': info, 'card': card, 'mm': mm, 'yy': yy, 'cvc': cvc,
            'bin_info': bin_info, 'phone_info': phone_info
        }
        timer = threading.Timer(60, lambda: state.pop('pending', None) or message.reply("Hold cancelled (timeout)."))
        timer.start()
        state['timer'] = timer
        message.reply("Send **YES** to confirm 15x $0.01 hold.", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply())

# === FILTERS ===
text_non_command = filters.create(lambda _, __, m: m.text and m.from_user.id == OWNER_ID and not m.command)

# === /bin WITH BUTTONS ===
@app.on_message(filters.command("bin") & filters.user(OWNER_ID))
def bin_menu(client, message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Visa", callback_data="bin:424242")],
        [InlineKeyboardButton("MasterCard", callback_data="bin:555555")],
        [InlineKeyboardButton("Amex", callback_data="bin:378282")],
        [InlineKeyboardButton("Custom BIN", callback_data="bin:custom")]
    ])
    message.reply("*BIN Checker*\nClick or reply with 6 digits.", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

@app.on_callback_query(filters.regex(r"^bin:") & filters.user(OWNER_ID))
def bin_callback(client, query):
    data = query.data.split(":", 1)[1]
    if data == "custom":
        query.message.reply("Reply with 6-digit BIN:", reply_markup=ForceReply())
    else:
        info = check_bin(data)
        query.message.reply(format_bin_result(info), parse_mode=ParseMode.MARKDOWN)
    query.answer()

# === /phone ===
@app.on_message(filters.command("phone") & filters.user(OWNER_ID))
def phone_lookup(client, message):
    if len(message.command) < 2:
        message.reply("Usage: `/phone +1234567890`", parse_mode=ParseMode.MARKDOWN)
        return
    num = message.text.split(maxsplit=1)[1]
    info = check_phone(num)
    message.reply(format_phone_result(info), parse_mode=ParseMode.MARKDOWN)

# === /start ===
@app.on_message(filters.command("start") & filters.user(OWNER_ID))
def start(client, message):
    message.reply(
        "*Mogambo Pro Bot*\n\n"
        "`/bin` → BIN Checker (Buttons)\n"
        "`/phone <num>` → Phone Info\n"
        "`/check` → Validate + BIN + Phone\n"
        "`/hold` → 15x $0.01 Hold\n"
        "`/my_data` → View Saved\n\n"
        "_Sirf apna card daalo!_",
        parse_mode=ParseMode.MARKDOWN
    )

# === /check /hold ===
@app.on_message(filters.command(["check", "hold"]) & filters.user(OWNER_ID))
def cmd_handler(client, message):
    cmd = message.command[0]
    args = message.command[1:]

    if len(args) == 4:
        card, mm, yy, cvc = args
        info = state.pop('info', {}) if 'info' in state else {}
        process_card(message, cmd, info, card, mm, yy, cvc)
    elif not args:
        state.update({'command': cmd, 'step': 'name', 'info': {}})
        message.reply("Enter *Full Name*:", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply())
    else:
        message.reply("Format: `/{cmd} card mm yy cvc`", parse_mode=ParseMode.MARKDOWN)

# === /my_data ===
@app.on_message(filters.command("my_data") & filters.user(OWNER_ID))
def my_data(client, message):
    data = load_data()
    if not data:
        message.reply("No data.")
        return
    text = "*Saved Entries*\n\n"
    for i, e in enumerate(data, 1):
        last4 = e['card'][-4:] if e.get('card') else 'N/A'
        bin_str = f"{e['bin_info'].get('brand','?')} ({e['bin_info'].get('bank','?')})" if e.get('bin_info') else 'N/A'
        phone_str = f"{e['phone_info'].get('carrier','?')} ({e['phone_info'].get('country','?')})" if e.get('phone_info') else 'N/A'
        text += f"*{i}. {e['timestamp'][:19].replace('T',' ')}*\n"
        text += f"Cmd: `{e['command']}` | Card: `****{last4}`\n"
        text += f"BIN: `{bin_str}`\n"
        text += f"Phone: `{e.get('phone','N/A')}` | `{phone_str}`\n"
        text += f"Result: `{e['result']}`\n\n"
    message.reply(text, parse_mode=ParseMode.MARKDOWN)

# === TEXT HANDLER ===
@app.on_message(text_non_command)
def text_input(client, message):
    if 'pending' in state and message.text.upper() == 'YES':
        pending = state.pop('pending')
        state.pop('timer', None)
        do_auth(message, **pending)
        return

    if 'step' not in state:
        # Custom BIN reply
        if message.reply_to_message and "Reply with 6-digit BIN" in message.reply_to_message.text:
            try:
                info = check_bin(int(message.text.strip()))
                message.reply(format_bin_result(info), parse_mode=ParseMode.MARKDOWN)
            except:
                message.reply("Invalid BIN.")
            return
        return

    step = state['step']
    text = message.text.strip()
    info = state['info']

    steps = ['name', 'zip', 'address', 'phone', 'email']
    if step == steps[-1]:
        info[step] = text
        cmd = state.pop('command')
        state.pop('step')
        message.reply(f"Info saved! Send: `/{cmd} card mm yy cvc`", parse_mode=ParseMode.MARKDOWN)
    else:
        info[step] = text
        state['step'] = steps[steps.index(step) + 1]
        message.reply(f"Enter *{state['step'].title() if state['step'] != 'zip' else 'ZIP Code'}^:*", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply())

# === RUN ===
if __name__ == "__main__":
    print("Mogambo Bot Starting...")
    app.run()
