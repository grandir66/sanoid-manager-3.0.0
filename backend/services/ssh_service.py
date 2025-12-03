"""
SSH Service - Gestione connessioni SSH ai nodi Proxmox
"""

import asyncio
import paramiko
from typing import Optional, Tuple, List, Dict
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SSHResult:
    """Risultato di un comando SSH"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int


class SSHService:
    """Servizio per eseguire comandi via SSH sui nodi Proxmox"""
    
    def __init__(self):
        self._connections: Dict[str, paramiko.SSHClient] = {}
    
    def _get_client(
        self, 
        hostname: str, 
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> paramiko.SSHClient:
        """Ottiene o crea una connessione SSH"""
        key = f"{username}@{hostname}:{port}"
        
        if key in self._connections:
            client = self._connections[key]
            # Verifica se la connessione è ancora attiva
            try:
                transport = client.get_transport()
                if transport and transport.is_active():
                    return client
            except:
                pass
            # Connessione non attiva, la rimuoviamo
            del self._connections[key]
        
        # Crea nuova connessione
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            client.connect(
                hostname=hostname,
                port=port,
                username=username,
                key_filename=key_path,
                timeout=10,
                banner_timeout=10
            )
            self._connections[key] = client
            return client
        except Exception as e:
            logger.error(f"Errore connessione SSH a {hostname}: {e}")
            raise
    
    async def execute(
        self,
        hostname: str,
        command: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa",
        timeout: int = 300
    ) -> SSHResult:
        """Esegue un comando su un nodo remoto"""
        def _execute():
            try:
                client = self._get_client(hostname, port, username, key_path)
                stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
                
                exit_code = stdout.channel.recv_exit_status()
                stdout_text = stdout.read().decode('utf-8', errors='replace')
                stderr_text = stderr.read().decode('utf-8', errors='replace')
                
                return SSHResult(
                    success=(exit_code == 0),
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_code=exit_code
                )
            except Exception as e:
                logger.error(f"Errore esecuzione comando su {hostname}: {e}")
                return SSHResult(
                    success=False,
                    stdout="",
                    stderr=str(e),
                    exit_code=-1
                )
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _execute)
    
    async def test_connection(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Tuple[bool, str]:
        """Testa la connessione a un nodo"""
        result = await self.execute(
            hostname=hostname,
            command="echo 'OK' && hostname",
            port=port,
            username=username,
            key_path=key_path,
            timeout=10
        )
        
        if result.success:
            return True, result.stdout.strip()
        return False, result.stderr
    
    async def check_sanoid_installed(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Tuple[bool, Optional[str]]:
        """Verifica se Sanoid è installato"""
        result = await self.execute(
            hostname=hostname,
            command="which sanoid && sanoid --version 2>/dev/null || echo 'not found'",
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success and "not found" not in result.stdout:
            lines = result.stdout.strip().split('\n')
            version = lines[-1] if len(lines) > 1 else "unknown"
            return True, version
        return False, None
    
    async def get_zfs_datasets(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> List[Dict]:
        """Ottiene la lista dei dataset ZFS"""
        result = await self.execute(
            hostname=hostname,
            command="zfs list -H -o name,used,avail,mountpoint -t filesystem,volume",
            port=port,
            username=username,
            key_path=key_path
        )
        
        datasets = []
        if result.success:
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split('\t')
                    if len(parts) >= 4:
                        datasets.append({
                            "name": parts[0],
                            "used": parts[1],
                            "available": parts[2],
                            "mountpoint": parts[3] if parts[3] != "-" else None
                        })
        return datasets
    
    async def get_snapshots(
        self,
        hostname: str,
        dataset: Optional[str] = None,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> List[Dict]:
        """Ottiene la lista degli snapshot ZFS"""
        cmd = "zfs list -H -t snapshot -o name,used,creation -s creation"
        if dataset:
            cmd += f" -r {dataset}"
        
        result = await self.execute(
            hostname=hostname,
            command=cmd,
            port=port,
            username=username,
            key_path=key_path
        )
        
        snapshots = []
        if result.success:
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        name_parts = parts[0].split('@')
                        snapshots.append({
                            "full_name": parts[0],
                            "dataset": name_parts[0] if len(name_parts) > 0 else "",
                            "snapshot": name_parts[1] if len(name_parts) > 1 else "",
                            "used": parts[1],
                            "creation": parts[2]
                        })
        return snapshots
    
    async def create_snapshot(
        self,
        hostname: str,
        dataset: str,
        snapshot_name: str,
        recursive: bool = False,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> SSHResult:
        """Crea uno snapshot manuale"""
        r_flag = "-r" if recursive else ""
        cmd = f"zfs snapshot {r_flag} {dataset}@{snapshot_name}"
        
        return await self.execute(
            hostname=hostname,
            command=cmd,
            port=port,
            username=username,
            key_path=key_path
        )
    
    async def delete_snapshot(
        self,
        hostname: str,
        full_snapshot_name: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> SSHResult:
        """Elimina uno snapshot"""
        cmd = f"zfs destroy {full_snapshot_name}"
        
        return await self.execute(
            hostname=hostname,
            command=cmd,
            port=port,
            username=username,
            key_path=key_path
        )
    
    def close_all(self):
        """Chiude tutte le connessioni"""
        for key, client in self._connections.items():
            try:
                client.close()
            except:
                pass
        self._connections.clear()


# Singleton instance
ssh_service = SSHService()
