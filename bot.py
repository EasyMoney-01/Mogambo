# bot.py
import os
import json
import base64
import hashlib
import time
import threading
import requests  # ← NEW: For BIN APIs
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import ForceReply
from pyrogram.enums import ParseMode
import stripe
from cryptography.fernet import Fernet

from config import *

app = Client("mogambo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
stripe.api_key = STRIPE_KEY
state = {}

# === BIN CHECKER FUNCTION (NEW) ===
def check_bin(bin_digits):
    """Check BIN using 3 free APIs (fallback if one fails)"""
    bin_str = str(bin_digits)[:6]  # First 6 digits only
    if len(bin_str) < 6:
        return {"error": "BIN must be at least 6 digits"}
    
    apis = [
        f"https://lookup.binlist.net/{bin_str}",  # API 1: binlist.net
        f"https://api.freebinchecker.com/bin/{bin_str}",  # API 2: freebinchecker.com
        f"https://api.bincodes.com/bin/?format=json&bin={bin_str}"  # API 3: bincodes.com
    ]
    
    for api_url in apis:
        try:
            response = requests.get(api_url, timeout=5)
            if response.status_code == 200:
                data = response.json()
                # Parse common fields (adapt to API response)
                if 'scheme' in data or 'card' in data:
                    # freebinchecker format
                    return {
                        'brand': data.get('card', {}).get('scheme', 'Unknown'),
                        'type': data.get('card', {}).get('type', 'Unknown'),
                        'bank': data.get('issuer', {}).get('name', 'Unknown'),
                        'country': data.get('country', {}).get('name', 'Unknown'),
                        'valid': data.get('valid', False)
                    }
                elif 'number' in data:
                    # binlist.net format
                    return {
                        'brand': data.get('scheme', 'Unknown'),
                        'type': data.get('type', 'Unknown'),
                        'bank': data.get('bank', {}).get('name', 'Unknown'),
                        'country': data.get('country', {}).get('name', 'Unknown'),
                        'valid': True
                    }
                elif 'bin' in data:
                    # bincodes format
                    return {
                        'brand': data.get('card', 'Unknown'),
                        'type': data.get('type', 'Unknown'),
                        'bank': data.get('bank', 'Unknown'),
                        'country': data.get('country', 'Unknown'),
                        'valid': data.get('valid', False)
                    }
        except Exception:
            continue  # Try next API
    
    return {"error": "BIN not found in any database"}

def format_bin_result(bin_info):
    """Format BIN result for message"""
    if 'error' in bin_info:
        return f"BIN Error: {bin_info['error']}"
    
    return (
        f"**BIN Info:**\n"
        f"• Brand: `{bin_info['brand']}`\n"
        f"• Type: `{bin_info['type']}`\n"
        f"• Bank: `{bin_info['bank']}`\n"
        f"• Country: `{bin_info['country']}`\n"
        f"• Valid: {'Yes' if bin_info['valid'] else 'No'}"
    )

# === ENCRYPTION ===
def get_fernet():
    key = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))

def save_data(entry):
    f = get_fernet()
    if os.path.exists('my_personal_card_data.enc'):
        with open('my_personal_card_data.enc', 'rb') as file:
            enc = file.read()
        try:
            dec = f.decrypt(enc)
            lst = json.loads(dec)
        except:
            lst = []
    else:
        lst = []
    lst.append(entry)
    enc_new = f.encrypt(json.dumps(lst).encode())
    with open('my_personal_card_data.enc', 'wb') as file:
        file.write(enc_new)

def load_data():
    if not os.path.exists('my_personal_card_data.enc'):
        return []
    f = get_fernet()
    try:
        with open('my_personal_card_data.enc', 'rb') as file:
            enc = file.read()
        dec = f.decrypt(enc)
        return json.loads(dec)
    except:
        return []

