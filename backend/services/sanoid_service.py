"""
Sanoid Service - Gestione configurazione e operazioni Sanoid
"""

import asyncio
from typing import Optional, List, Dict, Tuple
import logging
from dataclasses import dataclass

from services.ssh_service import ssh_service, SSHResult

logger = logging.getLogger(__name__)


SANOID_CONF_PATH = "/etc/sanoid/sanoid.conf"
SANOID_DEFAULTS_PATH = "/etc/sanoid/sanoid.defaults.conf"


@dataclass
class SanoidTemplate:
    """Template Sanoid predefinito"""
    name: str
    hourly: int
    daily: int
    weekly: int
    monthly: int
    yearly: int
    autosnap: bool = True
    autoprune: bool = True


# Template predefiniti
DEFAULT_TEMPLATES = {
    "production": SanoidTemplate("production", hourly=48, daily=90, weekly=12, monthly=24, yearly=5),
    "default": SanoidTemplate("default", hourly=24, daily=30, weekly=4, monthly=12, yearly=0),
    "minimal": SanoidTemplate("minimal", hourly=12, daily=7, weekly=0, monthly=0, yearly=0),
    "backup": SanoidTemplate("backup", hourly=0, daily=30, weekly=8, monthly=12, yearly=2),
    "vm": SanoidTemplate("vm", hourly=24, daily=14, weekly=4, monthly=6, yearly=0),
}


