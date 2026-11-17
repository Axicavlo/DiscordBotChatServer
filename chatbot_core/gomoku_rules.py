"""Server-side Renju rules for a 15 by 15 board."""

from functools import lru_cache

SIZE = 15
EMPTY, BLACK, WHITE = range(3)
DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))
EMPTY_BOARD = (EMPTY,) * (SIZE * SIZE)


def _line(index, dr, dc, start, stop):
    row, col = divmod(index, SIZE)
    for step in range(start, stop):
        r, c = row + dr * step, col + dc * step
        yield r * SIZE + c if 0 <= r < SIZE and 0 <= c < SIZE else None


def _put(board, index, color):
    return board[:index] + (color,) + board[index + 1:]


def _length(board, index, color, dr, dc):
    length = 1
    for sign in (-1, 1):
        for cell in _line(index, dr * sign, dc * sign, 1, SIZE):
            if cell is None or board[cell] != color:
                break
            length += 1
    return length


def _fours(board, index):
    fours = set()
    for dr, dc in DIRECTIONS:
        line = tuple(_line(index, dr, dc, -4, 5))
        for start in range(5):
            window = line[start:start + 5]
            if None in window:
                continue
            stones = frozenset(cell for cell in window if board[cell] == BLACK)
            gaps = [cell for cell in window if board[cell] == EMPTY]
            if len(stones) != 4 or index not in stones or len(gaps) != 1:
                continue
            placed = _put(board, gaps[0], BLACK)
            if _length(placed, gaps[0], BLACK, dr, dc) == 5:
                fours.add(stones)
    return fours


def _double_three(board, index):
    threes = set()
    for dr, dc in DIRECTIONS:
        line = tuple(_line(index, dr, dc, -4, 5))
        for start in range(1, 5):
            window = line[start - 1:start + 5]
            if None in window:
                continue
            left, *inner, right = window
            if board[left] != EMPTY or board[right] != EMPTY:
                continue
            stones = frozenset(cell for cell in inner if board[cell] == BLACK)
            gaps = [cell for cell in inner if board[cell] == EMPTY]
            if len(stones) != 3 or len(gaps) != 1 or index not in stones or stones in threes:
                continue
            gap = gaps[0]
            placed = _put(board, gap, BLACK)
            if any(_length(placed, end, BLACK, dr, dc) != 5 for end in (left, right)):
                continue
            if judge(board, gap, BLACK) == "legal":
                threes.add(stones)
                if len(threes) == 2:
                    return True
    return False


@lru_cache(maxsize=4096)
def judge(board, index, color):
    """Return legal, win, occupied, or the specific black forbidden move."""
    if type(index) is not int or not 0 <= index < len(EMPTY_BOARD):
        raise ValueError("Invalid board intersection")
    if color not in (BLACK, WHITE):
        raise ValueError("Invalid stone")
    if board[index] != EMPTY:
        return "occupied"

    placed = _put(board, index, color)
    lengths = [_length(placed, index, color, dr, dc) for dr, dc in DIRECTIONS]
    if 5 in lengths or (color == WHITE and max(lengths) > 5):
        return "win"
    if color == WHITE:
        return "legal"
    if max(lengths) > 5:
        return "overline"
    if len(_fours(placed, index)) >= 2:
        return "double-four"
    if _double_three(placed, index):
        return "double-three"
    return "legal"


def play(board, index, color):
    result = judge(board, index, color)
    return (_put(board, index, color) if result in ("legal", "win") else board), result


def has_legal_move(board, color):
    return any(
        cell == EMPTY and judge(board, index, color) in ("legal", "win")
        for index, cell in enumerate(board)
    )
