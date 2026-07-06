from __future__ import annotations

import argparse
import dataclasses
import heapq
import math
import sys
from collections import deque
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

try:
    import z3
except ImportError:  # pragma: no cover - optional fallback for small boards.
    z3 = None


Cell = tuple[int, int]
GridShape = tuple[int, int]


@dataclasses.dataclass(frozen=True)
class Endpoint:
    cell: Cell
    bgr: tuple[int, int, int]
    hsv: tuple[int, int, int]


@dataclasses.dataclass
class Puzzle:
    rows: int
    cols: int
    colors: dict[str, tuple[Endpoint, Endpoint]]
    crop: np.ndarray
    board_rect: tuple[int, int, int, int]

    @property
    def shape(self) -> GridShape:
        return self.rows, self.cols

    @property
    def cell_count(self) -> int:
        return self.rows * self.cols


class SolveError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and solve a Free Flow puzzle from a screenshot."
    )
    parser.add_argument("image", type=Path, help="Input screenshot path.")
    parser.add_argument("-o", "--output", type=Path, default=Path("solved.png"))
    parser.add_argument(
        "--grid-size",
        help="Board size. Examples: 7, 7x7, 12x15. Auto-detected if omitted.",
    )
    parser.add_argument(
        "--debug",
        type=Path,
        help="Optional debug image showing detected grid and endpoints.",
    )
    parser.add_argument(
        "--max-paths",
        type=int,
        default=20000,
        help="Maximum candidate paths to enumerate per color during search.",
    )
    parser.add_argument(
        "--min-dot-fill",
        type=float,
        default=0.08,
        help="Minimum saturated-pixel fraction in a cell to treat it as a colored dot.",
    )
    return parser.parse_args()


def load_image(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return image


def decode_image_bytes(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not decode image bytes.")
    return image


def find_board_rect(image: np.ndarray) -> tuple[int, int, int, int]:
    grid_rect = find_board_rect_from_grid_lines(image)
    if grid_rect is not None:
        return expand_rect(grid_rect, image.shape, pad=2)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 120)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    height, width = image.shape[:2]
    image_area = height * width
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < image_area * 0.08:
            continue
        aspect = w / float(h)
        if 0.75 <= aspect <= 1.35:
            score = area * (1.0 - min(abs(1.0 - aspect), 0.5))
            candidates.append((score, (x, y, w, h)))

    if candidates:
        _, rect = max(candidates, key=lambda item: item[0])
        return expand_rect(rect, image.shape, pad=3)

    side = min(width, height)
    x = (width - side) // 2
    y = (height - side) // 2
    return x, y, side, side


def cluster_positions(positions: list[int], tolerance: int = 5) -> list[int]:
    if not positions:
        return []
    clusters: list[list[int]] = []
    for value in sorted(positions):
        if clusters and abs(value - int(np.median(clusters[-1]))) <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [int(round(float(np.median(cluster)))) for cluster in clusters]


def find_board_rect_from_grid_lines(image: np.ndarray) -> tuple[int, int, int, int] | None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 40, 120)
    height, width = edges.shape
    min_line = int(min(width, height) * 0.45)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=120,
        minLineLength=min_line,
        maxLineGap=8,
    )
    if lines is None:
        return None

    horizontal: list[int] = []
    vertical: list[int] = []
    for x1, y1, x2, y2 in lines[:, 0]:
        if abs(y1 - y2) <= 3 and abs(x2 - x1) >= width * 0.45:
            horizontal.append(int(round((y1 + y2) / 2)))
        if abs(x1 - x2) <= 3 and abs(y2 - y1) >= height * 0.25:
            vertical.append(int(round((x1 + x2) / 2)))

    xs = cluster_positions(vertical)
    ys = cluster_positions(horizontal)
    if len(xs) < 5 or len(ys) < 5:
        return None

    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    w = x2 - x1
    h = y2 - y1
    if w <= 0 or h <= 0:
        return None
    aspect = w / float(h)
    if not 0.45 <= aspect <= 2.2:
        return None
    return x1, y1, w, h


