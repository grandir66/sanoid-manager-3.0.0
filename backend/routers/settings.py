"""
Router per gestione impostazioni di sistema
Con autenticazione e configurazione avanzata
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, field_validator
from datetime import datetime

from database import (
    get_db, Settings, SystemConfig, NotificationConfig, User,
    get_config_value, set_config_value, init_default_config
)
from routers.auth import get_current_user, require_admin, log_audit

router = APIRouter()


# ============== Schemas ==============

class SettingUpdate(BaseModel):
    value: str


class SystemConfigUpdate(BaseModel):
    value: str
    description: Optional[str] = None


class SystemConfigResponse(BaseModel):
    key: str
    value: Optional[str]
    value_type: str
    category: str
    description: Optional[str]
    is_secret: bool
    
    class Config:
        from_attributes = True


class NotificationConfigUpdate(BaseModel):
    # SMTP
    smtp_enabled: Optional[bool] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    smtp_to: Optional[str] = None  # Destinatari separati da virgola
    smtp_subject_prefix: Optional[str] = None  # Prefisso soggetto
    smtp_tls: Optional[bool] = None
    
    # Webhook
    webhook_enabled: Optional[bool] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    
    # Telegram
    telegram_enabled: Optional[bool] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    
    # Triggers
    notify_on_success: Optional[bool] = None
    notify_on_failure: Optional[bool] = None
    notify_on_warning: Optional[bool] = None
    
    @field_validator('smtp_port', mode='before')
    @classmethod
    def convert_port_to_int(cls, v):
        if v is None or v == '':
            return None
        return int(v)


class NotificationConfigResponse(BaseModel):
    id: int
    smtp_enabled: bool
    smtp_host: Optional[str]
    smtp_port: int
    smtp_user: Optional[str]
    smtp_from: Optional[str]
    smtp_to: Optional[str]
    smtp_subject_prefix: Optional[str]
    smtp_tls: bool
    
    webhook_enabled: bool
    webhook_url: Optional[str]
    
    telegram_enabled: bool
    telegram_chat_id: Optional[str]
    
    notify_on_success: bool
    notify_on_failure: bool
    notify_on_warning: bool
    
    class Config:
        from_attributes = True


class AuthConfigUpdate(BaseModel):
    auth_method: str  # local, proxmox
    auth_proxmox_node: Optional[str] = None
    auth_proxmox_port: Optional[int] = 8006
    auth_proxmox_verify_ssl: Optional[bool] = False
    auth_session_timeout: Optional[int] = 480
    auth_allow_local_fallback: Optional[bool] = True


# ============== Legacy Endpoints (compatibilità) ==============

@router.get("/")
async def list_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Lista tutte le impostazioni (legacy)"""
    settings = db.query(Settings).all()
    return {s.key: {"value": s.value, "description": s.description} for s in settings}


