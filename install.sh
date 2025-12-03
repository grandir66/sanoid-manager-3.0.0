#!/bin/bash
#
# Sanoid Manager v1.1.0 - Script di Installazione
# Gestione centralizzata Sanoid/Syncoid per Proxmox VE
# Con autenticazione integrata Proxmox
#

set -e

# ============== CONFIGURAZIONE ==============
VERSION="3.0.0"
GITHUB_REPO="tuouser/sanoid-manager"  # Modifica con il tuo repo GitHub
INSTALL_DIR="/opt/sanoid-manager"
DATA_DIR="/var/lib/sanoid-manager"
LOG_DIR="/var/log/sanoid-manager"
CONFIG_DIR="/etc/sanoid-manager"
SERVICE_USER="root"
SERVICE_PORT="8420"
PYTHON_MIN_VERSION="3.9"

# Determina directory dello script (metodo robusto)
# Salviamo subito il path prima che cambi con cd
ORIGINAL_PWD="$(pwd)"
if [[ -n "${BASH_SOURCE[0]}" ]] && [[ -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
elif [[ -n "$0" ]] && [[ -f "$0" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
else
    SCRIPT_DIR="$ORIGINAL_PWD"
fi

# Colori per output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# ============== FUNZIONI UTILITÀ ==============

log_info() {
    echo -e "${CYAN}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_step() {
    echo -e "\n${BLUE}${BOLD}▶ $1${NC}"
}

print_banner() {
    clear
    echo -e "${CYAN}"
    cat << 'EOF'
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║     ███████╗ █████╗ ███╗   ██╗ ██████╗ ██╗██████╗             ║
    ║     ██╔════╝██╔══██╗████╗  ██║██╔═══██╗██║██╔══██╗            ║
    ║     ███████╗███████║██╔██╗ ██║██║   ██║██║██║  ██║            ║
    ║     ╚════██║██╔══██║██║╚██╗██║██║   ██║██║██║  ██║            ║
    ║     ███████║██║  ██║██║ ╚████║╚██████╔╝██║██████╔╝            ║
    ║     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚═╝╚═════╝             ║
    ║                                                               ║
    ║              M A N A G E R   v2.0.0                           ║
    ║                                                               ║
    ║     Gestione centralizzata Snapshot ZFS e Replica             ║
    ║           per infrastrutture Proxmox VE                       ║
    ║                                                               ║
    ║     ✓ Autenticazione integrata Proxmox                        ║
    ║     ✓ Gestione multi-nodo via SSH                             ║
    ║     ✓ Scheduling replica automatico                           ║
    ║     ✓ Registrazione VM post-replica                           ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
}

print_progress() {
    local current=$1
    local total=$2
    local desc=$3
    local width=40
    local percentage=$((current * 100 / total))
    local filled=$((width * current / total))
    local empty=$((width - filled))
    
    printf "\r${CYAN}[${NC}"
    printf "%${filled}s" | tr ' ' '█'
    printf "%${empty}s" | tr ' ' '░'
    printf "${CYAN}]${NC} %3d%% - %s" "$percentage" "$desc"
}

confirm() {
    local prompt="$1"
    local default="${2:-y}"
    
    if [[ "$default" == "y" ]]; then
        prompt="$prompt [Y/n]: "
    else
        prompt="$prompt [y/N]: "
    fi
    
    read -p "$prompt" response
    response=${response:-$default}
    [[ "$response" =~ ^[Yy]$ ]]
}

# ============== VERIFICHE PRELIMINARI ==============

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Questo script deve essere eseguito come root"
        echo -e "Esegui: ${YELLOW}sudo $0${NC}"
        exit 1
    fi
}

check_os() {
    log_step "Verifica sistema operativo"
    
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        OS_NAME="$NAME"
        OS_VERSION="$VERSION_ID"
        log_info "Sistema: $OS_NAME $OS_VERSION"
    else
        log_warning "Impossibile determinare il sistema operativo"
    fi
    
    # Check Proxmox
    if command -v pveversion &> /dev/null; then
        PVE_VERSION=$(pveversion --verbose 2>/dev/null | head -1 || echo "Proxmox VE")
        log_success "Rilevato: $PVE_VERSION"
        IS_PROXMOX=true
    else
        log_warning "Proxmox VE non rilevato (installazione su sistema Debian standard)"
        IS_PROXMOX=false
    fi
}

check_zfs() {
    log_step "Verifica ZFS"
    
    if ! command -v zfs &> /dev/null; then
        log_error "ZFS non trovato!"
        echo -e "Installa ZFS prima di procedere:"
        echo -e "  ${YELLOW}apt install zfsutils-linux${NC}"
        exit 1
    fi
    
    ZFS_VERSION=$(zfs version 2>/dev/null | head -1 || modinfo zfs 2>/dev/null | grep ^version | awk '{print $2}' || echo "installato")
    log_success "ZFS disponibile: $ZFS_VERSION"
    
    # Verifica pool ZFS
    POOLS=$(zpool list -H -o name 2>/dev/null | wc -l)
    if [[ "$POOLS" -eq 0 ]]; then
        log_warning "Nessun pool ZFS trovato. Creane uno prima di usare Sanoid Manager."
    else
        log_info "Pool ZFS trovati: $POOLS"
    fi
}

check_python() {
    log_step "Verifica Python"
    
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        PYTHON_PATH=$(which python3)
        log_info "Python $PYTHON_VERSION trovato: $PYTHON_PATH"
        
        if python3 -c "import sys; exit(0 if sys.version_info >= (3, 9) else 1)" 2>/dev/null; then
            log_success "Python $PYTHON_VERSION è compatibile (>= $PYTHON_MIN_VERSION)"
            return 0
        else
            log_warning "Python $PYTHON_VERSION è troppo vecchio (richiesto >= $PYTHON_MIN_VERSION)"
            return 1
        fi
    else
        log_warning "Python 3 non trovato"
        return 1
    fi
}

check_existing_installation() {
    if [[ -d "$INSTALL_DIR" ]]; then
        log_warning "Installazione esistente trovata in $INSTALL_DIR"
        
        if systemctl is-active --quiet sanoid-manager 2>/dev/null; then
            log_info "Servizio sanoid-manager attualmente in esecuzione"
        fi
        
        if ! confirm "Vuoi aggiornare l'installazione esistente?"; then
            log_info "Installazione annullata"
            exit 0
        fi
        
        UPGRADE_MODE=true
    else
        UPGRADE_MODE=false
    fi
}

# ============== INSTALLAZIONE DIPENDENZE ==============

install_system_dependencies() {
    log_step "Installazione dipendenze di sistema"
    
    local packages=(
        python3
        python3-pip
        python3-venv
        python3-dev
        git
        curl
        wget
        # Strumenti per syncoid
        mbuffer
        pv
        # Compressione per syncoid
        lz4
        lzop
        pigz
        zstd
        gzip
        xz-utils
        # SSH
        openssh-client
        # Build
        build-essential
        libffi-dev
        libssl-dev
    )
    
    log_info "Aggiornamento repository..."
    apt-get update -qq
    
    log_info "Installazione pacchetti..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${packages[@]}" 2>/dev/null
    
    log_success "Dipendenze di sistema installate"
}

install_sanoid() {
    log_step "Verifica/Installazione Sanoid"
    
    if command -v sanoid &> /dev/null; then
        SANOID_VERSION=$(sanoid --version 2>&1 | head -1 || echo "installato")
        log_success "Sanoid già installato: $SANOID_VERSION"
        return 0
    fi
    
    log_info "Installazione Sanoid da repository..."
    
    # Installa dipendenze Perl
    apt-get install -y -qq \
        debhelper \
        libcapture-tiny-perl \
        libconfig-inifiles-perl \
        2>/dev/null
    
    # Clona repository
    cd /tmp
    rm -rf sanoid 2>/dev/null || true
    
    log_info "Download Sanoid..."
    git clone --quiet https://github.com/jimsalterjrs/sanoid.git
    cd sanoid
    
    # Checkout ultima release stabile
    LATEST_TAG=$(git describe --tags $(git rev-list --tags --max-count=1) 2>/dev/null || echo "master")
    git checkout "$LATEST_TAG" --quiet 2>/dev/null || true
    log_info "Versione: $LATEST_TAG"
    
    # Prova build pacchetto Debian
    if [[ -d "packages/debian" ]]; then
        ln -sf packages/debian . 2>/dev/null || true
        
        if dpkg-buildpackage -uc -us -b 2>/dev/null; then
            apt-get install -y ../sanoid_*.deb 2>/dev/null || {
                log_warning "Installazione pacchetto fallita, uso metodo manuale"
                _install_sanoid_manual
            }
        else
            log_warning "Build pacchetto fallito, uso metodo manuale"
            _install_sanoid_manual
        fi
    else
        _install_sanoid_manual
    fi
    
    # Configura directory e file
    mkdir -p /etc/sanoid
    
    if [[ ! -f /etc/sanoid/sanoid.defaults.conf ]]; then
        if [[ -f /usr/share/sanoid/sanoid.defaults.conf ]]; then
            cp /usr/share/sanoid/sanoid.defaults.conf /etc/sanoid/
        elif [[ -f sanoid.defaults.conf ]]; then
            cp sanoid.defaults.conf /etc/sanoid/
        fi
    fi
    
    if [[ ! -f /etc/sanoid/sanoid.conf ]]; then
        cat > /etc/sanoid/sanoid.conf << 'SANOID_CONF'
# Sanoid configuration
# Managed by Sanoid Manager
# Configure via web interface: http://localhost:8420

# Default templates are defined in sanoid.defaults.conf
# Add your dataset configurations below or use the web UI
SANOID_CONF
    fi
    
    # Abilita timer systemd se disponibile
    if [[ -f /lib/systemd/system/sanoid.timer ]] || [[ -f /etc/systemd/system/sanoid.timer ]]; then
        systemctl daemon-reload
        systemctl enable sanoid.timer 2>/dev/null || true
        systemctl start sanoid.timer 2>/dev/null || true
        log_info "Timer Sanoid abilitato"
    fi
    
    # Cleanup
    cd /
    rm -rf /tmp/sanoid /tmp/sanoid_*.deb /tmp/sanoid_*.buildinfo /tmp/sanoid_*.changes 2>/dev/null || true
    
    log_success "Sanoid installato"
}

_install_sanoid_manual() {
    log_info "Installazione manuale Sanoid..."
    
    mkdir -p /usr/local/sbin
    cp sanoid syncoid findoid /usr/local/sbin/ 2>/dev/null || cp sanoid syncoid /usr/local/sbin/
    chmod +x /usr/local/sbin/sanoid /usr/local/sbin/syncoid
    [[ -f /usr/local/sbin/findoid ]] && chmod +x /usr/local/sbin/findoid
    
    mkdir -p /etc/sanoid
    [[ -f sanoid.defaults.conf ]] && cp sanoid.defaults.conf /etc/sanoid/
    
    # Crea servizio systemd
    cat > /etc/systemd/system/sanoid.service << 'SYSTEMD_SERVICE'
[Unit]
Description=Sanoid ZFS snapshot service
Requires=zfs.target
After=zfs.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/sanoid --cron
SYSTEMD_SERVICE

    cat > /etc/systemd/system/sanoid.timer << 'SYSTEMD_TIMER'
[Unit]
Description=Run Sanoid every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
SYSTEMD_TIMER

    systemctl daemon-reload
}

# ============== INSTALLAZIONE APPLICAZIONE ==============

create_directories() {
    log_step "Creazione directory"
    
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$DATA_DIR"
    mkdir -p "$LOG_DIR"
    mkdir -p "$CONFIG_DIR"
    
    chmod 750 "$DATA_DIR"
    chmod 750 "$LOG_DIR"
    chmod 750 "$CONFIG_DIR"
    
    log_success "Directory create"
}

create_virtual_environment() {
    log_step "Configurazione ambiente Python"
    
    if [[ "$UPGRADE_MODE" == true ]] && [[ -d "$INSTALL_DIR/venv" ]]; then
        log_info "Aggiornamento virtual environment esistente..."
    else
        log_info "Creazione virtual environment..."
        python3 -m venv "$INSTALL_DIR/venv"
    fi
    
    source "$INSTALL_DIR/venv/bin/activate"
    
    log_info "Aggiornamento pip..."
    pip install --upgrade pip --quiet
    
    log_success "Virtual environment pronto"
}

install_python_dependencies() {
    log_step "Installazione dipendenze Python"
    
    source "$INSTALL_DIR/venv/bin/activate"
    
    # Lista dipendenze
    local deps=(
        "fastapi>=0.104.0"
        "uvicorn[standard]>=0.24.0"
        "python-multipart>=0.0.6"
        "sqlalchemy>=2.0.0"
        "aiosqlite>=0.19.0"
        "paramiko>=3.3.0"
        "croniter>=2.0.0"
        "pydantic>=2.5.0"
        "pydantic[email]>=2.5.0"
        "python-dotenv>=1.0.0"
        "python-jose[cryptography]>=3.3.0"
        "passlib[bcrypt]>=1.7.4"
        "bcrypt>=4.0.0"
        "aiohttp>=3.9.0"
        "aiosmtplib>=3.0.0"
    )
    
    log_info "Installazione ${#deps[@]} pacchetti Python..."
    
    for dep in "${deps[@]}"; do
        pip install "$dep" --quiet 2>/dev/null || {
            log_warning "Errore installazione $dep, riprovo..."
            pip install "$dep" 2>/dev/null || true
        }
    done
    
    deactivate
    log_success "Dipendenze Python installate"
}

copy_application_files() {
    log_step "Copia file applicazione"
    
    # Usa la directory determinata all'avvio dello script
    # Se non trova backend, prova con ORIGINAL_PWD (directory da cui è stato lanciato)
    if [[ ! -d "$SCRIPT_DIR/backend" ]]; then
        if [[ -d "$ORIGINAL_PWD/backend" ]]; then
            SCRIPT_DIR="$ORIGINAL_PWD"
        fi
    fi
    
    log_info "Directory sorgente: $SCRIPT_DIR"
    
    # Backup configurazione esistente se upgrade
    if [[ "$UPGRADE_MODE" == true ]]; then
        if [[ -f "$DATA_DIR/sanoid-manager.db" ]]; then
            cp "$DATA_DIR/sanoid-manager.db" "$DATA_DIR/sanoid-manager.db.backup-$(date +%Y%m%d%H%M%S)"
            log_info "Database backuppato"
        fi
    fi
    
    # Copia backend
    if [[ -d "$SCRIPT_DIR/backend" ]]; then
        log_info "Copia backend..."
        cp -r "$SCRIPT_DIR/backend/"* "$INSTALL_DIR/"
        
        # Rimuovi directory test in produzione (opzionale, mantienili per debug)
        # rm -rf "$INSTALL_DIR/tests" 2>/dev/null || true
        
        log_success "Backend copiato"
    else
        log_error "Directory backend non trovata in $SCRIPT_DIR"
        log_error "Assicurati di eseguire lo script dalla directory del pacchetto estratto"
        log_info "Contenuto directory corrente:"
        ls -la "$SCRIPT_DIR" 2>/dev/null || ls -la "$(pwd)"
        exit 1
    fi
    
    # Copia frontend
    if [[ -d "$SCRIPT_DIR/frontend/dist" ]]; then
        log_info "Copia frontend..."
        mkdir -p "$INSTALL_DIR/frontend/dist"
        cp -r "$SCRIPT_DIR/frontend/dist/"* "$INSTALL_DIR/frontend/dist/"
        log_success "Frontend copiato"
    else
        log_warning "Directory frontend non trovata, l'interfaccia web potrebbe non funzionare"
    fi
    
    # Imposta permessi
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    chmod +x "$INSTALL_DIR/main.py" 2>/dev/null || true
}

generate_secret_key() {
    log_step "Generazione chiave segreta"
    
    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    
    # Salva in file di configurazione
    cat > "$CONFIG_DIR/sanoid-manager.env" << ENV_FILE
# Sanoid Manager Configuration
# Generated: $(date)

# Secret key for JWT tokens (DO NOT SHARE!)
SANOID_MANAGER_SECRET_KEY=$SECRET_KEY

# Database path
SANOID_MANAGER_DB=$DATA_DIR/sanoid-manager.db

# Web server port
SANOID_MANAGER_PORT=$SERVICE_PORT

# Token expiration (minutes)
SANOID_MANAGER_TOKEN_EXPIRE=480

# CORS origins (comma-separated, empty for same-origin only)
SANOID_MANAGER_CORS_ORIGINS=

# Log level
SANOID_MANAGER_LOG_LEVEL=INFO
ENV_FILE

    chmod 600 "$CONFIG_DIR/sanoid-manager.env"
    log_success "Chiave segreta generata e salvata"
}

# ============== CONFIGURAZIONE SERVIZIO ==============

create_systemd_service() {
    log_step "Configurazione servizio systemd"
    
    cat > /etc/systemd/system/sanoid-manager.service << SYSTEMD_UNIT
[Unit]
Description=Sanoid Manager - ZFS Snapshot Management Web Interface
Documentation=https://github.com/yourusername/sanoid-manager
After=network.target network-online.target
Wants=network-online.target
Requires=zfs.target
After=zfs.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR

# Environment
EnvironmentFile=$CONFIG_DIR/sanoid-manager.env
Environment="PATH=$INSTALL_DIR/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Execution
ExecStart=$INSTALL_DIR/venv/bin/uvicorn main:app --host 0.0.0.0 --port $SERVICE_PORT --workers 1
ExecReload=/bin/kill -HUP \$MAINPID

# Restart policy
Restart=always
RestartSec=10
TimeoutStartSec=30
TimeoutStopSec=30

# Logging
StandardOutput=append:$LOG_DIR/sanoid-manager.log
StandardError=append:$LOG_DIR/sanoid-manager.log

# Security (relaxed for SSH operations)
NoNewPrivileges=false
ProtectSystem=false
PrivateTmp=true
ProtectHome=false
ReadWritePaths=$DATA_DIR $LOG_DIR /root/.ssh

# Resource limits
LimitNOFILE=65535
LimitNPROC=4096

[Install]
WantedBy=multi-user.target
SYSTEMD_UNIT

    # Logrotate
    cat > /etc/logrotate.d/sanoid-manager << LOGROTATE_CONF
$LOG_DIR/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 640 $SERVICE_USER $SERVICE_USER
    sharedscripts
    postrotate
        systemctl reload sanoid-manager > /dev/null 2>&1 || true
    endscript
}
LOGROTATE_CONF

    systemctl daemon-reload
    log_success "Servizio systemd configurato"
}

configure_firewall() {
    log_step "Configurazione firewall"
    
    # UFW
    if command -v ufw &> /dev/null && ufw status | grep -q "active"; then
        ufw allow $SERVICE_PORT/tcp comment "Sanoid Manager" 2>/dev/null || true
        log_info "Regola UFW aggiunta per porta $SERVICE_PORT"
    fi
    
    # iptables (solo se non c'è già la regola)
    if command -v iptables &> /dev/null; then
        if ! iptables -C INPUT -p tcp --dport $SERVICE_PORT -j ACCEPT 2>/dev/null; then
            # Non aggiungiamo automaticamente, solo info
            log_info "Per aprire la porta manualmente: iptables -I INPUT -p tcp --dport $SERVICE_PORT -j ACCEPT"
        fi
    fi
    
    # Proxmox firewall
    if [[ "$IS_PROXMOX" == true ]] && [[ -d /etc/pve/firewall ]]; then
        log_info "Per Proxmox firewall, aggiungi la porta $SERVICE_PORT nelle regole del datacenter"
    fi
    
    log_success "Configurazione firewall completata"
}

# ============== CONFIGURAZIONE SSH ==============

setup_ssh_keys() {
    log_step "Configurazione chiavi SSH"
    
    SSH_DIR="/root/.ssh"
    SSH_KEY_PATH="$SSH_DIR/id_rsa"
    SSH_KEY_NAME="sanoid-manager@$(hostname -s)"
    
    mkdir -p "$SSH_DIR"
    chmod 700 "$SSH_DIR"
    
    if [[ ! -f "$SSH_KEY_PATH" ]]; then
        log_info "Generazione nuova chiave SSH..."
        ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_PATH" -N "" -C "$SSH_KEY_NAME" -q
        log_success "Chiave SSH generata"
    else
        log_info "Chiave SSH esistente trovata"
    fi
    
    # Configura SSH client
    if [[ ! -f "$SSH_DIR/config" ]] || ! grep -q "StrictHostKeyChecking" "$SSH_DIR/config" 2>/dev/null; then
        cat >> "$SSH_DIR/config" << SSH_CONFIG

# Sanoid Manager SSH Configuration
Host *
    StrictHostKeyChecking accept-new
    ServerAliveInterval 60
    ServerAliveCountMax 3
    ConnectTimeout 10
SSH_CONFIG
        chmod 600 "$SSH_DIR/config"
    fi
    
    chmod 600 "$SSH_KEY_PATH"
    chmod 644 "$SSH_KEY_PATH.pub"
    
    log_success "Configurazione SSH completata"
}

# ============== AVVIO SERVIZIO ==============

start_service() {
    log_step "Avvio servizio"
    
    systemctl enable sanoid-manager
    
    if [[ "$UPGRADE_MODE" == true ]]; then
        log_info "Riavvio servizio..."
        systemctl restart sanoid-manager
    else
        log_info "Avvio servizio..."
        systemctl start sanoid-manager
    fi
    
    # Attendi avvio
    sleep 3
    
    # Verifica stato
    local retries=5
    while [[ $retries -gt 0 ]]; do
        if systemctl is-active --quiet sanoid-manager; then
            log_success "Servizio avviato correttamente"
            return 0
        fi
        sleep 2
        ((retries--))
    done
    
    log_error "Errore avvio servizio"
    echo -e "Controlla i log con: ${YELLOW}journalctl -u sanoid-manager -n 50${NC}"
    return 1
}

verify_installation() {
    log_step "Verifica installazione"
    
    # Test API health
    sleep 2
    if curl -s -o /dev/null -w "%{http_code}" "http://localhost:$SERVICE_PORT/api/health" | grep -q "200"; then
        log_success "API health check: OK"
    else
        log_warning "API health check: FAILED (il servizio potrebbe essere ancora in avvio)"
    fi
    
    # Verifica file
    [[ -f "$INSTALL_DIR/main.py" ]] && log_success "Backend: OK" || log_warning "Backend: MISSING"
    [[ -f "$INSTALL_DIR/frontend/dist/index.html" ]] && log_success "Frontend: OK" || log_warning "Frontend: MISSING"
    [[ -f "$CONFIG_DIR/sanoid-manager.env" ]] && log_success "Config: OK" || log_warning "Config: MISSING"
}

# ============== OUTPUT FINALE ==============

print_ssh_key() {
    echo ""
    echo -e "${YELLOW}══════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  IMPORTANTE: Chiave pubblica SSH per i nodi remoti${NC}"
    echo -e "${YELLOW}══════════════════════════════════════════════════════════════${NC}"
    echo ""
    cat "/root/.ssh/id_rsa.pub"
    echo ""
    echo -e "${CYAN}Copia questa chiave su ogni nodo Proxmox da gestire:${NC}"
    echo ""
    echo -e "  ${GREEN}ssh-copy-id -i /root/.ssh/id_rsa.pub root@<IP-NODO>${NC}"
    echo ""
}

print_completion() {
    local LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
    
    echo ""
    echo -e "${GREEN}"
    cat << 'EOF'
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║         ✓ INSTALLAZIONE COMPLETATA CON SUCCESSO!              ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
EOF
    echo -e "${NC}"
    
    echo -e "${BOLD}Accedi all'interfaccia web:${NC}"
    echo ""
    echo -e "    ${GREEN}➜${NC}  http://${LOCAL_IP}:${SERVICE_PORT}"
    echo -e "    ${GREEN}➜${NC}  http://localhost:${SERVICE_PORT}"
    echo ""
    
    echo -e "${BOLD}Primo accesso:${NC}"
    echo ""
    echo -e "    1. Apri il browser all'indirizzo sopra"
    echo -e "    2. Crea l'account amministratore nel wizard di setup"
    echo -e "    3. Configura l'autenticazione (Proxmox o locale)"
    echo -e "    4. Aggiungi i nodi Proxmox usando la chiave SSH mostrata sopra"
    echo ""
    
    echo -e "${BOLD}Comandi utili:${NC}"
    echo ""
    echo -e "    Stato servizio:     ${CYAN}systemctl status sanoid-manager${NC}"
    echo -e "    Riavvia servizio:   ${CYAN}systemctl restart sanoid-manager${NC}"
    echo -e "    Visualizza log:     ${CYAN}journalctl -u sanoid-manager -f${NC}"
    echo -e "    Log applicazione:   ${CYAN}tail -f $LOG_DIR/sanoid-manager.log${NC}"
    echo ""
    
    echo -e "${BOLD}Directory:${NC}"
    echo ""
    echo -e "    Applicazione:   $INSTALL_DIR"
    echo -e "    Configurazione: $CONFIG_DIR"
    echo -e "    Database:       $DATA_DIR/sanoid-manager.db"
    echo -e "    Log:            $LOG_DIR"
    echo ""
    
    echo -e "${BOLD}Documentazione:${NC}"
    echo ""
    echo -e "    API Docs:       http://${LOCAL_IP}:${SERVICE_PORT}/docs"
    echo -e "    Health Check:   http://${LOCAL_IP}:${SERVICE_PORT}/api/health"
    echo ""
}

# ============== DISINSTALLAZIONE ==============

uninstall() {
    print_banner
    log_step "Disinstallazione Sanoid Manager"
    
    # Stop servizio
    if systemctl is-active --quiet sanoid-manager 2>/dev/null; then
        log_info "Arresto servizio..."
        systemctl stop sanoid-manager
    fi
    
    # Disabilita servizio
    systemctl disable sanoid-manager 2>/dev/null || true
    
    # Rimuovi file systemd
    rm -f /etc/systemd/system/sanoid-manager.service
    rm -f /etc/logrotate.d/sanoid-manager
    systemctl daemon-reload
    
    log_success "Servizio rimosso"
    
    # Chiedi conferma per dati
    echo ""
    if confirm "Eliminare i dati (database, configurazione)?"; then
        rm -rf "$DATA_DIR"
        rm -rf "$CONFIG_DIR"
        log_success "Dati eliminati"
    else
        log_info "Dati mantenuti in $DATA_DIR e $CONFIG_DIR"
    fi
    
    # Chiedi conferma per applicazione
    if confirm "Eliminare l'applicazione ($INSTALL_DIR)?"; then
        rm -rf "$INSTALL_DIR"
        log_success "Applicazione eliminata"
    else
        log_info "Applicazione mantenuta in $INSTALL_DIR"
    fi
    
    # Log directory
    if confirm "Eliminare i log ($LOG_DIR)?"; then
        rm -rf "$LOG_DIR"
        log_success "Log eliminati"
    fi
    
    echo ""
    log_success "Disinstallazione completata"
    echo ""
    echo -e "${CYAN}Nota: Sanoid/Syncoid NON sono stati rimossi.${NC}"
    echo -e "Per rimuoverli: ${YELLOW}apt remove sanoid${NC}"
}

# ============== FUNZIONI GITHUB ==============

download_from_github() {
    local version="${1:-latest}"
    local temp_dir="/tmp/sanoid-manager-download"
    
    rm -rf "$temp_dir"
    mkdir -p "$temp_dir"
    
    if [[ "$version" == "latest" ]]; then
        log_info "Recupero ultima versione da GitHub..."
        local api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
        version=$(curl -s "$api_url" | grep -oP '"tag_name": "\K[^"]+' | sed 's/^v//' || echo "")
        
        if [[ -z "$version" ]]; then
            log_error "Impossibile ottenere l'ultima versione da GitHub"
            return 1
        fi
        log_info "Ultima versione: $version"
    fi
    
    local download_url="https://github.com/${GITHUB_REPO}/releases/download/v${version}/sanoid-manager-${version}.tar.gz"
    local archive="$temp_dir/sanoid-manager-${version}.tar.gz"
    
    log_info "Download da: $download_url"
    
    if ! curl -L -o "$archive" "$download_url" 2>/dev/null; then
        # Prova senza 'v' prefix
        download_url="https://github.com/${GITHUB_REPO}/releases/download/${version}/sanoid-manager-${version}.tar.gz"
        if ! curl -L -o "$archive" "$download_url" 2>/dev/null; then
            log_error "Download fallito"
            return 1
        fi
    fi
    
    log_info "Estrazione archivio..."
    tar -xzf "$archive" -C "$temp_dir"
    
    # Trova la directory estratta
    local extracted=$(find "$temp_dir" -maxdepth 1 -type d -name "sanoid-manager*" | head -1)
    if [[ -z "$extracted" ]]; then
        extracted="$temp_dir"
    fi
    
    # Aggiorna SCRIPT_DIR per puntare ai file scaricati
    SCRIPT_DIR="$extracted"
    
    log_success "Download completato"
    return 0
}

install_from_github() {
    local version="${1:-latest}"
    
    echo ""
    log_info "Installazione da GitHub..."
    
    if ! download_from_github "$version"; then
        exit 1
    fi
    
    # Continua con installazione normale
    do_install
}

show_install_menu() {
    echo ""
    echo -e "${CYAN}Scegli modalità di installazione:${NC}"
    echo ""
    echo "  1) Installa da file locali (questa directory)"
    echo "  2) Installa da GitHub (ultima versione)"
    echo "  3) Installa da GitHub (versione specifica)"
    echo "  4) Aggiorna installazione esistente"
    echo "  5) Disinstalla"
    echo "  6) Esci"
    echo ""
    read -p "Seleziona [1-6]: " choice
    
    case $choice in
        1)
            do_install
            ;;
        2)
            install_from_github "latest"
            ;;
        3)
            read -p "Versione (es: 3.0.0): " ver
            install_from_github "$ver"
            ;;
        4)
            update_existing
            ;;
        5)
            uninstall
            ;;
        6)
            exit 0
            ;;
        *)
            log_error "Opzione non valida"
            show_install_menu
            ;;
    esac
}

