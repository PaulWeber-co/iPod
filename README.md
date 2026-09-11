# iPodFS

Direkter USB-Zugriff auf einen iPod touch (oder ein iPhone) vom PC oder Mac —
**ohne iTunes, ohne CopyTrans, ohne Cloud**. Mit einer Musikverwaltung, die das
Problem löst, an dem Cover Flow auf alten Geräten reihenweise scheitert:
Alben, die in mehrere Kacheln zerfallen, weil bei einem Song
*„Katy Perry“* und beim nächsten *„Katy Perry feat. Snoop Dogg“* steht.

```
┌──────────┐   USB    ┌─────────┐   TCP    ┌────────────┐   AFC   ┌───────────┐
│  iPodFS  │ ───────► │ usbmux  │ ───────► │ lockdownd  │ ──────► │ Dateien   │
│ (Python) │          │(Treiber)│  :62078  │ (auf iPod) │         │ auf iPod  │
└──────────┘          └─────────┘          └────────────┘         └───────────┘
```

---

## Das Problem, kurz erklärt

Die Musik-App auf dem iPod baut **eine Cover-Flow-Kachel pro Kombination aus
Album-Interpret und Album**. Der Album-Interpret ist das ID3-Feld `TPE2` —
und wenn das leer ist, nimmt iOS ersatzweise den normalen Interpreten `TPE1`.

Genau da bricht es:

| Song | Interpret (TPE1) | Album-Interpret (TPE2) | Kachel |
|------|------------------|------------------------|--------|
| Teenage Dream | Katy Perry | *leer* | „Katy Perry – Teenage Dream“ |
| California Gurls | Katy Perry feat. Snoop Dogg | *leer* | „Katy Perry feat. Snoop Dogg – Teenage Dream“ |
| E.T. | Katy Perry (feat. Kanye West) | *leer* | „Katy Perry (feat. Kanye West) – Teenage Dream“ |

Ein Album, drei Kacheln, dreimal dasselbe Cover.

iPodFS setzt `TPE2` bei allen drei Songs auf `Katy Perry` — der Interpret bleibt
unangetastet, die Gastauftritte gehen also nicht verloren. Aus drei Kacheln
wird eine.

### Und die anderen Ursachen, die keiner sieht

Dasselbe passiert bei einem halben Dutzend weiterer Kleinigkeiten. iPodFS
erkennt und behebt sie alle:

| Ursache | Beispiel |
|---------|----------|
| feat. / ft. / featuring im Interpret | `Katy Perry feat. Snoop Dogg` |
| Gäste ohne feat.-Marker | `Eminem & Rihanna`, `Eminem, Lil Wayne` |
| Album-Interpret uneinheitlich gesetzt | mal `Eminem`, mal `Eminem & Rihanna` |
| Zusätze im Albumnamen | `Recovery` vs. `Recovery (Deluxe Edition)` |
| Disc-Nummer im Albumnamen | `The Wall (Disc 1)` / `(Disc 2)` |
| **Unsichtbare Zeichen** | `Teenage Dream` vs. `Teenage Dream␠`, geschütztes Leerzeichen, typografischer Apostroph |
| Uneinheitliche oder mehrfache Cover | drei Songs mit Cover A, einer mit Cover B |
| Kompilations-Flag (TCMP) uneinheitlich | ein Song landet unter „Compilations“ |

Bänder wie *Simon & Garfunkel* oder *Earth, Wind & Fire* bleiben dabei heil —
ein `&` allein trennt nichts, nur ein erkennbarer Hauptinterpret tut das.
Echte Sampler behalten „Various Artists“.

Vor jeder Reparatur landen die alten Werte **samt Cover** in einem Backup.
Ein Klick nimmt den ganzen Lauf zurück.

---

## Loslegen

### Windows

