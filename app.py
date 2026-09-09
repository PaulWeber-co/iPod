"""
iPod Sync Tool - Desktop Application
Fixes MP3 metadata issues (album splitting) and transfers music to iPod.

Features:
- Scan folders for MP3 files
- Detect album splitting caused by feat./ft. in artist names
- One-click fix via Album Artist (TPE2) tag
- Transfer music directly to iPod via iTunes COM automation
- Manual metadata editor for individual tracks

Uses CustomTkinter for a modern dark-mode GUI.
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import os
import io
import threading
from collections import defaultdict

import metadata_engine as engine
import ipod_transfer

# Try importing Pillow for album artwork
try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


# ──────────────────────────────────────────────
# Color palette
# ──────────────────────────────────────────────
C = {
    'bg':               '#0f0f1a',
    'card_ok':          '#16213e',
    'card_issue':       '#2d1b1b',
    'card_hover_ok':    '#1a2744',
    'card_hover_issue': '#3a2222',
    'accent_blue':      '#4a9eff',
    'accent_orange':    '#e67e22',
    'accent_orange_h':  '#d35400',
    'accent_red':       '#e74c3c',
    'accent_red_h':     '#c0392b',
    'accent_green':     '#2ecc71',
    'accent_green_h':   '#27ae60',
    'text':             '#ffffff',
    'text2':            '#8892a0',
    'text_warn':        '#ff6b6b',
    'text_suggest':     '#ffd93d',
    'track_bg':         '#111827',
    'track_alt':        '#151d2e',
    'divider':          '#2a2a3e',
    'ipod_bg':          '#1a1a2e',
    'ipod_ok':          '#0d3320',
    'ipod_fail':        '#331a1a',
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

        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - 520) // 2
        y = master.winfo_rooty() + (master.winfo_height() - 480) // 2
        self.geometry(f"+{x}+{y}")
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="🎵 Song bearbeiten",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(padx=20, pady=(20, 15))

        fields = [
            ("Titel",        "title"),
            ("Artist",       "artist"),
            ("Album",        "album"),
            ("Album Artist", "album_artist"),
            ("Track #",      "track_num"),
            ("Genre",        "genre"),
        ]

        self.entries = {}
        for label_text, key in fields:
            row = ctk.CTkFrame(self, fg_color="transparent")
            row.pack(fill="x", padx=25, pady=4)
            ctk.CTkLabel(row, text=label_text, width=110, anchor="w",
                         font=ctk.CTkFont(size=13)).pack(side="left")
            entry = ctk.CTkEntry(row, font=ctk.CTkFont(size=13), height=34)
            entry.pack(side="left", fill="x", expand=True)
            entry.insert(0, self.track.get(key, ''))
            self.entries[key] = entry

        ctk.CTkLabel(self, text=f"📁 {self.track['filename']}",
                     font=ctk.CTkFont(size=11), text_color=C['text2']
                     ).pack(padx=25, pady=(15, 5), anchor="w")

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=25, pady=(10, 20))
        ctk.CTkButton(btn_frame, text="Abbrechen", width=120, fg_color="transparent",
                      border_width=1, border_color=C['text2'],
                      command=self.destroy).pack(side="right", padx=(10, 0))
        ctk.CTkButton(btn_frame, text="💾 Speichern", width=140,
                      fg_color=C['accent_blue'], command=self._save).pack(side="right")

    def _save(self):
        vals = {k: e.get() for k, e in self.entries.items()}
        ok, err = engine.update_track(self.track['filepath'], **vals)
        if ok:
            for k, v in vals.items():
                self.track[k] = v
            if self.on_save:
                self.on_save()
            self.destroy()
        else:
            messagebox.showerror("Fehler", f"Speichern fehlgeschlagen:\n{err}")


# ──────────────────────────────────────────────
# Main Application
# ──────────────────────────────────────────────
class App(ctk.CTk):
    """iPod Sync Tool main window."""

    def __init__(self):
        super().__init__()
        self.title("iPod Sync Tool")
        self.geometry("1020x800")
        self.minsize(820, 640)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Center on screen
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"1020x800+{(sw - 1020) // 2}+{(sh - 800) // 2}")

        # State
        self.tracks = []
        self.issues = []
        self.album_groups = {}
        self._expanded_albums = set()
        self._ipod_status = None  # result of check_connection()

        self._build_ui()

        # Auto-check iPod on startup (slight delay to let window appear)
        self.after(500, self._check_ipod)

    # ── UI Construction ──────────────────────

    def _build_ui(self):
        self.configure(fg_color=C['bg'])

        # ── Header ──
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=30, pady=(20, 2))

        ctk.CTkLabel(header, text="🎵  iPod Sync Tool",
                     font=ctk.CTkFont(size=28, weight="bold")).pack(side="left")

        ctk.CTkLabel(self, text="Metadaten fixen  •  Album-Splitting reparieren  •  Auf iPod übertragen",
                     font=ctk.CTkFont(size=13), text_color=C['text2']).pack(padx=30, anchor="w")

        # ── iPod Status Bar ──
        self.ipod_frame = ctk.CTkFrame(self, fg_color=C['ipod_fail'], corner_radius=10)
        self.ipod_frame.pack(fill="x", padx=30, pady=(10, 5))

        ipod_inner = ctk.CTkFrame(self.ipod_frame, fg_color="transparent")
        ipod_inner.pack(fill="x", padx=15, pady=10)

        self.ipod_icon = ctk.CTkLabel(ipod_inner, text="📱", font=ctk.CTkFont(size=22))
        self.ipod_icon.pack(side="left", padx=(0, 10))

        self.ipod_label = ctk.CTkLabel(
            ipod_inner, text="iPod: Prüfe Verbindung...",
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w", justify="left",
        )
        self.ipod_label.pack(side="left", fill="x", expand=True)

        self.ipod_check_btn = ctk.CTkButton(
            ipod_inner, text="🔄 Verbindung prüfen", width=170, height=32,
            fg_color="transparent", border_width=1, border_color=C['text2'],
            text_color=C['text2'], font=ctk.CTkFont(size=12),
            command=self._check_ipod,
        )
        self.ipod_check_btn.pack(side="right")

        # ── Folder Selection ──
        folder_frame = ctk.CTkFrame(self, fg_color=C['card_ok'], corner_radius=12)
        folder_frame.pack(fill="x", padx=30, pady=(8, 5))

        inner = ctk.CTkFrame(folder_frame, fg_color="transparent")
        inner.pack(fill="x", padx=15, pady=12)

        ctk.CTkLabel(inner, text="📂", font=ctk.CTkFont(size=20)).pack(side="left", padx=(0, 8))

        self.folder_entry = ctk.CTkEntry(
            inner, placeholder_text="Ordner mit MP3-Dateien auswählen...",
            height=38, font=ctk.CTkFont(size=13),
        )
        self.folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        ctk.CTkButton(inner, text="Durchsuchen", width=120, height=38,
                      fg_color="transparent", border_width=1, border_color=C['text2'],
                      command=self._browse_folder).pack(side="left", padx=(0, 8))

        self.scan_btn = ctk.CTkButton(
            inner, text="🔍 Scannen", width=130, height=38,
            fg_color=C['accent_blue'], font=ctk.CTkFont(size=14, weight="bold"),
            command=self._scan,
        )
        self.scan_btn.pack(side="left")

        # ── Status Bar ──
        self.status_label = ctk.CTkLabel(
            self, text="Wähle einen Ordner mit deinen MP3-Dateien und klicke 'Scannen'",
            font=ctk.CTkFont(size=13), text_color=C['text2'],
        )
        self.status_label.pack(fill="x", padx=30, pady=(6, 3))

        # ── Main Content (scrollable) ──
        self.content = ctk.CTkScrollableFrame(self, fg_color=C['bg'], corner_radius=0)
        self.content.pack(fill="both", expand=True, padx=25, pady=(3, 3))
        self._show_welcome()

        # ── Bottom Bar ──
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=30, pady=(5, 8))

        # Row 1: Fix All + Transfer
        btn_row = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 5))

        self.fix_all_btn = ctk.CTkButton(
            btn_row, text="🔧  Alle Metadaten-Probleme fixen",
            height=42, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=C['accent_orange'], hover_color=C['accent_orange_h'],
            state="disabled", command=self._fix_all,
        )
        self.fix_all_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.transfer_btn = ctk.CTkButton(
            btn_row, text="📱  Auf iPod übertragen",
            height=42, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=C['accent_green'], hover_color=C['accent_green_h'],
            state="disabled", command=self._start_transfer,
        )
        self.transfer_btn.pack(side="right", fill="x", expand=True, padx=(6, 0))

        # Row 2: Progress bar (hidden by default)
        self.progress_frame = ctk.CTkFrame(bottom, fg_color="transparent")

        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=8, corner_radius=4)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(0, 4))

        self.progress_label = ctk.CTkLabel(
            self.progress_frame, text="", font=ctk.CTkFont(size=12),
            text_color=C['text2'], anchor="w",
        )
        self.progress_label.pack(fill="x")

    def _show_welcome(self):
        for w in self.content.winfo_children():
            w.destroy()
        f = ctk.CTkFrame(self.content, fg_color="transparent")
        f.pack(expand=True, fill="both", pady=60)
        ctk.CTkLabel(f, text="🎵", font=ctk.CTkFont(size=60)).pack(pady=(0, 12))
        ctk.CTkLabel(f, text="Wähle einen Ordner mit deinen MP3-Dateien",
                     font=ctk.CTkFont(size=18, weight="bold")).pack()
        ctk.CTkLabel(f, text=(
            "Die App erkennt automatisch Alben, die durch\n"
            "\"feat.\" / \"ft.\" im Artist-Tag aufgespalten werden,\n"
            "und fixt sie mit einem Klick.\n\n"
            "Danach kannst du die Musik direkt auf deinen\n"
            "iPod übertragen – alles in dieser App."
        ), font=ctk.CTkFont(size=14), text_color=C['text2'], justify="center").pack(pady=(8, 0))

    # ── iPod Connection ──────────────────────

    def _check_ipod(self):
        self.ipod_label.configure(text="⏳ Prüfe Verbindung...")
        self.ipod_check_btn.configure(state="disabled")
        self.update_idletasks()

        def do_check():
            result = ipod_transfer.check_connection()
            self.after(0, lambda: self._on_ipod_checked(result))

        threading.Thread(target=do_check, daemon=True).start()

    def _on_ipod_checked(self, result):
        self._ipod_status = result
        self.ipod_check_btn.configure(state="normal")

        status = result.get('status', 'unknown')

        if status == 'ok':
            self.ipod_frame.configure(fg_color=C['ipod_ok'])
            self.ipod_label.configure(
                text=f"✅ {result['message']}",
                text_color=C['accent_green'],
            )
            self._update_transfer_btn()
        elif status == 'no_itunes':
            self.ipod_frame.configure(fg_color=C['ipod_fail'])
            self.ipod_label.configure(
                text=f"❌ {result['message']}",
                text_color=C['text_warn'],
            )
            self.transfer_btn.configure(state="disabled")
        elif status == 'no_ipod':
            self.ipod_frame.configure(fg_color=C['ipod_fail'])
            self.ipod_label.configure(
                text=f"🔌 {result['message']}",
                text_color=C['text_suggest'],
            )
            self.transfer_btn.configure(state="disabled")
        else:
            self.ipod_frame.configure(fg_color=C['ipod_fail'])
            self.ipod_label.configure(
                text=f"⚠️ {result['message']}",
                text_color=C['text_warn'],
            )
            self.transfer_btn.configure(state="disabled")

    def _update_transfer_btn(self):
        """Enable/disable transfer button based on state."""
        has_tracks = len(self.tracks) > 0
        ipod_ok = self._ipod_status and self._ipod_status.get('status') == 'ok'

        if has_tracks and ipod_ok:
            self.transfer_btn.configure(
                state="normal",
                text=f"📱  Auf iPod übertragen ({len(self.tracks)} Songs)",
            )
        elif has_tracks and not ipod_ok:
            self.transfer_btn.configure(
                state="disabled",
                text="📱  iPod nicht verbunden",
            )
        else:
            self.transfer_btn.configure(
                state="disabled",
                text="📱  Auf iPod übertragen",
            )

    # ── Folder Scanning ──────────────────────

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
        self.status_label.configure(text="⏳ Scanne Ordner nach MP3-Dateien...", text_color=C['text2'])
        self._expanded_albums.clear()

        def do_scan():
            tracks = engine.scan_directory(folder)
            issues = engine.detect_album_issues(tracks)
            self.after(0, lambda: self._on_scan_done(tracks, issues))

        threading.Thread(target=do_scan, daemon=True).start()

    def _on_scan_done(self, tracks, issues):
        self.tracks = tracks
        self.issues = issues
        self.album_groups = engine.group_by_album(tracks)
        self.scan_btn.configure(state="normal", text="🔍 Scannen")

        n = len(self.tracks)
        a = len(self.album_groups)
        p = len(self.issues)

        if n == 0:
            self.status_label.configure(
                text="❌ Keine MP3-Dateien gefunden. Versuche einen anderen Ordner.",
                text_color=C['text_warn'],
            )
            self._show_welcome()
            self.fix_all_btn.configure(state="disabled")
            self._update_transfer_btn()
            return

        if p > 0:
            self.status_label.configure(
                text=f"✅ {n} Songs in {a} Alben  •  ⚠️ {p} Album{'e' if p != 1 else ''} mit Problemen",
                text_color=C['text_suggest'],
            )
            self.fix_all_btn.configure(state="normal")
        else:
            self.status_label.configure(
                text=f"✅ {n} Songs in {a} Alben  •  ✅ Keine Probleme erkannt!",
                text_color=C['accent_green'],
            )
            self.fix_all_btn.configure(state="disabled")

        self._update_transfer_btn()
        self._render_albums()

    # ── Album Rendering ──────────────────────

    def _render_albums(self):
        for w in self.content.winfo_children():
            w.destroy()

        issue_keys = {i['album'].strip().lower() for i in self.issues}
        issue_map = {i['album'].strip().lower(): i for i in self.issues}

        sorted_keys = sorted(
            self.album_groups.keys(),
            key=lambda k: (0 if k in issue_keys else 1, k),
        )

        # Section header: issues
        if self.issues:
            ctk.CTkLabel(
                self.content,
                text=f"⚠️  {len(self.issues)} Album{'e' if len(self.issues) != 1 else ''} mit Problemen",
                font=ctk.CTkFont(size=18, weight="bold"),
                text_color=C['text_warn'], anchor="w",
            ).pack(fill="x", padx=5, pady=(10, 8))

        ok_header_shown = False

        for key in sorted_keys:
            data = self.album_groups[key]
            issue = issue_map.get(key)

            if key not in issue_keys and not ok_header_shown:
                ok_header_shown = True
                ctk.CTkFrame(self.content, height=1, fg_color=C['divider']).pack(fill="x", padx=5, pady=(20, 5))
                ctk.CTkLabel(
                    self.content,
                    text=f"✅  Alben ohne Probleme ({len(self.album_groups) - len(self.issues)})",
                    font=ctk.CTkFont(size=18, weight="bold"),
                    text_color=C['accent_green'], anchor="w",
                ).pack(fill="x", padx=5, pady=(8, 8))

            self._create_album_card(data['name'], data['tracks'], issue)

    def _create_album_card(self, album_name, tracks, issue=None):
        has_issue = issue is not None
        bg = C['card_issue'] if has_issue else C['card_ok']

        card = ctk.CTkFrame(self.content, fg_color=bg, corner_radius=12)
        card.pack(fill="x", pady=4, padx=2)

        # ── Header ──
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=15, pady=(12, 4))

        # Album artwork
        art = self._load_artwork(tracks[0]['filepath'])
        if art:
            ctk.CTkLabel(hdr, image=art, text="", width=52, height=52
                         ).pack(side="left", padx=(0, 12))

        info = ctk.CTkFrame(hdr, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)

        badge = "⚠️  " if has_issue else "✅  "
        ctk.CTkLabel(info, text=f"{badge}{album_name}",
                     font=ctk.CTkFont(size=15, weight="bold"), anchor="w").pack(fill="x")

        artists = sorted(set(t['artist'] for t in tracks if t['artist']))
        main = (artists[0] if len(artists) == 1 else
                issue['main_artist'] if issue else
                ", ".join(artists[:2]) + (f" +{len(artists) - 2}" if len(artists) > 2 else ""))
        ctk.CTkLabel(info, text=main, font=ctk.CTkFont(size=12),
                     text_color=C['text2'], anchor="w").pack(fill="x")

        ctk.CTkLabel(hdr, text=f"{len(tracks)} Songs", font=ctk.CTkFont(size=12),
                     text_color=C['text2']).pack(side="right", padx=(10, 0))

        # ── Issue description ──
        if has_issue:
            iss_f = ctk.CTkFrame(card, fg_color="transparent")
            iss_f.pack(fill="x", padx=15, pady=(2, 4))

            variants = ", ".join(f'"{v}"' for v in issue['artist_variants'])
            ctk.CTkLabel(iss_f,
                         text=f"Problem: {len(issue['artist_variants'])} verschiedene Artist-Einträge: {variants}",
                         font=ctk.CTkFont(size=12), text_color=C['text_warn'],
                         wraplength=800, anchor="w", justify="left").pack(fill="x")
            ctk.CTkLabel(iss_f,
                         text=f"→ Empfehlung: Album Artist auf \"{issue['main_artist']}\" setzen",
                         font=ctk.CTkFont(size=12), text_color=C['text_suggest'],
                         anchor="w").pack(fill="x", pady=(2, 0))

        # ── Action row ──
        act = ctk.CTkFrame(card, fg_color="transparent")
        act.pack(fill="x", padx=15, pady=(4, 4))

        if has_issue:
            ctk.CTkButton(
                act, text=f"🔧 Album Artist → \"{issue['main_artist']}\"",
                fg_color=C['accent_orange'], hover_color=C['accent_orange_h'],
                height=32, font=ctk.CTkFont(size=12, weight="bold"),
                command=lambda i=issue: self._fix_single(i),
            ).pack(side="left", padx=(0, 8))

        # Toggle tracks
        tracks_frame = ctk.CTkFrame(card, fg_color=C['track_bg'], corner_radius=8)
        album_key = album_name.lower()

        toggle_btn = ctk.CTkButton(
            act, text=f"▶  {len(tracks)} Songs anzeigen",
            fg_color="transparent", border_width=1, border_color=C['divider'],
            text_color=C['text2'],
            hover_color=C['card_hover_ok'] if not has_issue else C['card_hover_issue'],
            height=32, font=ctk.CTkFont(size=12), width=180,
            command=lambda tf=tracks_frame, ak=album_key, t=tracks: self._toggle_tracks(tf, ak, t),
        )
        toggle_btn.pack(side="left")

        self._populate_tracks(tracks_frame, tracks)

        if album_key in self._expanded_albums:
            tracks_frame.pack(fill="x", padx=15, pady=(0, 10))
            toggle_btn.configure(text="▼  Songs ausblenden")

    def _populate_tracks(self, frame, tracks):
        def sort_key(t):
            try:
                return int(t['track_num'].split('/')[0])
            except (ValueError, IndexError, AttributeError):
                return 999

        for i, track in enumerate(sorted(tracks, key=sort_key)):
            bg = C['track_bg'] if i % 2 == 0 else C['track_alt']
            row = ctk.CTkFrame(frame, fg_color=bg, height=32, corner_radius=0)
            row.pack(fill="x", padx=8, pady=1)
            row.pack_propagate(False)

            num = track['track_num'].split('/')[0] if track['track_num'] else str(i + 1)
            ctk.CTkLabel(row, text=num.rjust(2), font=ctk.CTkFont(size=12, family="Consolas"),
                         text_color=C['text2'], width=30).pack(side="left", padx=(8, 5))

            ctk.CTkLabel(row, text=track['title'], font=ctk.CTkFont(size=12),
                         anchor="w").pack(side="left", fill="x", expand=True, padx=(0, 10))

            ctk.CTkLabel(row, text=track['artist'], font=ctk.CTkFont(size=11),
                         text_color=C['text2'], anchor="w", width=200
                         ).pack(side="left", padx=(0, 10))

            ctk.CTkLabel(row, text=engine.format_duration(track['duration']),
                         font=ctk.CTkFont(size=11, family="Consolas"),
                         text_color=C['text2'], width=45).pack(side="left", padx=(0, 5))

            ctk.CTkButton(row, text="✏️", width=30, height=24, fg_color="transparent",
                          hover_color=C['card_hover_ok'], font=ctk.CTkFont(size=12),
                          command=lambda t=track: self._edit_track(t)).pack(side="right", padx=(0, 5))

    def _toggle_tracks(self, tracks_frame, album_key, tracks):
        if tracks_frame.winfo_ismapped():
            tracks_frame.pack_forget()
            self._expanded_albums.discard(album_key)
            self._update_toggle_btn(tracks_frame, f"▶  {len(tracks)} Songs anzeigen")
        else:
            tracks_frame.pack(fill="x", padx=15, pady=(0, 10))
            self._expanded_albums.add(album_key)
            self._update_toggle_btn(tracks_frame, "▼  Songs ausblenden")

    def _update_toggle_btn(self, tracks_frame, text):
        card = tracks_frame.master
        for child in card.winfo_children():
            if isinstance(child, ctk.CTkFrame):
                for btn in child.winfo_children():
                    if isinstance(btn, ctk.CTkButton) and "Songs" in str(btn.cget("text")):
                        btn.configure(text=text)
                        return

    def _load_artwork(self, filepath, size=(52, 52)):
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

    # ── Metadata Fixing ──────────────────────

    def _fix_single(self, issue):
        fixed, errors = engine.fix_album_artist(issue['tracks'], issue['main_artist'])
        if errors:
            messagebox.showwarning("Teilweise gefixt",
                                   f"{fixed} Songs gefixt, {len(errors)} Fehler:\n" + "\n".join(errors[:5]))
        else:
            messagebox.showinfo("Gefixt!", f"{fixed} Songs gefixt!\nAlbum Artist = \"{issue['main_artist']}\"")
        self._scan()

    def _fix_all(self):
        if not self.issues:
            return
        n = len(self.issues)
        if not messagebox.askyesno(
            "Alle Probleme fixen?",
            f"{n} Album{'e' if n != 1 else ''} mit Problemen gefunden.\n\n"
            f"Album Artist bei allen automatisch auf den\n"
            f"Hauptkünstler setzen?\n\n"
            f"(Die originalen Artist-Tags bleiben erhalten)"
        ):
            return

        total_fixed, total_errors = 0, []
        for issue in self.issues:
            fixed, errors = engine.fix_album_artist(issue['tracks'], issue['main_artist'])
            total_fixed += fixed
            total_errors.extend(errors)

        if total_errors:
            messagebox.showwarning("Teilweise gefixt",
                                   f"{total_fixed} Songs gefixt, {len(total_errors)} Fehler.")
        else:
            messagebox.showinfo("Alle gefixt! 🎉",
                                f"{total_fixed} Songs in {n} Alben gefixt!\n\n"
                                f"Alben werden jetzt korrekt in Cover Flow angezeigt.")
        self._scan()

    # ── iPod Transfer ────────────────────────

    def _start_transfer(self):
        if not self.tracks:
            messagebox.showwarning("Keine Songs", "Bitte scanne zuerst einen Ordner.")
            return

        n = len(self.tracks)
        if not messagebox.askyesno(
            "Auf iPod übertragen?",
            f"{n} Songs auf den iPod übertragen?\n\n"
            f"iTunes wird im Hintergrund gestartet\n"
            f"und überträgt die Dateien automatisch."
        ):
            return

        # Show progress
        self.progress_frame.pack(fill="x", pady=(5, 0))
        self.progress_bar.set(0)
        self.progress_label.configure(text="⏳ Starte Übertragung...")

        self.transfer_btn.configure(state="disabled", text="⏳ Übertrage...")
        self.fix_all_btn.configure(state="disabled")
        self.scan_btn.configure(state="disabled")

        filepaths = [t['filepath'] for t in self.tracks]

        def do_transfer():
            result = ipod_transfer.transfer_files(
                filepaths,
                progress_callback=lambda cur, total, name:
                    self.after(0, self._on_transfer_progress, cur, total, name),
            )
            self.after(0, lambda: self._on_transfer_done(result))

        threading.Thread(target=do_transfer, daemon=True).start()

    def _on_transfer_progress(self, current, total, filename):
        progress = current / total if total > 0 else 0
        self.progress_bar.set(progress)
        self.progress_label.configure(
            text=f"📱 Übertrage: {filename}  ({current}/{total})"
        )

    def _on_transfer_done(self, result):
        self.scan_btn.configure(state="normal")
        self.fix_all_btn.configure(state="normal" if self.issues else "disabled")
        self._update_transfer_btn()

        transferred = result.get('transferred', 0)
        errors = result.get('errors', [])
        method = result.get('method', 'unknown')

        if errors and transferred == 0:
            # Complete failure
            self.progress_bar.set(0)
            self.progress_label.configure(
                text=f"❌ Übertragung fehlgeschlagen",
            )
            messagebox.showerror(
                "Fehler",
                "Übertragung fehlgeschlagen:\n\n" + "\n".join(errors[:10])
            )
        elif errors:
            # Partial success
            self.progress_bar.set(1)
            self.progress_label.configure(
                text=f"⚠️ {transferred} Songs übertragen, {len(errors)} Fehler",
            )
            msg = f"{transferred} Songs übertragen!\n{len(errors)} Fehler:\n\n"
            msg += "\n".join(errors[:5])
            if method == 'library':
                msg += (
                    "\n\n⚠️ Songs wurden zur iTunes-Mediathek hinzugefügt.\n"
                    "Stelle sicher, dass dein iPod auf 'Manuell\n"
                    "verwalten' eingestellt ist, damit sie\n"
                    "automatisch übertragen werden."
                )
            messagebox.showwarning("Teilweise übertragen", msg)
        else:
            # Full success
            self.progress_bar.set(1)

            if method == 'direct':
                self.progress_label.configure(
                    text=f"✅ {transferred} Songs erfolgreich auf iPod übertragen!",
                )
                messagebox.showinfo(
                    "Übertragung abgeschlossen! 🎉",
                    f"✅ {transferred} Songs erfolgreich auf den\n"
                    f"iPod übertragen!\n\n"
                    f"Die Musik ist jetzt auf deinem iPod."
                )
            else:
                self.progress_label.configure(
                    text=f"✅ {transferred} Songs zur iTunes-Mediathek hinzugefügt",
                )
                messagebox.showinfo(
                    "Songs hinzugefügt! ✅",
                    f"{transferred} Songs zur iTunes-Mediathek hinzugefügt!\n\n"
                    f"Falls der iPod nicht auf 'Manuell verwalten'\n"
                    f"eingestellt ist, synchronisiere ihn in iTunes,\n"
                    f"um die Songs zu übertragen."
                )

    # ── Track Editor ─────────────────────────

    def _edit_track(self, track):
        TrackEditor(self, track, on_save=self._scan)


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
