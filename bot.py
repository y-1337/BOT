import logging
import sqlite3
import datetime
import asyncio
from datetime import date, timedelta
from contextlib import contextmanager
from typing import Dict, List
from enum import Enum

from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler,
    MessageHandler, 
    filters, 
    ContextTypes,
    ConversationHandler
)

# =================== НАСТРОЙКИ ===================
TOKEN = "8581658074:AAFdjd_B4UMIwRa2wiDzHjxxew8nO-zt_NY"  # Замените на свой токен
DB_NAME = "habits.db"

# Состояния для ConversationHandler
class States(Enum):
    MAIN_MENU = 0
    ADD_HABIT = 1
    ADD_CUSTOM_HABIT = 2
    DELETE_HABIT = 3
    TRACK_HABIT = 4
    ADD_NOTE = 5

# =================== НАСТРОЙКА ЛОГИРОВАНИЯ ===================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =================== БАЗА ДАННЫХ ===================
class Database:
    """Класс для работы с базой данных SQLite"""
    
    def __init__(self, db_name: str):
        self.db_name = db_name
        self.init_db()
    
    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для соединения с БД"""
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()
    
    def init_db(self):
        """Инициализация таблиц базы данных"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS habits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    habit_name TEXT,
                    habit_emoji TEXT,
                    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN DEFAULT 1
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS completed_habits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    habit_id INTEGER,
                    completion_date DATE,
                    completion_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notes TEXT,
                    UNIQUE(user_id, habit_id, completion_date)
                )
            ''')
    
    def add_user(self, user_id: int, username: str, first_name: str, last_name: str):
        """Добавление нового пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO users 
                (user_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name))
    
    def add_habit(self, user_id: int, habit_name: str, habit_emoji: str) -> int:
        """Добавление новой привычки"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO habits (user_id, habit_name, habit_emoji)
                VALUES (?, ?, ?)
            ''', (user_id, habit_name, habit_emoji))
            return cursor.lastrowid
    
    def get_user_habits(self, user_id: int) -> List[Dict]:
        """Получение привычек пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, habit_name, habit_emoji, created_date
                FROM habits 
                WHERE user_id = ? AND is_active = 1
                ORDER BY created_date
            ''', (user_id,))
            
            habits = []
            for row in cursor.fetchall():
                habits.append(dict(row))
            return habits
    
    def delete_habit(self, habit_id: int, user_id: int):
        """Удаление привычки"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE habits 
                SET is_active = 0 
                WHERE id = ? AND user_id = ?
            ''', (habit_id, user_id))
    
    def mark_habit_done(self, user_id: int, habit_id: int, notes: str = "") -> bool:
        """Отметка привычки как выполненной на сегодня"""
        today = date.today()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT id FROM completed_habits 
                WHERE user_id = ? AND habit_id = ? AND completion_date = ?
            ''', (user_id, habit_id, today))
            
            if cursor.fetchone():
                return False
            
            cursor.execute('''
                INSERT INTO completed_habits 
                (user_id, habit_id, completion_date, notes)
                VALUES (?, ?, ?, ?)
            ''', (user_id, habit_id, today, notes))
            
            return True
    
    def get_today_stats(self, user_id: int) -> tuple:
        """Получение статистики за сегодня"""
        today = date.today()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT COUNT(DISTINCT ch.habit_id) as completed
                FROM completed_habits ch
                JOIN habits h ON ch.habit_id = h.id
                WHERE ch.user_id = ? AND ch.completion_date = ? AND h.is_active = 1
            ''', (user_id, today))
            completed = cursor.fetchone()['completed']
            
            cursor.execute('''
                SELECT COUNT(*) as total
                FROM habits 
                WHERE user_id = ? AND is_active = 1
            ''', (user_id,))
            total = cursor.fetchone()['total']
            
            return completed, total
    
    def get_today_completed_ids(self, user_id: int) -> List[int]:
        """Получение ID выполненных сегодня привычек"""
        today = date.today()
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT habit_id 
                FROM completed_habits 
                WHERE user_id = ? AND completion_date = ?
            ''', (user_id, today))
            
            return [row['habit_id'] for row in cursor.fetchall()]