1. [Python 3.9+](https://www.python.org/downloads/) installieren
   (beim Setup **„Add python.exe to PATH“** ankreuzen).
2. Den USB-Treiber besorgen — **das ist nicht iTunes**, siehe
   [`tools/windows_treiber.md`](tools/windows_treiber.md).
   Kurzfassung: *Apple Devices* aus dem Microsoft Store installieren,
   einmal starten, nie wieder öffnen.
3. `start.bat` doppelklicken.

### macOS

```bash
./start.sh
```

Mehr ist nicht nötig: macOS bringt usbmux seit jeher mit.

### Linux

```bash
sudo apt install usbmuxd    # oder das Äquivalent der Distribution
./start.sh
```

Der Browser öffnet sich auf `http://127.0.0.1:8731/`. Der Server hört
ausschließlich auf localhost und verlangt bei jedem Aufruf einen Token, den
nur die eigene Seite kennt.

---

## Der Ablauf

1. **iPod anstecken.** Hängt genau ein Gerät am Kabel, verbindet sich iPodFS
   von selbst. Oben rechts stehen Name, Modell, iOS-Version und freier Platz.

2. **Dateien.** Der Reiter *Dateien* zeigt das echte Dateisystem des Geräts:
   `/var/mobile/Media` mit `DCIM`, `Photos`, `Recordings` und allem, was sonst
   dort liegt. Dateien und ganze Ordner lassen sich aus dem Explorer bzw.
   Finder hineinziehen, in beide Richtungen kopieren, umbenennen und löschen.

3. **Musik aufräumen.** Musikordner wählen, *Einlesen*. iPodFS zeigt sofort:
   *„10 Cover-Kacheln jetzt → 5 nach der Reparatur, 5 überflüssige Dubletten.“*
   Jedes Album lässt sich aufklappen; dort steht Feld für Feld, was sich ändern
   würde — <del>alter Wert</del> → <ins>neuer Wert</ins>. Erst dann wird
   geschrieben.

4. **Synchronisieren.** Ziel wählen, *Vorschau*, *Übertragen*. Ein Manifest auf
   dem Gerät merkt sich den Stand, beim nächsten Mal wandert nur das Geänderte
   über das Kabel.

---

## Was geht — und was nicht

Hier die ehrliche Auskunft, denn an dieser Stelle scheitern alle
Selbstbau-Lösungen.

### Geht

* **Volles Lesen und Schreiben auf der Medien-Partition** (`com.apple.afc`) —
  ohne Jailbreak, ohne jede Apple-Anwendung.
* **Sandbox jeder App mit Dateifreigabe** (`house_arrest`) — dort landet die
  Musik. Genau dieser Mechanismus steckt hinter der „Dateifreigabe“ in iTunes.
* **Apps über USB installieren** (`installation_proxy`) — signierte `.ipa`
  vorausgesetzt, mit Jailbreak + AppSync jede.
* **Mit Jailbreak: das komplette Wurzel-Dateisystem** über `com.apple.afc2`.
  iPodFS erkennt das automatisch und bietet `/` als zusätzliche Wurzel an.

### Geht nicht — und warum

**Die iOS-eigene Musik-App lässt sich von außen nicht befüllen.**

Bis iOS 4 lag die Musikdatenbank in `iTunes_Control/iTunes/iTunesDB`, und
Projekte wie libgpod konnten sie schreiben. Ab iOS 5 ist es
`iTunes_Control/iTunes/MediaLibrary.sqlitedb`, und jede dieser Dateien trägt
eine gerätespezifische Prüfsumme — intern „hashAB“ genannt, abgelegt in
`.cbk`-Dateien.

Dieser Hash ist bis heute nicht reversiert. libgpod lädt dafür ein Modul
`libhashab.so`, das für iPhone und iPod touch schlicht nie jemand geschrieben
hat; in der Dokumentation steht ausdrücklich, dass iPhone 4, iPod touch 4 und
iPad nicht unterstützt werden. Eine selbst gebaute Datenbank ohne gültige
Prüfsumme ignoriert die Musik-App. Nachträgliches Hineinschreiben in eine
bestehende Datenbank fällt bei der nächsten Prüfung durch.

Ein Jailbreak ändert daran nichts: die Prüfung läuft auf dem Gerät, und der
Schlüssel steckt in Apples eigenem Code.

**Der Weg, der funktioniert:** ein Player mit Dateifreigabe. VLC etwa liest
seine Mediathek selbst aus den Tags — und weil iPodFS die vorher in Ordnung
gebracht hat, stimmt dort auch die Albumansicht. Kein Cover Flow, aber saubere
Alben, richtige Cover und kein iTunes.

Wie ein Player auf ein iOS-6-Gerät kommt, steht im Reiter *Synchronisieren*:
entweder über den App Store (bietet bei alten Geräten „letzte kompatible
Version“ an) oder als `.ipa` direkt über das Kabel.

---

## Ordnerstruktur auf dem Gerät

Standardlayout — der oberste Ordner kommt aus dem **Album-Interpret**, nicht
aus dem Interpreten. Deshalb liegen Gastauftritte zwangsläufig beim richtigen
Album:

```
/iPodFS/
  Katy Perry/
    Teenage Dream/
      01 Teenage Dream.mp3
      02 California Gurls.mp3      ← Interpret: "Katy Perry feat. Snoop Dogg"
      03 E.T..mp3
  Pink Floyd/
    The Wall/
      1-01 In The Flesh.mp3        ← Doppel-CD bekommt das Disc-Präfix
      2-01 Hey You.mp3
  .ipodfs-manifest.json            ← Stand des letzten Abgleichs
```

Drei weitere Layouts stehen zur Wahl (Interpret statt Album-Interpret, nur
Album, oder alles flach in einen Ordner).

---

## Aufbau

| Datei | Aufgabe |
|-------|---------|
| `ipodfs/devicelink.py` | usbmux / lockdown / AFC / house_arrest — der ganze Gerätezugriff |
| `ipodfs/tags.py` | Tags lesen und schreiben, formatunabhängig (MP3, M4A, FLAC, OGG) |
| `ipodfs/normalize.py` | Album-Diagnose und Reparaturplan — das Herzstück |
| `ipodfs/library.py` | Sammlung einlesen, Plan anwenden, Backup und Undo |
| `ipodfs/syncer.py` | Zielpfade, Manifest, inkrementeller Abgleich |
| `ipodfs/jobs.py` | Hintergrundarbeiten mit Fortschritt |
| `ipodfs/server.py` | lokale REST-API |
| `ipodfs/static/` | Oberfläche (reines HTML/CSS/JS, kein Build-Schritt) |
| `ipodfs/doctor.py` | Verbindungsdiagnose mit Klartext-Hinweisen |
| `tools/make_testlib.py` | erzeugt eine Testsammlung mit genau diesen Problemen |

Abhängigkeiten: `pymobiledevice3` (Gerät), `mutagen` (Tags), `Flask`
(Oberfläche), `Pillow` (Cover). Nichts davon stammt von Apple.

### Warum eine Weboberfläche?

Weil Drag & Drop aus dem Explorer und dem Finder dort ohne Zusatzbibliothek
funktioniert — ganze Ordnerbäume eingeschlossen — und auf allen drei
Betriebssystemen gleich aussieht.

---

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

37 Tests. `tests/fake_device.py` bildet die Geräteschnittstelle auf einem
lokalen Ordner nach, sodass der komplette Sync-Ablauf — Manifest,
inkrementeller Abgleich, verwaiste Dateien, voller Speicher — ohne echten iPod
durchläuft.

Zum Ausprobieren ohne eigene Musik:

```bash
.venv/bin/python tools/make_testlib.py test_music
```

Erzeugt fünf Alben mit genau den Problemen aus der Tabelle oben.

---

## Wenn etwas klemmt

Zuerst in den Reiter **Diagnose** schauen — dort steht Schritt für Schritt,
wo die Kette reißt, und was dagegen hilft. Dasselbe im Terminal:

```bash
.venv/bin/python -m ipodfs --doctor
```

| Symptom | Ursache |
|---------|---------|
| „Kein Gerät gefunden“ trotz Kabel | Meist ein Ladekabel ohne Datenleitung. Ein Kabel nehmen, mit dem schon einmal Daten geflossen sind. |
| „usbmux-Dienst nicht erreichbar“ | Windows: Treiber fehlt → `tools/windows_treiber.md`. Linux: `sudo apt install usbmuxd`. |
| „Der iPod ist gesperrt“ | Code eingeben, Bildschirm anlassen. |
| „Zugriff verweigert“ | Der Pfad liegt außerhalb von `/var/mobile/Media`. Ohne Jailbreak ist dort Schluss. |
| Nach dem Reparieren stimmt etwas nicht | *Musik aufräumen → Rückgängig machen*. Das Backup enthält auch die alten Cover. |

---

## Hinweis

Für den privaten Gebrauch mit eigener Musik auf eigenen Geräten. iPodFS
benutzt dieselben dokumentierten Dienste, die auch Apples Software anspricht —
es wird nichts umgangen und nichts entschlüsselt.
