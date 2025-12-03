"""
Router per autenticazione e gestione utenti
Supporta autenticazione locale e integrata Proxmox VE
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, timedelta
from pydantic import BaseModel, EmailStr, validator
import logging

from database import (
    get_db, User, UserSession, AuditLog, SystemConfig, Node,
    get_config_value, init_default_config
)
from services.auth_service import auth_service, ACCESS_TOKEN_EXPIRE_MINUTES
from services.proxmox_auth_service import proxmox_auth_service, ProxmoxUser

logger = logging.getLogger(__name__)
router = APIRouter()
security = HTTPBearer(auto_error=False)


# ============== Schemas ==============

class UserCreate(BaseModel):
    username: str
    email: Optional[EmailStr] = None
    password: Optional[str] = None  # Opzionale per utenti Proxmox
    full_name: Optional[str] = None
    role: str = "viewer"
    auth_method: str = "local"
    proxmox_userid: Optional[str] = None
    
    @validator('username')
    def username_valid(cls, v):
        if len(v) < 3:
            raise ValueError('Username deve essere di almeno 3 caratteri')
        return v.lower()
    
    @validator('role')
    def role_valid(cls, v):
        valid_roles = ['admin', 'operator', 'viewer']
        if v not in valid_roles:
            raise ValueError(f'Ruolo deve essere uno di: {", ".join(valid_roles)}')
        return v


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    allowed_nodes: Optional[List[int]] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class LoginRequest(BaseModel):
    username: str
    password: str
    realm: Optional[str] = None  # Per Proxmox: pam, pve, ldap, ad


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class UserResponse(BaseModel):
    id: int
    username: str
    email: Optional[str]
    full_name: Optional[str]
    role: str
    auth_method: str
    proxmox_userid: Optional[str]
    is_active: bool
    last_login: Optional[datetime]
    created_at: datetime
    allowed_nodes: Optional[List[int]]
    
    class Config:
        from_attributes = True


class AuthConfigResponse(BaseModel):
    auth_method: str
    proxmox_node: Optional[str]
    realms: List[dict]
    allow_local_fallback: bool


# ============== Dipendenze ==============

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Dipendenza per ottenere l'utente corrente dal token"""
    
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token di autenticazione mancante",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    success, payload = auth_service.verify_token(token)
    
    if not success or not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token non valido o scaduto",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tipo di token non valido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token malformato",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utente non trovato",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabilitato"
        )
    
    return user


async def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Dipendenza opzionale per l'utente corrente"""
    if not credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


def require_role(allowed_roles: List[str]):
    """Factory per dipendenza che richiede ruoli specifici"""
    async def role_checker(user: User = Depends(get_current_user)):
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Richiesto ruolo: {', '.join(allowed_roles)}"
            )
        return user
    return role_checker


require_admin = require_role(["admin"])
require_operator = require_role(["admin", "operator"])


# ============== Helper Functions ==============

def log_audit(
    db: Session,
    user_id: Optional[int],
    action: str,
    resource_type: str,
    resource_id: Optional[int] = None,
    details: Optional[str] = None,
    ip_address: Optional[str] = None,
    status: str = "success"
):
    """Registra un'azione nel log di audit"""
    audit = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        ip_address=ip_address,
        status=status
    )
    db.add(audit)
    db.commit()


async def get_auth_node(db: Session) -> Optional[Node]:
    """Ottiene il nodo configurato per l'autenticazione Proxmox"""
    # Prima cerca nodo marcato come auth_node
    node = db.query(Node).filter(Node.is_auth_node == True, Node.is_active == True).first()
    if node:
        return node
    
    # Altrimenti usa il primo nodo online
    node = db.query(Node).filter(Node.is_online == True, Node.is_active == True).first()
    return node


