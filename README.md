# DistriStock Pro

Application de **gestion de stock et vente distribuée** : deux microservices Flask communiquent sur un réseau Docker privé, avec une base PostgreSQL centralisée.

## Architecture

| Composant | Rôle | Port (hôte) |
|-----------|------|-------------|
| **inventory-app** | Catalogue, stocks, API `POST /api/check-and-reduce` | `5001` |
| **sales-app** | Boutique (Tailwind), panier, commandes | `5000` |
| **database** | PostgreSQL 16 | interne (5432) |

Le réseau bridge **`distrinet`** isole les services. Le DNS Docker résout le nom de service `inventory-app` depuis `sales-app` (communication East-West).

## Prérequis

- Docker et Docker Compose (plugin `docker compose`)

## Installation

1. (Optionnel) Copier l’exemple de variables d’environnement :

   ```bash
   cp .env.example .env
   ```

   Modifier les mots de passe et `FLASK_SECRET_KEY` en production.

2. Lancer la stack :

   ```bash
   docker compose up --build -d
   ```

3. Accès :

   - Boutique : [http://localhost:5000](http://localhost:5000)
   - Inventaire (dashboard) : [http://localhost:5001](http://localhost:5001)
   - Santé : `GET http://localhost:5000/health` et `GET http://localhost:5001/health`

## Démonstration (critères du cahier des charges)

1. **Deux conteneurs Flask** : `docker compose ps` — `distristock-inventory` et `distristock-sales` à l’état `running`.

2. **Vente et stock en temps réel** : passer une commande sur le port 5000, puis rafraîchir le dashboard inventaire (5001) : les quantités diminuent.

3. **Inventaire arrêté** : `docker compose stop inventory-app`, tenter une commande — message d’erreur côté boutique (service indisponible). `docker compose start inventory-app` pour rétablir.

4. **Logs** : `docker compose logs -f inventory-app sales-app` pour voir les requêtes et le trafic applicatif.

## Structure du dépôt

```
├── docker-compose.yml
├── db/init.sql              # Schéma + données de démo
├── inventory-app/           # Microservice inventaire
├── sales-app/               # Microservice ventes
└── .github/workflows/       # CI (build images, bonus)
```

## API inter-services

- **URL interne** (depuis `sales-app`) : `http://inventory-app:5001/api/check-and-reduce`
- **Corps JSON** : `{ "items": [ { "sku": "...", "quantity": n } ] }` ou `{ "sku": "...", "quantity": n }`

Réponses typiques : `200` avec `ok: true`, ou `400` si stock insuffisant ou produit inconnu.

## Licence

Projet pédagogique (L3 Réseaux et Informatique).
