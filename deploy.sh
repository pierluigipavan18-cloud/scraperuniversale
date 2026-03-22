#!/bin/bash
# ============================================
# ScraperUniversale - Deploy su VPS
# ============================================
# Uso:
#   1. Copia questo file sul VPS:
#      scp deploy.sh root@TUO_IP:~/deploy.sh
#
#   2. Lancia sul VPS:
#      ssh root@TUO_IP
#      bash deploy.sh
# ============================================

set -e

REPO="https://github.com/pierluigipavan18-cloud/scraperuniversale.git"
INSTALL_DIR="$HOME/scraperuniversale"
PYTHON_MIN="3.10"

# Colori
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
fail() { echo -e "${RED}[ERRORE]${NC} $1"; exit 1; }

echo ""
echo "=========================================="
echo "  ScraperUniversale - Deploy automatico"
echo "=========================================="
echo ""

# ------------------------------------------
# 1. Check Python
# ------------------------------------------
echo "--- Step 1: Controllo Python ---"
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    ok "Python $PY_VER trovato"
    # Check versione minima
    PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
        warn "Python $PY_VER troppo vecchio, serve >= $PYTHON_MIN"
        warn "Installo Python..."
        apt update && apt install -y python3 python3-pip python3-venv
    fi
else
    warn "Python3 non trovato, lo installo..."
    apt update && apt install -y python3 python3-pip python3-venv
fi

# Assicurati che venv sia disponibile
if ! python3 -m venv --help &>/dev/null; then
    warn "Installo python3-venv..."
    apt install -y python3-venv
fi

ok "Python pronto"
echo ""

# ------------------------------------------
# 2. Check git
# ------------------------------------------
echo "--- Step 2: Controllo Git ---"
if ! command -v git &>/dev/null; then
    warn "Git non trovato, lo installo..."
    apt update && apt install -y git
fi
ok "Git pronto"
echo ""

# ------------------------------------------
# 3. Clona o aggiorna repo
# ------------------------------------------
echo "--- Step 3: Repository ---"
if [ -d "$INSTALL_DIR" ]; then
    warn "Cartella $INSTALL_DIR esiste gia"
    echo "    Aggiorno con git pull..."
    cd "$INSTALL_DIR"
    git pull origin main || git pull origin master || warn "Pull fallito, continuo con versione esistente"
    ok "Repository aggiornato"
else
    echo "    Clono repository..."
    git clone "$REPO" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    ok "Repository clonato in $INSTALL_DIR"
fi
echo ""

# ------------------------------------------
# 4. Virtual environment
# ------------------------------------------
echo "--- Step 4: Virtual Environment ---"
cd "$INSTALL_DIR"
if [ -d "venv" ]; then
    ok "venv esiste gia"
else
    echo "    Creo virtual environment..."
    python3 -m venv venv
    ok "venv creato"
fi

source venv/bin/activate
ok "venv attivato"
echo ""

# ------------------------------------------
# 5. Installa dipendenze
# ------------------------------------------
echo "--- Step 5: Dipendenze ---"
pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "Dipendenze installate"
echo ""

# ------------------------------------------
# 6. Crea directory output
# ------------------------------------------
echo "--- Step 6: Directory output ---"
mkdir -p "$INSTALL_DIR/output"
ok "Directory output pronta"
echo ""

# ------------------------------------------
# 7. Test rapido
# ------------------------------------------
echo "--- Step 7: Test ---"
if python3 -c "from scraper.engine import ScraperEngine; print('Import OK')" 2>/dev/null; then
    ok "Import test passato"
else
    warn "Import test fallito - controlla i log"
fi
echo ""

# ------------------------------------------
# Done
# ------------------------------------------
echo "=========================================="
echo -e "  ${GREEN}DEPLOY COMPLETATO${NC}"
echo "=========================================="
echo ""
echo "  Per usare lo scraper:"
echo ""
echo "    cd $INSTALL_DIR"
echo "    source venv/bin/activate"
echo ""
echo "  Esempi:"
echo ""
echo "    # Energivori italiani (lista CSEA)"
echo "    python main.py -s csea_energivori -f excel"
echo ""
echo "    # Produttori europei"
echo "    python main.py -k \"steel\" \"manufacturing\" \\"
echo "      -s europages kompass -c Italy Germany -f excel --max 100"
echo ""
echo "    # Full power"
echo "    python main.py -k \"steel\" \"chemical\" \"energy\" \\"
echo "      -s europages kompass wlw industrystock -f excel"
echo ""
echo "  I risultati finiscono in: $INSTALL_DIR/output/"
echo ""
echo "  ===== WEB DASHBOARD ====="
echo ""
echo "    python dashboard.py"
echo "    Apri: http://$(hostname -I | awk '{print $1}'):8000"
echo ""
echo "  Per lanciare in background:"
echo "    nohup python dashboard.py > dashboard.log 2>&1 &"
echo ""
echo "  Per scaricarli sul tuo PC:"
echo "    scp root@$(hostname -I | awk '{print $1}'):$INSTALL_DIR/output/*.xlsx ~/Desktop/"
echo ""