# Инициализация базы данных
db = Database(DB_NAME)

# =================== КЛАВИАТУРЫ ===================
def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Основное меню"""
    keyboard = [
        [KeyboardButton("➕ Добавить привычку")],
        [KeyboardButton("📊 Статистика"), KeyboardButton("📋 Мои привычки")],
        [KeyboardButton("✅ Отметить выполнение")],
        [KeyboardButton("🗑️ Удалить привычку"), KeyboardButton("ℹ️ Помощь")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_habits_keyboard(habits: List[Dict], prefix: str = "habit_") -> InlineKeyboardMarkup:
    """Инлайн-клавиатура с привычками"""
    keyboard = []
    
    for habit in habits:
        button_text = f"{habit['habit_emoji']} {habit['habit_name']}"
        callback_data = f"{prefix}{habit['id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

def get_predefined_habits_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с предопределенными привычками"""
    habits_list = [
        ('💧', 'Пить воду'),
        ('🏃', 'Спорт'),
        ('📚', 'Чтение'),
        ('🧘', 'Медитация'),
        ('🛌', 'Ранний подъем'),
        ('✍️', 'Дневник'),
        ('🍎', 'Здоровое питание'),
        ('🚫', 'Отказ от вредного')
    ]
    
    keyboard = []
    # Группируем по 2 привычки в ряд
    for i in range(0, len(habits_list), 2):
        row = []
        for emoji, name in habits_list[i:i+2]:
            button_text = f"{emoji} {name}"
            callback_data = f"predef_{emoji}"
            row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
        keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton("✏️ Своя привычка", callback_data="custom_habit"),
        InlineKeyboardButton("❌ Отмена", callback_data="cancel")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_yes_no_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура Да/Нет"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да", callback_data="yes"), 
         InlineKeyboardButton("❌ Нет", callback_data="no")]
    ])

