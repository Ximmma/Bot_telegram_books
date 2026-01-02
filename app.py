import os
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler
)
from flask import Flask, request

# --- Настройка Google Sheets ---
def setup_google_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise ValueError("Не задана переменная окружения GOOGLE_CREDENTIALS")
    creds_dict = json.loads(creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open("Pythonbot").sheet1
    return sheet

# --- Состояния ConversationHandler ---
TITLE, AUTHOR, REMOVE = range(3)
current_page = 0
books_cache = []

async def update_books_cache():
    global books_cache
    try:
        sheet = setup_google_sheets()
        books_cache = sorted(sheet.get_all_records(), key=lambda x: x["Автор"].lower())
    except Exception as e:
        print(f"Ошибка при обновлении кэша: {e}")

# --- Обработчики команд ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для каталогизации книг.\n"
        "Используй команды:\n"
        "/addbook - добавить книгу\n"
        "/listbooks - показать список книг\n"
        "/removebook - удалить книгу"
    )

async def add_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скажите название книги, которую хотите добавить.")
    return TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text
    await update.message.reply_text("Понял. А какой автор?")
    return AUTHOR

async def get_author(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["author"] = update.message.text
    title = context.user_data["title"]
    author = context.user_data["author"]

    sheet = setup_google_sheets()
    sheet.append_row([author, title])
    await update_books_cache()
    await update.message.reply_text(f"Понял, записал: {author} - {title}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_page
    if not books_cache:
        await update_books_cache()
    await show_books_page(update, context, page=0)

async def show_books_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    global current_page
    current_page = page
    items_per_page = 50
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page

    book_list = "\n".join(
        f"{i+1}. {book['Автор']} – {book['Название']}"
        for i, book in enumerate(books_cache[start_idx:end_idx], start=start_idx)
    )

    keyboard = []
    total_pages = (len(books_cache) + items_per_page - 1) // items_per_page

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"prev_{page-1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="page_info"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"next_{page+1}"))

    keyboard.append(nav_buttons)
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=f"Список книг (страница {page+1}):\n{book_list}",
            reply_markup=reply_markup
        )
        await update.callback_query.answer()
    else:
        await update.message.reply_text(
            f"Список книг (страница {page+1}):\n{book_list}",
            reply_markup=reply_markup
        )

async def handle_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data.startswith("prev_"):
        page = int(data.split("_")[1])
        await show_books_page(update, context, page)
    elif data.startswith("next_"):
        page = int(data.split("_")[1])
        await show_books_page(update, context, page)
    else:
        await query.answer()

async def remove_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите номер книги, которую хотите удалить.")
    return REMOVE

async def get_book_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        book_number = int(update.message.text) - 1
        sheet = setup_google_sheets()
        books = sheet.get_all_records()
        if book_number < 0 or book_number >= len(books_cache):
            await update.message.reply_text("Неверный номер книги.")
            return ConversationHandler.END
        book_to_remove = books_cache[book_number]
        for i, book in enumerate(books):
            if book["Название"] == book_to_remove["Название"] and book["Автор"] == book_to_remove["Автор"]:
                sheet.delete_rows(i + 2)
                await update_books_cache()
                await update.message.reply_text(f"Книга удалена: {book_to_remove['Автор']} - {book_to_remove['Название']}")
                return ConversationHandler.END
        await update.message.reply_text("Книга не найдена.")
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите число.")
        return REMOVE
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return ConversationHandler.END

# --- Flask Web Service ---
flask_app = Flask(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

if not TOKEN or not WEBHOOK_URL:
    raise ValueError("Не заданы переменные окружения TELEGRAM_TOKEN или WEBHOOK_URL")

# --- Создание Application ---
application = Application.builder().token(TOKEN).build()

# Добавляем обработчики
application.add_handler(CommandHandler("start", start))
add_book_handler = ConversationHandler(
    entry_points=[CommandHandler("addbook", add_book)],
    states={
        TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
        AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_author)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)
remove_book_handler = ConversationHandler(
    entry_points=[CommandHandler("removebook", remove_book)],
    states={REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_book_number)]},
    fallbacks=[CommandHandler("cancel", cancel)],
)
application.add_handler(add_book_handler)
application.add_handler(remove_book_handler)
application.add_handler(CommandHandler("listbooks", list_books))
application.add_handler(CallbackQueryHandler(handle_pagination, pattern="^(prev|next)_"))

# --- Установка webhook сразу ---
application.bot.delete_webhook()
application.bot.set_webhook(url=WEBHOOK_URL)
print(f"Webhook установлен: {WEBHOOK_URL}")

# --- Endpoint для Telegram ---
@flask_app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put(update)
    return "OK"

# --- Запуск Flask ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
