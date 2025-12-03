"""
Auth Service - Gestione autenticazione JWT e utenti
Compatibile con Python 3.9-3.13
"""

import os
import secrets
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, Tuple
from jose import JWTError, jwt
import logging

logger = logging.getLogger(__name__)

SECRET_KEY = os.environ.get("SANOID_MANAGER_SECRET_KEY", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("SANOID_MANAGER_TOKEN_EXPIRE", 480))


class AuthService:
    """Servizio per autenticazione e gestione token JWT"""
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verifica che la password corrisponda all'hash"""
        # Tronca a 72 byte (limite bcrypt)
        password_bytes = plain_password.encode('utf-8')[:72]
        hash_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(password_bytes, hash_bytes)
    
    def get_password_hash(self, password: str) -> str:
        """Genera hash della password"""
        # Tronca a 72 byte (limite bcrypt)
        password_bytes = password.encode('utf-8')[:72]
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password_bytes, salt)
        return hashed.decode('utf-8')
    
    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Crea un JWT access token"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access"
        })
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    
    def create_refresh_token(self, data: dict) -> str:
        """Crea un refresh token con scadenza più lunga"""
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(days=7)
        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh"
        })
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    
    def verify_token(self, token: str) -> Tuple[bool, Optional[dict]]:
        """Verifica e decodifica un token JWT"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return True, payload
        except JWTError as e:
            logger.warning(f"Token verification failed: {e}")
            return False, None
    
    def extract_user_id(self, token: str) -> Optional[int]:
        """Estrae l'ID utente dal token"""
        success, payload = self.verify_token(token)
        if success and payload:
            return payload.get("sub")
        return None
    
    def generate_api_key(self) -> str:
        """Genera una API key sicura"""
        return f"sm_{secrets.token_urlsafe(32)}"
    
    def validate_password_strength(self, password: str) -> Tuple[bool, str]:
        """Valida la forza della password"""
        if len(password) < 8:
            return False, "La password deve essere di almeno 8 caratteri"
        has_upper = any(c.isupper() for c in password)
        has_lower = any(c.islower() for c in password)
        has_digit = any(c.isdigit() for c in password)
        if not (has_upper and has_lower and has_digit):
            return False, "La password deve contenere maiuscole, minuscole e numeri"
        return True, "Password valida"


# Singleton
auth_service = AuthService()
