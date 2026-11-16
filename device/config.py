# Non-secret settings for the widget. WiFi details, GITHUB_USERNAME and optional API
# tokens live in secrets.py (see secrets.example.py).

# Apps shown in rotation, in order. Any of: graph, clock, stars, followers, x, youtube
APPS = ["graph", "clock", "stars", "followers", "x", "youtube"]

# Seconds each app is shown before switching to the next
APP_INTERVAL_S = 20

# Clock: offset from UTC in hours (e.g. 1 for BST, -8 for PST; no automatic DST)
TIMEZONE_OFFSET_HOURS = 0
CLOCK_24H = True

# Repo whose stars are shown by the "stars" app
STARS_REPO = "martinwoodward/octolamp"

# X / Twitter handle (without @) for the "x" app
X_HANDLE = "martinwoodward"

# YouTube channel handle for the "youtube" app (as in https://www.youtube.com/@github)
YOUTUBE_HANDLE = "@github"
