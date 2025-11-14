from telegram import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton
)

# Текст кнопок
BTN_TRACK = "🔍 Отследить разбор"
BTN_ADDRS = "🏠 Мои адреса"
BTN_SUBS  = "🔔 Мои подписки"
BTN_CANCEL = "❌ Отмена"

# Основная клавиатура
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_TRACK)],
        [KeyboardButton(BTN_ADDRS), KeyboardButton(BTN_SUBS)],
        [KeyboardButton(BTN_CANCEL)],
    ],
    resize_keyboard=True,
)
