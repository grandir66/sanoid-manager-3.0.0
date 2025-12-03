"""
Proxmox Auth Service - Autenticazione integrata con Proxmox VE API
Supporta tutti i realm: PAM, PVE, LDAP, AD
"""

import aiohttp
import ssl
import logging
from typing import Optional, Tuple, Dict, List
from dataclasses import dataclass
from datetime import datetime
import urllib.parse

logger = logging.getLogger(__name__)


@dataclass
class ProxmoxUser:
    """Rappresenta un utente Proxmox autenticato"""
    userid: str  # formato: username@realm
    username: str
    realm: str
    firstname: Optional[str] = None
    lastname: Optional[str] = None
    email: Optional[str] = None
    groups: List[str] = None
    is_admin: bool = False
    permissions: Dict[str, List[str]] = None
    
    @property
    def full_name(self) -> str:
        if self.firstname and self.lastname:
            return f"{self.firstname} {self.lastname}"
        return self.username


@dataclass
class ProxmoxTicket:
    """Ticket di autenticazione Proxmox"""
    ticket: str
    csrf_token: str
    username: str
    expires: datetime


class ProxmoxAuthService:
    """
    Servizio per autenticazione tramite API Proxmox VE.
    
    Supporta:
    - Autenticazione PAM (utenti Linux)
    - Autenticazione PVE (utenti Proxmox)
    - Autenticazione LDAP/AD
    - API Token
    """
    
    def __init__(self):
        # Cache dei ticket per evitare richieste ripetute
        self._ticket_cache: Dict[str, ProxmoxTicket] = {}
    
    def _get_ssl_context(self, verify_ssl: bool = False) -> ssl.SSLContext:
        """Crea un contesto SSL (Proxmox usa spesso certificati self-signed)"""
        if verify_ssl:
            return ssl.create_default_context()
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
    
    async def authenticate(
        self,
        api_host: str,
        username: str,
        password: str,
        realm: str = "pam",
        port: int = 8006,
        verify_ssl: bool = False
    ) -> Tuple[bool, Optional[ProxmoxUser], Optional[str]]:
        """
        Autentica un utente tramite Proxmox API.
        
        Args:
            api_host: Hostname/IP del nodo Proxmox
            username: Nome utente (senza @realm)
            password: Password
            realm: Realm di autenticazione (pam, pve, ldap, ad)
            port: Porta API Proxmox (default 8006)
            verify_ssl: Verifica certificato SSL
            
        Returns:
            Tuple[bool, Optional[ProxmoxUser], Optional[str]]: 
                (success, user_info, error_message)
        """
        userid = f"{username}@{realm}"
        api_url = f"https://{api_host}:{port}/api2/json"
        
        ssl_context = self._get_ssl_context(verify_ssl)
        
        try:
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                # 1. Ottieni ticket di autenticazione
                auth_url = f"{api_url}/access/ticket"
                auth_data = {
                    "username": userid,
                    "password": password
                }
                
                async with session.post(auth_url, data=auth_data) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.warning(f"Proxmox auth failed for {userid}: {response.status}")
                        return False, None, "Credenziali non valide"
                    
                    result = await response.json()
                    
                    if "data" not in result:
                        return False, None, "Risposta API non valida"
                    
                    data = result["data"]
                    ticket = data.get("ticket")
                    csrf_token = data.get("CSRFPreventionToken")
                    
                    if not ticket:
                        return False, None, "Ticket non ricevuto"
                
                # 2. Ottieni informazioni utente
                user_info = await self._get_user_info(
                    session, api_url, userid, ticket, csrf_token
                )
                
                # 3. Ottieni permessi
                permissions = await self._get_user_permissions(
                    session, api_url, userid, ticket, csrf_token
                )
                
                # 4. Determina se è admin
                is_admin = await self._check_admin_privileges(
                    session, api_url, userid, ticket, csrf_token, permissions
                )
                
                # Crea oggetto utente
                proxmox_user = ProxmoxUser(
                    userid=userid,
                    username=username,
                    realm=realm,
                    firstname=user_info.get("firstname"),
                    lastname=user_info.get("lastname"),
                    email=user_info.get("email"),
                    groups=user_info.get("groups", []),
                    is_admin=is_admin,
                    permissions=permissions
                )
                
                # Cache del ticket
                self._ticket_cache[userid] = ProxmoxTicket(
                    ticket=ticket,
                    csrf_token=csrf_token,
                    username=userid,
                    expires=datetime.utcnow()
                )
                
                logger.info(f"Proxmox auth successful for {userid} (admin={is_admin})")
                return True, proxmox_user, None
                
        except aiohttp.ClientConnectorError as e:
            logger.error(f"Connection error to Proxmox API: {e}")
            return False, None, f"Impossibile connettersi a Proxmox: {api_host}"
        except Exception as e:
            logger.error(f"Proxmox auth error: {e}")
            return False, None, f"Errore di autenticazione: {str(e)}"
    
    async def authenticate_with_token(
        self,
        api_host: str,
        token_id: str,
        token_secret: str,
        port: int = 8006,
        verify_ssl: bool = False
    ) -> Tuple[bool, Optional[ProxmoxUser], Optional[str]]:
        """
        Autentica usando un API Token Proxmox.
        
        Args:
            api_host: Hostname/IP del nodo Proxmox
            token_id: ID del token (formato: user@realm!tokenname)
            token_secret: Secret del token
            
        Returns:
            Tuple[bool, Optional[ProxmoxUser], Optional[str]]
        """
        api_url = f"https://{api_host}:{port}/api2/json"
        ssl_context = self._get_ssl_context(verify_ssl)
        
        # Estrai username dal token_id
        # Formato: user@realm!tokenname
        try:
            userid = token_id.split("!")[0]
            username, realm = userid.split("@")
        except (ValueError, IndexError):
            return False, None, "Formato token_id non valido"
        
        headers = {
            "Authorization": f"PVEAPIToken={token_id}={token_secret}"
        }
        
        try:
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                # Verifica token con una chiamata semplice
                async with session.get(
                    f"{api_url}/version",
                    headers=headers
                ) as response:
                    if response.status != 200:
                        return False, None, "API Token non valido"
                
                # Ottieni info utente
                user_info = await self._get_user_info_with_token(
                    session, api_url, userid, headers
                )
                
                permissions = await self._get_user_permissions_with_token(
                    session, api_url, userid, headers
                )
                
                is_admin = "Sys.Audit" in permissions.get("/", []) or \
                           "Sys.Modify" in permissions.get("/", [])
                
                proxmox_user = ProxmoxUser(
                    userid=userid,
                    username=username,
                    realm=realm,
                    firstname=user_info.get("firstname"),
                    lastname=user_info.get("lastname"),
                    email=user_info.get("email"),
                    is_admin=is_admin,
                    permissions=permissions
                )
                
                return True, proxmox_user, None
                
        except Exception as e:
            logger.error(f"Proxmox token auth error: {e}")
            return False, None, str(e)
    
    async def _get_user_info(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
        userid: str,
        ticket: str,
        csrf_token: str
    ) -> Dict:
        """Ottiene informazioni dettagliate sull'utente"""
        headers = {
            "Cookie": f"PVEAuthCookie={urllib.parse.quote(ticket)}",
            "CSRFPreventionToken": csrf_token
        }
        
        try:
            encoded_userid = urllib.parse.quote(userid, safe='')
            async with session.get(
                f"{api_url}/access/users/{encoded_userid}",
                headers=headers
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("data", {})
        except Exception as e:
            logger.warning(f"Could not get user info: {e}")
        
        return {}
    
    async def _get_user_info_with_token(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
        userid: str,
        headers: Dict
    ) -> Dict:
        """Ottiene informazioni utente usando API token"""
        try:
            encoded_userid = urllib.parse.quote(userid, safe='')
            async with session.get(
                f"{api_url}/access/users/{encoded_userid}",
                headers=headers
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("data", {})
        except Exception as e:
            logger.warning(f"Could not get user info with token: {e}")
        
        return {}
    
    async def _get_user_permissions(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
        userid: str,
        ticket: str,
        csrf_token: str
    ) -> Dict[str, List[str]]:
        """Ottiene i permessi dell'utente su tutti i path"""
        headers = {
            "Cookie": f"PVEAuthCookie={urllib.parse.quote(ticket)}",
            "CSRFPreventionToken": csrf_token
        }
        
        permissions = {}
        
        try:
            async with session.get(
                f"{api_url}/access/permissions",
                headers=headers
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    data = result.get("data", {})
                    
                    # Converte in formato path -> [permissions]
                    for path, perms in data.items():
                        permissions[path] = [p for p, v in perms.items() if v == 1]
        except Exception as e:
            logger.warning(f"Could not get permissions: {e}")
        
        return permissions
    
    async def _get_user_permissions_with_token(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
        userid: str,
        headers: Dict
    ) -> Dict[str, List[str]]:
        """Ottiene i permessi usando API token"""
        permissions = {}
        
        try:
            async with session.get(
                f"{api_url}/access/permissions",
                headers=headers
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    data = result.get("data", {})
                    for path, perms in data.items():
                        permissions[path] = [p for p, v in perms.items() if v == 1]
        except Exception as e:
            logger.warning(f"Could not get permissions with token: {e}")
        
        return permissions
    
    async def _check_admin_privileges(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
        userid: str,
        ticket: str,
        csrf_token: str,
        permissions: Dict[str, List[str]]
    ) -> bool:
        """Determina se l'utente ha privilegi di amministratore"""
        
        # Verifica se è root@pam (sempre admin)
        if userid == "root@pam":
            return True
        
        # Verifica permessi sul path root
        root_perms = permissions.get("/", [])
        admin_perms = ["Sys.Audit", "Sys.Modify", "Permissions.Modify"]
        
        if any(p in root_perms for p in admin_perms):
            return True
        
        # Verifica se appartiene al gruppo admin
        headers = {
            "Cookie": f"PVEAuthCookie={urllib.parse.quote(ticket)}",
            "CSRFPreventionToken": csrf_token
        }
        
        try:
            async with session.get(
                f"{api_url}/access/groups",
                headers=headers
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    groups = result.get("data", [])
                    
                    # Cerca gruppi admin
                    for group in groups:
                        if group.get("groupid") in ["admin", "administrators"]:
                            members = group.get("members", "").split(",")
                            if userid in members:
                                return True
        except Exception as e:
            logger.warning(f"Could not check admin groups: {e}")
        
        return False
    
    async def get_available_realms(
        self,
        api_host: str,
        port: int = 8006,
        verify_ssl: bool = False
    ) -> List[Dict]:
        """
        Ottiene i realm di autenticazione disponibili su Proxmox.
        Non richiede autenticazione.
        """
        api_url = f"https://{api_host}:{port}/api2/json"
        ssl_context = self._get_ssl_context(verify_ssl)
        
        try:
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(f"{api_url}/access/domains") as response:
                    if response.status == 200:
                        result = await response.json()
                        realms = result.get("data", [])
                        
                        return [
                            {
                                "realm": r.get("realm"),
                                "type": r.get("type"),
                                "comment": r.get("comment", ""),
                                "default": r.get("default", 0) == 1
                            }
                            for r in realms
                        ]
        except Exception as e:
            logger.error(f"Could not get realms: {e}")
        
        # Fallback con realm comuni
        return [
            {"realm": "pam", "type": "pam", "comment": "Linux PAM", "default": True},
            {"realm": "pve", "type": "pve", "comment": "Proxmox VE", "default": False}
        ]
    
    async def verify_node_access(
        self,
        api_host: str,
        ticket: str,
        csrf_token: str,
        node_name: str,
        port: int = 8006,
        verify_ssl: bool = False
    ) -> bool:
        """Verifica se l'utente ha accesso a un nodo specifico"""
        api_url = f"https://{api_host}:{port}/api2/json"
        ssl_context = self._get_ssl_context(verify_ssl)
        
        headers = {
            "Cookie": f"PVEAuthCookie={urllib.parse.quote(ticket)}",
            "CSRFPreventionToken": csrf_token
        }
        
        try:
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    f"{api_url}/nodes/{node_name}/status",
                    headers=headers
                ) as response:
                    return response.status == 200
        except Exception:
            return False
    
    def get_cached_ticket(self, userid: str) -> Optional[ProxmoxTicket]:
        """Ottiene un ticket dalla cache se ancora valido"""
        ticket = self._ticket_cache.get(userid)
        if ticket:
            # I ticket Proxmox durano 2 ore
            from datetime import timedelta
            if datetime.utcnow() - ticket.expires < timedelta(hours=2):
                return ticket
            else:
                del self._ticket_cache[userid]
        return None
    
    def clear_cache(self, userid: Optional[str] = None):
        """Pulisce la cache dei ticket"""
        if userid:
            self._ticket_cache.pop(userid, None)
        else:
            self._ticket_cache.clear()


# Singleton
proxmox_auth_service = ProxmoxAuthService()