def create_user_from_proxmox(
    db: Session,
    proxmox_user: ProxmoxUser,
    role: str = None
) -> User:
    """Crea o aggiorna un utente dal login Proxmox"""
    
    # Cerca utente esistente
    user = db.query(User).filter(User.proxmox_userid == proxmox_user.userid).first()
    
    if not user:
        # Cerca per username
        user = db.query(User).filter(User.username == proxmox_user.username).first()
    
    # Determina ruolo
    if role is None:
        if proxmox_user.is_admin:
            role = "admin"
        else:
            role = "operator"  # Default per utenti Proxmox
    
    if user:
        # Aggiorna dati esistenti
        user.proxmox_userid = proxmox_user.userid
        user.proxmox_realm = proxmox_user.realm
        user.email = proxmox_user.email or user.email
        user.full_name = proxmox_user.full_name or user.full_name
        user.auth_method = "proxmox"
        user.last_login = datetime.utcnow()
    else:
        # Crea nuovo utente
        user = User(
            username=proxmox_user.username,
            email=proxmox_user.email,
            full_name=proxmox_user.full_name,
            auth_method="proxmox",
            proxmox_userid=proxmox_user.userid,
            proxmox_realm=proxmox_user.realm,
            role=role,
            last_login=datetime.utcnow()
        )
        db.add(user)
    
    db.commit()
    db.refresh(user)
    return user


# ============== Endpoints ==============

@router.get("/config", response_model=AuthConfigResponse)
async def get_auth_config(db: Session = Depends(get_db)):
    """
    Ottiene la configurazione di autenticazione.
    Non richiede autenticazione - usato dalla pagina di login.
    """
    init_default_config(db)
    
    auth_method = get_config_value(db, "auth_method", "proxmox")
    allow_local = get_config_value(db, "auth_allow_local_fallback", True)
    
    realms = []
    proxmox_node = None
    
    if auth_method == "proxmox":
        node = await get_auth_node(db)
        if node:
            proxmox_node = node.hostname
            realms = await proxmox_auth_service.get_available_realms(
                api_host=node.hostname,
                port=8006,
                verify_ssl=node.proxmox_verify_ssl
            )
    
    return AuthConfigResponse(
        auth_method=auth_method,
        proxmox_node=proxmox_node,
        realms=realms,
        allow_local_fallback=allow_local
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    login_data: LoginRequest,
    db: Session = Depends(get_db)
):
    """
    Effettua il login.
    Supporta autenticazione locale e Proxmox.
    """
    init_default_config(db)
    
    auth_method = get_config_value(db, "auth_method", "proxmox")
    allow_local = get_config_value(db, "auth_allow_local_fallback", True)
    client_ip = request.client.host if request.client else None
    
    user = None
    proxmox_ticket = None
    proxmox_csrf = None
    
    # ===== AUTENTICAZIONE PROXMOX =====
    if auth_method == "proxmox":
        node = await get_auth_node(db)
        
        if node:
            realm = login_data.realm or "pam"
            
            success, proxmox_user, error = await proxmox_auth_service.authenticate(
                api_host=node.hostname,
                username=login_data.username,
                password=login_data.password,
                realm=realm,
                port=8006,
                verify_ssl=node.proxmox_verify_ssl
            )
            
            if success and proxmox_user:
                # Crea/aggiorna utente locale
                user = create_user_from_proxmox(db, proxmox_user)
                
                # Salva ticket Proxmox per operazioni future
                cached_ticket = proxmox_auth_service.get_cached_ticket(proxmox_user.userid)
                if cached_ticket:
                    proxmox_ticket = cached_ticket.ticket
                    proxmox_csrf = cached_ticket.csrf_token
                
                logger.info(f"Proxmox login successful: {proxmox_user.userid}")
            else:
                logger.warning(f"Proxmox login failed for {login_data.username}: {error}")
                
                # Fallback a login locale se abilitato
                if not allow_local:
                    log_audit(db, None, "login_failed", "auth",
                              details=f"Proxmox auth failed: {login_data.username}@{realm}",
                              ip_address=client_ip, status="failed")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=error or "Autenticazione Proxmox fallita"
                    )
        elif not allow_local:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Nessun nodo Proxmox disponibile per autenticazione"
            )
    
    # ===== AUTENTICAZIONE LOCALE (fallback o primaria) =====
    if not user:
        user = db.query(User).filter(User.username == login_data.username.lower()).first()
        
        if not user:
            log_audit(db, None, "login_failed", "auth",
                      details=f"User not found: {login_data.username}",
                      ip_address=client_ip, status="failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Username o password non corretti"
            )
        
        # Verifica password per utenti locali
        if user.auth_method == "local":
            if not user.password_hash or not auth_service.verify_password(
                login_data.password, user.password_hash
            ):
                log_audit(db, user.id, "login_failed", "auth",
                          details="Invalid password",
                          ip_address=client_ip, status="failed")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Username o password non corretti"
                )
        else:
            # Utente Proxmox ma auth Proxmox fallita
            log_audit(db, user.id, "login_failed", "auth",
                      details="Proxmox user requires Proxmox auth",
                      ip_address=client_ip, status="failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Questo utente richiede autenticazione Proxmox"
            )
    
    # ===== VERIFICA STATO UTENTE =====
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabilitato"
        )
    
    # ===== GENERA TOKEN =====
    access_token = auth_service.create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
            "auth_method": user.auth_method
        }
    )
    refresh_token = auth_service.create_refresh_token(
        data={"sub": str(user.id)}
    )
    
    # Aggiorna ultimo login
    user.last_login = datetime.utcnow()
    
    # Crea sessione
    session = UserSession(
        user_id=user.id,
        token_hash=auth_service.get_password_hash(access_token[:32]),
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
        expires_at=datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        proxmox_ticket=proxmox_ticket,
        proxmox_csrf=proxmox_csrf
    )
    db.add(session)
    
    log_audit(db, user.id, "login_success", "auth",
              details=f"Method: {user.auth_method}",
              ip_address=client_ip)
    
    db.commit()
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "auth_method": user.auth_method,
            "must_change_password": user.must_change_password
        }
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """Rinnova l'access token usando il refresh token"""
    
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token mancante"
        )
    
    token = credentials.credentials
    success, payload = auth_service.verify_token(token)
    
    if not success or not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token non valido"
        )
    
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == int(user_id)).first()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utente non valido"
        )
    
    access_token = auth_service.create_access_token(
        data={
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
            "auth_method": user.auth_method
        }
    )
    refresh_token = auth_service.create_refresh_token(data={"sub": str(user.id)})
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user={
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
            "role": user.role,
            "auth_method": user.auth_method,
            "must_change_password": user.must_change_password
        }
    )


