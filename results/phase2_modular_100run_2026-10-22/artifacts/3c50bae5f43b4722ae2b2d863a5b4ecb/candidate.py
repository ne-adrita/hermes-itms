"""Two-dimensional geometry on an integer grid.

The module provides a small set of coordinate helpers for working with
points and axis-aligned rectangles on a plane.  Everything is measured in
the same unit, and every function is a pure formula that never mutates its
arguments.

The :class:`Rectangle` class bundles the rectangle operations, while the
module level functions are the primitives the class is built from.  The
module deliberately uses only the standard library so that it can be copied
into any project without a dependency.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "Rectangle",
    "manhattan_distance",
    "rectangle_area",
    "point_in_rectangle",
    "bounding_box",
    "clamp",
    "lerp",
    "distance_squared",
    "shift_point",
]

def manhattan_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """Return the Manhattan distance between two points.

    The Manhattan distance is the sum of the absolute differences of the
    two coordinates.  It is never negative.

    >>> manhattan_distance(0, 0, 3, 4)
    7
    """
    dx = x1 - x2
    if dx < 0:
        dx = -dx
    dy = y1 - y2
    if dy < 0:
        dy = -dy
    return dx + dy


def rectangle_area(x: float, y: float, width: float, height: float) -> float:
    """Return the area of an axis-aligned rectangle.

    A rectangle with a negative width or height has no area, so the result
    is ``0``.

    >>> rectangle_area(0, 0, 3, 4)
    12
    """
    if width <= 0 or height <= 0:
        return 0
    return width * height


def point_in_rectangle(px: float, py: float, x: float, y: float, width: float, height: float) -> bool:
    """Return whether the point lies inside the rectangle.

    The rectangle is described by its lower-left corner and its size.  The
    left and bottom edges belong to the rectangle, the right and top edges
    do not.  A rectangle with a non-positive size contains no point.

    >>> point_in_rectangle(2, 2, 0, 0, 3, 3)
    True
    """
    if width <= 0 or height <= 0:
        return False
    if px < x or px >= x + width:
        return False
    if py < y or py >= y + height:
        return False
    return True


def bounding_box(
    points: Sequence[Tuple[float, float]],
) -> Optional[Tuple[float, float, float, float]]:
    """Return the smallest rectangle that contains every point.

    The result is the tuple ``(x, y, width, height)`` of the lower-left
    corner and the size of the bounding rectangle.  An empty sequence of
    points has no bounding rectangle, so ``None`` is returned.

    >>> bounding_box([(1, 2), (4, 6)])
    (1, 2, 3, 4)
    """
    if not points:
        return None
    min_x = points[0][0]
    max_x = points[0][0]
    min_y = points[0][1]
    max_y = points[0][1]
    for point in points:
        px = point[0]
        py = point[1]
        if px < min_x:
            min_x = px
        if px > max_x:
            max_x = px
        if py < min_y:
            min_y = py
        if py > max_y:
            max_y = py
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def clamp(value: float, low: float, high: float) -> float:
    """Return ``value`` restricted to the closed range ``[low, high]``.

    >>> clamp(12, 0, 10)
    10
    """
    if value < low:
        return low
    if value > high:
        return high
    return value


def lerp(start: float, end: float, fraction: float) -> float:
    """Return the point ``fraction`` of the way from ``start`` to ``end``.

    A fraction of ``0.0`` returns ``start`` and a fraction of ``1.0``
    returns ``end``.

    >>> lerp(0, 10, 0.25)
    2.5
    """
    return start + (end - start) * fraction


def distance_squared(x1: float, y1: float, x2: float, y2: float) -> float:
    """Return the squared Euclidean distance between two points.

    Squaring avoids the square root and is enough to compare distances.

    >>> distance_squared(0, 0, 3, 4)
    25
    """
    dx = x1 - x2
    dy = y1 - y2
    return dx * dx + dy * dy


def shift_point(
    point: Tuple[float, float],
    dx: float,
    dy: float,
) -> Tuple[float, float]:
    """Return the point moved by ``dx`` horizontally and ``dy`` vertically.

    >>> shift_point((1, 1), 2, -1)
    (3, 0)
    """
    return (point[0] + dx, point[1] + dy)


class Rectangle:
    """An axis-aligned rectangle described by its corner and its size."""

    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    def area(self) -> float:
        """Return the area of the rectangle."""
        return rectangle_area(self.x, self.y, self.width, self.height)

    def contains(self, px: float, py: float) -> bool:
        """Return whether the point lies inside the rectangle."""
        return point_in_rectangle(px, py, self.x, self.y, self.width, self.height)

    def intersects(self, other: "Rectangle") -> bool:
        """Return whether the two rectangles share at least one point."""
        if self.width <= 0 or self.height <= 0:
            return False
        if other.width <= 0 or other.height <= 0:
            return False
        if other.x >= self.x + self.width:
            return False
        if other.x + other.width <= self.x:
            return False
        if other.y >= self.y + self.height:
            return False
        if other.y + other.height <= self.y:
            return False
        return True

    def union(self, other: "Rectangle") -> "Rectangle":
        """Return the smallest rectangle that contains both rectangles."""
        left = min(self.x, other.x)
        bottom = min(self.y, other.y)
        right = max(self.x + self.width, other.x + other.width)
        top = max(self.y + self.height, other.y + other.height)
        return Rectangle(left, bottom, right - left, top - bottom)

    def corners(self) -> List[Point]:
        """Return the four corners of the rectangle, clockwise from lower-left."""
        return [
            (self.x, self.y),
            (self.x + self.width, self.y),
            (self.x + self.width, self.y + self.height),
            (self.x, self.y + self.height),
        ]

    def summary(self) -> Dict[str, object]:
        """Return a dictionary with the geometric figures of the rectangle."""
        return {
            "area": self.area(),
            "corners": self.corners(),
        }

    def __repr__(self) -> str:
        return (
            f"Rectangle(x={self.x}, y={self.y}, "
            f"width={self.width}, height={self.height})"
        )
