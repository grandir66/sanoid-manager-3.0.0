# 🗃️ Sanoid Manager

**Gestione centralizzata di snapshot ZFS e replica per infrastrutture Proxmox VE**

![Version](https://img.shields.io/badge/version-2.0.0-blue)
![Python](https://img.shields.io/badge/python-3.9+-green)
![License](https://img.shields.io/badge/license-MIT-orange)

---

## ✨ Caratteristiche

- **🔐 Autenticazione Integrata** - Login con credenziali Proxmox VE (PAM, PVE, LDAP, AD)
- **🖥️ Dashboard Centralizzata** - Monitora tutti i tuoi nodi Proxmox da un'unica interfaccia
- **📸 Gestione Snapshot** - Configura Sanoid per snapshot automatici con policy personalizzabili
- **🔄 Replica ZFS** - Sincronizza dataset tra nodi con Syncoid, scheduling incluso
- **🎮 Registrazione VM** - Registra automaticamente le VM replicate sul nodo di destinazione
- **👥 Gestione Utenti** - Ruoli (Admin, Operator, Viewer) con permessi granulari
- **📊 Audit Log** - Tracciamento completo di tutte le operazioni
- **🔔 Notifiche** - Email, Webhook, Telegram per alert e report
- **🎨 Interfaccia Moderna** - Web UI responsive e intuitiva

---

## 📋 Requisiti

### Nodo Manager (dove installi Sanoid Manager)
- Proxmox VE 7.x / 8.x (o Debian 11/12)
- ZFS installato e configurato
- Python 3.9+
- Accesso root

### Nodi Gestiti
- Proxmox VE con ZFS
- SSH accessibile (porta 22 di default)
- Chiave SSH del nodo manager autorizzata

---

## 🚀 Installazione Rapida

### 1. Scarica e installa

```bash
# Scarica il pacchetto
cd /tmp
wget https://github.com/yourusername/sanoid-manager/releases/download/v1.1.0/sanoid-manager-1.1.0.tar.gz
tar xzf sanoid-manager-1.1.0.tar.gz
cd sanoid-manager-1.1.0

# Rendi eseguibile e avvia l'installer
chmod +x install.sh
./install.sh
```

### 2. Setup Iniziale

1. Apri il browser su: `http://<IP-NODO-MANAGER>:8420`
2. Completa il wizard di setup:
   - Crea l'account amministratore
   - Configura il metodo di autenticazione (Proxmox o locale)
   - Imposta le preferenze di base

### 3. Configura Accesso SSH ai Nodi

L'installer mostrerà la chiave pubblica SSH. Copiala su ogni nodo:

```bash
# Per ogni nodo Proxmox da gestire:
ssh-copy-id -i /root/.ssh/id_rsa.pub root@192.168.1.10
ssh-copy-id -i /root/.ssh/id_rsa.pub root@192.168.1.11
# ... etc
```

### 4. Verifica Installazione

```bash
# Esegui lo script di test
./test-installation.sh

# Per test completi (include pytest)
./test-installation.sh --full
```

---

## 🔐 Autenticazione

### Metodi Supportati

| Metodo | Descrizione |
|--------|-------------|
| **Proxmox** | Login con credenziali Proxmox VE |
| **Locale** | Utenti gestiti direttamente in Sanoid Manager |

### Autenticazione Proxmox

Sanoid Manager può autenticare gli utenti direttamente contro Proxmox VE:

1. **Realm PAM**: Utenti locali del sistema Linux
2. **Realm PVE**: Utenti nativi Proxmox
3. **Realm LDAP/AD**: Utenti da directory LDAP o Active Directory

Configurazione:
1. Vai su **Impostazioni** → **Autenticazione**
2. Seleziona "Proxmox" come metodo primario
3. Scegli il nodo Proxmox di riferimento per l'autenticazione
4. Configura il realm predefinito

### Ruoli Utente

| Ruolo | Visualizza | Crea/Modifica | Admin |
|-------|------------|---------------|-------|
| **Viewer** | ✅ | ❌ | ❌ |
| **Operator** | ✅ | ✅ | ❌ |
| **Admin** | ✅ | ✅ | ✅ |

### Restrizione Nodi

Gli utenti possono essere limitati a gestire solo specifici nodi:
- Vai su **Impostazioni** → **Utenti**
- Modifica l'utente
- Seleziona i nodi consentiti

---

## 📖 Guida all'Uso

### Aggiungere un Nodo

1. Vai su **Nodi** → **Aggiungi Nodo**
2. Inserisci:
   - **Nome**: identificativo (es. `pve-node-01`)
   - **Hostname/IP**: indirizzo del nodo
   - **Porta SSH**: default 22
   - **Utente SSH**: default root
   - **Chiave SSH**: `/root/.ssh/id_rsa`
3. Clicca **Aggiungi** e poi **Test** per verificare la connessione

### Configurare Snapshot (Sanoid)

1. Vai su **Snapshot**
2. Seleziona un nodo dal dropdown
3. Per ogni dataset che vuoi proteggere:
   - Abilita la checkbox **Sanoid**
   - Scegli un **Template** di retention:
     
     | Template | Hourly | Daily | Weekly | Monthly | Yearly |
     |----------|--------|-------|--------|---------|--------|
     | production | 48 | 90 | 12 | 24 | 5 |
     | default | 24 | 30 | 4 | 12 | 0 |
     | minimal | 12 | 7 | 0 | 0 | 0 |
     | backup | 0 | 30 | 8 | 12 | 2 |
     | vm | 24 | 14 | 4 | 6 | 0 |

4. Clicca **Applica Config** per salvare sul nodo

### Creare un Job di Replica

1. Vai su **Replica** → **Nuovo Job**
2. Configura:
   - **Nome**: identificativo del job
   - **Nodo Sorgente**: da dove replicare
   - **Dataset Sorgente**: es. `rpool/data/vm-100-disk-0`
   - **Nodo Destinazione**: dove replicare
   - **Dataset Destinazione**: es. `rpool/replica/vm-100-disk-0`
   - **Schedule** (opzionale): formato cron, es:
     - `0 */4 * * *` = ogni 4 ore
     - `0 2 * * *` = ogni notte alle 2:00
     - `*/30 * * * *` = ogni 30 minuti
3. Opzioni avanzate:
   - **Ricorsivo**: replica anche sotto-dataset
   - **Compressione**: lz4 (default), gzip, zstd
   - **Registra VM**: registra automaticamente la VM sul nodo destinazione

### Registrazione VM Post-Replica

Per avere una VM funzionante sul nodo di destinazione dopo la replica:

1. Nella creazione del job, abilita **Registra VM dopo replica**
2. Inserisci il **VMID** e il **Tipo** (qemu/lxc)
3. Dopo la sincronizzazione, Sanoid Manager:
   - Copia il file di configurazione dalla sorgente
   - Lo adatta per il nodo destinazione
   - Registra la VM in Proxmox

> ⚠️ La VM registrata sarà in stato **stopped**. Avviala manualmente solo in caso di failover.

---

## ⚙️ Configurazione

### Impostazioni Generali

Vai su **Impostazioni** → **Generale**:
- **Lingua**: Italiano/Inglese
- **Tema**: Chiaro/Scuro
- **Timezone**: Fuso orario per log e scheduling

### Configurazione Notifiche

#### Email (SMTP)
```
Server SMTP: smtp.example.com
Porta: 587
TLS: Abilitato
Username: sanoid@example.com
Password: ********
Destinatario: admin@example.com
```

#### Webhook
```
URL: https://hooks.slack.com/services/xxx
Metodo: POST
Header: Content-Type: application/json
```

#### Telegram
```
Bot Token: 123456789:ABC...
Chat ID: -1001234567890
```

### Variabili d'Ambiente

File: `/etc/sanoid-manager/sanoid-manager.env`

```bash
# Chiave segreta JWT (generata automaticamente)
SANOID_MANAGER_SECRET_KEY=your-secret-key

# Database
SANOID_MANAGER_DB=/var/lib/sanoid-manager/sanoid-manager.db

# Porta web
SANOID_MANAGER_PORT=8420

# Scadenza token (minuti)
SANOID_MANAGER_TOKEN_EXPIRE=480

# Origini CORS (vuoto = solo same-origin)
SANOID_MANAGER_CORS_ORIGINS=

# Livello log
SANOID_MANAGER_LOG_LEVEL=INFO
```

---

## 🔧 Amministrazione

### Comandi Servizio

```bash
# Stato
systemctl status sanoid-manager

# Avvia/Ferma/Riavvia
systemctl start sanoid-manager
systemctl stop sanoid-manager
systemctl restart sanoid-manager

# Log in tempo reale
journalctl -u sanoid-manager -f

# Log applicazione
tail -f /var/log/sanoid-manager/sanoid-manager.log
```

### Backup Database

```bash
# Backup manuale
cp /var/lib/sanoid-manager/sanoid-manager.db ~/sanoid-manager-backup-$(date +%Y%m%d).db

# Restore
systemctl stop sanoid-manager
cp ~/sanoid-manager-backup.db /var/lib/sanoid-manager/sanoid-manager.db
systemctl start sanoid-manager
```

### Aggiornamento

```bash
# Scarica nuova versione
cd /tmp
wget https://github.com/yourusername/sanoid-manager/releases/download/vX.Y.Z/sanoid-manager-X.Y.Z.tar.gz
tar xzf sanoid-manager-X.Y.Z.tar.gz
cd sanoid-manager-X.Y.Z

# L'installer rileva l'installazione esistente e fa upgrade
./install.sh
```

### Disinstallazione

```bash
./install.sh --uninstall
```

---

## 📁 Struttura Directory

```
/opt/sanoid-manager/          # Applicazione
├── main.py                   # Entry point FastAPI
├── database.py               # Models SQLAlchemy
├── routers/                  # API endpoints
│   ├── auth.py               # Autenticazione
│   ├── nodes.py
│   ├── snapshots.py
│   ├── sync_jobs.py
│   ├── vms.py
│   ├── logs.py
│   └── settings.py
├── services/                 # Business logic
│   ├── auth_service.py       # JWT e gestione utenti
│   ├── proxmox_auth_service.py # Auth Proxmox
│   ├── ssh_service.py
│   ├── sanoid_service.py
│   ├── syncoid_service.py
│   ├── proxmox_service.py
│   └── scheduler.py
├── tests/                    # Test suite
├── frontend/
│   └── dist/
│       └── index.html        # Single-page application
└── venv/                     # Python virtual environment

/etc/sanoid-manager/          # Configurazione
└── sanoid-manager.env        # Variabili d'ambiente

/var/lib/sanoid-manager/      # Dati persistenti
└── sanoid-manager.db         # Database SQLite

/var/log/sanoid-manager/      # Log
└── sanoid-manager.log
```

---

## 🔒 Sicurezza

### Best Practices

1. **Accesso Rete**: Limita l'accesso alla porta 8420 solo alla rete di gestione
2. **SSH**: Usa chiavi SSH dedicate, non condividere con altri servizi
3. **Password**: Usa password complesse per l'admin locale
4. **HTTPS**: Configura un reverse proxy con SSL

### Firewall

```bash
# UFW
ufw allow from 192.168.100.0/24 to any port 8420

# iptables
iptables -A INPUT -p tcp --dport 8420 -s 192.168.100.0/24 -j ACCEPT
iptables -A INPUT -p tcp --dport 8420 -j DROP
```

### Reverse Proxy con SSL (Nginx)

```nginx
server {
    listen 443 ssl;
    server_name sanoid.example.com;
    
    ssl_certificate /etc/ssl/certs/sanoid.pem;
    ssl_certificate_key /etc/ssl/private/sanoid.key;
    
    location / {
        proxy_pass http://127.0.0.1:8420;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 🐛 Troubleshooting

### Il servizio non parte

```bash
# Controlla i log
journalctl -u sanoid-manager -n 50

# Verifica permessi
ls -la /opt/sanoid-manager/
ls -la /var/lib/sanoid-manager/

# Testa manualmente
cd /opt/sanoid-manager
source venv/bin/activate
python -c "from main import app; print('OK')"
```

### Errore autenticazione

```bash
# Verifica configurazione
cat /etc/sanoid-manager/sanoid-manager.env

# Reset password admin (da implementare)
cd /opt/sanoid-manager
source venv/bin/activate
python -c "
from database import SessionLocal, User
from services.auth_service import auth_service
db = SessionLocal()
user = db.query(User).filter(User.username == 'admin').first()
if user:
    user.hashed_password = auth_service.get_password_hash('newpassword')
    db.commit()
    print('Password reset!')
"
```

### Connessione SSH fallisce

```bash
# Testa connessione manuale
ssh -i /root/.ssh/id_rsa -p 22 root@hostname "echo OK"

# Verifica chiave autorizzata sul nodo remoto
ssh root@hostname "cat ~/.ssh/authorized_keys"
```

### Sanoid non crea snapshot

```bash
# Verifica config sul nodo
ssh root@nodo "cat /etc/sanoid/sanoid.conf"

# Esegui manualmente
ssh root@nodo "sanoid --cron --verbose"

# Verifica timer systemd
ssh root@nodo "systemctl status sanoid.timer"
```

---

## 📝 API Reference

Base URL: `http://localhost:8420/api`

Documentazione interattiva: `http://localhost:8420/docs`

### Autenticazione

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| POST | `/auth/login` | Login utente |
| POST | `/auth/logout` | Logout |
| POST | `/auth/refresh` | Rinnova token |
| GET | `/auth/me` | Info utente corrente |

### Nodi

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/nodes/` | Lista nodi |
| POST | `/nodes/` | Crea nodo |
| GET | `/nodes/{id}` | Dettaglio nodo |
| PUT | `/nodes/{id}` | Modifica nodo |
| DELETE | `/nodes/{id}` | Elimina nodo |
| POST | `/nodes/{id}/test` | Test connessione |
| GET | `/nodes/{id}/datasets` | Lista dataset ZFS |

### Snapshot

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/snapshots/node/{id}` | Lista snapshot |
| POST | `/snapshots/node/{id}/apply-config` | Applica config Sanoid |
| DELETE | `/snapshots/{name}` | Elimina snapshot |

### Replica

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/sync-jobs/` | Lista job |
| POST | `/sync-jobs/` | Crea job |
| GET | `/sync-jobs/{id}` | Dettaglio job |
| PUT | `/sync-jobs/{id}` | Modifica job |
| DELETE | `/sync-jobs/{id}` | Elimina job |
| POST | `/sync-jobs/{id}/run` | Esegui job |

### Impostazioni

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/settings/` | Leggi impostazioni |
| PUT | `/settings/` | Aggiorna impostazioni |
| GET | `/settings/auth` | Config autenticazione |
| PUT | `/settings/auth` | Aggiorna auth config |

---

## 🧪 Testing

### Esegui Test

```bash
cd /opt/sanoid-manager
source venv/bin/activate

# Tutti i test
pytest tests/ -v

# Con coverage
pytest tests/ -v --cov=. --cov-report=html

# Test specifico
pytest tests/test_auth.py -v
```

---

## 🤝 Contribuire

1. Fork del repository
2. Crea un branch (`git checkout -b feature/AmazingFeature`)
3. Commit (`git commit -m 'Add AmazingFeature'`)
4. Push (`git push origin feature/AmazingFeature`)
5. Apri una Pull Request

---

## 📄 Licenza

MIT License - vedi [LICENSE](LICENSE) per dettagli.

---

## 🙏 Credits

- [Sanoid/Syncoid](https://github.com/jimsalterjrs/sanoid) - Jim Salter
- [Proxmox VE](https://www.proxmox.com/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Vue.js](https://vuejs.org/)

---

## 📋 Changelog

Vedi [CHANGELOG.md](CHANGELOG.md) per la lista completa delle modifiche.
