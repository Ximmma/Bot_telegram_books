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
    CallbackQueryHandler,
)

# =======================
# НАСТРОЙКИ И СЕКРЕТЫ
# =======================

BOT_TOKEN = os.environ["BOT_TOKEN"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Pythonbot")

# =======================
# GOOGLE SHEETS
# =======================

def setup_google_sheets():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)

    client = gspread.authorize(creds)
    return client.open(SPREADSHEET_NAME).sheet1

# =======================
# CONVERSATION STATES
# =======================

TITLE, AUTHOR, REMOVE = range(3)

current_page = 0
books_cache = []

# =======================
# CACHE
# =======================

async def update_books_cache():
    global books_cache
    try:
        sheet = setup_google_sheets()
        books_cache = sorted(
            sheet.get_all_records(),
            key=lambda x: x["Автор"].lower(),
        )
    except Exception as e:
        print(f"Ошибка обновления кэша: {e}")

# =======================
# COMMANDS
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для каталогизации книг.\n\n"
        "/addbook — добавить книгу\n"
        "/listbooks — список книг\n"
        "/removebook — удалить книгу"
    )

async def add_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите название книги.")
    return TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text
    await update.message.reply_text("Теперь укажите автора.")
    return AUTHOR

async def get_author(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = context.user_data["title"]
    author = update.message.text

    sheet = setup_google_sheets()
    sheet.append_row([author, title])

    await update_books_cache()
    await update.message.reply_text(f"Добавлено: {author} — {title}")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

# =======================
# LIST & PAGINATION
# =======================

async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not books_cache:
        await update_books_cache()
    await show_books_page(update, context, 0)

async def show_books_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    items_per_page = 50
    start = page * items_per_page
    end = start + items_per_page

    page_books = books_cache[start:end]

    if not page_books:
        await update.message.reply_text("Книг пока нет.")
        return

    text = "\n".join(
        f"{i+1}. {b['Автор']} — {b['Название']}"
        for i, b in enumerate(page_books, start=start)
    )

    total_pages = (len(books_cache) + items_per_page - 1) // items_per_page

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️", callback_data=f"prev_{page-1}"))
    buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("➡️", callback_data=f"next_{page+1}"))

    markup = InlineKeyboardMarkup([buttons])

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
        await update.callback_query.answer()
    else:
        await update.message.reply_text(text, reply_markup=markup)

async def handle_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith(("prev_", "next_")):
        page = int(data.split("_")[1])
        await show_books_page(update, context, page)

# =======================
# REMOVE BOOK
# =======================

async def remove_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите номер книги для удаления.")
    return REMOVE

async def get_book_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        idx = int(update.message.text) - 1
        sheet = setup_google_sheets()
        books = sheet.get_all_records()

        book = books_cache[idx]
        for i, b in enumerate(books):
            if b == book:
                sheet.delete_rows(i + 2)
                await update_books_cache()
                await update.message.reply_text("Книга удалена.")
                return ConversationHandler.END

        await update.message.reply_text("Книга не найдена.")
    except Exception:
        await update.message.reply_text("Ошибка. Введите корректный номер.")
        return REMOVE

# =======================
# APP
# =======================

def build_app():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("listbooks", list_books))
    app.add_handler(CallbackQueryHandler(handle_pagination, pattern="^(prev|next)_"))

    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("addbook", add_book)],
            states={
                TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
                AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_author)],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
        )
    )

    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("removebook", remove_book)],
            states={REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_book_number)]},
            fallbacks=[CommandHandler("cancel", cancel)],
        )
    )

    return app
