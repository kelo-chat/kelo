"""Every call that differs between operating systems, in one place: folders (paths), process groups (processes), a terminal
(terminal), file locks (locks), the work room's link (channels) and the machine's memory (capacity).

Rules for this package: operating-system differences live here and nowhere else (no other module checks `sys.platform`); every
module uses only the standard library when it is imported (psutil and pywinpty are imported inside the function that needs them),
because the work room's agent runs from a plain Python with no packages installed.
"""