def expand_rect(
    rect: tuple[int, int, int, int], shape: tuple[int, ...], pad: int
) -> tuple[int, int, int, int]:
    x, y, w, h = rect
    height, width = shape[:2]
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(width, x + w + pad)
    y2 = min(height, y + h + pad)
    return x1, y1, x2 - x1, y2 - y1


def crop_board(image: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    x, y, w, h = rect
    return image[y : y + h, x : x + w].copy()


def score_grid_lines(edges: np.ndarray, rows: int, cols: int) -> float:
    height, width = edges.shape
    xs = np.linspace(0, width - 1, cols + 1)
    ys = np.linspace(0, height - 1, rows + 1)
    score = 0.0
    for x in xs:
        xi = int(round(x))
        lo = max(0, xi - 1)
        hi = min(width, xi + 2)
        score += float(edges[:, lo:hi].mean())
    for y in ys:
        yi = int(round(y))
        lo = max(0, yi - 1)
        hi = min(height, yi + 2)
        score += float(edges[lo:hi, :].mean())
    return score / (rows + cols + 2)


def estimate_grid_shape(crop: np.ndarray) -> GridShape:
    candidates = estimate_grid_shape_candidates(crop)
    if not candidates:
        raise SolveError("Could not estimate grid shape; pass --grid-size.")
    return candidates[0]


def estimate_grid_shape_candidates(crop: np.ndarray) -> list[GridShape]:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 35, 120)
    height, width = edges.shape

    candidates: list[tuple[float, GridShape]] = []
    for rows in range(4, 21):
        for cols in range(4, 21):
            cell_ratio = (height / rows) / max(1e-6, (width / cols))
            if not 0.75 <= cell_ratio <= 1.35:
                continue
            score = score_grid_lines(edges, rows, cols)
            score *= 1.0 - min(abs(1.0 - cell_ratio), 0.5)
            # Divisor grids can score well because their lines overlap the real grid.
            # Prefer finer grids when the line score is comparable.
            score *= 1.0 + min(rows * cols, 225) / 500.0
            candidates.append((score, (rows, cols)))

    return [
        shape
        for _, shape in sorted(candidates, key=lambda item: item[0], reverse=True)
    ]


def choose_grid_shape_from_endpoints(crop: np.ndarray, min_dot_fill: float) -> tuple[GridShape, list[Endpoint]]:
    dot_centers = detect_dot_centers(crop)
    ranked: list[tuple[int, float, GridShape, list[Endpoint]]] = []
    for rank, shape in enumerate(estimate_grid_shape_candidates(crop)[:120]):
        dot_score = score_shape_against_dots(crop, shape, dot_centers)
        if dot_score is None:
            continue
        endpoints = detect_endpoints(crop, shape, min_dot_fill)
        if dot_centers and len(endpoints) != len(dot_centers):
            continue
        if len(endpoints) < 2 or len(endpoints) % 2:
            continue
        try:
            pair_endpoints(endpoints)
        except SolveError:
            continue
        rows, cols = shape
        cell_h = crop.shape[0] / rows
        cell_w = crop.shape[1] / cols
        squareness = 1.0 - min(abs(1.0 - (cell_h / cell_w)), 0.5)
        ranked.append((len(endpoints), dot_score + squareness - rank / 1000.0, shape, endpoints))

    if not ranked:
        shape = estimate_grid_shape(crop)
        return shape, detect_endpoints(crop, shape, min_dot_fill)

    _, _, shape, endpoints = max(ranked, key=lambda item: (item[0], item[1]))
    return shape, endpoints


def parse_grid_shape(value: str | None) -> GridShape | None:
    if value is None or value.strip() == "":
        return None
    normalized = value.lower().replace(" ", "")
    for sep in ("x", "*", ","):
        if sep in normalized:
            left, right = normalized.split(sep, 1)
            first = int(left)
            second = int(right)
            # Free Flow labels are commonly written width x height.
            return second, first
    size = int(normalized)
    return size, size


