"""
Flibusta Telegram Bot - Search and download books from Flibusta library.

Features:
- Search by book title
- Search by author name
- Pagination (when results > 5)
- Dual source support (OPDS + HTTP fallback)
- Download books in various formats
"""

import asyncio
import logging
import io
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

from config import BOT_TOKEN, ITEMS_PER_PAGE
from flibusta_client import FlibustaClient, Book, Author

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
(
    MAIN_MENU,
    AWAITING_BOOK_QUERY,
    AWAITING_AUTHOR_QUERY,
    SHOWING_BOOK_RESULTS,
    SHOWING_AUTHOR_RESULTS,
    SHOWING_AUTHOR_BOOKS,
    SHOWING_BOOK_DETAILS,
) = range(7)

# Initialize client
flibusta = FlibustaClient()


def escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    if not text:
        return ""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text


def create_main_menu() -> InlineKeyboardMarkup:
    """Create the main menu keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Поиск по названию книги", callback_data="menu:search_book")],
        [InlineKeyboardButton("👤 Поиск по автору", callback_data="menu:search_author")],
    ])


def create_book_list_keyboard(
    books: list[Book], 
    page: int, 
    total_items: int,
    callback_prefix: str = "book"
) -> InlineKeyboardMarkup:
    """
    Create keyboard with book list and pagination.
    Shows ITEMS_PER_PAGE books per page with pagination if needed.
    """
    keyboard = []
    
    # Calculate pagination
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, len(books))
    page_books = books[start_idx:end_idx]
    
    # Add book buttons
    for book in page_books:
        # Format: "📖 Title (Author)"
        title = book.title[:35] + "..." if len(book.title) > 35 else book.title
        author_name = book.authors[0].name if book.authors else ""
        if author_name:
            author_name = author_name[:20] + "..." if len(author_name) > 20 else author_name
            label = f"📖 {title}"
        else:
            label = f"📖 {title}"
        
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"{callback_prefix}:{book.id}")
        ])
    
    # Pagination row (only if more than ITEMS_PER_PAGE items)
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    
    if total_pages > 1:
        nav_buttons = []
        
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton("◀️ Назад", callback_data=f"page:{callback_prefix}:{page - 1}")
            )
        
        # Page indicator
        nav_buttons.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton("Вперёд ▶️", callback_data=f"page:{callback_prefix}:{page + 1}")
            )
        
        keyboard.append(nav_buttons)
    
    # Back to main menu
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back:main")])
    
    return InlineKeyboardMarkup(keyboard)


def create_author_list_keyboard(
    authors: list[Author], 
    page: int,
    total_items: int
) -> InlineKeyboardMarkup:
    """
    Create keyboard with author list and pagination.
    Shows ITEMS_PER_PAGE authors per page with pagination if needed.
    """
    keyboard = []
    
    # Calculate pagination
    start_idx = page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, len(authors))
    page_authors = authors[start_idx:end_idx]
    
    # Add author buttons
    for author in page_authors:
        name = author.name[:45] + "..." if len(author.name) > 45 else author.name
        keyboard.append([
            InlineKeyboardButton(f"👤 {name}", callback_data=f"author:{author.id}")
        ])
    
    # Pagination row
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    
    if total_pages > 1:
        nav_buttons = []
        
        if page > 0:
            nav_buttons.append(
                InlineKeyboardButton("◀️ Назад", callback_data=f"page:author:{page - 1}")
            )
        
        nav_buttons.append(
            InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
        )
        
        if page < total_pages - 1:
            nav_buttons.append(
                InlineKeyboardButton("Вперёд ▶️", callback_data=f"page:author:{page + 1}")
            )
        
        keyboard.append(nav_buttons)
    
    # Back to main menu
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back:main")])
    
    return InlineKeyboardMarkup(keyboard)


def create_book_details_keyboard(book: Book, back_state: str = "results") -> InlineKeyboardMarkup:
    """Create keyboard for book details with download buttons."""
    keyboard = []
    
    # Download format buttons (max 3 per row)
    format_row = []
    for link in book.download_links[:6]:  # Limit to 6 formats
        format_label = link.format.upper()
        format_row.append(
            InlineKeyboardButton(f"📥 {format_label}", callback_data=f"dl:{book.id}:{link.format}")
        )
        
        if len(format_row) >= 3:
            keyboard.append(format_row)
            format_row = []
    
    if format_row:
        keyboard.append(format_row)
    
    # Navigation buttons
    keyboard.append([InlineKeyboardButton("🔙 К результатам", callback_data=f"back:{back_state}")])
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="back:main")])
    
    return InlineKeyboardMarkup(keyboard)


def format_book_info(book: Book) -> str:
    """Format book information for display."""
    authors_str = ", ".join(a.name for a in book.authors if a.name) or "Неизвестен"
    
    text = f"📖 *{escape_md(book.title)}*\n\n"
    text += f"✍️ *Автор:* {escape_md(authors_str)}\n"
    
    if book.year:
        text += f"📅 *Год:* {escape_md(book.year)}\n"
    if book.language:
        text += f"🌐 *Язык:* {escape_md(book.language)}\n"
    
    formats = [link.format.upper() for link in book.download_links]
    if formats:
        text += f"📄 *Форматы:* {escape_md(', '.join(formats))}\n"
    
    if book.description:
        # Clean and truncate description
        import re
        desc = re.sub(r'<[^>]+>', '', book.description)
        desc = desc.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        if len(desc) > 400:
            desc = desc[:400] + "..."
        text += f"\n📝 {escape_md(desc)}"
    
    return text


# ============== HANDLERS ==============

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /start command."""
    user = update.effective_user
    
    # Clear any previous data
    context.user_data.clear()
    
    text = (
        f"👋 Привет, *{escape_md(user.first_name)}*\\!\n\n"
        "Я бот для поиска и скачивания книг с Флибусты\\.\n\n"
        "Выберите способ поиска:"
    )
    
    await update.message.reply_text(
        text,
        reply_markup=create_main_menu(),
        parse_mode='MarkdownV2'
    )
    
    return MAIN_MENU


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main menu selections."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "menu:search_book":
        await query.edit_message_text(
            "📚 *Поиск книги*\n\nВведите название книги для поиска:",
            parse_mode='MarkdownV2',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Отмена", callback_data="back:main")]
            ])
        )
        return AWAITING_BOOK_QUERY
    
    elif data == "menu:search_author":
        await query.edit_message_text(
            "👤 *Поиск автора*\n\nВведите имя автора для поиска:",
            parse_mode='MarkdownV2',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Отмена", callback_data="back:main")]
            ])
        )
        return AWAITING_AUTHOR_QUERY
    
    elif data == "back:main":
        context.user_data.clear()
        await query.edit_message_text(
            "Выберите способ поиска:",
            reply_markup=create_main_menu()
        )
        return MAIN_MENU
    
    elif data == "noop":
        # Do nothing (pagination indicator button)
        return query.message.text  # Keep current state
    
    return MAIN_MENU


