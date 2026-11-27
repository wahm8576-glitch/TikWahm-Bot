#!/usr/bin/env python3
"""نسخة محدثة لبوت TikWahm مع تدوير تلقائي للبروكسي بعد كل محاولة."""
import asyncio
from collections import OrderedDict
import base64
import hashlib
import hmac
import html
import json
import math
import os
import re
import secrets
import socket
import urllib.error
import urllib.request
from pathlib import Path
import time
from urllib.parse import quote, urlencode, urlparse
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
import uvicorn

try:
    import firebase_admin
    from firebase_admin import credentials as firebase_credentials, firestore
    from google.api_core.exceptions import AlreadyExists
except ImportError:  # Firebase is optional for local/mock runs.
    firebase_admin = None
    firebase_credentials = firestore = AlreadyExists = None

from telebot import asyncio_helper, types
from telebot.async_telebot import AsyncTeleBot
import pycountry

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

bot = AsyncTeleBot(BOT_TOKEN)
WEBHOOK_SECRET = ""
WEBHOOK_SECRET_SEED = os.environ.get("WEBHOOK_SECRET", "").strip()
ADMIN_PANEL_COMMAND = os.environ.pop("TIKWAHM_COUNTRY_ADMIN_COMMAND", "").strip()
WEBHOOK_PATH = "/telegram/webhook"
FINTOK_URL = "https://fintok.neocities.org/"
FINTOK_API_URL = "https://tiktokinfov10.itfreeforever.workers.dev"
BRIDGLY_FOLLOWERS_URL = "https://api.bridgly.app/tiktok/get-user-followers"
BRIDGLY_FOLLOWING_URL = "https://api.bridgly.app/tiktok/get-user-following"
BRIDGLY_API_KEY = os.environ.get("BRIDGLY_API_KEY", "").strip()
BRIDGLY_LIST_CACHE_MAX_AGE_HOURS = 24
BRIDGLY_LIST_CACHE_TTL_SECONDS = 10 * 60
BRIDGLY_REQUEST_TIMEOUT_SECONDS = 8
MAX_FOLLOWER_NAMES = 5
# Public client-side request salt from FinTok's own JavaScript; not a credential.
FINTOK_SIGNATURE_SALT = "x2x7x1x90x79x24x001x"
MAX_LOOKUP_SECONDS = 15
EXPECTED_LOOKUP_SECONDS = 10
CACHE_TTL_SECONDS = 10 * 60
USER_SEARCH_COOLDOWN_SECONDS = 20
# Query FinTok's JSON endpoints directly; a short per-proxy timeout keeps lookups responsive.
SOURCE_TIMEOUT_SECONDS = 8
MAX_SOURCE_RESPONSE_BYTES = 1_000_000
PROGRESS_ANIMATION_INTERVAL = 1.0
LOADING_GIF_PATH = os.path.join(os.path.dirname(__file__), "tikwahm_loading.gif")
PROGRESS_MEDIA_ID = "tikwahm_loading"
PROGRESS_ATTACHMENT_NAME = "loading_animation"
_LOOKUP_CACHE = {}
_LOOKUP_TASKS = {}
_RETRY_IN_PROGRESS = set()
_BRIDGLY_LIST_CACHE = OrderedDict()
_BRIDGLY_LIST_TASKS = {}
_BRIDGLY_LIST_LOCK = asyncio.Lock()
_COUNTRY_LOOKUP_CACHE = {}
_COUNTRY_ORDER_CACHE = {}
_COUNTRY_SUBSCRIPTION_CACHE = {}
_COUNTRY_PENDING_ORDER_CACHE = {}
_COUNTRY_PROCESSED_PAYMENT_CACHE = set()
_COUNTRY_ACCESS_SETTINGS_CACHE = {"mode": "free", "stars_price": 0}
_COUNTRY_ORDER_LOCK = asyncio.Lock()
_COUNTRY_PAYMENT_LOCK = asyncio.Lock()
_SUPPORT_PENDING_USER_IDS = set()
_ADMIN_USER_ID = None
_ADMIN_PENDING_PRICE_USER_ID = None
_USER_SEARCH_LAST_AT = OrderedDict()
_USER_SEARCH_COOLDOWN_LOCK = asyncio.Lock()
_SEEN_WEBHOOK_UPDATE_IDS = OrderedDict()
_WEBHOOK_UPDATE_ID_CACHE_LIMIT = 4096
_SOURCE_SEMAPHORE = asyncio.Semaphore(3)

FIELD_LABELS = {
    "ESTIMATED CURRENT REGION": "country",
    "JOINED": "joined",
    "LANGUAGE": "language",
    "USER ID": "user_id",
    "NICKNAME UPDATED": "nickname_updated",
    "USERNAME UPDATED": "username_updated",
    "FOLLOWERS": "followers",
    "HEARTS": "hearts",
    "VIDEOS": "videos",
    "FOLLOWING": "following",
    "FRIENDS": "friends",
    "HEARTS PER FOLLOWER": "hearts_per_follower",
    "EST. ENGAGEMENT": "engagement",
}

FIELD_NAMES_AR = {
    "country": "الدولة الحالية",
    "joined": "تاريخ الانضمام",
    "language": "اللغة",
    "user_id": "معرّف الحساب",
    "nickname_updated": "آخر تحديث للاسم",
    "username_updated": "آخر تحديث لاسم المستخدم",
    "followers": "المتابعون",
    "hearts": "الإعجابات",
    "videos": "الفيديوهات",
    "following": "يتابع",
    "friends": "الأصدقاء",
    "hearts_per_follower": "الإعجابات لكل متابع",
    "engagement": "معدل التفاعل التقديري",
}

COUNTRIES_AR = {
    "italy": "إيطاليا",
    "saudi arabia": "المملكة العربية السعودية",
    "united states": "الولايات المتحدة",
    "united kingdom": "المملكة المتحدة",
    "united arab emirates": "الإمارات العربية المتحدة",
    "egypt": "مصر",
    "kuwait": "الكويت",
    "qatar": "قطر",
    "bahrain": "البحرين",
    "oman": "عُمان",
    "jordan": "الأردن",
    "iraq": "العراق",
    "turkey": "تركيا",
    "france": "فرنسا",
    "germany": "ألمانيا",
    "canada": "كندا",
    "australia": "أستراليا",
}

COUNTRY_FLAGS = {
    "italy": "🇮🇹",
    "saudi arabia": "🇸🇦",
    "united states": "🇺🇸",
    "united kingdom": "🇬🇧",
    "united arab emirates": "🇦🇪",
    "egypt": "🇪🇬",
    "kuwait": "🇰🇼",
    "qatar": "🇶🇦",
    "bahrain": "🇧🇭",
    "oman": "🇴🇲",
    "jordan": "🇯🇴",
    "iraq": "🇮🇶",
    "turkey": "🇹🇷",
    "france": "🇫🇷",
    "germany": "🇩🇪",
    "canada": "🇨🇦",
    "australia": "🇦🇺",
}

_COUNTRY_ISO2_FALLBACK = {
    "italy": "IT", "saudi arabia": "SA", "united states": "US",
    "united kingdom": "GB", "united arab emirates": "AE", "egypt": "EG",
    "kuwait": "KW", "qatar": "QA", "bahrain": "BH", "oman": "OM",
    "jordan": "JO", "iraq": "IQ", "turkey": "TR", "türkiye": "TR",
    "france": "FR", "germany": "DE", "canada": "CA", "australia": "AU",
    "south korea": "KR", "republic of korea": "KR", "north korea": "KP",
    "russia": "RU", "iran": "IR", "syria": "SY", "palestine": "PS",
    "taiwan": "TW", "hong kong": "HK", "china": "CN", "japan": "JP",
    "india": "IN", "pakistan": "PK", "bangladesh": "BD", "indonesia": "ID",
    "malaysia": "MY", "singapore": "SG", "philippines": "PH", "thailand": "TH",
    "vietnam": "VN", "new zealand": "NZ", "brazil": "BR", "mexico": "MX",
    "south africa": "ZA", "spain": "ES", "netherlands": "NL", "switzerland": "CH",
    "ukraine": "UA", "poland": "PL", "sweden": "SE", "norway": "NO",
    "denmark": "DK", "austria": "AT", "belgium": "BE", "portugal": "PT",
    "greece": "GR", "ireland": "IE", "finland": "FI", "czechia": "CZ",
    "czech republic": "CZ", "argentina": "AR", "chile": "CL", "colombia": "CO",
    "peru": "PE", "nigeria": "NG", "kenya": "KE", "morocco": "MA",
    "tunisia": "TN", "algeria": "DZ", "lebanon": "LB", "israel": "IL",
    "yemen": "YE", "sudan": "SD", "libya": "LY", "afghanistan": "AF",
    "nepal": "NP", "sri lanka": "LK", "myanmar": "MM", "cambodia": "KH",
    "turks and caicos islands": "TC", "côte d'ivoire": "CI", "ivory coast": "CI",
}
_COUNTRY_FLAG_CDN = "https://flagcdn.com/w160"


def _country_iso_code(country_text):
    """Resolve a FinTok country/region label to an ISO-3166 alpha-2 code."""
    if not country_text:
        return None
    text = str(country_text).strip()
    flag_pairs = re.findall(r"[\U0001F1E6-\U0001F1FF]{2}", text)
    for pair in flag_pairs:
        code = "".join(chr(ord(char) - 0x1F1E6 + ord("A")) for char in pair)
        if pycountry.countries.get(alpha_2=code):
            return code.lower()

    cleaned = re.sub(r"(?i)(?:source|المصدر)\s*:\s*.*$", "", text)
    cleaned = re.sub(r"[\U0001F1E6-\U0001F1FF]+", " ", cleaned)
    segments = re.split(r"[—–|,()/]+", cleaned)
    for segment in segments:
        candidate = re.sub(r"\s+", " ", segment).strip(" \t.:;-")
        if not candidate:
            continue
        fallback = _COUNTRY_ISO2_FALLBACK.get(candidate.casefold())
        if fallback:
            return fallback.lower()
        if re.fullmatch(r"[A-Za-z]{2,3}", candidate):
            try:
                country = pycountry.countries.lookup(candidate)
                return country.alpha_2.lower()
            except LookupError:
                pass
        try:
            country = pycountry.countries.lookup(candidate)
            return country.alpha_2.lower()
        except LookupError:
            pass
        for key, arabic_name in COUNTRIES_AR.items():
            if candidate.casefold() == arabic_name.casefold():
                return (_COUNTRY_ISO2_FALLBACK.get(key) or "").lower() or None

    folded = cleaned.casefold()
    aliases = []
    for country in pycountry.countries:
        aliases.extend((
            getattr(country, "common_name", ""),
            getattr(country, "official_name", ""),
            country.name,
        ))
    for alias in sorted({name for name in aliases if name}, key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(alias.casefold()) + r"(?![a-z])", folded):
            try:
                return pycountry.countries.lookup(alias).alpha_2.lower()
            except LookupError:
                continue
    for key, arabic_name in COUNTRIES_AR.items():
        if arabic_name.casefold() in folded:
            return (_COUNTRY_ISO2_FALLBACK.get(key) or "").lower() or None
    return None


def _flag_emoji_from_iso2(country_code):
    if not isinstance(country_code, str) or not re.fullmatch(r"[A-Za-z]{2}", country_code):
        return ""
    return "".join(chr(ord(char.upper()) - ord("A") + 0x1F1E6) for char in country_code)


def _country_flag_png_url(country_text):
    code = _country_iso_code(country_text)
    return f"{_COUNTRY_FLAG_CDN}/{code}.png" if code else None


LANGUAGES_AR = {
    "ar": "العربية",
    "en": "الإنجليزية",
    "it": "الإيطالية",
    "fr": "الفرنسية",
    "tr": "التركية",
    "es": "الإسبانية",
    "de": "الألمانية",
    "ja": "اليابانية",
    "ko": "الكورية",
    "zh": "الصينية",
}

MONTHS_AR = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل",
    5: "مايو", 6: "يونيو", 7: "يوليو", 8: "أغسطس",
    9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}


def normalize_username(text: str):
    text = (text or "").strip()
    match = re.search(
        r"(?:https?://)?(?:www\.)?tiktok\.com/@([A-Za-z0-9._]{1,24})",
        text,
        re.IGNORECASE,
    )
    username = match.group(1) if match else text.lstrip("@").strip()
    if re.fullmatch(r"[A-Za-z0-9._]{1,24}", username):
        return username
    return None


def _format_date(value: str) -> str:
    """حوّل التواريخ الشائعة في بطاقة المصدر إلى صيغة عربية مقروءة."""
    value = value.strip()
    match = re.fullmatch(
        r"(\d{1,2})/(\d{1,2})/(\d{4})(?:,?\s+(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM))?",
        value,
        re.IGNORECASE,
    )
    if not match:
        return value
    month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if month not in MONTHS_AR:
        return value
    formatted = f"{day} {MONTHS_AR[month]} {year}"
    if match.group(4):
        hour = int(match.group(4))
        minute = match.group(5)
        period = "صباحًا" if match.group(6).upper() == "AM" else "مساءً"
        formatted += f"، {hour:02d}:{minute} {period}"
    return formatted


def _translate_value(field: str, value: str) -> str:
    value = value.strip()
    if field == "country":
        country_name = COUNTRIES_AR.get(value.casefold(), value)
        flag = COUNTRY_FLAGS.get(value.casefold(), "")
        return f"{country_name} {flag}" if flag else country_name
    if field == "language":
        return LANGUAGES_AR.get(value.casefold(), value)
    if field in {"joined", "nickname_updated", "username_updated"}:
        if value.casefold() in {"never", "n/a", "not available"}:
            return "لم يسبق تغييره" if field != "joined" else value
        return _format_date(value)
    return value


def _parse_result(username: str, raw_text: str):
    lines = [line.strip() for line in (raw_text or "").splitlines()]
    lines = [line for line in lines if line]
    key_lines = {label.casefold(): key for label, key in FIELD_LABELS.items()}
    first_field_index = len(lines)
    fields = {}
    i = 0
    while i < len(lines):
        current = lines[i].strip()
        upper = current.upper().strip(" :")
        matched_label = None
        matched_value = ""
        for label in FIELD_LABELS:
            if upper == label:
                matched_label = label
                break
            prefix = label + ":"
            if current.upper().startswith(prefix):
                matched_label = label
                matched_value = current[len(prefix):].strip()
                break
        if matched_label:
            first_field_index = min(first_field_index, i)
            field = key_lines[matched_label.casefold()]
            if not matched_value:
                j = i + 1
                while j < len(lines) and lines[j].upper() in {"ENGAGEMENT", "PROFILE"}:
                    j += 1
                if j < len(lines) and lines[j].strip().casefold() not in key_lines:
                    matched_value = lines[j]
                    i = j
            if matched_value:
                fields[field] = _translate_value(field, matched_value)
        i += 1

    header = lines[:first_field_index]
    display_name = ""
    bio_parts = []
    saw_username = False
    for line in header:
        if line.startswith("@"):
            saw_username = True
            continue
        if not display_name:
            candidate = re.sub(r"^[♯#]+\s*", "", line).strip()
            if candidate and candidate.upper() not in {"ENGAGEMENT", "PROFILE"}:
                display_name = candidate
            continue
        if saw_username and line.upper() not in {"ENGAGEMENT", "PROFILE"}:
            bio_parts.append(line)

    display_name = display_name or f"@{username}"
    return display_name, " ".join(bio_parts), fields