def maybe_transpose_shape(shape: GridShape, crop: np.ndarray) -> GridShape:
    rows, cols = shape
    height, width = crop.shape[:2]
    direct = abs((height / rows) - (width / cols))
    swapped = abs((height / cols) - (width / rows))
    if swapped < direct:
        return cols, rows
    return rows, cols


def detect_endpoints(crop: np.ndarray, shape: GridShape, min_dot_fill: float) -> list[Endpoint]:
    rows, cols = shape
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    cell_h = crop.shape[0] / float(rows)
    cell_w = crop.shape[1] / float(cols)
    radius_base = min(cell_h, cell_w)
    endpoints: list[Endpoint] = []

    for row in range(rows):
        for col in range(cols):
            cx = int(round((col + 0.5) * cell_w))
            cy = int(round((row + 0.5) * cell_h))
            radius = max(3, int(radius_base * 0.34))
            y1 = max(0, cy - radius)
            y2 = min(crop.shape[0], cy + radius + 1)
            x1 = max(0, cx - radius)
            x2 = min(crop.shape[1], cx + radius + 1)

            patch_hsv = hsv[y1:y2, x1:x2]
            patch_bgr = crop[y1:y2, x1:x2]
            yy, xx = np.ogrid[y1:y2, x1:x2]
            circle_mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2
            saturated = (
                (
                    ((patch_hsv[:, :, 1] > 55) & (patch_hsv[:, :, 2] > 70))
                    | (patch_hsv[:, :, 2] > 135)
                )
                & circle_mask
            )
            fill = saturated.sum() / max(1, circle_mask.sum())
            if fill < min_dot_fill:
                continue

            selected_hsv = patch_hsv[saturated]
            selected_bgr = patch_bgr[saturated]
            mean_hsv = tuple(int(v) for v in np.median(selected_hsv, axis=0))
            mean_bgr = tuple(int(v) for v in np.median(selected_bgr, axis=0))
            endpoints.append(Endpoint(cell=(row, col), bgr=mean_bgr, hsv=mean_hsv))

    return endpoints


def detect_dot_centers(crop: np.ndarray) -> list[tuple[float, float]]:
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = (
        (((hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 80)) | (hsv[:, :, 2] > 150))
    ).astype("uint8") * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    height, width = crop.shape[:2]
    image_area = height * width
    min_area = max(80.0, image_area * 0.00045)
    max_area = image_area * 0.012
    centers: list[tuple[float, float]] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not min_area <= area <= max_area:
            continue
        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue
        circularity = 4 * math.pi * area / (perimeter * perimeter)
        x, y, w, h = cv2.boundingRect(contour)
        aspect = w / float(h)
        if circularity < 0.55 or not 0.65 <= aspect <= 1.45:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        centers.append((moments["m01"] / moments["m00"], moments["m10"] / moments["m00"]))
    return centers


def score_shape_against_dots(
    crop: np.ndarray,
    shape: GridShape,
    centers: list[tuple[float, float]],
) -> float | None:
    if not centers:
        return 0.0
    rows, cols = shape
    cell_h = crop.shape[0] / rows
    cell_w = crop.shape[1] / cols
    seen: set[Cell] = set()
    errors: list[float] = []
    for y, x in centers:
        row = int(y / cell_h)
        col = int(x / cell_w)
        if not (0 <= row < rows and 0 <= col < cols):
            return None
        cell = (row, col)
        if cell in seen:
            return None
        seen.add(cell)
        cy = (row + 0.5) * cell_h
        cx = (col + 0.5) * cell_w
        errors.append(math.hypot((y - cy) / cell_h, (x - cx) / cell_w))

    average_error = sum(errors) / len(errors)
    if average_error > 0.22:
        return None
    return 1.0 - average_error