@router.get("/legacy/{key}")
async def get_setting(
    key: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene un'impostazione specifica (legacy)"""
    setting = db.query(Settings).filter(Settings.key == key).first()
    if not setting:
        raise HTTPException(status_code=404, detail="Impostazione non trovata")
    return {"key": setting.key, "value": setting.value, "description": setting.description}


@router.put("/legacy/{key}")
async def update_setting(
    key: str,
    update: SettingUpdate,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Aggiorna un'impostazione (legacy)"""
    setting = db.query(Settings).filter(Settings.key == key).first()
    
    if setting:
        setting.value = update.value
    else:
        setting = Settings(key=key, value=update.value)
        db.add(setting)
    
    log_audit(
        db, user.id, "setting_updated", "settings",
        details=f"Updated setting: {key}",
        ip_address=request.client.host if request.client else None
    )
    
    db.commit()
    return {"key": key, "value": update.value}


# ============== System Config Endpoints ==============

@router.get("/system/all", response_model=Dict[str, Any])
async def get_all_system_config(
    category: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene tutte le configurazioni di sistema"""
    init_default_config(db)
    
    query = db.query(SystemConfig)
    if category:
        query = query.filter(SystemConfig.category == category)
    
    configs = query.all()
    
    result = {}
    for config in configs:
        # Non mostrare valori segreti a non-admin
        if config.is_secret and user.role != "admin":
            value = "********"
        else:
            value = config.value
        
        if config.category not in result:
            result[config.category] = {}
        
        result[config.category][config.key] = {
            "value": value,
            "type": config.value_type,
            "description": config.description
        }
    
    return result


@router.get("/system/{key}")
async def get_system_config(
    key: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene una configurazione di sistema specifica"""
    config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if not config:
        raise HTTPException(status_code=404, detail="Configurazione non trovata")
    
    if config.is_secret and user.role != "admin":
        raise HTTPException(status_code=403, detail="Accesso negato")
    
    return SystemConfigResponse.model_validate(config)


@router.put("/system/{key}")
async def update_system_config(
    key: str,
    update: SystemConfigUpdate,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Aggiorna una configurazione di sistema"""
    config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    
    if config:
        config.value = update.value
        if update.description:
            config.description = update.description
        config.updated_at = datetime.utcnow()
    else:
        config = SystemConfig(
                key=key,
            value=update.value,
            description=update.description
        )
        db.add(config)
    
    log_audit(
        db, user.id, "system_config_updated", "settings",
        details=f"Updated system config: {key}",
        ip_address=request.client.host if request.client else None
    )
    
    db.commit()
    return {"key": key, "value": update.value}


# ============== Auth Config Endpoints ==============

@router.get("/auth/config")
async def get_auth_settings(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Ottiene le impostazioni di autenticazione"""
    init_default_config(db)
    
    return {
        "auth_method": get_config_value(db, "auth_method", "proxmox"),
        "auth_proxmox_node": get_config_value(db, "auth_proxmox_node", ""),
        "auth_proxmox_port": get_config_value(db, "auth_proxmox_port", 8006),
        "auth_proxmox_verify_ssl": get_config_value(db, "auth_proxmox_verify_ssl", False),
        "auth_session_timeout": get_config_value(db, "auth_session_timeout", 480),
        "auth_allow_local_fallback": get_config_value(db, "auth_allow_local_fallback", True)
    }


@router.put("/auth/config")
async def update_auth_settings(
    config: AuthConfigUpdate,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Aggiorna le impostazioni di autenticazione"""
    
    set_config_value(db, "auth_method", config.auth_method)
    
    if config.auth_proxmox_node is not None:
        set_config_value(db, "auth_proxmox_node", config.auth_proxmox_node)
    if config.auth_proxmox_port is not None:
        set_config_value(db, "auth_proxmox_port", config.auth_proxmox_port, "int")
    if config.auth_proxmox_verify_ssl is not None:
        set_config_value(db, "auth_proxmox_verify_ssl", config.auth_proxmox_verify_ssl, "bool")
    if config.auth_session_timeout is not None:
        set_config_value(db, "auth_session_timeout", config.auth_session_timeout, "int")
    if config.auth_allow_local_fallback is not None:
        set_config_value(db, "auth_allow_local_fallback", config.auth_allow_local_fallback, "bool")
    
    log_audit(
        db, user.id, "auth_config_updated", "settings",
        details=f"Auth method: {config.auth_method}",
        ip_address=request.client.host if request.client else None
    )
    
    return {"message": "Configurazione autenticazione aggiornata"}


# ============== Notification Config Endpoints ==============

@router.get("/notifications", response_model=NotificationConfigResponse)
async def get_notification_config(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Ottiene la configurazione delle notifiche"""
    config = db.query(NotificationConfig).first()
    if not config:
        config = NotificationConfig()
        db.add(config)
        db.commit()
        db.refresh(config)
    
    return config


@router.put("/notifications")
async def update_notification_config(
    update: NotificationConfigUpdate,
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Aggiorna la configurazione delle notifiche"""
    config = db.query(NotificationConfig).first()
    if not config:
        config = NotificationConfig()
        db.add(config)
    
    for key, value in update.dict(exclude_unset=True).items():
        setattr(config, key, value)
    
    config.updated_at = datetime.utcnow()
    
    log_audit(
        db, user.id, "notification_config_updated", "settings",
        ip_address=request.client.host if request.client else None
    )
    
    db.commit()
    return {"message": "Configurazione notifiche aggiornata"}


@router.post("/notifications/test")
async def test_notification(
    channel: str,  # email, webhook, telegram
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Invia una notifica di test"""
    config = db.query(NotificationConfig).first()
    if not config:
        raise HTTPException(status_code=400, detail="Notifiche non configurate")
    
    if channel == "email":
        if not config.smtp_enabled:
            raise HTTPException(status_code=400, detail="Email non abilitata")
        if not config.smtp_host:
            raise HTTPException(status_code=400, detail="Server SMTP non configurato")
        if not config.smtp_to:
            raise HTTPException(status_code=400, detail="Destinatario email non configurato")
        
        from services.email_service import email_service
        
        # Configura il servizio email
        email_service.configure(
            host=config.smtp_host,
            port=config.smtp_port or 587,
            user=config.smtp_user,
            password=config.smtp_password,
            from_addr=config.smtp_from,
            to_addrs=config.smtp_to,
            subject_prefix=config.smtp_subject_prefix or "[Sanoid Manager]",
            use_tls=config.smtp_tls
        )
        
        # Invia email di test
        success, message = email_service.send_test_email()
        
        if success:
            return {"success": True, "message": f"Email di test inviata a {config.smtp_to}"}
        else:
            raise HTTPException(status_code=500, detail=f"Errore invio email: {message}")
    
    elif channel == "webhook":
        if not config.webhook_enabled:
            raise HTTPException(status_code=400, detail="Webhook non abilitato")
        if not config.webhook_url:
            raise HTTPException(status_code=400, detail="URL Webhook non configurato")
        
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    config.webhook_url,
                    json={
                        "type": "test",
                        "message": "Test notifica da Sanoid Manager",
                        "timestamp": datetime.utcnow().isoformat()
                    },
                    headers={"X-Webhook-Secret": config.webhook_secret} if config.webhook_secret else {},
                    timeout=10
                )
                if response.status_code < 300:
                    return {"success": True, "message": f"Webhook inviato con successo (status: {response.status_code})"}
                else:
                    raise HTTPException(status_code=500, detail=f"Webhook fallito: HTTP {response.status_code}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Errore webhook: {str(e)}")
    
    elif channel == "telegram":
        if not config.telegram_enabled:
            raise HTTPException(status_code=400, detail="Telegram non abilitato")
        if not config.telegram_bot_token or not config.telegram_chat_id:
            raise HTTPException(status_code=400, detail="Token o Chat ID Telegram non configurati")
        
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": config.telegram_chat_id,
                        "text": "🧪 *Test Sanoid Manager*\n\nSe ricevi questo messaggio, Telegram è configurato correttamente!",
                        "parse_mode": "Markdown"
                    },
                    timeout=10
                )
                result = response.json()
                if result.get("ok"):
                    return {"success": True, "message": "Messaggio Telegram inviato con successo"}
                else:
                    raise HTTPException(status_code=500, detail=f"Errore Telegram: {result.get('description', 'Unknown')}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Errore Telegram: {str(e)}")
    
    raise HTTPException(status_code=400, detail="Canale non valido")


# ============== Categories ==============

@router.get("/categories")
async def get_config_categories(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene le categorie di configurazione disponibili"""
    return {
        "categories": [
            {"id": "auth", "name": "Autenticazione", "icon": "🔐"},
            {"id": "syncoid", "name": "Syncoid", "icon": "🔄"},
            {"id": "retention", "name": "Retention", "icon": "📦"},
            {"id": "notifications", "name": "Notifiche", "icon": "🔔"},
            {"id": "ui", "name": "Interfaccia", "icon": "🎨"},
            {"id": "general", "name": "Generale", "icon": "⚙️"}
        ]
    }
