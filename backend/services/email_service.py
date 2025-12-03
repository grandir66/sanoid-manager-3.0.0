"""
Email Service - Invio notifiche via email
"""

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class EmailService:
    """Servizio per invio email SMTP"""
    
    def __init__(self):
        self.host: Optional[str] = None
        self.port: int = 587
        self.user: Optional[str] = None
        self.password: Optional[str] = None
        self.from_addr: Optional[str] = None
        self.to_addrs: List[str] = []
        self.subject_prefix: str = "[Sanoid Manager]"
        self.use_tls: bool = True
    
    def configure(
        self,
        host: str,
        port: int = 587,
        user: Optional[str] = None,
        password: Optional[str] = None,
        from_addr: Optional[str] = None,
        to_addrs: Optional[str] = None,
        subject_prefix: str = "[Sanoid Manager]",
        use_tls: bool = True
    ):
        """Configura il servizio email"""
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr or user
        self.to_addrs = [addr.strip() for addr in (to_addrs or "").split(",") if addr.strip()]
        self.subject_prefix = subject_prefix
        self.use_tls = use_tls
    
    def send_email(
        self,
        subject: str,
        body: str,
        to_addrs: Optional[List[str]] = None,
        html: bool = False
    ) -> Tuple[bool, str]:
        """
        Invia un'email.
        
        Args:
            subject: Oggetto dell'email
            body: Corpo dell'email
            to_addrs: Lista destinatari (opzionale, usa default se non specificato)
            html: Se True, invia come HTML
            
        Returns:
            Tuple[bool, str]: (successo, messaggio)
        """
        if not self.host:
            return False, "Server SMTP non configurato"
        
        recipients = to_addrs or self.to_addrs
        if not recipients:
            return False, "Nessun destinatario configurato"
        
        if not self.from_addr:
            return False, "Mittente non configurato"
        
        try:
            # Prepara il messaggio
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"{self.subject_prefix} {subject}"
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(recipients)
            msg["Date"] = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")
            
            # Aggiungi corpo
            content_type = "html" if html else "plain"
            msg.attach(MIMEText(body, content_type, "utf-8"))
            
            # Connessione SMTP
            if self.use_tls:
                context = ssl.create_default_context()
                with smtplib.SMTP(self.host, self.port) as server:
                    server.starttls(context=context)
                    if self.user and self.password:
                        server.login(self.user, self.password)
                    server.sendmail(self.from_addr, recipients, msg.as_string())
            else:
                # SSL diretto (porta 465)
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.host, self.port, context=context) as server:
                    if self.user and self.password:
                        server.login(self.user, self.password)
                    server.sendmail(self.from_addr, recipients, msg.as_string())
            
            logger.info(f"Email inviata a {recipients}: {subject}")
            return True, "Email inviata con successo"
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"Errore autenticazione SMTP: {e}")
            return False, f"Errore autenticazione: {str(e)}"
        except smtplib.SMTPException as e:
            logger.error(f"Errore SMTP: {e}")
            return False, f"Errore SMTP: {str(e)}"
        except Exception as e:
            logger.error(f"Errore invio email: {e}")
            return False, f"Errore: {str(e)}"
    
    def send_job_notification(
        self,
        job_name: str,
        status: str,  # success, failed, warning
        source: str,
        destination: str,
        duration: Optional[int] = None,
        error: Optional[str] = None,
        details: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Invia notifica per un job di replica.
        
        Args:
            job_name: Nome del job
            status: Stato (success, failed, warning)
            source: Dataset sorgente
            destination: Dataset destinazione
            duration: Durata in secondi
            error: Messaggio di errore (se fallito)
            details: Dettagli aggiuntivi
        """
        status_emoji = {
            "success": "✅",
            "failed": "❌",
            "warning": "⚠️"
        }.get(status, "ℹ️")
        
        status_text = {
            "success": "Completato",
            "failed": "Fallito",
            "warning": "Attenzione"
        }.get(status, status)
        
        subject = f"{status_emoji} Replica {status_text}: {job_name}"
        
        # Corpo email HTML
        body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background: {'#28a745' if status == 'success' else '#dc3545' if status == 'failed' else '#ffc107'}; 
                   color: {'white' if status != 'warning' else 'black'}; padding: 15px; border-radius: 8px; }}
        .content {{ background: #f8f9fa; padding: 15px; border-radius: 8px; margin-top: 15px; }}
        .label {{ font-weight: bold; color: #495057; }}
        .error {{ background: #f8d7da; border: 1px solid #f5c6cb; padding: 10px; border-radius: 4px; margin-top: 10px; }}
        .footer {{ margin-top: 20px; color: #6c757d; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h2>{status_emoji} Job Replica: {job_name}</h2>
        <p>Stato: <strong>{status_text}</strong></p>
    </div>
    
    <div class="content">
        <p><span class="label">Sorgente:</span> {source}</p>
        <p><span class="label">Destinazione:</span> {destination}</p>
        <p><span class="label">Data:</span> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
        {'<p><span class="label">Durata:</span> ' + str(duration) + ' secondi</p>' if duration else ''}
    </div>
    
    {'<div class="error"><strong>Errore:</strong><br><pre>' + (error or '') + '</pre></div>' if error else ''}
    
    {'<div class="content"><strong>Dettagli:</strong><br>' + (details or '') + '</div>' if details else ''}
    
    <div class="footer">
        <p>Questa email è stata generata automaticamente da Sanoid Manager.</p>
    </div>
</body>
</html>
"""
        
        return self.send_email(subject, body, html=True)
    
    def send_test_email(self) -> Tuple[bool, str]:
        """Invia un'email di test"""
        subject = "🧪 Email di Test"
        body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .success {{ background: #d4edda; border: 1px solid #c3e6cb; padding: 20px; border-radius: 8px; }}
    </style>
</head>
<body>
    <div class="success">
        <h2>✅ Test Notifiche Email</h2>
        <p>Se stai leggendo questa email, la configurazione SMTP è corretta!</p>
        <p><strong>Server:</strong> {self.host}:{self.port}</p>
        <p><strong>Mittente:</strong> {self.from_addr}</p>
        <p><strong>Data test:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
    </div>
</body>
</html>
"""
        return self.send_email(subject, body, html=True)


# Istanza singleton
email_service = EmailService()

