import logging
import os
import uuid
from fastapi import UploadFile, File
from PIL import Image
from fastapi import FastAPI, Depends, HTTPException, Request, Form, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import List, Optional
from datetime import datetime, timedelta
import json
import io

from app.database import db
from app.services.order_service import OrderService, ParticipantService
from app.services.user_service import AddressService, SubscriptionService
from app.services.admin_service import AdminService
from app.services.admin_chat_service import AdminChatService
from app.models import Order, AdminUserCreate, AdminUserUpdate, AdminChatMessageCreate
from app.config import STATUSES
from app.utils.security import verify_password, create_access_token, verify_token, generate_avatar_url
from app.utils.session import get_current_admin  # Убираем require_super_admin

logger = logging.getLogger(__name__)

app = FastAPI(title="SEABLUU Admin", docs_url=None, redoc_url=None)

# Определяем базовые пути для статики и шаблонов
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Mount static files and templates
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

def serialize_model(model):
    """Сериализация Pydantic модели в словарь с обработкой разных версий Pydantic"""
    try:
        # Пробуем Pydantic v2
        return model.model_dump()
    except AttributeError:
        try:
            # Пробуем Pydantic v1
            return model.dict()
        except AttributeError:
            # Если не Pydantic модель, используем __dict__
            return model.__dict__

# Вспомогательная функция для проверки супер-админа
def check_super_admin(current_admin: dict):
    """Проверка что пользователь супер-админ"""
    if current_admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_admin

# Страница входа
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request
    })

@app.post("/login")
async def login(request: Request, response: Response):
    """Аутентификация администратора"""
    try:
        form_data = await request.form()
        username = form_data.get("username")
        password = form_data.get("password")
        
        if not username or not password:
            raise HTTPException(400, "Необходимо указать имя пользователя и пароль")
        
        # Проверяем учетные данные
        admin_user = await AdminService.authenticate_user(username, password)
        if not admin_user:
            raise HTTPException(401, "Неверное имя пользователя или пароль")
        
        # Создаем токен
        access_token = create_access_token(
            data={"sub": admin_user.username, "user_id": admin_user.id, "role": admin_user.role}
        )
        
        # Устанавливаем cookie
        response = RedirectResponse(url="/admin/", status_code=302)
        response.set_cookie(
            key="admin_token",
            value=access_token,
            httponly=True,
            max_age=60 * 60 * 24 * 7,  # 7 дней
            secure=False,  # Для продакшена установите True
            samesite="lax"
        )
        
        # Обновляем время последнего входа
        await AdminService.update_last_login(admin_user.id)
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.get("/logout")
async def logout(response: Response):
    """Выход из системы"""
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie("admin_token")
    return response

# Защищенные страницы
@app.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "dashboard"
    })

@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("orders.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "orders",
        "statuses": STATUSES
    })

@app.get("/orders/new", response_class=HTMLResponse)
async def new_order_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("order_form.html", {
        "request": request,
        "current_admin": current_admin,
        "statuses": STATUSES,
        "current_page": "orders"
    })

@app.get("/orders/{order_id}/edit", response_class=HTMLResponse)
async def edit_order_page(request: Request, order_id: str, current_admin: dict = Depends(get_current_admin)):
    order = await OrderService.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    return templates.TemplateResponse("order_form.html", {
        "request": request,
        "current_admin": current_admin,
        "statuses": STATUSES,
        "current_page": "orders",
        "order": order
    })

@app.get("/participants", response_class=HTMLResponse)
async def participants_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("participants.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "participants"
    })

@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "reports"
    })

@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("broadcast.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "broadcast",
        "statuses": STATUSES
    })

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "settings",
        "statuses": STATUSES
    })

# Новые страницы для управления администраторами
@app.get("/admin-users", response_class=HTMLResponse)
async def admin_users_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    # Проверяем права супер-админа вручную
    check_super_admin(current_admin)
    
    return templates.TemplateResponse("admin_users.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "admin_users"
    })

@app.get("/admin-users/new", response_class=HTMLResponse)
async def new_admin_user_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    # Проверяем права супер-админа вручную
    check_super_admin(current_admin)
    
    return templates.TemplateResponse("admin_user_form.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "admin_users"
    })

@app.get("/admin-users/{user_id}/edit", response_class=HTMLResponse)
async def edit_admin_user_page(request: Request, user_id: int, current_admin: dict = Depends(get_current_admin)):
    # Проверяем права супер-админа вручную
    check_super_admin(current_admin)
    
    user = await AdminService.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Admin user not found")
    
    return templates.TemplateResponse("admin_user_form.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "admin_users",
        "user": user
    })

