import logging
from fastapi import FastAPI, Depends, HTTPException, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from typing import List, Optional
from datetime import datetime, timedelta
import json

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
        "username": username,
        "current_page": "dashboard"
    })

@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("orders.html", {
        "request": request,
        "username": username,
        "current_page": "orders",
        "statuses": STATUSES
    })

@app.get("/orders/new", response_class=HTMLResponse)
async def new_order_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("order_form.html", {
        "request": request,
        "username": username,
        "statuses": STATUSES,
        "current_page": "orders"
    })

@app.get("/participants", response_class=HTMLResponse)
async def participants_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("participants.html", {
        "request": request,
        "username": username,
        "current_page": "participants"
    })

@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("reports.html", {
        "request": request,
        "username": username,
        "current_page": "reports"
    })

@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("broadcast.html", {
        "request": request,
        "username": username,
        "current_page": "broadcast"
    })

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, username: str = Depends(authenticate_admin)):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "username": username,
        "current_page": "settings"
    })

# API endpoints
@app.get("/api/stats")
async def get_stats(username: str = Depends(authenticate_admin)):
    """Получение статистики для дашборда"""
    try:
        # Получаем все заказы для статистики
        orders = await OrderService.list_recent_orders(1000)
        total_orders = len(orders)
        
        # Активные заказы (исключаем завершенные)
        active_statuses = [s for s in STATUSES if "получен" not in s.lower() and "доставлен" not in s.lower()]
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

def serialize_model(model):
    """Сериализация Pydantic модели в словарь"""
    if hasattr(model, 'model_dump'):
        return model.model_dump()
    else:
        return model.dict()

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
        
        # Convert orders to dict for JSON serialization
        orders_data = []
        for order in paginated_orders:
            order_data = serialize_model(order)
            # Ensure datetime fields are serializable
            if order_data.get('created_at'):
                order_data['created_at'] = order_data['created_at'].isoformat()
            if order_data.get('updated_at'):
                order_data['updated_at'] = order_data['updated_at'].isoformat()
            orders_data.append(order_data)
        
        return {
            "orders": orders_data,
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
        
        # Convert to dict for JSON serialization
        order_data = serialize_model(order)
        if order_data.get('created_at'):
            order_data['created_at'] = order_data['created_at'].isoformat()
        if order_data.get('updated_at'):
            order_data['updated_at'] = order_data['updated_at'].isoformat()
        
        participants_data = []
        for participant in participants:
            participant_data = serialize_model(participant)
            if participant_data.get('created_at'):
                participant_data['created_at'] = participant_data['created_at'].isoformat()
            if participant_data.get('updated_at'):
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
    username: str = Depends(authenticate_admin)
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
    username: str = Depends(authenticate_admin)
):
    """Обновление заказа"""
    try:
        order = await OrderService.get_order(order_id)
        if not order:
            raise HTTPException(404, "Заказ не найден")
        
        data = await request.json()
        
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
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    username: str = Depends(authenticate_admin)
):
    """API для получения списка участников"""
    try:
        if order_id:
            participants = await ParticipantService.get_participants(order_id)
        else:
            # Получаем всех участников из всех заказов
            all_participants = []
            orders = await OrderService.list_recent_orders(1000)
            for order in orders:
                participants = await ParticipantService.get_participants(order.order_id)
                all_participants.extend(participants)
            participants = all_participants
        
        # Фильтрация по статусу оплаты
        if paid is not None:
            participants = [p for p in participants if p.paid == paid]
        
        # Пагинация
        paginated_participants = participants[offset:offset + limit]
        
        # Convert to dict for JSON serialization
        participants_data = []
        for participant in paginated_participants:
            participant_data = serialize_model(participant)
            if participant_data.get('created_at'):
                participant_data['created_at'] = participant_data['created_at'].isoformat()
            if participant_data.get('updated_at'):
                participant_data['updated_at'] = participant_data['updated_at'].isoformat()
            participants_data.append(participant_data)
        
        return {
            "participants": participants_data,
            "total": len(participants),
            "has_more": len(participants) > offset + limit
        }
    except Exception as e:
        logger.error(f"Error fetching participants: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.put("/api/participants/{order_id}/{username}/paid")
async def update_participant_paid(
    order_id: str,
    username: str,
    request: Request,
    username_auth: str = Depends(authenticate_admin)
):
    """Изменение статуса оплаты участника"""
    try:
        data = await request.json()
        paid = data.get('paid', False)
        
        success = await ParticipantService.toggle_participant_paid(order_id, username)
        if not success:
            raise HTTPException(400, "Не удалось обновить статус оплаты")
        
        return {"success": True, "message": "Статус оплаты обновлен"}
        
    except Exception as e:
        logger.error(f"Error updating participant payment: {e}")
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.get("/api/statuses")
async def get_statuses(username: str = Depends(authenticate_admin)):
    """API для получения списка статусов"""
    return {"statuses": STATUSES}

from fastapi.responses import StreamingResponse
import io
import csv

@app.get("/api/reports/orders/csv")
async def export_orders_csv(
    status: Optional[str] = None,
    country: Optional[str] = None,
    username: str = Depends(authenticate_admin)
):
    """Экспорт заказов в CSV"""
    try:
        if status:
            orders = await OrderService.list_orders_by_status([status])
        else:
            orders = await OrderService.list_recent_orders(1000)
        
        if country:
            orders = [o for o in orders if o.country == country.upper()]
        
        # Создаем CSV в памяти
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Заголовки
        writer.writerow([
            'Order ID', 'Client Name', 'Status', 'Country', 
            'Note', 'Updated At', 'Participants Count'
        ])
        
        # Данные
        for order in orders:
            participants = await ParticipantService.get_participants(order.order_id)
            writer.writerow([
                order.order_id,
                order.client_name,
                order.status,
                order.country,
                order.note or '',
                order.updated_at or '',
                len(participants)
            ])
        
        output.seek(0)
        
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode('utf-8')),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=orders_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            }
        )
        
    except Exception as e:
        logger.error(f"Error exporting CSV: {e}")
        raise HTTPException(500, "Ошибка при экспорте")

@app.get("/api/reports/participants/unpaid")
async def export_unpaid_participants(username: str = Depends(authenticate_admin)):
    """Отчет по неплательщикам"""
    try:
        grouped = await ParticipantService.get_all_unpaid_grouped()
        
        report_data = []
        for order_id, usernames in grouped.items():
            order = await OrderService.get_order(order_id)
            report_data.append({
                "order_id": order_id,
                "order_status": order.status if order else "Не найден",
                "unpaid_count": len(usernames),
                "usernames": ", ".join([f"@{u}" for u in usernames])
            })
        
        return {"unpaid_report": report_data}
        
    except Exception as e:
        logger.error(f"Error generating unpaid report: {e}")
        raise HTTPException(500, "Ошибка при генерации отчета")
