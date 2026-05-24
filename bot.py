"""
🇺🇦 Українсько-Російський Словник — Telegram бот
Переклад слів і фраз укр ↔ рос з наголосом (Wiktionary)
"""

import os
import re
import json
import logging
import asyncio
import time
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

from deep_translator import GoogleTranslator, MyMemoryTranslator
from gtts import gTTS

from data import DICTIONARY
from big_dictionary import BIG_DICT
from wiktionary import lookup_wiktionary, format_wiktionary_entry

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


# =====================================================================
# СЛОВНИК — об'єднуємо всі джерела
# =====================================================================

def _convert_big(entry_tuple):
    return {"pos": entry_tuple[0], "translation": entry_tuple[1], "example": entry_tuple[2]}

ALL_WORDS = {}
for w, e in BIG_DICT.items():
    ALL_WORDS[w] = _convert_big(e)
for w, e in DICTIONARY.items():
    ALL_WORDS[w] = e  # DICTIONARY перекриває BIG_DICT

REVERSE_DICT = {}
for ukr_word, entry in ALL_WORDS.items():
    rus_translation = entry.get("translation", "")
    parts = re.split(r'[/,;]', rus_translation)
    for part in parts:
        rus_key = part.strip().lower()
        if rus_key:
            if rus_key not in REVERSE_DICT:
                REVERSE_DICT[rus_key] = []
            REVERSE_DICT[rus_key].append((ukr_word, entry))
            first_word = rus_key.split()[0] if ' ' in rus_key else None
            if first_word and len(first_word) >= 3:
                if first_word not in REVERSE_DICT:
                    REVERSE_DICT[first_word] = []
                REVERSE_DICT[first_word].append((ukr_word, entry))

logger.info(f"📚 Словник: {len(ALL_WORDS)} укр слів, {len(REVERSE_DICT)} рос ключів")


# =====================================================================
# ВИЗНАЧЕННЯ МОВИ
# =====================================================================

UKR_ONLY = set("іїєґ")
RUS_ONLY = set("ыэёъ")


def detect_language(text: str) -> str:
    t = text.lower()
    has_ukr = any(ch in UKR_ONLY for ch in t)
    has_rus = any(ch in RUS_ONLY for ch in t)

    if has_ukr and not has_rus:
        return "uk"
    if has_rus and not has_ukr:
        return "ru"

    words = t.split()
    if len(words) == 1:
        w = words[0]
        if w in ALL_WORDS:
            return "uk"
        if w in REVERSE_DICT:
            return "ru"

    rus_patterns = ["ого", "ему", "ться", "ешь", "ёт", "щ", "ый", "ий"]
    ukr_patterns = ["ться", "ють", "ємо", "ішь", "ього"]
    rus_score = sum(1 for p in rus_patterns if p in t)
    ukr_score = sum(1 for p in ukr_patterns if p in t)

    if rus_score > ukr_score:
        return "ru"
    if ukr_score > rus_score:
        return "uk"
    return "unknown"


# =====================================================================
# НЕЧІТКИЙ ПОШУК У СЛОВНИКУ
# =====================================================================

def fuzzy_lookup_ukr(word: str) -> tuple:
    if word in ALL_WORDS:
        return word, ALL_WORDS[word]

    suffixes = [
        "ів", "ям", "ях", "ами", "ями", "ою", "ею", "єю",
        "ові", "еві", "єві", "ом", "ем", "єм", "їв", "ей",
        "ці", "зі", "сі",
        "у", "ю", "і", "ї", "и", "е", "є", "о", "а", "я",
        "ку", "ця", "ся",
        "ти", "ть",
        "ую", "юю", "єш", "еш", "ає", "є", "емо", "ємо", "ете", "єте", "ють", "ять",
        "ла", "ло", "ли", "в",
    ]

    for suffix in sorted(suffixes, key=len, reverse=True):
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            stem = word[:-len(suffix)]
            for ending in ["", "а", "я", "о", "е", "і", "и", "ь", "й", "ти", "ка", "ок"]:
                candidate = stem + ending
                if candidate in ALL_WORDS:
                    return candidate, ALL_WORDS[candidate]

    if len(word) >= 5:
        best_match = None
        best_score = 0
        for dict_word, entry in ALL_WORDS.items():
            if len(dict_word) >= 5 and abs(len(word) - len(dict_word)) <= 2:
                prefix_len = 0
                for a, b in zip(word, dict_word):
                    if a == b:
                        prefix_len += 1
                    else:
                        break
                if prefix_len >= 4 and prefix_len >= len(word) * 0.7:
                    if prefix_len > best_score:
                        best_score = prefix_len
                        best_match = (dict_word, entry)
        if best_match:
            return best_match

    return None, None


