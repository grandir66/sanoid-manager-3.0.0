#!/bin/bash
#
# Sanoid Manager - Test di Verifica Installazione
# Esegui questo script dopo l'installazione per verificare che tutto funzioni
#

set -e

# Colori
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

INSTALL_DIR="/opt/sanoid-manager"
PORT="8420"
PASSED=0
FAILED=0
WARNINGS=0

print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}${BOLD}          Sanoid Manager - Test Verifica Installazione          ${NC}"
    echo -e "${CYAN}${BOLD}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
}

test_pass() {
    echo -e "  ${GREEN}✓${NC} $1"
    ((PASSED++))
}

test_fail() {
    echo -e "  ${RED}✗${NC} $1"
    ((FAILED++))
}

test_warn() {
    echo -e "  ${YELLOW}!${NC} $1"
    ((WARNINGS++))
}

test_section() {
    echo ""
    echo -e "${BOLD}▶ $1${NC}"
}

# ============== TEST SISTEMA ==============

test_system() {
    test_section "Sistema"
    
    # ZFS
    if command -v zfs &> /dev/null; then
        test_pass "ZFS installato"
    else
        test_fail "ZFS non trovato"
    fi
    
    # Sanoid
    if command -v sanoid &> /dev/null; then
        test_pass "Sanoid installato"
    else
        test_fail "Sanoid non trovato"
    fi
    
    # Syncoid
    if command -v syncoid &> /dev/null; then
        test_pass "Syncoid installato"
    else
        test_fail "Syncoid non trovato"
    fi
    
    # Python
    if command -v python3 &> /dev/null; then
        PY_VER=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        if python3 -c "import sys; exit(0 if sys.version_info >= (3, 9) else 1)"; then
            test_pass "Python $PY_VER (>= 3.9)"
        else
            test_fail "Python $PY_VER troppo vecchio (richiesto >= 3.9)"
        fi
    else
        test_fail "Python3 non trovato"
    fi
}

# ============== TEST FILE ==============

test_files() {
    test_section "File Applicazione"
    
    # Directory principale
    if [[ -d "$INSTALL_DIR" ]]; then
        test_pass "Directory installazione: $INSTALL_DIR"
    else
        test_fail "Directory installazione non trovata"
        return
    fi
    
    # Virtual environment
    if [[ -f "$INSTALL_DIR/venv/bin/python" ]]; then
        test_pass "Virtual environment"
    else
        test_fail "Virtual environment non trovato"
    fi
    
    # Main.py
    if [[ -f "$INSTALL_DIR/main.py" ]]; then
        test_pass "Backend (main.py)"
    else
        test_fail "Backend non trovato"
    fi
    
    # Frontend
    if [[ -f "$INSTALL_DIR/frontend/dist/index.html" ]]; then
        test_pass "Frontend (index.html)"
    else
        test_warn "Frontend non trovato (interfaccia web non disponibile)"
    fi
    
    # Config
    if [[ -f "/etc/sanoid-manager/sanoid-manager.env" ]]; then
        test_pass "File configurazione"
    else
        test_warn "File configurazione non trovato"
    fi
    
    # Database directory
    if [[ -d "/var/lib/sanoid-manager" ]]; then
        test_pass "Directory dati"
    else
        test_warn "Directory dati non trovata"
    fi
}

# ============== TEST DIPENDENZE PYTHON ==============

test_python_deps() {
    test_section "Dipendenze Python"
    
    if [[ ! -f "$INSTALL_DIR/venv/bin/python" ]]; then
        test_fail "Virtual environment non disponibile"
        return
    fi
    
    source "$INSTALL_DIR/venv/bin/activate"
    
    local deps=("fastapi" "uvicorn" "sqlalchemy" "paramiko" "python-jose" "passlib" "pydantic" "croniter" "aiohttp")
    
    for dep in "${deps[@]}"; do
        if python -c "import ${dep//-/_}" 2>/dev/null; then
            test_pass "$dep"
        else
            test_fail "$dep non installato"
        fi
    done
    
    deactivate
}

# ============== TEST SERVIZIO ==============

test_service() {
    test_section "Servizio Systemd"
    
    # Unit file
    if [[ -f "/etc/systemd/system/sanoid-manager.service" ]]; then
        test_pass "Unit file presente"
    else
        test_fail "Unit file non trovato"
        return
    fi
    
    # Enabled
    if systemctl is-enabled --quiet sanoid-manager 2>/dev/null; then
        test_pass "Servizio abilitato all'avvio"
    else
        test_warn "Servizio non abilitato all'avvio"
    fi
    
    # Active
    if systemctl is-active --quiet sanoid-manager 2>/dev/null; then
        test_pass "Servizio in esecuzione"
    else
        test_fail "Servizio non in esecuzione"
    fi
}

# ============== TEST API ==============

