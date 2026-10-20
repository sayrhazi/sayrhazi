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

Avec les fonctions de profil (sourcées depuis `Sayrhazi-Profile.ps1` du dépôt) :

```powershell
Sayrhazi .
tunda sayrhazi
```

Le dashboard a aussi son raccourci, pour éviter d'avoir à se souvenir de
`python .opencode/watch-tui.py` :

```powershell
sayrhazi-tui                          # projet courant
sayrhazi-tui C:\mon\projet            # n'importe où dans le terminal
sayrhazi-tui . --once                 # une seule capture, puis sortie
```

Sous Linux, le même binaire utilise `python3`. Un tableau de détail
s'affiche sous le tableau et suit la ligne sélectionnée (flèches ou clic) : il
précise le modèle, la fenêtre de contexte, le coût de session et la décomposition
du contexte (`input`, `cache.read`, `cache.write`, `output`, `reasoning`). Cette
dernière est la plus parlante — `cache.read` représente d'ordinaire l'essentiel
du contexte renvoyé, donc une jauge à 55 % de fenêtre ne signifie pas que
l'agent a produit 55 % de tokens.

Deux façons de l'obtenir, parce qu'elles n'ont pas la même durée de vie :

| Source | Durée de vie | Marche quand |
|---|---|---|
| `npm i -g sayrhazi` | durable | immédiatement, le binaire est sur le PATH |
| profil (`Sayrhazi-Profile.ps1` / `Sayrhazi-Profile.sh`) | la session du terminal | après rechargement du profil ou ouverture d'un nouveau terminal |

Une fonction de profil n'existe que dans la session qui l'a définie : après une
mise à jour du noyau qui ajoute une commande, cette commande reste introuvable
jusqu'au rechargement. Préférez le binaire npm, qui n'a pas cette limite.

Dans les deux cas : le premier argument est le point de départ et la racine est
cherchée en remontant jusqu'à `.opencode/watch-tui.py`, donc rester dans
`.opencode` ou un sous-dossier marche aussi. Le dashboard résout sa racine
depuis le répertoire courant : la commande change de dossier pour la durée du
lancement puis revient, sans quoi `sayrhazi-tui <projet>` analyserait le projet
du terminal. Les flags après le chemin (`--once`, `--interval`, `--lines`) sont
transmis au dashboard. Sur un projet absent ou sans `.opencode/watch-tui.py`,
elle s'arrête sur un message explicite.

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
| `sayrhazi --version` | Affiche la version du noyau | — |

**Options communes** :

| Option | Commandes concernées | Effet |
|---|---|---|
| `--project <chemin>` | `check`, `tunda`, `info`, `update`, `remove` | Projet cible (défaut : dossier courant) |
| `--once` | `sayrhazi-tui` | Une seule capture puis sortie |
| `--interval <sec>` | `sayrhazi-tui` | Intervalle de rafraîchissement (défaut 1s) |
| `--lines <n>` | `sayrhazi-tui` | Nombre de lignes de log affichées |
| `--yes` | `remove` | Supprime sans confirmation |
| `--version` | `sayrhazi` | Affiche la version |

**Règle d'or** : utilisez toujours **`sayrhazi <commande>`**. Cela fonctionne
**identiquement sur Windows (PowerShell) et Linux (bash)**, sans profil à
sourcer. Les formes `<cmd> sayrhazi` (ex. `tunda sayrhazi`, `check sayrhazi`,
`info sayrhazi`) sont des fonctions de profil — elles nécessitent que le profil
soit sourcé dans le terminal, et ne fonctionnent pas sur Linux pour `info` /
`update` (conflit avec commandes système). Le binaire `sayrhazi` n'a aucune
dépendance au profil.

## Depuis le dépôt Sayrhazi

```powershell
cd <clone>
.\runtimes\opencode\scripts\Install-Sayrhazi.ps1 -ProjectPath "C:\chemin\vers\MonProjet"
```

## Linux

Le lanceur npm utilise `python3` hors Windows. Deux différences avec Windows méritent d'être connues, toutes deux rencontrées à l'usage :

**`yarn add` n'installe pas la commande sur le PATH.** `yarn add` place le binaire dans `./node_modules/.bin`, qui n'est pas dans le PATH : `sayrhazi` reste « commande introuvable ». Seul `npm install -g sayrhazi` installe réellement la commande. De même, `yarn remove -r sayrhazi` est une erreur — `-r` signifie *recursive* et attend des workspaces ; la commande est `yarn remove sayrhazi`.