class SanoidService:
    """Servizio per gestione Sanoid su nodi remoti"""
    
    async def install_sanoid(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Tuple[bool, str]:
        """Installa Sanoid su un nodo Proxmox/Debian"""
        
        install_script = """
set -e

# Verifica se già installato
if command -v sanoid &> /dev/null; then
    echo "Sanoid già installato"
    sanoid --version
    exit 0
fi

# Installa dipendenze
apt-get update
apt-get install -y debhelper libcapture-tiny-perl libconfig-inifiles-perl pv lzop mbuffer

# Clona e installa sanoid
cd /tmp
rm -rf sanoid
git clone https://github.com/jimsalterjrs/sanoid.git
cd sanoid

# Checkout ultima release stabile
LATEST_TAG=$(git describe --tags $(git rev-list --tags --max-count=1))
git checkout $LATEST_TAG

# Build e install
ln -sf packages/debian .
dpkg-buildpackage -uc -us
apt-get install -y ../sanoid_*.deb

# Crea directory config se non esiste
mkdir -p /etc/sanoid

# Copia configurazione di default se non esiste
if [ ! -f /etc/sanoid/sanoid.defaults.conf ]; then
    cp /usr/share/sanoid/sanoid.defaults.conf /etc/sanoid/
fi

# Crea config vuoto se non esiste
if [ ! -f /etc/sanoid/sanoid.conf ]; then
    touch /etc/sanoid/sanoid.conf
    echo "# Sanoid configuration - managed by Sanoid Manager" > /etc/sanoid/sanoid.conf
fi

# Abilita timer systemd
systemctl enable sanoid.timer
systemctl start sanoid.timer

# Cleanup
cd /
rm -rf /tmp/sanoid /tmp/sanoid_*.deb /tmp/sanoid_*.buildinfo /tmp/sanoid_*.changes

echo "Sanoid installato con successo"
sanoid --version
"""
        
        result = await ssh_service.execute(
            hostname=hostname,
            command=install_script,
            port=port,
            username=username,
            key_path=key_path,
            timeout=600
        )
        
        return result.success, result.stdout + result.stderr
    
    async def get_config(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Tuple[bool, str]:
        """Legge la configurazione Sanoid corrente"""
        result = await ssh_service.execute(
            hostname=hostname,
            command=f"cat {SANOID_CONF_PATH} 2>/dev/null || echo ''",
            port=port,
            username=username,
            key_path=key_path
        )
        
        return result.success, result.stdout
    
    async def set_config(
        self,
        hostname: str,
        config_content: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> SSHResult:
        """Scrive la configurazione Sanoid"""
        # Escape del contenuto per bash
        escaped_content = config_content.replace("'", "'\"'\"'")
        
        cmd = f"""
mkdir -p /etc/sanoid
cp {SANOID_CONF_PATH} {SANOID_CONF_PATH}.bak 2>/dev/null || true
cat > {SANOID_CONF_PATH} << 'SANOID_EOF'
{config_content}
SANOID_EOF
echo "Configuration saved"
"""
        
        return await ssh_service.execute(
            hostname=hostname,
            command=cmd,
            port=port,
            username=username,
            key_path=key_path
        )
    
    def generate_config(self, datasets: List[Dict]) -> str:
        """
        Genera il contenuto del file sanoid.conf
        
        datasets: lista di dict con keys:
            - name: nome dataset ZFS
            - template: nome template (o custom settings)
            - hourly, daily, weekly, monthly, yearly: retention
            - autosnap, autoprune: bool
        """
        lines = [
            "# Sanoid configuration",
            "# Managed by Sanoid Manager",
            "# Do not edit manually",
            "",
            "# Templates",
        ]
        
        # Aggiungi template predefiniti
        for name, tpl in DEFAULT_TEMPLATES.items():
            lines.extend([
                f"[template_{name}]",
                f"  hourly = {tpl.hourly}",
                f"  daily = {tpl.daily}",
                f"  weekly = {tpl.weekly}",
                f"  monthly = {tpl.monthly}",
                f"  yearly = {tpl.yearly}",
                f"  autosnap = {'yes' if tpl.autosnap else 'no'}",
                f"  autoprune = {'yes' if tpl.autoprune else 'no'}",
                "",
            ])
        
        lines.append("# Datasets")
        lines.append("")
        
        # Aggiungi dataset configurati
        for ds in datasets:
            if not ds.get("sanoid_enabled", False):
                continue
                
            lines.append(f"[{ds['name']}]")
            
            template = ds.get("sanoid_template", "default")
            if template and template in DEFAULT_TEMPLATES:
                lines.append(f"  use_template = {template}")
            else:
                # Configurazione custom
                lines.append(f"  hourly = {ds.get('hourly', 24)}")
                lines.append(f"  daily = {ds.get('daily', 30)}")
                lines.append(f"  weekly = {ds.get('weekly', 4)}")
                lines.append(f"  monthly = {ds.get('monthly', 12)}")
                lines.append(f"  yearly = {ds.get('yearly', 0)}")
            
            lines.append(f"  autosnap = {'yes' if ds.get('autosnap', True) else 'no'}")
            lines.append(f"  autoprune = {'yes' if ds.get('autoprune', True) else 'no'}")
            lines.append("")
        
        return "\n".join(lines)
    
    async def run_sanoid(
        self,
        hostname: str,
        cron: bool = False,
        prune: bool = False,
        verbose: bool = False,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> SSHResult:
        """Esegue Sanoid manualmente"""
        flags = []
        if cron:
            flags.append("--cron")
        if prune:
            flags.append("--prune-snapshots")
        if verbose:
            flags.append("--verbose")
        
        cmd = f"sanoid {' '.join(flags)}"
        
        return await ssh_service.execute(
            hostname=hostname,
            command=cmd,
            port=port,
            username=username,
            key_path=key_path,
            timeout=600
        )
    
    async def get_sanoid_status(
        self,
        hostname: str,
        port: int = 22,
        username: str = "root",
        key_path: str = "/root/.ssh/id_rsa"
    ) -> Dict:
        """Ottiene lo stato di Sanoid"""
        
        status = {
            "installed": False,
            "version": None,
            "timer_active": False,
            "last_run": None,
            "next_run": None
        }
        
        # Check installazione e versione
        result = await ssh_service.execute(
            hostname=hostname,
            command="sanoid --version 2>&1",
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success:
            status["installed"] = True
            status["version"] = result.stdout.strip()
        
        # Check timer systemd
        result = await ssh_service.execute(
            hostname=hostname,
            command="systemctl is-active sanoid.timer 2>/dev/null && systemctl show sanoid.timer --property=LastTriggerUSec,NextElapseUSecRealtime --value",
            port=port,
            username=username,
            key_path=key_path
        )
        
        if result.success and "active" in result.stdout:
            status["timer_active"] = True
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 3:
                status["last_run"] = lines[1] if lines[1] != "n/a" else None
                status["next_run"] = lines[2] if lines[2] != "n/a" else None
        
        return status


# Singleton
sanoid_service = SanoidService()
