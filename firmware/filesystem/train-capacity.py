import contribution_widget  # noqa: F401
import time


BG = color.rgb(25, 15, 0)
FG = color.rgb(250, 150, 0)


def carriage(x, population, front=False):
    width = 6
    offset_x = x * (width + 1)
    offset_y = 0 # (int(time.ticks_ms() / 100) % (screen.height * 2) - screen.height
    offset_x += (int(time.ticks_ms() / 50) % (screen.width * 2)) - screen.width
    screen.pen = FG
    screen.rectangle(offset_x, offset_y + 0, width, 6)
    screen.rectangle(offset_x + 1, offset_y + 6, 1, 1)
    screen.rectangle(offset_x + width - 2, offset_y + 6, 1, 1)
    if front:
        screen.rectangle(offset_x + width, offset_y + 3, 5, 3)
        screen.rectangle(offset_x + width, offset_y + 0, 2, 3)
        screen.line(offset_x + width + 2, offset_y + 0, offset_x + width + 5, offset_y + 3)
        screen.rectangle(offset_x + width, offset_y + 6, 1, 1)
        screen.rectangle(offset_x + width + 3, offset_y + 6, 1, 1)
    screen.pen = BG
    capacity = 4 - int(4 * population)
    screen.rectangle(offset_x + 1, offset_y + 1, width - 2, capacity)


while True:
    screen.pen = BG
    screen.clear()
    carriage(0, 1.0)
    carriage(1, 0.75)
    carriage(2, 0.5)
    carriage(3, 0.25)
    carriage(4, 0.25)
    carriage(5, 0.25)
    carriage(6, 0.25, front=True)

    display.update()
