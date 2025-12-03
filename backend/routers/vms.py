"""
Router per gestione VM Proxmox
Con autenticazione
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from database import get_db, Node, VMRegistry, User
from services.proxmox_service import proxmox_service
from routers.auth import get_current_user, require_operator, log_audit

router = APIRouter()


# ============== Schemas ==============

class VMResponse(BaseModel):
    vmid: int
    name: str
    type: str  # qemu o lxc
    status: str


class VMDatasetResponse(BaseModel):
    vmid: int
    datasets: List[str]


class VMRegisterRequest(BaseModel):
    vmid: int
    vm_type: str = "qemu"
    config_content: Optional[str] = None


# ============== Helper Functions ==============

def check_node_access(user: User, node: Node) -> bool:
    """Verifica se l'utente ha accesso al nodo"""
    if user.role == "admin":
        return True
    if user.allowed_nodes is None:
        return True
    return node.id in user.allowed_nodes


# ============== Endpoints ==============

@router.get("/node/{node_id}", response_model=List[VMResponse])
async def get_node_vms(
    node_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene tutte le VM e container di un nodo"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    guests = await proxmox_service.get_all_guests(
        hostname=node.hostname,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    return [VMResponse(**g) for g in guests]


@router.get("/node/{node_id}/vm/{vmid}")
async def get_vm_details(
    node_id: int,
    vmid: int,
    vm_type: str = "qemu",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene i dettagli di una VM specifica"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    success, config = await proxmox_service.get_vm_config(
        hostname=node.hostname,
        vmid=vmid,
        vm_type=vm_type,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="VM non trovata")
    
    return {"vmid": vmid, "type": vm_type, "config": config}


@router.get("/node/{node_id}/vm/{vmid}/datasets", response_model=VMDatasetResponse)
async def get_vm_datasets(
    node_id: int,
    vmid: int,
    vm_type: str = "qemu",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Trova i dataset ZFS associati a una VM"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    datasets = await proxmox_service.find_vm_dataset(
        hostname=node.hostname,
        vmid=vmid,
        vm_type=vm_type,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    return VMDatasetResponse(vmid=vmid, datasets=datasets)


@router.get("/node/{node_id}/vm/{vmid}/disks")
async def get_vm_disks(
    node_id: int,
    vmid: int,
    vm_type: str = "qemu",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Ottiene tutti i dischi di una VM con dimensioni e dataset ZFS.
    Usato per la creazione di job di replica VM-centrici.
    """
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    disks = await proxmox_service.get_vm_disks_with_size(
        hostname=node.hostname,
        vmid=vmid,
        vm_type=vm_type,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    # Calcola dimensione totale
    total_size = sum(d.get("size_bytes", 0) for d in disks)
    
    return {
        "vmid": vmid,
        "vm_type": vm_type,
        "disks": disks,
        "total_disks": len(disks),
        "total_size_bytes": total_size,
        "total_size": proxmox_service._format_size(total_size)
    }


@router.post("/node/{node_id}/register")
async def register_vm(
    node_id: int,
    vm_data: VMRegisterRequest,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Registra una VM su un nodo (dopo replica)"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    success, message = await proxmox_service.register_vm(
        hostname=node.hostname,
        vmid=vm_data.vmid,
        vm_type=vm_data.vm_type,
        config_content=vm_data.config_content,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    if success:
        log_audit(
            db, user.id, "vm_registered", "vm",
            resource_id=vm_data.vmid,
            details=f"Registered VM {vm_data.vmid} on {node.name}",
            ip_address=request.client.host if request.client else None
        )
    
    return {"success": success, "message": message}


@router.delete("/node/{node_id}/unregister/{vmid}")
async def unregister_vm(
    node_id: int,
    vmid: int,
    vm_type: str = "qemu",
    request: Request = None,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Rimuove la registrazione di una VM (senza eliminare i dati)"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    success, message = await proxmox_service.unregister_vm(
        hostname=node.hostname,
        vmid=vmid,
        vm_type=vm_type,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    if success:
        log_audit(
            db, user.id, "vm_unregistered", "vm",
            resource_id=vmid,
            details=f"Unregistered VM {vmid} on {node.name}",
            ip_address=request.client.host if request.client else None
        )
    
    return {"success": success, "message": message}


@router.get("/node/{node_id}/next-vmid")
async def get_next_vmid(
    node_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene il prossimo VMID disponibile"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    vmid = await proxmox_service.get_next_vmid(
        hostname=node.hostname,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    return {"next_vmid": vmid}


# ============== VM Registry ==============

@router.get("/registry")
async def list_vm_registry(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Lista le VM registrate nel sistema"""
    vms = db.query(VMRegistry).all()
    return vms


@router.get("/registry/{vm_id}")
async def get_vm_registry(
    vm_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene info di una VM dal registro"""
    vm = db.query(VMRegistry).filter(VMRegistry.vm_id == vm_id).first()
    if not vm:
        raise HTTPException(status_code=404, detail="VM non trovata nel registro")
    return vm
