"""
iPod Sync Tool - Desktop Application
Fixes MP3 metadata issues (album splitting) and prepares music for iPod transfer.

Uses CustomTkinter for a modern dark-mode GUI.
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import os
import io
import threading
from collections import defaultdict

import metadata_engine as engine

# Try importing Pillow for album artwork
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ──────────────────────────────────────────────
# Color palette
# ──────────────────────────────────────────────
COLORS = {
    'bg_dark': '#0f0f1a',
    'card_ok': '#16213e',
    'card_issue': '#2d1b1b',
    'card_hover_ok': '#1a2744',
    'card_hover_issue': '#3a2222',
    'accent_blue': '#4a9eff',
    'accent_orange': '#e67e22',
    'accent_orange_hover': '#d35400',
    'accent_red': '#e74c3c',
    'accent_red_hover': '#c0392b',
    'accent_green': '#2ecc71',
    'text_primary': '#ffffff',
    'text_secondary': '#8892a0',
    'text_warning': '#ff6b6b',
    'text_suggestion': '#ffd93d',
    'track_bg': '#111827',
    'track_alt': '#151d2e',
    'divider': '#2a2a3e',
}


# ──────────────────────────────────────────────
# Track Editor Dialog
# ──────────────────────────────────────────────
class TrackEditor(ctk.CTkToplevel):
    """Modal dialog for editing a single track's metadata."""

    def __init__(self, master, track, on_save=None):
        super().__init__(master)
        self.title(f"Bearbeiten: {track['title']}")
        self.geometry("520x480")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.track = track
        self.on_save = on_save

        # Center on parent
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - 520) // 2
        y = master.winfo_rooty() + (master.winfo_height() - 480) // 2
        self.geometry(f"+{x}+{y}")

        self._build_ui()

    def _build_ui(self):
        # Title
        ctk.CTkLabel(
            self, text="🎵 Song bearbeiten",
            font=ctk.CTkFont(size=20, weight="bold")
        ).pack(padx=20, pady=(20, 15))

        fields = [
            ("Titel", "title"),
            ("Artist", "artist"),
            ("Album", "album"),
            ("Album Artist", "album_artist"),
            ("Track #", "track_num"),
            ("Genre", "genre"),
        ]

        self.entries = {}
        for label_text, key in fields:
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(fill="x", padx=25, pady=4)

            ctk.CTkLabel(
                row, text=label_text, width=110,
                anchor="w", font=ctk.CTkFont(size=13)
            ).pack(side="left")

            entry = ctk.CTkEntry(row, font=ctk.CTkFont(size=13), height=34)
            entry.pack(side="left", fill="x", expand=True)
            entry.insert(0, self.track.get(key, ''))
            self.entries[key] = entry

        # Filepath info
        ctk.CTkLabel(
            self,
            text=f"📁 {self.track['filename']}",
            font=ctk.CTkFont(size=11),
            text_color=COLORS['text_secondary'],
        ).pack(padx=25, pady=(15, 5), anchor="w")

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=25, pady=(10, 20))

        ctk.CTkButton(
            btn_frame, text="Abbrechen", width=120,
            fg_color="transparent", border_width=1,
            border_color=COLORS['text_secondary'],
            command=self.destroy
        ).pack(side="right", padx=(10, 0))

        ctk.CTkButton(
            btn_frame, text="💾 Speichern", width=140,
            fg_color=COLORS['accent_blue'],
            command=self._save
        ).pack(side="right")

    def _save(self):
        values = {k: e.get() for k, e in self.entries.items()}

        success, error = engine.update_track(
            self.track['filepath'],
            title=values.get('title'),
            artist=values.get('artist'),
            album=values.get('album'),
            album_artist=values.get('album_artist'),
            track_num=values.get('track_num'),
            genre=values.get('genre'),
        )

        if success:
            # Update in-memory track data
            for k, v in values.items():
                self.track[k] = v
            if self.on_save:
                self.on_save()
            self.destroy()
        else:
            messagebox.showerror("Fehler", f"Speichern fehlgeschlagen:\n{error}")


# ──────────────────────────────────────────────
# Transfer Info Dialog
# ──────────────────────────────────────────────
class TransferInfoDialog(ctk.CTkToplevel):
    """Shows instructions for transferring music to iPod."""

    def __init__(self, master):
        super().__init__(master)
        self.title("So überträgst du Musik auf deinen iPod")
        self.geometry("620x480")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - 620) // 2
        y = master.winfo_rooty() + (master.winfo_height() - 480) // 2
        self.geometry(f"+{x}+{y}")

        self._build_ui()

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="📱 Musik auf iPod übertragen",
            font=ctk.CTkFont(size=22, weight="bold")
        ).pack(padx=20, pady=(20, 5))

        ctk.CTkLabel(
            self,
            text="Nachdem du die Metadaten gefixt hast, übertrage so:",
            font=ctk.CTkFont(size=13),
            text_color=COLORS['text_secondary'],
        ).pack(padx=20, pady=(0, 15))

        text_content = (
            "━━━  MIT ITUNES (empfohlen)  ━━━\n\n"
            "1.  Öffne iTunes\n"
            "2.  Verbinde den iPod per USB-Kabel\n"
            "3.  Klicke auf das iPod-Symbol oben links\n"
            "4.  Unter 'Übersicht' → aktiviere:\n"
            "     ☑ 'Musik und Videos manuell verwalten'\n"
            "5.  Ziehe deine MP3-Dateien aus dem\n"
            "     Datei-Explorer direkt auf den iPod in iTunes\n"
            "6.  Fertig! Alben erscheinen korrekt in Cover Flow ✅\n\n\n"
            "━━━  MIT COPYTRANS MANAGER (kostenlos)  ━━━\n\n"
            "1.  Lade CopyTrans Manager herunter:\n"
            "     → copytrans.net/copytransmanager\n"
            "2.  Installiere und starte es\n"
            "3.  Verbinde den iPod per USB\n"
            "4.  Ziehe MP3-Dateien per Drag & Drop rein\n"
            "5.  Klicke auf 'Update' → fertig! ✅"
        )

        textbox = ctk.CTkTextbox(
            self, font=ctk.CTkFont(size=13),
            fg_color=COLORS['track_bg'],
            corner_radius=10, wrap="word"
        )
        textbox.pack(fill="both", expand=True, padx=20, pady=(0, 15))
        textbox.insert("0.0", text_content)
        textbox.configure(state="disabled")

        ctk.CTkButton(
            self, text="Verstanden!", width=160,
            fg_color=COLORS['accent_blue'],
            command=self.destroy
        ).pack(pady=(0, 20))


# ──────────────────────────────────────────────
# Main Application
# ──────────────────────────────────────────────
class App(ctk.CTk):
    """iPod Sync Tool main window."""

    def __init__(self):
        super().__init__()

        self.title("iPod Sync Tool")
        self.geometry("1020x760")
        self.minsize(820, 600)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Center on screen
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"1020x760+{(sw - 1020) // 2}+{(sh - 760) // 2}")

        # State
        self.tracks = []
        self.issues = []
        self.album_groups = {}
        self._expanded_albums = set()

        self._build_ui()

    # ── UI Construction ──────────────────────

    def _build_ui(self):
        self.configure(fg_color=COLORS['bg_dark'])

        # ── Header ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=30, pady=(25, 5))

        ctk.CTkLabel(
            header, text="🎵  iPod Sync Tool",
            font=ctk.CTkFont(size=28, weight="bold"),
        ).pack(side="left")

        ctk.CTkButton(
            header, text="📱 Transfer-Anleitung", width=170,
            fg_color="transparent", border_width=1,
            border_color=COLORS['accent_blue'],
            text_color=COLORS['accent_blue'],
            hover_color="#1a2744",
            command=self._show_transfer_info
        ).pack(side="right")

        ctk.CTkLabel(
            self,
            text="Metadaten fixen  •  Album-Splitting reparieren  •  Cover Flow aufräumen",
            font=ctk.CTkFont(size=13),
            text_color=COLORS['text_secondary'],
        ).pack(padx=30, anchor="w")

        # ── Folder Selection ──
        folder_frame = ctk.CTkFrame(self, fg_color=COLORS['card_ok'], corner_radius=12)
        folder_frame.pack(fill="x", padx=30, pady=(15, 5))

        inner = ctk.CTkFrame(folder_frame, fg_color="transparent")
        inner.pack(fill="x", padx=15, pady=12)

        ctk.CTkLabel(
            inner, text="📂", font=ctk.CTkFont(size=20)
        ).pack(side="left", padx=(0, 8))

        self.folder_entry = ctk.CTkEntry(
            inner, placeholder_text="Ordner mit MP3-Dateien auswählen...",
            height=38, font=ctk.CTkFont(size=13),
        )
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(
            inner, text="Durchsuchen", width=120, height=38,
            fg_color="transparent", border_width=1,
            border_color=COLORS['text_secondary'],
            command=self._browse_folder
        ).pack(side="left", padx=(0, 8))

        self.scan_btn = ctk.CTkButton(
            inner, text="🔍 Scannen", width=130, height=38,
            fg_color=COLORS['accent_blue'],
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._scan
        )
        self.scan_btn.pack(side="left")

        # ── Status Bar ──
        self.status_label = ctk.CTkLabel(
            self,
            text="Wähle einen Ordner mit deinen MP3-Dateien und klicke 'Scannen'",
            font=ctk.CTkFont(size=13),
            text_color=COLORS['text_secondary'],
        )
        self.status_label.pack(fill="x", padx=30, pady=(8, 3))

        # ── Main Content (scrollable) ──
        self.content = ctk.CTkScrollableFrame(
            self, fg_color=COLORS['bg_dark'],
            corner_radius=0,
        )
        self.content.pack(fill="both", expand=True, padx=25, pady=(5, 5))

        # Welcome placeholder
        self._show_welcome()

        # ── Bottom Bar ──
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=30, pady=(5, 20))

        self.fix_all_btn = ctk.CTkButton(
            bottom,
            text="🔧  Alle Probleme automatisch fixen",
            height=46,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=COLORS['accent_red'],
            hover_color=COLORS['accent_red_hover'],
            state="disabled",
            command=self._fix_all,
        )
        self.fix_all_btn.pack(fill="x")

    def _show_welcome(self):
        """Show welcome placeholder in content area."""
        for w in self.content.winfo_children():
            w.destroy()

        welcome_frame = ctk.CTkFrame(self.content, fg_color="transparent")
        welcome_frame.pack(expand=True, fill="both", pady=80)

        ctk.CTkLabel(
            welcome_frame, text="🎵",
            font=ctk.CTkFont(size=60),
        ).pack(pady=(0, 15))

        ctk.CTkLabel(
            welcome_frame,
            text="Wähle einen Ordner mit deinen MP3-Dateien",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack()

        ctk.CTkLabel(
            welcome_frame,
            text=(
                "Die App erkennt automatisch Alben, die durch\n"
                "\"feat.\" / \"ft.\" im Artist-Tag aufgespalten werden,\n"
                "und fixt sie mit einem Klick."
            ),
            font=ctk.CTkFont(size=14),
            text_color=COLORS['text_secondary'],
            justify="center",
        ).pack(pady=(8, 0))

    # ── Actions ──────────────────────────────

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Ordner mit MP3-Dateien auswählen")
        if folder:
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, folder)

    def _scan(self):
        folder = self.folder_entry.get().strip()
        if not folder:
            messagebox.showwarning("Kein Ordner", "Bitte wähle zuerst einen Ordner aus.")
            return
        if not os.path.isdir(folder):
            messagebox.showerror("Fehler", f"Ordner nicht gefunden:\n{folder}")
            return

        self.scan_btn.configure(state="disabled", text="⏳ Scanne...")
        self.status_label.configure(text="⏳ Scanne Ordner nach MP3-Dateien...")
        self._expanded_albums.clear()

        def do_scan():
            tracks = engine.scan_directory(folder)
            issues = engine.detect_album_issues(tracks)
            self.after(0, lambda: self._on_scan_complete(tracks, issues))

        threading.Thread(target=do_scan, daemon=True).start()

    def _on_scan_complete(self, tracks, issues):
        self.tracks = tracks
        self.issues = issues
        self.album_groups = engine.group_by_album(tracks)

        self.scan_btn.configure(state="normal", text="🔍 Scannen")

        n_tracks = len(self.tracks)
        n_albums = len(self.album_groups)
        n_issues = len(self.issues)

        if n_tracks == 0:
            self.status_label.configure(
                text="❌ Keine MP3-Dateien gefunden. Versuche einen anderen Ordner.",
                text_color=COLORS['text_warning'],
            )
            self._show_welcome()
            return

        if n_issues > 0:
            status = (
                f"✅ {n_tracks} Songs in {n_albums} Alben  •  "
                f"⚠️ {n_issues} Album{'e' if n_issues != 1 else ''} mit Problemen"
            )
            self.status_label.configure(text=status, text_color=COLORS['text_warning'])
            self.fix_all_btn.configure(state="normal")
        else:
            status = (
                f"✅ {n_tracks} Songs in {n_albums} Alben  •  "
                f"✅ Keine Probleme erkannt!"
            )
            self.status_label.configure(text=status, text_color=COLORS['accent_green'])
            self.fix_all_btn.configure(state="disabled")

        self._render_albums()

    def _render_albums(self):
        """Render all album cards in the scrollable content area."""
        for w in self.content.winfo_children():
            w.destroy()

        issue_keys = {i['album'].strip().lower() for i in self.issues}
        issue_map = {i['album'].strip().lower(): i for i in self.issues}

        sorted_keys = sorted(
            self.album_groups.keys(),
            key=lambda k: (0 if k in issue_keys else 1, k)
        )

        # Section: Issues
        if self.issues:
            ctk.CTkLabel(
                self.content,
                text=f"⚠️  {len(self.issues)} Album{'e' if len(self.issues) != 1 else ''} mit Problemen",
                font=ctk.CTkFont(size=18, weight="bold"),
                text_color=COLORS['text_warning'],
                anchor="w",
            ).pack(fill="x", padx=5, pady=(10, 8))

        ok_header_shown = False

        for key in sorted_keys:
            data = self.album_groups[key]
            issue = issue_map.get(key)

            # Section header for OK albums
            if key not in issue_keys and not ok_header_shown:
                ok_header_shown = True

                sep = ctk.CTkFrame(self.content, height=1, fg_color=COLORS['divider'])
                sep.pack(fill="x", padx=5, pady=(20, 5))

                ctk.CTkLabel(
                    self.content,
                    text=f"✅  Alben ohne Probleme ({len(self.album_groups) - len(self.issues)})",
                    font=ctk.CTkFont(size=18, weight="bold"),
                    text_color=COLORS['accent_green'],
                    anchor="w",
                ).pack(fill="x", padx=5, pady=(8, 8))

            self._create_album_card(data['name'], data['tracks'], issue)

    def _create_album_card(self, album_name, tracks, issue=None):
        """Create a single album card widget."""
        has_issue = issue is not None
        bg = COLORS['card_issue'] if has_issue else COLORS['card_ok']

        card = ctk.CTkFrame(self.content, fg_color=bg, corner_radius=12)
        card.pack(fill="x", pady=4, padx=2)

        # ── Header Row ──
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(12, 4))

        # Album artwork (from first track)
        artwork_img = self._load_artwork(tracks[0]['filepath'])
        if artwork_img:
            ctk.CTkLabel(
                header, image=artwork_img, text="",
                width=52, height=52,
            ).pack(side="left", padx=(0, 12))

        # Album info
        info = ctk.CTkFrame(header, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)

        badge = "⚠️  " if has_issue else "✅  "
        ctk.CTkLabel(
            info, text=f"{badge}{album_name}",
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        ).pack(fill="x")

        # Artist line
        artists = sorted(set(t['artist'] for t in tracks if t['artist']))
        main_artist = artists[0] if len(artists) == 1 else (
            issue['main_artist'] if issue else ", ".join(artists[:2]) + (
                f" +{len(artists) - 2}" if len(artists) > 2 else ""
            )
        )
        ctk.CTkLabel(
            info, text=main_artist,
            font=ctk.CTkFont(size=12),
            text_color=COLORS['text_secondary'],
            anchor="w",
        ).pack(fill="x")

        # Track count badge
        ctk.CTkLabel(
            header, text=f"{len(tracks)} Songs",
            font=ctk.CTkFont(size=12),
            text_color=COLORS['text_secondary'],
        ).pack(side="right", padx=(10, 0))

        # ── Issue Description ──
        if has_issue:
            issue_frame = ctk.CTkFrame(card, fg_color="transparent")
            issue_frame.pack(fill="x", padx=15, pady=(2, 4))

            variants_text = ", ".join(f'"{v}"' for v in issue['artist_variants'])
            ctk.CTkLabel(
                issue_frame,
                text=f"Problem: {len(issue['artist_variants'])} verschiedene Artist-Einträge: {variants_text}",
                font=ctk.CTkFont(size=12),
                text_color=COLORS['text_warning'],
                wraplength=800, anchor="w", justify="left",
            ).pack(fill="x")

            ctk.CTkLabel(
                issue_frame,
                text=f"→ Empfehlung: Album Artist auf \"{issue['main_artist']}\" setzen",
                font=ctk.CTkFont(size=12),
                text_color=COLORS['text_suggestion'],
                anchor="w",
            ).pack(fill="x", pady=(2, 0))

        # ── Action Row ──
        action = ctk.CTkFrame(card, fg_color="transparent")
        action.pack(fill="x", padx=15, pady=(4, 4))

        if has_issue:
            ctk.CTkButton(
                action,
                text=f"🔧 Album Artist → \"{issue['main_artist']}\"",
                fg_color=COLORS['accent_orange'],
                hover_color=COLORS['accent_orange_hover'],
                height=32, font=ctk.CTkFont(size=12, weight="bold"),
                command=lambda i=issue: self._fix_single(i),
            ).pack(side="left", padx=(0, 8))

        # Toggle tracks button
        tracks_frame = ctk.CTkFrame(card, fg_color=COLORS['track_bg'], corner_radius=8)
        album_key = album_name.lower()

        toggle_btn = ctk.CTkButton(
            action,
            text=f"▶  {len(tracks)} Songs anzeigen",
            fg_color="transparent",
            border_width=1, border_color=COLORS['divider'],
            text_color=COLORS['text_secondary'],
            hover_color=COLORS['card_hover_ok'] if not has_issue else COLORS['card_hover_issue'],
            height=32, font=ctk.CTkFont(size=12),
            width=180,
            command=lambda tf=tracks_frame, ak=album_key, t=tracks: self._toggle_tracks(tf, ak, t),
        )
        toggle_btn.pack(side="left")

        # Pre-populate tracks frame (hidden by default)
        self._populate_tracks_frame(tracks_frame, tracks)

        # Show if was previously expanded
        if album_key in self._expanded_albums:
            tracks_frame.pack(fill="x", padx=15, pady=(0, 10))
            toggle_btn.configure(text=f"▼  Songs ausblenden")

    def _populate_tracks_frame(self, frame, tracks):
        """Fill the tracks frame with track rows."""
        # Sort by track number
        def sort_key(t):
            try:
                num = t['track_num'].split('/')[0]
                return int(num)
            except (ValueError, IndexError):
                return 999

        sorted_tracks = sorted(tracks, key=sort_key)

        for i, track in enumerate(sorted_tracks):
            row_bg = COLORS['track_bg'] if i % 2 == 0 else COLORS['track_alt']
            row = ctk.CTkFrame(frame, fg_color=row_bg, height=32, corner_radius=0)
            row.pack(fill="x", padx=8, pady=1)
            row.pack_propagate(False)

            # Track number
            num_text = track['track_num'].split('/')[0] if track['track_num'] else str(i + 1)
            ctk.CTkLabel(
                row, text=num_text.rjust(2),
                font=ctk.CTkFont(size=12, family="Consolas"),
                text_color=COLORS['text_secondary'],
                width=30,
            ).pack(side="left", padx=(8, 5))

            # Title
            ctk.CTkLabel(
                row, text=track['title'],
                font=ctk.CTkFont(size=12),
                anchor="w",
            ).pack(side="left", fill="x", expand=True, padx=(0, 10))

            # Artist
            ctk.CTkLabel(
                row, text=track['artist'],
                font=ctk.CTkFont(size=11),
                text_color=COLORS['text_secondary'],
                anchor="w", width=200,
            ).pack(side="left", padx=(0, 10))

            # Duration
            ctk.CTkLabel(
                row, text=engine.format_duration(track['duration']),
                font=ctk.CTkFont(size=11, family="Consolas"),
                text_color=COLORS['text_secondary'],
                width=45,
            ).pack(side="left", padx=(0, 5))

            # Edit button
            ctk.CTkButton(
                row, text="✏️", width=30, height=24,
                fg_color="transparent",
                hover_color=COLORS['card_hover_ok'],
                font=ctk.CTkFont(size=12),
                command=lambda t=track: self._edit_track(t),
            ).pack(side="right", padx=(0, 5))

    def _toggle_tracks(self, tracks_frame, album_key, tracks):
        """Toggle visibility of track list."""
        if tracks_frame.winfo_ismapped():
            tracks_frame.pack_forget()
            self._expanded_albums.discard(album_key)
            # Update button text - find the toggle button
            self._update_toggle_btn(tracks_frame, f"▶  {len(tracks)} Songs anzeigen")
        else:
            tracks_frame.pack(fill="x", padx=15, pady=(0, 10))
            self._expanded_albums.add(album_key)
            self._update_toggle_btn(tracks_frame, f"▼  Songs ausblenden")

    def _update_toggle_btn(self, tracks_frame, text):
        """Find and update the toggle button associated with a tracks frame."""
        card = tracks_frame.master
        for child in card.winfo_children():
            if isinstance(child, ctk.CTkFrame):
                for btn in child.winfo_children():
                    if isinstance(btn, ctk.CTkButton) and ("Songs" in str(btn.cget("text"))):
                        btn.configure(text=text)
                        return

    def _load_artwork(self, filepath, size=(52, 52)):
        """Load embedded artwork from an MP3 file as CTkImage."""
        if not HAS_PIL:
            return None
        try:
            data, mime = engine.get_artwork_bytes(filepath)
            if data:
                img = Image.open(io.BytesIO(data))
                img = img.resize(size, Image.LANCZOS)
                return ctk.CTkImage(img, size=size)
        except Exception:
            pass
        return None

    # ── Fix Operations ───────────────────────

    def _fix_single(self, issue):
        """Fix Album Artist for a single album."""
        fixed, errors = engine.fix_album_artist(issue['tracks'], issue['main_artist'])

        if errors:
            messagebox.showwarning(
                "Teilweise gefixt",
                f"{fixed} Songs gefixt, {len(errors)} Fehler:\n" +
                "\n".join(errors[:5])
            )
        else:
            messagebox.showinfo(
                "Gefixt! ✅",
                f"{fixed} Songs gefixt!\n"
                f"Album Artist = \"{issue['main_artist']}\"\n\n"
                f"Das Album \"{issue['album']}\" wird jetzt\n"
                f"korrekt in Cover Flow angezeigt."
            )

        # Re-scan to update UI
        self._scan()

    def _fix_all(self):
        """Fix Album Artist for all detected issues."""
        if not self.issues:
            return

        n_issues = len(self.issues)
        confirm = messagebox.askyesno(
            "Alle Probleme fixen?",
            f"{n_issues} Album{'e' if n_issues != 1 else ''} mit Problemen gefunden.\n\n"
            f"Soll der Album Artist bei allen automatisch\n"
            f"auf den Hauptkünstler gesetzt werden?\n\n"
            f"(Die originalen Artist-Tags bleiben erhalten)"
        )

        if not confirm:
            return

        total_fixed = 0
        total_errors = []

        for issue in self.issues:
            fixed, errors = engine.fix_album_artist(issue['tracks'], issue['main_artist'])
            total_fixed += fixed
            total_errors.extend(errors)

        if total_errors:
            messagebox.showwarning(
                "Teilweise gefixt",
                f"{total_fixed} Songs gefixt, {len(total_errors)} Fehler.\n"
                f"Erste Fehler:\n" + "\n".join(total_errors[:5])
            )
        else:
            messagebox.showinfo(
                "Alle Probleme gefixt! 🎉",
                f"✅ {total_fixed} Songs in {n_issues} Alben gefixt!\n\n"
                f"Alle Alben werden jetzt korrekt\n"
                f"in Cover Flow angezeigt.\n\n"
                f"Übertrage die Dateien jetzt auf deinen iPod\n"
                f"(iTunes → Manuell Verwalten → Drag & Drop)"
            )

        # Re-scan
        self._scan()

    def _edit_track(self, track):
        """Open the track editor dialog."""
        TrackEditor(self, track, on_save=self._scan)

    def _show_transfer_info(self):
        """Show transfer instructions dialog."""
        TransferInfoDialog(self)


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
