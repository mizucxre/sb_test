from telegram import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton
)

# Текст кнопок
BTN_TRACK = "🔍 Отследить разбор"
BTN_ADDRS = "🏠 Мой адрес"
BTN_SUBS  = "🔔 Мои подписки"
BTN_HELP = "❓ Помощь"
BTN_BACK = "⬅️ Назад"

# Основная клавиатура
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_TRACK)],
        [KeyboardButton(BTN_ADDRS), KeyboardButton(BTN_SUBS)],
        [KeyboardButton(BTN_HELP)],
    ],
    resize_keyboard=True,
)

# Клавиатура с кнопкой Назад
BACK_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_BACK)],
    ],
    resize_keyboard=True,
)