# =================== АНИМАЦИИ ===================
async def animate_button_press(query, emoji: str, habit_name: str):
    """Анимация нажатия кнопки"""
    try:
        # Первый этап: изменение эмодзи кнопки
        frames = ["⏳", "⌛", "✅"]
        for frame in frames:
            # Получаем текущую клавиатуру
            habits_list = [
                ('💧', 'Пить воду'),
                ('🏃', 'Спорт'),
                ('📚', 'Чтение'),
                ('🧘', 'Медитация'),
                ('🛌', 'Ранний подъем'),
                ('✍️', 'Дневник'),
                ('🍎', 'Здоровое питание'),
                ('🚫', 'Отказ от вредного')
            ]
            
            keyboard = []
            for i in range(0, len(habits_list), 2):
                row = []
                for habit_emoji, name in habits_list[i:i+2]:
                    if habit_emoji == emoji:
                        button_text = f"{frame} {name}"
                    else:
                        button_text = f"{habit_emoji} {name}"
                    row.append(InlineKeyboardButton(button_text, callback_data=f"predef_{habit_emoji}"))
                keyboard.append(row)
            
            keyboard.append([
                InlineKeyboardButton("✏️ Своя привычка", callback_data="custom_habit"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ])
            
            await query.edit_message_text(
                f"🎯 **Выберите привычку**\n\n"
                f"🔄 Обрабатываю: {habit_name}",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await asyncio.sleep(0.2)
        
        # Второй этап: индикатор загрузки
        loading_frames = ["⏳ Добавляю...", "⌛ Сохраняю...", "✅ Готово!"]
        for frame in loading_frames:
            await query.edit_message_text(
                f"**{frame}**\n\n"
                f"*{habit_name}*",
                parse_mode='Markdown'
            )
            await asyncio.sleep(0.2)
            
    except Exception as e:
        logger.error(f"Animation error: {e}")

async def animate_success(query, habit_emoji: str, habit_name: str):
    """Анимация успешного добавления"""
    try:
        success_frames = ["✨", "🌟", "🎉", "✅"]
        for emoji in success_frames:
            await query.edit_message_text(
                f"**{emoji} Привычка добавлена!**\n\n"
                f"{habit_emoji} **{habit_name}**",
                parse_mode='Markdown'
            )
            await asyncio.sleep(0.2)
            
    except Exception as e:
        logger.error(f"Success animation error: {e}")

async def animate_error(query, message: str):
    """Анимация ошибки"""
    try:
        error_frames = ["❌", "⚠️", "🚫"]
        for emoji in error_frames:
            await query.edit_message_text(
                f"**{emoji} {message}**",
                parse_mode='Markdown'
            )
            await asyncio.sleep(0.2)
            
    except Exception as e:
        logger.error(f"Error animation error: {e}")

# =================== ОБРАБОТЧИКИ ===================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    try:
        user = update.effective_user
        db.add_user(user.id, user.username or "", user.first_name or "", user.last_name or "")
        
        welcome_text = f"""👋 **Привет, {user.first_name}!**

Я бот для отслеживания привычек. Помогу тебе развивать полезные привычки!

📌 **Что я умею:**
➕ Добавлять привычки
✅ Отмечать выполнение
📊 Показывать статистику
🗑️ Удалять привычки

👇 **Используй кнопки меню:**"""
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
        return States.MAIN_MENU
        
    except Exception as e:
        logger.error(f"Error in start: {e}")
        await update.message.reply_text(
            "👋 Привет! Я бот для трекинга привычек.\n\nИспользуйте кнопки меню ниже:",
            reply_markup=get_main_menu_keyboard()
        )
        return States.MAIN_MENU

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда помощи"""
    help_text = """📚 **Помощь по боту**

🎯 **Как использовать:**
1. Нажмите ➕ **Добавить привычку**
2. Выберите из списка или создайте свою
3. Каждый день отмечайте выполнение
4. Следите за статистикой

✨ **Советы:**
• Начинайте с 2-3 простых привычек
• Отмечайте выполнение регулярно
• Не бойтесь удалять ненужные привычки

🔄 **Доступные команды:**
/start - Перезапустить бота
/help - Эта справка"""
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику"""
    try:
        user_id = update.effective_user.id
        completed, total = db.get_today_stats(user_id)
        
        stats_text = f"""📊 **Статистика за сегодня**

✅ Выполнено: **{completed}/{total}** привычек"""
        
        if total > 0:
            progress = int((completed/total*100))
            progress_bar = "█" * (progress // 10) + "░" * (10 - progress // 10)
            stats_text += f"\n📈 Прогресс: {progress}%\n{progress_bar}"
        
        if completed == total and total > 0:
            stats_text += "\n\n🎉 **Отличная работа! Все привычки выполнены!**"
        elif completed == 0 and total > 0:
            stats_text += "\n\n⏳ **Начните отмечать привычки сегодня!**"
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')
        return States.MAIN_MENU
        
    except Exception as e:
        logger.error(f"Error in show_stats: {e}")
        await update.message.reply_text("⚠️ Не удалось загрузить статистику")
        return States.MAIN_MENU

async def show_habits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать привычки"""
    try:
        user_id = update.effective_user.id
        habits = db.get_user_habits(user_id)
        
        if not habits:
            await update.message.reply_text(
                "📭 **У вас пока нет привычек**\n\n"
                "Нажмите ➕ **Добавить привычку**, чтобы создать первую!",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
            return States.MAIN_MENU
        
        habits_text = "📋 **Ваши привычки:**\n\n"
        
        for i, habit in enumerate(habits, 1):
            habits_text += f"{i}. {habit['habit_emoji']} **{habit['habit_name']}**\n"
        
        habits_text += f"\n✨ **Всего: {len(habits)} привычек**"
        
        await update.message.reply_text(habits_text, parse_mode='Markdown')
        return States.MAIN_MENU
        
    except Exception as e:
        logger.error(f"Error in show_habits: {e}")
        await update.message.reply_text("⚠️ Не удалось загрузить привычки")
        return States.MAIN_MENU

# =================== ДОБАВЛЕНИЕ ПРИВЫЧЕК С АНИМАЦИЕЙ ===================
async def add_habit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса добавления привычки"""
    try:
        await update.message.reply_text(
            "🎯 **Выберите привычку или создайте свою:**\n\n"
            "👇 Нажмите на кнопку ниже",
            parse_mode='Markdown',
            reply_markup=get_predefined_habits_keyboard()
        )
        return States.ADD_HABIT
        
    except Exception as e:
        logger.error(f"Error in add_habit_start: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка. Попробуйте еще раз.",
            reply_markup=get_main_menu_keyboard()
        )
        return States.MAIN_MENU

async def add_predefined_habit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление предопределенной привычки с анимацией"""
    try:
        query = update.callback_query
        await query.answer()
        
        # Получаем данные о выбранной привычке
        data = query.data.replace("predef_", "")
        habit_emoji = data
        habit_name = {
            '💧': 'Пить воду',
            '🏃': 'Спорт',
            '📚': 'Чтение',
            '🧘': 'Медитация',
            '🛌': 'Ранний подъем',
            '✍️': 'Ведение дневника',
            '🍎': 'Здоровое питание',
            '🚫': 'Отказ от вредного'
        }.get(habit_emoji, "Новая привычка")
        
        user_id = query.from_user.id
        
        # Запускаем анимацию нажатия кнопки
        await animate_button_press(query, habit_emoji, habit_name)
        
        # Проверяем, нет ли уже такой привычки
        habits = db.get_user_habits(user_id)
        for habit in habits:
            if habit['habit_name'] == habit_name:
                # Анимация ошибки
                await animate_error(query, "Привычка уже есть!")
                
                await query.edit_message_text(
                    f"❌ **Привычка уже есть!**\n\n"
                    f"'{habit_name}' уже есть в вашем списке.\n"
                    f"Выберите другую привычку.",
                    parse_mode='Markdown',
                    reply_markup=get_main_menu_keyboard()
                )
                return States.MAIN_MENU
        
        # Добавляем привычку в базу
        habit_id = db.add_habit(user_id, habit_name, habit_emoji)
        
        # Анимация успешного добавления
        await animate_success(query, habit_emoji, habit_name)
        
        # Финальное сообщение
        await query.edit_message_text(
            f"🎉 **Привычка добавлена!**\n\n"
            f"{habit_emoji} **{habit_name}**\n\n"
            f"✅ Теперь вы можете отмечать её выполнение каждый день.\n"
            f"📊 Следите за своим прогрессом в статистике!",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        
        return States.MAIN_MENU
        
    except Exception as e:
        logger.error(f"Error in add_predefined_habit: {e}")
        try:
            query = update.callback_query
            await query.edit_message_text(
                "⚠️ **Ошибка при добавлении привычки**\n\n"
                "Попробуйте еще раз или выберите другую привычку.",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
        except:
            pass
        return States.MAIN_MENU

async def add_custom_habit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления своей привычки"""
    try:
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "✏️ **Создайте свою привычку**\n\n"
            "Введите название привычки (2-30 символов):\n\n"
            "📝 **Примеры:**\n"
            "• Изучение английского\n"
            "• Прогулка на свежем воздухе\n"
            "• Планирование дня",
            parse_mode='Markdown'
        )
        
        return States.ADD_CUSTOM_HABIT
        
    except Exception as e:
        logger.error(f"Error in add_custom_habit_start: {e}")
        return States.MAIN_MENU

async def add_custom_habit_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение создания своей привычки"""
    try:
        habit_name = update.message.text.strip()
        
        if len(habit_name) < 2 or len(habit_name) > 30:
            await update.message.reply_text(
                "❌ **Некорректное название**\n\n"
                "Название привычки должно быть от 2 до 30 символов.\n"
                "Попробуйте еще раз:",
                parse_mode='Markdown'
            )
            return States.ADD_CUSTOM_HABIT
        
        user_id = update.effective_user.id
        
        # Проверяем, нет ли уже такой привычки
        habits = db.get_user_habits(user_id)
        for habit in habits:
            if habit['habit_name'].lower() == habit_name.lower():
                await update.message.reply_text(
                    f"❌ **Привычка уже существует!**\n\n"
                    f"'{habit_name}' уже есть в вашем списке.\n"
                    f"Придумайте другое название.",
                    parse_mode='Markdown',
                    reply_markup=get_main_menu_keyboard()
                )
                return States.MAIN_MENU
        
        # Анимация создания
        creation_frames = ["✨ Создаю привычку...", "🌟 Готово!"]
        message = await update.message.reply_text("✨ **Создаю привычку...**", parse_mode='Markdown')
        
        for frame in creation_frames:
            await message.edit_text(f"**{frame}**", parse_mode='Markdown')
            await asyncio.sleep(0.5)
        
        # Добавляем привычку
        habit_emoji = "✅"
        db.add_habit(user_id, habit_name, habit_emoji)
        
        await message.edit_text(
            f"🎊 **Привычка создана!**\n\n"
            f"{habit_emoji} **{habit_name}**\n\n"
            f"✨ Теперь вы можете отслеживать свой прогресс!\n"
            f"📈 Отмечайте выполнение каждый день!",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        
        return States.MAIN_MENU
        
    except Exception as e:
        logger.error(f"Error in add_custom_habit_finish: {e}")
        await update.message.reply_text(
            "⚠️ **Ошибка при создании привычки**\n\n"
            "Попробуйте еще раз или вернитесь в меню.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return States.MAIN_MENU

# =================== ОТСЛЕЖИВАНИЕ ПРИВЫЧЕК ===================
async def track_habit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало отслеживания"""
    try:
        user_id = update.effective_user.id
        habits = db.get_user_habits(user_id)
        
        if not habits:
            await update.message.reply_text(
                "📭 **У вас пока нет привычек**\n\n"
                "Добавьте первую привычку, чтобы начать отслеживание!",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
            return States.MAIN_MENU
        
        completed_ids = db.get_today_completed_ids(user_id)
        available_habits = [h for h in habits if h['id'] not in completed_ids]
        
        if not available_habits:
            await update.message.reply_text(
                "🎉 **Все привычки выполнены сегодня!**\n\n"
                "Отличная работа! Завтра - новый день!",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
            return States.MAIN_MENU
        
        await update.message.reply_text(
            "✅ **Отметить выполнение привычки**\n\n"
            "Выберите привычку, которую выполнили сегодня:",
            parse_mode='Markdown',
            reply_markup=get_habits_keyboard(available_habits, "track_")
        )
        return States.TRACK_HABIT
        
    except Exception as e:
        logger.error(f"Error in track_habit_start: {e}")
        return States.MAIN_MENU

async def track_habit_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение отслеживания"""
    try:
        query = update.callback_query
        await query.answer()
        
        habit_id = int(query.data.replace("track_", ""))
        user_id = query.from_user.id
        
        habits = db.get_user_habits(user_id)
        habit = next((h for h in habits if h['id'] == habit_id), None)
        
        if not habit:
            await query.edit_message_text("❌ Привычка не найдена")
            return States.MAIN_MENU
        
        context.user_data['track_habit'] = habit_id
        
        await query.edit_message_text(
            f"✅ **Подтвердите выполнение**\n\n"
            f"{habit['habit_emoji']} **{habit['habit_name']}**\n\n"
            f"Хотите добавить заметку к выполнению?",
            parse_mode='Markdown',
            reply_markup=get_yes_no_keyboard()
        )
        return States.ADD_NOTE
        
    except Exception as e:
        logger.error(f"Error in track_habit_finish: {e}")
        return States.MAIN_MENU

async def add_note_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка решения о заметке"""
    try:
        query = update.callback_query
        await query.answer()
        
        if query.data == "yes":
            await query.edit_message_text(
                "📝 **Добавление заметки**\n\n"
                "Введите заметку о выполнении:",
                parse_mode='Markdown'
            )
            return States.ADD_NOTE
        else:
            # Анимация сохранения без заметки
            await query.edit_message_text("💾 **Сохраняю...**", parse_mode='Markdown')
            await asyncio.sleep(0.5)
            
            return await complete_habit(update, context, "")
            
    except Exception as e:
        logger.error(f"Error in add_note_decision: {e}")
        return States.MAIN_MENU

async def add_note_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение с заметкой"""
    try:
        note = update.message.text.strip()[:200]
        
        # Анимация сохранения
        message = await update.message.reply_text("💾 **Сохраняю с заметкой...**", parse_mode='Markdown')
        await asyncio.sleep(0.5)
        
        return await complete_habit(update, context, note, message)
        
    except Exception as e:
        logger.error(f"Error in add_note_finish: {e}")
        return States.MAIN_MENU

async def complete_habit(update: Update, context: ContextTypes.DEFAULT_TYPE, note: str = "", message=None):
    """Завершение привычки"""
    try:
        habit_id = context.user_data.get('track_habit')
        user_id = update.effective_user.id if update.message else update.callback_query.from_user.id
        
        if not habit_id:
            if update.message:
                await update.message.reply_text("❌ Ошибка: привычка не найдена", reply_markup=get_main_menu_keyboard())
            else:
                query = update.callback_query
                await query.edit_message_text("❌ Ошибка: привычка не найдена", reply_markup=get_main_menu_keyboard())
            return States.MAIN_MENU
        
        success = db.mark_habit_done(user_id, habit_id, note)
        
        if success:
            completed, total = db.get_today_stats(user_id)
            
            # Анимация успеха
            if message:
                success_frames = ["✅ Сохранено!", "✨ Готово!", "🎉 Отлично!"]
                for frame in success_frames:
                    await message.edit_text(f"**{frame}**", parse_mode='Markdown')
                    await asyncio.sleep(0.3)
            
            final_msg = f"""✅ **Привычка отмечена!**

📊 Прогресс сегодня: **{completed}/{total}** привычек"""
            
            if note:
                final_msg += f"\n📝 **Заметка:** {note}"
            
            if completed == total:
                final_msg += "\n\n🎉 **Поздравляю! Все привычки выполнены!**"
            
            if update.message and message:
                await message.edit_text(final_msg, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
            elif update.callback_query:
                query = update.callback_query
                await query.edit_message_text(final_msg, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
        else:
            error_msg = "⚠️ **Эта привычка уже была отмечена сегодня!**"
            if update.message and message:
                await message.edit_text(error_msg, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
            elif update.callback_query:
                query = update.callback_query
                await query.edit_message_text(error_msg, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
        
        if 'track_habit' in context.user_data:
            del context.user_data['track_habit']
        
        return States.MAIN_MENU
        
    except Exception as e:
        logger.error(f"Error in complete_habit: {e}")
        return States.MAIN_MENU

# =================== УДАЛЕНИЕ ПРИВЫЧЕК ===================
async def delete_habit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало удаления"""
    try:
        user_id = update.effective_user.id
        habits = db.get_user_habits(user_id)
        
        if not habits:
            await update.message.reply_text(
                "📭 **Нет привычек для удаления**",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
            return States.MAIN_MENU
        
        await update.message.reply_text(
            "🗑️ **Удаление привычки**\n\n"
            "Выберите привычку для удаления:",
            parse_mode='Markdown',
            reply_markup=get_habits_keyboard(habits, "delete_")
        )
        return States.DELETE_HABIT
        
    except Exception as e:
        logger.error(f"Error in delete_habit_start: {e}")
        return States.MAIN_MENU

async def delete_habit_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение удаления"""
    try:
        query = update.callback_query
        await query.answer()
        
        if query.data == "cancel":
            await query.edit_message_text("❌ Отменено", reply_markup=get_main_menu_keyboard())
            return States.MAIN_MENU
        
        habit_id = int(query.data.replace("delete_", ""))
        user_id = query.from_user.id
        
        habits = db.get_user_habits(user_id)
        habit = next((h for h in habits if h['id'] == habit_id), None)
        
        if habit:
            # Анимация удаления
            delete_frames = ["🗑️ Удаляю...", "✅ Удалено!"]
            for frame in delete_frames:
                await query.edit_message_text(f"**{frame}**", parse_mode='Markdown')
                await asyncio.sleep(0.3)
            
            db.delete_habit(habit_id, user_id)
            
            await query.edit_message_text(
                f"🗑️ **Привычка удалена**\n\n"
                f"{habit['habit_emoji']} **{habit['habit_name']}**",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
        else:
            await query.edit_message_text("❌ Привычка не найдена", reply_markup=get_main_menu_keyboard())
        
        return States.MAIN_MENU
        
    except Exception as e:
        logger.error(f"Error in delete_habit_finish: {e}")
        return States.MAIN_MENU

# =================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===================
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик отмены"""
    try:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("❌ Отменено", reply_markup=get_main_menu_keyboard())
        return States.MAIN_MENU
    except:
        return States.MAIN_MENU

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда отмены"""
    await update.message.reply_text("❌ Отменено", reply_markup=get_main_menu_keyboard())
    return States.MAIN_MENU

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    try:
        text = update.message.text
        
        handlers = {
            "➕ Добавить привычку": add_habit_start,
            "📊 Статистика": show_stats,
            "📋 Мои привычки": show_habits,
            "✅ Отметить выполнение": track_habit_start,
            "🗑️ Удалить привычку": delete_habit_start,
            "ℹ️ Помощь": help_command
        }
        
        if text in handlers:
            return await handlers[text](update, context)
        else:
            await update.message.reply_text(
                "👇 **Используйте кнопки меню ниже**",
                reply_markup=get_main_menu_keyboard()
            )
            return States.MAIN_MENU
            
    except Exception as e:
        logger.error(f"Error in handle_text_message: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка. Попробуйте еще раз.",
            reply_markup=get_main_menu_keyboard()
        )
        return States.MAIN_MENU

# =================== ЗАПУСК БОТА ===================
def main():
    """Основная функция запуска"""
    print("🚀 Запуск бота с анимацией кнопок...")
    print(f"📅 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        application = Application.builder().token(TOKEN).build()
        
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
            ],
            states={
                States.MAIN_MENU: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message),
                    CommandHandler('add', add_habit_start),
                    CommandHandler('habits', show_habits),
                    CommandHandler('stats', show_stats),
                    CommandHandler('help', help_command),
                    CommandHandler('track', track_habit_start),
                    CommandHandler('delete', delete_habit_start),
                ],
                States.ADD_HABIT: [
                    CallbackQueryHandler(add_predefined_habit, pattern='^predef_'),
                    CallbackQueryHandler(add_custom_habit_start, pattern='^custom_habit$'),
                    CallbackQueryHandler(cancel_handler, pattern='^cancel$'),
                ],
                States.ADD_CUSTOM_HABIT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_custom_habit_finish)
                ],
                States.DELETE_HABIT: [
                    CallbackQueryHandler(delete_habit_finish, pattern='^(delete_|cancel$)'),
                ],
                States.TRACK_HABIT: [
                    CallbackQueryHandler(track_habit_finish, pattern='^track_'),
                    CallbackQueryHandler(cancel_handler, pattern='^cancel$'),
                ],
                States.ADD_NOTE: [
                    CallbackQueryHandler(add_note_decision, pattern='^(yes|no)$'),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_note_finish)
                ],
            },
            fallbacks=[
                CommandHandler('cancel', cancel_command),
                CommandHandler('start', start)
            ],
        )
        
        application.add_handler(conv_handler)
        
        print("✅ Бот успешно запущен!")
        print("✨ Анимация кнопок активна")
        print("📱 Ожидание сообщений...")
        print("🛑 Нажмите Ctrl+C для остановки")
        
        application.run_polling(
            poll_interval=0.5,
            timeout=20,
            drop_pending_updates=True
        )
        
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Critical error: {e}")
        print(f"❌ Критическая ошибка: {e}")

if __name__ == "__main__":
    main()
