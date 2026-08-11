---
source_sha256: 472844a34ca189775b1f61de23b6ed2a36820a935b8465106a9f283a134caa33
---

# Déployer Chimera sur un serveur (VPS)

Chimera s'exécute comme un processus **gateway** de longue durée. Ajoutez `--cron` et il
déclenche aussi des tâches planifiées sur une vraie horloge, si bien qu'il *agit à l'heure* (pas
seulement quand on lui envoie un message). Ce guide couvre un déploiement sur un VPS à 5 $ de
deux façons : **Docker Compose** (recommandé) ou **systemd**.

L'état — mémoire à long terme, tâches cron, trajectoires, journal d'audit — vit dans
`CHIMERA_HOME` (un répertoire). Persistez-le (un volume Docker ou un chemin réel) et l'agent
survit aux redémarrages.

---

## 0. Prérequis

- Un VPS Linux (1 vCPU / 1 Go de RAM suffit largement pour un seul agent).
- Au moins une clé de fournisseur. Le démarrage le moins cher est une clé OpenRouter.
- Pour les webhooks entrants publics (WhatsApp Cloud API, `POST /webhook/<hook>`), un domaine +
  un reverse proxy avec TLS (Caddy ou nginx). Pas nécessaire pour Discord/Telegram/Slack/Signal,
  qui se connectent en sortant.

Créez votre fichier d'environnement à partir du modèle et renseignez une clé :

```bash
cp .env.example .env
# edit .env — e.g. set CHIMERA_OPENROUTER_KEYS=sk-or-...
```

---

## 1. Docker Compose (recommandé)

```bash
docker compose up -d       # build + run; restarts on crash and on reboot
docker compose logs -f     # watch it
docker compose ps          # status + health
```

Cela exécute `chimera serve --host 0.0.0.0 --cron` : la gateway HTTP (`/chat`, `/webhook/<hook>`,
`/health`) **plus** le démon cron. L'état persiste dans le volume `chimera-data`.

**Servir une plateforme de chat** (Discord montré ici) — définissez le jeton dans `.env`, puis
surchargez la commande dans `docker-compose.yml` :

```yaml
    command: ["serve", "--host", "0.0.0.0", "--cron", "--discord"]
```

puis relancez `docker compose up -d`. (Telegram/Slack/Signal fonctionnent de la même manière via
leurs propres flags ; chacun a besoin de son jeton `CHIMERA_*` correspondant — voir
`.env.example`.)

**Mettre à jour vers une nouvelle version :**

```bash
git pull && docker compose up -d --build
```

---

## 2. systemd (sans Docker)

Installez dans un virtualenv sur l'hôte :

```bash
git clone https://github.com/brcampidelli/chimera-agent.git /opt/chimera
cd /opt/chimera
python3 -m venv .venv && . .venv/bin/activate
pip install '.[messaging,mcp]'
cp .env.example .env   # then edit it
```

Créez `/etc/systemd/system/chimera.service` :

```ini
[Unit]
Description=Chimera Agent gateway + cron daemon
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/chimera
EnvironmentFile=/opt/chimera/.env
Environment=CHIMERA_HOME=/opt/chimera/state
ExecStart=/opt/chimera/.venv/bin/chimera serve --host 0.0.0.0 --cron
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chimera
sudo systemctl status chimera
journalctl -u chimera -f
```

---

## 3. Planifier du travail proactif (le démon `--cron`)

`--cron` ne fait qu'*exécuter* les tâches que vous avez planifiées. Ajoutez-les avec le CLI
(elles persistent dans `CHIMERA_HOME`) :

```bash
chimera cron add "morning-brief" "0 8 * * *" "Summarize overnight news and post it."
chimera cron add "nightly-backup" "0 3 * * *" "Back up the important files."
chimera cron list
```

Dans Docker :

```bash
docker compose exec chimera chimera cron add "morning-brief" "0 8 * * *" "..."
```

Le démon fait un tick toutes les `--cron-tick` secondes (30 par défaut) et distribue l'action de
chaque tâche via l'agent quand elle est due. Une tâche en échec est journalisée et n'arrête
jamais le démon.

---

## 4. Santé, sauvegardes, sécurité

- **Santé :** `GET /health` renvoie `{"ok": true}`. Compose a un healthcheck câblé.
- **Sauvegardes :** sauvegardez le volume `chimera-data` (Docker) ou le répertoire `CHIMERA_HOME`
  (systemd) — c'est tout l'état durable. Exemple :
  `docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf /b/chimera-state.tgz -C /d .`
- **Secrets :** gardez les clés dans `.env` (ignoré par git) ; ne les intégrez jamais dans
  l'image.
- **Exposition :** ne liez la gateway à `0.0.0.0` que derrière un pare-feu/reverse proxy.
  Définissez **`CHIMERA_SERVER_TOKEN`** pour exiger `Authorization: Bearer <token>` sur la
  gateway HTTP et l'API desktop (l'interface desktop reçoit automatiquement le jeton uniquement
  pour les clients en loopback, donc une instance exposée à distance reste derrière votre propre
  authentification). L'authentification est opt-in et vide par défaut, donc sans cette variable
  il n'y en a aucune — restreignez le port, ou n'exposez que le chemin du webhook.
- **Sandboxing :** définissez `CHIMERA_SANDBOX=docker` pour exécuter les outils shell/code dans
  un conteneur jetable plutôt que sur l'hôte.
- **Exécution hôte sans surveillance :** depuis le 2026-07-20, un run headless **refuse** les
  commandes hôte sous le défaut `CHIMERA_HOST_EXEC=ask` (il n'y a pas de TTY pour confirmer). Un
  déploiement qui a réellement besoin que l'agent exécute du shell sur l'hôte définit
  délibérément `CHIMERA_HOST_EXEC=allow` ; l'option la plus sûre est `CHIMERA_SANDBOX=docker`, où
  la barrière est sautée parce que le conteneur isole réellement. De même, le serveur API arme le
  rétrécissement lié à la contamination (`CHIMERA_TAINT_NARROW=1`) : après que l'agent a lu du
  contenu non fiable, les outils d'exécution/écriture/sortie échouent par défaut (fail closed).
  Mettez-le à `0` pour continuer à agir de manière autonome.

---

## 5. Statut honnête

Chimera est en **alpha**. Cela se déploie et fonctionne, et le démon cron le rend proactif —
mais il n'a **aucun vécu en production** pour l'instant. Commencez par des crons à faible enjeu,
surveillez les `logs`, et gardez à l'esprit les garde-fous de gouvernance (`--guard` sur `solve`,
`CHIMERA_SANDBOX=docker`) pour tout ce qui touche à de vrais systèmes.

## Où ces pages sont publiées

Ces fichiers sont la source de la documentation sur **chimeraagent.space**, qui les rend
directement depuis ce répertoire au moment du build. Modifiez le markdown ici et le site suit ;
il n'y a pas de seconde copie à tenir synchronisée.

La configuration MkDocs qui vivait autrefois dans `mkdocs.yml` a été supprimée. Elle était
complète — thème, navigation, dix pages — et elle n'a jamais été publiée : il n'y avait ni
workflow ni branche `gh-pages`, donc les instructions de déploiement qui se trouvaient autrefois
à cet endroit décrivaient un site qui n'existait pas. Une configuration que personne n'exécute
est pire qu'aucune configuration, parce que la prochaine personne modifie sa navigation et ne
comprend pas pourquoi rien ne change.