async def book_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle book search query input."""
    query_text = update.message.text.strip()
    
    if not query_text:
        await update.message.reply_text(
            "❌ Пожалуйста, введите название книги\\.",
            parse_mode='MarkdownV2'
        )
        return AWAITING_BOOK_QUERY
    
    # Store query and show loading message
    context.user_data['book_query'] = query_text
    context.user_data['book_page'] = 0
    
    msg = await update.message.reply_text("🔍 Ищу книги...")
    
    # Define status callback helper
    loop = asyncio.get_running_loop()
    
    def status_callback(text):
        async def _update():
            try:
                await msg.edit_text(text, parse_mode='MarkdownV2')
            except Exception as e:
                logger.error(f"Failed to update status: {e}")
        
        asyncio.run_coroutine_threadsafe(_update(), loop)

    # Search for books
    result = await asyncio.get_event_loop().run_in_executor(
        None, 
        lambda: flibusta.search_books(query_text, 0, status_callback=status_callback)
    )
    
    if not result or not result.items:
        await msg.edit_text(
            f"😕 По запросу *\"{escape_md(query_text)}\"* ничего не найдено\\.\n\n"
            "Попробуйте другой запрос\\.",
            parse_mode='MarkdownV2',
            reply_markup=create_main_menu()
        )
        return MAIN_MENU
    
    # Store results
    context.user_data['book_results'] = result.items
    
    total = len(result.items)
    source = f"({'OPDS' if result.source == 'opds' else 'HTTP'})"
    
    text = f"📚 Найдено *{total}* книг по запросу *\"{escape_md(query_text)}\"* {escape_md(source)}:\n\nВыберите книгу:"
    
    await msg.edit_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=create_book_list_keyboard(result.items, 0, total, "book")
    )
    
    return SHOWING_BOOK_RESULTS


async def author_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle author search query input."""
    query_text = update.message.text.strip()
    
    if not query_text:
        await update.message.reply_text(
            "❌ Пожалуйста, введите имя автора\\.",
            parse_mode='MarkdownV2'
        )
        return AWAITING_AUTHOR_QUERY
    
    context.user_data['author_query'] = query_text
    context.user_data['author_page'] = 0
    
    msg = await update.message.reply_text("🔍 Ищу авторов...")
    
    # Define status callback helper
    loop = asyncio.get_running_loop()
    
    def status_callback(text):
        async def _update():
            try:
                await msg.edit_text(text, parse_mode='MarkdownV2')
            except Exception as e:
                logger.error(f"Failed to update status: {e}")
        
        asyncio.run_coroutine_threadsafe(_update(), loop)
    
    # Search for authors
    result = await asyncio.get_event_loop().run_in_executor(
        None, 
        lambda: flibusta.search_authors(query_text, 0, status_callback=status_callback)
    )
    
    if not result or not result.items:
        await msg.edit_text(
            f"😕 По запросу *\"{escape_md(query_text)}\"* авторы не найдены\\.\n\n"
            "Попробуйте другой запрос\\.",
            parse_mode='MarkdownV2',
            reply_markup=create_main_menu()
        )
        return MAIN_MENU
    
    # Store results
    context.user_data['author_results'] = result.items
    
    total = len(result.items)
    source = f"({'OPDS' if result.source == 'opds' else 'HTTP'})"
    
    text = f"👤 Найдено *{total}* авторов по запросу *\"{escape_md(query_text)}\"* {escape_md(source)}:\n\nВыберите автора:"
    
    await msg.edit_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=create_author_list_keyboard(result.items, 0, total)
    )
    
    return SHOWING_AUTHOR_RESULTS


