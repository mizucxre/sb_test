import logging
from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from typing import List, Optional

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

@app.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, username: str = Depends(authenticate_admin)):
    """Главная страница админки"""
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "username": username
    })

@app.get("/api/orders")
async def get_orders(
    status: Optional[str] = None,
    limit: int = 50,
    username: str = Depends(authenticate_admin)
):
    """API для получения списка заказов"""
    try:
        if status:
            orders = await OrderService.list_orders_by_status([status])
        else:
            orders = await OrderService.list_recent_orders(limit)
        return {"orders": [dict(order) for order in orders]}
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
        
        return {"message": "Order created successfully"}
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

@app.post("/api/orders/batch-status")
async def batch_update_status(
    order_ids: List[str] = Form(...),
    status: str = Form(...),
    username: str = Depends(authenticate_admin)
):
    """API для массового обновления статусов"""
    try:
        results = []
        for order_id in order_ids:
            try:
                success = await OrderService.update_order_status(order_id, status)
                results.append({
                    "order_id": order_id,
                    "success": success,
                    "message": "Updated" if success else "Not found"
                })
                
                if success:
                    from app.webhook import application
                    await notify_subscribers(application, order_id, status)
                    
            except Exception as e:
                results.append({
                    "order_id": order_id,
                    "success": False,
                    "message": str(e)
                })
        
        return {"results": results}
    except Exception as e:
        logger.error(f"Error in batch status update: {e}")
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
