# MTG Price Trend Bot — prototype v1

Bot gratuit de détection de variation de prix pour cartes Magic: The Gathering.
V1 : suit les prix Cardmarket EUR via l'API gratuite Scryfall, détecte les
variations et alerte sur Discord.

## Ce que fait cette v1

- `collector.py` — récupère le prix Cardmarket EUR de chaque carte de ta
  watchlist via Scryfall, stocke dans `prices.db` (SQLite).
- `trend_detector.py` — compare les derniers relevés, alerte sur Discord si
  la variation dépasse le seuil (10% par défaut).
- `.github/workflows/price_check.yml` — fait tourner tout ça automatiquement
  toutes les heures, gratuitement, sans laisser ton PC allumé.

## Setup (10 minutes, 100% gratuit)

### 1. Teste en local d'abord

```bash
pip install -r requirements.txt
python collector.py        # remplit prices.db avec les prix actuels
python trend_detector.py   # affiche l'analyse (pas d'alerte tant qu'il n'y a
                            # pas d'historique sur plusieurs jours)
```

### 2. Crée un webhook Discord (gratuit)

1. Dans ton serveur Discord : Paramètres du salon → Intégrations → Webhooks
   → Nouveau webhook
2. Copie l'URL du webhook

### 3. Mets le projet sur GitHub (gratuit)

```bash
git init
git add .
git commit -m "Initial commit"
# crée un repo sur github.com, puis :
git remote add origin https://github.com/TON_USER/mtg-price-bot.git
git push -u origin main
```

### 4. Ajoute le secret Discord

Sur GitHub : Settings du repo → Secrets and variables → Actions →
New repository secret → nom `DISCORD_WEBHOOK_URL`, valeur = l'URL copiée
à l'étape 2.

### 5. Active le workflow

Il tourne automatiquement toutes les heures (modifiable dans le fichier
`.github/workflows/price_check.yml`, syntaxe cron classique). Tu peux aussi
le lancer manuellement depuis l'onglet "Actions" de ton repo GitHub
(bouton "Run workflow").

## Modifier la watchlist

Édite `watchlist.json` — ajoute autant de cartes que tu veux. Chaque entrée :

```json
{"name": "Nom exact de la carte", "set": "code d'extension (optionnel)"}
```

## Dashboard web (gratuit, via GitHub Pages)

Le dossier `docs/` contient un dashboard statique (`index.html`) qui lit
`docs/data.json`, régénéré à chaque run automatique par `export_dashboard_data.py`.

**Activation (une seule fois) :**

1. Pousse le projet sur GitHub (voir étape 3 plus haut)
2. Sur GitHub : Settings du repo → Pages → Build and deployment → Source :
   "Deploy from a branch" → Branch : `main`, dossier `/docs` → Save
3. Après quelques minutes, ton dashboard est en ligne à une URL du type
   `https://TON_USER.github.io/mtg-price-bot/`

Le dashboard se met à jour tout seul à chaque run du workflow (toutes les
heures), puisque `docs/data.json` est régénéré et commité automatiquement.

**Pour le tester en local avant de déployer :**

```bash
python collector.py
python deal_scanner.py
python cardnexus_history.py
python trend_detector.py
python export_dashboard_data.py
python -m http.server 8000 --directory docs
# puis ouvre http://localhost:8000
```


- **Une seule source** (Scryfall/Cardmarket) — pas encore de comparaison
  avec CardNexus. Nécessite d'inspecter leur API interne via l'onglet
  Network du navigateur (F12) pendant que tu navigues sur une fiche carte —
  je peux t'accompagner sur cette étape ensuite.
- **Pas de signal compétitif** (tournois/Reddit) — c'est la partie qui
  te permettrait d'anticiper *avant* le prix. Prochaine brique à ajouter.
- **Seuil fixe (10%)** — à ajuster selon le bruit observé sur tes cartes.
- Le premier run n'aura qu'un seul point de données donc pas d'alerte
  possible — il faut au moins 2 collectes pour calculer une variation.

## Coût réel

0€. Scryfall API : gratuite. GitHub Actions : gratuit (largement sous la
limite mensuelle pour un cron horaire). Discord webhook : gratuit. SQLite :
pas de serveur de base de données à payer.