def format_tikip_result(username: str, raw_text: str) -> str:
    """حوّل نص بطاقة Tikip الخام إلى بطاقة عربية واضحة مع إبقاء الحقول المتاحة."""
    if not (raw_text or "").strip():
        return f"لم تظهر بيانات واضحة للحساب @{username}. جرّب مرة أخرى لاحقًا."

    display_name, bio, fields = _parse_result(username, raw_text)
    output = ["📊 إحصاءات حساب TikTok", "━━━━━━━━━━━━━━━━━━"]
    output.append(f"👤 الاسم: {display_name}")
    output.append(f"🔗 اسم المستخدم: @{username}")
    if bio:
        output.append("📝 النبذة: " + bio)

    profile_fields = [
        "country", "joined", "language", "user_id", "nickname_updated", "username_updated"
    ]
    profile_values = [key for key in profile_fields if fields.get(key)]
    stats_fields = [
        "followers", "hearts", "videos", "following", "friends",
        "hearts_per_follower", "engagement",
    ]
    stats_values = [key for key in stats_fields if fields.get(key) is not None]
    if profile_values or stats_values:
        first_section = "🌐 معلومات الحساب" if profile_values else "📈 الإحصاءات"
        output.extend(["", first_section, "", "البيان | القيمة"])
    if profile_values:
        for field in profile_values:
            source_note = " (بحسب المصدر)" if field == "country" else ""
            output.append(f"{FIELD_NAMES_AR[field]} | {fields[field]}{source_note}")

    if stats_values:
        if profile_values:
            output.extend(["", "📈 الإحصاءات"])
        for field in stats_values:
            output.append(f"{FIELD_NAMES_AR[field]} | {fields[field]}")

    if not profile_values and not stats_values:
        output.extend(["", raw_text.strip()])

    result = "\n".join(output)
    if len(result) > 3_500:
        result = result[:3_450].rstrip() + "\n… تم اختصار بقية النص."
    return result


def _format_source_timestamp(value) -> str:
    if value in (None, "", 0, "0", False, "Never", "N/A"):
        return "Never"
    try:
        timestamp = float(value)
        if timestamp > 0:
            if timestamp >= 1e12:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, timezone.utc).strftime(
                "%-m/%-d/%Y, %-I:%M:%S %p"
            )
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return str(value)


def _format_tikip_metric(value) -> str:
    if value is None:
        return "Unavailable"
    try:
        number = int(value)
        if str(number) == str(value).strip() or isinstance(value, int):
            return f"{number:,}"
    except (TypeError, ValueError):
        pass
    return str(value)


def format_tikip_api_result(username: str, payload: dict):
    """Format Tikip's public JSON response into the existing Arabic profile card."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return f"لم تظهر بيانات واضحة للحساب @{username}. جرّب مرة أخرى لاحقًا."

    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    profile_username = str(user.get("uniqueId") or username).lstrip("@")
    nickname = str(user.get("nickname") or f"@{profile_username}")
    lines = [nickname, f"@{profile_username}"]
    bio = str(user.get("signature") or "").strip()
    if bio:
        lines.append(bio)

    region = (
        data.get("current_region_name")
        or user.get("current_region_name")
        or data.get("current_region")
        or "Unavailable"
    )
    if isinstance(region, str) and len(region.strip()) == 2:
        region = "Unavailable"
    lines.extend([
        "ESTIMATED CURRENT REGION", str(region),
        "JOINED", _format_source_timestamp(user.get("createTime")),
        "LANGUAGE", str(user.get("language") or "Unavailable"),
        "USER ID", str(user.get("id") or "Unavailable"),
        "NICKNAME UPDATED",
        _format_source_timestamp(
            user.get("nickNameModifyTime")
            or data.get("nick_name_modify_time")
            or data.get("nickname_modify_time")
        ),
        "USERNAME UPDATED",
        _format_source_timestamp(
            user.get("uniqueIdModifyTime")
            or data.get("unique_id_modify_time")
            or data.get("username_modify_time")
        ),
    ])

    followers = stats.get("followerCount")
    hearts = stats.get("heartCount")
    videos = stats.get("videoCount")
    following = stats.get("followingCount")
    friends = stats.get("friendCount")
    lines.extend([
        "FOLLOWERS", _format_tikip_metric(followers),
        "HEARTS", _format_tikip_metric(hearts),
        "VIDEOS", _format_tikip_metric(videos),
        "FOLLOWING", _format_tikip_metric(following),
        "FRIENDS", _format_tikip_metric(friends),
    ])

    try:
        follower_count = float(followers or 0)
        heart_count = float(hearts or 0)
        video_count = float(videos or 0)
        hearts_per_follower = heart_count / follower_count if follower_count else 0
        engagement = (
            heart_count / video_count / follower_count * 100
            if video_count and follower_count else 0
        )
        lines.extend([
            "HEARTS PER FOLLOWER", f"{hearts_per_follower:.2f}",
            "EST. ENGAGEMENT", f"{engagement:.2f}%",
        ])
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    plain_text = format_tikip_result(username, "\n".join(lines))
    avatar_url = str(user.get("avatarLarger") or user.get("avatarMedium") or "")
    parsed_avatar = urlparse(avatar_url)
    if parsed_avatar.scheme != "https" or not parsed_avatar.hostname:
        avatar_url = ""
    return {
        "text": plain_text,
        "markdown": format_tikip_rich_markdown(username, plain_text, avatar_url),
        "avatar_url": avatar_url,
    }


def _escape_markdown(value: str) -> str:
    value = str(value).replace("\n", " ").replace("\r", " ")
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~])", r"\\\1", value)


def _fintok_display_value(value, *, compact_number=False):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "نعم" if value else "لا"
    if isinstance(value, str) and value.strip().casefold() in {"yes", "true"}:
        return "نعم"
    if isinstance(value, str) and value.strip().casefold() in {"no", "false"}:
        return "لا"
    if compact_number:
        number = None
        if isinstance(value, (int, float)):
            number = value
        elif isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
            number = float(value.strip())
        if number is not None and number >= 1000:
            unit, divisor = ("M", 1_000_000) if number >= 1_000_000 else ("K", 1_000)
            compact = f"{number / divisor:.2f}".rstrip("0").rstrip(".")
            return f"{compact}{unit}"
    return str(value)


def _fintok_region(value):
    if value is None or value == "":
        return None
    value = str(value).strip()
    key = value.casefold()
    country = next(
        (name for name in sorted(COUNTRY_FLAGS, key=len, reverse=True)
         if key.startswith(name.casefold())),
        None,
    )
    country_code = _country_iso_code(value)
    flag = COUNTRY_FLAGS.get(country, "") if country else ""
    flag = flag or _flag_emoji_from_iso2(country_code)
    arabic_name = COUNTRIES_AR.get(country) if country else None
    if arabic_name and arabic_name not in value:
        value = f"{arabic_name} — {value}"
    if flag and flag not in value:
        value = f"{value} {flag}"
    if "fintok" not in value.casefold():
        value = f"{value} — المصدر: FinTok"
    return value



def _fintok_account_type(value):
    if isinstance(value, bool):
        return "خاص (Private Account)" if value else "عام (Public Account)"
    if isinstance(value, (int, float)) and value in (0, 1):
        return "خاص (Private Account)" if value == 1 else "عام (Public Account)"
    if isinstance(value, str):
        key = value.strip().casefold()
        if key in {"private account", "private", "true"}:
            return f"خاص ({value})" if key != "private account" else "خاص (Private Account)"
        if key in {"public account", "public", "false"}:
            return f"عام ({value})" if key != "public account" else "عام (Public Account)"
    return value


def _fintok_language(value):
    if not isinstance(value, str) or not value.strip():
        return value
    value = value.strip()
    name_to_code = {
        "arabic": "ar", "english": "en", "italian": "it", "french": "fr",
        "turkish": "tr", "spanish": "es", "german": "de", "japanese": "ja",
        "korean": "ko", "chinese": "zh",
    }
    key = value.casefold()
    code = name_to_code.get(key) or (key[:2] if re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", key) else "")
    arabic_name = LANGUAGES_AR.get(code)
    return f"{arabic_name} ({value})" if arabic_name else value


def format_fintok_result(username, profile, active_region, registered_region, security):
    """Preserve the labels, values and order displayed by FinTok's profile page."""
    profile = profile if isinstance(profile, dict) else {}
    account = security if isinstance(security, dict) else {}
    oauth = account.get("oauth") if isinstance(account.get("oauth"), dict) else {}
    status = account.get("account_status") if isinstance(account.get("account_status"), dict) else {}
    name = str(profile.get("fullname") or "")
    handle = str(profile.get("username") or username).lstrip("@")
    basic = [("يتابع", _fintok_display_value(profile.get("following"), compact_number=True)),
             ("المتابعون", _fintok_display_value(profile.get("followers"), compact_number=True)),
             ("الإعجابات", _fintok_display_value(profile.get("likes"), compact_number=True))]
    info = [("معرّف المستخدم", profile.get("userid")),
            ("نوع الحساب", _fintok_account_type(profile.get("private"))),
            ("المنطقة النشطة", _fintok_region(active_region)),
            ("اللغة", _fintok_language(profile.get("language"))),
            ("وقت إنشاء الحساب", profile.get("created_time")),
            ("آخر تغيير للاسم", profile.get("last_change_name"))]
    platforms = oauth.get("platforms")
    if isinstance(platforms, list):
        platforms = ", ".join(str(item) for item in platforms) if platforms else None
    security_rows = [
        ("البريد الإلكتروني مرتبط", _fintok_display_value(account.get("is_email"))),
        ("رقم الجوال مرتبط", _fintok_display_value(account.get("is_mobile"))),
        ("كلمة المرور مُعيّنة", _fintok_display_value(account.get("is_password"))),
        ("مفتاح المرور (Passkey)", _fintok_display_value(account.get("is_passkey"))),
        ("OAuth مفعّل", _fintok_display_value(oauth.get("is_enabled"))),
        ("المنصات المتصلة", platforms),
        ("حالة تسجيل الدخول", _fintok_display_value(status.get("is_logged_in"))),
        ("مستخدم FTC", _fintok_display_value(status.get("is_ftc_user"))),
        ("حالة تعدد الأجهزة", _fintok_display_value(status.get("is_most_device"))),
        ("حساب مؤسسة", _fintok_display_value(status.get("is_org_account"))),
    ] if account else []
    note = "المعلومات الإقليمية وبيانات الأمان بحسب FinTok ولم يُتحقق من صحتها."
    plain = [name, f"@{handle}", "البيان | القيمة"]
    table = ["| البيان | القيمة |", "| --- | --- |"]
    rows = basic + info + security_rows
    for label, value in rows:
        if value is None or value == "":
            continue
        value = str(value)
        plain.append(f"{label} | {value}")
        table.append(f"| {_escape_markdown(label)} | {_escape_markdown(value)} |")
    plain.append(note)

    avatar_url = str(profile.get("profile") or "")
    parsed = urlparse(avatar_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname in {"localhost", "127.0.0.1"}:
        avatar_url = ""
    original_country = _fintok_region(registered_region)
    if original_country and len(original_country) > 150:
        original_country = original_country[:147].rstrip() + "…"
    rich = []
    if avatar_url:
        rich.append(f"![صورة الملف]({quote(avatar_url, safe=':/?&=%@,+-_.~')})")
    rich.extend([f"**{_escape_markdown(name)}** · `@{handle}`", "\n".join(table), _escape_markdown(note)])
    return {
        "text": "\n".join(plain),
        "markdown": "\n\n".join(rich),
        "avatar_url": avatar_url,
        "username": handle,
        "original_country": original_country,
    }


def format_tikip_rich_markdown(username: str, plain_text: str, avatar_url: str = "") -> str:
    """كوّن Rich Message بصيغة Markdown الموثقة رسميًا في Telegram Bot API."""
    lines = (plain_text or "").splitlines()
    display_name = next(
        (line.removeprefix("👤 الاسم: ").strip() for line in lines if line.startswith("👤 الاسم: ")),
        f"@{username}",
    )
    bio = next(
        (line.removeprefix("📝 النبذة: ").strip() for line in lines if line.startswith("📝 النبذة: ")),
        "",
    )
    sections = {"🌐 معلومات الحساب": [], "📈 الإحصاءات": []}
    active_section = None
    for line in lines:
        if line in sections:
            active_section = line
        elif line.startswith("📈 الإحصاءات"):
            active_section = "📈 الإحصاءات"
        elif line.startswith("🌐 معلومات الحساب"):
            active_section = "🌐 معلومات الحساب"
        elif active_section and (line.startswith("• ") or " | " in line):
            item = line[2:] if line.startswith("• ") else line
            if item.strip().casefold() == "البيان | القيمة":
                continue
            if " | " in item:
                label, value = item.split(" | ", 1)
                sections[active_section].append((label.strip(), value.strip()))
            elif ":" in item:
                label, value = item.split(":", 1)
                sections[active_section].append((label.strip(), value.strip()))

    followers = next(
        (value for label, value in sections["📈 الإحصاءات"] if label == FIELD_NAMES_AR["followers"]),
        "",
    )
    hearts = next(
        (value for label, value in sections["📈 الإحصاءات"] if label == FIELD_NAMES_AR["hearts"]),
        "",
    )

    chunks = ["## 📊 إحصاءات حساب TikTok"]
    parsed_avatar = urlparse(avatar_url or "")
    safe_avatar = (
        avatar_url
        if parsed_avatar.scheme == "https" and parsed_avatar.hostname
        and parsed_avatar.hostname not in {"localhost", "127.0.0.1"}
        else ""
    )
    if safe_avatar:
        photo_caption = []
        if followers:
            photo_caption.append(f"👥 {followers} متابعًا")
        if hearts:
            photo_caption.append(f"❤️ {hearts} إعجاب")
        caption = " · ".join(photo_caption) or f"صورة الحساب @{username}"
        image_url = quote(safe_avatar, safe=":/?&=%@,+-_.~")
        chunks.append(f'![صورة الحساب]({image_url} "{caption}")')

    chunks.append(f"**{_escape_markdown(display_name)}** · `@{username}`")
    if followers and not safe_avatar:
        chunks.append(f"👥 **المتابعون:** {followers}")
    if bio:
        chunks.append(f"📝 {_escape_markdown(bio)}")

    has_rows = any(sections.values())
    if has_rows:
        chunks.append("### تفاصيل الحساب والإحصاءات")
        table_rows = ["| البيان | القيمة |", "| --- | --- |"]
        for title, rows in sections.items():
            if not rows:
                continue
            table_rows.append(f"| **{_escape_markdown(title)}** |  |")
            for label, value in rows:
                table_rows.append(
                    f"| {_escape_markdown(label)} | {_escape_markdown(value)} |"
                )
        chunks.append("\n".join(table_rows))

    if not has_rows:
        chunks.append(_escape_markdown(plain_text))
    return "\n\n".join(chunks)


def _set_progress(progress, stage):
    if progress is not None:
        progress["stage"] = stage


# Proxy credentials live in a separate file; never log or print its contents.
PROXY_LIST_PATH = os.environ.get(
    "TIKWAHM_PROXY_LIST_FILE",
    os.path.join(os.path.dirname(__file__), "proxy_pool.txt"),
).strip()


def _load_proxy_pool(path: str):
    """Read Webshare entries in host:port:username:password format."""
    proxies = []
    try:
        encoded = os.environ.get("TIKWAHM_PROXY_LIST_B64", "").strip()
        if encoded:
            content = base64.b64decode(encoded, validate=True).decode("utf-8")
        else:
            with open(path, "r", encoding="utf-8") as proxy_file:
                content = proxy_file.read()
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":", 3)
            if len(parts) != 4:
                continue
            host, port, username, password = parts
            if not host or not port.isdigit() or not username or not password:
                continue
            port_number = int(port)
            if not 1 <= port_number <= 65535:
                continue
            proxies.append({
                "host": host,
                "port": port_number,
                "username": username,
                "password": password,
            })
    except (OSError, ValueError, UnicodeError):
        return []
    return proxies


