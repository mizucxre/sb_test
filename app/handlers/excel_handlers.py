import logging
import pandas as pd
import io
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from app.config import ADMIN_IDS
from app.utils.helpers import reply_animated, reply_markdown_animated, _is_admin
from app.services.order_service import OrderService, ParticipantService
from app.models import Order
from app.utils.validators import extract_usernames

logger = logging.getLogger(__name__)

async def handle_excel_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка загрузки Excel файлов"""
    if not _is_admin(update.effective_user.id, ADMIN_IDS):
        return

    document = update.message.document
    if not document:
        await reply_animated(update, context, "📎 Пожалуйста, отправьте Excel файл")
        return

    # Проверяем что это Excel файл
    if not document.file_name.endswith(('.xlsx', '.xls')):
        await reply_animated(update, context, "❌ Поддерживаются только Excel файлы (.xlsx, .xls)")
        return

    try:
        await reply_animated(update, context, "⏳ Обрабатываю Excel файл...")
        
        # Скачиваем файл
        file = await context.bot.get_file(document.file_id)
        file_bytes = await file.download_as_bytearray()
        
        # Читаем Excel
        df = pd.read_excel(io.BytesIO(file_bytes))
        
        # Проверяем обязательные колонки
        required_columns = ['order_id', 'client_name', 'country', 'status']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            await reply_animated(update, context, f"❌ В файле отсутствуют колонки: {', '.join(missing_columns)}")
            return

        # Обрабатываем каждую строку
        success_count = 0
        error_count = 0
        errors = []

        for index, row in df.iterrows():
            try:
                # Создаем заказ
                order = Order(
                    order_id=str(row['order_id']).strip(),
                    client_name=str(row['client_name']),
                    country=str(row['country']).upper(),
                    status=str(row['status']),
                    note=str(row.get('note', ''))
                )
                
                # Добавляем в базу
                success = await OrderService.add_order(order)
                
                if success:
                    # Добавляем участников
                    usernames = extract_usernames(str(row['client_name']))
                    if usernames:
                        await ParticipantService.ensure_participants(order.order_id, usernames)
                    
                    success_count += 1
                else:
                    error_count += 1
                    errors.append(f"Строка {index+2}: не удалось добавить заказ {order.order_id}")
                    
            except Exception as e:
                error_count += 1
                errors.append(f"Строка {index+2}: {str(e)}")

        # Формируем отчет
        report = [
            f"📊 Импорт Excel завершен:",
            f"✅ Успешно: {success_count}",
            f"❌ Ошибки: {error_count}",
            f"📁 Всего строк: {len(df)}"
        ]
        
        if errors:
            report.append("\nОшибки:")
            report.extend(errors[:10])  # Показываем только первые 10 ошибок
            if len(errors) > 10:
                report.append(f"... и еще {len(errors) - 10} ошибок")

        await reply_animated(update, context, "\n".join(report))

    except Exception as e:
        logger.error(f"Ошибка обработки Excel: {e}")
        await reply_animated(update, context, f"❌ Ошибка обработки файла: {e}")

def register(application):
    """Регистрация хэндлеров для Excel"""
    application.add_handler(MessageHandler(
        filters.Document.ALL & filters.User(ADMIN_IDS),
        handle_excel_upload
    ))
    logger.info("✅ Excel хэндлеры зарегистрированы")
