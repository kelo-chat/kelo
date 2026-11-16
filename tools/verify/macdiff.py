#!/usr/bin/env python3
"""The old name of appdiff.py (it compares the macOS app and, with --windows, the Windows app). Same arguments, same output."""
import os
import runpy
import sys

if __name__ == "__main__":
    runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "appdiff.py"), run_name="__main__")
