# Changelog

Tutte le modifiche significative a Sanoid Manager sono documentate in questo file.

Il formato è basato su [Keep a Changelog](https://keepachangelog.com/it-IT/1.0.0/),
e questo progetto aderisce al [Semantic Versioning](https://semver.org/lang/it/).

## [2.0.0] - 2024-12-02

### Breaking Changes
- Rimossa dipendenza da `passlib` - ora usa `bcrypt` direttamente
- Richiede Python 3.9+ (testato fino a Python 3.13)

### Corretto
- **Fix critico**: Errore "password cannot be longer than 72 bytes" su Python 3.13
- Compatibilità bcrypt con Python 3.13 (AttributeError su `bcrypt.__about__`)
- Gestione corretta del troncamento password a 72 byte (limite bcrypt)
- Determinazione directory script nell'installer più robusta
- Percorso frontend dinamico con fallback multipli

### Migliorato
- Messaggi di errore più dettagliati durante il setup
- Logging migliorato per debug
- Gestione errori più robusta in `auth_service.py`

---

## [1.1.0] - 2024-12-02

### Aggiunto

#### 🔐 Sistema di Autenticazione
- **Autenticazione JWT** con token di accesso e refresh
- **Integrazione Proxmox** - Login usando credenziali Proxmox VE (PAM, PVE, LDAP, AD)
- **Autenticazione locale** come fallback quando Proxmox non è disponibile
- **Sistema di ruoli** con tre livelli: Admin, Operator, Viewer
- **Restrizione accesso nodi** - Gli utenti possono essere limitati a specifici nodi
- **Audit log** - Tracciamento completo di tutte le azioni utente
- **Gestione sessioni** con scadenza configurabile
- **API Key** per accesso programmatico

#### ⚙️ Configurazione Avanzata
- **Wizard setup iniziale** per la configurazione del primo amministratore
- **Pagina gestione utenti** (solo admin)
- **Configurazione autenticazione** (metodo, timeout, realm Proxmox)
- **Configurazione notifiche**:
  - SMTP per email
  - Webhook per integrazioni
  - Telegram per messaggi istantanei
- **Tab organizzate** nelle impostazioni (Generale, Autenticazione, Notifiche)

#### 🧪 Test Suite
- **Framework pytest** con configurazione completa
- **Fixtures** per database di test, client API, utenti e token
- **Test autenticazione**: login, logout, token refresh, validazione
- **Test API protette**: nodi, sync jobs, impostazioni
- **Test ruoli**: verifica permessi per admin/operator/viewer
- **Test audit log**: verifica tracciamento azioni

#### 🎨 Frontend Aggiornato
- **Pagina di login** con supporto selezione realm Proxmox
- **Indicatore sessione** nella sidebar con info utente e ruolo
- **Menu navigazione** riorganizzato per sezioni
- **Gestione token automatica** con interceptor Axios
- **Logout e cambio password** integrati

#### 📦 Installer Migliorato
- **Banner grafico** durante l'installazione
- **Progress bar** per le operazioni lunghe
- **Generazione automatica secret key** per JWT
- **Configurazione firewall** automatica (UFW, info iptables)
- **Script test-installation.sh** per verifica post-installazione
- **Supporto upgrade** da versioni precedenti con backup database
- **Comando --uninstall** per rimozione pulita
- **Comando --status** per verifica stato servizio

### Modificato

#### Backend
- **main.py**: Aggiunta protezione autenticazione a tutti i router
- **database.py**: Nuovi modelli User, Session, Audit
- **requirements.txt**: Aggiunte dipendenze jwt, bcrypt, aiohttp
- **Tutti i router**: Aggiunta dipendenza autenticazione

#### Sicurezza
- **CORS**: Configurazione più restrittiva in produzione
- **Password hashing**: Utilizzo bcrypt con salt
- **Token JWT**: Scadenza configurabile, algoritmo HS256

---

## [1.0.0] - 2024-11-15

### Aggiunto
- Gestione centralizzata nodi Proxmox via SSH
- Interfaccia web Vue.js single-page
- Configurazione policy Sanoid (snapshot)
- Scheduling job Syncoid (replica)
- Registrazione automatica VM post-replica
- Log dettagliati operazioni
- API REST documentata con Swagger/OpenAPI

### Caratteristiche Iniziali
- Backend FastAPI con SQLite
- Frontend Vue.js 3 con Tailwind CSS
- Connessione SSH con chiavi
- Cron-like scheduling
- Multi-nodo support
