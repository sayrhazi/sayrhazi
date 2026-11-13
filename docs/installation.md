# Installer Sayrhazi

## Prérequis

L'installation nécessite uniquement un projet existant, Git est recommandé, et PowerShell sous Windows ou Bash sous Linux. Sayrhazi ne nécessite pas Docker. Docker n'est ajouté que si le projet applicatif en a besoin.

## Depuis n'importe quel projet (recommandé)

Via npm (pas de clone requis) :

```powershell
npm install -g sayrhazi
```

Puis, depuis le projet cible :

```powershell
sayrhazi .
```

Depuis un clone du dépôt (`https://github.com/Lawi-Salim/sayrhazi.git`), le script retrouve sa source tout seul :

```powershell
& "<clone>\runtimes\opencode\scripts\Install-Sayrhazi.ps1" -ProjectPath "."
```

Le dashboard web (`sayrhazi server`) est l'interface principale : état du workflow, rapports d'agents détaillés, création et suppression de discussions (titre renommé automatiquement par OpenCode, synchronisé — sessionID réel), streaming token par token avec indicateurs d'activité, pied de bulle agent (modèle + durée de tour), bouton de copie sous le message utilisateur. Il s'ouvre sur `http://localhost:8363`.

## Référence des commandes `sayrhazi`

Toutes les commandes s'utilisent sous la forme **`sayrhazi <commande>`** — cela
fonctionne **identiquement** sur Windows (PowerShell) et Linux (bash), sans
profil à sourcer.

| Commande | Description | Alias |
|---|---|---|
| `sayrhazi` | Installe Sayrhazi dans le dossier courant (ou chemin donné) | `sayrhazi .` |
| `sayrhazi install` | Installe explicitement | — |
| `sayrhazi check` | Vérifie la configuration du projet (tunda) | `sayrhazi tunda` |
| `sayrhazi tunda` | Alias de `check` | `sayrhazi check` |
| `sayrhazi info` | Affiche version noyau, version projet, agents, rapports | — |
| `sayrhazi update` | Met à jour le projet vers la version noyau | — |
| `sayrhazi remove` | Supprime les fichiers noyau (simulation sans `--yes`) | — |
| `sayrhazi server` | Lance le serveur web (dashboard + watcher, port 8363) | — |
| `sayrhazi --version` | Affiche la version du noyau | — |

**Options communes** :

| Option | Commandes concernées | Effet |
|---|---|---|
| `--project <chemin>` | `check`, `tunda`, `info`, `update`, `remove` | Projet cible (défaut : dossier courant) |
| `--yes` | `remove` | Supprime sans confirmation |
| `--version` | `sayrhazi` | Affiche la version |

**Règle d'or** : utilisez toujours **`sayrhazi <commande>`**. Cela fonctionne
**identiquement sur Windows (PowerShell) et Linux (bash)**, sans profil à
sourcer. Le binaire `sayrhazi` n'a aucune dépendance au profil de terminal.

## Dashboard web

Le dashboard web (`sayrhazi-serveur.py`, servi par le serveur unifié)
s'ouvre sur **`http://localhost:8363`** via `sayrhazi server`. C'est l'interface
graphique de Sayrhazi : état du workflow, rapports des agents, création et suppression
de discussions avec les agents via l'API native OpenCode (renommage automatique
du titre, synchronisé — sessionID réel), streaming token par token avec
indicateurs d'activité, pied de bulle agent (modèle + durée de tour) et bouton
de copie sous le message utilisateur.

`sayrhazi-modules.py` est désormais l'unique module partagé par le dashboard web (et les tests) pour tout ce qui concerne les données coordination, rapports et métriques OpenCode. Il n'a aucune dépendance externe, notamment pas `textual`.

Pour recharger proprement après mise à jour, faire un Ctrl+F5 ; les assets sont
servis avec `Cache-Control: no-store`.


## Depuis le dépôt Sayrhazi

```powershell
cd <clone>
.\runtimes\opencode\scripts\Install-Sayrhazi.ps1 -ProjectPath "C:\chemin\vers\MonProjet"
```

## Linux

