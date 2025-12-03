"""
Syncoid Service - Gestione replica ZFS tra nodi
"""

import asyncio
from datetime import datetime
from typing import Optional, Dict, Tuple
import logging
import re

from services.ssh_service import ssh_service, SSHResult

logger = logging.getLogger(__name__)


class SyncoidService:
    """Servizio per replica ZFS con Syncoid"""
    
    def build_syncoid_command(
        self,
        source_host: Optional[str],
        source_dataset: str,
        dest_host: Optional[str],
        dest_dataset: str,
        source_user: str = "root",
        dest_user: str = "root",
        source_port: int = 22,
        dest_port: int = 22,
        source_key: str = "/root/.ssh/id_rsa",
        dest_key: str = "/root/.ssh/id_rsa",
        recursive: bool = False,
        compress: str = "lz4",
        mbuffer_size: str = "128M",
        no_sync_snap: bool = False,
        force_delete: bool = False,
        extra_args: str = ""
    ) -> str:
        """
        Costruisce il comando syncoid.
        Usa sintassi compatibile con tutte le versioni di syncoid.
        
        Sintassi syncoid:
        - syncoid source dest                      (locale -> locale)
        - syncoid source user@host:dest            (locale -> remoto, push)
        - syncoid user@host:source dest            (remoto -> locale, pull)
        - syncoid user@host:source user@host:dest  (remoto -> remoto)
        """
        
        cmd_parts = ["syncoid"]
        
        # Opzioni base
        if recursive:
            cmd_parts.append("--recursive")
        
        if compress and compress != "none":
            cmd_parts.append(f"--compress={compress}")
        
        if mbuffer_size:
            cmd_parts.append(f"--mbuffer-size={mbuffer_size}")
        
        if no_sync_snap:
            cmd_parts.append("--no-sync-snap")
        
        if force_delete:
            cmd_parts.append("--force-delete")
        
        # SSH options (compatibile con tutte le versioni)
        # Determina quale chiave/porta usare in base a sorgente/destinazione remota
        if dest_host:
            # Push a destinazione remota - usa opzioni SSH per la destinazione
            cmd_parts.append(f"--sshkey={dest_key}")
            if dest_port != 22:
                cmd_parts.append(f"--sshport={dest_port}")
        elif source_host:
            # Pull da sorgente remota - usa opzioni SSH per la sorgente
            cmd_parts.append(f"--sshkey={source_key}")
            if source_port != 22:
                cmd_parts.append(f"--sshport={source_port}")
        
        if extra_args:
            cmd_parts.append(extra_args)
        
        # Costruisci sorgente
        if source_host:
            source = f"{source_user}@{source_host}:{source_dataset}"
        else:
            source = source_dataset
        
        # Costruisci destinazione
        if dest_host:
            dest = f"{dest_user}@{dest_host}:{dest_dataset}"
        else:
            dest = dest_dataset
        
        cmd_parts.append(source)
        cmd_parts.append(dest)
        
        return " ".join(cmd_parts)
    
    async def run_sync(
        self,
        executor_host: str,  # Nodo da cui eseguire syncoid
        source_host: Optional[str],  # None se locale all'executor
        source_dataset: str,
        dest_host: Optional[str],  # None se locale all'executor
        dest_dataset: str,
        source_user: str = "root",
        dest_user: str = "root",
        source_port: int = 22,
        dest_port: int = 22,
        executor_port: int = 22,
        executor_user: str = "root",
        executor_key: str = "/root/.ssh/id_rsa",
        source_key: str = "/root/.ssh/id_rsa",
        dest_key: str = "/root/.ssh/id_rsa",
        recursive: bool = False,
        compress: str = "lz4",
        mbuffer_size: str = "128M",
        no_sync_snap: bool = False,
        force_delete: bool = False,
        extra_args: str = "",
        timeout: int = 3600
    ) -> Dict:
        """
        Esegue una sincronizzazione Syncoid
        
        Returns dict con:
            - success: bool
            - output: str
            - error: str
            - duration: int (secondi)
            - transferred: str (es: "1.5G")
        """
        
        start_time = datetime.utcnow()
        
        # Costruisci comando
        cmd = self.build_syncoid_command(
            source_host=source_host,
            source_dataset=source_dataset,
            dest_host=dest_host,
            dest_dataset=dest_dataset,
            source_user=source_user,
            dest_user=dest_user,
            source_port=source_port,
            dest_port=dest_port,
            source_key=source_key,
            dest_key=dest_key,
            recursive=recursive,
            compress=compress,
            mbuffer_size=mbuffer_size,
            no_sync_snap=no_sync_snap,
            force_delete=force_delete,
            extra_args=extra_args
        )
        
        logger.info(f"Esecuzione syncoid: {cmd}")
        
        # Esegui comando
        result = await ssh_service.execute(
            hostname=executor_host,
            command=cmd,
            port=executor_port,
            username=executor_user,
            key_path=executor_key,
            timeout=timeout
        )
        
        end_time = datetime.utcnow()
        duration = int((end_time - start_time).total_seconds())
        
        # Parse output per trasferimento
        transferred = self._parse_transferred(result.stdout + result.stderr)
        
        return {
            "success": result.success,
            "output": result.stdout,
            "error": result.stderr,
            "duration": duration,
            "transferred": transferred,
            "command": cmd
        }
    
    def _parse_transferred(self, output: str) -> Optional[str]:
        """Estrae la quantità di dati trasferiti dall'output di syncoid"""
        # Pattern comuni nell'output di syncoid
        patterns = [
            r"(\d+(?:\.\d+)?[KMGT]i?B?)\s+transferred",
            r"sent\s+(\d+(?:\.\d+)?[KMGT]i?B?)",
            r"(\d+(?:\.\d+)?[KMGT]i?B?)\s+total",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                return match.group(1)
        
        return None
    
    async def verify_datasets_exist(
        self,
        hostname: str,
        datasets: list,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Dict[str, bool]:
        """Verifica che i dataset esistano su un nodo"""
        results = {}
        
        for ds in datasets:
            result = await ssh_service.execute(
                hostname=hostname,
                command=f"zfs list -H -o name {ds} 2>/dev/null",
                port=port,
                username=username,
                key_path=key_path
            )
            results[ds] = result.success and ds in result.stdout
        
        return results
    
    async def create_dataset(
        self,
        hostname: str,
        dataset: str,
        parent_must_exist: bool = True,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> SSHResult:
        """Crea un dataset ZFS"""
        flags = "-p" if not parent_must_exist else ""
        cmd = f"zfs create {flags} {dataset}"
        
        return await ssh_service.execute(
            hostname=hostname,
            command=cmd,
            port=port,
            username=username,
            key_path=key_path
        )
    
    async def get_last_common_snapshot(
        self,
        source_host: str,
        source_dataset: str,
        dest_host: str,
        dest_dataset: str,
        source_port: int = 22,
        dest_port: int = 22,
        source_user: str = "root",
        dest_user: str = "root",
        source_key: str = "/root/.ssh/id_rsa",
        dest_key: str = "/root/.ssh/id_rsa"
    ) -> Optional[str]:
        """Trova l'ultimo snapshot comune tra sorgente e destinazione"""
        
        # Ottieni snapshot sorgente
        source_result = await ssh_service.execute(
            hostname=source_host,
            command=f"zfs list -H -t snapshot -o name -s creation {source_dataset}",
            port=source_port,
            username=source_user,
            key_path=source_key
        )
        
        if not source_result.success:
            return None
        
        source_snaps = set()
        for line in source_result.stdout.strip().split('\n'):
            if '@' in line:
                snap_name = line.split('@')[1]
                source_snaps.add(snap_name)
        
        # Ottieni snapshot destinazione
        dest_result = await ssh_service.execute(
            hostname=dest_host,
            command=f"zfs list -H -t snapshot -o name -s creation {dest_dataset}",
            port=dest_port,
            username=dest_user,
            key_path=dest_key
        )
        
        if not dest_result.success:
            return None
        
        dest_snaps = []
        for line in dest_result.stdout.strip().split('\n'):
            if '@' in line:
                snap_name = line.split('@')[1]
                if snap_name in source_snaps:
                    dest_snaps.append(snap_name)
        
        # Ritorna l'ultimo comune (il più recente per creation time)
        return dest_snaps[-1] if dest_snaps else None


# Singleton
syncoid_service = SyncoidService()
