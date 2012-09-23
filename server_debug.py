import window
s = window.Server()
try:
    s.serve()
except KeyboardInterrupt:
    g = s.games[0]
    raise