async def book_results_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle book results list interactions (selection, pagination, back)."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Handle pagination
    if data.startswith("page:book:"):
        page = int(data.split(":")[-1])
        context.user_data['book_page'] = page
        
        books = context.user_data.get('book_results', [])
        query_text = context.user_data.get('book_query', '')
        
        text = f"📚 Результаты поиска *\"{escape_md(query_text)}\"*:"
        
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=create_book_list_keyboard(books, page, len(books), "book")
        )
        return SHOWING_BOOK_RESULTS
    
    # Handle book selection
    if data.startswith("book:"):
        book_id = data.split(":")[1]
        books = context.user_data.get('book_results', [])
        
        book = next((b for b in books if b.id == book_id), None)
        if not book:
            await query.edit_message_text(
                "❌ Книга не найдена\\.",
                parse_mode='MarkdownV2',
                reply_markup=create_main_menu()
            )
            return MAIN_MENU
        
        context.user_data['selected_book'] = book
        context.user_data['back_to'] = 'book_results'
        
        await query.edit_message_text(
            format_book_info(book),
            parse_mode='MarkdownV2',
            reply_markup=create_book_details_keyboard(book, "book_results")
        )
        return SHOWING_BOOK_DETAILS
    
    # Handle download
    if data.startswith("dl:"):
        return await handle_download(update, context)
    
    # Handle back navigation
    if data == "back:main":
        context.user_data.clear()
        await query.edit_message_text(
            "Выберите способ поиска:",
            reply_markup=create_main_menu()
        )
        return MAIN_MENU
    
    if data == "back:book_results":
        books = context.user_data.get('book_results', [])
        page = context.user_data.get('book_page', 0)
        query_text = context.user_data.get('book_query', '')
        
        text = f"📚 Результаты поиска *\"{escape_md(query_text)}\"*:"
        
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=create_book_list_keyboard(books, page, len(books), "book")
        )
        return SHOWING_BOOK_RESULTS
    
    if data == "noop":
        return SHOWING_BOOK_RESULTS
    
    return SHOWING_BOOK_RESULTS


