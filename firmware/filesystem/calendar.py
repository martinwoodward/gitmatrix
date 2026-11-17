import contribution_widget  # noqa: F401
from encoder import Encoder
import time


months = [
    ("January",  31, 3),
    ("February", 28, 6),  # Or 29?
    ("March",    31, 6),
    ("April",    30, 2),
    ("May",      31, 4),
    ("June",     30, 0),
    ("July",     31, 2),
    ("August",   31, 5),
    ("September", 30, 1),
    ("October",  31, 3),
    ("November", 30, 6),
    ("December", 31, 1)
]


class EncoderPager:
    def __init__(self, num_items, speed=0.01):
        self.enc = Encoder(1, 0, (22, 21))
        self.num_items = num_items
        self.old_item = 0
        self.new_item = 0
        self.speed = speed
        self.direction = 0
        self.offset = 0
        self._progress = 0

    def progress(self):
        return 1.0 - min(1.0, self._progress)

    def update(self):
        delta = self.enc.delta()
        if delta != 0:
            self.direction = 1 if delta > 0 else -1

        self.offset += self.speed * self.direction
        self._progress += self.speed

        self.new_item = int(self.offset) % self.num_items

        if self.new_item != self.old_item:
            self.direction = 0
            self._progress = 0
            self.old_item = self.new_item
            return self.new_item

        return self.old_item


pager = EncoderPager(len(months))

screen.font = font.sins

while True:
    page = pager.update()
    monthname, monthdays, firstweekday = months[page]
    offset_y = int(pager.progress() * 7)

    screen.pen = color.rgb(0, 0, 0)
    screen.clear()
    screen.pen = color.rgb(0, 255, 0)
    screen.text(monthname[:3], 0, -3 + offset_y)

    calendar_x = 25
    offset = None

    screen.pen = color.rgb(0, 255, 0)

    for week in range(6):
        for day in range(7):
            if week == 0 and day == firstweekday:
                offset = 0
            if offset is not None and offset < monthdays:
                screen.pen = color.rgb(0, 255, 0) if day in (0, 2, 4, 6) else color.rgb(0, 100, 0)
                screen.rectangle((day * 4) + calendar_x, week + 1 + offset_y, 4, 1)
                screen.rectangle((day * 4) + calendar_x, week + 1 + offset_y, 1, 1)
                offset += 1

    display.update()
    time.sleep(0.001)
