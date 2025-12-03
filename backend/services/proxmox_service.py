"""
Proxmox Service - Gestione VM e integrazione API Proxmox
"""

import asyncio
from typing import Optional, Dict, List, Tuple
import logging
import json
import re

from services.ssh_service import ssh_service, SSHResult

logger = logging.getLogger(__name__)


class ProxmoxService:
    """Servizio per integrazione con Proxmox VE"""
    
    async def get_vm_list(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> List[Dict]:
        """Ottiene la lista delle VM (qemu) sul nodo"""
        
        result = await ssh_service.execute(
            hostname=hostname,
            command="qm list 2>/dev/null | tail -n +2",
            port=port,
            username=username,
            key_path=key_path
        )
        
        vms = []
        if result.success:
            for line in result.stdout.strip().split('\n'):
                if line:
                    # Format: VMID NAME STATUS MEM BOOTDISK PID
                    parts = line.split()
                    if len(parts) >= 3:
                        vms.append({
                            "vmid": int(parts[0]),
                            "name": parts[1],
                            "status": parts[2],
                            "type": "qemu"
                        })
        
        return vms
    
    async def get_container_list(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> List[Dict]:
        """Ottiene la lista dei container LXC sul nodo"""
        
        result = await ssh_service.execute(
            hostname=hostname,
            command="pct list 2>/dev/null | tail -n +2",
            port=port,
            username=username,
            key_path=key_path
        )
        
        containers = []
        if result.success:
            for line in result.stdout.strip().split('\n'):
                if line:
                    # Format: VMID STATUS LOCK NAME
                    parts = line.split()
                    if len(parts) >= 2:
                        containers.append({
                            "vmid": int(parts[0]),
                            "status": parts[1],
                            "name": parts[3] if len(parts) >= 4 else f"CT{parts[0]}",
                            "type": "lxc"
                        })
        
        return containers
    
    async def get_all_guests(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> List[Dict]:
        """Ottiene tutte le VM e i container"""
        vms = await self.get_vm_list(hostname, port, username, key_path)
        containers = await self.get_container_list(hostname, port, username, key_path)
        return vms + containers
    
    async def get_vm_config(
        self,
        hostname: str,
        vmid: int,
        vm_type: str = "qemu",
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Tuple[bool, str]:
        """Ottiene la configurazione di una VM/container"""
        
        cmd = "qm" if vm_type == "qemu" else "pct"
        
        result = await ssh_service.execute(
            hostname=hostname,
            command=f"{cmd} config {vmid}",
            port=port,
            username=username,
            key_path=key_path
        )
        
        return result.success, result.stdout
    
    async def get_vm_config_file(
        self,
        hostname: str,
        vmid: int,
        vm_type: str = "qemu",
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Tuple[bool, str]:
        """Ottiene il file di configurazione raw della VM"""
        
        if vm_type == "qemu":
            config_path = f"/etc/pve/qemu-server/{vmid}.conf"
        else:
            config_path = f"/etc/pve/lxc/{vmid}.conf"
        
        result = await ssh_service.execute(
            hostname=hostname,
            command=f"cat {config_path}",
            port=port,
            username=username,
            key_path=key_path
        )
        
        return result.success, result.stdout
    
    async def get_vm_disks_with_size(
        self,
        hostname: str,
        vmid: int,
        vm_type: str = "qemu",
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> List[Dict]:
        """
        Ottiene tutti i dischi di una VM con dimensioni e dataset ZFS.
        Ritorna lista di dict con: disk_name, storage, volume, dataset, size, size_bytes
        """
        
        success, config = await self.get_vm_config(hostname, vmid, vm_type, port, username, key_path)
        
        if not success:
            return []
        
        # Pattern per dischi QEMU: scsi0: local-zfs:vm-100-disk-0,size=32G
        # Pattern per dischi LXC: mp0: local-zfs:subvol-100-disk-0,mp=/mnt/data,size=8G
        if vm_type == "qemu":
            disk_pattern = r'((?:scsi|sata|virtio|ide)\d+):\s*(\S+?):(\S+?)(?:,|$)'
        else:
            disk_pattern = r'((?:rootfs|mp)\d*):\s*(\S+?):(\S+?)(?:,|$)'
        
        matches = re.findall(disk_pattern, config)
        disks = []
        
        for disk_name, storage, volume in matches:
            # Ignora cdrom e cloudinit
            if 'cloudinit' in volume.lower() or 'none' in volume.lower():
                continue
            
            disk_info = {
                "disk_name": disk_name,
                "storage": storage,
                "volume": volume,
                "dataset": None,
                "size": "N/A",
                "size_bytes": 0
            }
            
            # Ottieni il path ZFS dello storage
            storage_result = await ssh_service.execute(
                hostname=hostname,
                command=f"pvesm path {storage}:{volume} 2>/dev/null",
                port=port,
                username=username,
                key_path=key_path
            )
            
            if storage_result.success and storage_result.stdout.strip():
                # Il path è tipo /dev/zvol/poolname/data/vm-100-disk-0
                # o /poolname/data/subvol-100-disk-0 per LXC
                path = storage_result.stdout.strip()
                
                # Estrai il dataset ZFS dal path
                if path.startswith('/dev/zvol/'):
                    dataset = path.replace('/dev/zvol/', '')
                elif path.startswith('/'):
                    # Per subvol LXC, cerca il dataset
                    dataset = path.lstrip('/')
                else:
                    dataset = None
                
                if dataset:
                    disk_info["dataset"] = dataset
                    
                    # Ottieni la dimensione del dataset/zvol
                    size_result = await ssh_service.execute(
                        hostname=hostname,
                        command=f"zfs get -Hp -o value used,volsize,referenced {dataset} 2>/dev/null | head -1",
                        port=port,
                        username=username,
                        key_path=key_path
                    )
                    
                    if size_result.success and size_result.stdout.strip():
                        try:
                            size_bytes = int(size_result.stdout.strip().split()[0])
                            disk_info["size_bytes"] = size_bytes
                            disk_info["size"] = self._format_size(size_bytes)
                        except:
                            pass
            
            disks.append(disk_info)
        
        return disks
    
    def _format_size(self, size_bytes: int) -> str:
        """Formatta dimensione in formato human-readable"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"

    async def find_vm_dataset(
        self,
        hostname: str,
        vmid: int,
        vm_type: str = "qemu",
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> List[str]:
        """Trova i dataset ZFS associati a una VM"""
        
        # Prima ottieni la config per trovare gli storage
        success, config = await self.get_vm_config(hostname, vmid, vm_type, port, username, key_path)
        
        if not success:
            return []
        
        # Cerca pattern disco (es: scsi0: local-zfs:vm-100-disk-0)
        disk_pattern = r'(?:scsi|sata|virtio|ide|mp)\d+:\s*(\S+):(\S+)'
        disks = re.findall(disk_pattern, config)
        
        datasets = []
        
        for storage, disk_name in disks:
            # Trova il path ZFS dello storage
            result = await ssh_service.execute(
                hostname=hostname,
                command=f"pvesm status -storage {storage} 2>/dev/null | grep -E 'zfspool|dir'",
                port=port,
                username=username,
                key_path=key_path
            )
            
            if result.success and "zfspool" in result.stdout:
                # È uno storage ZFS, trova il dataset
                result2 = await ssh_service.execute(
                    hostname=hostname,
                    command=f"pvesm path {storage}:{disk_name} 2>/dev/null",
                    port=port,
                    username=username,
                    key_path=key_path
                )
                
                if result2.success:
                    # Output format: /dev/zvol/rpool/data/vm-100-disk-0
                    path = result2.stdout.strip()
                    if path.startswith("/dev/zvol/"):
                        dataset = path.replace("/dev/zvol/", "")
                        datasets.append(dataset)
                    elif path.startswith("/"):
                        # Potrebbe essere un dataset montato
                        result3 = await ssh_service.execute(
                            hostname=hostname,
                            command=f"zfs list -H -o name {path} 2>/dev/null",
                            port=port,
                            username=username,
                            key_path=key_path
                        )
                        if result3.success:
                            datasets.append(result3.stdout.strip())
        
        # Aggiungi anche il parent dataset se esiste (es: rpool/data)
        if datasets:
            parent = "/".join(datasets[0].split("/")[:-1])
            if parent and parent not in datasets:
                # Verifica se il parent contiene subvol per la VM
                result = await ssh_service.execute(
                    hostname=hostname,
                    command=f"zfs list -r -H -o name {parent} 2>/dev/null | grep -E 'vm-{vmid}|subvol-{vmid}'",
                    port=port,
                    username=username,
                    key_path=key_path
                )
                if result.success:
                    for line in result.stdout.strip().split('\n'):
                        if line and line not in datasets:
                            datasets.append(line)
        
        return list(set(datasets))
    
    async def ensure_zfs_storage(
        self,
        hostname: str,
        storage_name: str,
        zfs_pool: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Tuple[bool, str]:
        """
        Verifica/crea uno storage ZFS in Proxmox.
        Necessario per registrare VM con dischi in dataset personalizzati.
        """
        
        # Verifica se lo storage esiste già
        check_cmd = f"pvesm status -storage {storage_name} 2>/dev/null"
        result = await ssh_service.execute(
            hostname=hostname,
            command=check_cmd,
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success and storage_name in result.stdout:
            return True, f"Storage {storage_name} già esistente"
        
        # Crea lo storage ZFS
        # Il formato del pool può essere "pool" o "pool/dataset"
        create_cmd = f"pvesm add zfspool {storage_name} --pool {zfs_pool} --content images,rootdir --sparse 1"
        result = await ssh_service.execute(
            hostname=hostname,
            command=create_cmd,
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success or "already exists" in result.stderr:
            return True, f"Storage {storage_name} creato/verificato"
        else:
            return False, f"Errore creazione storage: {result.stderr}"

    async def register_vm(
        self,
        hostname: str,
        vmid: int,
        vm_type: str = "qemu",
        config_content: Optional[str] = None,
        source_storage: Optional[str] = None,
        dest_storage: Optional[str] = None,
        dest_zfs_pool: Optional[str] = None,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Tuple[bool, str]:
        """
        Registra una VM replicata su Proxmox
        
        Se config_content è fornito, crea il file di configurazione.
        Se dest_storage è specificato, sostituisce lo storage nella config.
        Se dest_zfs_pool è specificato, crea lo storage se non esiste.
        """
        
        if vm_type == "qemu":
            config_path = f"/etc/pve/qemu-server/{vmid}.conf"
        else:
            config_path = f"/etc/pve/lxc/{vmid}.conf"
        
        # Verifica che il VMID non sia già in uso
        check_cmd = f"qm status {vmid} 2>/dev/null || pct status {vmid} 2>/dev/null"
        result = await ssh_service.execute(
            hostname=hostname,
            command=check_cmd,
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success and ("status:" in result.stdout or "running" in result.stdout or "stopped" in result.stdout):
            return False, f"VMID {vmid} già in uso su questo nodo"
        
        # Se abbiamo un dest_storage e dest_zfs_pool, creiamo/verifichiamo lo storage
        if dest_storage and dest_zfs_pool:
            storage_ok, storage_msg = await self.ensure_zfs_storage(
                hostname=hostname,
                storage_name=dest_storage,
                zfs_pool=dest_zfs_pool,
                port=port,
                username=username,
                key_path=key_path
            )
            if not storage_ok:
                return False, f"Errore storage: {storage_msg}"
        
        if config_content:
            # Se abbiamo source_storage e dest_storage, sostituisci nella config
            if source_storage and dest_storage and source_storage != dest_storage:
                # Sostituisci il nome dello storage (es: local-zfs: -> replica-storage:)
                config_content = config_content.replace(f"{source_storage}:", f"{dest_storage}:")
            
            # Crea il file di configurazione
            cmd = f"""
mkdir -p $(dirname {config_path})
cat > {config_path} << 'VMCONF_EOF'
{config_content}
VMCONF_EOF
echo "Configuration created"
"""
            result = await ssh_service.execute(
                hostname=hostname,
                command=cmd,
                port=port,
                username=username,
                key_path=key_path
            )
            
            if not result.success:
                return False, f"Errore creazione config: {result.stderr}"
        
        # Verifica registrazione
        verify_cmd = f"{'qm' if vm_type == 'qemu' else 'pct'} status {vmid}"
        result = await ssh_service.execute(
            hostname=hostname,
            command=verify_cmd,
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success:
            return True, f"VM {vmid} registrata con successo"
        else:
            return False, f"Verifica fallita: {result.stderr}"
    
    async def unregister_vm(
        self,
        hostname: str,
        vmid: int,
        vm_type: str = "qemu",
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Tuple[bool, str]:
        """
        Rimuove la registrazione di una VM (senza eliminare i dati)
        """
        
        # Prima verifica che sia spenta
        cmd = "qm" if vm_type == "qemu" else "pct"
        
        result = await ssh_service.execute(
            hostname=hostname,
            command=f"{cmd} status {vmid}",
            port=port,
            username=username,
            key_path=key_path
        )
        
        if "running" in result.stdout:
            return False, "La VM deve essere spenta prima della rimozione"
        
        # Rimuovi solo il file config (non i dati)
        if vm_type == "qemu":
            config_path = f"/etc/pve/qemu-server/{vmid}.conf"
        else:
            config_path = f"/etc/pve/lxc/{vmid}.conf"
        
        result = await ssh_service.execute(
            hostname=hostname,
            command=f"rm -f {config_path}",
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success:
            return True, f"VM {vmid} deregistrata (dati mantenuti)"
        return False, result.stderr
    
    async def get_next_vmid(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> int:
        """Ottiene il prossimo VMID disponibile"""
        
        result = await ssh_service.execute(
            hostname=hostname,
            command="pvesh get /cluster/nextid",
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success:
            try:
                return int(result.stdout.strip())
            except ValueError:
                pass
        
        # Fallback: trova manualmente
        result = await ssh_service.execute(
            hostname=hostname,
            command="(qm list 2>/dev/null; pct list 2>/dev/null) | awk '{print $1}' | sort -n | tail -1",
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success and result.stdout.strip():
            try:
                return int(result.stdout.strip()) + 1
            except ValueError:
                pass
        
        return 100  # Default


# Singleton
proxmox_service = ProxmoxService()