async def author_results_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle author results list interactions."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Handle pagination
    if data.startswith("page:author:"):
        page = int(data.split(":")[-1])
        context.user_data['author_page'] = page
        
        authors = context.user_data.get('author_results', [])
        query_text = context.user_data.get('author_query', '')
        
        text = f"👤 Результаты поиска *\"{escape_md(query_text)}\"*:"
        
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=create_author_list_keyboard(authors, page, len(authors))
        )
        return SHOWING_AUTHOR_RESULTS
    
    # Handle author selection
    if data.startswith("author:"):
        author_id = data.split(":")[1]
        authors = context.user_data.get('author_results', [])
        
        author = next((a for a in authors if a.id == author_id), None)
        author_name = author.name if author else "Автор"
        
        context.user_data['selected_author_id'] = author_id
        context.user_data['selected_author_name'] = author_name
        context.user_data['author_books_page'] = 0
        
        # msg = await query.edit_message_text("🔍 Загружаю книги автора...") NO - query obj doesn't return msg
        # We need to edit and get message object? query.edit_message_text returns the message or bool
        # Actually it returns Message or True.
        
        # Proper way to get msg for updates:
        # We'll use query.message directly but edit_message_text is needed to change content.
        # Let's send a new "Loading" state or edit current.
        
        msg = await query.edit_message_text("🔍 Загружаю книги автора...")
        # Note: if msg is bool (True), we can't use it directly. 
        # But in v20+, edit_message_text usually returns Message if types match.
        # Let's assume it returns Message object or we can use query.message (but that's the OLD message).
        # We need the live message object ID to edit it again.
        # Actually, query.edit_message_text edits query.message.
        # So we can just direct edit query.message or use edit_message_text again.
        
        # However, to use status_callback which updates the same message, we need a target.
        # status_callback above uses `await msg.edit_text`
        
        # Let's define callback to use query.edit_message_text
        loop = asyncio.get_running_loop()
        
        def status_callback(text):
            async def _update():
                try:
                    await query.edit_message_text(text, parse_mode='MarkdownV2')
                except Exception as e:
                    logger.error(f"Failed to update status: {e}")
            
            asyncio.run_coroutine_threadsafe(_update(), loop)
        
        # Get author's books
        result = await asyncio.get_event_loop().run_in_executor(
            None, 
            lambda: flibusta.get_author_books(author_id, 0, status_callback=status_callback)
        )
        
        if not result or not result.items:
            await query.edit_message_text(
                f"😕 У автора *{escape_md(author_name)}* нет доступных книг\\.",
                parse_mode='MarkdownV2',
                reply_markup=create_main_menu()
            )
            return MAIN_MENU
        
        context.user_data['author_books'] = result.items
        
        total = len(result.items)
        text = f"📚 Книги автора *{escape_md(author_name)}* \\({total}\\):\n\nВыберите книгу:"
        
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=create_book_list_keyboard(result.items, 0, total, "abook")
        )
        return SHOWING_AUTHOR_BOOKS
    
    # Handle back
    if data == "back:main":
        context.user_data.clear()
        await query.edit_message_text(
            "Выберите способ поиска:",
            reply_markup=create_main_menu()
        )
        return MAIN_MENU
    
    if data == "noop":
        return SHOWING_AUTHOR_RESULTS
    
    return SHOWING_AUTHOR_RESULTS