update_existing() {
    if [[ ! -d "$INSTALL_DIR" ]]; then
        log_error "Sanoid Manager non installato"
        show_install_menu
        return
    fi
    
    echo ""
    log_info "Aggiornamento installazione esistente..."
    
    # Usa lo script update.sh se disponibile
    if [[ -f "$INSTALL_DIR/update.sh" ]]; then
        bash "$INSTALL_DIR/update.sh"
    else
        # Fallback: reinstalla da GitHub
        install_from_github "latest"
    fi
}

do_install() {
    check_existing_installation
    
    echo ""
    if ! confirm "Procedere con l'installazione di Sanoid Manager v$VERSION?"; then
        log_info "Installazione annullata"
        exit 0
    fi
    
    echo ""
    log_info "Inizio installazione..."
    echo ""
    
    # Installazione
    install_system_dependencies
    
    if ! check_python; then
        log_error "Python >= $PYTHON_MIN_VERSION richiesto"
        exit 1
    fi
    
    install_sanoid
    create_directories
    create_virtual_environment
    install_python_dependencies
    copy_application_files
    generate_secret_key
    create_systemd_service
    configure_firewall
    setup_ssh_keys
    start_service
    verify_installation
    
    # Output finale
    print_ssh_key
    print_completion_message
}

# ============== FUNZIONE PRINCIPALE ==============