_PROXY_POOL = _load_proxy_pool(PROXY_LIST_PATH)
_ACTIVE_PROXY_INDEX = 0
_PROXY_LOCK = asyncio.Lock()

_PROVIDER_DENIAL_MARKERS = (
    "captcha",
    "i'm not a robot",
    "i am not a robot",
    "verify you are human",
    "verify that you are human",
    "checking your browser",
    "checking if the site connection is secure",
    "access denied",
    "unauthorized",
    "not authorized",
    "you do not have access",
    "you don't have access",
    "access restricted",
    "too many requests",
    "forbidden",
    "request blocked",
    "unusual traffic",
    "automated requests",
    "security check",
    "security challenge",
    "robot check",
)
_PROVIDER_RESTRICTION_MESSAGE = (
    "رفض مصدر FinTok الطلب أو عرض تحققًا أمنيًا عبر البروكسيات المتاحة. "
    "جرّب البحث مرة أخرى لاحقًا."
)


class _RetryableFinTokError(Exception):
    """A transient proxy/source failure for which the next proxy should be tried."""


class _ProxyConnectionBroken(_RetryableFinTokError):
    """Raised only for an explicit transport/proxy connection failure."""


class _FinTokRequestRejected(_RetryableFinTokError):
    """Raised when Tikip refuses a request or shows a challenge page."""


class _FinTokAttemptTimedOut(_RetryableFinTokError):
    """Raised when a direct Tikip API request times out."""


async def _get_next_proxy():
    """سحب البروكسي الحالي وتقديم المؤشر للبروكسي التالي تلقائياً لكل محاولة."""
    global _ACTIVE_PROXY_INDEX
    async with _PROXY_LOCK:
        total = len(_PROXY_POOL)
        if total == 0:
            return None, -1
        current_index = _ACTIVE_PROXY_INDEX % total
        _ACTIVE_PROXY_INDEX = (current_index + 1) % total
        return _PROXY_POOL[current_index], current_index


def _fintok_signature(timestamp, *parts):
    signature_input = "".join(map(str, parts)) + str(timestamp) + FINTOK_SIGNATURE_SALT
    return hashlib.sha256(signature_input.encode("utf-8")).hexdigest()


def _request_fintok_payload(path, params, signature_parts, proxy):
    """Use FinTok's own public client protocol through the selected proxy only."""
    proxy_url = (
        f"http://{quote(proxy['username'], safe='')}:{quote(proxy['password'], safe='')}"
        f"@{proxy['host']}:{proxy['port']}"
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    )
    timestamp = int(time.time())
    params = {**params, "ts": timestamp}
    endpoint = f"{FINTOK_API_URL}{path}?{urlencode(params)}"
    request = urllib.request.Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "Origin": FINTOK_URL.rstrip("/"),
            "Referer": FINTOK_URL,
            "x-token": _fintok_signature(timestamp, *signature_parts),
            "User-Agent": "Mozilla/5.0 (compatible; TikWahm/1.0)",
        },
    )

    try:
        with opener.open(request, timeout=SOURCE_TIMEOUT_SECONDS) as response:
            status = response.getcode()
            raw_body = response.read(MAX_SOURCE_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in {401, 403, 429}:
            raise _FinTokRequestRejected(_PROVIDER_RESTRICTION_MESSAGE) from exc
        raise _FinTokRequestRejected(f"FinTok HTTP {exc.code}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise _FinTokAttemptTimedOut from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        reason_text = str(reason or exc).casefold()
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in reason_text:
            raise _FinTokAttemptTimedOut from exc
        raise _ProxyConnectionBroken from exc
    except OSError as exc:
        if "timed out" in str(exc).casefold():
            raise _FinTokAttemptTimedOut from exc
        raise _ProxyConnectionBroken from exc

    if not isinstance(status, int) or status < 200 or status >= 300:
        raise _FinTokRequestRejected(f"FinTok HTTP {status}")
    if len(raw_body) > MAX_SOURCE_RESPONSE_BYTES:
        raise _FinTokRequestRejected("FinTok returned an oversized response")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _FinTokRequestRejected("FinTok returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise _FinTokRequestRejected("FinTok returned an invalid response")
    return payload


async def _fetch_once_through_proxy(username: str, proxy, progress=None):
    """Fetch the four endpoint results as FinTok does, using the same proxy."""
    _set_progress(progress, "البحث عن الحساب وجمع بياناته")
    async def request(path, params, parts):
        return await asyncio.to_thread(_request_fintok_payload, path, params, parts, proxy)

    # Fetch account details and the security section concurrently, like the site.
    profile_payload, security_payload = await asyncio.gather(
        request("/", {"username": username}, (username,)),
        request("/security", {"username": username}, (username,)),
    )
    if str(profile_payload.get("status", "")).casefold() != "ok":
        message = str(profile_payload.get("message") or profile_payload.get("error") or "")
        payload_text = json.dumps(profile_payload, ensure_ascii=False).casefold()
        if any(marker in payload_text for marker in _PROVIDER_DENIAL_MARKERS):
            raise _FinTokRequestRejected(_PROVIDER_RESTRICTION_MESSAGE)
        return message or "لم يعثر المصدر على بيانات عامة للحساب."
    security_text = json.dumps(security_payload, ensure_ascii=False).casefold()
    if any(marker in security_text for marker in _PROVIDER_DENIAL_MARKERS):
        raise _FinTokRequestRejected(_PROVIDER_RESTRICTION_MESSAGE)
    profile = profile_payload.get("data")
    if not isinstance(profile, dict):
        raise _FinTokRequestRejected("FinTok returned an invalid profile")
    uid, secid = profile.get("userid"), profile.get("secUid")
    if not uid or not secid:
        raise _FinTokRequestRejected("FinTok profile is missing the IDs required for region lookup")
    params = {"userid": uid, "secid": secid}
    active, registered = await asyncio.gather(
        request("/active", params, (uid, secid)),
        request("/registered", params, (uid, secid)),
    )
    if str(active.get("status", "")).casefold() != "ok" or str(registered.get("status", "")).casefold() != "ok":
        raise _FinTokRequestRejected("FinTok did not return both region results")
    active_data = active.get("data") if isinstance(active.get("data"), dict) else {}
    registered_data = registered.get("data") if isinstance(registered.get("data"), dict) else {}
    active_region = active_data.get("active_region")
    registered_region = registered_data.get("registered_region")
    msg = security_payload.get("msg") if isinstance(security_payload.get("msg"), dict) else {}
    security = msg.get("account_info")
    if not isinstance(security, dict):
        raise _FinTokRequestRejected("FinTok returned an invalid security section")
    _set_progress(progress, "وصلت البيانات؛ جارٍ تنسيق النتيجة")
    return format_fintok_result(username, profile, active_region, registered_region, security)


async def fetch_data_from_fintok(username: str, progress=None):
    """يتم تغيير البروكسي تلقائياً مع كل محاولة بحث جديدة، وعند فشل الاتصال يتم الانتقال للتالي فوراً."""
    if not _PROXY_POOL:
        return (
            "لا يمكن إجراء البحث: ملف إعداد البروكسي غير متاح أو لا يحتوي إدخالات صالحة. "
            "لم يتم الاتصال مباشرة."
        )

    total_proxies = len(_PROXY_POOL)
    saw_timeout = False
    saw_rejection = False
    for _ in range(total_proxies):
        proxy, proxy_index = await _get_next_proxy()
        if proxy is None:
            break
        attempt_started = time.monotonic()
        try:
            async with _SOURCE_SEMAPHORE:
                result = await _fetch_once_through_proxy(username, proxy, progress)
            print(
                f"Proxy slot #{proxy_index + 1} completed FinTok JSON request in "
                f"{time.monotonic() - attempt_started:.2f}s.",
                flush=True,
            )
            return result
        except _ProxyConnectionBroken:
            print(
                f"Proxy slot #{proxy_index + 1} failed to connect after "
                f"{time.monotonic() - attempt_started:.2f}s; rotating to next...",
                flush=True,
            )
            continue
        except (_FinTokAttemptTimedOut, asyncio.TimeoutError, TimeoutError):
            saw_timeout = True
            print(
                f"Proxy slot #{proxy_index + 1} timed out after "
                f"{time.monotonic() - attempt_started:.2f}s; rotating to next...",
                flush=True,
            )
            continue
        except _FinTokRequestRejected:
            saw_rejection = True
            print(
                f"Proxy slot #{proxy_index + 1} was rejected by FinTok after "
                f"{time.monotonic() - attempt_started:.2f}s; rotating to next...",
                flush=True,
            )
            continue
        except Exception as exc:
            return (
                "تعذر إكمال البحث بسبب مشكلة في مصدر FinTok أو البروكسي "
                f"({type(exc).__name__}). حاول مرة أخرى لاحقًا."
            )

    if saw_rejection:
        return _PROVIDER_RESTRICTION_MESSAGE
    if saw_timeout:
        return "انتهت مهلة البحث عبر البروكسيات المتاحة. حاول مرة أخرى لاحقًا."
    return (
        "تعذر الاتصال عبر جميع البروكسيات المتاحة في القائمة بسبب أعطال اتصال."
    )


def _arabic_digits(value) -> str:
    return str(value).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


def _progress_message(username: str, percent: int, headline: str = "") -> str:
    percent = max(0, min(100, int(percent)))
    filled = percent // 10
    bar = "🟦" * filled + "⬜" * (10 - filled)
    title = headline or f"🔎 جارٍ البحث عن @{username}"
    return f"{title}\n\n{bar} {_arabic_digits(percent)}٪"


def _wait_progress_for_elapsed(username: str, elapsed: float) -> str:
    if elapsed < EXPECTED_LOOKUP_SECONDS:
        percent = int(max(0, elapsed) / EXPECTED_LOOKUP_SECONDS * 100)
        return _progress_message(username, percent)
    frames = ("◴", "◷", "◶", "◵")
    frame = frames[int(elapsed / PROGRESS_ANIMATION_INTERVAL) % len(frames)]
    return _progress_message(username, 100, f"{frame} جارٍ إكمال البحث")


def _progress_rich_message(text: str, media_source: str) -> types.InputRichMessage:
    """Build a Rich Message with an auto-playing animation and its progress text."""
    caption = "<br>".join(html.escape(line) for line in str(text).splitlines())
    if not caption:
        caption = "&nbsp;"
    return types.InputRichMessage(
        html=(
            f'<figure><video src="tg://video?id={PROGRESS_MEDIA_ID}"></video>'
            f"<figcaption>{caption}</figcaption></figure>"
        ),
        media=[
            types.InputRichMessageMedia(
                id=PROGRESS_MEDIA_ID,
                media=types.InputMediaAnimation(media=media_source),
            )
        ],
        is_rtl=True,
    )


def _progress_animation_file_id(wait_msg) -> str:
    animation = getattr(wait_msg, "animation", None)
    file_id = getattr(animation, "file_id", None)
    if file_id:
        return file_id
    rich_message = getattr(wait_msg, "rich_message", None)
    for block in getattr(rich_message, "blocks", None) or []:
        file_id = getattr(getattr(block, "animation", None), "file_id", None)
        if file_id:
            return file_id
    return ""


async def _send_rich_progress_message(message, text: str):
    """Upload the GIF as an attached rich-message animation (no public host needed)."""
    with open(LOADING_GIF_PATH, "rb") as animation:
        rich_message = _progress_rich_message(
            text, f"attach://{PROGRESS_ATTACHMENT_NAME}"
        )
        result = await asyncio_helper._process_request(
            bot.token,
            "sendRichMessage",
            method="post",
            params={
                "chat_id": str(message.chat.id),
                "rich_message": rich_message.to_json(),
                "reply_parameters": types.ReplyParameters(
                    message_id=message.message_id
                ).to_json(),
            },
            files={
                PROGRESS_ATTACHMENT_NAME: (
                    os.path.basename(LOADING_GIF_PATH),
                    animation,
                )
            },
        )
    return types.Message.de_json(result)


def _retry_markup(username: str):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "إعادة المحاولة مجددًا",
            callback_data=f"retry:{username}",
        )
    )
    return markup


def _prune_lookup_cache():
    now = time.monotonic()
    expired = [key for key, (expires_at, _) in _LOOKUP_CACHE.items() if expires_at <= now]
    for key in expired:
        _LOOKUP_CACHE.pop(key, None)


async def _reserve_user_search(user_id, now=None):
    """Reserve one search slot per Telegram user for each 20-second window."""
    if user_id is None:
        return 0
    key = str(user_id)
    now = time.monotonic() if now is None else now
    async with _USER_SEARCH_COOLDOWN_LOCK:
        last_started = _USER_SEARCH_LAST_AT.get(key)
        if last_started is not None:
            remaining = USER_SEARCH_COOLDOWN_SECONDS - (now - last_started)
            if remaining > 0:
                _USER_SEARCH_LAST_AT.move_to_end(key)
                return max(1, math.ceil(remaining))
        _USER_SEARCH_LAST_AT[key] = now
        _USER_SEARCH_LAST_AT.move_to_end(key)
        if len(_USER_SEARCH_LAST_AT) > 10_000:
            _USER_SEARCH_LAST_AT.popitem(last=False)
        return 0


def _search_cooldown_text(remaining: int) -> str:
    number = _arabic_digits(remaining)
    if remaining == 1:
        phrase = "ثانية"
    elif remaining == 2:
        phrase = "ثانيتين"
    elif 3 <= remaining <= 10:
        phrase = f"{number} ثوانٍ"
    else:
        phrase = f"{number} ثانية"
    return f"🔎 البحث متاح بعد {phrase}"


async def _run_search_cooldown_countdown(
    chat_id, wait_msg, is_animation, deadline, *, initial_seconds=None,
    clock=None, sleep=None, completion_text="✅ البحث متاح الآن، أرسل اسم المستخدم من جديد.",
):
    clock = clock or time.monotonic
    sleep = sleep or asyncio.sleep
    last_displayed = initial_seconds
    while True:
        remaining = deadline - clock()
        seconds = math.ceil(max(0.0, remaining))
        if seconds <= 0:
            break
        if seconds != last_displayed:
            try:
                await _edit_wait_message(
                    chat_id, wait_msg, is_animation, _search_cooldown_text(seconds)
                )
            except Exception:
                pass
            last_displayed = seconds
        next_change_at = deadline - (seconds - 1)
        delay = max(0.05, min(remaining, next_change_at - clock()))
        await sleep(delay)

    try:
        await _edit_wait_message(
            chat_id,
            wait_msg,
            is_animation,
            completion_text,
        )
    except Exception:
        pass


async def _send_search_cooldown_message(message, remaining):
    deadline = time.monotonic() + remaining
    seconds = math.ceil(remaining)
    caption = _search_cooldown_text(seconds)
    try:
        with open(LOADING_GIF_PATH, "rb") as animation:
            wait_msg = await bot.send_animation(
                chat_id=message.chat.id,
                animation=animation,
                caption=caption,
                reply_to_message_id=message.message_id,
            )
        is_animation = "animation"
    except Exception as exc:
        print(f"Cooldown animation fallback: {type(exc).__name__}", flush=True)
        wait_msg = await bot.reply_to(message, caption)
        is_animation = False
    await _run_search_cooldown_countdown(
        message.chat.id,
        wait_msg,
        is_animation,
        deadline,
        initial_seconds=seconds,
        completion_text="✅ انتهى الانتظار؛ أعد إرسال اسم المستخدم لبدء البحث.",
    )


async def _start_username_search(message, username):
    wait_msg, is_animation = await _send_wait_message(message, username)
    cached_result, task, is_cached = _get_or_start_lookup(username)
    if is_cached:
        await _show_cached_progress(
            message.chat.id, wait_msg, username, is_animation, cached_result
        )
        return
    await _wait_for_visible_result(
        username, wait_msg, message.chat.id, is_animation, task
    )


async def _show_search_cooldown_on_existing_message(
    chat_id, wait_msg, is_animation, remaining
):
    deadline = time.monotonic() + remaining
    seconds = math.ceil(remaining)
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=wait_msg.message_id, reply_markup=None
        )
    except Exception:
        pass
    try:
        await _edit_wait_message(
            chat_id, wait_msg, is_animation, _search_cooldown_text(seconds)
        )
    except Exception:
        pass
    await _run_search_cooldown_countdown(
        chat_id,
        wait_msg,
        is_animation,
        deadline,
        initial_seconds=seconds,
    )