**Le dashboard exige `textual`, absent d'une installation fraîche.** Sans lui, `sayrhazi-tui` s'arrête sur une erreur d'import. `tunda sayrhazi` le signale désormais avant que cela n'arrive. Installation recommandée, dans un venv dédié pour ne pas toucher au Python système :

```bash
python3 -m venv ~/.venvs/sayrhazi
~/.venvs/sayrhazi/bin/pip install textual
```

Si `pip3 install textual` direct est refusé par votre distribution (Ubuntu 23.10+ applique PEP 668), utilisez le venv ci-dessus plutôt que `--break-system-packages`.

Rendre le script exécutable puis l'appeler avec la racine du projet :

```bash
chmod +x runtimes/opencode/scripts/install-sayrhazi.sh
./runtimes/opencode/scripts/install-sayrhazi.sh /chemin/vers/MonProjet
```

Sous bash, les fonctions de profil (`Sayrhazi`, `tunda`, `update`, `info`, `remove`, `sayrhazi-tui`) se sourcent depuis `~/.bashrc` :

```bash
export SAYRHAZI_HOME="$(npm root -g)/sayrhazi"
. "$SAYRHAZI_HOME/runtimes/opencode/scripts/Sayrhazi-Profile.sh"
```

**Attention aux collisions de noms.** Sur Ubuntu, `info` et `update` sont des commandes système (coreutils et `update-manager`) : tant que le profil n'est pas sourcé, `info sayrhazi` appelle le `info` de coreutils et répond « aucune entrée de menu ». Ce n'est pas une panne de Sayrhazi. Le binaire global `sayrhazi` et `sayrhazi-tui` n'ont pas ce conflit et sont préférables.

## Ce que fait l'installation

Le script crée `.opencode/agent/`, `resume/`, `history/`, `features/`, `state/` et `backup/`, puis copie les six agents génériques, `watch-work.py` (orchestrateur), `watch-tui.py` (dashboard, lecture seule) et `models.yaml` (fenêtres de contexte connues), tous mis à jour à chaque install. `.opencode/state/` (`workflow-state.yaml`, vue de coordination du Lot 3) est local à la machine : ajoutez-le au `.gitignore` du projet (la config `workflow.yaml` se versionne, l'état non). Il crée `AGENTS.md`, `sayrhazi.yaml` et `opencode.json` uniquement lorsqu'ils sont absents. Il ne supprime ni ne remplace les rapports, historiques, décisions ou configuration déjà présents.

Les fichiers d'agents sont les fichiers fournis par Sayrhazi et peuvent être actualisés par une future version. Pour un projet existant, effectuer un commit ou une sauvegarde avant toute mise à jour et relire les différences.

## Après installation

Compléter `.opencode/sayrhazi.yaml`, notamment `project.name`, les commandes réelles et les options de QA. Vérifier les règles locales dans `AGENTS.md` (§4), puis lancer le check depuis le projet :

```powershell
tunda sayrhazi
# ou sans alias : python "$env:SAYRHAZI_HOME/cli/commands/check.py" sayrhazi --project .
```

Ouvrir ensuite OpenCode depuis la racine du projet utilisateur, pas depuis le dépôt Sayrhazi.

## Mise à jour (`update sayrhazi`)

Quand le noyau évolue, mettre à jour depuis un projet déjà intégré :

```powershell
update sayrhazi
# ou multi-plateforme : python cli/main.py update --project <projet>
```

Cela réinstalle agents + `watch-work.py` + `watch-tui.py` (mis à jour avec `-Force`), affiche `version projet -> noyau`, signale les fichiers `agent/*.md` inconnus du noyau (suppression manuelle), puis relance le check. Configuration, rapports, historiques, décisions, `models.yaml` et `AGENTS.md` ne sont jamais écrasés.

Migration douce de la config : les clés manquantes du template sont ajoutées à `sayrhazi.yaml` (ex. nouvelle section `agents:`, `database:` legacy migrée vers `database_type/driver/name` avec reprise de l'ancien nom), backup horodaté dans `backup/` (les anciens backups restés dans `history/` ne sont pas déplacés), valeurs existantes et `version:` alignée sur le noyau. Vérifier les valeurs migrées après coup.

## Mise à jour prudente

Ne jamais déplacer l'historique d'un projet sans sauvegarde. Pour une migration, conserver les rapports et décisions, comparer les agents existants avec la version Sayrhazi, puis intégrer les changements par commit identifiable.
