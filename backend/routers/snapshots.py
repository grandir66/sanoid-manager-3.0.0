"""
Router per gestione snapshot ZFS e configurazione Sanoid
Con autenticazione
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from database import get_db, Node, Dataset, User, JobLog
from services.ssh_service import ssh_service
from services.sanoid_service import sanoid_service, DEFAULT_TEMPLATES
from routers.auth import get_current_user, require_operator, log_audit

router = APIRouter()


# ============== Schemas ==============

class DatasetConfigUpdate(BaseModel):
    sanoid_enabled: bool = False
    sanoid_template: str = "default"
    hourly: int = 24
    daily: int = 30
    weekly: int = 4
    monthly: int = 12
    yearly: int = 0
    autosnap: bool = True
    autoprune: bool = True


class SnapshotCreate(BaseModel):
    name: str
    recursive: bool = False


class SnapshotResponse(BaseModel):
    full_name: str
    dataset: str
    snapshot: str
    used: str
    creation: str


class TemplateResponse(BaseModel):
    name: str
    hourly: int
    daily: int
    weekly: int
    monthly: int
    yearly: int
    autosnap: bool
    autoprune: bool


# ============== Helper Functions ==============

def check_node_access(user: User, node: Node) -> bool:
    """Verifica se l'utente ha accesso al nodo"""
    if user.role == "admin":
        return True
    if user.allowed_nodes is None:
        return True
    return node.id in user.allowed_nodes


# ============== Endpoints ==============

@router.get("/templates", response_model=List[TemplateResponse])
async def get_templates(user: User = Depends(get_current_user)):
    """Ottiene i template Sanoid disponibili"""
    return [
        TemplateResponse(
            name=name,
            hourly=t.hourly,
            daily=t.daily,
            weekly=t.weekly,
            monthly=t.monthly,
            yearly=t.yearly,
            autosnap=t.autosnap,
            autoprune=t.autoprune
        )
        for name, t in DEFAULT_TEMPLATES.items()
    ]


