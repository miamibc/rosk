"""Keep-like masonry layout: fixed-width columns, each card goes to the shortest column."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Graphene, Gsk, Gtk  # noqa: E402


class Masonry(Gtk.Widget):
    __gtype_name__ = "RoskMasonry"

    def __init__(self, column_width=240, spacing=12):
        super().__init__()
        self.column_width = column_width
        self.spacing = spacing
        self._children = []
        self._cache = None  # (width, positions, height)

    # ---------- children ----------
    def append(self, child):
        child.set_parent(self)
        self._children.append(child)
        self._cache = None
        self.queue_resize()

    def remove_all(self):
        for child in self._children:
            child.unparent()
        self._children = []
        self._cache = None
        self.queue_resize()

    @property
    def children(self):
        return list(self._children)

    def do_dispose(self):
        self.remove_all()
        Gtk.Widget.do_dispose(self)

    # ---------- layout ----------
    def _columns(self, width):
        cols = max(1, (width + self.spacing) // (self.column_width + self.spacing))
        col_w = (width - self.spacing * (cols - 1)) // cols
        return cols, max(col_w, 1)

    def _layout(self, width):
        if self._cache and self._cache[0] == width:
            return self._cache
        cols, col_w = self._columns(width)
        heights = [0] * cols
        positions = []
        for child in self._children:
            if not child.get_visible():
                positions.append(None)
                continue
            _min, nat, _b1, _b2 = child.measure(Gtk.Orientation.VERTICAL, col_w)
            col = heights.index(min(heights))
            x = col * (col_w + self.spacing)
            y = heights[col]
            positions.append((x, y, col_w, nat))
            heights[col] = y + nat + self.spacing
        total = max(heights) - self.spacing if self._children else 0
        self._cache = (width, positions, max(total, 0))
        return self._cache

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.column_width, self.column_width, -1, -1
        width = for_size if for_size > 0 else self.column_width
        _w, _p, height = self._layout(width)
        return height, height, -1, -1

    def do_size_allocate(self, width, height, baseline):
        _w, positions, _h = self._layout(width)
        for child, pos in zip(self._children, positions):
            if pos is None:
                continue
            x, y, w, h = pos
            transform = Gsk.Transform().translate(Graphene.Point().init(x, y))
            child.allocate(w, h, -1, transform)