# === CARD PROCESSING ===
def do_auth(message, cmd, info, card, mm, yy, cvc, bin_info=None):
    last4 = card[-4:]
    success = False
    result_msg = ""
    try:
        mm = int(mm)
        yy = int(yy)
        if yy < 100:
            yy += 2000

        pm = stripe.PaymentMethod.create(
            type="card",
            card={"number": card, "exp_month": mm, "exp_year": yy, "cvc": cvc},
        )

        if cmd == 'check':
            si = stripe.SetupIntent.create(payment_method=pm.id, confirm=True, usage="off_session")
            if si.status in ['succeeded', 'requires_capture']:
                result_msg = f"Card is valid! (Last 4: {last4})"
                success = True
            else:
                result_msg = "Validation failed."
        elif cmd == 'hold':
            for _ in range(15):
                pi = stripe.PaymentIntent.create(
                    amount=1, currency="usd", payment_method=pm.id, confirm=True, capture_method="manual"
                )
                if pi.status != "requires_capture":
                    raise Exception("Auth failed.")
            result_msg = f"15x $0.01 auth sent. Card (Last 4: {last4}) on hold (1-24 hrs)."
            success = True

    except stripe.error.CardError as e:
        result_msg = f"Card Error: {e.user_message} (Code: {e.code})"
    except stripe.error.StripeError as e:
        result_msg = f"Stripe Error: {e.user_message or str(e)}"
    except Exception as e:
        result_msg = f"Error: {str(e)}"

    # BIN info if available
    full_msg = result_msg
    if bin_info:
        full_msg = format_bin_result(bin_info) + "\n\n" + result_msg

    message.reply(full_msg, parse_mode=ParseMode.MARKDOWN)

    entry = {
        'timestamp': datetime.now().isoformat(),
        'command': cmd,
        'name': info.get('name', '') if info else '',
        'zip': info.get('zip', '') if info else '',
        'address': info.get('address', '') if info else '',
        'phone': info.get('phone', '') if info else '',
        'email': info.get('email', '') if info else '',
        'card': card,
        'exp_month': mm,
        'exp_year': yy,
        'cvc': cvc,
        'bin_info': bin_info,  # ← NEW: Save BIN data
        'result': 'success' if success else 'failed'
    }
    save_data(entry)

# === CARD PROCESSING WITH CONFIRM ===
def process_card(message, cmd, info, card, mm, yy, cvc):
    # ← NEW: BIN check before auth
    bin_digits = int(card[:6])
    bin_info = check_bin(bin_digits)
    message.reply(format_bin_result(bin_info), parse_mode=ParseMode.MARKDOWN)
    
    if cmd == 'check':
        do_auth(message, cmd, info, card, mm, yy, cvc, bin_info)
    elif cmd == 'hold':
        state['pending'] = {'cmd': cmd, 'info': info, 'card': card, 'mm': mm, 'yy': yy, 'cvc': cvc, 'bin_info': bin_info}
        def timeout():
            if 'pending' in state:
                message.reply("Hold cancelled (60s timeout).")
                state.pop('pending', None)
                state.pop('timer', None)
        timer = threading.Timer(60, timeout)
        timer.start()
        state['timer'] = timer
        message.reply("Send **YES** to confirm 15x $0.01 hold.", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply())

# === CUSTOM FILTER: Text + Owner + Not Command ===
def is_text_non_command(_, __, m):
    return m.text and m.from_user and m.from_user.id == OWNER_ID and not m.command

text_non_command = filters.create(is_text_non_command)

