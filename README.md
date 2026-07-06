# Free Flow Capture Solver

Python CLI that reads a Free Flow puzzle screenshot, detects the grid and colored endpoint circles, solves the puzzle, and writes a solved image.

## Install

```powershell
pip install -r requirements.txt
```

## Usage

```powershell
python freeflow_solver.py input.png -o solved.png
```

If automatic grid-size detection is wrong, pass the board size explicitly:

```powershell
python freeflow_solver.py input.png -o solved.png --grid-size 7
python freeflow_solver.py input.png -o solved.png --grid-size 12x15
```

For troubleshooting detection, write a debug overlay:

```powershell
python freeflow_solver.py input.png -o solved.png --grid-size 7 --debug debug.png
```

## Local Web UI

```powershell
python web_app.py
```

Open `http://127.0.0.1:8000`, then paste, drop, or choose a screenshot. The UI sends the image to the local solver and displays the solved board plus a debug overlay.

## Notes

- Works best with a clean screenshot where the board is not heavily tilted.
- The solver assumes standard Free Flow rules: connect matching colored pairs, paths cannot cross, and every cell must be filled.
- Larger boards can be expensive because the puzzle is a combinatorial search problem. Use `--max-paths` to raise or lower the per-color path enumeration limit.
