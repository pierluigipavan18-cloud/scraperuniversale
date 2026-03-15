# ScraperUniversale - Piano Architetturale

## Vision
Scraper B2B enterprise-grade per estrazione dati aziendali (nome, citta, email, telefono, sito web)
da directory industriali europee. Target: aziende produttrici e energivore.

## Fonti Dati

### 1. Europages (Fonte Primaria)
- **URL**: europages.co.uk / europages.com
- **Volume**: 2.6M+ aziende B2B europee
- **Dati disponibili**: nome azienda, indirizzo, citta, telefono, fax, sito web, descrizione, settore, n. dipendenti, partita IVA
- **Tecnica**: API interna JSON (requests + JSON parsing) - il sito e Vue.js con backend API
- **Paginazione**: parametri query con offset/limit
- **Anti-bot**: CAPTCHA iniziale, rate limiting su IP - richiede delays e proxy rotation

### 2. Kompass (Fonte Secondaria)
- **URL**: kompass.com
- **Volume**: 57M+ aziende in 70+ paesi
- **Dati disponibili**: nome, indirizzo, telefono, email, sito web, settore, classificazione NACE
- **Tecnica**: HTML parsing con BeautifulSoup/lxml
- **Anti-bot**: rate limiting moderato

### 3. Email Enrichment (dal sito web aziendale)
- Per ogni azienda trovata senza email, il modulo visita il sito web aziendale
- Estrae email con regex pattern matching dal HTML
- Priorita: pagine contatti, about, impressum, footer

## Architettura

```
scraperuniversale/
├── CLAUDE.md                  # Questo file
├── README.md                  # Docs (non creato ora)
├── requirements.txt           # Dipendenze Python
├── config.yaml                # Configurazione utente
├── main.py                    # CLI entry point
├── scraper/
│   ├── __init__.py
│   ├── engine.py              # Core engine (session, proxy, rate limit, retry)
│   ├── europages.py           # Modulo Europages
│   ├── kompass.py             # Modulo Kompass
│   ├── email_enricher.py      # Arricchimento email da siti web
│   └── models.py              # Dataclass Company
├── export/
│   ├── __init__.py
│   ├── exporter.py            # CSV/JSON/Excel export
│   └── dedup.py               # Deduplicazione e validazione
└── output/                    # Directory output dati
```

## Stack Tecnologico
- **Python 3.10+**
- **httpx** - HTTP client asincrono (superiore a requests per performance)
- **beautifulsoup4 + lxml** - HTML parsing
- **pydantic** - Validazione dati
- **rich** - CLI output bello
- **openpyxl** - Export Excel
- **pyyaml** - Configurazione
- **fake-useragent** - Rotazione User-Agent

## Funzionalita Chiave
1. **Multi-source scraping** - Europages + Kompass + enrichment email
2. **Proxy rotation** - Supporto lista proxy (HTTP/SOCKS5)
3. **Rate limiting intelligente** - Delay adattivo per evitare ban
4. **Retry con backoff esponenziale** - Resilienza network
5. **Deduplicazione** - Merge dati da fonti multiple per stessa azienda
6. **Export multiplo** - CSV, JSON, Excel
7. **Filtri avanzati** - Per paese, citta, settore, keyword
8. **Resume/checkpoint** - Riprendi scraping interrotto
9. **Validazione email** - Formato + dedup
10. **CLI professionale** - Argparse + Rich progress bars

## Fasi di Sviluppo
- [x] Fase 1: Ricerca fonti dati
- [x] Fase 2: Core engine + models
- [x] Fase 3: Europages scraper
- [x] Fase 4: Kompass scraper
- [x] Fase 5: Email enrichment
- [x] Fase 6: Export + dedup pipeline
- [x] Fase 7: CLI + config

## Uso Rapido

```bash
# Installa dipendenze
pip install -r requirements.txt

# Scraping base (usa config.yaml)
python main.py -k "manufacturing" -c Italy

# Multi-keyword, multi-country, export Excel
python main.py -k "steel production" "solar energy" -c Italy Germany France -f excel --max 500

# Solo Europages, senza enrichment email
python main.py -k "plastics" -s europages --no-enrich -f csv

# Verbose/debug mode
python main.py -k "chemical" -v
```

## Prossimi Miglioramenti
- [ ] Checkpoint/resume per scraping interrotto
- [ ] Dashboard web con Flask/FastAPI
- [ ] Database SQLite per storage persistente
- [ ] Playwright per siti JS-heavy
- [ ] API REST per integrazioni esterne
