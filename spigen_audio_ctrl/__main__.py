"""Entry point for ``python -m spigen_audio_ctrl``."""
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    # Fallback if executed directly as a standalone script
    parent_dir = str(Path(__file__).resolve().parent.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    from spigen_audio_ctrl.app_win import main
else:
    from .app_win import main

if __name__ == "__main__":
    raise SystemExit(main())
