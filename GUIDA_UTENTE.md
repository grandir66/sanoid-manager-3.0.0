# 📘 Guida Utente - Sanoid Manager

## Gestione Centralizzata Snapshot e Replica ZFS per Proxmox VE

---

## Indice

1. [Introduzione](#1-introduzione)
2. [Installazione](#2-installazione)
3. [Primo Accesso](#3-primo-accesso)
4. [Gestione Nodi](#4-gestione-nodi)
5. [Configurazione Snapshot](#5-configurazione-snapshot)
6. [Job di Replica](#6-job-di-replica)
7. [Gestione VM](#7-gestione-vm)
8. [Monitoraggio e Log](#8-monitoraggio-e-log)
9. [Manutenzione](#9-manutenzione)
10. [Troubleshooting](#10-troubleshooting)
11. [Riferimento API](#11-riferimento-api)

---

## 1. Introduzione

### Cos'è Sanoid Manager?

Sanoid Manager è un'interfaccia web per gestire:
- **Snapshot ZFS automatici** tramite Sanoid
- **Replica ZFS** tra nodi Proxmox tramite Syncoid
- **Registrazione VM** replicate sul nodo di destinazione

### Architettura

```
┌─────────────────────────────────────────────────────────────┐
│                    SANOID MANAGER                           │
│                   (Nodo Principale)                         │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  Web UI     │  │  REST API   │  │  Scheduler  │         │
│  │  (Vue.js)   │  │  (FastAPI)  │  │  (Cron)     │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│                          │                                  │
│                    ┌─────┴─────┐                           │
│                    │  SQLite   │                           │
│                    │  Database │                           │
│                    └───────────┘                           │
└─────────────────────────────────────────────────────────────┘
           │                    │                    │
           │ SSH                │ SSH                │ SSH
           ▼                    ▼                    ▼
    ┌────────────┐       ┌────────────┐       ┌────────────┐
    │  Proxmox   │       │  Proxmox   │       │  Proxmox   │
    │  Node 1    │◄─────►│  Node 2    │◄─────►│  Node 3    │
    │            │ ZFS   │            │ ZFS   │            │
    │  Sanoid    │ Send  │  Sanoid    │ Send  │  Sanoid    │
    └────────────┘       └────────────┘       └────────────┘
```

### Requisiti

| Componente | Requisito |
|------------|-----------|
| Sistema Operativo | Proxmox VE 7.x/8.x o Debian 11/12 |
| Storage | ZFS configurato |
| Python | 3.9 o superiore |
| Rete | SSH accessibile tra i nodi |
| Porta | 8420 (configurabile) |

---

## 2. Installazione

### 2.1 Download e Estrazione

```bash
# Scarica il pacchetto (sostituisci con il tuo metodo)
cd /tmp

# Estrai
tar -xzf sanoid-manager-1.0.0.tar.gz
cd sanoid-manager-1.0.0
```

### 2.2 Esecuzione Installer

```bash
chmod +x install.sh
./install.sh
```

L'installer esegue automaticamente:
1. ✅ Verifica prerequisiti (ZFS, Python)
2. ✅ Installa dipendenze di sistema
3. ✅ Installa Sanoid/Syncoid
4. ✅ Crea virtual environment Python
5. ✅ Installa dipendenze Python
6. ✅ Copia file applicazione
7. ✅ Configura servizio systemd
8. ✅ Genera chiavi SSH
9. ✅ Avvia il servizio

### 2.3 Configurazione SSH per Nodi Remoti

L'installer mostra la chiave pubblica SSH. Copiala su ogni nodo:

```bash
# Per ogni nodo Proxmox da gestire
ssh-copy-id -i /root/.ssh/id_rsa.pub root@192.168.1.10
ssh-copy-id -i /root/.ssh/id_rsa.pub root@192.168.1.11
```

Verifica la connessione:
```bash
ssh -i /root/.ssh/id_rsa root@192.168.1.10 "hostname"
```

### 2.4 Verifica Installazione

```bash
# Stato servizio
systemctl status sanoid-manager

# Test API
curl http://localhost:8420/api/health
# Output atteso: {"status":"healthy","version":"1.0.0"}
```

---

## 3. Primo Accesso

### 3.1 Accesso Web

Apri il browser e vai a:
```
http://<IP-SERVER>:8420
```

### 3.2 Dashboard

La dashboard mostra:
- **Nodi Gestiti**: numero totale di nodi configurati
- **Nodi Online**: nodi attualmente raggiungibili
- **Job Replica**: numero di job di sincronizzazione configurati
- **Sync Completati**: operazioni completate nelle ultime 24h

### 3.3 Navigazione

| Menu | Funzione |
|------|----------|
| 🏠 Dashboard | Panoramica generale |
| 🖥️ Nodi | Gestione nodi Proxmox |
| 📸 Snapshot | Configurazione Sanoid |
| 🔄 Replica | Job Syncoid |
| 💻 VM | Gestione virtual machine |
| 📋 Log | Storico operazioni |
| ⚙️ Impostazioni | Configurazione globale |

---

## 4. Gestione Nodi

### 4.1 Aggiungere un Nodo

1. Vai su **Nodi** → **Aggiungi Nodo**
2. Compila i campi:

| Campo | Descrizione | Esempio |
|-------|-------------|---------|
| Nome | Identificativo univoco | `pve-prod-01` |
| Hostname/IP | Indirizzo del nodo | `192.168.1.10` |
| Porta SSH | Porta SSH (default 22) | `22` |
| Utente SSH | Utente per connessione | `root` |
| Chiave SSH | Percorso chiave privata | `/root/.ssh/id_rsa` |

3. Clicca **Aggiungi**

### 4.2 Test Connessione

Dopo aver aggiunto un nodo:
1. Clicca **Test** nella riga del nodo
2. Verifica che lo stato passi a **Online**
3. Verifica che Sanoid sia **Installato**

### 4.3 Installare Sanoid su un Nodo

Se Sanoid non è installato:
1. Clicca **Installa Sanoid** nella riga del nodo
2. Attendi il completamento (può richiedere alcuni minuti)
3. Lo stato cambierà in **Installato**

### 4.4 Refresh Dataset

Per aggiornare la lista dei dataset ZFS:
1. Clicca **Refresh** nella riga del nodo
2. I nuovi dataset saranno disponibili nella sezione Snapshot

---

## 5. Configurazione Snapshot

### 5.1 Concetti Base

**Sanoid** crea snapshot automatici secondo una policy di retention:
- **Hourly**: snapshot ogni ora
- **Daily**: snapshot giornalieri
- **Weekly**: snapshot settimanali
- **Monthly**: snapshot mensili
- **Yearly**: snapshot annuali

### 5.2 Template Predefiniti

| Template | Hourly | Daily | Weekly | Monthly | Yearly | Uso Consigliato |
|----------|--------|-------|--------|---------|--------|-----------------|
| `production` | 48 | 90 | 12 | 24 | 5 | VM critiche, database |
| `default` | 24 | 30 | 4 | 12 | 0 | Uso generale |
| `minimal` | 12 | 7 | 0 | 0 | 0 | Test, sviluppo |
| `backup` | 0 | 30 | 8 | 12 | 2 | Storage backup |
| `vm` | 24 | 14 | 4 | 6 | 0 | VM standard |

### 5.3 Configurare un Dataset

1. Vai su **Snapshot**
2. Seleziona un **Nodo** dal dropdown
3. Per ogni dataset:
   - ✅ Abilita la checkbox **Sanoid**
   - Seleziona un **Template** di retention
4. Clicca **Applica Config**

### 5.4 Visualizzare Snapshot

1. Seleziona un nodo
2. Clicca **Carica Snapshot**
3. La tabella mostra tutti gli snapshot con:
   - Nome snapshot
   - Dataset di appartenenza
   - Spazio utilizzato
   - Data creazione

### 5.5 Eseguire Sanoid Manualmente

Per forzare la creazione/pulizia degli snapshot:
1. Vai su **Snapshot**
2. Seleziona il nodo
3. Clicca **Esegui Sanoid**

---

## 6. Job di Replica

### 6.1 Concetti Base

**Syncoid** replica dataset ZFS tra nodi usando:
- **ZFS Send/Receive**: trasferimento incrementale efficiente
- **Compressione**: riduce banda durante il trasferimento
- **SSH**: canale sicuro tra nodi

### 6.2 Creare un Job di Replica

1. Vai su **Replica** → **Nuovo Job**
2. Compila i campi:

| Campo | Descrizione | Esempio |
|-------|-------------|---------|
| Nome | Identificativo del job | `replica-vm-100` |
| Nodo Sorgente | Da dove replicare | `pve-prod-01` |
| Dataset Sorgente | Dataset da replicare | `rpool/data/vm-100-disk-0` |
| Nodo Destinazione | Dove replicare | `pve-backup-01` |
| Dataset Destinazione | Dataset target | `rpool/replica/vm-100-disk-0` |
| Schedule | Frequenza (cron) | `0 */4 * * *` |

### 6.3 Formato Schedule (Cron)

```
┌───────────── minuti (0 - 59)
│ ┌───────────── ore (0 - 23)
│ │ ┌───────────── giorno del mese (1 - 31)
│ │ │ ┌───────────── mese (1 - 12)
│ │ │ │ ┌───────────── giorno della settimana (0 - 6)
│ │ │ │ │
* * * * *
```

**Esempi comuni:**

| Schedule | Significato |
|----------|-------------|
| `*/30 * * * *` | Ogni 30 minuti |
| `0 * * * *` | Ogni ora |
| `0 */4 * * *` | Ogni 4 ore |
| `0 2 * * *` | Ogni notte alle 2:00 |
| `0 2 * * 0` | Ogni domenica alle 2:00 |
| `0 2 1 * *` | Primo del mese alle 2:00 |

### 6.4 Opzioni Avanzate

| Opzione | Descrizione |
|---------|-------------|
| **Ricorsivo** | Replica anche sotto-dataset |
| **Compressione** | `lz4` (veloce), `gzip` (buona), `zstd` (ottima) |
| **Registra VM** | Registra automaticamente la VM sul nodo destinazione |

### 6.5 Eseguire un Job Manualmente

1. Nella lista job, clicca **Run**
2. Il job verrà eseguito in background
3. Lo stato cambierà da `running` a `success` o `failed`

### 6.6 Gestire i Job

| Azione | Descrizione |
|--------|-------------|
| **Run** | Esegue immediatamente il job |
| **Abilita/Disabilita** | Attiva/disattiva lo scheduling automatico |
| **Elimina** | Rimuove il job (non elimina i dati replicati) |

---

## 7. Gestione VM

### 7.1 Visualizzare VM di un Nodo

1. Vai su **VM**
2. Seleziona un **Nodo**
3. La tabella mostra:
   - VMID
   - Nome
   - Tipo (VM o LXC)
   - Stato (running/stopped)

### 7.2 Trovare Dataset di una VM

Per sapere quali dataset ZFS sono associati a una VM:
1. Seleziona il nodo
2. Clicca **Vedi Dataset** nella riga della VM
3. Vedrai l'elenco dei dataset ZFS

### 7.3 Registrazione VM Post-Replica

Quando crei un job di replica con **Registra VM** abilitato:

1. Dopo ogni sincronizzazione, Sanoid Manager:
   - Copia il file di configurazione dalla sorgente
   - Lo salva sul nodo destinazione
   - Registra la VM in Proxmox

2. La VM sul nodo destinazione:
   - Avrà lo stesso VMID
   - Sarà in stato **stopped**
   - Non si avvierà automaticamente

> ⚠️ **Importante**: Avvia la VM destinazione solo in caso di failover/disaster recovery per evitare conflitti di rete e storage.

---

## 8. Monitoraggio e Log

### 8.1 Dashboard

La dashboard mostra statistiche in tempo reale:
- Stato dei nodi
- Job recenti
- Conteggio successi/fallimenti

### 8.2 Pagina Log

Vai su **Log** per vedere:

| Colonna | Descrizione |
|---------|-------------|
| Data | Timestamp dell'operazione |
| Tipo | `sync`, `snapshot`, `register` |
| Nodo | Nodi coinvolti |
| Dataset | Dataset interessati |
| Stato | `success`, `failed`, `running` |
| Durata | Tempo impiegato |
| Trasferito | Dati trasferiti (per sync) |

### 8.3 Filtri Log

Puoi filtrare i log per:
- Tipo di operazione
- Stato
- Job specifico
- Intervallo temporale

### 8.4 Statistiche

Nella pagina Log, le statistiche mostrano:
- Totale operazioni
- Percentuale successo
- Durata media
- Dati trasferiti

---

## 9. Manutenzione

### 9.1 Comandi Systemd

```bash
# Stato del servizio
systemctl status sanoid-manager

# Riavvia il servizio
systemctl restart sanoid-manager

# Ferma il servizio
systemctl stop sanoid-manager

# Avvia il servizio
systemctl start sanoid-manager

# Log in tempo reale
journalctl -u sanoid-manager -f
```

### 9.2 Backup Database

Il database SQLite si trova in:
```
/var/lib/sanoid-manager/sanoid-manager.db
```

Per fare backup:
```bash
cp /var/lib/sanoid-manager/sanoid-manager.db /backup/sanoid-manager-$(date +%Y%m%d).db
```

### 9.3 Pulizia Log

I log più vecchi di 30 giorni (configurabile) vengono eliminati automaticamente.

Per pulizia manuale:
```bash
# Via API
curl -X DELETE "http://localhost:8420/api/logs/cleanup?days=30"
```

### 9.4 Aggiornamento

```bash
# Ferma il servizio
systemctl stop sanoid-manager

# Backup database
cp /var/lib/sanoid-manager/sanoid-manager.db /tmp/sanoid-manager-backup.db

# Estrai nuovo pacchetto
cd /tmp
tar -xzf sanoid-manager-X.X.X.tar.gz

# Copia file
cp -r sanoid-manager-X.X.X/backend/* /opt/sanoid-manager/
cp -r sanoid-manager-X.X.X/frontend/dist/* /opt/sanoid-manager/frontend/dist/

# Riavvia
systemctl start sanoid-manager
```

### 9.5 Disinstallazione

```bash
./install.sh --uninstall
```

---

## 10. Troubleshooting

### 10.1 Il servizio non si avvia

```bash
# Verifica log
journalctl -u sanoid-manager -n 50 --no-pager

# Test manuale
cd /opt/sanoid-manager
source venv/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8420
```

### 10.2 Connessione SSH fallisce

```bash
# Test manuale
ssh -i /root/.ssh/id_rsa -p 22 root@<hostname> "echo OK"

# Verifica chiave autorizzata
ssh root@<hostname> "cat ~/.ssh/authorized_keys"

# Verifica permessi chiave
ls -la /root/.ssh/id_rsa
# Deve essere: -rw------- (600)
```

### 10.3 Sanoid non crea snapshot

```bash
# Verifica config sul nodo
ssh root@<nodo> "cat /etc/sanoid/sanoid.conf"

# Esegui manualmente
ssh root@<nodo> "sanoid --cron --verbose"

# Verifica timer
ssh root@<nodo> "systemctl status sanoid.timer"
```

### 10.4 Syncoid fallisce

```bash
# Test manuale
ssh root@<sorgente> "syncoid --compress=lz4 <source_dataset> root@<dest>:<dest_dataset>"

# Verifica spazio su destinazione
ssh root@<dest> "zfs list -o name,avail"

# Verifica dataset esistente
ssh root@<dest> "zfs list <dest_dataset>"
```

### 10.5 Frontend non carica

```bash
# Verifica file
ls -la /opt/sanoid-manager/frontend/dist/

# Verifica permessi
cat /opt/sanoid-manager/frontend/dist/index.html | head -5

# Test API
curl http://localhost:8420/api/health
```

### 10.6 Errori Comuni

| Errore | Causa | Soluzione |
|--------|-------|-----------|
| `Connection refused` | Servizio non attivo | `systemctl start sanoid-manager` |
| `Permission denied (publickey)` | Chiave SSH non autorizzata | `ssh-copy-id` sul nodo remoto |
| `dataset does not exist` | Dataset non trovato | Verifica nome dataset |
| `cannot receive: destination has snapshots` | Conflitto snapshot | Usa `--force-delete` o elimina snapshot manualmente |

---

## 11. Riferimento API

### Base URL
```
http://<host>:8420/api
```

### Endpoints Principali

#### Nodi
| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/nodes/` | Lista tutti i nodi |
| POST | `/nodes/` | Crea un nuovo nodo |
| GET | `/nodes/{id}` | Dettagli nodo |
| PUT | `/nodes/{id}` | Modifica nodo |
| DELETE | `/nodes/{id}` | Elimina nodo |
| POST | `/nodes/{id}/test` | Test connessione |
| POST | `/nodes/{id}/install-sanoid` | Installa Sanoid |
| GET | `/nodes/{id}/datasets` | Lista dataset ZFS |
| GET | `/nodes/{id}/vms` | Lista VM |

#### Snapshot
| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/snapshots/templates` | Template disponibili |
| GET | `/snapshots/node/{id}` | Lista snapshot |
| POST | `/snapshots/node/{id}` | Crea snapshot manuale |
| PUT | `/snapshots/dataset/{id}/config` | Configura dataset |
| POST | `/snapshots/node/{id}/apply-config` | Applica config Sanoid |

#### Sync Jobs
| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/sync-jobs/` | Lista job |
| POST | `/sync-jobs/` | Crea job |
| GET | `/sync-jobs/{id}` | Dettagli job |
| PUT | `/sync-jobs/{id}` | Modifica job |
| DELETE | `/sync-jobs/{id}` | Elimina job |
| POST | `/sync-jobs/{id}/run` | Esegui job |
| POST | `/sync-jobs/{id}/toggle` | Abilita/disabilita |

#### Log
| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| GET | `/logs/` | Lista log |
| GET | `/logs/stats` | Statistiche |
| GET | `/logs/{id}` | Dettagli log |
| DELETE | `/logs/cleanup` | Pulizia vecchi log |

---

## 📞 Supporto

Per problemi o suggerimenti:
1. Controlla la sezione Troubleshooting
2. Verifica i log con `journalctl -u sanoid-manager -f`
3. Consulta la documentazione Sanoid/Syncoid ufficiale

---

*Sanoid Manager v1.0.0 - Gestione ZFS per Proxmox*
