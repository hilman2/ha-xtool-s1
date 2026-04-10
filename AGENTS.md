# Projekt-Hinweise

## SSH-Zugang All-Inkl

- Host: `w01a0e03.kasserver.com`
- User: `ssh-w01a0e03`
- SSH-Config Alias: `allinkl` (in `~/.ssh/config`)
- Key: `~/.ssh/id_ed25519` (Public Key im KAS hinterlegt)
- Auth: Key + Passwort (All-Inkl verlangt beides)

## Dev-Tooling: IMMER WSL Ubuntu

- Alle Python-Dev-Tools (`pytest`, `mypy`, `ruff`, `black`) laufen in WSL Ubuntu, niemals mit Windows-Python.
- Die Default-WSL-Distro ist `docker-desktop` und hat kein Python. Immer explizit `-d Ubuntu` verwenden.
- Venvs liegen in `~/venvs/<project>` innerhalb von WSL, nicht im Repo auf `/mnt/d` (NTFS long-path bug).
- Repo-Pfade: `D:\Git\...` werden in WSL zu `/mnt/d/Git/...`.
- Aufruf-Pattern:

```bash
wsl -d Ubuntu -- bash -lc 'source ~/venvs/<project>/bin/activate && cd /mnt/d/Git/<repo> && <command>'
```

## rsync auf Windows

- Windows hat kein natives `rsync`.
- Nutzung daher immer über WSL Ubuntu: `wsl -d Ubuntu rsync ...`
- WSL kennt Windows-SSH-Keys nicht automatisch. Für SSH-Transport daher Windows-`ssh.exe` verwenden:

```bash
-e /mnt/c/Windows/System32/OpenSSH/ssh.exe
```

## GitHub-Workflow

- Vor jeder Arbeit immer zuerst den aktuellen Stand von GitHub holen, mindestens mit `git fetch origin --prune`.
- Nicht auf veraltetem lokalen Stand arbeiten. Die Ausgangsbasis muss vor Änderungen mit GitHub abgeglichen sein.
- Niemals direkt auf `main` arbeiten.
- Für jede Änderung immer zuerst einen Branch anlegen und die Arbeit vollständig auf diesem Branch erledigen.
- Änderungen immer per Pull Request nach `main` einbringen, niemals direkt auf `main` pushen.
- `main` ist geschützt. Pull Requests müssen die erforderlichen GitHub-Checks bestehen und vor dem Merge aktuell zu `main` sein.
- Die GitHub-CI ist verbindlich und führt mindestens Lint, Hassfest, HACS-Validierung und Tests aus.
- Offene Review-Gespräche müssen vor dem Merge aufgelöst sein.
- Tags im Format `v*` lösen automatisch den Release-Workflow aus.
- Der Release-Workflow führt CI erneut als Gate aus, prüft die `manifest.json` gegen den Tag und baut danach automatisch die GitHub Release ZIP.
- Release-Tags daher nur bewusst und im vorgesehenen Release-Prozess erstellen.

## Empfohlenes Arbeitsmuster

1. `git fetch origin --prune`
2. Branch von aktuellem `main` erstellen
3. Änderungen umsetzen
4. Lokal prüfen
5. Branch pushen
6. Pull Request erstellen
7. Nach grünem CI und abgeschlossenem Review mergen