main() {
    print_banner
    
    check_root
    check_os
    check_zfs
    
    # Gestione parametri da riga di comando
    case "${1:-}" in
        --github)
            install_from_github "${2:-latest}"
            exit 0
            ;;
        --update)
            update_existing
            exit 0
            ;;
        --uninstall)
            uninstall
            exit 0
            ;;
        --local)
            do_install
            exit 0
            ;;
        --help|-h)
            echo "Uso: $0 [OPZIONE]"
            echo ""
            echo "Opzioni:"
            echo "  --local           Installa da file locali"
            echo "  --github [VER]    Installa da GitHub (default: latest)"
            echo "  --update          Aggiorna installazione esistente"
            echo "  --uninstall       Disinstalla"
            echo "  --help            Mostra questo help"
            echo ""
            echo "Senza opzioni: mostra menu interattivo"
            exit 0
            ;;
        "")
            show_install_menu
            ;;
        *)
            log_error "Opzione sconosciuta: $1"
            echo "Usa --help per vedere le opzioni disponibili"
            exit 1
            ;;
    esac
}

print_completion_message_old() {
    # Vecchia funzione, mantenuta per compatibilità
    print_ssh_key
    print_completion
}

# ============== HELP ==============

show_help() {
    echo "Sanoid Manager Installer v$VERSION"
    echo ""
    echo "Uso: $0 [COMANDO]"
    echo ""
    echo "Comandi:"
    echo "  (nessuno)       Installa o aggiorna Sanoid Manager"
    echo "  --uninstall     Disinstalla Sanoid Manager"
    echo "  --status        Mostra stato del servizio"
    echo "  --version       Mostra versione"
    echo "  --help, -h      Mostra questo messaggio"
    echo ""
    echo "Esempi:"
    echo "  $0              # Installa Sanoid Manager"
    echo "  $0 --uninstall  # Disinstalla"
    echo "  $0 --status     # Verifica stato"
    echo ""
}