@app.get("/addresses", response_class=HTMLResponse)
async def addresses_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("addresses.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "addresses"
    })

@app.get("/admin-chat", response_class=HTMLResponse)
async def admin_chat_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("admin_chat.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "admin_chat"
    })

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "profile"
    })

# API endpoints для администраторов
@app.get("/api/admin/users")
async def get_admin_users(current_admin: dict = Depends(get_current_admin)):
    """Получение списка администраторов"""
    try:
        check_super_admin(current_admin)
        users = await AdminService.get_all_users()
        return {"users": users}
    except Exception as e:
        logger.error(f"Error fetching admin users: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/admin/users")
async def create_admin_user(request: Request, current_admin: dict = Depends(get_current_admin)):
    """Создание нового администратора"""
    try:
        check_super_admin(current_admin)
        data = await request.json()
        user_data = AdminUserCreate(**data)
        
        # Проверяем существование пользователя
        existing = await AdminService.get_user_by_username(user_data.username)
        if existing:
            raise HTTPException(400, "Пользователь с таким именем уже существует")
        
        user = await AdminService.create_user(user_data)
        return {"success": True, "user": user, "message": "Администратор создан"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating admin user: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.put("/api/admin/users/{user_id}")
async def update_admin_user(user_id: int, request: Request, current_admin: dict = Depends(get_current_admin)):
    """Обновление администратора"""
    try:
        check_super_admin(current_admin)
        data = await request.json()
        user_data = AdminUserUpdate(**data)
        
        user = await AdminService.update_user(user_id, user_data)
        if not user:
            raise HTTPException(404, "Пользователь не найден")
        
        return {"success": True, "user": user, "message": "Администратор обновлен"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating admin user: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.delete("/api/admin/users/{user_id}")
async def delete_admin_user(user_id: int, current_admin: dict = Depends(get_current_admin)):
    """Удаление администратора"""
    try:
        check_super_admin(current_admin)
        if user_id == current_admin["user_id"]:
            raise HTTPException(400, "Нельзя удалить самого себя")
        
        success = await AdminService.delete_user(user_id)
        if not success:
            raise HTTPException(404, "Пользователь не найден")
        
        return {"success": True, "message": "Администратор удален"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting admin user: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

# API для чата администраторов
@app.get("/api/admin/chat/messages")
async def get_chat_messages(current_admin: dict = Depends(get_current_admin)):
    """Получение сообщений чата"""
    try:
        messages = await AdminChatService.get_recent_messages(50)
        return {"messages": messages}
    except Exception as e:
        logger.error(f"Error fetching chat messages: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/admin/chat/messages")
async def create_chat_message(request: Request, current_admin: dict = Depends(get_current_admin)):
    """Создание сообщения в чате"""
    try:
        data = await request.json()
        message_data = AdminChatMessageCreate(**data)
        
        message = await AdminChatService.create_message(
            current_admin["user_id"], 
            message_data.message
        )
        
        return {"success": True, "message": message}
        
    except Exception as e:
        logger.error(f"Error creating chat message: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

# API для профиля
@app.put("/api/admin/profile")
async def update_profile(request: Request, current_admin: dict = Depends(get_current_admin)):
    """Обновление профиля текущего пользователя"""
    try:
        data = await request.json()
        
        # Только супер-админ может менять свою роль
        if "role" in data and current_admin["role"] != "super_admin":
            del data["role"]
        
        user_data = AdminUserUpdate(**data)
        user = await AdminService.update_user(current_admin["user_id"], user_data)
        
        return {"success": True, "user": user, "message": "Профиль обновлен"}
        
    except Exception as e:
        logger.error(f"Error updating profile: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.put("/api/admin/profile/password")
async def change_password(request: Request, current_admin: dict = Depends(get_current_admin)):
    """Смена пароля"""
    try:
        data = await request.json()
        current_password = data.get("current_password")
        new_password = data.get("new_password")
        
        if not current_password or not new_password:
            raise HTTPException(400, "Необходимо указать текущий и новый пароль")
        
        success = await AdminService.change_password(
            current_admin["user_id"], 
            current_password, 
            new_password
        )
        
        if not success:
            raise HTTPException(400, "Неверный текущий пароль")
        
        return {"success": True, "message": "Пароль изменен"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error changing password: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.get("/api/stats")
async def get_stats(current_admin: dict = Depends(get_current_admin)):
    """Оптимизированное получение статистики для дашборда"""
    try:
        # Используем один SQL запрос для получения всей статистики
        async with db.pool.acquire() as conn:
            # Получаем общее количество заказов
            total_orders = await conn.fetchval("SELECT COUNT(*) FROM orders")
            
            # Получаем количество активных заказов
            active_statuses = [s for s in STATUSES if "получен" not in s.lower() and "доставлен" not in s.lower()]
            if active_statuses:
                active_orders = await conn.fetchval(
                    "SELECT COUNT(*) FROM orders WHERE status = ANY($1)",
                    active_statuses
                )
            else:
                active_orders = 0
            
            # Получаем количество уникальных участников одним запросом
            unique_participants = await conn.fetchval(
                "SELECT COUNT(DISTINCT username) FROM participants"
            )
            
            # Получаем количество подписок
            total_subscriptions = await conn.fetchval("SELECT COUNT(*) FROM subscriptions")
        
        return {
            "total_orders": total_orders or 0,
            "active_orders": active_orders or 0,
            "total_participants": unique_participants or 0,
            "total_subscriptions": total_subscriptions or 0
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        # Возвращаем базовые значения в случае ошибки
        return {
            "total_orders": 0,
            "active_orders": 0,
            "total_participants": 0,
            "total_subscriptions": 0
        }
        
# Middleware для проверки аутентификации
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Пропускаем страницу логина и статические файлы
    if request.url.path in ["/admin/login", "/admin/logout"] or request.url.path.startswith("/admin/static"):
        return await call_next(request)
    
    # Проверяем токен для защищенных страниц
    token = request.cookies.get("admin_token")
    if not token:
        return RedirectResponse(url="/admin/login")
    
    payload = verify_token(token)
    if not payload:
        response = RedirectResponse(url="/admin/login")
        response.delete_cookie("admin_token")
        return response
    
    # Добавляем информацию о пользователе в запрос
    request.state.admin_user = payload
    
    response = await call_next(request)
    return response

# Существующие API endpoints с новой аутентификацией
@app.get("/api/orders")
async def get_orders(
    status: Optional[str] = None,
    country: Optional[str] = None,
    note: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_admin: dict = Depends(get_current_admin)
):
    """API для получения списка заказов с пагинацией и фильтрацией по меткам"""
    try:
        # Получаем заказы с фильтрацией по меткам
        if note:
            orders = await OrderService.list_orders_by_note(note)
        elif status:
            orders = await OrderService.list_orders_by_status([status])
        else:
            # Получаем все заказы для правильного подсчета total
            orders = await OrderService.list_recent_orders(10000)
            
        # Фильтрация по стране
        if country:
            orders = [o for o in orders if o.country == country.upper()]
        
        # Фильтрация по поиску
        if search:
            search_lower = search.lower()
            orders = [o for o in orders if 
                     search_lower in o.order_id.lower() or 
                     search_lower in o.client_name.lower() or
                     (o.note and search_lower in o.note.lower())]
        
        # Общее количество заказов (после фильтрации)
        total_orders = len(orders)
        
        # Пагинация
        paginated_orders = orders[offset:offset + limit]
        
        # Convert orders to dict for JSON serialization
        orders_data = []
        for order in paginated_orders:
            order_data = serialize_model(order)
            # Ensure datetime fields are serializable
            if order_data.get('created_at') and isinstance(order_data['created_at'], datetime):
                order_data['created_at'] = order_data['created_at'].isoformat()
            if order_data.get('updated_at') and isinstance(order_data['updated_at'], datetime):
                order_data['updated_at'] = order_data['updated_at'].isoformat()
            orders_data.append(order_data)
        
        return {
            "orders": orders_data,
            "total": total_orders,
            "has_more": total_orders > offset + limit,
            "offset": offset,
            "limit": limit
        }
    except Exception as e:
        logger.error(f"Error fetching orders: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/orders/unique-notes")
async def get_unique_notes(current_admin: dict = Depends(get_current_admin)):
    """API для получения уникальных меток из заказов"""
    try:
        notes = await OrderService.get_unique_notes()
        return {"notes": notes}
    except Exception as e:
        logger.error(f"Error fetching unique notes: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/orders/{order_id}")
async def get_order(order_id: str, current_admin: dict = Depends(get_current_admin)):
    """API для получения информации о заказе"""
    try:
        order = await OrderService.get_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        participants = await ParticipantService.get_participants(order_id)
        subscriptions = await SubscriptionService.get_all_subscriptions()
        order_subs = [s for s in subscriptions if s.order_id == order_id]
        
        # Convert to dict for JSON serialization
        order_data = serialize_model(order)
        if order_data.get('created_at') and isinstance(order_data['created_at'], datetime):
            order_data['created_at'] = order_data['created_at'].isoformat()
        if order_data.get('updated_at') and isinstance(order_data['updated_at'], datetime):
            order_data['updated_at'] = order_data['updated_at'].isoformat()
        
        participants_data = []
        for participant in participants:
            participant_data = serialize_model(participant)
            if participant_data.get('created_at') and isinstance(participant_data['created_at'], datetime):
                participant_data['created_at'] = participant_data['created_at'].isoformat()
            if participant_data.get('updated_at') and isinstance(participant_data['updated_at'], datetime):
                participant_data['updated_at'] = participant_data['updated_at'].isoformat()
            participants_data.append(participant_data)
        
        return {
            "order": order_data,
            "participants": participants_data,
            "subscribers": len(order_subs)
        }
    except Exception as e:
        logger.error(f"Error fetching order {order_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/orders/create")
async def create_order_api(
    request: Request,
    current_admin: dict = Depends(get_current_admin)
):
    """Создание нового заказа"""
    try:
        data = await request.json()
        
        # Проверяем существование заказа
        existing = await OrderService.get_order(data['order_id'])
        if existing:
            raise HTTPException(400, "Заказ с таким ID уже существует")
        
        order = Order(
            order_id=data['order_id'],
            client_name=data['client_name'],
            country=data['country'].upper(),
            status=data['status'],
            note=data.get('note', '')
        )
        
        success = await OrderService.add_order(order)
        if not success:
            raise HTTPException(500, "Ошибка при создании заказа")
        
        # Добавляем участников
        from app.utils.validators import extract_usernames
        usernames = extract_usernames(data['client_name'])
        if usernames:
            await ParticipantService.ensure_participants(data['order_id'], usernames)
        
        return {"success": True, "message": "Заказ успешно создан"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.put("/api/orders/{order_id}")
async def update_order_api(
    order_id: str,
    request: Request,
    current_admin: dict = Depends(get_current_admin)):
    """Обновление заказа"""
    try:
        order = await OrderService.get_order(order_id)
        if not order:
            raise HTTPException(404, "Заказ не найден")
        
        data = await request.json()
        
        # Сохраняем старый статус для проверки изменений
        old_status = order.status
        
        # Обновляем поля
        update_data = {}
        if data.get('client_name') is not None:
            update_data["client_name"] = data['client_name']
        if data.get('country') is not None:
            update_data["country"] = data['country'].upper()
        if data.get('status') is not None:
            update_data["status"] = data['status']
        if data.get('note') is not None:
            update_data["note"] = data['note']
        
        if update_data:
            success = await OrderService.update_order(order_id, update_data)
            if not success:
                raise HTTPException(500, "Ошибка при обновлении заказа")
        
            # Отправляем уведомления если статус изменился
            if 'status' in update_data and update_data['status'] != old_status:
                await OrderService._send_status_notifications(order_id, update_data['status'])
        
        return {"success": True, "message": "Заказ обновлен"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating order: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.delete("/api/orders/{order_id}")
async def delete_order_api(
    order_id: str,
    current_admin: dict = Depends(get_current_admin)):
    """Удаление заказа"""
    try:
        # Проверяем существование заказа
        order = await OrderService.get_order(order_id)
        if not order:
            raise HTTPException(404, "Заказ не найден")
        
        success = await OrderService.delete_order(order_id)
        if not success:
            raise HTTPException(500, "Ошибка при удалении заказа")
        
        return {"success": True, "message": "Заказ удален"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting order: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.get("/api/participants")
async def get_participants(
    order_id: Optional[str] = None,
    paid: Optional[bool] = None,
    search: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_admin: dict = Depends(get_current_admin)):
    """API для получения списка участников с оптимизированной пагинацией"""
    try:
        # Используем новый метод для получения участников с пагинацией на уровне БД
        result = await ParticipantService.get_participants_paginated(
            order_id=order_id,
            paid=paid,
            search=search,
            limit=limit,
            offset=offset
        )
        
        # Convert to dict for JSON serialization
        participants_data = []
        for participant in result["participants"]:
            participant_data = serialize_model(participant)
            if participant_data.get('created_at') and isinstance(participant_data['created_at'], datetime):
                participant_data['created_at'] = participant_data['created_at'].isoformat()
            if participant_data.get('updated_at') and isinstance(participant_data['updated_at'], datetime):
                participant_data['updated_at'] = participant_data['updated_at'].isoformat()
            participants_data.append(participant_data)
        
        return {
            "participants": participants_data,
            "total": result["total"],
            "has_more": result["has_more"],
            "offset": offset,
            "limit": limit
        }
    except Exception as e:
        logger.error(f"Error fetching participants: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.put("/api/participants/{order_id}/{username}/paid")
async def update_participant_paid(
    order_id: str,
    username: str,
    request: Request,
    current_admin: dict = Depends(get_current_admin)):
    """Изменение статуса оплаты участника"""
    try:
        # Используем toggle метод вместо получения данных из тела
        success = await ParticipantService.toggle_participant_paid(order_id, username)
        if not success:
            raise HTTPException(400, "Не удалось обновить статус оплаты")
        
        return {"success": True, "message": "Статус оплаты обновлен"}
        
    except Exception as e:
        logger.error(f"Error updating participant payment: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.post("/api/broadcast/unpaid")
async def broadcast_unpaid(
    request: Request,
    current_admin: dict = Depends(get_current_admin)):
    """Рассылка уведомлений неплательщикам"""
    try:
        # Проверяем, что тело запроса не пустое
        body = await request.body()
        if not body:
            raise HTTPException(400, "Empty request body")
        
        try:
            data = await request.json()
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            raise HTTPException(400, "Invalid JSON format")
            
        message = data.get('message', '')
        
        if not message:
            raise HTTPException(400, "Сообщение не может быть пустым")
        
        # Получаем всех неплательщиков
        from app.services.order_service import ParticipantService
        unpaid_grouped = await ParticipantService.get_all_unpaid_grouped()
        
        if not unpaid_grouped:
            return {
                "success": True, 
                "message": "Нет неплательщиков для рассылки",
                "result": {
                    "sent": 0,
                    "failed": 0, 
                    "total": 0
                }
            }
        
        # Собираем все username
        all_usernames = []
        for usernames in unpaid_grouped.values():
            all_usernames.extend(usernames)
        
        # Получаем user_id по username
        user_ids = await AddressService.get_user_ids_by_usernames(all_usernames)
        
        sent_count = 0
        failed_count = 0
        
        # Отправляем сообщения через Telegram бота
        for user_id in user_ids:
            try:
                # Импортируем бота здесь чтобы избежать циклических импортов
                from app.webhook import application
                await application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='HTML'
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Error sending message to {user_id}: {e}")
                failed_count += 1
        
        return {
            "success": True,
            "message": "Рассылка завершена",
            "result": {
                "sent": sent_count,
                "failed": failed_count,
                "total": len(user_ids)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error broadcasting to unpaid: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.post("/api/broadcast/reminder")
async def send_reminder(
    request: Request,
    current_admin: dict = Depends(get_current_admin)):
    """Отправка напоминания конкретному пользователю"""
    try:
        data = await request.json()
        message = data.get('message', '')
        usernames = data.get('usernames', [])
        
        if not message or not usernames:
            raise HTTPException(400, "Сообщение и список пользователей обязательны")
        
        # Получаем user_id по username
        user_ids = await AddressService.get_user_ids_by_usernames(usernames)
        
        if not user_ids:
            return {
                "success": False,
                "message": "Пользователи не найдены"
            }
        
        sent_count = 0
        failed_count = 0
        
        # Отправляем сообщения через Telegram бота
        for user_id in user_ids:
            try:
                from app.webhook import application
                await application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='HTML'
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Error sending reminder to {user_id}: {e}")
                failed_count += 1
        
        return {
            "success": True,
            "message": f"Напоминания отправлены ({sent_count}/{len(user_ids)})",
            "result": {
                "sent": sent_count,
                "failed": failed_count,
                "total": len(user_ids)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending reminders: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.post("/api/broadcast/all")
async def broadcast_all(
    request: Request,
    current_admin: dict = Depends(get_current_admin)):
    """Рассылка сообщения всем пользователям"""
    try:
        body = await request.body()
        if not body:
            raise HTTPException(400, "Empty request body")
        
        try:
            data = await request.json()
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            raise HTTPException(400, "Invalid JSON format")
            
        message = data.get('message', '')
        
        if not message:
            raise HTTPException(400, "Сообщение не может быть пустым")
        
        # Получаем всех пользователей с адресами
        async with db.pool.acquire() as conn:
            rows = await conn.fetch("SELECT DISTINCT user_id FROM addresses")
            user_ids = [row['user_id'] for row in rows]
        
        sent_count = 0
        failed_count = 0
        
        # Отправляем сообщения через Telegram бота
        for user_id in user_ids:
            try:
                from app.webhook import application
                await application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='HTML'
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Error sending message to {user_id}: {e}")
                failed_count += 1
        
        return {
            "success": True, 
            "message": "Рассылка завершена",
            "result": {
                "sent": sent_count,
                "failed": failed_count,
                "total": len(user_ids)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error broadcasting to all users: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.get("/api/statuses")
async def get_statuses(current_admin: dict = Depends(get_current_admin)):
    """API для получения списка статусов"""
    return {"statuses": STATUSES}

@app.get("/api/telegram/posts")
async def get_telegram_posts(
    limit: int = Query(5, ge=1, le=10),
    current_admin: dict = Depends(get_current_admin)):
    """API для получения постов из Telegram канала"""
    try:
        from app.services.telegram_service import telegram_service
        posts = await telegram_service.get_channel_posts(limit)
        return {"posts": posts}
    except Exception as e:
        logger.error(f"Error fetching Telegram posts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/import")
async def import_orders_page(request: Request, current_admin: dict = Depends(get_current_admin)):
    """Страница импорта заказов"""
    return templates.TemplateResponse("import_orders.html", {
        "request": request,
        "current_admin": current_admin,
        "current_page": "orders",
        "statuses": STATUSES
    })

@app.post("/api/orders/bulk")
async def bulk_create_orders(
    request: Request,
    current_admin: dict = Depends(get_current_admin)):
    """Массовое создание заказов"""
    try:
        data = await request.json()
        orders_data = data.get('orders', [])
        
        if not orders_data:
            raise HTTPException(400, "Нет данных для импорта")
        
        results = {
            "total": len(orders_data),
            "success": 0,
            "errors": 0,
            "duplicates": 0,
            "errorList": []
        }
        
        for order_data in orders_data:
            try:
                # Проверяем существование заказа
                existing = await OrderService.get_order(order_data['order_id'])
                if existing:
                    results["duplicates"] += 1
                    results["errorList"].append({
                        "order_id": order_data['order_id'],
                        "message": "Заказ уже существует"
                    })
                    continue
                
                # Создаем заказ
                order = Order(
                    order_id=order_data['order_id'],
                    client_name=order_data['client_name'],
                    country=order_data.get('country', 'RU').upper(),
                    status=order_data.get('status', 'В обработке'),
                    note=order_data.get('note', '')
                )
                
                success = await OrderService.add_order(order)
                if success:
                    # Добавляем участников
                    from app.utils.validators import extract_usernames
                    usernames = extract_usernames(order_data['client_name'])
                    if usernames:
                        await ParticipantService.ensure_participants(order_data['order_id'], usernames)
                    
                    # Отправляем уведомление клиенту
                    await send_order_created_notification(order, usernames)
                    
                    results["success"] += 1
                else:
                    raise Exception("Ошибка при создании заказа")
                    
            except Exception as e:
                results["errors"] += 1
                results["errorList"].append({
                    "order_id": order_data.get('order_id', 'Unknown'),
                    "message": str(e)
                })
        
        return results
        
    except Exception as e:
        logger.error(f"Error in bulk order creation: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.post("/api/orders/parse-excel")
async def parse_excel_file(
    file: UploadFile = File(...),
    current_admin: dict = Depends(get_current_admin)):
    """Парсинг Excel файла с заказами"""
    try:
        # Проверяем расширение файла
        if not file.filename.lower().endswith(('.xlsx', '.xls')):
            raise HTTPException(400, "Формат файла не поддерживается. Используйте .xlsx или .xls")
        
        # Читаем файл
        contents = await file.read()
        
        # Парсим Excel
        import pandas as pd
        df = pd.read_excel(io.BytesIO(contents))
        
        # Логируем заголовки столбцов для отладки
        logger.info(f"Excel columns: {list(df.columns)}")
        
        # Преобразуем в JSON
        orders = []
        for index, row in df.iterrows():
            # Пробуем разные варианты названий столбцов
            order_number = str(row.get('order_number', row.get('order_id', ''))).strip()
            client_name = str(row.get('client_name', row.get('client', ''))).strip()
            country = str(row.get('country', 'CN')).strip().upper()
            status = str(row.get('status', 'В обработке')).strip()
            note = str(row.get('note', '')).strip()
            
            # Если номер заказа пустой, пропускаем
            if not order_number:
                continue
                
            # Формируем полный ID заказа
            if country == 'CN':
                order_id = f"CN-{order_number}"
            elif country == 'KR':
                order_id = f"KR-{order_number}"
            else:
                order_id = f"{country}-{order_number}"
            
            # Проверяем обязательные поля
            if order_number and client_name:
                order_data = {
                    "order_id": order_id,
                    "client_name": client_name,
                    "country": country,
                    "status": status,
                    "note": note
                }
                orders.append(order_data)
                logger.info(f"Parsed order {index}: {order_data}")
        
        logger.info(f"Successfully parsed {len(orders)} orders from Excel file")
        
        return {
            "success": True,
            "orders": orders,
            "total": len(orders)
        }
        
    except Exception as e:
        logger.error(f"Error parsing Excel file: {e}")
        raise HTTPException(500, f"Ошибка при обработке файла: {str(e)}")

async def send_order_created_notification(order, usernames):
    """Отправка уведомления о создании заказа"""
    try:
        if not usernames:
            return
        
        # Получаем user_id по username
        user_ids = await AddressService.get_user_ids_by_usernames(usernames)
        
        message = f"""
📦 <b>Новый заказ создан!</b>

🆔 <b>Order ID:</b> {order.order_id}
👤 <b>Клиент:</b> {order.client_name}
🌍 <b>Страна:</b> {order.country}
📊 <b>Статус:</b> {order.status}

Следите за обновлениями статуса заказа!
"""
        
        # Отправляем сообщения через Telegram бота
        for user_id in user_ids:
            try:
                from app.webhook import application
                await application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Error sending order notification to {user_id}: {e}")
                
    except Exception as e:
        logger.error(f"Error in send_order_created_notification: {e}")

# Добавляем новые API endpoints для массовых операций
@app.post("/api/orders/bulk-update-status")
async def bulk_update_status(
    request: Request,
    current_admin: dict = Depends(get_current_admin)):
    """Массовое обновление статусов заказов"""
    try:
        data = await request.json()
        order_ids = data.get('order_ids', [])
        status = data.get('status')
        
        if not order_ids or not status:
            raise HTTPException(400, "Необходимо указать список заказов и статус")
        
        updated_count = 0
        error_count = 0
        
        for order_id in order_ids:
            try:
                success = await OrderService.update_order(order_id, {"status": status})
                if success:
                    updated_count += 1
                else:
                    error_count += 1
            except Exception as e:
                logger.error(f"Error updating order {order_id}: {e}")
                error_count += 1
        
        return {
            "success": True,
            "message": f"Обновлено {updated_count} из {len(order_ids)} заказов",
            "updated": updated_count,
            "errors": error_count
        }
        
    except Exception as e:
        logger.error(f"Error in bulk update status: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.post("/api/orders/bulk-delete")
async def bulk_delete_orders(
    request: Request,
    current_admin: dict = Depends(get_current_admin)):
    """Массовое удаление заказов"""
    try:
        data = await request.json()
        order_ids = data.get('order_ids', [])
        
        if not order_ids:
            raise HTTPException(400, "Необходимо указать список заказов")
        
        deleted_count = 0
        error_count = 0
        
        for order_id in order_ids:
            try:
                success = await OrderService.delete_order(order_id)
                if success:
                    deleted_count += 1
                else:
                    error_count += 1
            except Exception as e:
                logger.error(f"Error deleting order {order_id}: {e}")
                error_count += 1
        
        return {
            "success": True,
            "message": f"Удалено {deleted_count} из {len(order_ids)} заказов",
            "deleted": deleted_count,
            "errors": error_count
        }
        
    except Exception as e:
        logger.error(f"Error in bulk delete: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

# Новые API endpoints для адресов и отчетов
@app.get("/api/addresses")
async def get_addresses(
    search_name: Optional[str] = None,
    search_username: Optional[str] = None,
    city: Optional[str] = None,
    current_admin: dict = Depends(get_current_admin)
):
    """API для получения списка адресов"""
    try:
        addresses = await AddressService.get_all_addresses()
        
        # Фильтрация по имени
        if search_name:
            addresses = [a for a in addresses if search_name.lower() in a.get('full_name', '').lower()]
        
        # Фильтрация по username
        if search_username:
            addresses = [a for a in addresses if search_username.lower() in a.get('username', '').lower()]
        
        # Фильтрация по городу
        if city:
            addresses = [a for a in addresses if a.get('city') == city]
        
        return {"addresses": addresses}
    except Exception as e:
        logger.error(f"Error fetching addresses: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/addresses/export/xlsx")
async def export_addresses_xlsx(current_admin: dict = Depends(get_current_admin)):
    """Экспорт адресов в XLSX"""
    try:
        addresses = await AddressService.get_all_addresses()
        
        # Создаем DataFrame
        import pandas as pd
        df = pd.DataFrame(addresses)
        
        # Создаем Excel файл
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Addresses', index=False)
        
        output.seek(0)
        
        # Возвращаем файл
        filename = f"addresses_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        return Response(
            content=output.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except Exception as e:
        logger.error(f"Error exporting addresses: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/reports/analytics")
async def get_reports_analytics(current_admin: dict = Depends(get_current_admin)):
    """API для получения аналитики отчетов"""
    try:
        # Получаем все заказы
        orders = await OrderService.list_recent_orders(10000)
        
        # Базовая статистика
        total_orders = len(orders)
        completed_orders = len([o for o in orders if "доставлен" in o.status.lower() or "получен" in o.status.lower()])
        
        # Статистика по статусам
        status_stats = {}
        for order in orders:
            status = order.status
            status_stats[status] = status_stats.get(status, 0) + 1
        
        # Статистика по странам
        country_stats = {}
        for order in orders:
            country = order.country
            country_stats[country] = country_stats.get(country, 0) + 1
        
        # Статистика платежей
        all_participants = []
        for order in orders:
            participants = await ParticipantService.get_participants(order.order_id)
            all_participants.extend(participants)
        
        paid_participants = [p for p in all_participants if p.paid]
        unpaid_participants = [p for p in all_participants if not p.paid]
        
        return {
            "total_orders": total_orders,
            "completed_orders": completed_orders,
            "unique_participants": len(set(p.username for p in all_participants)),
            "status_stats": status_stats,
            "country_stats": country_stats,
            "payment_stats": {
                "total": len(all_participants),
                "paid": len(paid_participants),
                "unpaid": len(unpaid_participants)
            }
        }
    except Exception as e:
        logger.error(f"Error fetching reports analytics: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/reports/export/{report_type}")
async def export_report(
    report_type: str,
    format: str = Query("csv"),
    current_admin: dict = Depends(get_current_admin)
):
    """Экспорт отчетов"""
    try:
        if report_type == "orders":
            orders = await OrderService.list_recent_orders(10000)
            
            if format == "csv":
                # Создаем CSV
                import csv
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Заголовки
                writer.writerow(["Order ID", "Client Name", "Country", "Status", "Note", "Created At", "Updated At"])
                
                # Данные
                for order in orders:
                    writer.writerow([
                        order.order_id,
                        order.client_name,
                        order.country,
                        order.status,
                        order.note or "",
                        order.created_at.isoformat() if order.created_at else "",
                        order.updated_at.isoformat() if order.updated_at else ""
                    ])
                
                content = output.getvalue()
                filename = f"orders_{datetime.now().strftime('%Y-%m-%d')}.csv"
                
                return {
                    "content": content,
                    "filename": filename
                }
            
        elif report_type == "participants":
            if format == "csv":
                # Получаем всех участников
                result = await ParticipantService.get_participants_paginated(limit=10000)
                participants = result["participants"]
                
                # Создаем CSV
                import csv
                output = io.StringIO()
                writer = csv.writer(output)
                
                # Заголовки
                writer.writerow(["Order ID", "Username", "Paid", "Created At", "Updated At"])
                
                # Данные
                for participant in participants:
                    writer.writerow([
                        participant.order_id,
                        participant.username,
                        "Да" if participant.paid else "Нет",
                        participant.created_at.isoformat() if participant.created_at else "",
                        participant.updated_at.isoformat() if participant.updated_at else ""
                    ])
                
                content = output.getvalue()
                filename = f"participants_{datetime.now().strftime('%Y-%m-%d')}.csv"
                
                return {
                    "content": content,
                    "filename": filename
                }
            
            elif format == "json":
                # Получаем все данные
                orders = await OrderService.list_recent_orders(10000)
                all_participants = []
                
                for order in orders:
                    participants = await ParticipantService.get_participants(order.order_id)
                    all_participants.extend(participants)
                
                # Сериализуем данные
                data = {
                    "orders": [serialize_model(order) for order in orders],
                    "participants": [serialize_model(participant) for participant in all_participants]
                }
                
                filename = f"data_{datetime.now().strftime('%Y-%m-%d')}.json"
                
                return {
                    "content": json.dumps(data, indent=2, ensure_ascii=False, default=str),
                    "filename": filename
                }
        
        raise HTTPException(400, "Неверный тип отчета или формат")
        
    except Exception as e:
        logger.error(f"Error exporting report {report_type}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