async def _lookup_and_cache(username: str):
    key = username.casefold()
    current_task = asyncio.current_task()
    try:
        try:
            result = await asyncio.wait_for(
                fetch_data_from_fintok(username), timeout=CACHE_TTL_SECONDS
            )
        except asyncio.TimeoutError:
            result = "نعتذر، لم تكتمل عملية البحث. اضغط إعادة المحاولة مجددًا."
        except Exception as exc:
            result = f"تعذر إكمال الطلب ({type(exc).__name__}). حاول مرة أخرى لاحقًا."
        _LOOKUP_CACHE[key] = (time.monotonic() + CACHE_TTL_SECONDS, result)
        return result
    finally:
        if _LOOKUP_TASKS.get(key) is current_task:
            _LOOKUP_TASKS.pop(key, None)


def _get_or_start_lookup(username: str, force_fresh: bool = False):
    _prune_lookup_cache()
    key = username.casefold()
    if force_fresh:
        _LOOKUP_CACHE.pop(key, None)
    cached = _LOOKUP_CACHE.get(key)
    if cached:
        return cached[1], None, True
    task = _LOOKUP_TASKS.get(key)
    if task is None:
        task = asyncio.create_task(_lookup_and_cache(username))
        _LOOKUP_TASKS[key] = task
    return None, task, False


async def _send_wait_message(message, username: str):
    caption = _progress_message(username, 0)
    try:
        wait_msg = await _send_rich_progress_message(message, caption)
        if not _progress_animation_file_id(wait_msg):
            try:
                await bot.delete_message(message.chat.id, wait_msg.message_id)
            except Exception:
                pass
            raise RuntimeError("Rich progress message did not return an animation file_id")
        return wait_msg, "rich"
    except Exception as exc:
        print(f"Rich progress fallback: {type(exc).__name__}", flush=True)

    try:
        with open(LOADING_GIF_PATH, "rb") as animation:
            wait_msg = await bot.send_animation(
                chat_id=message.chat.id,
                animation=animation,
                caption=caption,
                reply_to_message_id=message.message_id,
            )
        return wait_msg, "animation"
    except Exception as exc:
        print(f"Animation progress fallback: {type(exc).__name__}", flush=True)
        wait_msg = await bot.reply_to(message, caption)
        return wait_msg, "text"


async def _edit_wait_message(chat_id, wait_msg, is_animation: bool, text: str, reply_markup=None):
    if is_animation == "rich":
        file_id = _progress_animation_file_id(wait_msg)
        if not file_id:
            raise RuntimeError("Progress animation file_id is unavailable")
        return await bot.edit_message_text(
            chat_id=chat_id,
            message_id=wait_msg.message_id,
            rich_message=_progress_rich_message(text, file_id),
            reply_markup=reply_markup,
        )
    if is_animation == "animation":
        return await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=wait_msg.message_id,
            caption=text,
            reply_markup=reply_markup,
        )
    if is_animation:
        return await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=wait_msg.message_id,
            caption=text,
            reply_markup=reply_markup,
        )
    return await bot.edit_message_text(
        chat_id=chat_id,
        message_id=wait_msg.message_id,
        text=text,
        reply_markup=reply_markup,
    )


async def _deliver_result(chat_id, wait_msg, is_animation: bool, result):
    if isinstance(result, dict) and result.get("markdown"):
        try:
            await bot.delete_message(chat_id, wait_msg.message_id)
        except Exception:
            pass
        try:
            rich_message = types.InputRichMessage(markdown=result["markdown"], is_rtl=True)
            reply_markup = None
            original_country = result.get("original_country")
            username = result.get("username", "")
            if original_country or username:
                try:
                    lookup_id = await _store_country_lookup(
                        username, original_country or ""
                    )
                    reply_markup = _country_button_markup(
                        lookup_id, include_country=bool(original_country)
                    )
                except Exception as exc:
                    print(f"Result buttons setup failed: {type(exc).__name__}", flush=True)
            await bot.send_rich_message(
                chat_id=chat_id,
                rich_message=rich_message,
                reply_markup=reply_markup,
            )
            return
        except Exception as exc:
            print(f"Rich result fallback: {type(exc).__name__}", flush=True)
            result = result.get("text", "تعذر تنسيق النتيجة. حاول مرة أخرى.")

    text = str(result)
    if is_animation:
        try:
            await bot.delete_message(chat_id, wait_msg.message_id)
        except Exception:
            pass
        await bot.send_message(chat_id=chat_id, text=text)
        return
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=wait_msg.message_id,
            text=text,
        )
    except Exception:
        await bot.send_message(chat_id=chat_id, text=text)


async def _show_timeout(wait_msg, chat_id, username: str, is_animation: bool):
    text = (
        "نعتذر، تأخرت النتيجة. حفظنا البحث مؤقتًا لمدة ١٠ دقائق؛ "
        "اضغط لإعادة المحاولة."
    )
    try:
        await _edit_wait_message(
            chat_id,
            wait_msg,
            is_animation,
            text,
            reply_markup=_retry_markup(username),
        )
    except Exception:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=_retry_markup(username),
        )


async def _wait_for_visible_result(username, wait_msg, chat_id, is_animation, task):
    started = time.monotonic()
    while True:
        if task.done():
            try:
                result = task.result()
            except Exception as exc:
                result = f"تعذر إكمال الطلب ({type(exc).__name__}). حاول مرة أخرى لاحقًا."
            await _deliver_result(chat_id, wait_msg, is_animation, result)
            return True

        elapsed = time.monotonic() - started
        if elapsed >= MAX_LOOKUP_SECONDS:
            final_delay_message = _wait_progress_for_elapsed(username, elapsed)
            try:
                await _edit_wait_message(
                    chat_id, wait_msg, is_animation, final_delay_message
                )
                await asyncio.sleep(0.2)
            except Exception:
                pass
            await _show_timeout(wait_msg, chat_id, username, is_animation)
            return False

        text = _wait_progress_for_elapsed(username, elapsed)
        try:
            await _edit_wait_message(chat_id, wait_msg, is_animation, text)
        except Exception:
            pass
        remaining = MAX_LOOKUP_SECONDS - elapsed
        await asyncio.wait({task}, timeout=min(PROGRESS_ANIMATION_INTERVAL, remaining))


async def _show_cached_progress(chat_id, wait_msg, username: str, is_animation: bool, result):
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=wait_msg.message_id,
            reply_markup=None,
        )
    except Exception:
        pass
    for percent in (20, 40, 60, 80, 100):
        try:
            await _edit_wait_message(
                chat_id,
                wait_msg,
                is_animation,
                _progress_message(username, percent, "🔄 جارٍ استرجاع النتيجة"),
            )
        except Exception:
            pass
        await asyncio.sleep(0.5)
    await _deliver_result(chat_id, wait_msg, is_animation, result)


async def _retry_active_lookup(chat_id, wait_msg, username: str, is_animation: bool, task):
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=wait_msg.message_id,
            reply_markup=None,
        )
    except Exception:
        pass
    for percent in (20, 40, 60, 80, 100):
        if task.done():
            break
        try:
            await _edit_wait_message(
                chat_id,
                wait_msg,
                is_animation,
                _progress_message(username, percent, "🔄 جارٍ استرجاع النتيجة"),
            )
        except Exception:
            pass
        await asyncio.wait({task}, timeout=0.5)
    if task.done():
        try:
            result = task.result()
        except Exception as exc:
            result = f"تعذر إكمال الطلب ({type(exc).__name__}). حاول مرة أخرى لاحقًا."
        await _deliver_result(chat_id, wait_msg, is_animation, result)
        return
    await _show_timeout(wait_msg, chat_id, username, is_animation)


@bot.message_handler(commands=["start", "help"])
async def send_welcome(message):
    await bot.reply_to(
        message,
        "أرسل اسم مستخدم TikTok عام مثل @username أو رابط الملف، "
        "وسأبحث عن معلومات الحساب وإحصاءاته. استخدم /terms لشروط كشف الدولة، "
        "و /support للدعم، و /subscription لإدارة اشتراكك.",
    )


@bot.message_handler(commands=["terms"])
async def country_terms_command(message):
    await bot.reply_to(message, _country_terms_text())


@bot.message_handler(commands=["support"])
async def country_support_command(message):
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if user_id is not None:
        _SUPPORT_PENDING_USER_IDS.add(user_id)
    await bot.reply_to(
        message,
        "أرسل الآن وصف المشكلة أو رقم عملية الدفع وسأحوّله للإدارة إذا كانت متصلة. "
        "لا ترسل كلمات مرور أو بيانات حساسة. دعم Telegram أو @botsupport "
        "لا يستطيع معالجة مشتريات البوت.",
    )


@bot.message_handler(commands=["subscription", "cancel_subscription", "resume_subscription"])
async def country_subscription_command(message):
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if user_id is None or getattr(getattr(message, "chat", None), "type", None) != "private":
        await bot.reply_to(message, "إدارة الاشتراك متاحة في المحادثة الخاصة مع البوت.")
        return
    command = (getattr(message, "text", "").split()[0].split("@", 1)[0].lower())
    subscription = await _get_country_subscription(user_id)
    if not subscription or float(subscription.get("expires_at", 0) or 0) <= time.time():
        await bot.reply_to(message, "لا يوجد اشتراك نشط حاليًا.")
        return
    if command in {"/cancel_subscription", "/resume_subscription"}:
        charge_id = subscription.get("telegram_payment_charge_id")
        if not charge_id:
            await bot.reply_to(message, "تعذر العثور على مرجع الاشتراك؛ تواصل مع /support.")
            return
        cancel = command == "/cancel_subscription"
        if bool(subscription.get("is_canceled")) == cancel:
            status = "تم إيقاف التجديد مسبقًا" if cancel else "التجديد مفعّل مسبقًا"
            await bot.reply_to(message, f"{status}. يظل الوصول متاحًا حتى نهاية الفترة الحالية.")
            return
        try:
            changed = await bot.edit_user_star_subscription(
                user_id=user_id,
                telegram_payment_charge_id=charge_id,
                is_canceled=cancel,
            )
        except Exception as exc:
            print(f"Country subscription update failed: {type(exc).__name__}", flush=True)
            changed = False
        if not changed:
            await bot.reply_to(message, "تعذر تحديث التجديد الآن. أعد المحاولة أو استخدم /support.")
            return
        await _update_country_subscription(user_id, {"is_canceled": cancel})
        text = (
            "تم إيقاف التجديد التلقائي؛ يبقى الوصول متاحًا حتى نهاية الفترة الحالية."
            if cancel else "تمت إعادة تفعيل التجديد التلقائي."
        )
        await bot.reply_to(message, text)
        return
    expiration = datetime.fromtimestamp(
        float(subscription.get("expires_at", 0)), timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")
    renewal = "التجديد التلقائي موقوف" if subscription.get("is_canceled") else "التجديد تلقائي كل 30 يومًا"
    await bot.reply_to(
        message,
        f"اشتراك كشف الدولة نشط حتى {expiration}. {renewal}.\n"
        "استخدم /cancel_subscription لإيقاف التجديد، أو /resume_subscription لإعادته.",
    )


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("country:")))
async def country_button_callback(call):
    lookup_id = (call.data or "").removeprefix("country:")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", lookup_id):
        await bot.answer_callback_query(call.id, text="تعذر قراءة بيانات الدولة.", show_alert=True)
        return
    lookup = await _get_country_lookup(lookup_id)
    if not lookup or not lookup.get("country"):
        await bot.answer_callback_query(call.id, text="انتهت صلاحية بيانات هذا الزر. أعد البحث عن الحساب.", show_alert=True)
        return

    user_id = getattr(getattr(call, "from_user", None), "id", None)
    if user_id is None:
        await bot.answer_callback_query(call.id, text="تعذر تحديد المستخدم.", show_alert=True)
        return
    settings = await _get_country_access_settings()
    if settings.get("mode") != "paid" or await _has_active_country_subscription(user_id):
        await bot.answer_callback_query(call.id)
        destination = _country_delivery_chat_id(call, user_id)
        await _send_original_country_photo(destination, lookup)
        return

    call_message = getattr(call, "message", None)
    if getattr(getattr(call_message, "chat", None), "type", None) != "private":
        await bot.answer_callback_query(
            call.id,
            text="افتح المحادثة الخاصة مع البوت ثم اضغط زر الدولة مجددًا.",
            show_alert=True,
        )
        return

    try:
        pending = await _get_pending_country_order(user_id)
    except Exception as exc:
        print(f"Country pending order lookup failed: {type(exc).__name__}", flush=True)
        pending = None
    if pending:
        order_id, order = pending
        if order.get("amount") == settings.get("stars_price") and order.get("status") == "pending":
            order.update({
                "requested_lookup_id": lookup_id,
                "requested_country": lookup["country"],
                "username": lookup.get("username", ""),
            })
            await _store_country_order(order_id, order)
            await bot.answer_callback_query(call.id, text="لديك فاتورة اشتراك معلّقة.")
            await _send_country_invoice_link(user_id, order_id, order)
            return
        await _update_country_order(order_id, {"status": "price_changed"})
        await _clear_pending_country_order(user_id)

    price = settings.get("stars_price", 0)
    if not isinstance(price, int) or isinstance(price, bool) or price < 1 or price > _MAX_COUNTRY_STARS_PRICE:
        await bot.answer_callback_query(call.id, text="إعداد سعر الاشتراك غير صالح؛ أبلغ الإدارة.", show_alert=True)
        return
    await bot.answer_callback_query(call.id)
    await bot.send_message(
        chat_id=user_id,
        text=_country_terms_text(price),
        reply_markup=_country_terms_markup(lookup_id),
    )


