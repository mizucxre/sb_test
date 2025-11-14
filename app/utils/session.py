from fastapi import Depends, HTTPException, Request
from app.utils.security import verify_token
from app.services.admin_service import AdminService

async def get_current_admin(request: Request):
    """Получение текущего администратора из токена"""
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    username = payload.get("sub")
    user_id = payload.get("user_id")
    role = payload.get("role")
    
    if not username or not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    
    # Получаем актуальные данные пользователя из базы
    admin_user = await AdminService.get_user_by_id(user_id)
    if not admin_user or not admin_user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    
    return {
        "user_id": user_id,
        "username": username,
        "role": role,
        "avatar_url": admin_user.avatar_url
    }

def require_permission(required_role: str = None):
    """Декоратор для проверки прав доступа"""
    def role_checker(current_admin: dict = Depends(get_current_admin)):
        if not current_admin:
            raise HTTPException(status_code=401, detail="Not authenticated")
        
        if required_role and current_admin.get("role") != required_role:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        return current_admin
    return role_checker

# Исправленная функция - возвращаем dependency, а не используем как декоратор напрямую
def require_super_admin():
    """Фабрика зависимости для проверки супер-админа"""
    def super_admin_checker(current_admin: dict = Depends(get_current_admin)):
        if current_admin.get("role") != "super_admin":
            raise HTTPException(status_code=403, detail="Super admin access required")
        return current_admin
    return super_admin_checker
