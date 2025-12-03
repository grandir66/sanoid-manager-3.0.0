"""
Router per gestione log
Con autenticazione
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel

from database import get_db, JobLog, User, AuditLog
from routers.auth import get_current_user, require_admin

router = APIRouter()


# ============== Schemas ==============

class JobLogResponse(BaseModel):
    id: int
    job_type: str
    job_id: Optional[int]
    node_name: Optional[str]
    dataset: Optional[str]
    status: str
    message: Optional[str]
    output: Optional[str]
    error: Optional[str]
    duration: Optional[int]
    transferred: Optional[str]
    attempt_number: int
    started_at: datetime
    completed_at: Optional[datetime]
    triggered_by: Optional[int]
    
    class Config:
        from_attributes = True


class LogStatsResponse(BaseModel):
    total: int
    success: int
    failed: int
    running: int
    success_rate: float
    avg_duration: Optional[float]
    total_transferred: Optional[str]


# ============== Endpoints ==============

@router.get("/", response_model=List[JobLogResponse])
async def list_logs(
    limit: int = 100,
    offset: int = 0,
    job_type: Optional[str] = None,
    status: Optional[str] = None,
    job_id: Optional[int] = None,
    since: Optional[datetime] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Lista i log delle operazioni"""
    query = db.query(JobLog)
    
    if job_type:
        query = query.filter(JobLog.job_type == job_type)
    if status:
        query = query.filter(JobLog.status == status)
    if job_id:
        query = query.filter(JobLog.job_id == job_id)
    if since:
        query = query.filter(JobLog.started_at >= since)
    
    logs = query.order_by(JobLog.started_at.desc()).offset(offset).limit(limit).all()
    return logs


@router.get("/stats", response_model=LogStatsResponse)
async def get_log_stats(
    days: int = 7,
    job_type: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene statistiche sui log"""
    since = datetime.utcnow() - timedelta(days=days)
    
    query = db.query(JobLog).filter(JobLog.started_at >= since)
    
    if job_type:
        query = query.filter(JobLog.job_type == job_type)
    
    logs = query.all()
    
    total = len(logs)
    success = len([l for l in logs if l.status == "success"])
    failed = len([l for l in logs if l.status == "failed"])
    running = len([l for l in logs if l.status in ("started", "running")])
    
    durations = [l.duration for l in logs if l.duration]
    avg_duration = sum(durations) / len(durations) if durations else None
    
    success_rate = (success / total * 100) if total > 0 else 0
    
    return LogStatsResponse(
        total=total,
        success=success,
        failed=failed,
        running=running,
        success_rate=round(success_rate, 1),
        avg_duration=round(avg_duration, 1) if avg_duration else None,
        total_transferred=None  # TODO: calcolare
    )


@router.get("/{log_id}", response_model=JobLogResponse)
async def get_log(
    log_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene un log specifico"""
    log = db.query(JobLog).filter(JobLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Log non trovato")
    return log


@router.delete("/cleanup")
async def cleanup_old_logs(
    days: int = 30,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Elimina log più vecchi di N giorni (solo admin)"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    count = db.query(JobLog).filter(JobLog.started_at < cutoff).count()
    db.query(JobLog).filter(JobLog.started_at < cutoff).delete()
    db.commit()
    
    return {"message": f"Eliminati {count} log più vecchi di {days} giorni"}


@router.get("/recent/failed")
async def get_recent_failures(
    limit: int = 10,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene i fallimenti recenti"""
    logs = db.query(JobLog).filter(
        JobLog.status == "failed"
    ).order_by(JobLog.started_at.desc()).limit(limit).all()
    
    return logs


@router.get("/job/{job_id}/history")
async def get_job_history(
    job_id: int,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ottiene lo storico di un job specifico"""
    logs = db.query(JobLog).filter(
        JobLog.job_id == job_id
    ).order_by(JobLog.started_at.desc()).limit(limit).all()
    
    return logs


# ============== Audit Log Endpoints ==============

@router.get("/audit")
async def list_audit_logs(
    limit: int = 100,
    user_id: Optional[int] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    since: Optional[datetime] = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Lista i log di audit (solo admin)"""
    query = db.query(AuditLog)
    
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    if action:
        query = query.filter(AuditLog.action == action)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if since:
        query = query.filter(AuditLog.created_at >= since)
    
    logs = query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    
    # Aggiungi username
    from database import User as UserModel
    result = []
    for log in logs:
        log_dict = {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "details": log.details,
            "ip_address": log.ip_address,
            "status": log.status,
            "created_at": log.created_at,
            "username": None
        }
        if log.user_id:
            user_obj = db.query(UserModel).filter(UserModel.id == log.user_id).first()
            if user_obj:
                log_dict["username"] = user_obj.username
        result.append(log_dict)
    
    return result


@router.delete("/audit/cleanup")
async def cleanup_audit_logs(
    days: int = 90,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """Elimina audit log più vecchi di N giorni (solo admin)"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    
    count = db.query(AuditLog).filter(AuditLog.created_at < cutoff).count()
    db.query(AuditLog).filter(AuditLog.created_at < cutoff).delete()
    db.commit()
    
    return {"message": f"Eliminati {count} audit log più vecchi di {days} giorni"}