def hue_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    dh = abs(a[0] - b[0])
    dh = min(dh, 180 - dh) / 90.0
    ds = abs(a[1] - b[1]) / 255.0
    dv = abs(a[2] - b[2]) / 255.0
    return math.sqrt((2.8 * dh) ** 2 + ds**2 + (0.6 * dv) ** 2)


def pair_endpoints(endpoints: list[Endpoint]) -> dict[str, tuple[Endpoint, Endpoint]]:
    if len(endpoints) % 2:
        raise SolveError(f"Detected odd number of dots ({len(endpoints)}).")

    unused = set(range(len(endpoints)))
    pairs: dict[str, tuple[Endpoint, Endpoint]] = {}
    color_index = 1
    while unused:
        i = unused.pop()
        nearest = min(unused, key=lambda j: hue_distance(endpoints[i].hsv, endpoints[j].hsv))
        dist = hue_distance(endpoints[i].hsv, endpoints[nearest].hsv)
        if dist > 0.8:
            raise SolveError(
                "Could not confidently pair endpoints by color. Try a cleaner crop or lower --min-dot-fill."
            )
        unused.remove(nearest)
        pairs[f"c{color_index}"] = (endpoints[i], endpoints[nearest])
        color_index += 1
    return pairs


def read_puzzle(
    image: np.ndarray,
    grid_shape: GridShape | None,
    min_dot_fill: float,
) -> Puzzle:
    rect = find_board_rect(image)
    crop = crop_board(image, rect)
    if grid_shape:
        shape = maybe_transpose_shape(grid_shape, crop)
        endpoints = detect_endpoints(crop, shape, min_dot_fill)
    else:
        shape, endpoints = choose_grid_shape_from_endpoints(crop, min_dot_fill)
    pairs = pair_endpoints(endpoints)
    return Puzzle(rows=shape[0], cols=shape[1], colors=pairs, crop=crop, board_rect=rect)


def neighbors(cell: Cell, shape: GridShape) -> Iterable[Cell]:
    rows, cols = shape
    row, col = cell
    if row > 0:
        yield row - 1, col
    if row + 1 < rows:
        yield row + 1, col
    if col > 0:
        yield row, col - 1
    if col + 1 < cols:
        yield row, col + 1


def shortest_distance(start: Cell, end: Cell, shape: GridShape, blocked: set[Cell]) -> int | None:
    queue = deque([(start, 0)])
    seen = {start}
    while queue:
        cell, dist = queue.popleft()
        if cell == end:
            return dist
        for nxt in neighbors(cell, shape):
            if nxt in seen or (nxt in blocked and nxt != end):
                continue
            seen.add(nxt)
            queue.append((nxt, dist + 1))
    return None


def enumerate_paths(
    start: Cell,
    end: Cell,
    shape: GridShape,
    blocked: set[Cell],
    max_paths: int,
) -> list[list[Cell]]:
    paths: list[list[Cell]] = []
    path = [start]
    seen = {start}

    def dfs(cell: Cell) -> None:
        if len(paths) >= max_paths:
            return
        if cell == end:
            paths.append(path.copy())
            return

        candidates = []
        for nxt in neighbors(cell, shape):
            if nxt in seen or (nxt in blocked and nxt != end):
                continue
            dist = abs(nxt[0] - end[0]) + abs(nxt[1] - end[1])
            candidates.append((dist, nxt))

        for _, nxt in sorted(candidates):
            seen.add(nxt)
            path.append(nxt)
            dfs(nxt)
            path.pop()
            seen.remove(nxt)

    dfs(start)
    paths.sort(key=len)
    return paths


