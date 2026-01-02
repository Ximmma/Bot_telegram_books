import os
import json
import logging
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

# --- Логирование (Важно для отладки на Render) ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Переменные окружения ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
# На Render WEBHOOK_URL должен быть без / в конце, например: https://my-bot.onrender.com
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") 
PORT = int(os.environ.get("PORT", "8443")) # Render автоматически дает порт
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")

if not TOKEN or not WEBHOOK_URL or not GOOGLE_CREDENTIALS:
    raise ValueError("Не заданы переменные окружения!")

# --- Google Sheets ---
def setup_google_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(GOOGLE_CREDENTIALS)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open("Pythonbot").sheet1
    return sheet

# --- Conversation states ---
TITLE, AUTHOR, REMOVE = range(3)
# Кэш книг лучше хранить в context.bot_data, но для простоты оставим глобально, 
# однако помните, что на Render при перезапуске (deploy) он сбросится.
books_cache = []

async def update_books_cache():
    global books_cache
    try:
        # gspread - синхронная библиотека. В идеале ее нужно запускать в отдельном потоке,
        # чтобы не блокировать бота, но для начала оставим так.
        sheet = setup_google_sheets()
        records = sheet.get_all_records()
        # Сортировка безопасна, даже если ключа нет (добавим проверку)
        books_cache = sorted(records, key=lambda x: x.get("Автор", "").lower())
    except Exception as e:
        logger.error(f"Ошибка при обновлении кэша: {e}")

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для каталогизации книг.\n"
        "Команды:\n/addbook\n/listbooks\n/removebook"
    )

async def add_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скажите название книги.")
    return TITLE

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text
    await update.message.reply_text("Понял. А какой автор?")
    return AUTHOR

async def get_author(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["author"] = update.message.text
    title = context.user_data["title"]
    author = context.user_data["author"]

    try:
        sheet = setup_google_sheets()
        sheet.append_row([author, title])
        await update_books_cache()
        await update.message.reply_text(f"Записал: {author} - {title}")
    except Exception as e:
        logger.error(f"Ошибка записи: {e}")
        await update.message.reply_text("Ошибка при записи в таблицу.")
        
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END

async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not books_cache:
        await update.message.reply_text("Загружаю список книг...")
        await update_books_cache()
    
    if not books_cache:
         await update.message.reply_text("Список пуст или ошибка доступа к таблице.")
         return

    await show_books_page(update, context, page=0)

async def show_books_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    items_per_page = 30 # 50 может быть слишком длинным сообщением для Telegram
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page

    # Проверка на пустой кэш
    if not books_cache:
        await update_books_cache()

    subset = books_cache[start_idx:end_idx]
    if not subset:
         text = "Книг больше нет."
    else:
        text = "\n".join(
            f"{i+1}. {book.get('Автор', 'Неизв')} – {book.get('Название', 'Без назв')}"
            for i, book in enumerate(subset, start=start_idx)
        )

    keyboard = []
    total_pages = (len(books_cache) + items_per_page - 1) // items_per_page

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"prev_{page-1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"next_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg_text = f"Список книг (стр. {page+1}):\n\n{text}"

    if update.callback_query:
        # Чтобы не было ошибки "Message is not modified"
        try:
            await update.callback_query.edit_message_text(text=msg_text, reply_markup=reply_markup)
        except Exception:
            pass 
        await update.callback_query.answer()
    else:
        await update.message.reply_text(msg_text, reply_markup=reply_markup)

async def handle_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "noop":
        await query.answer()
        return

    if data.startswith("prev_"):
        page = int(data.split("_")[1])
        await show_books_page(update, context, page)
    elif data.startswith("next_"):
        page = int(data.split("_")[1])
        await show_books_page(update, context, page)

async def remove_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите номер книги (из списка), которую удалить.")
    return REMOVE

async def get_book_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        book_number = int(update.message.text) - 1
        
        # Обновим кэш перед удалением, чтобы индексы были актуальны
        if not books_cache:
            await update_books_cache()

        if book_number < 0 or book_number >= len(books_cache):
            await update.message.reply_text("Неверный номер.")
            return ConversationHandler.END
            
        book_to_remove = books_cache[book_number]
        
        # Удаление из Google Sheets
        sheet = setup_google_sheets()
        all_vals = sheet.get_all_records()
        
        # Ищем строку для удаления. Это не самый надежный метод (лучше ID), но для начала пойдет
        row_to_delete = -1
        for i, row in enumerate(all_vals):
            if (row.get("Название") == book_to_remove.get("Название") and 
                row.get("Автор") == book_to_remove.get("Автор")):
                row_to_delete = i + 2 # +2 т.к. в gspread нумерация с 1 и есть заголовок
                break
        
        if row_to_delete != -1:
            sheet.delete_rows(row_to_delete)
            await update.message.reply_text(f"Удалено: {book_to_remove.get('Название')}")
            await update_books_cache()
        else:
            await update.message.reply_text("Не удалось найти книгу в таблице (возможно она уже удалена).")

    except ValueError:
        await update.message.reply_text("Нужно ввести число.")
        return REMOVE
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        await update.message.reply_text("Ошибка при удалении.")
    
    return ConversationHandler.END

# --- ЗАПУСК ---
if __name__ == "__main__":
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()

    # Добавляем хендлеры
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("addbook", add_book)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
            AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_author)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    remove_handler = ConversationHandler(
        entry_points=[CommandHandler("removebook", remove_book)],
        states={REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_book_number)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("listbooks", list_books))
    application.add_handler(conv_handler)
    application.add_handler(remove_handler)
    application.add_handler(CallbackQueryHandler(handle_pagination, pattern="^(prev|next|noop)"))

    # Запускаем через встроенный Webhook
    # Render передает PORT и ждет, что мы будем слушать 0.0.0.0
    print(f"Запускаю вебхук на порту {PORT} с URL {WEBHOOK_URL}")
    
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )
