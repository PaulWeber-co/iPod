# Windows: USB-Treiber ohne iTunes

Unter macOS und Linux spricht iPodFS das Gerät direkt an — macOS bringt den
usbmux-Dienst ab Werk mit, Linux bekommt ihn mit `sudo apt install usbmuxd`.

Windows hat nichts Vergleichbares. Der USB-Treiber für iOS-Geräte kommt dort
ausschließlich von Apple. Er steckt aber in einem eigenen Installationspaket,
das sich **ohne iTunes** einspielen lässt.

## Weg 1 — „Apple Devices“ aus dem Microsoft Store (am einfachsten)

Apple hat iTunes unter Windows aufgeteilt. Die App **Apple Devices** enthält
den Treiber und den Hintergrunddienst, aber keinen Musikplayer und keine
Mediathek.

1. Microsoft Store öffnen, nach *Apple Devices* suchen, installieren.
2. Einmal starten, damit der Dienst eingerichtet wird — danach schließen.
3. iPod anstecken. In iPodFS auf **Diagnose → Erneut prüfen** klicken.

Die App muss nie wieder geöffnet werden. iPodFS redet direkt mit dem Dienst.

## Weg 2 — nur das Treiberpaket aus dem iTunes-Installer

Wer gar nichts aus dem Store installieren will, zieht sich das MSI-Paket
einzeln heraus. Installiert wird dabei nur der Treiber, nicht iTunes.

1. `iTunes64Setup.exe` von apple.com/de/itunes/download herunterladen.
   **Nicht ausführen.**
2. [7-Zip](https://www.7-zip.org/) installieren.
3. Rechtsklick auf `iTunes64Setup.exe` → *7-Zip* → *Entpacken nach …*
4. Im entpackten Ordner liegen mehrere `.msi`-Dateien. Nur diese beiden
   per Doppelklick installieren, in dieser Reihenfolge:
   - `AppleApplicationSupport64.msi`
   - `AppleMobileDeviceSupport64.msi`
5. Die übrigen Dateien (`iTunes64.msi`, `AppleSoftwareUpdate.msi`,
   `Bonjour64.msi`) einfach liegen lassen — die braucht niemand.
6. Neu starten, iPod anstecken, in iPodFS auf **Diagnose** prüfen.

Erfolgskontrolle: Der Dienst *Apple Mobile Device Service* muss laufen.
In der Eingabeaufforderung:

    sc query "Apple Mobile Device Service"

`STATE : 4 RUNNING` heißt: alles da. iPodFS zeigt dasselbe in der Diagnose an.

## Weg 3 — komplett ohne Apple-Software

Das Projekt [libimobiledevice](https://libimobiledevice.org/) hat einen
eigenen `usbmuxd` für Windows. Dafür muss dem iPod mit
[Zadig](https://zadig.akeo.ie/) der WinUSB-Treiber zugewiesen werden, was
Apples Treiber ersetzt — für ein Gerät, das ohnehin nie wieder an iTunes soll,
ist das kein Verlust.

Läuft `usbmuxd.exe` als TCP-Dienst, findet iPodFS ihn über die
Umgebungsvariable:

    set USBMUXD_SOCKET_ADDRESS=127.0.0.1:27015

Dieser Weg ist der fummeligste und nur etwas für Leute, die wirklich kein
einziges Apple-Bit auf der Platte haben wollen.

## Wenn es trotzdem nicht klappt

* **Kabel.** Die häufigste Ursache. Viele 30-Pin-Kabel aus Grabbelkisten haben
  nur Stromleitungen. Ein Kabel nehmen, mit dem das Gerät nachweislich schon
  einmal Daten übertragen hat.
* **Bildschirm entsperren.** Ein gesperrter iPod gibt keine Dienste frei.
* **„Vertrauen“ bestätigen.** Ab iOS 7 fragt das Gerät nach. iOS 6 koppelt
  stillschweigend.
* **USB-Port direkt am Rechner**, nicht über einen Hub.
