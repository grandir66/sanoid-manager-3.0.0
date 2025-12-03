"""
Router per gestione job di sincronizzazione Syncoid
Con autenticazione e autorizzazione
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel

from database import get_db, Node, SyncJob, JobLog, User
from services.syncoid_service import syncoid_service
from services.scheduler import scheduler_service
from routers.auth import get_current_user, require_operator, require_admin, log_audit

router = APIRouter()


# ============== Helper Function per notifiche email ==============

async def send_job_email_notification(
    db_session,
    job_name: str,
    status: str,
    source: str,
    destination: str,
    duration: int = None,
    error: str = None,
    details: str = None
):
    """Invia notifica email per un job di replica se configurato."""
    from database import NotificationConfig
    from services.email_service import email_service
    
    # Carica configurazione notifiche
    config = db_session.query(NotificationConfig).first()
    if not config or not config.smtp_enabled:
        return
    
    # Verifica se notificare per questo status
    if status == "success" and not config.notify_on_success:
        return
    if status == "failed" and not config.notify_on_failure:
        return
    if status == "warning" and not config.notify_on_warning:
        return
    
    # Verifica configurazione SMTP
    if not config.smtp_host or not config.smtp_to:
        return
    
    # Configura email service
    email_service.configure(
        host=config.smtp_host,
        port=config.smtp_port or 587,
        user=config.smtp_user,
        password=config.smtp_password,
        from_addr=config.smtp_from,
        to_addrs=config.smtp_to,
        subject_prefix=config.smtp_subject_prefix or "[Sanoid Manager]",
        use_tls=config.smtp_tls
    )
    
    # Invia notifica
    email_service.send_job_notification(
        job_name=job_name,
        status=status,
        source=source,
        destination=destination,
        duration=duration,
        error=error,
        details=details[:1000] if details else None  # Limita dettagli
    )


# ============== Helper Function per esecuzione job ==============

async def execute_sync_job_task(job_id: int, triggered_by_user_id: int = None):
    """
    Funzione standalone per eseguire un job di sincronizzazione.
    Può essere usata da più endpoint come task in background.
    """
    from database import SessionLocal, SyncJob, Node, JobLog
    from services.syncoid_service import syncoid_service
    from services.ssh_service import ssh_service
    import traceback
    
    db_session = SessionLocal()
    log_entry = None
    job_record = None
    
    try:
        # Recupera job e nodi dal database
        job = db_session.query(SyncJob).filter(SyncJob.id == job_id).first()
        if not job:
            return
        
        source_node = db_session.query(Node).filter(Node.id == job.source_node_id).first()
        dest_node = db_session.query(Node).filter(Node.id == job.dest_node_id).first()
        
        if not source_node or not dest_node:
            return
        
        # Crea log entry
        log_entry = JobLog(
            job_type="sync",
            job_id=job_id,
            node_name=f"{source_node.name} -> {dest_node.name}",
            dataset=f"{job.source_dataset} -> {job.dest_dataset}",
            status="started",
            triggered_by=triggered_by_user_id
        )
        db_session.add(log_entry)
        db_session.commit()
        
        # Aggiorna stato
        job_record = db_session.query(SyncJob).filter(SyncJob.id == job_id).first()
        job_record.last_status = "running"
        db_session.commit()
        
        # Crea dataset parent sulla destinazione se non esiste
        dest_parent = "/".join(job.dest_dataset.split("/")[:-1])
        if dest_parent:
            check_result = await ssh_service.execute(
                hostname=dest_node.hostname,
                command=f"zfs list -H -o name {dest_parent} 2>/dev/null || echo 'NOT_EXISTS'",
                port=dest_node.ssh_port,
                username=dest_node.ssh_user,
                key_path=dest_node.ssh_key_path,
                timeout=30
            )
            
            if "NOT_EXISTS" in check_result.stdout or not check_result.success:
                create_result = await ssh_service.execute(
                    hostname=dest_node.hostname,
                    command=f"zfs create -p {dest_parent}",
                    port=dest_node.ssh_port,
                    username=dest_node.ssh_user,
                    key_path=dest_node.ssh_key_path,
                    timeout=30
                )
                if create_result.success:
                    log_entry.message = f"Creato dataset parent: {dest_parent}"
                else:
                    log_entry.message = f"Attenzione: impossibile creare {dest_parent}: {create_result.stderr}"
        
        # Esegui sync
        result = await syncoid_service.run_sync(
            executor_host=source_node.hostname,
            source_host=None,
            source_dataset=job.source_dataset,
            dest_host=dest_node.hostname,
            dest_dataset=job.dest_dataset,
            dest_user=dest_node.ssh_user,
            dest_port=dest_node.ssh_port,
            dest_key=dest_node.ssh_key_path,
            executor_port=source_node.ssh_port,
            executor_user=source_node.ssh_user,
            executor_key=source_node.ssh_key_path,
            recursive=job.recursive,
            compress=job.compress or "lz4",
            mbuffer_size=job.mbuffer_size or "128M",
            no_sync_snap=job.no_sync_snap,
            force_delete=job.force_delete,
            extra_args=job.extra_args or ""
        )
        
        # Aggiorna job e log
        job_record.last_run = datetime.utcnow()
        job_record.last_duration = result["duration"]
        job_record.last_transferred = result.get("transferred")
        job_record.run_count += 1
        
        if result["success"]:
            job_record.last_status = "success"
            job_record.consecutive_failures = 0
            log_entry.status = "success"
            log_entry.message = (log_entry.message or "") + " Sincronizzazione completata"
            
            # Registrazione VM se richiesta
            if job.register_vm and job.vm_id:
                from services.proxmox_service import proxmox_service
                target_vmid = job.dest_vm_id if job.dest_vm_id else job.vm_id
                
                try:
                    vm_type = job.vm_type or "qemu"
                    config_path = f"/etc/pve/qemu-server/{job.vm_id}.conf" if vm_type == "qemu" else f"/etc/pve/lxc/{job.vm_id}.conf"
                    
                    config_result = await ssh_service.execute(
                        hostname=source_node.hostname,
                        command=f"cat {config_path} 2>/dev/null",
                        port=source_node.ssh_port,
                        username=source_node.ssh_user,
                        key_path=source_node.ssh_key_path,
                        timeout=30
                    )
                    
                    if config_result.success and config_result.stdout.strip():
                        dest_zfs_pool = "/".join(job.dest_dataset.split("/")[:-1]) or job.dest_dataset.split("/")[0]
                        
                        success, msg = await proxmox_service.register_vm(
                            hostname=dest_node.hostname,
                            vmid=target_vmid,
                            vm_type=vm_type,
                            config_content=config_result.stdout,
                            source_storage=job.source_storage,
                            dest_storage=job.dest_storage,
                            dest_zfs_pool=dest_zfs_pool,
                            port=dest_node.ssh_port,
                            username=dest_node.ssh_user,
                            key_path=dest_node.ssh_key_path
                        )
                        
                        if success:
                            vm_info = f"VM {target_vmid}" + (f" (da {job.vm_id})" if target_vmid != job.vm_id else "")
                            log_entry.message += f" | {vm_info} registrata"
                        else:
                            log_entry.message += f" | Registrazione VM fallita: {msg}"
                    else:
                        log_entry.message += f" | Config VM non trovata su sorgente"
                except Exception as e:
                    log_entry.message += f" | Errore registrazione VM: {str(e)}"
        else:
            job_record.last_status = "failed"
            job_record.error_count += 1
            job_record.consecutive_failures += 1
            log_entry.status = "failed"
            error_msg = result.get("error", "")
            if result.get("output") and "error" in result.get("output", "").lower():
                error_msg = f"{error_msg}\n\nOutput:\n{result.get('output')}" if error_msg else result.get("output")
            error_msg = f"Comando: {result.get('command', 'N/A')}\n\n{error_msg}" if error_msg else f"Comando: {result.get('command', 'N/A')}\nErrore sconosciuto"
            log_entry.error = error_msg
        
        log_entry.output = result.get("output")
        log_entry.duration = result["duration"]
        log_entry.transferred = result.get("transferred")
        log_entry.completed_at = datetime.utcnow()
        
        db_session.commit()
        
        # Invia notifica email se configurata
        try:
            await send_job_email_notification(
                db_session=db_session,
                job_name=job.name,
                status="success" if result["success"] else "failed",
                source=f"{source_node.name}:{job.source_dataset}",
                destination=f"{dest_node.name}:{job.dest_dataset}",
                duration=result["duration"],
                error=result.get("error") if not result["success"] else None,
                details=result.get("output")
            )
        except Exception as email_err:
            # Non bloccare se l'email fallisce
            import logging
            logging.getLogger(__name__).warning(f"Errore invio notifica email: {email_err}")
        
    except Exception as e:
        if log_entry:
            log_entry.status = "failed"
            log_entry.error = f"Eccezione Python: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            log_entry.completed_at = datetime.utcnow()
        
        if job_record:
            try:
                job_record.last_status = "failed"
                job_record.error_count += 1
                job_record.consecutive_failures += 1
            except:
                pass
        
        try:
            db_session.commit()
        except:
            pass
    finally:
        db_session.close()


# ============== Schemas ==============

class SyncJobCreate(BaseModel):
    name: str
    source_node_id: int
    source_dataset: str
    dest_node_id: int
    dest_dataset: str
    recursive: bool = False
    compress: str = "lz4"
    mbuffer_size: str = "128M"
    no_sync_snap: bool = False
    force_delete: bool = False
    extra_args: Optional[str] = None
    schedule: Optional[str] = None  # cron format
    register_vm: bool = False
    vm_id: Optional[int] = None
    dest_vm_id: Optional[int] = None  # ID VM destinazione (se diverso da sorgente)
    vm_type: Optional[str] = None
    vm_name: Optional[str] = None
    retry_on_failure: bool = True
    max_retries: int = 3


class SyncJobUpdate(BaseModel):
    name: Optional[str] = None
    source_dataset: Optional[str] = None
    dest_dataset: Optional[str] = None
    recursive: Optional[bool] = None
    compress: Optional[str] = None
    mbuffer_size: Optional[str] = None
    no_sync_snap: Optional[bool] = None
    force_delete: Optional[bool] = None
    extra_args: Optional[str] = None
    schedule: Optional[str] = None
    is_active: Optional[bool] = None
    register_vm: Optional[bool] = None
    vm_id: Optional[int] = None
    dest_vm_id: Optional[int] = None
    vm_type: Optional[str] = None
    vm_name: Optional[str] = None
    retry_on_failure: Optional[bool] = None
    max_retries: Optional[int] = None


class SyncJobResponse(BaseModel):
    id: int
    name: str
    source_node_id: int
    source_dataset: str
    dest_node_id: int
    dest_dataset: str
    recursive: bool
    compress: Optional[str]
    mbuffer_size: Optional[str]
    no_sync_snap: bool
    force_delete: bool
    extra_args: Optional[str]
    schedule: Optional[str]
    is_active: bool
    register_vm: bool
    vm_id: Optional[int]
    dest_vm_id: Optional[int]
    vm_type: Optional[str]
    vm_name: Optional[str]
    vm_group_id: Optional[str]
    disk_name: Optional[str]
    retry_on_failure: bool
    max_retries: int
    last_run: Optional[datetime]
    last_status: Optional[str]
    last_duration: Optional[int]
    last_transferred: Optional[str]
    run_count: int
    error_count: int
    consecutive_failures: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class SyncJobResponseWithNodes(SyncJobResponse):
    source_node_name: Optional[str] = None
    dest_node_name: Optional[str] = None


# ============== Helper Functions ==============

def check_job_access(user: User, job: SyncJob, db: Session) -> bool:
    """Verifica se l'utente ha accesso al job"""
    if user.role == "admin":
        return True
    
    if user.allowed_nodes is None:
        return True
    
    # Deve avere accesso sia al nodo sorgente che destinazione
    return (job.source_node_id in user.allowed_nodes and 
            job.dest_node_id in user.allowed_nodes)


