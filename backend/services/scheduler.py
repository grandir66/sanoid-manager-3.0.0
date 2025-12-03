"""
Scheduler Service - Gestione job schedulati
"""

import asyncio
from datetime import datetime
from typing import Dict, Optional, Callable
import logging
from croniter import croniter
from sqlalchemy.orm import Session

from database import SessionLocal, SyncJob, JobLog, Node
from services.syncoid_service import syncoid_service
from services.proxmox_service import proxmox_service

logger = logging.getLogger(__name__)


class SchedulerService:
    """Servizio per scheduling dei job di sincronizzazione"""
    
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._jobs: Dict[int, datetime] = {}  # job_id -> next_run
    
    async def start(self):
        """Avvia lo scheduler"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("Scheduler avviato")
    
    async def stop(self):
        """Ferma lo scheduler"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler fermato")
    
    async def _scheduler_loop(self):
        """Loop principale dello scheduler"""
        while self._running:
            try:
                await self._check_and_run_jobs()
                await asyncio.sleep(60)  # Check ogni minuto
            except Exception as e:
                logger.error(f"Errore nello scheduler: {e}")
                await asyncio.sleep(60)
    
    async def _check_and_run_jobs(self):
        """Verifica e esegue i job schedulati"""
        db = SessionLocal()
        try:
            # Ottieni job attivi con schedule
            jobs = db.query(SyncJob).filter(
                SyncJob.is_active == True,
                SyncJob.schedule.isnot(None),
                SyncJob.schedule != ""
            ).all()
            
            now = datetime.utcnow()
            
            for job in jobs:
                try:
                    # Calcola prossima esecuzione
                    if job.id not in self._jobs:
                        # Prima volta, calcola dalla schedule
                        cron = croniter(job.schedule, job.last_run or now)
                        self._jobs[job.id] = cron.get_next(datetime)
                    
                    next_run = self._jobs[job.id]
                    
                    if now >= next_run:
                        # Tempo di eseguire
                        logger.info(f"Esecuzione job schedulato: {job.name} (ID: {job.id})")
                        asyncio.create_task(self._execute_job(job.id))
                        
                        # Calcola prossima esecuzione
                        cron = croniter(job.schedule, now)
                        self._jobs[job.id] = cron.get_next(datetime)
                        
                except Exception as e:
                    logger.error(f"Errore scheduling job {job.id}: {e}")
        finally:
            db.close()
    
    async def _execute_job(self, job_id: int):
        """Esegue un job di sincronizzazione"""
        db = SessionLocal()
        log_entry = None
        
        try:
            job = db.query(SyncJob).filter(SyncJob.id == job_id).first()
            if not job:
                logger.error(f"Job {job_id} non trovato")
                return
            
            source_node = db.query(Node).filter(Node.id == job.source_node_id).first()
            dest_node = db.query(Node).filter(Node.id == job.dest_node_id).first()
            
            if not source_node or not dest_node:
                logger.error(f"Nodi non trovati per job {job_id}")
                return
            
            # Crea log entry
            log_entry = JobLog(
                job_type="sync",
                job_id=job_id,
                node_name=f"{source_node.name} -> {dest_node.name}",
                dataset=f"{job.source_dataset} -> {job.dest_dataset}",
                status="started",
                message=f"Sincronizzazione avviata"
            )
            db.add(log_entry)
            db.commit()
            
            # Aggiorna stato job
            job.last_status = "running"
            db.commit()
            
            # Determina da dove eseguire (sorgente)
            executor_host = source_node.hostname
            
            # Esegui sync
            result = await syncoid_service.run_sync(
                executor_host=executor_host,
                source_host=None,  # Locale all'executor
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
            
            # Aggiorna job
            job.last_run = datetime.utcnow()
            job.last_duration = result["duration"]
            job.last_transferred = result.get("transferred")
            job.run_count += 1
            
            if result["success"]:
                job.last_status = "success"
                log_entry.status = "success"
                log_entry.message = "Sincronizzazione completata"
                
                # Registra VM se richiesto
                if job.register_vm and job.vm_id:
                    await self._register_vm_after_sync(db, job, source_node, dest_node, log_entry)
            else:
                job.last_status = "failed"
                job.error_count += 1
                log_entry.status = "failed"
                log_entry.message = "Sincronizzazione fallita"
                log_entry.error = result.get("error", "")
            
            log_entry.output = result.get("output", "")
            log_entry.duration = result["duration"]
            log_entry.transferred = result.get("transferred")
            log_entry.completed_at = datetime.utcnow()
            
            db.commit()
            
        except Exception as e:
            logger.error(f"Errore esecuzione job {job_id}: {e}")
            if log_entry:
                log_entry.status = "failed"
                log_entry.error = str(e)
                log_entry.completed_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()
    
    async def _register_vm_after_sync(
        self,
        db: Session,
        job: SyncJob,
        source_node: Node,
        dest_node: Node,
        log_entry: JobLog
    ):
        """Registra una VM sul nodo destinazione dopo la sync"""
        try:
            # Ottieni config dalla sorgente
            success, config = await proxmox_service.get_vm_config_file(
                hostname=source_node.hostname,
                vmid=job.vm_id,
                vm_type=job.vm_type or "qemu",
                port=source_node.ssh_port,
                username=source_node.ssh_user,
                key_path=source_node.ssh_key_path
            )
            
            if not success:
                log_entry.message += f" | Registrazione VM fallita: impossibile ottenere config"
                return
            
            # Modifica config per il nuovo storage se necessario
            # (potrebbe essere necessario adattare i path dei dischi)
            modified_config = self._adapt_vm_config(config, job.source_dataset, job.dest_dataset)
            
            # Registra sul nodo destinazione
            success, msg = await proxmox_service.register_vm(
                hostname=dest_node.hostname,
                vmid=job.vm_id,
                vm_type=job.vm_type or "qemu",
                config_content=modified_config,
                port=dest_node.ssh_port,
                username=dest_node.ssh_user,
                key_path=dest_node.ssh_key_path
            )
            
            if success:
                log_entry.message += f" | VM {job.vm_id} registrata"
            else:
                log_entry.message += f" | Registrazione VM fallita: {msg}"
                
        except Exception as e:
            log_entry.message += f" | Errore registrazione VM: {e}"
    
    def _adapt_vm_config(self, config: str, source_dataset: str, dest_dataset: str) -> str:
        """
        Adatta la configurazione VM per il nodo destinazione
        
        Sostituisce i riferimenti allo storage sorgente con quello destinazione
        """
        # Estrai il nome dello storage dal dataset
        # es: rpool/data -> local-zfs (dipende dalla config Proxmox)
        # Per ora ritorniamo la config così com'è
        # In produzione servirebbe una mappatura storage sorgente -> destinazione
        
        return config
    
    def update_job_schedule(self, job_id: int, schedule: str):
        """Aggiorna lo schedule di un job"""
        if schedule:
            cron = croniter(schedule, datetime.utcnow())
            self._jobs[job_id] = cron.get_next(datetime)
        elif job_id in self._jobs:
            del self._jobs[job_id]
    
    def remove_job(self, job_id: int):
        """Rimuove un job dallo scheduler"""
        if job_id in self._jobs:
            del self._jobs[job_id]


# Singleton
scheduler_service = SchedulerService()