show_status() {
    print_banner
    
    echo -e "${BOLD}Stato Sanoid Manager:${NC}"
    echo ""
    
    if systemctl is-active --quiet sanoid-manager 2>/dev/null; then
        echo -e "  Servizio:    ${GREEN}● Attivo${NC}"
    else
        echo -e "  Servizio:    ${RED}○ Non attivo${NC}"
    fi
    
    if systemctl is-enabled --quiet sanoid-manager 2>/dev/null; then
        echo -e "  Autostart:   ${GREEN}● Abilitato${NC}"
    else
        echo -e "  Autostart:   ${YELLOW}○ Disabilitato${NC}"
    fi
    
    echo ""
    echo -e "${BOLD}Informazioni:${NC}"
    echo ""
    
    [[ -f "$INSTALL_DIR/main.py" ]] && echo -e "  Installazione: ${GREEN}$INSTALL_DIR${NC}" || echo -e "  Installazione: ${RED}Non trovata${NC}"
    [[ -f "$DATA_DIR/sanoid-manager.db" ]] && echo -e "  Database:      ${GREEN}Presente${NC}" || echo -e "  Database:      ${YELLOW}Non inizializzato${NC}"
    
    echo ""
    
    if systemctl is-active --quiet sanoid-manager 2>/dev/null; then
        echo -e "${BOLD}Health Check:${NC}"
        echo ""
        curl -s "http://localhost:$SERVICE_PORT/api/health" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "  Non disponibile"
        echo ""
    fi
}

# ============== ENTRY POINT ==============

case "${1:-}" in
    --uninstall)
        check_root
        uninstall
        ;;
    --status)
        show_status
        ;;
    --version|-v)
        echo "Sanoid Manager Installer v$VERSION"
        ;;
    --help|-h)
        show_help
        ;;
    *)
        main
        ;;
esac