def remaining_is_viable(
    shape: GridShape,
    occupied: set[Cell],
    remaining_pairs: Iterable[tuple[Endpoint, Endpoint]],
) -> bool:
    rows, cols = shape
    remaining = list(remaining_pairs)
    all_remaining_endpoints = {endpoint.cell for pair in remaining for endpoint in pair}

    for start, end in remaining:
        blocked = occupied - {start.cell, end.cell}
        if shortest_distance(start.cell, end.cell, shape, blocked) is None:
            return False

    free = {
        (row, col)
        for row in range(rows)
        for col in range(cols)
        if (row, col) not in occupied or (row, col) in all_remaining_endpoints
    }
    seen: set[Cell] = set()
    for cell in free:
        if cell in seen:
            continue
        queue = deque([cell])
        seen.add(cell)
        component: set[Cell] = set()
        while queue:
            cur = queue.popleft()
            component.add(cur)
            for nxt in neighbors(cur, shape):
                if nxt in free and nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        if component.isdisjoint(all_remaining_endpoints):
            return False
    return True


def solve_puzzle(puzzle: Puzzle, max_paths: int) -> dict[str, list[Cell]]:
    if z3 is not None and puzzle.cell_count > 80:
        return solve_puzzle_z3(puzzle, timeout_ms=30000)
    return solve_puzzle_dfs(puzzle, max_paths)


def solve_puzzle_z3(puzzle: Puzzle, timeout_ms: int = 30000) -> dict[str, list[Cell]]:
    rows, cols = puzzle.shape
    color_names = list(puzzle.colors.keys())
    color_index = {name: idx for idx, name in enumerate(color_names)}
    endpoint_to_color = {
        endpoint.cell: color_index[name]
        for name, pair in puzzle.colors.items()
        for endpoint in pair
    }
    starts = {color_index[name]: pair[0].cell for name, pair in puzzle.colors.items()}
    ends = {color_index[name]: pair[1].cell for name, pair in puzzle.colors.items()}

    color_vars = {
        (row, col): z3.Int(f"c_{row}_{col}")
        for row in range(rows)
        for col in range(cols)
    }
    order_vars = {
        (row, col): z3.Int(f"o_{row}_{col}")
        for row in range(rows)
        for col in range(cols)
    }

    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    max_order = puzzle.cell_count - 1

    for cell, color_var in color_vars.items():
        solver.add(color_var >= 0, color_var < len(color_names))
        solver.add(order_vars[cell] >= 0, order_vars[cell] <= max_order)
        if cell in endpoint_to_color:
            solver.add(color_var == endpoint_to_color[cell])

    for idx, start_cell in starts.items():
        solver.add(order_vars[start_cell] == 0)

    for cell, color_var in color_vars.items():
        same_neighbor_exprs = [
            color_vars[nxt] == color_var
            for nxt in neighbors(cell, puzzle.shape)
        ]
        degree = z3.Sum([z3.If(expr, 1, 0) for expr in same_neighbor_exprs])
        if cell in endpoint_to_color:
            solver.add(degree == 1)
        else:
            solver.add(degree == 2)

        for idx in range(len(color_names)):
            if cell == starts[idx]:
                continue
            predecessor_options = [
                z3.And(color_vars[nxt] == idx, order_vars[nxt] == order_vars[cell] - 1)
                for nxt in neighbors(cell, puzzle.shape)
            ]
            solver.add(
                z3.Implies(
                    color_var == idx,
                    z3.And(order_vars[cell] > 0, z3.Or(predecessor_options)),
                )
            )

    result = solver.check()
    if result == z3.unknown:
        raise SolveError(f"Z3 solver timed out or returned unknown: {solver.reason_unknown()}")
    if result != z3.sat:
        raise SolveError("Z3 solver found no solution.")

    model = solver.model()
    grid = {
        cell: model.eval(var).as_long()
        for cell, var in color_vars.items()
    }
    solution: dict[str, list[Cell]] = {}
    for name, idx in color_index.items():
        solution[name] = trace_color_path(
            puzzle.shape,
            grid,
            idx,
            starts[idx],
            ends[idx],
        )
    return solution