def fuzzy_lookup_rus(word: str) -> list:
    if word in REVERSE_DICT:
        return REVERSE_DICT[word]

    rus_suffixes = [
        "ами", "ями", "ов", "ев", "ей",
        "ом", "ем", "ой",
        "ам", "ям", "ах", "ях",
        "у", "ю", "а", "я", "е", "и", "ы", "о", "ть", "ся",
        "ую", "ешь", "ет", "ем", "ете", "ют", "ят",
        "ал", "ала", "ало", "али", "ил", "ила", "ило", "или",
    ]

    for suffix in sorted(rus_suffixes, key=len, reverse=True):
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            stem = word[:-len(suffix)]
            for ending in ["", "а", "я", "о", "е", "и", "ь", "й", "ть", "ок", "ка", "ый", "ий", "ой"]:
                candidate = stem + ending
                if candidate in REVERSE_DICT:
                    return REVERSE_DICT[candidate]

    if len(word) >= 5:
        best_match = None
        best_score = 0
        for rus_key, entries in REVERSE_DICT.items():
            if len(rus_key) >= 5 and abs(len(word) - len(rus_key)) <= 2:
                prefix_len = 0
                for a, b in zip(word, rus_key):
                    if a == b:
                        prefix_len += 1
                    else:
                        break
                if prefix_len >= 4 and prefix_len >= len(word) * 0.7:
                    if prefix_len > best_score:
                        best_score = prefix_len
                        best_match = entries
        if best_match:
            return best_match

    return []


# =====================================================================
# ПЕРЕКЛАД — з повторними спробами
# =====================================================================