test_api() {
    test_section "API Endpoints"
    
    # Health check
    local health_status=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/api/health" 2>/dev/null || echo "000")
    
    if [[ "$health_status" == "200" ]]; then
        test_pass "Health endpoint (/api/health)"
    else
        test_fail "Health endpoint non raggiungibile (HTTP $health_status)"
        return
    fi
    
    # Docs
    local docs_status=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/docs" 2>/dev/null || echo "000")
    
    if [[ "$docs_status" == "200" ]]; then
        test_pass "Documentazione API (/docs)"
    else
        test_warn "Documentazione API non disponibile"
    fi
    
    # Auth endpoint (deve richiedere body)
    local auth_status=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:$PORT/api/auth/login" 2>/dev/null || echo "000")
    
    if [[ "$auth_status" == "422" ]] || [[ "$auth_status" == "400" ]]; then
        test_pass "Auth endpoint attivo (richiede credenziali)"
    elif [[ "$auth_status" == "200" ]]; then
        test_warn "Auth endpoint non protetto correttamente"
    else
        test_fail "Auth endpoint non raggiungibile (HTTP $auth_status)"
    fi
    
    # Protected endpoint (deve richiedere auth)
    local nodes_status=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/api/nodes" 2>/dev/null || echo "000")
    
    if [[ "$nodes_status" == "401" ]] || [[ "$nodes_status" == "403" ]]; then
        test_pass "Endpoint protetti richiedono autenticazione"
    elif [[ "$nodes_status" == "200" ]]; then
        test_warn "Endpoint /api/nodes accessibile senza autenticazione"
    else
        test_fail "Endpoint /api/nodes non raggiungibile (HTTP $nodes_status)"
    fi
    
    # Frontend
    local frontend_status=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/" 2>/dev/null || echo "000")
    
    if [[ "$frontend_status" == "200" ]]; then
        test_pass "Frontend accessibile"
    else
        test_warn "Frontend non accessibile (HTTP $frontend_status)"
    fi
}

# ============== TEST SSH ==============

test_ssh() {
    test_section "Configurazione SSH"
    
    # SSH key
    if [[ -f "/root/.ssh/id_rsa" ]]; then
        test_pass "Chiave SSH privata presente"
    else
        test_warn "Chiave SSH non trovata (necessaria per nodi remoti)"
    fi
    
    if [[ -f "/root/.ssh/id_rsa.pub" ]]; then
        test_pass "Chiave SSH pubblica presente"
    else
        test_warn "Chiave SSH pubblica non trovata"
    fi
    
    # SSH agent
    if ssh-add -l &>/dev/null; then
        test_pass "SSH agent attivo"
    else
        test_warn "SSH agent non attivo"
    fi
}

# ============== TEST NETWORK ==============

test_network() {
    test_section "Network"
    
    # Port listening
    if ss -tlnp | grep -q ":$PORT "; then
        test_pass "Porta $PORT in ascolto"
    else
        test_fail "Porta $PORT non in ascolto"
    fi
    
    # Connessione localhost
    if nc -z localhost $PORT 2>/dev/null; then
        test_pass "Connessione localhost:$PORT"
    else
        test_fail "Impossibile connettersi a localhost:$PORT"
    fi
}

# ============== TEST UNIT (OPZIONALE) ==============

run_unit_tests() {
    test_section "Unit Tests (pytest)"
    
    if [[ ! -d "$INSTALL_DIR/tests" ]]; then
        test_warn "Directory tests non trovata (skip unit tests)"
        return
    fi
    
    source "$INSTALL_DIR/venv/bin/activate"
    
    # Verifica pytest
    if ! command -v pytest &>/dev/null; then
        pip install pytest pytest-asyncio pytest-cov --quiet 2>/dev/null
    fi
    
    # Esegui tests
    cd "$INSTALL_DIR"
    
    if pytest tests/ -v --tb=short 2>/dev/null; then
        test_pass "Tutti i test passati"
    else
        test_warn "Alcuni test falliti (controlla l'output sopra)"
    fi
    
    deactivate
}

# ============== SUMMARY ==============

print_summary() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${BOLD}  Riepilogo Test:${NC}"
    echo ""
    echo -e "    ${GREEN}Passati:${NC}     $PASSED"
    echo -e "    ${YELLOW}Warning:${NC}     $WARNINGS"
    echo -e "    ${RED}Falliti:${NC}     $FAILED"
    echo ""
    
    if [[ $FAILED -eq 0 ]]; then
        echo -e "${GREEN}${BOLD}  ✓ Installazione verificata con successo!${NC}"
        if [[ $WARNINGS -gt 0 ]]; then
            echo -e "    (alcuni warning potrebbero richiedere attenzione)"
        fi
    else
        echo -e "${RED}${BOLD}  ✗ Rilevati problemi nell'installazione${NC}"
        echo -e "    Risolvi i problemi e riesegui questo script"
    fi
    
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # Info accesso
    if [[ $FAILED -eq 0 ]]; then
        LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
        echo -e "${CYAN}Accedi a Sanoid Manager:${NC}"
        echo ""
        echo -e "    http://${LOCAL_IP}:${PORT}"
        echo -e "    http://localhost:${PORT}"
        echo ""
    fi
}

# ============== MAIN ==============

main() {
    print_header
    
    test_system
    test_files
    test_python_deps
    test_service
    test_network
    test_api
    test_ssh
    
    # Unit tests solo se richiesto
    if [[ "${1:-}" == "--full" ]]; then
        run_unit_tests
    fi
    
    print_summary
    
    exit $FAILED
}

# Help
if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
    echo "Uso: $0 [--full]"
    echo ""
    echo "Opzioni:"
    echo "  --full    Esegue anche i test unitari pytest"
    echo "  --help    Mostra questo messaggio"
    echo ""
    exit 0
fi

main "$@"

