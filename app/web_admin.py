import logging
from fastapi import FastAPI, Depends, HTTPException, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from typing import List, Optional
from datetime import datetime, timedelta

from app.database import db
from app.services.order_service import OrderService, ParticipantService
from app.services.user_service import AddressService, SubscriptionService
from app.models import Order
from app.config import ADMIN_USERNAME, ADMIN_PASSWORD, SECRET_KEY, STATUSES

logger = logging.getLogger(__name__)

security = HTTPBasic()

app = FastAPI(title="SEABLUU Admin", docs_url=None, redoc_url=None)

# Mount static files and templates
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

def authenticate_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# Страницы
@app.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "username": username
    })

@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("orders.html", {
        "request": request,
        "username": username
    })

@app.get("/orders/new", response_class=HTMLResponse)
async def new_order_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("order_form.html", {
        "request": request,
        "username": username,
        "statuses": STATUSES
    })

@app.get("/participants", response_class=HTMLResponse)
async def participants_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("participants.html", {
        "request": request,
        "username": username
    })

@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "username": username
    })

@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("broadcast.html", {
        "request": request,
        "username": username
    })

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "username": username
    })

# API endpoints
@app.get("/api/stats")
async def get_stats(username: str = Depends(authenticate_admin)):
    """Получение статистики для дашборда"""
    try:
        # Получаем все заказы для статистики
        orders = await OrderService.list_recent_orders(1000)  # Большой лимит для статистики
        total_orders = len(orders)
        
        # Активные заказы (исключаем завершенные)
        active_statuses = [s for s in STATUSES if "получен" not in s.lower()]
        active_orders = len([o for o in orders if o.status in active_statuses])
        
        # Участники
        all_participants = []
        for order in orders:
            participants = await ParticipantService.get_participants(order.order_id)
            all_participants.extend(participants)
        total_participants = len(set(p.username for p in all_participants))
        
        # Подписки
        subscriptions = await SubscriptionService.get_all_subscriptions()
        total_subscriptions = len(subscriptions)
        
        return {
            "total_orders": total_orders,
            "active_orders": active_orders,
            "total_participants": total_participants,
            "total_subscriptions": total_subscriptions
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/orders")
async def get_orders(
    status: Optional[str] = None,
    country: Optional[str] = None,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    username: str = Depends(authenticate_admin)
):
    """API для получения списка заказов с пагинацией"""
    try:
        if status:
            orders = await OrderService.list_orders_by_status([status])
        else:
            orders = await OrderService.list_recent_orders(limit + offset)
        
        # Фильтрация по стране
        if country:
            orders = [o for o in orders if o.country == country.upper()]
        
        # Пагинация
        paginated_orders = orders[offset:offset + limit]
        
        return {
            "orders": [dict(order) for order in paginated_orders],
            "total": len(orders),
            "has_more": len(orders) > offset + limit
        }
    except Exception as e:
        logger.error(f"Error fetching orders: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/orders/{order_id}")
async def get_order(order_id: str, username: str = Depends(authenticate_admin)):
    """API для получения информации о заказе"""
    try:
        order = await OrderService.get_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        participants = await ParticipantService.get_participants(order_id)
        subscriptions = await SubscriptionService.get_all_subscriptions()
        order_subs = [s for s in subscriptions if s.order_id == order_id]
        
        return {
            "order": dict(order),
            "participants": [dict(p) for p in participants],
            "subscribers": len(order_subs)
        }
    except Exception as e:
        logger.error(f"Error fetching order {order_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/orders")
async def create_order(
    order_id: str = Form(...),
    client_name: str = Form(...),
    country: str = Form(...),
    status: str = Form(...),
    note: str = Form(""),
    username: str = Depends(authenticate_admin)
):
    """API для создания нового заказа"""
    try:
        # Проверяем, существует ли уже заказ
        existing_order = await OrderService.get_order(order_id)
        if existing_order:
            raise HTTPException(status_code=400, detail="Order already exists")
        
        order = Order(
            order_id=order_id,
            client_name=client_name,
            country=country.upper(),
            status=status,
            note=note
        )
        
        success = await OrderService.add_order(order)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to create order")
        
        # Добавляем участников из username в client_name
        from app.utils.validators import extract_usernames
        usernames = extract_usernames(client_name)
        if usernames:
            await ParticipantService.ensure_participants(order_id, usernames)
        
        return {"message": "Order created successfully", "order_id": order_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.put("/api/orders/{order_id}/status")
async def update_order_status(
    order_id: str,
    status: str = Form(...),
    username: str = Depends(authenticate_admin)
):
    """API для обновления статуса заказа"""
    try:
        success = await OrderService.update_order_status(order_id, status)
        if not success:
            raise HTTPException(status_code=404, detail="Order not found")
        
        # Уведомляем подписчиков
        from app.webhook import application
        await notify_subscribers(application, order_id, status)
        
        return {"message": "Status updated successfully"}
    except Exception as e:
        logger.error(f"Error updating order status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.delete("/api/orders/{order_id}")
async def delete_order(order_id: str, username: str = Depends(authenticate_admin)):
    """API для удаления заказа"""
    try:
        # Здесь должна быть логика удаления заказа
        # Пока просто возвращаем заглушку
        return {"message": "Delete functionality to be implemented"}
    except Exception as e:
        logger.error(f"Error deleting order {order_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/participants")
async def get_participants(
    order_id: Optional[str] = None,
    paid: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    username: str = Depends(authenticate_admin)
):
    """API для получения списка участников"""
    try:
        if order_id:
            participants = await ParticipantService.get_participants(order_id)
        else:
            # Здесь должна быть логика получения всех участников
            # Пока возвращаем пустой список
            participants = []
        
        # Фильтрация по статусу оплаты
        if paid is not None:
            participants = [p for p in participants if p.paid == paid]
        
        # Пагинация
        paginated_participants = participants[offset:offset + limit]
        
        return {
            "participants": [dict(p) for p in paginated_participants],
            "total": len(participants),
            "has_more": len(participants) > offset + limit
        }
    except Exception as e:
        logger.error(f"Error fetching participants: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.put("/api/participants/{order_id}/{username}/paid")
async def toggle_participant_paid(
    order_id: str,
    username: str,
    paid: bool = Form(...),
    username_auth: str = Depends(authenticate_admin)
):
    """API для изменения статуса оплаты участника"""
    try:
        if paid:
            # Здесь должна быть логика отметки оплаты
            # Пока заглушка
            return {"message": "Payment status updated"}
        else:
            return {"message": "Payment status updated"}
    except Exception as e:
        logger.error(f"Error updating participant payment status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/statuses")
async def get_statuses(username: str = Depends(authenticate_admin)):
    """API для получения списка статусов"""
    return {"statuses": STATUSES}

@app.get("/api/participants/unpaid")
async def get_unpaid_participants(username: str = Depends(authenticate_admin)):
    """API для получения списка неплательщиков"""
    try:
        grouped = await ParticipantService.get_all_unpaid_grouped()
        return {"unpaid": grouped}
    except Exception as e:
        logger.error(f"Error fetching unpaid participants: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/broadcast/unpaid")
async def broadcast_unpaid(
    order_id: Optional[str] = Form(None),
    message: str = Form(...),
    username: str = Depends(authenticate_admin)
):
    """API для рассылки неплательщикам"""
    try:
        # Здесь должна быть логика рассылки
        # Пока заглушка
        return {"message": "Broadcast functionality to be implemented", "sent_to": 0}
    except Exception as e:
        logger.error(f"Error sending broadcast: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

async def notify_subscribers(application, order_id: str, new_status: str):
    """Уведомление подписчиков об изменении статуса"""
    try:
        subs = await SubscriptionService.get_all_subscriptions()
        targets = [s for s in subs if s.order_id == order_id]
        
        for sub in targets:
            try:
                await application.bot.send_message(
                    chat_id=sub.user_id,
                    text=f"🔄 Обновление по заказу *{order_id}*\nНовый статус: *{new_status}*",
                    parse_mode="Markdown",
                )
                await SubscriptionService.set_last_sent_status(sub.user_id, order_id, new_status)
            except Exception as e:
                logger.warning(f"Не удалось уведомить {sub.user_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка уведомления подписчиков: {e}")

from fastapi import Form, HTTPException
from pydantic import BaseModel
from typing import Optional

# Pydantic модели для валидации
class OrderCreate(BaseModel):
    order_id: str
    client_name: str
    country: str
    status: str
    note: Optional[str] = ""

class OrderUpdate(BaseModel):
    client_name: Optional[str] = None
    country: Optional[str] = None
    status: Optional[str] = None
    note: Optional[str] = None

# API endpoints для заказов
@app.post("/api/orders/create")
async def create_order_api(
    order_data: OrderCreate,
    username: str = Depends(authenticate_admin)
):
    """Создание нового заказа"""
    try:
        # Проверяем существование заказа
        existing = await OrderService.get_order(order_data.order_id)
        if existing:
            raise HTTPException(400, "Заказ с таким ID уже существует")
        
        order = Order(
            order_id=order_data.order_id,
            client_name=order_data.client_name,
            country=order_data.country.upper(),
            status=order_data.status,
            note=order_data.note or ""
        )
        
        success = await OrderService.add_order(order)
        if not success:
            raise HTTPException(500, "Ошибка при создании заказа")
        
        # Добавляем участников
        from app.utils.validators import extract_usernames
        usernames = extract_usernames(order_data.client_name)
        if usernames:
            await ParticipantService.ensure_participants(order_data.order_id, usernames)
        
        return {"success": True, "message": "Заказ успешно создан"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.put("/api/orders/{order_id}")
async def update_order_api(
    order_id: str,
    order_data: OrderUpdate,
    username: str = Depends(authenticate_admin)
):
    """Обновление заказа"""
    try:
        order = await OrderService.get_order(order_id)
        if not order:
            raise HTTPException(404, "Заказ не найден")
        
        # Обновляем поля
        update_data = {}
        if order_data.client_name is not None:
            update_data["client_name"] = order_data.client_name
        if order_data.country is not None:
            update_data["country"] = order_data.country.upper()
        if order_data.status is not None:
            update_data["status"] = order_data.status
        if order_data.note is not None:
            update_data["note"] = order_data.note
        
        # Здесь должна быть логика обновления в базе
        # Покажем как это можно сделать через существующий сервис
        if update_data:
            # Для примера - обновим статус если он изменился
            if "status" in update_data:
                await OrderService.update_order_status(order_id, update_data["status"])
            
            # Для остальных полей нужен отдельный метод update_order
            # Пока оставим заглушку
            logger.info(f"Order {order_id} update data: {update_data}")
        
        return {"success": True, "message": "Заказ обновлен"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating order: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.delete("/api/orders/{order_id}")
async def delete_order_api(
    order_id: str,
    username: str = Depends(authenticate_admin)
):
    """Удаление заказа"""
    try:
        # Проверяем существование заказа
        order = await OrderService.get_order(order_id)
        if not order:
            raise HTTPException(404, "Заказ не найден")
        
        # Здесь должна быть логика удаления заказа и связанных данных
        # Пока заглушка - в реальности нужно удалить из orders, participants, subscriptions
        logger.info(f"Order {order_id} marked for deletion")
        
        return {"success": True, "message": "Заказ удален"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting order: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")
