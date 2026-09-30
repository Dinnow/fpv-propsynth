#!/usr/bin/env python
"""Entry point: `python run.py --gui` for the GUI, otherwise the CLI.

Examples:
    python run.py btfl_002.bbl --kv 7200 --prop 2.5 -o out.mp3
    python run.py --gui
"""

import sys


def main():
    if "--gui" in sys.argv[1:]:
        from drone_sound.gui import main as gui_main
        gui_main()
        return 0
    from drone_sound.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
