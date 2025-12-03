"""
SSH Keys Router - API per gestione chiavi SSH
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

from database import get_db, Node
from services.ssh_key_service import ssh_key_service, SSHKeyInfo

router = APIRouter(prefix="/ssh-keys", tags=["ssh-keys"])


class GenerateKeyRequest(BaseModel):
    key_type: str = "rsa"
    bits: int = 4096
    comment: str = "sanoid-manager"
    overwrite: bool = False


class DistributeKeyRequest(BaseModel):
    node_ids: Optional[List[int]] = None  # Se None, distribuisci a tutti
    password: Optional[str] = None  # Password per autenticazione iniziale


class TestConnectionRequest(BaseModel):
    node_ids: Optional[List[int]] = None  # Se None, testa tutti


class KeyInfoResponse(BaseModel):
    exists: bool
    public_key: Optional[str] = None
    key_type: Optional[str] = None
    fingerprint: Optional[str] = None
    comment: Optional[str] = None


class DistributionResultResponse(BaseModel):
    host: str
    success: bool
    message: str
    already_present: bool = False


class TestResultResponse(BaseModel):
    node_id: Optional[int] = None
    node_name: Optional[str] = None
    host: str
    success: bool
    message: str


@router.get("/info", response_model=KeyInfoResponse)
async def get_key_info():
    """Ottiene informazioni sulla chiave SSH locale"""
    info = ssh_key_service.get_key_info()
    return KeyInfoResponse(
        exists=info.exists,
        public_key=info.public_key,
        key_type=info.key_type,
        fingerprint=info.fingerprint,
        comment=info.comment
    )


@router.post("/generate")
async def generate_key(request: GenerateKeyRequest):
    """Genera una nuova coppia di chiavi SSH"""
    success, message = ssh_key_service.generate_key(
        key_type=request.key_type,
        bits=request.bits,
        comment=request.comment,
        overwrite=request.overwrite
    )
    
    if not success:
        raise HTTPException(status_code=400, detail=message)
    
    # Ritorna anche le info della nuova chiave
    info = ssh_key_service.get_key_info()
    return {
        "success": True,
        "message": message,
        "key_info": {
            "exists": info.exists,
            "public_key": info.public_key,
            "key_type": info.key_type,
            "fingerprint": info.fingerprint,
            "comment": info.comment
        }
    }


@router.post("/distribute", response_model=List[DistributionResultResponse])
async def distribute_key(request: DistributeKeyRequest, db: Session = Depends(get_db)):
    """Distribuisce la chiave pubblica ai nodi selezionati o a tutti"""
    
    # Ottieni nodi dal database
    if request.node_ids:
        nodes = db.query(Node).filter(Node.id.in_(request.node_ids)).all()
    else:
        nodes = db.query(Node).all()
    
    if not nodes:
        raise HTTPException(status_code=404, detail="Nessun nodo trovato")
    
    # Prepara lista nodi per il servizio
    node_list = [
        {
            "id": node.id,
            "name": node.name,
            "host": node.host,
            "port": node.port or 22,
            "username": node.username or "root"
        }
        for node in nodes
    ]
    
    # Distribuisci chiave
    results = await ssh_key_service.distribute_key_to_all_nodes(
        nodes=node_list,
        password=request.password
    )
    
    return [
        DistributionResultResponse(
            host=r.host,
            success=r.success,
            message=r.message,
            already_present=r.already_present
        )
        for r in results
    ]


@router.post("/test", response_model=List[TestResultResponse])
async def test_connections(request: TestConnectionRequest, db: Session = Depends(get_db)):
    """Testa la connettività SSH con chiave a tutti i nodi o a nodi selezionati"""
    
    # Ottieni nodi dal database
    if request.node_ids:
        nodes = db.query(Node).filter(Node.id.in_(request.node_ids)).all()
    else:
        nodes = db.query(Node).all()
    
    if not nodes:
        raise HTTPException(status_code=404, detail="Nessun nodo trovato")
    
    # Prepara lista nodi per il servizio
    node_list = [
        {
            "id": node.id,
            "name": node.name,
            "host": node.host,
            "port": node.port or 22,
            "username": node.username or "root"
        }
        for node in nodes
    ]
    
    # Testa connessioni
    results = await ssh_key_service.test_all_nodes(nodes=node_list)
    
    return [
        TestResultResponse(
            node_id=r.get("node_id"),
            node_name=r.get("node_name"),
            host=r.get("host"),
            success=r.get("success"),
            message=r.get("message")
        )
        for r in results
    ]


@router.get("/authorized-keys")
async def get_authorized_keys():
    """Ottiene le chiavi autorizzate sul server locale"""
    keys = ssh_key_service.get_authorized_keys()
    return {"keys": keys, "count": len(keys)}


@router.post("/distribute-single/{node_id}")
async def distribute_key_to_single_node(
    node_id: int,
    password: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Distribuisce la chiave a un singolo nodo"""
    node = db.query(Node).filter(Node.id == node_id).first()
    
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    result = await ssh_key_service.distribute_key_to_host(
        hostname=node.host,
        port=node.port or 22,
        username=node.username or "root",
        password=password
    )
    
    return {
        "host": result.host,
        "success": result.success,
        "message": result.message,
        "already_present": result.already_present
    }


@router.post("/test-single/{node_id}")
async def test_single_connection(node_id: int, db: Session = Depends(get_db)):
    """Testa la connessione SSH a un singolo nodo"""
    node = db.query(Node).filter(Node.id == node_id).first()
    
    if not node:
        raise HTTPException(status_code=404, detail="Nodo non trovato")
    
    success, message = await ssh_key_service.test_key_auth(
        hostname=node.host,
        port=node.port or 22,
        username=node.username or "root"
    )
    
    return {
        "node_id": node.id,
        "node_name": node.name,
        "host": node.host,
        "success": success,
        "message": message
    }


@router.post("/force-sync")
async def force_sync_keys(request: DistributeKeyRequest, db: Session = Depends(get_db)):
    """
    Forza la sincronizzazione delle chiavi SSH a tutti i nodi.
    Utile dopo un reinstallazione o quando le chiavi non sono allineate.
    """
    
    # Verifica che esista una chiave locale
    key_info = ssh_key_service.get_key_info()
    if not key_info.exists:
        # Genera una nuova chiave
        success, msg = ssh_key_service.generate_key()
        if not success:
            raise HTTPException(status_code=500, detail=f"Impossibile generare chiave: {msg}")
        key_info = ssh_key_service.get_key_info()
    
    # Ottieni tutti i nodi
    nodes = db.query(Node).all()
    
    if not nodes:
        return {
            "success": True,
            "message": "Nessun nodo configurato",
            "results": [],
            "public_key": key_info.public_key
        }
    
    # Distribuisci a tutti
    node_list = [
        {
            "id": node.id,
            "name": node.name,
            "host": node.host,
            "port": node.port or 22,
            "username": node.username or "root"
        }
        for node in nodes
    ]
    
    results = await ssh_key_service.distribute_key_to_all_nodes(
        nodes=node_list,
        password=request.password
    )
    
    # Statistiche
    success_count = sum(1 for r in results if r.success)
    failed_count = len(results) - success_count
    
    return {
        "success": failed_count == 0,
        "message": f"Distribuzione completata: {success_count} OK, {failed_count} falliti",
        "results": [
            {
                "host": r.host,
                "success": r.success,
                "message": r.message,
                "already_present": r.already_present
            }
            for r in results
        ],
        "public_key": key_info.public_key,
        "stats": {
            "total": len(results),
            "success": success_count,
            "failed": failed_count
        }
    }