async def author_books_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle author's books list interactions."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Handle pagination
    if data.startswith("page:abook:"):
        page = int(data.split(":")[-1])
        context.user_data['author_books_page'] = page
        
        books = context.user_data.get('author_books', [])
        author_name = context.user_data.get('selected_author_name', 'Автор')
        
        text = f"📚 Книги автора *{escape_md(author_name)}*:"
        
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=create_book_list_keyboard(books, page, len(books), "abook")
        )
        return SHOWING_AUTHOR_BOOKS
    
    # Handle book selection from author's list
    if data.startswith("abook:"):
        book_id = data.split(":")[1]
        books = context.user_data.get('author_books', [])
        
        book = next((b for b in books if b.id == book_id), None)
        if not book:
            await query.edit_message_text(
                "❌ Книга не найдена\\.",
                parse_mode='MarkdownV2',
                reply_markup=create_main_menu()
            )
            return MAIN_MENU
        
        context.user_data['selected_book'] = book
        context.user_data['back_to'] = 'author_books'
        
        await query.edit_message_text(
            format_book_info(book),
            parse_mode='MarkdownV2',
            reply_markup=create_book_details_keyboard(book, "author_books")
        )
        return SHOWING_BOOK_DETAILS
    
    # Handle download
    if data.startswith("dl:"):
        return await handle_download(update, context)
    
    # Handle back navigation
    if data == "back:main":
        context.user_data.clear()
        await query.edit_message_text(
            "Выберите способ поиска:",
            reply_markup=create_main_menu()
        )
        return MAIN_MENU
    
    if data == "back:author_books":
        books = context.user_data.get('author_books', [])
        page = context.user_data.get('author_books_page', 0)
        author_name = context.user_data.get('selected_author_name', 'Автор')
        
        text = f"📚 Книги автора *{escape_md(author_name)}*:"
        
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=create_book_list_keyboard(books, page, len(books), "abook")
        )
        return SHOWING_AUTHOR_BOOKS
    
    if data == "noop":
        return SHOWING_AUTHOR_BOOKS
    
    return SHOWING_AUTHOR_BOOKS


async def book_details_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle book details view interactions."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Handle download
    if data.startswith("dl:"):
        return await handle_download(update, context)
    
    # Handle back navigation
    if data == "back:main":
        context.user_data.clear()
        await query.edit_message_text(
            "Выберите способ поиска:",
            reply_markup=create_main_menu()
        )
        return MAIN_MENU
    
    back_to = context.user_data.get('back_to', 'main')
    
    if data == "back:book_results" or (data.startswith("back:") and back_to == 'book_results'):
        books = context.user_data.get('book_results', [])
        page = context.user_data.get('book_page', 0)
        query_text = context.user_data.get('book_query', '')
        
        text = f"📚 Результаты поиска *\"{escape_md(query_text)}\"*:"
        
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=create_book_list_keyboard(books, page, len(books), "book")
        )
        return SHOWING_BOOK_RESULTS
    
    if data == "back:author_books" or (data.startswith("back:") and back_to == 'author_books'):
        books = context.user_data.get('author_books', [])
        page = context.user_data.get('author_books_page', 0)
        author_name = context.user_data.get('selected_author_name', 'Автор')
        
        text = f"📚 Книги автора *{escape_md(author_name)}*:"
        
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=create_book_list_keyboard(books, page, len(books), "abook")
        )
        return SHOWING_AUTHOR_BOOKS
    
    if data == "noop":
        return SHOWING_BOOK_DETAILS
    
    return SHOWING_BOOK_DETAILS