# === NEW: BIN COMMAND HANDLER ===
@app.on_message(filters.command("bin") & filters.user(OWNER_ID))
def bin_handler(client, message):
    args = message.command[1:]
    if not args:
        message.reply("Usage: `/bin <first6digits>` e.g., `/bin 424242`", parse_mode=ParseMode.MARKDOWN)
        return
    
    try:
        bin_digits = int(''.join(args))
        bin_info = check_bin(bin_digits)
        message.reply(format_bin_result(bin_info), parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        message.reply("Invalid BIN. Use numbers only (first 6 digits).")

# === HANDLERS ===
@app.on_message(filters.command("start") & filters.user(OWNER_ID))
def start(client, message):
    message.reply(
        "*Mogambo Full Info Card Hold Bot*\n\n"
        "`/bin <6digits>` → BIN checker (NEW!)\n"  # ← NEW
        "`/check` → $0.00 validation + BIN\n"
        "`/hold` → 15x $0.01 → temp hold + BIN\n"
        "`/my_data` → view saved\n\n"
        "_Sirf apna card daalo!_",
        parse_mode=ParseMode.MARKDOWN
    )

@app.on_message(filters.command(["check", "hold"]) & filters.user(OWNER_ID))
def command_handler(client, message):
    cmd = message.command[0]
    args = message.command[1:]

    if len(args) == 4:
        card, mm, yy, cvc = args
        info = state.pop('info', None) if 'info' in state and state.get('command') == cmd else None
        if 'command' in state: state.pop('command')
        process_card(message, cmd, info, card, mm, yy, cvc)
    elif len(args) == 0:
        if 'step' in state:
            message.reply("Complete current info collection.")
            return
        state['command'] = cmd
        state['step'] = 'name'
        state['info'] = {}
        message.reply("Enter *Full Name*:", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply())
    else:
        message.reply(
            f"Invalid format!\n\n"
            f"Direct: `/{cmd} 4242424242424242 12 25 123`\n"
            f"Or: `/{cmd}` → fill info step-by-step",
            parse_mode=ParseMode.MARKDOWN
        )

@app.on_message(filters.command("my_data") & filters.user(OWNER_ID))
def my_data_handler(client, message):
    data = load_data()
    if not data:
        message.reply("No data saved yet.")
        return

    text = "*Saved Card Data*\n\n"
    for i, entry in enumerate(data, 1):
        last4 = entry['card'][-4:] if entry.get('card') else 'N/A'
        bin_info = entry.get('bin_info', {})
        bin_str = f"{bin_info.get('brand', 'Unknown')} ({bin_info.get('bank', 'Unknown')})" if bin_info else 'N/A'
        text += f"*{i}. {entry['timestamp'][:19].replace('T', ' ')}*\n"
        text += f"Cmd: `{entry['command']}`\n"
        text += f"Name: `{entry.get('name', 'N/A')}`\n"
        text += f"ZIP: `{entry.get('zip', 'N/A')}`\n"
        text += f"Address: `{entry.get('address', 'N/A')}`\n"
        text += f"Phone: `{entry.get('phone', 'N/A')}`\n"
        text += f"Email: `{entry.get('email', 'N/A')}`\n"
        text += f"Card: `****{last4}` | BIN: `{bin_str}`\n"  # ← NEW: Show BIN in data
        text += f"Result: `{entry['result']}`\n\n"

    if len(text) > 4000:
        for part in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            message.reply(part, parse_mode=ParseMode.MARKDOWN)
    else:
        message.reply(text, parse_mode=ParseMode.MARKDOWN)

@app.on_message(text_non_command)
def text_handler(client, message):
    if 'pending' in state:
        if message.text.strip().upper() == 'YES':
            if 'timer' in state: state['timer'].cancel()
            pending = state.pop('pending')
            state.pop('timer', None)
            do_auth(message, pending['cmd'], pending['info'], pending['card'],
                    pending['mm'], pending['yy'], pending['cvc'], pending.get('bin_info'))
        else:
            if 'timer' in state: state['timer'].cancel()
            state.pop('pending', None)
            state.pop('timer', None)
            message.reply("Hold cancelled.")
        return

    if 'step' not in state:
        return

    step = state['step']
    text = message.text.strip()

    if step == 'name':
        state['info']['name'] = text
        state['step'] = 'zip'
        message.reply("Enter *ZIP Code*:", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply())
    elif step == 'zip':
        state['info']['zip'] = text
        state['step'] = 'address'
        message.reply("Enter *Address*:", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply())
    elif step == 'address':
        state['info']['address'] = text
        state['step'] = 'phone'
        message.reply("Enter *Phone Number*:", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply())
    elif step == 'phone':
        state['info']['phone'] = text
        state['step'] = 'email'
        message.reply("Enter *Email*:", parse_mode=ParseMode.MARKDOWN, reply_markup=ForceReply())
    elif step == 'email':
        state['info']['email'] = text
        cmd = state.pop('command')
        state.pop('step')
        message.reply(
            f"Info saved!\n\n"
            f"Send card:\n`/{cmd} <card> <mm> <yy> <cvc>`\n"
            f"Example: `/{cmd} 4242424242424242 12 25 123`",
            parse_mode=ParseMode.MARKDOWN
        )

# === RUN ===
if __name__ == "__main__":
    print("Mogambo Bot Starting...")
    app.run()