async def _bridgly_list_button_callback(call, list_kind):
    prefix = "followers:" if list_kind == "followers" else "following:"
    lookup_id = (call.data or "").removeprefix(prefix)
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", lookup_id):
        await bot.answer_callback_query(call.id, text="تعذر قراءة بيانات القائمة.", show_alert=True)
        return
    lookup = await _get_country_lookup(lookup_id)
    if not lookup or not lookup.get("username"):
        await bot.answer_callback_query(
            call.id, text="انتهت صلاحية هذا الزر. أعد البحث عن الحساب.", show_alert=True
        )
        return
    if not BRIDGLY_API_KEY:
        await bot.answer_callback_query(
            call.id, text="خدمة قوائم المتابعين غير مهيأة حاليًا.", show_alert=True
        )
        return

    user_id = getattr(getattr(call, "from_user", None), "id", None)
    if user_id is None:
        await bot.answer_callback_query(call.id, text="تعذر تحديد المستخدم.", show_alert=True)
        return
    await bot.answer_callback_query(call.id, text="جارٍ جلب القائمة من المصدر…")
    destination = _country_delivery_chat_id(call, user_id)
    try:
        users = await _get_bridgly_social_list(lookup["username"], list_kind)
    except _BridglyPrivateAccountError:
        text = (
            "قائمة الحسابات التي يتابعها غير متاحة لأن حساب TikTok خاص."
            if list_kind == "following"
            else "قائمة المتابعين غير متاحة لأن حساب TikTok خاص."
        )
        await bot.send_message(chat_id=destination, text=text)
        return
    except _BridglyRequestError as exc:
        print(f"Bridgly {list_kind} lookup failed: {type(exc).__name__}", flush=True)
        text = (
            "تعذر جلب القائمة: لا يتوفر رصيد Bridgly مجاني حاليًا."
            if exc.status == 402
            else "تعذر جلب القائمة حاليًا. حاول مرة أخرى لاحقًا."
        )
        await bot.send_message(chat_id=destination, text=text)
        return
    except Exception as exc:
        print(f"Bridgly {list_kind} lookup failed: {type(exc).__name__}", flush=True)
        await bot.send_message(
            chat_id=destination, text="تعذر جلب القائمة حاليًا. حاول مرة أخرى لاحقًا."
        )
        return

    rich_message = _format_bridgly_list_rich_message(
        lookup["username"], users, list_kind
    )
    try:
        await bot.send_rich_message(chat_id=destination, rich_message=rich_message)
    except Exception as exc:
        print(f"Bridgly rich-list delivery failed: {type(exc).__name__}", flush=True)
        await bot.send_message(
            chat_id=destination,
            text=_format_bridgly_list_text(lookup["username"], users, list_kind),
        )


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("followers:")))
async def followers_button_callback(call):
    await _bridgly_list_button_callback(call, "followers")


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("following:")))
async def following_button_callback(call):
    await _bridgly_list_button_callback(call, "following")


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("country_agree:")))
async def country_agree_to_terms_callback(call):
    lookup_id = (call.data or "").removeprefix("country_agree:")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", lookup_id):
        await bot.answer_callback_query(call.id, text="تعذر قراءة بيانات الدولة.", show_alert=True)
        return
    user_id = getattr(getattr(call, "from_user", None), "id", None)
    call_message = getattr(call, "message", None)
    if user_id is None or getattr(getattr(call_message, "chat", None), "type", None) != "private":
        await bot.answer_callback_query(call.id, text="أكمل الموافقة في المحادثة الخاصة.", show_alert=True)
        return
    lookup = await _get_country_lookup(lookup_id)
    if not lookup or not lookup.get("country"):
        await bot.answer_callback_query(call.id, text="انتهت صلاحية بيانات هذا الزر. أعد البحث.", show_alert=True)
        return
    settings = await _get_country_access_settings()
    if settings.get("mode") != "paid":
        await bot.answer_callback_query(call.id, text="أصبح الكشف مجانيًا.")
        await _send_original_country_photo(user_id, lookup)
        return
    if await _has_active_country_subscription(user_id):
        await bot.answer_callback_query(call.id, text="اشتراكك نشط.")
        await _send_original_country_photo(user_id, lookup)
        return
    price = settings.get("stars_price", 0)
    if not isinstance(price, int) or isinstance(price, bool) or not 1 <= price <= _MAX_COUNTRY_STARS_PRICE:
        await bot.answer_callback_query(call.id, text="إعداد سعر الاشتراك غير صالح.", show_alert=True)
        return
    await bot.answer_callback_query(call.id, text="جارٍ تجهيز رابط الاشتراك الشهري.")
    try:
        order_id, order = await _create_or_reuse_country_order(user_id, lookup, price)
        await _send_country_invoice_link(user_id, order_id, order)
    except Exception as exc:
        print(f"Country subscription invoice creation failed: {type(exc).__name__}", flush=True)
        await bot.send_message(
            chat_id=user_id,
            text="تعذر تجهيز رابط الاشتراك الآن. لم يتم تحصيل أي نجوم؛ أعد المحاولة لاحقًا.",
        )


@bot.pre_checkout_query_handler(func=lambda query: True)
async def country_pre_checkout(query):
    async def validate():
        order = await _get_country_order(getattr(query, "invoice_payload", ""))
        settings = await _get_country_access_settings()
        user = getattr(query, "from_user", None)
        valid = bool(
            order
            and order.get("status") == "pending"
            and order.get("terms_accepted_at")
            and order.get("currency") == "XTR"
            and getattr(query, "currency", None) == "XTR"
            and user is not None
            and getattr(user, "id", None) == order.get("user_id")
            and getattr(query, "total_amount", None) == order.get("amount")
            and order.get("expires_at", 0) > time.time()
            and settings.get("mode") == "paid"
            and settings.get("stars_price") == order.get("amount")
            and not await _has_active_country_subscription(order.get("user_id"))
        )
        return valid

    try:
        valid = await asyncio.wait_for(validate(), timeout=7.0)
    except Exception as exc:
        print(f"Country pre-checkout validation failed: {type(exc).__name__}", flush=True)
        valid = False
    error_message = None if valid else "الفاتورة غير صالحة أو انتهت. أعد طلب الاشتراك من زر الدولة."
    await bot.answer_pre_checkout_query(
        pre_checkout_query_id=query.id,
        ok=valid,
        error_message=error_message,
    )


@bot.message_handler(content_types=["successful_payment"])
async def country_successful_payment(message):
    payment = getattr(message, "successful_payment", None)
    if payment is None:
        return
    order_id = getattr(payment, "invoice_payload", "")
    order = await _get_country_order(order_id)
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    charge_id = str(getattr(payment, "telegram_payment_charge_id", "") or "")
    expiration = getattr(payment, "subscription_expiration_date", None)
    is_recurring = getattr(payment, "is_recurring", None)
    now = time.time()
    try:
        expiration = int(expiration)
    except (TypeError, ValueError, OverflowError):
        expiration = 0
    first_payment = bool(order and order.get("status") == "pending")
    valid = bool(
        order
        and order.get("status") in {"pending", "paid"}
        and user_id == order.get("user_id")
        and getattr(payment, "currency", None) == "XTR"
        and order.get("currency") == "XTR"
        and getattr(payment, "total_amount", None) == order.get("amount")
        and is_recurring is True
        and expiration > now
        and charge_id
        and (not first_payment or (
            order.get("terms_accepted_at")
            and order.get("expires_at", 0) > now
            and getattr(payment, "is_first_recurring", None) in (None, True)
        ))
        and (first_payment or getattr(payment, "is_first_recurring", None) in (None, False))
    )
    if not valid:
        print("Country Stars subscription payment validation failed.", flush=True)
        return

    async with _COUNTRY_PAYMENT_LOCK:
        if await _country_payment_seen(charge_id):
            return
        try:
            await _save_country_subscription_payment(
                order_id=order_id,
                order=order,
                user_id=user_id,
                charge_id=charge_id,
                expiration=expiration,
                payment=payment,
            )
        except Exception as exc:
            print(f"Country subscription persistence failed: {type(exc).__name__}", flush=True)
            try:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text="وصل تأكيد الدفع، لكن تعذر تحديث حالة الاشتراك مؤقتًا. لا تدفع مرة أخرى؛ استخدم /support.",
                )
            except Exception:
                pass
            return

    expiration_text = datetime.fromtimestamp(expiration, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    await bot.send_message(
        chat_id=message.chat.id,
        text=f"تم تفعيل اشتراك كشف دولة الحساب الأصلية حتى {expiration_text}. "
        "يتجدد كل 30 يومًا ما لم توقف التجديد عبر /cancel_subscription.",
    )
    if first_payment and order.get("requested_lookup_id"):
        lookup = await _get_country_lookup(order["requested_lookup_id"])
        if lookup:
            await _send_original_country_photo(message.chat.id, lookup)


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("country_admin:")))
async def country_admin_callback(call):
    global _ADMIN_PENDING_PRICE_USER_ID
    user_id = getattr(getattr(call, "from_user", None), "id", None)
    if _ADMIN_USER_ID is None or user_id != _ADMIN_USER_ID:
        await bot.answer_callback_query(call.id, text="هذه اللوحة مخصصة للإدارة.", show_alert=True)
        return
    action = (call.data or "").removeprefix("country_admin:")
    settings = await _get_country_access_settings()
    if action == "price":
        _ADMIN_PENDING_PRICE_USER_ID = user_id
        await bot.answer_callback_query(call.id)
        await bot.send_message(
            chat_id=call.message.chat.id,
            text=f"أرسل سعرًا صحيحًا بالنجوم من 1 إلى {_MAX_COUNTRY_STARS_PRICE}.",
        )
        return
    if action == "free":
        await _save_country_access_settings("free", settings.get("stars_price", 0))
    elif action == "paid":
        if settings.get("stars_price", 0) < 1:
            await bot.answer_callback_query(call.id, text="حدّد السعر أولًا.", show_alert=True)
            return
        await _save_country_access_settings("paid", settings["stars_price"])
    else:
        await bot.answer_callback_query(call.id, text="خيار غير معروف.", show_alert=True)
        return
    settings = await _get_country_access_settings()
    await bot.answer_callback_query(call.id, text="تم حفظ الإعداد.")
    await bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=_country_admin_panel_text(settings),
        reply_markup=_country_admin_markup(),
    )


@bot.message_handler(func=lambda message: True)
async def handle_username_message(message):
    global _ADMIN_USER_ID, _ADMIN_PENDING_PRICE_USER_ID
    raw_text = getattr(message, "text", None) or ""
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if ADMIN_PANEL_COMMAND and user_id is not None and hmac.compare_digest(raw_text, ADMIN_PANEL_COMMAND):
        if getattr(getattr(message, "chat", None), "type", None) != "private":
            await bot.reply_to(message, "لوحة الإدارة متاحة في المحادثة الخاصة فقط.")
            return
        _ADMIN_USER_ID = user_id
        _ADMIN_PENDING_PRICE_USER_ID = None
        settings = await _get_country_access_settings()
        await bot.send_message(
            chat_id=message.chat.id,
            text=_country_admin_panel_text(settings),
            reply_markup=_country_admin_markup(),
        )
        return

    if user_id is not None and user_id in _SUPPORT_PENDING_USER_IDS:
        _SUPPORT_PENDING_USER_IDS.discard(user_id)
        support_text = raw_text.strip()[:1500] or "(رسالة دعم فارغة)"
        if _ADMIN_USER_ID is not None:
            try:
                await bot.send_message(
                    chat_id=_ADMIN_USER_ID,
                    text=f"طلب دعم اشتراك من المستخدم {user_id}:\n{support_text}",
                )
                await bot.reply_to(message, "تم إرسال طلب الدعم إلى الإدارة.")
            except Exception:
                await bot.reply_to(message, "تعذر إرسال الطلب الآن. أعد المحاولة عبر /support.")
        else:
            await bot.reply_to(message, "الإدارة غير متصلة حاليًا؛ احتفظ برقم الدفع واستخدم /support لاحقًا.")
        return

    if user_id is not None and user_id == _ADMIN_PENDING_PRICE_USER_ID:
        if getattr(getattr(message, "chat", None), "type", None) != "private":
            await bot.reply_to(message, "أرسل السعر في المحادثة الخاصة مع البوت.")
            return
        price_text = raw_text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")).strip()
        if price_text.isdigit() and 1 <= int(price_text) <= _MAX_COUNTRY_STARS_PRICE:
            settings = await _get_country_access_settings()
            await _save_country_access_settings(settings.get("mode", "free"), int(price_text))
            _ADMIN_PENDING_PRICE_USER_ID = None
            settings = await _get_country_access_settings()
            await bot.send_message(
                chat_id=message.chat.id,
                text=_country_admin_panel_text(settings),
                reply_markup=_country_admin_markup(),
            )
        else:
            await bot.reply_to(
                message,
                f"أرسل عددًا صحيحًا من 1 إلى {_MAX_COUNTRY_STARS_PRICE} نجمة.",
            )
        return

    username = normalize_username(raw_text)
    if not username:
        await bot.reply_to(
            message,
            "أرسل اسم مستخدم TikTok صالحًا (من 1 إلى 24 حرفًا: أحرف أو أرقام أو نقطة أو شرطة سفلية).",
        )
        return

    if user_id is None:
        user_id = f"chat:{message.chat.id}"
    cooldown = await _reserve_user_search(user_id)
    if cooldown:
        await _send_search_cooldown_message(message, cooldown)
        return

    await _start_username_search(message, username)


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("retry:")))
async def retry_lookup_callback(call):
    username = normalize_username((call.data or "").removeprefix("retry:"))
    if not username:
        await bot.answer_callback_query(call.id, text="تعذر قراءة اسم المستخدم.")
        return

    message = call.message
    chat_id = message.chat.id
    retry_key = f"{chat_id}:{username.casefold()}"
    if retry_key in _RETRY_IN_PROGRESS:
        await bot.answer_callback_query(call.id, text="جارٍ استرجاع النتيجة الآن.")
        return

    user_id = getattr(getattr(call, "from_user", None), "id", None)
    if user_id is None:
        user_id = f"chat:{chat_id}"
    cooldown = await _reserve_user_search(user_id)
    if cooldown:
        await bot.answer_callback_query(call.id)
        if _progress_animation_file_id(message):
            is_animation = "rich" if getattr(message, "rich_message", None) else "animation"
        else:
            is_animation = False
        await _show_search_cooldown_on_existing_message(
            chat_id, message, is_animation, cooldown
        )
        return

    _RETRY_IN_PROGRESS.add(retry_key)
    try:
        await bot.answer_callback_query(call.id)
        if _progress_animation_file_id(message):
            is_animation = "rich" if getattr(message, "rich_message", None) else "animation"
        else:
            is_animation = False
        # إذا كان هناك بحث نشط حالياً نتابعه، وإلا نبدأ بحثاً جديداً ببروكسي جديد
        cached_result, task, is_cached = _get_or_start_lookup(username)
        if is_cached and isinstance(cached_result, str):
            # إذا كانت النتيجة المخزنة مجرد رسالة خطأ، نلغي الكاش لنعيد المحاولة ببروكسي جديد
            cached_result, task, is_cached = _get_or_start_lookup(username, force_fresh=True)

        if is_cached:
            await _show_cached_progress(
                chat_id, message, username, is_animation, cached_result
            )
        elif task:
            await _retry_active_lookup(
                chat_id, message, username, is_animation, task
            )
    finally:
        _RETRY_IN_PROGRESS.discard(retry_key)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global WEBHOOK_SECRET
    await _initialize_proxy_storage()
    public_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not public_url:
        raise RuntimeError("RENDER_EXTERNAL_URL is required to configure Telegram webhook")

    secret_seed = WEBHOOK_SECRET_SEED or BOT_TOKEN
    secret_payload = f"tikwahm-webhook:{public_url}".encode("utf-8")
    digest = hmac.new(secret_seed.encode("utf-8"), secret_payload, hashlib.sha256).digest()
    WEBHOOK_SECRET = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    webhook_url = f"{public_url}{WEBHOOK_PATH}"
    webhook_set = await bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=["message", "callback_query", "pre_checkout_query"],
    )
    if not webhook_set:
        raise RuntimeError("Telegram did not accept the webhook configuration")
    print("TikWahm webhook configured.", flush=True)
    yield



