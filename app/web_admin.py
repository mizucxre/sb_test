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
        
        return {"message": "Status updated successfully"}
    except Exception as e:
        logger.error(f"Error updating order status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.delete("/api/orders/{order_id}")
async def delete_order(order_id: str, username: str = Depends(authenticate_admin)):
    """API для удаления заказа"""
    try:
        success = await OrderService.delete_order(order_id)
        if not success:
            raise HTTPException(status_code=404, detail="Order not found")
        
        return {"message": "Order deleted successfully"}
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
        success = await ParticipantService.toggle_participant_paid(order_id, username)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to update payment status")
        
        return {"message": "Payment status updated successfully"}
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
        from app.webhook import application
        
        sent_count = 0
        failed_count = 0
        
        if order_id:
            # Рассылка неплательщикам конкретного заказа
            usernames = await ParticipantService.get_unpaid_usernames(order_id)
        else:
            # Рассылка всем неплательщикам
            grouped = await ParticipantService.get_all_unpaid_grouped()
            usernames = []
            for order_usernames in grouped.values():
                usernames.extend(order_usernames)
        
        for username in usernames:
            try:
                user_ids = await AddressService.get_user_ids_by_usernames([username])
                if user_ids:
                    await application.bot.send_message(
                        chat_id=user_ids[0],
                        text=message,
                        parse_mode="Markdown"
                    )
                    sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send to {username}: {e}")
                failed_count += 1
        
        return {
            "message": f"Broadcast sent: {sent_count} successful, {failed_count} failed",
            "sent_to": sent_count
        }
    except Exception as e:
        logger.error(f"Error sending broadcast: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

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