def _do_translate(text: str, source: str, target: str, retries: int = 3) -> str:
    """Синхронний переклад з повторами. Викликається через run_in_executor."""
    for attempt in range(retries):
        try:
            result = GoogleTranslator(source=source, target=target).translate(text)
            if result and result.strip():
                return result.strip()
        except Exception as e:
            logger.warning(f"Google Translate ({source}→{target}) спроба {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))  # 1.5s, 3s між спробами

    # Fallback: MyMemory
    try:
        src_code = source if source != 'auto' else 'uk'
        result = MyMemoryTranslator(source=src_code, target=target).translate(text)
        if result and result.strip():
            return result.strip()
    except Exception as e:
        logger.warning(f"MyMemory ({source}→{target}) помилка: {e}")

    return ""


def _is_different(text: str, result: str) -> bool:
    if not result:
        return False
    return result.lower().strip() != text.lower().strip()


async def smart_translate(text: str, lang: str) -> tuple:
    """Розумний переклад. Повертає (переклад, tts_текст)."""
    loop = asyncio.get_event_loop()

    if lang == "ru":
        # Рос → Укр
        result = await loop.run_in_executor(None, _do_translate, text, 'ru', 'uk', 3)
        if not result:
            result = await loop.run_in_executor(None, _do_translate, text, 'auto', 'uk', 2)
        if result and _is_different(text, result):
            return result, result
        return result or "", result or ""
    else:
        # Укр → Рос (і для 'unknown')
        result = await loop.run_in_executor(None, _do_translate, text, 'uk', 'ru', 3)
        if not result:
            result = await loop.run_in_executor(None, _do_translate, text, 'auto', 'ru', 2)

        if result and _is_different(text, result):
            return result, text  # TTS = оригінальний укр текст

        # Google повернув те саме — можливо слово насправді рос
        if not _is_different(text, result):
            result2 = await loop.run_in_executor(None, _do_translate, text, 'auto', 'uk', 2)
            if result2 and _is_different(text, result2):
                return result2, result2

        return result or "", text


# =====================================================================
# TTS КЕSH
# =====================================================================

TTS_CACHE = {}
_tts_counter = 0


def tts_store(text: str) -> str:
    global _tts_counter
    _tts_counter += 1
    key = f"t{_tts_counter}"
    TTS_CACHE[key] = text
    if len(TTS_CACHE) > 500:
        oldest = list(TTS_CACHE.keys())[:250]
        for k in oldest:
            del TTS_CACHE[k]
    return key


# =====================================================================
# /start
# =====================================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🇺🇦 <b>Українсько-Російський Словник</b>\n\n"
        "Надішліть <b>слово або фразу</b> — переведу!\n\n"
        "Визначаю мову автоматично (укр ↔ рос).\n"
        "Для кожного слова показую <b>наголос</b> 🔉 та тлумачення.\n\n"
        f"📚 {len(ALL_WORDS):,} слів у базі + Google Translate + Wiktionary"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# =====================================================================
# /help
# =====================================================================

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 <b>Як користуватися</b>\n\n"
        "Просто надішліть <b>слово або фразу</b> — укр або рос.\n"
        "Бот визначить мову і перекладе.\n\n"
        "Для кожного слова:\n"
        "• переклад\n"
        "• наголос і вимова 🔉\n"
        "• тлумачення і приклади\n"
        "• кнопка «Озвучити» 🔊\n\n"
        f"📚 {len(ALL_WORDS):,} слів у базі + Google Translate + Wiktionary (53 000+ слів)"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# =====================================================================
# ОСНОВНИЙ ОБРОБНИК СЛІВ / ФРАЗ
# =====================================================================

async def handle_word(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    word = raw_text.lower()

    if not word or len(word) > 4000:
        return

    is_single_word = ' ' not in word
    lang = detect_language(raw_text)

    # ── ОДНЕ СЛОВО ──────────────────────────────────────────────────
    if is_single_word and len(word) >= 2:
        translation, tts_text = await smart_translate(raw_text, lang)
        gt_different = _is_different(word, translation)

        # Wiktionary — наголос + тлумачення (пробуємо завжди)
        wikt_data = None
        wikt_text = ""
        ukr_word = word
        try:
            # Спробувати саме слово
            wikt_data = await asyncio.get_event_loop().run_in_executor(
                None, lookup_wiktionary, word
            )
            # Якщо переклад різний — спробувати перекладену форму
            if not wikt_data and gt_different and translation:
                trans_word = translation.lower().strip().split()[0]
                if trans_word:
                    wikt_data = await asyncio.get_event_loop().run_in_executor(
                        None, lookup_wiktionary, trans_word
                    )
                    if wikt_data:
                        ukr_word = trans_word
            if wikt_data:
                wikt_text = format_wiktionary_entry(wikt_data)
        except Exception as e:
            logger.warning(f"Wiktionary помилка: {e}")

        # Локальний словник укр→рос
        found_word, entry = fuzzy_lookup_ukr(word)
        if not entry and gt_different and translation:
            found_word, entry = fuzzy_lookup_ukr(translation.lower().strip())

        # Якщо слово не знайшли як українське — шукаємо як російське
        rus_entries = []
        if not entry and not gt_different:
            rus_entries = fuzzy_lookup_rus(word)

        # Будуємо відповідь
        text = f"<b>{raw_text}</b>\n\n"
        has_content = False

        # 1) Переклад від Google (якщо відрізняється від оригіналу)
        if gt_different and translation:
            text += f"{translation}\n"
            has_content = True

        # 2) Якщо Google повернув те саме — слово однакове або він не спрацював
        #    Показуємо переклад з локального словника
        if not gt_different:
            if entry:
                text += f"{entry['translation']}\n"
                has_content = True
            elif rus_entries:
                # Слово є в рос→укр словнику — воно російське
                ukr_words = ", ".join(dict.fromkeys(e[0] for e in rus_entries[:4]))
                text += f"{ukr_words}\n"
                ukr_word = rus_entries[0][0]
                has_content = True
                # Wiktionary для знайденого укр слова
                if not wikt_text:
                    try:
                        wd2 = await asyncio.get_event_loop().run_in_executor(
                            None, lookup_wiktionary, ukr_word
                        )
                        if wd2:
                            wikt_text = format_wiktionary_entry(wd2)
                    except Exception:
                        pass

        # 3) Wiktionary (наголос + тлумачення)
        if wikt_text:
            sep = "\n📖 <b>Словник:</b>\n" if has_content else "📖 "
            text += f"{sep}{wikt_text}\n"
            has_content = True

        # 4) Приклад з локального словника
        if entry and entry.get("example"):
            text += f"<i>{entry['example']}</i>\n"
            has_content = True

        # 5) Якщо слово однакове в обох мовах (напр. "драма", "кролик")
        if not has_content and translation and not gt_different:
            text += f"(слово однакове в укр. та рос.)\n"
            has_content = True

        if has_content:
            tts_key = tts_store(tts_text or ukr_word or word)
            keyboard = [[InlineKeyboardButton("🔊 Озвучити", callback_data=f"tts_{tts_key}")]]
            await update.message.reply_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Остання спроба — примусовий переклад через auto
            loop = asyncio.get_event_loop()
            forced = await loop.run_in_executor(None, _do_translate, raw_text, 'auto', 'ru', 2)
            if not forced:
                forced = await loop.run_in_executor(None, _do_translate, raw_text, 'auto', 'uk', 2)
            if forced and _is_different(word, forced):
                tts_key = tts_store(forced)
                keyboard = [[InlineKeyboardButton("🔊 Озвучити", callback_data=f"tts_{tts_key}")]]
                await update.message.reply_text(
                    f"<b>{raw_text}</b>\n\n{forced}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(
                    f"❓ Не вдалося перекласти <b>{raw_text}</b>.\n"
                    "Спробуйте інше написання.",
                    parse_mode=ParseMode.HTML
                )

    # ── ФРАЗА / РЕЧЕННЯ ─────────────────────────────────────────────
    else:
        translation, tts_text = await smart_translate(raw_text, lang)

        if translation and translation.strip():
            text = f"<b>{raw_text}</b>\n\n{translation}"
            tts_key = tts_store(tts_text or translation)
            keyboard = [[InlineKeyboardButton("🔊 Озвучити", callback_data=f"tts_{tts_key}")]]
            await update.message.reply_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Примусовий переклад через auto
            loop = asyncio.get_event_loop()
            forced = await loop.run_in_executor(None, _do_translate, raw_text, 'auto', 'ru', 2)
            if forced and _is_different(raw_text.lower(), forced):
                tts_key = tts_store(forced)
                keyboard = [[InlineKeyboardButton("🔊 Озвучити", callback_data=f"tts_{tts_key}")]]
                await update.message.reply_text(
                    f"<b>{raw_text}</b>\n\n{forced}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(
                    f"❓ Не вдалося перекласти <b>{raw_text}</b>.\n"
                    "Спробуйте інше написання.",
                    parse_mode=ParseMode.HTML
                )


# =====================================================================
# CALLBACK HANDLER — тільки TTS
# =====================================================================

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("tts_"):
        tts_key = data[4:]
        tts_text = TTS_CACHE.get(tts_key, tts_key)
        try:
            tts_obj = gTTS(text=tts_text, lang='uk', slow=False)
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                tts_obj.save(f.name)
                with open(f.name, 'rb') as audio:
                    await query.message.reply_voice(
                        voice=audio,
                        caption=f"🔊 {tts_text[:100]}"
                    )
            os.unlink(f.name)
        except Exception as e:
            logger.error(f"TTS помилка: {e}")
            await query.message.reply_text(f"⚠️ Помилка озвучення: {str(e)[:200]}")


# =====================================================================
# MAIN
# =====================================================================

def main():
    if not BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не встановлено!")
        print("Створіть .env файл з TELEGRAM_BOT_TOKEN=ваш_токен")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_word))

    print("🇺🇦 Бот запущено!")
    print(f"📚 Словник: {len(ALL_WORDS):,} слів")
    print("📖 Wiktionary: наголос + тлумачення")
    print("🔄 Google Translate з повторними спробами")
    app.run_polling()


if __name__ == "__main__":
    main()