def trace_color_path(
    shape: GridShape,
    grid: dict[Cell, int],
    color: int,
    start: Cell,
    end: Cell,
) -> list[Cell]:
    path = [start]
    previous: Cell | None = None
    current = start
    seen = {start}
    while current != end:
        next_cells = [
            cell
            for cell in neighbors(current, shape)
            if cell != previous and grid[cell] == color
        ]
        if not next_cells:
            raise SolveError("Solved grid could not be traced into paths.")
        if len(next_cells) > 1:
            unvisited = [cell for cell in next_cells if cell not in seen]
            if len(unvisited) != 1:
                raise SolveError("Solved grid contains an ambiguous path branch.")
            nxt = unvisited[0]
        else:
            nxt = next_cells[0]
        previous, current = current, nxt
        if current in seen and current != end:
            raise SolveError("Solved grid contains a cycle.")
        seen.add(current)
        path.append(current)
    return path


def solve_puzzle_dfs(puzzle: Puzzle, max_paths: int) -> dict[str, list[Cell]]:
    shape = puzzle.shape
    colors = puzzle.colors
    endpoint_cells = {endpoint.cell for pair in colors.values() for endpoint in pair}
    color_names = tuple(colors.keys())
    cache: dict[tuple[str, frozenset[Cell]], list[list[Cell]]] = {}

    def candidates_for(color: str, occupied: set[Cell]) -> list[list[Cell]]:
        start, end = colors[color]
        blocked = (occupied | endpoint_cells) - {start.cell, end.cell}
        key = (color, frozenset(blocked))
        if key not in cache:
            cache[key] = enumerate_paths(start.cell, end.cell, shape, blocked, max_paths)
        return cache[key]

    def recurse(done: dict[str, list[Cell]], occupied: set[Cell]) -> dict[str, list[Cell]] | None:
        remaining = [name for name in color_names if name not in done]
        if not remaining:
            return done if len(occupied) == puzzle.cell_count else None

        ranked = []
        for color in remaining:
            start, end = colors[color]
            blocked = (occupied | endpoint_cells) - {start.cell, end.cell}
            distance = shortest_distance(start.cell, end.cell, shape, blocked)
            if distance is None:
                return None
            free_degree = sum(
                1
                for nxt in neighbors(start.cell, shape)
                if nxt == end.cell or nxt not in blocked
            )
            free_degree += sum(
                1
                for nxt in neighbors(end.cell, shape)
                if nxt == start.cell or nxt not in blocked
            )
            heapq.heappush(ranked, (distance, free_degree, color))

        _, _, color = heapq.heappop(ranked)
        paths = candidates_for(color, occupied)
        if not paths:
            return None
        next_remaining_pairs = [
            colors[name] for name in remaining if name != color
        ]

        for path in paths:
            path_cells = set(path)
            if path_cells - {colors[color][0].cell, colors[color][1].cell} & occupied:
                continue
            next_occupied = occupied | path_cells
            if not remaining_is_viable(shape, next_occupied, next_remaining_pairs):
                continue
            done[color] = path
            solved = recurse(done, next_occupied)
            if solved is not None:
                return solved
            del done[color]
        return None

    solution = recurse({}, set())
    if solution is None:
        raise SolveError("No solution found. Try passing --grid-size or increasing --max-paths.")
    return solution


def color_for_path(endpoint: Endpoint) -> tuple[int, int, int]:
    b, g, r = endpoint.bgr
    return int(b), int(g), int(r)