@router.post("/logout")
async def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Effettua il logout"""
    
    # Invalida sessioni
    db.query(UserSession).filter(
        UserSession.user_id == user.id,
        UserSession.is_active == True
    ).update({"is_active": False})
    
    # Clear Proxmox ticket cache
    if user.proxmox_userid:
        proxmox_auth_service.clear_cache(user.proxmox_userid)
    
    log_audit(db, user.id, "logout", "auth",
              ip_address=request.client.host if request.client else None)
    
    db.commit()
    
    return {"message": "Logout effettuato"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(user: User = Depends(get_current_user)):
    """Ottiene le informazioni dell'utente corrente"""
    return user


@router.put("/me/password")
async def change_password(
    password_data: PasswordChange,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Cambia la password dell'utente corrente (solo utenti locali)"""
    
    if user.auth_method != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cambia la password in Proxmox per questo account"
        )
    
    if not auth_service.verify_password(password_data.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password attuale non corretta"
        )
    
    is_valid, message = auth_service.validate_password_strength(password_data.new_password)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    
    user.password_hash = auth_service.get_password_hash(password_data.new_password)
    user.must_change_password = False
    db.commit()
    
    return {"message": "Password aggiornata con successo"}


# ============== Admin Endpoints ==============

@router.get("/users", response_model=List[UserResponse])
async def list_users(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Lista tutti gli utenti (solo admin)"""
    return db.query(User).all()


@router.post("/users", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Crea un nuovo utente (solo admin)"""
    
    existing = db.query(User).filter(User.username == user_data.username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username già in uso")
    
    password_hash = None
    if user_data.auth_method == "local":
        if not user_data.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password richiesta per utenti locali"
            )
        is_valid, message = auth_service.validate_password_strength(user_data.password)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
        password_hash = auth_service.get_password_hash(user_data.password)
    
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=password_hash,
        full_name=user_data.full_name,
        role=user_data.role,
        auth_method=user_data.auth_method,
        proxmox_userid=user_data.proxmox_userid
    )
    
    db.add(new_user)
    log_audit(db, admin.id, "user_created", "user",
              details=f"Created: {new_user.username}",
              ip_address=request.client.host if request.client else None)
    db.commit()
    db.refresh(new_user)
    
    return new_user


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Aggiorna un utente (solo admin)"""
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    
    if user_id == admin.id and user_data.is_active == False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Non puoi disabilitare il tuo account"
        )
    
    for key, value in user_data.dict(exclude_unset=True).items():
        setattr(target_user, key, value)
    
    log_audit(db, admin.id, "user_updated", "user",
              resource_id=user_id,
              details=f"Updated: {target_user.username}",
              ip_address=request.client.host if request.client else None)
    db.commit()
    db.refresh(target_user)
    
    return target_user


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Elimina un utente (solo admin)"""
    
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Non puoi eliminare il tuo account"
        )
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    
    username = target_user.username
    db.delete(target_user)
    log_audit(db, admin.id, "user_deleted", "user",
              resource_id=user_id,
              details=f"Deleted: {username}",
              ip_address=request.client.host if request.client else None)
    db.commit()
    
    return {"message": "Utente eliminato"}