@router.get("/node/{node_id}", response_model=List[SnapshotResponse])
async def get_node_snapshots(
    node_id: int,
    dataset: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene gli snapshot di un nodo"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    snapshots = await ssh_service.get_snapshots(
        hostname=node.hostname,
        dataset=dataset,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    return [SnapshotResponse(**s) for s in snapshots]


@router.post("/node/{node_id}")
async def create_snapshot(
    node_id: int,
    dataset: str,
    snapshot_data: SnapshotCreate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Crea uno snapshot manuale"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    result = await ssh_service.create_snapshot(
        hostname=node.hostname,
        dataset=dataset,
        snapshot_name=snapshot_data.name,
        recursive=snapshot_data.recursive,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    if result.success:
        log_audit(
            db, user.id, "snapshot_created", "snapshot",
            details=f"Created {dataset}@{snapshot_data.name} on {node.name}",
            ip_address=request.client.host if request.client else None
        )
        return {"success": True, "message": f"Snapshot {dataset}@{snapshot_data.name} creato"}
    else:
        return {"success": False, "message": result.stderr}


@router.delete("/node/{node_id}")
async def delete_snapshot(
    node_id: int,
    full_name: str,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Elimina uno snapshot"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    result = await ssh_service.delete_snapshot(
        hostname=node.hostname,
        full_snapshot_name=full_name,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    if result.success:
        log_audit(
            db, user.id, "snapshot_deleted", "snapshot",
            details=f"Deleted {full_name} on {node.name}",
            ip_address=request.client.host if request.client else None
        )
        return {"success": True, "message": f"Snapshot {full_name} eliminato"}
    else:
        return {"success": False, "message": result.stderr}


@router.put("/dataset/{dataset_id}/config")
async def update_dataset_config(
    dataset_id: int,
    config: DatasetConfigUpdate,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Aggiorna la configurazione Sanoid di un dataset"""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset non trovato")
    
    node = db.query(Node).filter(Node.id == dataset.node_id).first()
    if node and not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    for key, value in config.dict().items():
        setattr(dataset, key, value)
    
    dataset.last_updated = datetime.utcnow()
    db.commit()
    
    return {"message": "Configurazione aggiornata"}


@router.post("/node/{node_id}/apply-config")
async def apply_sanoid_config(
    node_id: int,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Applica la configurazione Sanoid su un nodo"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    # Ottieni dataset configurati
    datasets = db.query(Dataset).filter(Dataset.node_id == node_id).all()
    
    # Genera configurazione
    dataset_configs = [
        {
            "name": ds.name,
            "sanoid_enabled": ds.sanoid_enabled,
            "sanoid_template": ds.sanoid_template,
            "hourly": ds.hourly,
            "daily": ds.daily,
            "weekly": ds.weekly,
            "monthly": ds.monthly,
            "yearly": ds.yearly,
            "autosnap": ds.autosnap,
            "autoprune": ds.autoprune
        }
        for ds in datasets
    ]
    
    config_content = sanoid_service.generate_config(dataset_configs)
    
    # Applica sul nodo
    result = await sanoid_service.set_config(
        hostname=node.hostname,
        config_content=config_content,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    if result.success:
        log_audit(
            db, user.id, "sanoid_config_applied", "node",
            resource_id=node_id,
            details=f"Applied Sanoid config on {node.name}",
            ip_address=request.client.host if request.client else None
        )
        return {"success": True, "message": "Configurazione applicata"}
    else:
        return {"success": False, "message": result.stderr}


@router.post("/node/{node_id}/run-sanoid")
async def run_sanoid(
    node_id: int,
    cron: bool = True,
    prune: bool = False,
    request: Request = None,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Esegue Sanoid manualmente su un nodo"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    result = await sanoid_service.run_sanoid(
        hostname=node.hostname,
        cron=cron,
        prune=prune,
        verbose=True,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    # Log operazione
    log_entry = JobLog(
        job_type="snapshot",
        node_name=node.name,
        status="success" if result.success else "failed",
        message="Sanoid manual run",
        output=result.stdout,
        error=result.stderr if not result.success else None,
        triggered_by=user.id
    )
    db.add(log_entry)
    db.commit()
    
    return {
        "success": result.success,
        "output": result.stdout,
        "error": result.stderr
    }


@router.get("/node/{node_id}/sanoid-config")
async def get_sanoid_config(
    node_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene la configurazione Sanoid attuale di un nodo"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    success, config = await sanoid_service.get_config(
        hostname=node.hostname,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    return {
        "success": success,
        "config": config
    }


@router.get("/stats/node/{node_id}")
async def get_node_snapshot_stats(
    node_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene statistiche sugli snapshot di un nodo"""
    node = db.query(Node).filter(Node.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    if not check_node_access(user, node):
        raise HTTPException(status_code=403, detail="Accesso negato a questo nodo")
    
    snapshots = await ssh_service.get_snapshots(
        hostname=node.hostname,
        port=node.ssh_port,
        username=node.ssh_user,
        key_path=node.ssh_key_path
    )
    
    # Conta per tipo (autosnap-hourly, autosnap-daily, etc.)
    type_counts = {}
    for snap in snapshots:
        name = snap["snapshot"]
        if "autosnap" in name:
            if "hourly" in name:
                snap_type = "hourly"
            elif "daily" in name:
                snap_type = "daily"
            elif "weekly" in name:
                snap_type = "weekly"
            elif "monthly" in name:
                snap_type = "monthly"
            elif "yearly" in name:
                snap_type = "yearly"
            else:
                snap_type = "other"
        else:
            snap_type = "manual"
        
        type_counts[snap_type] = type_counts.get(snap_type, 0) + 1
    
    # Dataset con più snapshot
    dataset_counts = {}
    for snap in snapshots:
        ds = snap["dataset"]
        dataset_counts[ds] = dataset_counts.get(ds, 0) + 1
    
    top_datasets = sorted(dataset_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    
    return {
        "total_snapshots": len(snapshots),
        "by_type": type_counts,
        "top_datasets": [{"dataset": d, "count": c} for d, c in top_datasets]
    }
