"""
SSH Key Service - Gestione chiavi SSH per sincronizzazione tra nodi
"""

import os
import asyncio
import subprocess
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass
import logging
import paramiko

logger = logging.getLogger(__name__)


@dataclass
class SSHKeyInfo:
    """Informazioni su una chiave SSH"""
    exists: bool
    public_key: Optional[str] = None
    key_type: Optional[str] = None
    fingerprint: Optional[str] = None
    comment: Optional[str] = None


@dataclass  
class KeyDistributionResult:
    """Risultato della distribuzione chiave"""
    host: str
    success: bool
    message: str
    already_present: bool = False


class SSHKeyService:
    """Servizio per gestione chiavi SSH"""
    
    DEFAULT_KEY_PATH = "/root/.ssh/id_rsa"
    DEFAULT_AUTHORIZED_KEYS = "/root/.ssh/authorized_keys"
    
    def __init__(self):
        pass
    
    def get_key_info(self, key_path: str = None) -> SSHKeyInfo:
        """Ottiene informazioni sulla chiave SSH locale"""
        key_path = key_path or self.DEFAULT_KEY_PATH
        pub_key_path = f"{key_path}.pub"
        
        if not os.path.exists(key_path) or not os.path.exists(pub_key_path):
            return SSHKeyInfo(exists=False)
        
        try:
            # Leggi chiave pubblica
            with open(pub_key_path, 'r') as f:
                public_key = f.read().strip()
            
            # Parsa la chiave pubblica
            parts = public_key.split()
            key_type = parts[0] if len(parts) > 0 else "unknown"
            comment = parts[2] if len(parts) > 2 else ""
            
            # Ottieni fingerprint
            result = subprocess.run(
                ['ssh-keygen', '-lf', pub_key_path],
                capture_output=True,
                text=True
            )
            fingerprint = ""
            if result.returncode == 0:
                fp_parts = result.stdout.strip().split()
                fingerprint = fp_parts[1] if len(fp_parts) > 1 else ""
            
            return SSHKeyInfo(
                exists=True,
                public_key=public_key,
                key_type=key_type,
                fingerprint=fingerprint,
                comment=comment
            )
        except Exception as e:
            logger.error(f"Errore lettura chiave SSH: {e}")
            return SSHKeyInfo(exists=False)
    
    def generate_key(
        self, 
        key_path: str = None,
        key_type: str = "rsa",
        bits: int = 4096,
        comment: str = "sanoid-manager",
        overwrite: bool = False
    ) -> Tuple[bool, str]:
        """Genera una nuova coppia di chiavi SSH"""
        key_path = key_path or self.DEFAULT_KEY_PATH
        
        # Verifica se esiste già
        if os.path.exists(key_path) and not overwrite:
            return False, "La chiave esiste già. Usa overwrite=True per sovrascriverla."
        
        try:
            # Crea directory .ssh se non esiste
            ssh_dir = os.path.dirname(key_path)
            if not os.path.exists(ssh_dir):
                os.makedirs(ssh_dir, mode=0o700)
            
            # Rimuovi chiave esistente se overwrite
            if overwrite:
                for f in [key_path, f"{key_path}.pub"]:
                    if os.path.exists(f):
                        os.remove(f)
            
            # Genera nuova chiave
            cmd = [
                'ssh-keygen',
                '-t', key_type,
                '-b', str(bits),
                '-C', comment,
                '-f', key_path,
                '-N', ''  # Nessuna passphrase
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                # Imposta permessi corretti
                os.chmod(key_path, 0o600)
                os.chmod(f"{key_path}.pub", 0o644)
                return True, "Chiave generata con successo"
            else:
                return False, f"Errore generazione chiave: {result.stderr}"
                
        except Exception as e:
            logger.error(f"Errore generazione chiave SSH: {e}")
            return False, str(e)
    
    async def distribute_key_to_host(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        password: str = None,
        key_path: str = None
    ) -> KeyDistributionResult:
        """Distribuisce la chiave pubblica a un host remoto"""
        key_path = key_path or self.DEFAULT_KEY_PATH
        pub_key_path = f"{key_path}.pub"
        
        if not os.path.exists(pub_key_path):
            return KeyDistributionResult(
                host=hostname,
                success=False,
                message="Chiave pubblica non trovata. Genera prima una chiave."
            )
        
        try:
            with open(pub_key_path, 'r') as f:
                public_key = f.read().strip()
            
            def _distribute():
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                
                try:
                    # Prima prova con la chiave esistente
                    try:
                        client.connect(
                            hostname=hostname,
                            port=port,
                            username=username,
                            key_filename=key_path,
                            timeout=10
                        )
                        auth_method = "key"
                    except Exception:
                        # Fallback a password se fornita
                        if password:
                            client.connect(
                                hostname=hostname,
                                port=port,
                                username=username,
                                password=password,
                                timeout=10
                            )
                            auth_method = "password"
                        else:
                            raise
                    
                    # Verifica se la chiave è già presente
                    stdin, stdout, stderr = client.exec_command(
                        f"grep -F '{public_key}' ~/.ssh/authorized_keys 2>/dev/null"
                    )
                    if stdout.read().decode().strip():
                        return KeyDistributionResult(
                            host=hostname,
                            success=True,
                            message="Chiave già presente",
                            already_present=True
                        )
                    
                    # Aggiungi la chiave
                    commands = [
                        "mkdir -p ~/.ssh",
                        "chmod 700 ~/.ssh",
                        f"echo '{public_key}' >> ~/.ssh/authorized_keys",
                        "chmod 600 ~/.ssh/authorized_keys",
                        "sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys"  # Rimuovi duplicati
                    ]
                    
                    for cmd in commands:
                        stdin, stdout, stderr = client.exec_command(cmd)
                        exit_code = stdout.channel.recv_exit_status()
                        if exit_code != 0:
                            err = stderr.read().decode()
                            return KeyDistributionResult(
                                host=hostname,
                                success=False,
                                message=f"Errore: {err}"
                            )
                    
                    return KeyDistributionResult(
                        host=hostname,
                        success=True,
                        message=f"Chiave distribuita con successo (auth: {auth_method})"
                    )
                    
                finally:
                    client.close()
                    
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _distribute)
            
        except Exception as e:
            logger.error(f"Errore distribuzione chiave a {hostname}: {e}")
            return KeyDistributionResult(
                host=hostname,
                success=False,
                message=str(e)
            )
    
    async def test_key_auth(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = None
    ) -> Tuple[bool, str]:
        """Testa l'autenticazione via chiave SSH"""
        key_path = key_path or self.DEFAULT_KEY_PATH
        
        if not os.path.exists(key_path):
            return False, "Chiave privata non trovata"
        
        def _test():
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            try:
                client.connect(
                    hostname=hostname,
                    port=port,
                    username=username,
                    key_filename=key_path,
                    timeout=10,
                    look_for_keys=False,
                    allow_agent=False
                )
                
                stdin, stdout, stderr = client.exec_command("hostname && whoami")
                exit_code = stdout.channel.recv_exit_status()
                
                if exit_code == 0:
                    output = stdout.read().decode().strip()
                    return True, f"Connesso: {output}"
                else:
                    return False, stderr.read().decode()
                    
            except paramiko.AuthenticationException:
                return False, "Autenticazione fallita - chiave non autorizzata"
            except Exception as e:
                return False, str(e)
            finally:
                client.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _test)
    
    async def distribute_key_to_all_nodes(
        self,
        nodes: List[Dict],
        password: str = None,
        key_path: str = None
    ) -> List[KeyDistributionResult]:
        """Distribuisce la chiave a tutti i nodi"""
        results = []
        
        for node in nodes:
            result = await self.distribute_key_to_host(
                hostname=node.get('host') or node.get('ip'),
                port=node.get('port', 22),
                username=node.get('username', 'root'),
                password=password,
                key_path=key_path
            )
            results.append(result)
        
        return results
    
    async def test_all_nodes(
        self,
        nodes: List[Dict],
        key_path: str = None
    ) -> List[Dict]:
        """Testa la connettività SSH a tutti i nodi"""
        results = []
        
        for node in nodes:
            success, message = await self.test_key_auth(
                hostname=node.get('host') or node.get('ip'),
                port=node.get('port', 22),
                username=node.get('username', 'root'),
                key_path=key_path
            )
            results.append({
                "node_id": node.get('id'),
                "node_name": node.get('name'),
                "host": node.get('host') or node.get('ip'),
                "success": success,
                "message": message
            })
        
        return results
    
    def get_authorized_keys(self) -> List[Dict]:
        """Ottiene le chiavi autorizzate sul server locale"""
        auth_keys_path = self.DEFAULT_AUTHORIZED_KEYS
        keys = []
        
        if not os.path.exists(auth_keys_path):
            return keys
        
        try:
            with open(auth_keys_path, 'r') as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parts = line.split()
                        keys.append({
                            "index": i,
                            "type": parts[0] if len(parts) > 0 else "unknown",
                            "key": parts[1][:50] + "..." if len(parts) > 1 and len(parts[1]) > 50 else (parts[1] if len(parts) > 1 else ""),
                            "comment": parts[2] if len(parts) > 2 else "",
                            "full_key": line
                        })
        except Exception as e:
            logger.error(f"Errore lettura authorized_keys: {e}")
        
        return keys
    
    async def remove_key_from_host(
        self,
        hostname: str,
        key_to_remove: str,
        port: int = 22,
        username: str = "root",
        key_path: str = None
    ) -> Tuple[bool, str]:
        """Rimuove una chiave specifica da un host remoto"""
        key_path = key_path or self.DEFAULT_KEY_PATH
        
        def _remove():
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            try:
                client.connect(
                    hostname=hostname,
                    port=port,
                    username=username,
                    key_filename=key_path,
                    timeout=10
                )
                
                # Escape della chiave per sed
                escaped_key = key_to_remove.replace('/', '\\/').replace('+', '\\+')
                
                cmd = f"sed -i '/{escaped_key}/d' ~/.ssh/authorized_keys"
                stdin, stdout, stderr = client.exec_command(cmd)
                exit_code = stdout.channel.recv_exit_status()
                
                if exit_code == 0:
                    return True, "Chiave rimossa con successo"
                else:
                    return False, stderr.read().decode()
                    
            except Exception as e:
                return False, str(e)
            finally:
                client.close()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _remove)
    
    async def copy_keypair_to_host(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = None
    ) -> Tuple[bool, str]:
        """
        Copia la coppia di chiavi (privata e pubblica) su un host remoto.
        Questo permette all'host di usare la stessa chiave per connettersi ad altri nodi.
        """
        key_path = key_path or self.DEFAULT_KEY_PATH
        pub_key_path = f"{key_path}.pub"
        
        if not os.path.exists(key_path) or not os.path.exists(pub_key_path):
            return False, "Chiavi locali non trovate"
        
        try:
            # Leggi le chiavi locali
            with open(key_path, 'r') as f:
                private_key = f.read()
            with open(pub_key_path, 'r') as f:
                public_key = f.read().strip()
            
            def _copy():
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                
                try:
                    client.connect(
                        hostname=hostname,
                        port=port,
                        username=username,
                        key_filename=key_path,
                        timeout=10
                    )
                    
                    # Crea directory .ssh se non esiste
                    client.exec_command("mkdir -p ~/.ssh && chmod 700 ~/.ssh")
                    
                    # Usa SFTP per copiare i file
                    sftp = client.open_sftp()
                    
                    # Scrivi chiave privata
                    remote_key_path = f"/root/.ssh/id_rsa"
                    with sftp.file(remote_key_path, 'w') as f:
                        f.write(private_key)
                    sftp.chmod(remote_key_path, 0o600)
                    
                    # Scrivi chiave pubblica
                    remote_pub_path = f"/root/.ssh/id_rsa.pub"
                    with sftp.file(remote_pub_path, 'w') as f:
                        f.write(public_key + '\n')
                    sftp.chmod(remote_pub_path, 0o644)
                    
                    sftp.close()
                    
                    return True, "Coppia di chiavi copiata con successo"
                    
                except Exception as e:
                    return False, str(e)
                finally:
                    client.close()
            
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _copy)
            
        except Exception as e:
            logger.error(f"Errore copia chiavi a {hostname}: {e}")
            return False, str(e)
    
    async def setup_mesh_ssh(
        self,
        nodes: List[Dict],
        key_path: str = None
    ) -> List[Dict]:
        """
        Configura SSH mesh: copia la stessa chiave su tutti i nodi
        e aggiunge la chiave pubblica agli authorized_keys di tutti.
        
        Questo permette a ogni nodo di connettersi a ogni altro nodo.
        """
        results = []
        key_path = key_path or self.DEFAULT_KEY_PATH
        
        for node in nodes:
            hostname = node.get('host') or node.get('ip')
            port = node.get('port', 22)
            username = node.get('username', 'root')
            
            # Step 1: Copia la coppia di chiavi
            copy_success, copy_msg = await self.copy_keypair_to_host(
                hostname=hostname,
                port=port,
                username=username,
                key_path=key_path
            )
            
            # Step 2: Aggiungi chiave pubblica agli authorized_keys
            dist_result = await self.distribute_key_to_host(
                hostname=hostname,
                port=port,
                username=username,
                key_path=key_path
            )
            
            results.append({
                "node_id": node.get('id'),
                "node_name": node.get('name'),
                "host": hostname,
                "keypair_copied": copy_success,
                "keypair_message": copy_msg,
                "authorized": dist_result.success,
                "auth_message": dist_result.message,
                "success": copy_success and dist_result.success
            })
        
        return results


# Singleton instance
ssh_key_service = SSHKeyService()