@router.get("/audit-log")
async def get_audit_log(
    limit: int = 100,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Ottiene il log di audit (solo admin)"""
    
    query = db.query(AuditLog)
    
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    if action:
        query = query.filter(AuditLog.action == action)
    
    logs = query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return logs


@router.post("/setup")
async def initial_setup(
    user_data: UserCreate,
    db: Session = Depends(get_db)
):
    """
    Setup iniziale - crea il primo utente admin.
    Funziona solo se non esistono utenti nel sistema.
    """
    
    try:
        existing_users = db.query(User).count()
        if existing_users > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Setup già completato"
            )
        
        init_default_config(db)
        
        # Per il setup iniziale, forza autenticazione locale
        if not user_data.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password richiesta per il setup iniziale"
            )
        
        is_valid, message = auth_service.validate_password_strength(user_data.password)
        if not is_valid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
        
        admin_user = User(
            username=user_data.username.lower(),
            email=user_data.email,
            password_hash=auth_service.get_password_hash(user_data.password),
            full_name=user_data.full_name or "Administrator",
            role="admin",
            auth_method="local"  # Primo admin sempre locale
        )
        
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
        
        logger.info(f"Setup completato - Admin creato: {admin_user.username}")
        
        return {
            "message": "Setup completato! Ora puoi effettuare il login.",
            "user": {
                "id": admin_user.id,
                "username": admin_user.username,
                "email": admin_user.email,
                "full_name": admin_user.full_name,
                "role": admin_user.role,
                "auth_method": admin_user.auth_method
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore durante setup: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Errore durante la creazione dell'utente: {str(e)}"
        )


@router.get("/realms")
async def get_proxmox_realms(db: Session = Depends(get_db)):
    """Ottiene i realm Proxmox disponibili"""
    
    node = await get_auth_node(db)
    if not node:
        return []
    
    return await proxmox_auth_service.get_available_realms(
        api_host=node.hostname,
        port=8006,
        verify_ssl=node.proxmox_verify_ssl
    )