def render_solution(puzzle: Puzzle, solution: dict[str, list[Cell]]) -> np.ndarray:
    rows, cols = puzzle.shape
    cell_px = 72
    margin = 30
    board_w = cols * cell_px
    board_h = rows * cell_px
    width = board_w + margin * 2
    height = board_h + margin * 2
    image = np.full((height, width, 3), (28, 28, 28), dtype=np.uint8)

    for row in range(rows + 1):
        y = int(round(margin + row * cell_px))
        cv2.line(image, (margin, y), (margin + board_w, y), (78, 78, 78), 2)
    for col in range(cols + 1):
        x = int(round(margin + col * cell_px))
        cv2.line(image, (x, margin), (x, margin + board_h), (78, 78, 78), 2)

    def center(cell_pos: Cell) -> tuple[int, int]:
        row, col = cell_pos
        return (
            int(round(margin + (col + 0.5) * cell_px)),
            int(round(margin + (row + 0.5) * cell_px)),
        )

    line_width = max(12, int(cell_px * 0.34))
    dot_radius = max(12, int(cell_px * 0.34))
    for color, path in solution.items():
        bgr = color_for_path(puzzle.colors[color][0])
        points = [center(cell_pos) for cell_pos in path]
        for p1, p2 in zip(points, points[1:]):
            cv2.line(image, p1, p2, bgr, line_width, cv2.LINE_AA)

    for color, pair in puzzle.colors.items():
        bgr = color_for_path(pair[0])
        for endpoint in pair:
            cv2.circle(image, center(endpoint.cell), dot_radius, bgr, -1, cv2.LINE_AA)
            cv2.circle(image, center(endpoint.cell), dot_radius, (245, 245, 245), 3, cv2.LINE_AA)

    return image


def render_debug(puzzle: Puzzle, endpoints: dict[str, tuple[Endpoint, Endpoint]]) -> np.ndarray:
    crop = puzzle.crop.copy()
    rows, cols = puzzle.shape
    height, width = crop.shape[:2]
    cell_h = height / float(rows)
    cell_w = width / float(cols)

    for row in range(rows + 1):
        y = int(round(row * cell_h))
        cv2.line(crop, (0, y), (width, y), (255, 255, 255), 1)
    for col in range(cols + 1):
        x = int(round(col * cell_w))
        cv2.line(crop, (x, 0), (x, height), (255, 255, 255), 1)

    for color, pair in endpoints.items():
        for endpoint in pair:
            row, col = endpoint.cell
            cx = int(round((col + 0.5) * cell_w))
            cy = int(round((row + 0.5) * cell_h))
            cv2.circle(crop, (cx, cy), max(6, int(min(cell_w, cell_h) * 0.18)), endpoint.bgr, -1)
            cv2.putText(
                crop,
                color,
                (cx + 4, cy - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
    return crop


def write_image(path: Path, image: np.ndarray) -> None:
    ext = path.suffix or ".png"
    ok, encoded = cv2.imencode(ext, image)
    if not ok:
        raise OSError(f"Could not encode image as {ext}")
    encoded.tofile(str(path))


def encode_png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise OSError("Could not encode image as PNG.")
    return encoded.tobytes()


def solve_image(
    image: np.ndarray,
    grid_size: int | str | GridShape | None = None,
    min_dot_fill: float = 0.08,
    max_paths: int = 20000,
) -> tuple[Puzzle, dict[str, list[Cell]], np.ndarray, np.ndarray]:
    if isinstance(grid_size, tuple):
        grid_shape = grid_size
    elif isinstance(grid_size, int):
        grid_shape = (grid_size, grid_size)
    else:
        grid_shape = parse_grid_shape(grid_size)
    puzzle = read_puzzle(image, grid_shape, min_dot_fill)
    solution = solve_puzzle(puzzle, max_paths)
    solved = render_solution(puzzle, solution)
    debug = render_debug(puzzle, puzzle.colors)
    return puzzle, solution, solved, debug


def main() -> int:
    args = parse_args()
    try:
        image = load_image(args.image)
        puzzle, _, output, debug = solve_image(
            image,
            grid_size=args.grid_size,
            min_dot_fill=args.min_dot_fill,
            max_paths=args.max_paths,
        )
        write_image(args.output, output)
        if args.debug:
            write_image(args.debug, debug)
        print(f"grid={puzzle.cols}x{puzzle.rows} colors={len(puzzle.colors)} output={args.output}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