PROXY_DASHBOARD_FILE = os.environ.get(
    "TIKWAHM_PROXY_DASHBOARD_FILE",
    os.path.join(os.path.dirname(__file__), "proxy_dashboard.json"),
).strip()

FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
_FIRESTORE_PROXY_DOCUMENT = None
_FIRESTORE_COUNTRY_SETTINGS_DOCUMENT = None
_FIRESTORE_CLIENT = None
_FIRESTORE_PROXY_VERSION = 0
if FIREBASE_SERVICE_ACCOUNT_JSON:
    try:
        _firebase_service_account = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
        _firebase_project_id = _firebase_service_account.get("project_id")
        if not isinstance(_firebase_service_account, dict) or not _firebase_project_id:
            raise ValueError("missing project_id")
        _firebase_app = firebase_admin.initialize_app(
            firebase_credentials.Certificate(_firebase_service_account),
            {"projectId": _firebase_project_id},
            name="tikwahm_proxy_dashboard",
        )
        _FIRESTORE_CLIENT = firestore.client(app=_firebase_app)
        _FIRESTORE_PROXY_DOCUMENT = _FIRESTORE_CLIENT.collection("private_config").document("proxy_dashboard")
        _FIRESTORE_COUNTRY_SETTINGS_DOCUMENT = _FIRESTORE_CLIENT.collection("private_config").document("fintok_country_access")
    except Exception:
        raise RuntimeError(
            "تعذر تهيئة Firebase. تحقق من قيمة FIREBASE_SERVICE_ACCOUNT_JSON وصلاحية حساب الخدمة."
        ) from None


_COUNTRY_LOOKUP_TTL_SECONDS = 30 * 24 * 60 * 60
_COUNTRY_ORDER_TTL_SECONDS = 30 * 60
_MAX_COUNTRY_STARS_PRICE = 10_000
_COUNTRY_SUBSCRIPTION_PERIOD_SECONDS = 2_592_000


def _country_button_markup(lookup_id, include_country=True):
    markup = types.InlineKeyboardMarkup()
    if include_country:
        markup.add(types.InlineKeyboardButton(
            "دولة الحساب الأصلية", callback_data=f"country:{lookup_id}"
        ))
    markup.row(
        types.InlineKeyboardButton(
            "قائمة المتابعين", callback_data=f"followers:{lookup_id}"
        ),
        types.InlineKeyboardButton(
            "قائمة اللي يتابعهم", callback_data=f"following:{lookup_id}"
        ),
    )
    return markup


def _country_admin_markup():
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("مجاني", callback_data="country_admin:free"),
        types.InlineKeyboardButton("مدفوع بالنجوم", callback_data="country_admin:paid"),
    )
    markup.add(types.InlineKeyboardButton(
        "تحديد السعر بالنجوم", callback_data="country_admin:price"
    ))
    return markup


async def _store_country_lookup(username, country):
    lookup_id = secrets.token_urlsafe(12)
    record = {
        "username": str(username or "")[:64],
        "country": str(country)[:300],
        "created_at": time.time(),
        "expires_at": time.time() + _COUNTRY_LOOKUP_TTL_SECONDS,
    }
    _COUNTRY_LOOKUP_CACHE[lookup_id] = record
    if _FIRESTORE_CLIENT is not None:
        document = _FIRESTORE_CLIENT.collection("fintok_country_lookups").document(lookup_id)
        await asyncio.to_thread(document.set, record)
    return lookup_id


