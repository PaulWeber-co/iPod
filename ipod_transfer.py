"""
iPod Transfer Module
Handles iPod detection and music transfer via iTunes COM automation.

How it works:
- Uses the Windows COM interface to control iTunes in the background
- iTunes handles all the heavy lifting: USB communication, database updates, file copying
- The user never needs to open or interact with iTunes directly
- iTunes must be installed (for USB drivers + database management)

Requires: pywin32, iTunes installed on Windows.
"""

import os
import sys
import time

IS_WINDOWS = sys.platform == 'win32'

# Try to import COM support
HAS_COM = False
if IS_WINDOWS:
    try:
        import win32com.client
        HAS_COM = True
    except ImportError:
        pass


def _get_itunes():
    """Get or start iTunes COM object. Raises Exception on failure."""
    if not HAS_COM:
        raise RuntimeError(
            "pywin32 ist nicht installiert.\n"
            "Installiere mit: pip install pywin32"
        )
    import win32com.client
    return win32com.client.Dispatch("iTunes.Application")


def check_connection():
    """
    Check the full connection chain: pywin32 -> iTunes -> iPod.

    Returns dict:
        status: 'ok' | 'no_pywin32' | 'no_itunes' | 'no_ipod'
        message: Human-readable status message
        ipod_name: Name of iPod (only if status='ok')
        capacity_gb: Total storage in GB (only if status='ok')
        free_gb: Free storage in GB (only if status='ok')
    """
    if not IS_WINDOWS:
        return {'status': 'no_pywin32', 'message': 'Nur unter Windows verfügbar.'}

    if not HAS_COM:
        return {
            'status': 'no_pywin32',
            'message': (
                'pywin32 fehlt.\n'
                'Installiere mit:\n'
                'pip install pywin32'
            ),
        }

    # Try to connect to iTunes
    try:
        itunes = _get_itunes()
    except Exception as e:
        return {
            'status': 'no_itunes',
            'message': (
                'iTunes nicht gefunden.\n'
                'Bitte installiere iTunes von\n'
                'apple.com/itunes\n'
                f'\nFehler: {e}'
            ),
        }

    # Minimize iTunes window (keep it in background)
    try:
        if itunes.BrowserWindow:
            itunes.BrowserWindow.Minimized = True
    except Exception:
        pass

    # Search for connected iPod/iOS device
    try:
        for i in range(1, itunes.Sources.Count + 1):
            src = itunes.Sources.Item(i)
            if src.Kind == 2:  # ITSourceKindIPod = 2
                result = {
                    'status': 'ok',
                    'ipod_name': src.Name,
                    'capacity_gb': 0,
                    'free_gb': 0,
                }

                try:
                    result['capacity_gb'] = round(src.Capacity / (1024 ** 3), 1)
                    result['free_gb'] = round(src.FreeSpace / (1024 ** 3), 1)
                except Exception:
                    pass

                result['message'] = (
                    f"'{src.Name}' verbunden\n"
                    f"{result['free_gb']} GB frei / {result['capacity_gb']} GB"
                )
                return result
    except Exception as e:
        return {
            'status': 'no_ipod',
            'message': f'Fehler beim Suchen: {e}',
        }

    return {
        'status': 'no_ipod',
        'message': 'Kein iPod gefunden.\nVerbinde deinen iPod per USB-Kabel.',
    }


def transfer_files(filepaths, progress_callback=None):
    """
    Transfer MP3 files to the connected iPod via iTunes.

    Strategy:
    1. Try adding files directly to the iPod's playlist (works for manually managed devices)
    2. If that fails, add to iTunes library (the user may need to sync manually)

    Args:
        filepaths: List of absolute file paths
        progress_callback: func(current, total, filename) called per file

    Returns dict:
        transferred: Number of files successfully transferred
        errors: List of error messages
        method: 'direct' | 'library' — how files were added
    """
    # Initialize COM for this thread
    import pythoncom
    pythoncom.CoInitialize()

    try:
        return _do_transfer(filepaths, progress_callback)
    finally:
        pythoncom.CoUninitialize()


def _do_transfer(filepaths, progress_callback):
    """Internal transfer logic (must be called with COM initialized)."""
    import win32com.client

    if not filepaths:
        return {'transferred': 0, 'errors': ['Keine Dateien ausgewählt.'], 'method': 'none'}

    itunes = win32com.client.Dispatch("iTunes.Application")

    # Minimize iTunes
    try:
        if itunes.BrowserWindow:
            itunes.BrowserWindow.Minimized = True
    except Exception:
        pass

    # Find iPod source
    ipod_source = None
    for i in range(1, itunes.Sources.Count + 1):
        src = itunes.Sources.Item(i)
        if src.Kind == 2:
            ipod_source = src
            break

    if not ipod_source:
        return {
            'transferred': 0,
            'errors': ['Kein iPod verbunden. Verbinde deinen iPod per USB.'],
            'method': 'none',
        }

    # Get iPod's main playlist (first one is the device library)
    ipod_playlist = None
    try:
        if ipod_source.Playlists.Count > 0:
            ipod_playlist = ipod_source.Playlists.Item(1)
    except Exception:
        pass

    transferred = 0
    errors = []
    method = 'direct'
    direct_failed = False

    for idx, fp in enumerate(filepaths):
        abs_path = os.path.abspath(fp)
        basename = os.path.basename(fp)

        if not os.path.isfile(abs_path):
            errors.append(f"Nicht gefunden: {basename}")
            continue

        try:
            # Strategy 1: Add directly to iPod playlist
            if ipod_playlist and not direct_failed:
                try:
                    op = ipod_playlist.AddFile(abs_path)
                    _wait_for_operation(op)
                    transferred += 1
                    if progress_callback:
                        progress_callback(idx + 1, len(filepaths), basename)
                    continue
                except Exception:
                    # Direct add failed — iPod might not be in manual mode
                    direct_failed = True
                    method = 'library'

            # Strategy 2: Add to iTunes library
            op = itunes.LibraryPlaylist.AddFile(abs_path)
            _wait_for_operation(op)
            transferred += 1
            if method != 'library':
                method = 'library'

            if progress_callback:
                progress_callback(idx + 1, len(filepaths), basename)

        except Exception as e:
            errors.append(f"{basename}: {e}")

    return {
        'transferred': transferred,
        'errors': errors,
        'method': method,
    }


def _wait_for_operation(op, timeout=60):
    """Wait for an iTunes operation to complete."""
    if op is None:
        return
    if not hasattr(op, 'InProgress'):
        return

    start = time.time()
    while op.InProgress and (time.time() - start) < timeout:
        time.sleep(0.15)
