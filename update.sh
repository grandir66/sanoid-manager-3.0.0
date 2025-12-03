#!/bin/bash
#
# Sanoid Manager - Update Script
# Scarica e installa aggiornamenti da GitHub
#

set -e

# Configurazione
GITHUB_REPO="grandir66/sanoid-manager-3.0.0"
INSTALL_DIR="/opt/sanoid-manager"
BACKUP_DIR="/opt/sanoid-manager-backup"
SERVICE_NAME="sanoid-manager"

# Colori
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[✓]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
log_error() { echo -e "${RED}[✗]${NC} $1"; }

# Banner
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     Sanoid Manager - Update Tool           ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""

# Verifica root
if [[ $EUID -ne 0 ]]; then
   log_error "Questo script deve essere eseguito come root"
   exit 1
fi

# Funzione per ottenere versione corrente
get_current_version() {
    if [[ -f "$INSTALL_DIR/version.txt" ]]; then
        cat "$INSTALL_DIR/version.txt"
    elif [[ -f "$INSTALL_DIR/main.py" ]]; then
        grep -oP 'version="\K[^"]+' "$INSTALL_DIR/main.py" 2>/dev/null | head -1 || echo "unknown"
    else
        echo "not-installed"
    fi
}

# Funzione per ottenere ultima versione da GitHub
get_latest_version() {
    local api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
    curl -s "$api_url" | grep -oP '"tag_name": "\K[^"]+' | sed 's/^v//' || echo "unknown"
}

# Funzione per scaricare release
download_release() {
    local version=$1
    local download_url="https://github.com/${GITHUB_REPO}/releases/download/v${version}/sanoid-manager-${version}.tar.gz"
    local temp_file="/tmp/sanoid-manager-${version}.tar.gz"
    
    log_info "Download versione ${version}..."
    
    if curl -L -o "$temp_file" "$download_url" 2>/dev/null; then
        echo "$temp_file"
    else
        # Prova senza 'v' prefix
        download_url="https://github.com/${GITHUB_REPO}/releases/download/${version}/sanoid-manager-${version}.tar.gz"
        if curl -L -o "$temp_file" "$download_url" 2>/dev/null; then
            echo "$temp_file"
        else
            echo ""
        fi
    fi
}

# Funzione per backup
backup_current() {
    if [[ -d "$INSTALL_DIR" ]]; then
        log_info "Backup installazione corrente..."
        rm -rf "$BACKUP_DIR"
        cp -r "$INSTALL_DIR" "$BACKUP_DIR"
        # Preserva database e configurazioni
        log_success "Backup completato in $BACKUP_DIR"
    fi
}

# Funzione per restore
restore_backup() {
    if [[ -d "$BACKUP_DIR" ]]; then
        log_warning "Ripristino backup..."
        rm -rf "$INSTALL_DIR"
        mv "$BACKUP_DIR" "$INSTALL_DIR"
        log_success "Backup ripristinato"
    fi
}

# Funzione per installare aggiornamento
install_update() {
    local archive=$1
    local temp_dir="/tmp/sanoid-manager-update"
    
    log_info "Estrazione archivio..."
    rm -rf "$temp_dir"
    mkdir -p "$temp_dir"
    tar -xzf "$archive" -C "$temp_dir"
    
    # Trova la directory estratta
    local extracted_dir=$(find "$temp_dir" -maxdepth 1 -type d -name "sanoid-manager*" | head -1)
    if [[ -z "$extracted_dir" ]]; then
        extracted_dir="$temp_dir"
    fi
    
    # Stop servizio
    log_info "Stop servizio..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    
    # Backup
    backup_current
    
    # Preserva file importanti
    local db_file=""
    local env_file=""
    if [[ -f "$INSTALL_DIR/sanoid-manager.db" ]]; then
        cp "$INSTALL_DIR/sanoid-manager.db" /tmp/
        db_file="/tmp/sanoid-manager.db"
    fi
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        cp "$INSTALL_DIR/.env" /tmp/
        env_file="/tmp/.env"
    fi
    
    # Copia nuovi file
    log_info "Installazione nuovi file..."
    
    # Backend
    if [[ -d "$extracted_dir/backend" ]]; then
        cp -r "$extracted_dir/backend/"* "$INSTALL_DIR/"
    fi
    
    # Frontend
    if [[ -d "$extracted_dir/frontend" ]]; then
        rm -rf "$INSTALL_DIR/frontend"
        cp -r "$extracted_dir/frontend" "$INSTALL_DIR/"
    fi
    
    # Ripristina database e config
    if [[ -n "$db_file" && -f "$db_file" ]]; then
        cp "$db_file" "$INSTALL_DIR/"
    fi
    if [[ -n "$env_file" && -f "$env_file" ]]; then
        cp "$env_file" "$INSTALL_DIR/"
    fi
    
    # Aggiorna dipendenze Python
    log_info "Aggiornamento dipendenze Python..."
    if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
        source "$INSTALL_DIR/venv/bin/activate"
        pip install -q -r "$INSTALL_DIR/requirements.txt" 2>/dev/null || true
        deactivate
    fi
    
    # Aggiorna database (migrations)
    log_info "Aggiornamento database..."
    cd "$INSTALL_DIR"
    source venv/bin/activate
    python -c "from database import Base, engine; Base.metadata.create_all(bind=engine)" 2>/dev/null || true
    deactivate
    
    # Start servizio
    log_info "Avvio servizio..."
    systemctl start "$SERVICE_NAME"
    
    # Cleanup
    rm -rf "$temp_dir" "$archive"
    
    log_success "Aggiornamento completato!"
}

