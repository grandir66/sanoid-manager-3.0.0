"""
Sanoid Manager - Backend API
Gestione centralizzata di Sanoid/Syncoid per infrastrutture Proxmox
Con autenticazione integrata Proxmox VE
"""

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import os
import logging

from database import engine, Base, get_db, init_default_config, SessionLocal
from routers import nodes, snapshots, sync_jobs, vms, logs, settings, auth
from services.scheduler import SchedulerService

# Configurazione logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Inizializzazione scheduler
scheduler = SchedulerService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestione lifecycle dell'applicazione"""
    # Startup
    logger.info("Avvio Sanoid Manager...")
    Base.metadata.create_all(bind=engine)
    
    # Inizializza configurazione di default
    db = SessionLocal()
    try:
        init_default_config(db)
    finally:
        db.close()
    
    await scheduler.start()
    logger.info("Sanoid Manager avviato")
    
    yield
    
    # Shutdown
    logger.info("Arresto Sanoid Manager...")
    await scheduler.stop()
    logger.info("Sanoid Manager arrestato")


app = FastAPI(
    title="Sanoid Manager",
    description="Gestione centralizzata snapshot ZFS e replica per Proxmox VE",
    version="3.0.3",
    lifespan=lifespan
)


# CORS configurato correttamente
ALLOWED_ORIGINS = os.environ.get("SANOID_MANAGER_CORS_ORIGINS", "").split(",")
if not ALLOWED_ORIGINS or ALLOWED_ORIGINS == [""]:
    # Default: stesso host
    ALLOWED_ORIGINS = ["http://localhost:8420", "http://127.0.0.1:8420"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)


# Exception handler globale
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Errore interno del server"}
    )


# Router API
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(nodes.router, prefix="/api/nodes", tags=["Nodes"])
app.include_router(snapshots.router, prefix="/api/snapshots", tags=["Snapshots"])
app.include_router(sync_jobs.router, prefix="/api/sync-jobs", tags=["Sync Jobs"])
app.include_router(vms.router, prefix="/api/vms", tags=["VMs"])
app.include_router(logs.router, prefix="/api/logs", tags=["Logs"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])


# Health check (non richiede autenticazione)
@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "3.0.3",
        "auth_enabled": True
    }


# Setup check (verifica se è necessario il setup iniziale)
@app.get("/api/setup-required")
async def setup_required(db=Depends(get_db)):
    from database import User
    user_count = db.query(User).count()
    return {"setup_required": user_count == 0}


# Serve static files (frontend)
# Prova diversi percorsi possibili per il frontend
possible_frontend_paths = [
    os.path.join(os.path.dirname(__file__), "frontend", "dist"),  # /opt/sanoid-manager/frontend/dist
    os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"),  # Struttura sviluppo
    "/opt/sanoid-manager/frontend/dist",  # Percorso assoluto installazione
]

frontend_path = None
for path in possible_frontend_paths:
    if os.path.exists(path) and os.path.isfile(os.path.join(path, "index.html")):
        frontend_path = path
        logger.info(f"Frontend trovato in: {frontend_path}")
        break

if frontend_path:
    # Mount assets directory
    assets_path = os.path.join(frontend_path, "assets")
    if os.path.exists(assets_path):
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")
    
    @app.get("/")
    async def serve_frontend():
        return FileResponse(os.path.join(frontend_path, "index.html"))
    
    @app.get("/{full_path:path}")
    async def catch_all(full_path: str):
        # Non intercettare le API
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")
        
        # Serve file statici se esistono
        file_path = os.path.join(frontend_path, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        
        # SPA fallback
        return FileResponse(os.path.join(frontend_path, "index.html"))
else:
    logger.warning("Frontend non trovato! L'interfaccia web non sarà disponibile.")
    logger.warning(f"Percorsi cercati: {possible_frontend_paths}")
    
    @app.get("/")
    async def no_frontend():
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Frontend non disponibile",
                "message": "L'interfaccia web non è stata trovata. Usa l'API direttamente.",
                "api_docs": "/docs"
            }
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("SANOID_MANAGER_PORT", 8420))
    uvicorn.run(app, host="0.0.0.0", port=port)