async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle book download request."""
    query = update.callback_query
    data = query.data
    
    # Parse: dl:book_id:format
    parts = data.split(":")
    if len(parts) < 3:
        await query.answer("❌ Неверный формат запроса", show_alert=True)
        return SHOWING_BOOK_DETAILS
    
    book_id = parts[1]
    fmt = parts[2]
    
    book = context.user_data.get('selected_book')
    if not book:
        await query.answer("❌ Книга не найдена", show_alert=True)
        return SHOWING_BOOK_DETAILS
    
    # Find download link
    download_link = next((l for l in book.download_links if l.format == fmt), None)
    if not download_link:
        await query.answer("❌ Ссылка не найдена", show_alert=True)
        return SHOWING_BOOK_DETAILS
    
    await query.answer("📥 Начинаю скачивание...")
    
    await query.edit_message_text(
        f"📥 Скачиваю *{escape_md(book.title)}* в формате {escape_md(fmt.upper())}\\.\\.\\.",
        parse_mode='MarkdownV2'
    )
    
    # Download the book
    result = await asyncio.get_event_loop().run_in_executor(
        None, flibusta.download_book, download_link.url
    )
    
    back_to = context.user_data.get('back_to', 'main')
    
    if not result:
        await query.edit_message_text(
            "❌ Не удалось скачать книгу\\. Попробуйте позже\\.",
            parse_mode='MarkdownV2',
            reply_markup=create_main_menu()
        )
        return MAIN_MENU
    
    content, filename = result
    
    # Ensure proper filename
    if not filename.endswith(f'.{fmt}'):
        safe_title = "".join(c for c in book.title if c.isalnum() or c in (' ', '-', '_')).strip()[:50]
        filename = f"{safe_title}.{fmt}"
    
    # Send file
    try:
        authors_str = ", ".join(a.name for a in book.authors if a.name) or "Неизвестен"
        
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=io.BytesIO(content),
            filename=filename,
            caption=f"📖 {book.title}\n✍️ {authors_str}"
        )
        
        await query.edit_message_text(
            f"✅ Книга *{escape_md(book.title)}* успешно отправлена\\!",
            parse_mode='MarkdownV2',
            reply_markup=create_main_menu()
        )
    except Exception as e:
        logger.error(f"Failed to send document: {e}")
        await query.edit_message_text(
            "❌ Ошибка при отправке файла\\. Файл может быть слишком большим\\.",
            parse_mode='MarkdownV2',
            reply_markup=create_main_menu()
        )
    
    return MAIN_MENU


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle /cancel command."""
    context.user_data.clear()
    await update.message.reply_text(
        "Выберите способ поиска:",
        reply_markup=create_main_menu()
    )
    return MAIN_MENU


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    text = (
        "📚 *Бот для поиска книг на Флибусте*\n\n"
        "*Команды:*\n"
        "/start \\- Начать работу\n"
        "/help \\- Показать помощь\n"
        "/cancel \\- Вернуться в главное меню\n\n"
        "*Как пользоваться:*\n"
        "1\\. Выберите тип поиска \\(по книге или автору\\)\n"
        "2\\. Введите название книги или имя автора\n"
        "3\\. Выберите нужный результат\n"
        "4\\. Скачайте книгу в нужном формате\n\n"
        "*Примечание:*\n"
        "Бот использует OPDS и HTTP для получения данных\\. "
        "Если один источник недоступен, автоматически используется другой\\."
    )
    await update.message.reply_text(text, parse_mode='MarkdownV2')


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors."""
    logger.error(f"Exception while handling update: {context.error}")
    
    # Try to inform user about the error
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="😕 Произошла ошибка. Пожалуйста, попробуйте снова или используйте /start",
                reply_markup=create_main_menu()
            )
        except Exception:
            pass


def main():
    """Start the bot."""
    if not BOT_TOKEN:
        print("❌ Error: BOT_TOKEN not set!")
        print("Please create a .env file with your bot token.")
        print("Example: BOT_TOKEN=your_telegram_bot_token_here")
        return
    
    print("=" * 50)
    print("🚀 Flibusta Telegram Bot")
    print("=" * 50)
    
    # Check connection
    success, message = flibusta.check_connection()
    print(message)
    
    if not success:
        print("\n⚠️ Warning: Cannot connect to Flibusta!")
        print("Possible solutions:")
        print("  1. Use a VPN to bypass regional blocks")
        print("  2. Check your internet connection")
        print("  3. Wait and try again later")
        print("-" * 50)
        
        try:
            response = input("Start anyway? (y/n): ").strip().lower()
            if response != 'y':
                print("Cancelled.")
                return
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return
    
    print("-" * 50)
    print(f"📊 Pagination: {ITEMS_PER_PAGE} items per page")
    print("-" * 50)
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Create conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(menu_handler)
            ],
            AWAITING_BOOK_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, book_search_handler),
                CallbackQueryHandler(menu_handler)
            ],
            AWAITING_AUTHOR_QUERY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, author_search_handler),
                CallbackQueryHandler(menu_handler)
            ],
            SHOWING_BOOK_RESULTS: [
                CallbackQueryHandler(book_results_handler)
            ],
            SHOWING_AUTHOR_RESULTS: [
                CallbackQueryHandler(author_results_handler)
            ],
            SHOWING_AUTHOR_BOOKS: [
                CallbackQueryHandler(author_books_handler)
            ],
            SHOWING_BOOK_DETAILS: [
                CallbackQueryHandler(book_details_handler)
            ],
        },
        fallbacks=[
            CommandHandler("start", start_command),
            CommandHandler("cancel", cancel_command),
        ],
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    application.add_error_handler(error_handler)
    
    # Start bot
    print("🤖 Bot is starting...")
    print("Press Ctrl+C to stop")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
