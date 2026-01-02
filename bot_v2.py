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

# Настройка Google Sheets
def setup_google_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open("Pythonbot").sheet1
    return sheet

# Состояния для conversation handler
TITLE, AUTHOR, REMOVE = range(3)

# Глобальные переменные для пагинации
current_page = 0
books_cache = []

# Обновление кэша книг
async def update_books_cache():
    global books_cache
    try:
        sheet = setup_google_sheets()
        books_cache = sorted(sheet.get_all_records(), key=lambda x: x["Автор"].lower())
    except Exception as e:
        print(f"Ошибка при обновлении кэша: {e}")

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для каталогизации книг.\n"
        "Используй команды:\n"
        "/addbook - добавить книгу\n"
        "/listbooks - показать список книг\n"
        "/removebook - удалить книгу"
    )

# Начало добавления книги
async def add_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скажите название книги, которую хотите добавить.")
    return TITLE

# Получение названия книги
async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text
    await update.message.reply_text("Понял. А какой автор?")
    return AUTHOR

# Получение автора и сохранение книги
async def get_author(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["author"] = update.message.text
    title = context.user_data["title"]
    author = context.user_data["author"]

    sheet = setup_google_sheets()
    sheet.append_row([author, title])
    await update_books_cache()  # Обновляем кэш после добавления

    await update.message.reply_text(f"Понял, записал: {author} - {title}")
    return ConversationHandler.END

# Отмена диалога
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

# Команда /listbooks с пагинацией
async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global current_page

    if not books_cache:
        await update_books_cache()

    await show_books_page(update, context, page=0)

# Показать страницу с книгами
async def show_books_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    global current_page
    current_page = page
    items_per_page = 50
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page

    # Формируем список книг для текущей страницы
    book_list = "\n".join(
        f"{i+1}. {book['Автор']} – {book['Название']}"
        for i, book in enumerate(books_cache[start_idx:end_idx], start=start_idx)
    )

    # Создаем клавиатуру с пагинацией
    keyboard = []
    total_pages = (len(books_cache) + items_per_page - 1) // items_per_page

    # Кнопки навигации
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"prev_{page-1}"))

    nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="page_info"))

    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"next_{page+1}"))

    keyboard.append(nav_buttons)

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Отправляем или редактируем сообщение
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

# Обработчик кнопок пагинации
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

# Начало удаления книги
async def remove_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите номер книги, которую хотите удалить.")
    return REMOVE

# Получение номера книги для удаления
async def get_book_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        book_number = int(update.message.text) - 1

        sheet = setup_google_sheets()
        books = sheet.get_all_records()

        if book_number < 0 or book_number >= len(books_cache):
            await update.message.reply_text("Неверный номер книги.")
            return ConversationHandler.END

        # Получаем книгу из кэша
        book_to_remove = books_cache[book_number]

        # Находим индекс книги в Google Sheets
        for i, book in enumerate(books):
            if book["Название"] == book_to_remove["Название"] and book["Автор"] == book_to_remove["Автор"]:
                sheet.delete_rows(i + 2)  # +2, так как первая строка — заголовки
                await update_books_cache()  # Обновляем кэш
                await update.message.reply_text(f"Книга удалена: {book_to_remove['Автор']} - {book_to_remove['Название']}")
                return ConversationHandler.END

        await update.message.reply_text("Книга не найдена.")
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите число.")
        return REMOVE
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return ConversationHandler.END

# Основная функция
def main():
    TOKEN = '7718799655:AAGhWbGw9-zc4er5nrA36nFGsnebflJ0YMI'
    application = Application.builder().token(TOKEN).build()

    # Conversation handler для добавления книги
    add_book_handler = ConversationHandler(
        entry_points=[CommandHandler("addbook", add_book)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_title)],
            AUTHOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_author)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Conversation handler для удаления книги
    remove_book_handler = ConversationHandler(
        entry_points=[CommandHandler("removebook", remove_book)],
        states={
            REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_book_number)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(add_book_handler)
    application.add_handler(remove_book_handler)
    application.add_handler(CommandHandler("listbooks", list_books))
    application.add_handler(CallbackQueryHandler(handle_pagination, pattern="^(prev|next)_"))

    # Инициализация кэша при старте
    application.run_polling()

if __name__ == '__main__':
    main()