Le lanceur npm utilise `python3` hors Windows. Deux différences avec Windows méritent d'être connues, toutes deux rencontrées à l'usage :

**`yarn add` n'installe pas la commande sur le PATH.** `yarn add` place le binaire dans `./node_modules/.bin`, qui n'est pas dans le PATH : `sayrhazi` reste « commande introuvable ». Seul `npm install -g sayrhazi` installe réellement la commande. De même, `yarn remove -r sayrhazi` est une erreur — `-r` signifie *recursive* et attend des workspaces ; la commande est `yarn remove sayrhazi`.

Rendre le script exécutable puis l'appeler avec la racine du projet :

```bash
chmod +x runtimes/opencode/scripts/install-sayrhazi.sh
./runtimes/opencode/scripts/install-sayrhazi.sh /chemin/vers/MonProjet
```

Sous Linux, seules les commandes du binaire npm sont nécessaires (`sayrhazi`). Elles n'ont pas besoin d'être sourcées dans `~/.bashrc` et ne rencontrent pas de conflit avec les commandes système `info` ou `update`.

## Ce que fait l'installation

Le script crée `.opencode/agent/`, `resume/`, `history/`, `features/`, `state/` et `backup/`, puis copie les six agents génériques, `watch-work.py` (orchestrateur), `services.py` (gestionnaire de services), `sayrhazi-modules.py` (briques partagées : lecture OpenCode + `Handler` web), `sayrhazi-serveur.py` + `static/` (dashboard web) (serveur unifié : dashboard web + moteur watcher dans un seul processus) et `models.yaml` (fenêtres de contexte connues), tous mis à jour à chaque installation. `.opencode/state/` (`workflow-state.yaml`, vue de coordination) est local à la machine : ajoutez-le au `.gitignore` du projet. Il crée `AGENTS.md`, `sayrhazi.yaml` et `opencode.json` uniquement lorsqu'ils sont absents. Il ne supprime ni ne remplace les rapports, historiques, décisions ou configuration déjà présents. Le dossier personnel OpenCode (`~/.opencode/agent/`) est aussi synchronisé : OpenCode y lit le `model:` de chaque agent au lancement, donc tout changement de modèle du projet s'y répercute sur toutes les sessions (installateurs shell et PowerShell idem).

Les fichiers d'agents sont les fichiers fournis par Sayrhazi et peuvent être actualisés par une future version. Pour un projet existant, effectuer un commit ou une sauvegarde avant toute mise à jour et relire les différences.

## Après installation

Compléter `.opencode/sayrhazi.yaml`, notamment `project.name`, les commandes réelles et les options de QA. Vérifier les règles locales dans `AGENTS.md` (§4), puis lancer le check depuis le projet :

```powershell
sayrhazi tunda
# ou sans alias : python cli/commands/check.py sayrhazi --project .
```

Ouvrir ensuite OpenCode depuis la racine du projet utilisateur, pas depuis le dépôt Sayrhazi.

## Mise à jour (`update sayrhazi`)

Quand le noyau évolue, mettre à jour depuis un projet déjà intégré :

```powershell
sayrhazi update
# ou multi-plateforme : python cli/main.py update --project <projet>
```

Cela réinstalle agents + `watch-work.py` + `sayrhazi-modules.py` (mis à jour avec `-Force`), affiche `version projet -> noyau`, signale les fichiers `agent/*.md` inconnus du noyau (suppression manuelle), puis relance le check. Configuration, rapports, historiques, décisions, `models.yaml` et `AGENTS.md` ne sont jamais écrasés.

Migration douce de la config : les clés manquantes du template sont ajoutées à `sayrhazi.yaml` (ex. nouvelle section `agents:`, `database:` legacy migrée vers `database_type/driver/name` avec reprise de l'ancien nom), backup horodaté dans `backup/` (les anciens backups restés dans `history/` ne sont pas déplacés), valeurs existantes et `version:` alignée sur le noyau. Vérifier les valeurs migrées après coup.

## Mise à jour prudente

Ne jamais déplacer l'historique d'un projet sans sauvegarde. Pour une migration, conserver les rapports et décisions, comparer les agents existants avec la version Sayrhazi, puis intégrer les changements par commit identifiable.