async def _get_country_lookup(lookup_id):
    if not isinstance(lookup_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", lookup_id):
        return None
    record = _COUNTRY_LOOKUP_CACHE.get(lookup_id)
    if record is None and _FIRESTORE_CLIENT is not None:
        document = _FIRESTORE_CLIENT.collection("fintok_country_lookups").document(lookup_id)
        snapshot = await asyncio.to_thread(document.get)
        if snapshot.exists:
            record = snapshot.to_dict() or {}
            _COUNTRY_LOOKUP_CACHE[lookup_id] = record
    try:
        expires_at = float(record.get("expires_at", 0)) if isinstance(record, dict) else 0
    except (TypeError, ValueError):
        expires_at = 0
    if not isinstance(record, dict) or expires_at <= time.time():
        _COUNTRY_LOOKUP_CACHE.pop(lookup_id, None)
        return None
    return dict(record)


class _BridglyConfigurationError(Exception):
    """The social-list provider has no configured credential."""


class _BridglyRequestError(Exception):
    """The provider returned an unusable or unsuccessful social-list response."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class _BridglyPrivateAccountError(_BridglyRequestError):
    """TikTok does not expose the requested following list for a private account."""


def _request_bridgly_social_list(username, list_kind):
    endpoints = {
        "followers": BRIDGLY_FOLLOWERS_URL,
        "following": BRIDGLY_FOLLOWING_URL,
    }
    endpoint = endpoints.get(list_kind)
    if not endpoint:
        raise ValueError("unknown Bridgly list kind")
    if not BRIDGLY_API_KEY:
        raise _BridglyConfigurationError("Bridgly API key is not configured")
    payload = json.dumps({
        "uniqueId": str(username or "").lstrip("@")[:24],
        "count": MAX_FOLLOWER_NAMES,
        "cacheMaxAgeHours": BRIDGLY_LIST_CACHE_MAX_AGE_HOURS,
    }).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {BRIDGLY_API_KEY}",
            "User-Agent": "TikWahm/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=BRIDGLY_REQUEST_TIMEOUT_SECONDS
        ) as response:
            status = response.getcode()
            raw_body = response.read(MAX_SOURCE_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            error_payload = json.loads(exc.read().decode("utf-8", "replace"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            error_payload = {}
        finally:
            exc.close()
        provider_message = str(error_payload.get("message") or "")
        if status == 403 and "private" in provider_message.casefold():
            raise _BridglyPrivateAccountError(
                "TikTok account is private", status=status
            ) from None
        raise _BridglyRequestError(f"Bridgly HTTP {status}", status=status) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise _BridglyRequestError(type(exc).__name__) from None

    if not isinstance(status, int) or status < 200 or status >= 300:
        raise _BridglyRequestError(f"Bridgly HTTP {status}", status=status)
    if len(raw_body) > MAX_SOURCE_RESPONSE_BYTES:
        raise _BridglyRequestError("Bridgly returned an oversized response")
    try:
        response_payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _BridglyRequestError(type(exc).__name__) from None
    data = response_payload.get("data") if isinstance(response_payload, dict) else None
    raw_users = data.get("users") if isinstance(data, dict) else None
    if not isinstance(raw_users, list):
        raise _BridglyRequestError("Bridgly response has no users list")

    users = []
    seen = set()
    for user in raw_users:
        if not isinstance(user, dict):
            continue
        handle = str(
            user.get("uniqueId") or user.get("username") or user.get("unique_id") or ""
        ).strip().lstrip("@")
        nickname = str(
            user.get("nickname") or user.get("displayName") or user.get("display_name") or ""
        ).strip()
        avatar_url = str(user.get("avatarUrl") or user.get("avatar_url") or "").strip()
        parsed_avatar = urlparse(avatar_url)
        if (
            parsed_avatar.scheme != "https" or not parsed_avatar.hostname
            or parsed_avatar.hostname.casefold() in {"localhost", "127.0.0.1"}
        ):
            avatar_url = ""
        handle = re.sub(r"\s+", " ", handle)[:64]
        nickname = re.sub(r"\s+", " ", nickname)[:100]
        identity = (handle or nickname).casefold()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        users.append({"handle": handle, "nickname": nickname, "avatar_url": avatar_url})
        if len(users) >= MAX_FOLLOWER_NAMES:
            break
    return users


def _request_bridgly_followers(username):
    return _request_bridgly_social_list(username, "followers")


def _request_bridgly_following(username):
    return _request_bridgly_social_list(username, "following")


async def _get_bridgly_social_list(username, list_kind):
    requesters = {
        "followers": _request_bridgly_followers,
        "following": _request_bridgly_following,
    }
    requester = requesters.get(list_kind)
    if requester is None:
        raise ValueError("unknown Bridgly list kind")
    normalized = str(username or "").strip().lstrip("@")[:24]
    if not normalized:
        raise _BridglyRequestError("Missing TikTok username")
    if not BRIDGLY_API_KEY:
        raise _BridglyConfigurationError("Bridgly API key is not configured")
    cache_key = f"{list_kind}:{normalized.casefold()}"
    now = time.time()
    cached = _BRIDGLY_LIST_CACHE.get(cache_key)
    if isinstance(cached, dict) and cached.get("expires_at", 0) > now:
        _BRIDGLY_LIST_CACHE.move_to_end(cache_key)
        return [dict(user) for user in cached.get("users", [])]

    async with _BRIDGLY_LIST_LOCK:
        cached = _BRIDGLY_LIST_CACHE.get(cache_key)
        if isinstance(cached, dict) and cached.get("expires_at", 0) > time.time():
            _BRIDGLY_LIST_CACHE.move_to_end(cache_key)
            return [dict(user) for user in cached.get("users", [])]
        task = _BRIDGLY_LIST_TASKS.get(cache_key)
        if task is None:
            task = asyncio.create_task(asyncio.to_thread(requester, normalized))
            _BRIDGLY_LIST_TASKS[cache_key] = task

    try:
        users = await asyncio.shield(task)
    finally:
        if task.done():
            async with _BRIDGLY_LIST_LOCK:
                if _BRIDGLY_LIST_TASKS.get(cache_key) is task:
                    _BRIDGLY_LIST_TASKS.pop(cache_key, None)

    async with _BRIDGLY_LIST_LOCK:
        _BRIDGLY_LIST_CACHE[cache_key] = {
            "users": [dict(user) for user in users],
            "expires_at": time.time() + BRIDGLY_LIST_CACHE_TTL_SECONDS,
        }
        _BRIDGLY_LIST_CACHE.move_to_end(cache_key)
        while len(_BRIDGLY_LIST_CACHE) > 1000:
            _BRIDGLY_LIST_CACHE.popitem(last=False)
    return [dict(user) for user in users]


async def _get_bridgly_followers(username):
    return await _get_bridgly_social_list(username, "followers")


async def _get_bridgly_following(username):
    return await _get_bridgly_social_list(username, "following")


def _format_bridgly_list_text(username, users, list_kind):
    username = str(username or "").lstrip("@")
    title = (
        "قائمة المتابعين"
        if list_kind == "followers"
        else "قائمة الحسابات التي يتابعها"
    )
    lines = [title, f"الحساب @{username}"]
    for user in (users or [])[:MAX_FOLLOWER_NAMES]:
        handle = str(user.get("handle") or "").strip().lstrip("@")
        nickname = str(user.get("nickname") or "").strip()
        if handle and nickname:
            lines.append(f"• {nickname} — @{handle}")
        elif handle:
            lines.append(f"• @{handle}")
        elif nickname:
            lines.append(f"• {nickname}")
    if len(lines) == 2:
        lines.append("لم يُعد المصدر أسماءً لهذه القائمة.")
    return "\n".join(lines)


def _format_followers_message(username, users):
    return _format_bridgly_list_text(username, users, "followers")


def _format_bridgly_list_rich_message(username, users, list_kind):
    username = str(username or "").lstrip("@")
    title = (
        "قائمة المتابعين"
        if list_kind == "followers"
        else "قائمة الحسابات التي يتابعها"
    )
    blocks = [
        types.InputRichBlockSectionHeading(text=title, size=3),
        types.InputRichBlockParagraph(text=f"الحساب @{username}"),
    ]
    has_users = False
    for user in (users or [])[:MAX_FOLLOWER_NAMES]:
        handle = str(user.get("handle") or "").strip().lstrip("@")
        nickname = str(user.get("nickname") or "").strip() or "اسم غير متاح"
        caption_parts = [f"الاسم: {nickname}"]
        if handle:
            caption_parts.append(f"المستخدم: @{handle}")
        caption = "\n".join(caption_parts)
        avatar_url = str(user.get("avatar_url") or "").strip()
        parsed_avatar = urlparse(avatar_url)
        has_users = has_users or bool(handle or nickname)
        if parsed_avatar.scheme == "https" and parsed_avatar.hostname:
            photo = types.InputMediaPhoto(media=avatar_url)
            blocks.append(types.InputRichBlockPhoto(
                photo=photo,
                caption=types.RichBlockCaption(text=caption),
            ))
        else:
            blocks.append(types.InputRichBlockParagraph(text=caption))
    if not has_users:
        blocks.append(types.InputRichBlockParagraph(text="لم يُعد المصدر أسماءً لهذه القائمة."))
    return types.InputRichMessage(blocks=blocks, is_rtl=True)


async def _get_country_access_settings():
    if _FIRESTORE_COUNTRY_SETTINGS_DOCUMENT is not None:
        try:
            snapshot = await asyncio.to_thread(_FIRESTORE_COUNTRY_SETTINGS_DOCUMENT.get)
            if snapshot.exists:
                stored = snapshot.to_dict() or {}
                if stored.get("billing_model") != "monthly":
                    # Previous settings were per-result purchases; never carry paid mode
                    # forward into the new recurring product without an explicit admin choice.
                    stored = {
                        "mode": "free",
                        "stars_price": 0,
                        "billing_model": "monthly",
                        "updated_at": time.time(),
                    }
                    await asyncio.to_thread(_FIRESTORE_COUNTRY_SETTINGS_DOCUMENT.set, stored)
                    _COUNTRY_ACCESS_SETTINGS_CACHE.update({"mode": "free", "stars_price": 0})
                else:
                    mode = stored.get("mode")
                    price = stored.get("stars_price")
                    if mode in {"free", "paid"} and isinstance(price, int) and not isinstance(price, bool) and 0 <= price <= _MAX_COUNTRY_STARS_PRICE:
                        if mode == "paid" and price == 0:
                            mode = "free"
                        _COUNTRY_ACCESS_SETTINGS_CACHE.update({"mode": mode, "stars_price": price})
        except Exception as exc:
            print(f"Country settings read failed: {type(exc).__name__}", flush=True)
    return dict(_COUNTRY_ACCESS_SETTINGS_CACHE)


async def _save_country_access_settings(mode, stars_price):
    if mode not in {"free", "paid"} or not isinstance(stars_price, int) or isinstance(stars_price, bool):
        raise ValueError("invalid country access settings")
    if not 0 <= stars_price <= _MAX_COUNTRY_STARS_PRICE:
        raise ValueError("country Stars price is out of range")
    if mode == "paid" and stars_price < 1:
        raise ValueError("paid country access requires a positive monthly price")
    payload = {
        "mode": mode,
        "stars_price": stars_price,
        "billing_model": "monthly",
        "updated_at": time.time(),
    }
    if _FIRESTORE_COUNTRY_SETTINGS_DOCUMENT is not None:
        await asyncio.to_thread(_FIRESTORE_COUNTRY_SETTINGS_DOCUMENT.set, payload)
    _COUNTRY_ACCESS_SETTINGS_CACHE.update({"mode": mode, "stars_price": stars_price})
    return dict(_COUNTRY_ACCESS_SETTINGS_CACHE)


async def _store_country_order(order_id, record):
    _COUNTRY_ORDER_CACHE[order_id] = dict(record)
    if _FIRESTORE_CLIENT is not None:
        document = _FIRESTORE_CLIENT.collection("fintok_country_orders").document(order_id)
        await asyncio.to_thread(document.set, dict(record))


async def _get_country_order(order_id):
    if not isinstance(order_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", order_id):
        return None
    record = _COUNTRY_ORDER_CACHE.get(order_id)
    if record is None and _FIRESTORE_CLIENT is not None:
        document = _FIRESTORE_CLIENT.collection("fintok_country_orders").document(order_id)
        snapshot = await asyncio.to_thread(document.get)
        if snapshot.exists:
            record = snapshot.to_dict() or {}
            _COUNTRY_ORDER_CACHE[order_id] = record
    return dict(record) if isinstance(record, dict) else None


async def _update_country_order(order_id, updates):
    record = await _get_country_order(order_id)
    if record is None:
        return None
    record.update(updates)
    await _store_country_order(order_id, record)
    return record


def _country_user_key(user_id):
    return str(user_id)


async def _has_active_country_subscription(user_id, now=None):
    if user_id is None:
        return False
    now = time.time() if now is None else now
    subscription = await _get_country_subscription(user_id)
    try:
        return bool(subscription and float(subscription.get("expires_at", 0)) > now)
    except (TypeError, ValueError):
        return False


async def _get_country_subscription(user_id):
    key = _country_user_key(user_id)
    cached = _COUNTRY_SUBSCRIPTION_CACHE.get(key)
    if isinstance(cached, dict) and float(cached.get("expires_at", 0) or 0) > time.time():
        return dict(cached)
    if _FIRESTORE_CLIENT is None:
        return dict(cached) if isinstance(cached, dict) else None
    try:
        document = _FIRESTORE_CLIENT.collection("fintok_country_subscriptions").document(key)
        snapshot = await asyncio.to_thread(document.get)
        if not snapshot.exists:
            _COUNTRY_SUBSCRIPTION_CACHE.pop(key, None)
            return None
        record = snapshot.to_dict() or {}
        _COUNTRY_SUBSCRIPTION_CACHE[key] = record
        return dict(record)
    except Exception as exc:
        print(f"Country subscription read failed: {type(exc).__name__}", flush=True)
        return None


async def _update_country_subscription(user_id, updates):
    key = _country_user_key(user_id)
    current = await _get_country_subscription(user_id) or {"user_id": user_id}
    current.update(updates)
    current["updated_at"] = time.time()
    if _FIRESTORE_CLIENT is not None:
        document = _FIRESTORE_CLIENT.collection("fintok_country_subscriptions").document(key)
        await asyncio.to_thread(document.set, current, merge=True)
    _COUNTRY_SUBSCRIPTION_CACHE[key] = current
    return dict(current)


async def _get_pending_country_order(user_id):
    user_key = _country_user_key(user_id)
    order_id = _COUNTRY_PENDING_ORDER_CACHE.get(user_key)
    if not order_id and _FIRESTORE_CLIENT is not None:
        pointer = _FIRESTORE_CLIENT.collection("fintok_country_pending_orders").document(user_key)
        snapshot = await asyncio.to_thread(pointer.get)
        if snapshot.exists:
            order_id = (snapshot.to_dict() or {}).get("order_id")
            if order_id:
                _COUNTRY_PENDING_ORDER_CACHE[user_key] = order_id
    if not order_id:
        return None
    order = await _get_country_order(order_id)
    if (
        not order or order.get("status") != "pending"
        or float(order.get("expires_at", 0) or 0) <= time.time()
    ):
        if order and order.get("status") == "pending":
            await _update_country_order(order_id, {"status": "expired"})
        await _clear_pending_country_order(user_id)
        return None
    return order_id, order


async def _set_pending_country_order(user_id, order_id):
    user_key = _country_user_key(user_id)
    _COUNTRY_PENDING_ORDER_CACHE[user_key] = order_id
    if _FIRESTORE_CLIENT is not None:
        document = _FIRESTORE_CLIENT.collection("fintok_country_pending_orders").document(user_key)
        await asyncio.to_thread(document.set, {
            "user_id": user_id, "order_id": order_id, "updated_at": time.time(),
        })


async def _clear_pending_country_order(user_id):
    user_key = _country_user_key(user_id)
    _COUNTRY_PENDING_ORDER_CACHE.pop(user_key, None)
    if _FIRESTORE_CLIENT is not None:
        document = _FIRESTORE_CLIENT.collection("fintok_country_pending_orders").document(user_key)
        await asyncio.to_thread(document.delete)


async def _country_payment_seen(charge_id):
    if charge_id in _COUNTRY_PROCESSED_PAYMENT_CACHE:
        return True
    if _FIRESTORE_CLIENT is None:
        return False
    payment_key = hashlib.sha256(charge_id.encode("utf-8")).hexdigest()
    document = _FIRESTORE_CLIENT.collection("fintok_country_payments").document(payment_key)
    snapshot = await asyncio.to_thread(document.get)
    if snapshot.exists:
        _COUNTRY_PROCESSED_PAYMENT_CACHE.add(charge_id)
        return True
    return False


async def _save_country_subscription_payment(
    *, order_id, order, user_id, charge_id, expiration, payment
):
    now = time.time()
    user_key = _country_user_key(user_id)
    previous = _COUNTRY_SUBSCRIPTION_CACHE.get(user_key, {})
    if not previous and _FIRESTORE_CLIENT is not None:
        previous = await _get_country_subscription(user_id) or {}
    subscription = {
        "user_id": user_id,
        "status": "active",
        "expires_at": int(expiration),
        "currency": "XTR",
        "amount": int(order["amount"]),
        "is_recurring": True,
        "is_canceled": False,
        "telegram_payment_charge_id": charge_id,
        "last_order_id": order_id,
        "started_at": previous.get("started_at", now),
        "updated_at": now,
    }
    payment_record = {
        "user_id": user_id,
        "order_id": order_id,
        "telegram_payment_charge_id": charge_id,
        "currency": "XTR",
        "amount": int(order["amount"]),
        "subscription_expiration_date": int(expiration),
        "is_recurring": True,
        "is_first_recurring": getattr(payment, "is_first_recurring", None),
        "paid_at": now,
    }
    updated_order = dict(order)
    updated_order.update({
        "status": "paid",
        "telegram_payment_charge_id": charge_id,
        "last_payment_at": now,
        "subscription_expiration_date": int(expiration),
    })

    if _FIRESTORE_CLIENT is not None:
        payment_key = hashlib.sha256(charge_id.encode("utf-8")).hexdigest()
        subscription_doc = _FIRESTORE_CLIENT.collection("fintok_country_subscriptions").document(user_key)
        order_doc = _FIRESTORE_CLIENT.collection("fintok_country_orders").document(order_id)
        payment_doc = _FIRESTORE_CLIENT.collection("fintok_country_payments").document(payment_key)
        pending_doc = _FIRESTORE_CLIENT.collection("fintok_country_pending_orders").document(user_key)

        def commit_records():
            batch = _FIRESTORE_CLIENT.batch()
            batch.set(subscription_doc, subscription)
            batch.set(order_doc, updated_order)
            batch.set(payment_doc, payment_record)
            batch.delete(pending_doc)
            batch.commit()

        await asyncio.to_thread(commit_records)

    _COUNTRY_SUBSCRIPTION_CACHE[user_key] = subscription
    _COUNTRY_ORDER_CACHE[order_id] = updated_order
    _COUNTRY_PENDING_ORDER_CACHE.pop(user_key, None)
    _COUNTRY_PROCESSED_PAYMENT_CACHE.add(charge_id)



def _country_terms_text(price=None):
    price_line = (
        f"السعر: {price} ⭐ كل 30 يومًا.\n"
        if isinstance(price, int) and price > 0 else "السعر المدفوع إن فُعّل سيظهر قبل الدفع.\n"
    )
    return (
        "شروط كشف دولة الحساب الأصلية\n\n"
        "الاشتراك خدمة رقمية تُحصّل عبر Telegram Stars، ومدته الثابتة 30 يومًا "
        "ويتجدد تلقائيًا كل 30 يومًا ما لم توقف التجديد. لا يدعم Telegram تغيير "
        "مدة الاشتراك المتكرر إلى أيام أو ساعات أو دقائق. يبقى الوصول متاحًا حتى "
        "نهاية الفترة المدفوعة.\n"
        + price_line
        + "بيانات الدولة مصدرها FinTok وقد لا تكون دقيقة أو مؤكدة. "
        "اضغط «أوافق على الشروط وأتابع» للمتابعة إلى رابط الدفع.\n"
        "إدارة التجديد: /subscription و /cancel_subscription. للدعم: /support."
    )


def _country_terms_markup(lookup_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        "أوافق على الشروط وأتابع", callback_data=f"country_agree:{lookup_id}"
    ))
    return markup


def _country_invoice_markup(invoice_link, price):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        f"اشتراك شهري — {price} ⭐", url=invoice_link
    ))
    return markup


def _country_delivery_chat_id(call, user_id):
    chat = getattr(call, "message", None)
    chat = getattr(chat, "chat", None)
    return chat.id if getattr(chat, "type", None) == "private" else user_id


async def _send_original_country_photo(chat_id, lookup):
    country = str(lookup.get("country") or "").strip()
    if not country:
        return False
    caption = f"دولة الحساب الأصلية بحسب FinTok:\n{country}"
    flag_url = _country_flag_png_url(country)
    if flag_url:
        try:
            await bot.send_photo(chat_id=chat_id, photo=flag_url, caption=caption)
            return True
        except Exception as exc:
            print(f"Country flag PNG delivery failed: {type(exc).__name__}", flush=True)
    await bot.send_message(
        chat_id=chat_id,
        text=caption + "\nتعذر تحديد رمز ISO لعرض صورة العلم.",
    )
    return False


async def _create_or_reuse_country_order(user_id, lookup, price):
    async with _COUNTRY_ORDER_LOCK:
        pending = await _get_pending_country_order(user_id)
        if pending:
            order_id, order = pending
            if order.get("amount") == price and order.get("currency") == "XTR":
                order.update({
                    "requested_lookup_id": lookup.get("lookup_id") or "",
                    "requested_country": lookup.get("country", ""),
                    "username": lookup.get("username", ""),
                })
                await _store_country_order(order_id, order)
                return order_id, order
            await _update_country_order(order_id, {"status": "price_changed"})
            await _clear_pending_country_order(user_id)

        order_id = secrets.token_urlsafe(16)
        order = {
            "user_id": user_id,
            "currency": "XTR",
            "amount": int(price),
            "status": "pending",
            "created_at": time.time(),
            "expires_at": time.time() + _COUNTRY_ORDER_TTL_SECONDS,
            "terms_accepted_at": time.time(),
            "requested_lookup_id": lookup.get("lookup_id") or "",
            "requested_country": lookup.get("country", ""),
            "username": lookup.get("username", ""),
        }
        await _store_country_order(order_id, order)
        await _set_pending_country_order(user_id, order_id)
        if not order.get("invoice_link"):
            order["invoice_link"] = await bot.create_invoice_link(
                title="اشتراك كشف دولة الحساب الأصلية",
                description="اشتراك رقمي متجدد كل 30 يومًا لكشف دولة الحساب الأصلية في نتائج البحث.",
                payload=order_id,
                provider_token="",
                currency="XTR",
                prices=[types.LabeledPrice(label="اشتراك كشف الدولة لمدة شهر", amount=int(price))],
                subscription_period=_COUNTRY_SUBSCRIPTION_PERIOD_SECONDS,
            )
            await _store_country_order(order_id, order)
        return order_id, order


async def _send_country_invoice_link(user_id, order_id, order):
    invoice_link = order.get("invoice_link")
    if not invoice_link:
        invoice_link = await bot.create_invoice_link(
            title="اشتراك كشف دولة الحساب الأصلية",
            description="اشتراك رقمي متجدد كل 30 يومًا لكشف دولة الحساب الأصلية في نتائج البحث.",
            payload=order_id,
            provider_token="",
            currency="XTR",
            prices=[types.LabeledPrice(
                label="اشتراك كشف الدولة لمدة شهر", amount=int(order["amount"])
            )],
            subscription_period=_COUNTRY_SUBSCRIPTION_PERIOD_SECONDS,
        )
        order["invoice_link"] = invoice_link
        await _store_country_order(order_id, order)
    await bot.send_message(
        chat_id=user_id,
        text=(
            f"وافقْت على الشروط. رابط الاشتراك الشهري: {order['amount']} ⭐ كل 30 يومًا. "
            "لا يتم تفعيل الوصول إلا بعد تأكيد Telegram للدفع. "
            "يمكنك إيقاف التجديد عبر /cancel_subscription."
        ),
        reply_markup=_country_invoice_markup(invoice_link, order["amount"]),
    )


def _country_admin_panel_text(settings):
    if settings.get("mode") == "paid":
        current = f"مدفوع — {settings.get('stars_price', 0)} ⭐ شهريًا، يتجدد كل 30 يومًا"
    else:
        price = settings.get("stars_price", 0)
        current = "مجاني" + (f" (السعر المحفوظ: {price} ⭐ شهريًا)" if price else "")
    return (
        "لوحة دولة الحساب الأصلية\nالوضع الحالي: " + current
        + "\n\nمدة اشتراك Telegram Stars المتكرر ثابتة: 30 يومًا؛ لا يدعم Telegram "
        "تحديد مدة مخصصة بالأيام أو الساعات أو الدقائق. "
        "اختر المجاني أو المدفوع، ثم حدّد سعرًا شهريًا من 1 إلى 10,000 نجمة."
    )


def _parse_proxy_source_line(raw_line: str):
    line = raw_line.strip()
    enabled = True
    if line.casefold().startswith("# disabled:"):
        enabled = False
        line = line.split(":", 1)[1].strip()
    elif not line or line.startswith("#"):
        return None
    parts = line.split(":", 3)
    if len(parts) != 4:
        return None
    host, port, username, password = parts
    if (
        not host or not re.fullmatch(r"[A-Za-z0-9.-]+", host)
        or ".." in host or not port.isdigit() or not username or not password
        or any(char in username + password for char in "\r\n\x00")
    ):
        return None
    port_number = int(port)
    if not 1 <= port_number <= 65535:
        return None
    return {
        "id": secrets.token_urlsafe(12),
        "host": host.rstrip("."),
        "port": port_number,
        "username": username,
        "password": password,
        "enabled": enabled,
    }


def _load_proxy_admin_entries(path: str):
    try:
        encoded = os.environ.get("TIKWAHM_PROXY_LIST_B64", "").strip()
        if encoded:
            content = base64.b64decode(encoded, validate=True).decode("utf-8")
        else:
            with open(path, "r", encoding="utf-8") as proxy_file:
                content = proxy_file.read()
    except (OSError, ValueError, UnicodeError):
        return []
    entries = []
    for raw_line in content.splitlines():
        parsed = _parse_proxy_source_line(raw_line)
        if parsed is not None:
            entries.append(parsed)
    return entries


def _normalize_proxy_dashboard_entries(entries):
    if not isinstance(entries, list):
        return None
    loaded = []
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        parsed = _parse_proxy_source_line(
            f"{entry.get('host', '')}:{entry.get('port', '')}:"
            f"{entry.get('username', '')}:{entry.get('password', '')}"
        )
        if parsed is None:
            return None
        proxy_id = str(entry.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", proxy_id):
            proxy_id = secrets.token_urlsafe(12)
        parsed["id"] = proxy_id
        parsed["enabled"] = bool(entry.get("enabled", True))
        loaded.append(parsed)
    return loaded


def _load_proxy_dashboard_state():
    try:
        with open(PROXY_DASHBOARD_FILE, "r", encoding="utf-8") as state_file:
            payload = json.load(state_file)
        entries = payload.get("proxies") if isinstance(payload, dict) else None
        return _normalize_proxy_dashboard_entries(entries)
    except (OSError, ValueError, UnicodeError, TypeError):
        return None


def _proxy_payload_version(payload):
    version = payload.get("version") if isinstance(payload, dict) else None
    return version if isinstance(version, int) and not isinstance(version, bool) and version >= 1 else 1


_PROXY_ADMIN_ENTRIES = (
    _load_proxy_admin_entries(PROXY_LIST_PATH)
    if _FIRESTORE_PROXY_DOCUMENT is not None and os.environ.get("TIKWAHM_PROXY_LIST_B64", "").strip()
    else _load_proxy_dashboard_state()
)
if _PROXY_ADMIN_ENTRIES is None:
    _PROXY_ADMIN_ENTRIES = _load_proxy_admin_entries(PROXY_LIST_PATH)
_PROXY_POOL = [dict(proxy) for proxy in _PROXY_ADMIN_ENTRIES if proxy.get("enabled", True)]
_ACTIVE_PROXY_INDEX = 0
_PROXY_LOCK = asyncio.Lock()


async def _save_proxy_admin_entries(entries):
    """Persist credentials remotely when configured, then refresh the live pool."""
    global _PROXY_ADMIN_ENTRIES, _PROXY_POOL, _ACTIVE_PROXY_INDEX, _FIRESTORE_PROXY_VERSION
    payload = {"version": _FIRESTORE_PROXY_VERSION or 1, "saved_at": int(time.time()), "proxies": entries}
    if _FIRESTORE_PROXY_DOCUMENT is not None:
        expected_version = _FIRESTORE_PROXY_VERSION
        next_payload = {**payload, "version": expected_version + 1}
        serialized_size = len(json.dumps(next_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if serialized_size > 900_000:
            raise HTTPException(status_code=413, detail="قائمة البروكسيات أكبر من الحد الآمن لوثيقة Firestore.")
        def commit_if_current():
            transaction = _FIRESTORE_CLIENT.transaction()

            @firestore.transactional
            def update(tx):
                snapshots = list(tx.get_all([_FIRESTORE_PROXY_DOCUMENT]))
                snapshot = snapshots[0] if snapshots else None
                current_version = _proxy_payload_version(snapshot.to_dict() if snapshot.exists else None)
                if not snapshot.exists or current_version != expected_version:
                    raise RuntimeError("proxy_dashboard_conflict")
                tx.set(_FIRESTORE_PROXY_DOCUMENT, next_payload)
                return current_version + 1

            return update(transaction)

        try:
            _FIRESTORE_PROXY_VERSION = await asyncio.to_thread(commit_if_current)
        except RuntimeError as exc:
            if str(exc) == "proxy_dashboard_conflict":
                # Reject the stale write, but first replace the in-process pool
                # with the winning remote version so subsequent reads are fresh.
                latest_snapshot = await asyncio.to_thread(_FIRESTORE_PROXY_DOCUMENT.get)
                if not latest_snapshot.exists:
                    raise HTTPException(status_code=503, detail="تعذر استعادة إعدادات البروكسيات بعد التعارض.") from None
                latest_payload = latest_snapshot.to_dict() or {}
                latest_entries = _normalize_proxy_dashboard_entries(latest_payload.get("proxies"))
                if latest_entries is None:
                    raise HTTPException(status_code=503, detail="وثيقة البروكسيات البعيدة غير صالحة بعد التعارض.") from None
                async with _PROXY_LOCK:
                    _PROXY_ADMIN_ENTRIES = [dict(proxy) for proxy in latest_entries]
                    _PROXY_POOL = [dict(proxy) for proxy in latest_entries if proxy.get("enabled", True)]
                    _ACTIVE_PROXY_INDEX = 0
                    _FIRESTORE_PROXY_VERSION = _proxy_payload_version(latest_payload)
                raise HTTPException(status_code=409, detail="تعارض: تغيّر إعداد البروكسيات في نسخة أخرى. أعد تحميل الصفحة قبل الحفظ.") from None
            raise
    else:
        parent = os.path.dirname(PROXY_DASHBOARD_FILE) or "."
        os.makedirs(parent, exist_ok=True)
        temp_path = f"{PROXY_DASHBOARD_FILE}.{secrets.token_hex(8)}.tmp"
        try:
            descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
                json.dump(payload, state_file, ensure_ascii=False, separators=(",", ":"))
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temp_path, PROXY_DASHBOARD_FILE)
            os.chmod(PROXY_DASHBOARD_FILE, 0o600)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    source_lines = []
    for proxy in entries:
        line = f"{proxy['host']}:{proxy['port']}:{proxy['username']}:{proxy['password']}"
        source_lines.append(line if proxy.get("enabled", True) else f"# disabled: {line}")
    encoded = base64.b64encode(("\n".join(source_lines) + ("\n" if source_lines else "")).encode("utf-8")).decode("ascii")
    async with _PROXY_LOCK:
        _PROXY_ADMIN_ENTRIES = [dict(proxy) for proxy in entries]
        _PROXY_POOL = [dict(proxy) for proxy in entries if proxy.get("enabled", True)]
        _ACTIVE_PROXY_INDEX = 0
        os.environ["TIKWAHM_PROXY_LIST_B64"] = encoded


async def _initialize_proxy_storage():
    """Load Firestore state or migrate the existing file/environment list once."""
    global _PROXY_ADMIN_ENTRIES, _PROXY_POOL, _ACTIVE_PROXY_INDEX, _FIRESTORE_PROXY_VERSION
    if _FIRESTORE_PROXY_DOCUMENT is None:
        return

    snapshot = await asyncio.to_thread(_FIRESTORE_PROXY_DOCUMENT.get)
    if snapshot.exists:
        payload = snapshot.to_dict() or {}
        entries = _normalize_proxy_dashboard_entries(payload.get("proxies"))
        if entries is None:
            raise RuntimeError("وثيقة البروكسيات في Firestore غير صالحة؛ أوقف التشغيل لتجنب فقد البيانات.")
        remote_version = _proxy_payload_version(payload)
    else:
        entries = _PROXY_ADMIN_ENTRIES
        payload = {"version": 1, "saved_at": int(time.time()), "proxies": entries}
        serialized_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if serialized_size > 900_000:
            raise RuntimeError("قائمة البروكسيات أكبر من الحد الآمن لوثيقة Firestore.")
        try:
            await asyncio.to_thread(_FIRESTORE_PROXY_DOCUMENT.create, payload)
        except AlreadyExists:
            snapshot = await asyncio.to_thread(_FIRESTORE_PROXY_DOCUMENT.get)
            if not snapshot.exists:
                raise RuntimeError("تعذر قراءة وثيقة البروكسيات بعد تعارض الترحيل.")
            payload = snapshot.to_dict() or {}
            entries = _normalize_proxy_dashboard_entries(payload.get("proxies"))
            if entries is None:
                raise RuntimeError("وثيقة البروكسيات في Firestore غير صالحة؛ أوقف التشغيل لتجنب فقد البيانات.")
            remote_version = _proxy_payload_version(payload)
        else:
            remote_version = 1

    async with _PROXY_LOCK:
        _PROXY_ADMIN_ENTRIES = [dict(proxy) for proxy in entries]
        _PROXY_POOL = [dict(proxy) for proxy in entries if proxy.get("enabled", True)]
        _ACTIVE_PROXY_INDEX = 0
        _FIRESTORE_PROXY_VERSION = remote_version


async def _test_proxy_connection(proxy):
    def check():
        proxy_url = (
            f"http://{quote(proxy['username'], safe='')}:{quote(proxy['password'], safe='')}"
            f"@{proxy['host']}:{proxy['port']}"
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
        request = urllib.request.Request(
            "https://api.ipify.org?format=json",
            headers={"User-Agent": "TikWahm-Proxy-Check/1.0"},
        )
        try:
            with opener.open(request, timeout=12) as response:
                data = json.loads(response.read(512).decode("utf-8", "replace"))
            ip = str(data.get("ip", ""))
            return {"ok": bool(ip), "ip": ip[:80]}
        except Exception as exc:
            return {"ok": False, "reason": type(exc).__name__}

    return await asyncio.to_thread(check)


app = FastAPI(title="TikWahm Test Bot", lifespan=lifespan)


from proxy_admin import build_password_hash, build_proxy_admin_router
_ADMIN_PASSWORD = os.environ.pop("TIKWAHM_PROXY_DASHBOARD_PASSWORD", "")
_PROXY_ADMIN_PASSWORD_HASH = build_password_hash(_ADMIN_PASSWORD, BOT_TOKEN)
del _ADMIN_PASSWORD
app.include_router(build_proxy_admin_router(
    get_entries=lambda: [dict(item) for item in _PROXY_ADMIN_ENTRIES],
    save_entries=_save_proxy_admin_entries,
    get_session_secret=lambda: WEBHOOK_SECRET or WEBHOOK_SECRET_SEED or BOT_TOKEN,
    test_proxy=_test_proxy_connection,
    password_hash=_PROXY_ADMIN_PASSWORD_HASH,
    password_pepper=lambda: BOT_TOKEN,
    storage_backend=lambda: "firestore" if _FIRESTORE_PROXY_DOCUMENT is not None else "file",
))

@app.middleware("http")
async def proxy_admin_security_headers(request: Request, call_next):
    if request.url.path.startswith("/proxy-admin"):
        forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        if (forwarded or request.url.scheme) != "https":
            return PlainTextResponse("HTTPS is required for the proxy dashboard.", status_code=400)
    response = await call_next(request)
    if request.url.path.startswith("/proxy-admin"):
        for key, value in {"Cache-Control":"no-store, max-age=0", "Pragma":"no-cache", "X-Content-Type-Options":"nosniff", "X-Frame-Options":"DENY", "Referrer-Policy":"no-referrer", "Strict-Transport-Security":"max-age=31536000"}.items():
            response.headers[key] = value
    return response


@app.get("/", response_class=HTMLResponse)
async def home_page():
    return """<!doctype html>
<html lang="ar" dir="rtl">
  <head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>TikWahm</title>
    <style>body{font:16px system-ui;background:#101827;color:#e7edf7;display:grid;place-items:center;min-height:90vh;margin:0}main{max-width:36rem;padding:2rem;border:1px solid #34415a;border-radius:1rem;background:#182338}h1{margin-top:0;color:#60d6c4}p{line-height:1.8;color:#c3cede}code{color:#9cb6ff}</style>
  </head>
  <body><main><h1>خدمة TikWahm تعمل</h1>
    <p>هذه صفحة حالة الخدمة. يستقبل البوت تحديثات تيليجرام عبر webhook.</p>
    <p>فحص الصحة: <code>/health</code></p>
  </main></body>
</html>"""


@app.get("/health")
async def health_check():
    return {"ok": True, "service": "tikwahm", "mode": "webhook"}


def _claim_webhook_update(update_id):
    if update_id is None:
        return True
    if update_id in _SEEN_WEBHOOK_UPDATE_IDS:
        return False
    _SEEN_WEBHOOK_UPDATE_IDS[update_id] = time.monotonic()
    while len(_SEEN_WEBHOOK_UPDATE_IDS) > _WEBHOOK_UPDATE_ID_CACHE_LIMIT:
        _SEEN_WEBHOOK_UPDATE_IDS.popitem(last=False)
    return True


async def _process_webhook_update(update):
    try:
        await bot.process_new_updates([update])
    except Exception as exc:
        print(f"Telegram update processing failed ({type(exc).__name__}).", flush=True)


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    supplied_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not WEBHOOK_SECRET or not hmac.compare_digest(supplied_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        update = types.Update.de_json(json.dumps(await request.json()))
        update_id = getattr(update, "update_id", None)
    except Exception as exc:
        print(f"Telegram update parsing failed ({type(exc).__name__}).", flush=True)
        raise HTTPException(status_code=400, detail="Invalid update") from exc
    if not _claim_webhook_update(update_id):
        return {"ok": True}
    background_tasks.add_task(_process_webhook_update, update)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        log_level="info",
    )