# ============== Endpoints ==============

@router.get("/", response_model=List[SyncJobResponseWithNodes])
async def list_sync_jobs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Lista tutti i job di sincronizzazione"""
    jobs = db.query(SyncJob).all()
    
    result = []
    for job in jobs:
        if not check_job_access(user, job, db):
            continue
            
        job_dict = SyncJobResponse.model_validate(job).model_dump()
        
        source_node = db.query(Node).filter(Node.id == job.source_node_id).first()
        dest_node = db.query(Node).filter(Node.id == job.dest_node_id).first()
        
        job_dict["source_node_name"] = source_node.name if source_node else None
        job_dict["dest_node_name"] = dest_node.name if dest_node else None
        
        result.append(SyncJobResponseWithNodes(**job_dict))
    
    return result


@router.post("/", response_model=SyncJobResponse)
async def create_sync_job(
    job: SyncJobCreate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Crea un nuovo job di sincronizzazione"""
    
    # Verifica nodi esistenti
    source_node = db.query(Node).filter(Node.id == job.source_node_id).first()
    dest_node = db.query(Node).filter(Node.id == job.dest_node_id).first()
    
    if not source_node:
        raise HTTPException(status_code=400, detail="Nodo sorgente non trovato")
    if not dest_node:
        raise HTTPException(status_code=400, detail="Nodo destinazione non trovato")
    
    # Verifica accesso ai nodi
    if user.allowed_nodes is not None:
        if job.source_node_id not in user.allowed_nodes:
            raise HTTPException(status_code=403, detail="Accesso negato al nodo sorgente")
        if job.dest_node_id not in user.allowed_nodes:
            raise HTTPException(status_code=403, detail="Accesso negato al nodo destinazione")
    
    db_job = SyncJob(**job.dict(), created_by=user.id)
    db.add(db_job)
    
    log_audit(
        db, user.id, "sync_job_created", "sync_job",
        details=f"Created job: {job.name}",
        ip_address=request.client.host if request.client else None
    )
    
    db.commit()
    db.refresh(db_job)
    
    # Aggiorna scheduler
    if db_job.schedule:
        scheduler_service.update_job_schedule(db_job.id, db_job.schedule)
    
    return db_job


