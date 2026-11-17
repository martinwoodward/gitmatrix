import random
import contribution_widget  # noqa: F401
import math

screen.antialias = 2
display.set_brightness(1)

width = 53
height = 7

BASE_COLOR = 0
COLOR_RANGE = 30
NUM_BLOBS = 5
BLOB_SPEED = 1
BLOB_SIZE = 4
BLOB_DEFINITION = 0.5
BLOB_DAMPING = 0.80
BLUR_AMOUNT = 1


class Blob:
    def __init__(self):
        self.r = random.uniform(0.5, 2) * BLOB_SIZE
        self.x = random.uniform(0, width)
        self.y = random.uniform(-3, height + 3)
        self.pen = color.oklch(
            random.uniform(10, 200),  # luma
            random.uniform(200, 240), # chroma
            random.uniform(BASE_COLOR, BASE_COLOR + COLOR_RANGE),  # hue
            random.randint(150, 220))  # alpha
        self.temperature = (self.x / width) * 100
        self.dx = 0
        self.inertia = random.uniform(0.5, 1)
        self.dy = (random.uniform(-1, 1) + 0.2) / 100

        # Create a unit circle of radius 1
        self.circle = shape.circle(0, 0, 1.0)

    def draw(self):
        screen.pen = blob.pen
        screen.shape(self.circle)

    def update(self):
        # self.temperature *= 0.999  # While some overall entropy is realistic, it tends toward equilibrium
        self.temperature += math.sin((self.x / width + 0.5) * math.pi) / 40 * self.inertia

        self.temperature = max(0, min(100, self.temperature))

        self.dx += (self.temperature - 50) / 10000
        self.dx *= BLOB_DAMPING  # A sort of viscosity, dampening movement

        self.x += self.dx
        self.y += self.dy

        if self.x < 0 or self.x >= width or self.y < 0 or self.y >= height:
            self.dy *= -1

        self.x = max(0, min(width, self.x))
        self.y = max(2, min(height - 2, self.y))

        # Scale blobs disproportionately along the X axis so they sort of squish out toward the middle
        scale_x = 0.5 + math.sin(self.x / (screen.width - 1) * math.pi) ** 2

        # Scale and translate our unit circle
        self.circle.transform = mat3().translate(blob.x, blob.y).scale(blob.r * scale_x, blob.r)


blobs = [Blob() for _ in range(NUM_BLOBS)]

base_color = color.oklch(12, 16, BASE_COLOR)

while True:
    screen.alpha = 255

    # It's a lamp so everything should kinda glow
    screen.pen = base_color
    screen.clear()

    # Update and draw the blobs
    for blob in blobs:
        blob.update()
        blob.draw()

    # Blur pass to meld everything together
    screen.blur(BLUR_AMOUNT)

    # Knock back the alpha and overdraw the blobs
    # gives them a bit more definition
    screen.alpha = int(255 * BLOB_DEFINITION)
    for blob in blobs:
        blob.draw()

    display.update()
