"""
iPodFS - direkter USB-Zugriff auf iPod touch / iPhone ohne iTunes.

Die App redet ueber usbmux -> lockdownd -> AFC direkt mit dem Geraet,
raeumt vorher die ID3-Tags auf (damit Alben nicht mehr durch "feat."
zerfallen) und synchronisiert per Drag & Drop.
"""

__version__ = "2.0.0"