class VMReplicaCreate(BaseModel):
    """Schema per creare replica completa di una VM"""
    vm_id: int
    vm_type: str = "qemu"
    vm_name: Optional[str] = None
    source_node_id: int
    dest_node_id: int
    dest_pool: str  # Pool ZFS destinazione
    dest_subfolder: str = "replica"  # Sottocartella (es: replica)
    dest_storage: Optional[str] = None  # Nome storage Proxmox destinazione (se vuoto, usa dest_subfolder)
    dest_vm_id: Optional[int] = None  # ID VM destinazione se diverso
    schedule: Optional[str] = None
    compress: str = "lz4"
    recursive: bool = False
    register_vm: bool = True
    disks: List[dict] = []  # Lista dischi da replicare (se vuota, replica tutti)


@router.post("/vm-replica")
async def create_vm_replica_jobs(
    vm_data: VMReplicaCreate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """
    Crea job di replica per tutti i dischi di una VM.
    Ritorna la lista dei job creati.
    """
    import uuid
    from services.proxmox_service import proxmox_service
    
    # Verifica nodi
    source_node = db.query(Node).filter(Node.id == vm_data.source_node_id).first()
    dest_node = db.query(Node).filter(Node.id == vm_data.dest_node_id).first()
    
    if not source_node:
        raise HTTPException(status_code=400, detail="Nodo sorgente non trovato")
    if not dest_node:
        raise HTTPException(status_code=400, detail="Nodo destinazione non trovato")
    
    # Verifica accesso
    if user.allowed_nodes is not None:
        if vm_data.source_node_id not in user.allowed_nodes:
            raise HTTPException(status_code=403, detail="Accesso negato al nodo sorgente")
        if vm_data.dest_node_id not in user.allowed_nodes:
            raise HTTPException(status_code=403, detail="Accesso negato al nodo destinazione")
    
    # Ottieni i dischi della VM se non specificati
    if not vm_data.disks:
        disks = await proxmox_service.get_vm_disks_with_size(
            hostname=source_node.hostname,
            vmid=vm_data.vm_id,
            vm_type=vm_data.vm_type,
            port=source_node.ssh_port,
            username=source_node.ssh_user,
            key_path=source_node.ssh_key_path
        )
    else:
        disks = vm_data.disks
    
    if not disks:
        raise HTTPException(status_code=400, detail="Nessun disco trovato per questa VM")
    
    # Genera un group_id univoco per tutti i job di questa VM
    vm_group_id = str(uuid.uuid4())[:8]
    
    # Determina VMID destinazione
    dest_vmid = vm_data.dest_vm_id if vm_data.dest_vm_id else vm_data.vm_id
    
    created_jobs = []
    total_size = 0
    
    for disk in disks:
        if not disk.get("dataset"):
            continue
        
        # Costruisci il path destinazione
        # Es: dest_pool/replica/vm-100-disk-0
        source_dataset = disk["dataset"]
        dataset_name = source_dataset.split("/")[-1]  # es: vm-100-disk-0
        
        if vm_data.dest_subfolder:
            dest_dataset = f"{vm_data.dest_pool}/{vm_data.dest_subfolder}/{dataset_name}"
        else:
            dest_dataset = f"{vm_data.dest_pool}/{dataset_name}"
        
        # Nome job: VM-100 scsi0 -> Node2
        job_name = f"VM-{vm_data.vm_id} {disk.get('disk_name', 'disk')} → {dest_node.name}"
        
        # Crea il job
        # Determina storage sorgente (dal disco) e destinazione
        source_storage = disk.get("storage", None)  # es: local-zfs
        # Se dest_storage non specificato, usa dest_subfolder come nome storage
        dest_storage = vm_data.dest_storage or (vm_data.dest_subfolder if vm_data.dest_subfolder else vm_data.dest_pool)
        
        db_job = SyncJob(
            name=job_name,
            source_node_id=vm_data.source_node_id,
            source_dataset=source_dataset,
            dest_node_id=vm_data.dest_node_id,
            dest_dataset=dest_dataset,
            recursive=vm_data.recursive,
            compress=vm_data.compress,
            schedule=vm_data.schedule,
            register_vm=vm_data.register_vm,
            vm_id=vm_data.vm_id,
            dest_vm_id=dest_vmid if dest_vmid != vm_data.vm_id else None,
            vm_type=vm_data.vm_type,
            vm_name=vm_data.vm_name,
            vm_group_id=vm_group_id,
            disk_name=disk.get("disk_name"),
            source_storage=source_storage,
            dest_storage=dest_storage,
            created_by=user.id,
            is_active=True
        )
        
        db.add(db_job)
        total_size += disk.get("size_bytes", 0)
        
        created_jobs.append({
            "disk_name": disk.get("disk_name"),
            "source_dataset": source_dataset,
            "dest_dataset": dest_dataset,
            "size": disk.get("size", "N/A")
        })
    
    db.commit()
    
    # Log audit
    log_audit(
        db, user.id, "vm_replica_created", "sync_job",
        details=f"Created {len(created_jobs)} jobs for VM {vm_data.vm_id} (group: {vm_group_id})",
        ip_address=request.client.host if request.client else None
    )
    
    # Aggiorna scheduler per tutti i job con schedule
    if vm_data.schedule:
        for job in db.query(SyncJob).filter(SyncJob.vm_group_id == vm_group_id).all():
            scheduler_service.update_job_schedule(job.id, job.schedule)
    
    return {
        "success": True,
        "vm_id": vm_data.vm_id,
        "vm_group_id": vm_group_id,
        "dest_vm_id": dest_vmid,
        "jobs_created": len(created_jobs),
        "total_size": proxmox_service._format_size(total_size),
        "jobs": created_jobs
    }


@router.get("/vm-group/{vm_group_id}")
async def get_vm_group_jobs(
    vm_group_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene tutti i job di un gruppo VM"""
    jobs = db.query(SyncJob).filter(SyncJob.vm_group_id == vm_group_id).all()
    
    if not jobs:
        raise HTTPException(status_code=404, detail="Gruppo non trovato")
    
    # Verifica accesso al primo job
    if not check_job_access(user, jobs[0], db):
        raise HTTPException(status_code=403, detail="Accesso negato")
    
    return {
        "vm_group_id": vm_group_id,
        "vm_id": jobs[0].vm_id,
        "vm_name": jobs[0].vm_name,
        "total_jobs": len(jobs),
        "jobs": [SyncJobResponseWithNodes(
            **j.__dict__,
            source_node_name=j.source_node.name if j.source_node else None,
            dest_node_name=j.dest_node.name if j.dest_node else None
        ) for j in jobs]
    }


@router.post("/vm-group/{vm_group_id}/run")
async def run_vm_group_jobs(
    vm_group_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Esegue tutti i job di un gruppo VM"""
    jobs = db.query(SyncJob).filter(SyncJob.vm_group_id == vm_group_id).all()
    
    if not jobs:
        raise HTTPException(status_code=404, detail="Gruppo non trovato")
    
    started = 0
    for job in jobs:
        if check_job_access(user, job, db) and job.is_active:
            background_tasks.add_task(execute_sync_job_task, job.id, user.id)
            started += 1
    
    return {
        "success": True,
        "vm_group_id": vm_group_id,
        "jobs_started": started
    }


@router.delete("/vm-group/{vm_group_id}")
async def delete_vm_group_jobs(
    vm_group_id: str,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Elimina tutti i job di un gruppo VM"""
    jobs = db.query(SyncJob).filter(SyncJob.vm_group_id == vm_group_id).all()
    
    if not jobs:
        raise HTTPException(status_code=404, detail="Gruppo non trovato")
    
    deleted = 0
    for job in jobs:
        if check_job_access(user, job, db):
            scheduler_service.remove_job(job.id)
            db.delete(job)
            deleted += 1
    
    db.commit()
    
    log_audit(
        db, user.id, "vm_group_deleted", "sync_job",
        details=f"Deleted {deleted} jobs from group {vm_group_id}",
        ip_address=request.client.host if request.client else None
    )
    
    return {"success": True, "jobs_deleted": deleted}


@router.get("/{job_id}", response_model=SyncJobResponseWithNodes)
async def get_sync_job(
    job_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene un job specifico"""
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job non trovato")
    
    if not check_job_access(user, job, db):
        raise HTTPException(status_code=403, detail="Accesso negato")
    
    job_dict = SyncJobResponse.model_validate(job).model_dump()
    
    source_node = db.query(Node).filter(Node.id == job.source_node_id).first()
    dest_node = db.query(Node).filter(Node.id == job.dest_node_id).first()
    
    job_dict["source_node_name"] = source_node.name if source_node else None
    job_dict["dest_node_name"] = dest_node.name if dest_node else None
    
    return SyncJobResponseWithNodes(**job_dict)


@router.put("/{job_id}", response_model=SyncJobResponse)
async def update_sync_job(
    job_id: int,
    update: SyncJobUpdate,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Aggiorna un job"""
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job non trovato")
    
    if not check_job_access(user, job, db):
        raise HTTPException(status_code=403, detail="Accesso negato")
    
    for key, value in update.dict(exclude_unset=True).items():
        setattr(job, key, value)
    
    log_audit(
        db, user.id, "sync_job_updated", "sync_job",
        resource_id=job_id,
        details=f"Updated job: {job.name}",
        ip_address=request.client.host if request.client else None
    )
    
    db.commit()
    db.refresh(job)
    
    # Aggiorna scheduler
    scheduler_service.update_job_schedule(job.id, job.schedule)
    
    return job


@router.delete("/{job_id}")
async def delete_sync_job(
    job_id: int,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Elimina un job"""
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job non trovato")
    
    if not check_job_access(user, job, db):
        raise HTTPException(status_code=403, detail="Accesso negato")
    
    job_name = job.name
    scheduler_service.remove_job(job_id)
    
    db.delete(job)
    
    log_audit(
        db, user.id, "sync_job_deleted", "sync_job",
        resource_id=job_id,
        details=f"Deleted job: {job_name}",
        ip_address=request.client.host if request.client else None
    )
    
    db.commit()
    return {"message": "Job eliminato"}


@router.post("/{job_id}/run")
async def run_sync_job(
    job_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Esegue un job manualmente"""
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job non trovato")
    
    if not check_job_access(user, job, db):
        raise HTTPException(status_code=403, detail="Accesso negato")
    
    source_node = db.query(Node).filter(Node.id == job.source_node_id).first()
    dest_node = db.query(Node).filter(Node.id == job.dest_node_id).first()
    
    if not source_node or not dest_node:
        raise HTTPException(status_code=400, detail="Nodi non configurati correttamente")
    
    # Esegui in background usando la funzione helper
    background_tasks.add_task(execute_sync_job_task, job_id, user.id)
    
    log_audit(
        db, user.id, "sync_job_started", "sync_job",
        resource_id=job_id,
        details=f"Manual run: {job.name}",
        ip_address=request.client.host if request.client else None
    )
    
    return {"message": "Job avviato in background", "job_id": job_id}


@router.get("/{job_id}/logs")
async def get_job_logs(
    job_id: int,
    limit: int = 20,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene i log di un job"""
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job non trovato")
    
    if not check_job_access(user, job, db):
        raise HTTPException(status_code=403, detail="Accesso negato")
    
    logs = db.query(JobLog).filter(
        JobLog.job_id == job_id
    ).order_by(JobLog.started_at.desc()).limit(limit).all()
    
    return logs


@router.post("/{job_id}/toggle")
async def toggle_sync_job(
    job_id: int,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """Attiva/disattiva un job"""
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job non trovato")
    
    if not check_job_access(user, job, db):
        raise HTTPException(status_code=403, detail="Accesso negato")
    
    job.is_active = not job.is_active
    
    log_audit(
        db, user.id, "sync_job_toggled", "sync_job",
        resource_id=job_id,
        details=f"{'Enabled' if job.is_active else 'Disabled'}: {job.name}",
        ip_address=request.client.host if request.client else None
    )
    
    db.commit()
    
    if job.is_active and job.schedule:
        scheduler_service.update_job_schedule(job.id, job.schedule)
    else:
        scheduler_service.remove_job(job.id)
    
    return {"is_active": job.is_active}


@router.post("/{job_id}/register-vm")
async def register_vm_manually(
    job_id: int,
    request: Request,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db)
):
    """
    Registra manualmente la VM associata a un job sul nodo destinazione.
    Copia la configurazione dalla sorgente e registra la VM.
    """
    from services.ssh_service import ssh_service
    from services.proxmox_service import proxmox_service
    
    job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job non trovato")
    
    if not check_job_access(user, job, db):
        raise HTTPException(status_code=403, detail="Accesso negato")
    
    if not job.vm_id:
        raise HTTPException(status_code=400, detail="VMID sorgente non configurato per questo job")
    
    # Usa dest_vm_id se specificato, altrimenti usa vm_id
    target_vmid = job.dest_vm_id if job.dest_vm_id else job.vm_id
    
    source_node = db.query(Node).filter(Node.id == job.source_node_id).first()
    dest_node = db.query(Node).filter(Node.id == job.dest_node_id).first()
    
    if not source_node or not dest_node:
        raise HTTPException(status_code=400, detail="Nodi non configurati")
    
    vm_type = job.vm_type or "qemu"
    
    # Ottieni la configurazione dalla sorgente
    if vm_type == "qemu":
        config_path = f"/etc/pve/qemu-server/{job.vm_id}.conf"
    else:
        config_path = f"/etc/pve/lxc/{job.vm_id}.conf"
    
    config_result = await ssh_service.execute(
        hostname=source_node.hostname,
        command=f"cat {config_path} 2>/dev/null",
        port=source_node.ssh_port,
        username=source_node.ssh_user,
        key_path=source_node.ssh_key_path,
        timeout=30
    )
    
    if not config_result.success or not config_result.stdout.strip():
        raise HTTPException(
            status_code=400, 
            detail=f"Configurazione VM {job.vm_id} non trovata su {source_node.name}. "
                   f"Path: {config_path}, Errore: {config_result.stderr}"
        )
    
    # Modifica la configurazione
    config_content = config_result.stdout
    
    # Sostituisci i path del dataset
    # Proxmox usa il formato pool:dataset (con : invece di /)
    source_zfs = job.source_dataset.split("/")[0]  # pool sorgente
    dest_zfs = job.dest_dataset.split("/")[0]  # pool destinazione
    
    # Determina il pool ZFS destinazione per creare lo storage
    dest_zfs_pool = "/".join(job.dest_dataset.split("/")[:-1])  # es: ZFS/replica
    if not dest_zfs_pool:
        dest_zfs_pool = job.dest_dataset.split("/")[0]
    
    # Registra la VM sulla destinazione con ID diverso se specificato
    # Passa source_storage e dest_storage per la sostituzione automatica
    success, msg = await proxmox_service.register_vm(
        hostname=dest_node.hostname,
        vmid=target_vmid,
        vm_type=vm_type,
        config_content=config_content,
        source_storage=job.source_storage,
        dest_storage=job.dest_storage,
        dest_zfs_pool=dest_zfs_pool,
        port=dest_node.ssh_port,
        username=dest_node.ssh_user,
        key_path=dest_node.ssh_key_path
    )
    
    if success:
        log_audit(
            db, user.id, "vm_registered", "sync_job",
            resource_id=job_id,
            details=f"VM {target_vmid} registrata su {dest_node.name}" + (f" (da VM {job.vm_id})" if target_vmid != job.vm_id else ""),
            ip_address=request.client.host if request.client else None
        )
        return {
            "success": True,
            "message": f"VM {target_vmid} ({vm_type}) registrata su {dest_node.name}" + (f" (copiata da VM {job.vm_id})" if target_vmid != job.vm_id else ""),
            "config_path": config_path,
            "source_vmid": job.vm_id,
            "dest_vmid": target_vmid
        }
    else:
        raise HTTPException(status_code=500, detail=f"Registrazione fallita: {msg}")


@router.get("/stats/summary")
async def get_sync_stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene statistiche sui job di sincronizzazione"""
    from sqlalchemy import func
    
    # Job totali
    total_jobs = db.query(SyncJob).count()
    active_jobs = db.query(SyncJob).filter(SyncJob.is_active == True).count()
    
    # Esecuzioni ultime 24h
    from datetime import timedelta
    yesterday = datetime.utcnow() - timedelta(days=1)
    
    recent_logs = db.query(JobLog).filter(
        JobLog.job_type == "sync",
        JobLog.started_at >= yesterday
    ).all()
    
    success_count = len([l for l in recent_logs if l.status == "success"])
    failed_count = len([l for l in recent_logs if l.status == "failed"])
    
    # Dati trasferiti
    total_transferred = sum(
        int(l.transferred.replace("G", "000").replace("M", "").replace("K", "")[:10]) 
        for l in recent_logs 
        if l.transferred and l.transferred[0].isdigit()
    ) if recent_logs else 0
    
    return {
        "total_jobs": total_jobs,
        "active_jobs": active_jobs,
        "runs_24h": len(recent_logs),
        "success_24h": success_count,
        "failed_24h": failed_count,
        "success_rate": round(success_count / len(recent_logs) * 100, 1) if recent_logs else 0
    }