# Menu principale
show_menu() {
    local current=$(get_current_version)
    
    echo ""
    echo "Versione corrente: ${GREEN}${current}${NC}"
    echo ""
    echo "Opzioni:"
    echo "  1) Verifica aggiornamenti"
    echo "  2) Aggiorna all'ultima versione"
    echo "  3) Aggiorna a versione specifica"
    echo "  4) Scarica da URL"
    echo "  5) Ripristina backup"
    echo "  6) Esci"
    echo ""
    read -p "Seleziona [1-6]: " choice
    
    case $choice in
        1)
            check_updates
            ;;
        2)
            update_latest
            ;;
        3)
            update_specific
            ;;
        4)
            update_from_url
            ;;
        5)
            do_restore
            ;;
        6)
            exit 0
            ;;
        *)
            log_error "Opzione non valida"
            show_menu
            ;;
    esac
}

check_updates() {
    local current=$(get_current_version)
    local latest=$(get_latest_version)
    
    echo ""
    echo "Versione corrente: ${GREEN}${current}${NC}"
    echo "Ultima versione:   ${BLUE}${latest}${NC}"
    
    if [[ "$current" == "$latest" ]]; then
        log_success "Sei già alla versione più recente!"
    elif [[ "$latest" != "unknown" ]]; then
        log_warning "Aggiornamento disponibile!"
        read -p "Vuoi aggiornare ora? [y/N]: " yn
        if [[ "$yn" =~ ^[Yy]$ ]]; then
            update_latest
        fi
    else
        log_error "Impossibile verificare aggiornamenti. Controlla la connessione."
    fi
    
    show_menu
}

update_latest() {
    local latest=$(get_latest_version)
    
    if [[ "$latest" == "unknown" ]]; then
        log_error "Impossibile ottenere ultima versione"
        show_menu
        return
    fi
    
    log_info "Aggiornamento a versione ${latest}..."
    
    local archive=$(download_release "$latest")
    
    if [[ -n "$archive" && -f "$archive" ]]; then
        install_update "$archive"
    else
        log_error "Download fallito"
    fi
    
    show_menu
}

update_specific() {
    read -p "Inserisci versione (es: 3.0.0): " version
    
    if [[ -z "$version" ]]; then
        log_error "Versione non specificata"
        show_menu
        return
    fi
    
    log_info "Aggiornamento a versione ${version}..."
    
    local archive=$(download_release "$version")
    
    if [[ -n "$archive" && -f "$archive" ]]; then
        install_update "$archive"
    else
        log_error "Download fallito. Verifica che la versione esista."
    fi
    
    show_menu
}

update_from_url() {
    read -p "Inserisci URL del pacchetto .tar.gz: " url
    
    if [[ -z "$url" ]]; then
        log_error "URL non specificato"
        show_menu
        return
    fi
    
    local temp_file="/tmp/sanoid-manager-download.tar.gz"
    
    log_info "Download da URL..."
    if curl -L -o "$temp_file" "$url"; then
        install_update "$temp_file"
    else
        log_error "Download fallito"
    fi
    
    show_menu
}

do_restore() {
    if [[ -d "$BACKUP_DIR" ]]; then
        read -p "Ripristinare il backup precedente? [y/N]: " yn
        if [[ "$yn" =~ ^[Yy]$ ]]; then
            systemctl stop "$SERVICE_NAME" 2>/dev/null || true
            restore_backup
            systemctl start "$SERVICE_NAME"
            log_success "Backup ripristinato!"
        fi
    else
        log_warning "Nessun backup disponibile"
    fi
    
    show_menu
}

# Parametri da riga di comando
case "${1:-}" in
    --check)
        current=$(get_current_version)
        latest=$(get_latest_version)
        echo "current=$current"
        echo "latest=$latest"
        if [[ "$current" != "$latest" && "$latest" != "unknown" ]]; then
            echo "update_available=true"
        else
            echo "update_available=false"
        fi
        ;;
    --update)
        update_latest
        ;;
    --version)
        echo $(get_current_version)
        ;;
    *)
        show_menu
        ;;
esac

