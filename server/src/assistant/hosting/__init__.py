"""How a Kelo part (a "role") is started, kept apart and kept running, and every call that differs between operating systems.

Rules for this package (docs/native-server.md section 7): operating-system differences live here and nowhere else (no other module
checks `sys.platform`); every module uses only the standard library when it is imported (psutil and pywinpty are imported inside
the function that needs them), because the work room's agent runs from a plain Python with no packages installed.
"""
