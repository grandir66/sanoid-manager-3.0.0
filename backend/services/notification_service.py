"""
Notification Service - Servizio centralizzato per invio notifiche
Supporta Email, Webhook e Telegram
"""

import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict, Any
import logging

from services.email_service import email_service
from database import SessionLocal, NotificationConfig, JobLog, SyncJob, Node

logger = logging.getLogger(__name__)


class NotificationService:
    """Servizio centralizzato per tutte le notifiche"""
    
    def __init__(self):
        self._config: Optional[NotificationConfig] = None
        self._last_config_load: Optional[datetime] = None
        self._config_cache_seconds = 60  # Ricarica config ogni 60 secondi
        # Tracking notifiche giornaliere per job: {job_id: last_notification_date}
        self._daily_job_notifications: Dict[int, datetime] = {}
    
    def _load_config(self) -> Optional[NotificationConfig]:
        """Carica la configurazione notifiche dal database"""
        now = datetime.utcnow()
        
        # Usa cache se recente
        if (self._config and self._last_config_load and 
            (now - self._last_config_load).seconds < self._config_cache_seconds):
            return self._config
        
        db = SessionLocal()
        try:
            self._config = db.query(NotificationConfig).first()
            self._last_config_load = now
            return self._config
        finally:
            db.close()
    
    def _configure_email_service(self, config: NotificationConfig):
        """Configura il servizio email con i dati dal database"""
        if config and config.smtp_enabled and config.smtp_host:
            email_service.configure(
                host=config.smtp_host,
                port=config.smtp_port or 587,
                user=config.smtp_user,
                password=config.smtp_password,
                from_addr=config.smtp_from,
                to_addrs=config.smtp_to,
                subject_prefix=config.smtp_subject_prefix or "[Sanoid Manager]",
                use_tls=config.smtp_tls if config.smtp_tls is not None else True
            )
    
    def _cleanup_old_notifications(self):
        """Rimuove tracking notifiche più vecchie di 2 giorni"""
        cutoff = datetime.utcnow() - timedelta(days=2)
        to_remove = [
            job_id for job_id, last_date in self._daily_job_notifications.items()
            if last_date < cutoff
        ]
        for job_id in to_remove:
            del self._daily_job_notifications[job_id]
    
    async def send_job_notification(
        self,
        job_name: str,
        status: str,  # success, failed, warning
        source: str,
        destination: str,
        duration: Optional[int] = None,
        error: Optional[str] = None,
        details: Optional[str] = None,
        job_id: Optional[int] = None,
        is_scheduled: bool = False
    ) -> Dict[str, Any]:
        """
        Invia notifica per un job completato su tutti i canali abilitati.
        
        Per job schedulati (ricorrenti), limita a max 1 notifica di successo al giorno.
        I fallimenti vengono sempre notificati.
        
        Args:
            job_name: Nome del job
            status: Stato (success, failed, warning)
            source: Sorgente
            destination: Destinazione
            duration: Durata in secondi
            error: Messaggio errore
            details: Dettagli aggiuntivi
            job_id: ID del job (per tracking notifiche giornaliere)
            is_scheduled: True se job schedulato/ricorrente
        
        Returns:
            Dict con risultati per ogni canale
        """
        config = self._load_config()
        if not config:
            logger.debug("Notifiche non configurate")
            return {"sent": False, "reason": "not_configured"}
        
        # Verifica se notificare in base allo status
        should_notify = (
            (status == "success" and config.notify_on_success) or
            (status == "failed" and config.notify_on_failure) or
            (status == "warning" and config.notify_on_warning)
        )
        
        if not should_notify:
            logger.debug(f"Notifica non richiesta per status: {status}")
            return {"sent": False, "reason": f"notify_on_{status}_disabled"}
        
        # Per job schedulati: limita notifiche successo a 1 al giorno
        # I fallimenti vengono sempre notificati
        if is_scheduled and job_id and status == "success":
            today = datetime.utcnow().date()
            last_notification = self._daily_job_notifications.get(job_id)
            
            if last_notification and last_notification.date() == today:
                logger.debug(f"Notifica già inviata oggi per job {job_id}, skip")
                return {"sent": False, "reason": "daily_limit_reached"}
            
            # Aggiorna tracking
            self._daily_job_notifications[job_id] = datetime.utcnow()
            
            # Pulizia entries vecchie (più di 2 giorni)
            self._cleanup_old_notifications()
        
        results = {"sent": True, "channels": {}}
        
        # Email
        if config.smtp_enabled:
            try:
                self._configure_email_service(config)
                success, message = email_service.send_job_notification(
                    job_name=job_name,
                    status=status,
                    source=source,
                    destination=destination,
                    duration=duration,
                    error=error,
                    details=details
                )
                results["channels"]["email"] = {"success": success, "message": message}
                if success:
                    logger.info(f"Email notifica inviata per job {job_name}")
                else:
                    logger.error(f"Errore invio email per job {job_name}: {message}")
            except Exception as e:
                logger.error(f"Eccezione invio email: {e}")
                results["channels"]["email"] = {"success": False, "message": str(e)}
        
        # Webhook
        if config.webhook_enabled and config.webhook_url:
            try:
                webhook_result = await self._send_webhook(
                    config=config,
                    event_type="job_completed",
                    data={
                        "job_name": job_name,
                        "status": status,
                        "source": source,
                        "destination": destination,
                        "duration": duration,
                        "error": error,
                        "details": details,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                )
                results["channels"]["webhook"] = webhook_result
            except Exception as e:
                logger.error(f"Eccezione webhook: {e}")
                results["channels"]["webhook"] = {"success": False, "message": str(e)}
        
        # Telegram
        if config.telegram_enabled and config.telegram_bot_token and config.telegram_chat_id:
            try:
                telegram_result = await self._send_telegram(
                    config=config,
                    message=self._format_telegram_job_message(
                        job_name, status, source, destination, duration, error
                    )
                )
                results["channels"]["telegram"] = telegram_result
            except Exception as e:
                logger.error(f"Eccezione telegram: {e}")
                results["channels"]["telegram"] = {"success": False, "message": str(e)}
        
        return results
    
    async def send_daily_summary(self) -> Dict[str, Any]:
        """
        Invia il riepilogo giornaliero delle attività con dettaglio per ogni job.
        
        Returns:
            Dict con risultati invio
        """
        config = self._load_config()
        if not config:
            logger.debug("Notifiche non configurate per riepilogo giornaliero")
            return {"sent": False, "reason": "not_configured"}
        
        # Verifica se almeno un canale è abilitato
        if not (config.smtp_enabled or config.webhook_enabled or config.telegram_enabled):
            logger.debug("Nessun canale notifiche abilitato")
            return {"sent": False, "reason": "no_channels_enabled"}
        
        # Raccogli dati delle ultime 24 ore
        db = SessionLocal()
        try:
            yesterday = datetime.utcnow() - timedelta(hours=24)
            
            # Ottieni tutti i sync jobs attivi
            sync_jobs = db.query(SyncJob).filter(SyncJob.is_active == True).all()
            
            if not sync_jobs:
                logger.info("Nessun job configurato, riepilogo non inviato")
                return {"sent": False, "reason": "no_jobs_configured"}
            
            # Statistiche generali
            total_runs = 0
            successful = 0
            failed = 0
            total_duration = 0
            
            # Dettaglio per ogni job
            jobs_summary = []
            
            for job in sync_jobs:
                # Ottieni logs per questo job nelle ultime 24 ore
                job_logs = db.query(JobLog).filter(
                    JobLog.job_id == job.id,
                    JobLog.job_type == "sync",
                    JobLog.started_at >= yesterday
                ).order_by(JobLog.started_at.desc()).all()
                
                # Ottieni nodi
                source_node = db.query(Node).filter(Node.id == job.source_node_id).first()
                dest_node = db.query(Node).filter(Node.id == job.dest_node_id).first()
                
                job_runs = len(job_logs)
                job_success = len([l for l in job_logs if l.status == "success"])
                job_failed = len([l for l in job_logs if l.status == "failed"])
                job_duration = sum(l.duration or 0 for l in job_logs)
                
                # Ultimo errore se presente
                last_error = None
                last_error_time = None
                for log in job_logs:
                    if log.status == "failed" and log.error:
                        last_error = log.error[:200]
                        last_error_time = log.started_at.strftime("%H:%M") if log.started_at else None
                        break
                
                # Ultimo trasferimento
                last_transferred = None
                for log in job_logs:
                    if log.transferred:
                        last_transferred = log.transferred
                        break
                
                job_info = {
                    "id": job.id,
                    "name": job.name,
                    "source_node": source_node.name if source_node else "N/A",
                    "dest_node": dest_node.name if dest_node else "N/A",
                    "source_dataset": job.source_dataset,
                    "dest_dataset": job.dest_dataset,
                    "schedule": job.schedule or "Manuale",
                    "runs_24h": job_runs,
                    "success_24h": job_success,
                    "failed_24h": job_failed,
                    "duration_24h": job_duration,
                    "last_status": job.last_status or "never_run",
                    "last_run": job.last_run.strftime("%d/%m %H:%M") if job.last_run else "Mai",
                    "last_transferred": last_transferred or job.last_transferred,
                    "last_error": last_error,
                    "last_error_time": last_error_time
                }
                jobs_summary.append(job_info)
                
                # Aggiungi ai totali
                total_runs += job_runs
                successful += job_success
                failed += job_failed
                total_duration += job_duration
            
            summary_data = {
                "total_jobs": len(sync_jobs),
                "total_runs": total_runs,
                "successful": successful,
                "failed": failed,
                "total_duration": total_duration,
                "jobs": jobs_summary
            }
            
        finally:
            db.close()
        
        results = {"sent": True, "channels": {}, "summary": summary_data}
        
        # Invia su tutti i canali
        # Email
        if config.smtp_enabled:
            try:
                self._configure_email_service(config)
                success, message = self._send_daily_summary_email(summary_data)
                results["channels"]["email"] = {"success": success, "message": message}
            except Exception as e:
                logger.error(f"Errore invio email riepilogo: {e}")
                results["channels"]["email"] = {"success": False, "message": str(e)}
        
        # Webhook
        if config.webhook_enabled and config.webhook_url:
            try:
                webhook_result = await self._send_webhook(
                    config=config,
                    event_type="daily_summary",
                    data=summary_data
                )
                results["channels"]["webhook"] = webhook_result
            except Exception as e:
                results["channels"]["webhook"] = {"success": False, "message": str(e)}
        
        # Telegram
        if config.telegram_enabled and config.telegram_bot_token and config.telegram_chat_id:
            try:
                telegram_result = await self._send_telegram(
                    config=config,
                    message=self._format_telegram_summary(summary_data)
                )
                results["channels"]["telegram"] = telegram_result
            except Exception as e:
                results["channels"]["telegram"] = {"success": False, "message": str(e)}
        
        return results
    
    def _send_daily_summary_email(self, summary: Dict[str, Any]) -> Tuple[bool, str]:
        """Genera e invia email riepilogo giornaliero con dettaglio per job"""
        
        # Determina stato generale
        if summary["failed"] > 0:
            status_emoji = "❌"
            status_color = "#dc3545"
            status_text = "Attenzione Richiesta"
        else:
            status_emoji = "✅"
            status_color = "#28a745"
            status_text = "Tutto OK"
        
        # Formatta durata totale
        hours = summary["total_duration"] // 3600
        minutes = (summary["total_duration"] % 3600) // 60
        duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        
        # Genera righe tabella per ogni job
        job_rows = ""
        for job in summary.get("jobs", []):
            # Determina colore stato
            if job["failed_24h"] > 0:
                status_icon = "❌"
                row_style = "background: #fff5f5;"
            elif job["last_status"] == "success":
                status_icon = "✅"
                row_style = ""
            elif job["last_status"] == "running":
                status_icon = "🔄"
                row_style = "background: #fff9e6;"
            elif job["last_status"] == "never_run":
                status_icon = "⏸️"
                row_style = "background: #f5f5f5;"
            else:
                status_icon = "⚠️"
                row_style = "background: #fff9e6;"
            
            # Formatta durata job
            job_hours = job["duration_24h"] // 3600
            job_mins = (job["duration_24h"] % 3600) // 60
            job_duration = f"{job_hours}h {job_mins}m" if job_hours > 0 else f"{job_mins}m"
            
            job_rows += f"""
            <tr style="{row_style}">
                <td style="padding: 12px; border-bottom: 1px solid #dee2e6;">
                    <strong>{job['name']}</strong><br>
                    <span style="font-size: 11px; color: #6c757d;">{job['schedule']}</span>
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #dee2e6; font-size: 12px;">
                    {job['source_node']}<br>
                    <code style="background: #f1f1f1; padding: 2px 4px; border-radius: 3px; font-size: 10px;">{job['source_dataset']}</code>
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #dee2e6; font-size: 12px;">
                    {job['dest_node']}<br>
                    <code style="background: #f1f1f1; padding: 2px 4px; border-radius: 3px; font-size: 10px;">{job['dest_dataset']}</code>
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #dee2e6; text-align: center;">
                    <span style="font-size: 18px;">{status_icon}</span><br>
                    <span style="font-size: 11px; color: #6c757d;">{job['last_run']}</span>
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #dee2e6; text-align: center;">
                    <span style="color: #28a745; font-weight: bold;">{job['success_24h']}</span> / 
                    <span style="color: #dc3545; font-weight: bold;">{job['failed_24h']}</span>
                </td>
                <td style="padding: 12px; border-bottom: 1px solid #dee2e6; text-align: center; font-size: 12px;">
                    {job_duration}<br>
                    <span style="color: #6c757d;">{job['last_transferred'] or '-'}</span>
                </td>
            </tr>
            """
            
            # Aggiungi riga errore se presente
            if job["last_error"]:
                job_rows += f"""
                <tr style="background: #fff5f5;">
                    <td colspan="6" style="padding: 8px 12px; border-bottom: 2px solid #dee2e6; font-size: 11px;">
                        <span style="color: #dc3545;">⚠️ Ultimo errore ({job['last_error_time'] or 'N/A'}):</span>
                        <code style="display: block; margin-top: 4px; padding: 6px; background: #f8d7da; border-radius: 4px; white-space: pre-wrap; word-break: break-all;">{job['last_error']}</code>
                    </td>
                </tr>
                """
        
        subject = f"{status_emoji} Riepilogo Giornaliero - {summary['successful']}/{summary['total_runs']} esecuzioni OK"
        
        body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }}
        .container {{ max-width: 900px; margin: 0 auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        .header {{ background: {status_color}; color: white; padding: 25px; text-align: center; }}
        .header h1 {{ margin: 0 0 10px 0; font-size: 24px; }}
        .content {{ padding: 25px; }}
        .stats {{ display: flex; justify-content: space-around; margin: 20px 0; padding: 20px; background: #f8f9fa; border-radius: 8px; flex-wrap: wrap; }}
        .stat {{ text-align: center; min-width: 80px; margin: 5px; }}
        .stat-value {{ font-size: 28px; font-weight: bold; }}
        .stat-label {{ font-size: 11px; color: #6c757d; text-transform: uppercase; }}
        .stat-success {{ color: #28a745; }}
        .stat-failed {{ color: #dc3545; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 13px; }}
        th {{ background: #343a40; color: white; padding: 12px 8px; text-align: left; font-size: 11px; text-transform: uppercase; }}
        .footer {{ padding: 20px; text-align: center; color: #6c757d; font-size: 12px; border-top: 1px solid #dee2e6; }}
        code {{ font-family: 'Consolas', 'Monaco', monospace; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{status_emoji} Riepilogo Giornaliero Sanoid Manager</h1>
            <p>{status_text}</p>
        </div>
        
        <div class="content">
            <p><strong>Periodo:</strong> Ultime 24 ore | <strong>Data:</strong> {datetime.utcnow().strftime('%d/%m/%Y %H:%M')} UTC</p>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-value">{summary['total_jobs']}</div>
                    <div class="stat-label">Job Configurati</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{summary['total_runs']}</div>
                    <div class="stat-label">Esecuzioni</div>
                </div>
                <div class="stat">
                    <div class="stat-value stat-success">{summary['successful']}</div>
                    <div class="stat-label">Successi</div>
                </div>
                <div class="stat">
                    <div class="stat-value stat-failed">{summary['failed']}</div>
                    <div class="stat-label">Falliti</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{duration_str}</div>
                    <div class="stat-label">Tempo Totale</div>
                </div>
            </div>
            
            <h3 style="margin-top: 30px; color: #343a40;">📋 Dettaglio Job</h3>
            
            <table>
                <thead>
                    <tr>
                        <th>Job</th>
                        <th>Sorgente</th>
                        <th>Destinazione</th>
                        <th>Stato</th>
                        <th>24h (OK/Fail)</th>
                        <th>Durata/Transfer</th>
                    </tr>
                </thead>
                <tbody>
                    {job_rows if job_rows else '<tr><td colspan="6" style="padding: 20px; text-align: center; color: #6c757d;">Nessun job configurato</td></tr>'}
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>Questo riepilogo è stato generato automaticamente da Sanoid Manager.</p>
        </div>
    </div>
</body>
</html>
"""
        
        return email_service.send_email(subject, body, html=True)
    
    async def _send_webhook(
        self,
        config: NotificationConfig,
        event_type: str,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Invia notifica via webhook"""
        try:
            headers = {"Content-Type": "application/json"}
            if config.webhook_secret:
                headers["X-Webhook-Secret"] = config.webhook_secret
            
            payload = {
                "event": event_type,
                "data": data,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "sanoid-manager"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    config.webhook_url,
                    json=payload,
                    headers=headers,
                    timeout=15
                )
                
                if response.status_code < 300:
                    logger.info(f"Webhook inviato: {event_type}")
                    return {"success": True, "status_code": response.status_code}
                else:
                    logger.error(f"Webhook fallito: HTTP {response.status_code}")
                    return {"success": False, "status_code": response.status_code}
                    
        except Exception as e:
            logger.error(f"Errore webhook: {e}")
            return {"success": False, "message": str(e)}
    
    async def _send_telegram(
        self,
        config: NotificationConfig,
        message: str
    ) -> Dict[str, Any]:
        """Invia notifica via Telegram"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": config.telegram_chat_id,
                        "text": message,
                        "parse_mode": "Markdown"
                    },
                    timeout=15
                )
                
                result = response.json()
                if result.get("ok"):
                    logger.info("Telegram notifica inviata")
                    return {"success": True}
                else:
                    logger.error(f"Telegram errore: {result.get('description')}")
                    return {"success": False, "message": result.get("description")}
                    
        except Exception as e:
            logger.error(f"Errore Telegram: {e}")
            return {"success": False, "message": str(e)}
    
    def _format_telegram_job_message(
        self,
        job_name: str,
        status: str,
        source: str,
        destination: str,
        duration: Optional[int],
        error: Optional[str]
    ) -> str:
        """Formatta messaggio Telegram per job"""
        emoji = {"success": "✅", "failed": "❌", "warning": "⚠️"}.get(status, "ℹ️")
        status_text = {"success": "Completato", "failed": "Fallito", "warning": "Attenzione"}.get(status, status)
        
        msg = f"""{emoji} *Replica {status_text}*

*Job:* {job_name}
*Sorgente:* `{source}`
*Destinazione:* `{destination}`"""
        
        if duration:
            minutes = duration // 60
            seconds = duration % 60
            msg += f"\n*Durata:* {minutes}m {seconds}s"
        
        if error:
            msg += f"\n\n❌ *Errore:*\n`{error[:500]}`"
        
        return msg
    
    def _format_telegram_summary(self, summary: Dict[str, Any]) -> str:
        """Formatta messaggio Telegram per riepilogo giornaliero con dettaglio per job"""
        
        if summary["failed"] > 0:
            emoji = "❌"
            status = "Attenzione Richiesta"
        else:
            emoji = "✅"
            status = "Tutto OK"
        
        hours = summary["total_duration"] // 3600
        minutes = (summary["total_duration"] % 3600) // 60
        
        msg = f"""{emoji} *Riepilogo Giornaliero Sanoid Manager*

*Stato:* {status}
*Periodo:* Ultime 24 ore

📊 *Statistiche Generali:*
• Job Configurati: {summary['total_jobs']}
• Esecuzioni: {summary['total_runs']}
• ✅ Successi: {summary['successful']}
• ❌ Falliti: {summary['failed']}
• ⏱ Tempo Totale: {hours}h {minutes}m"""
        
        # Dettaglio per job
        jobs = summary.get("jobs", [])
        if jobs:
            msg += "\n\n📋 *Dettaglio Job:*"
            for job in jobs[:10]:  # Max 10 job nel messaggio Telegram
                if job["failed_24h"] > 0:
                    job_emoji = "❌"
                elif job["last_status"] == "success":
                    job_emoji = "✅"
                elif job["last_status"] == "never_run":
                    job_emoji = "⏸️"
                else:
                    job_emoji = "⚠️"
                
                msg += f"\n\n{job_emoji} *{job['name']}*"
                msg += f"\n   `{job['source_node']}` → `{job['dest_node']}`"
                msg += f"\n   24h: {job['success_24h']}✓ {job['failed_24h']}✗ | Ultimo: {job['last_run']}"
                
                if job["last_error"]:
                    msg += f"\n   ⚠️ Errore: `{job['last_error'][:100]}...`"
        
        return msg


# Singleton
notification_service = NotificationService()

